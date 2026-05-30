"""规则引擎 — 六维幻觉检测核心。

实现全部六个幻觉维度（H-1至H-6）的规则化检测。
支持仲裁时效检测、法律方法论替换检测、计算核验、
程序时效校验、法条张冠李戴检测、递减惩罚评分。
"""

import logging
import re
from datetime import datetime

from .claim_parser import ClaimParser, extract_interest_base
from .config import (
    LAW_ARTICLE_COMPATIBILITY,
    METHODOLOGY_REPLACEMENT_RULES,
    RULE_ENGINE_PATTERNS,
    STRUCTURE_CHECK_RULES,
    SUBJECTIVE_RHETORIC_KEYWORDS,
)
from .cross_reference_engine import CrossReferenceEngine
from .evidence_index import EvidenceIndex
from .law_citation_checker import LawCitationChecker
from .law_knowledge_base import LawKnowledgeBase
from .models import (
    CalculationIssue,
    CitationFraudItem,
    ClaimBoundaryItem,
    ClaimComparisonItem,
    FactSourceIssue,
    HallucinationDetectionResult,
    HallucinationDimensionResult,
    LawCitationItem,
    MethodologyReplacement,
    RhetoricItem,
    RuleFlag,
    StructureCheckItem,
    SyllogismBreakItem,
    TimeBarIssue,
)

logger = logging.getLogger("legal-hallucination")

_DATEUTIL_AVAILABLE = False
try:
    from dateutil.relativedelta import relativedelta
    _DATEUTIL_AVAILABLE = True
except ImportError:
    logger.info("python-dateutil未安装，仲裁时效检测不可用。安装方式：pip install python-dateutil")


class RuleEngine:
    def __init__(
        self,
        evidence_index: EvidenceIndex | None = None,
        claim_parser: ClaimParser | None = None,
        law_checker: LawCitationChecker | None = None,
        law_kb: LawKnowledgeBase | None = None,
        cross_ref_engine: CrossReferenceEngine | None = None,
        web_verifier=None,
        vector_index=None,
        verbose: bool = False,
    ):
        self.evidence_index = evidence_index or EvidenceIndex()
        self.claim_parser = claim_parser or ClaimParser()
        self.law_checker = law_checker or LawCitationChecker()
        self.law_kb = law_kb or LawKnowledgeBase()
        self.web_verifier = web_verifier
        self.cross_ref_engine = cross_ref_engine or CrossReferenceEngine(
            evidence_index=self.evidence_index,
            law_kb=self.law_kb,
            web_verifier=web_verifier,
            vector_index=vector_index,
        )
        self.verbose = verbose
        if verbose:
            logger.info("RuleEngine initialized with verbose=True")

    def run_full_scan(
        self,
        document_text: str,
        complaint_text: str = "",
        amended_text: str = "",
        manifest_path: str = "",
        vault_root: str = "",
        local_law_dir: str = "",
    ) -> HallucinationDetectionResult:
        result = HallucinationDetectionResult()

        if manifest_path:
            self.evidence_index.load(manifest_path, vault_root)

        if complaint_text:
            self.claim_parser.parse(complaint_text, amended_text=amended_text)
        elif self.evidence_index.loaded:
            claim_text = self.evidence_index.get_claim_texts()
            if claim_text:
                self.claim_parser.parse(claim_text, amended_text=amended_text)

        if local_law_dir:
            self.law_checker.load_local_laws(local_law_dir)
            self.law_kb.load_from_directory(local_law_dir)

        result.structure_issues = self.check_structure(document_text)
        result.citation_frauds = self.check_citation_fraud(document_text)
        result.claim_violations = self.check_claim_boundary(document_text)
        result.syllogism_breaks = self.check_syllogism(document_text)
        result.rhetoric_items = self.check_subjective_rhetoric(document_text)
        result.law_citation_issues = self.check_law_citations(document_text)
        result.fact_source_issues = self.check_fact_source_binding(document_text)
        result.time_bar_issues = self.check_time_bar(document_text)
        result.methodology_replacements = self.check_methodology_replacement(document_text, complaint_text)
        result.interest_base_items = extract_interest_base(document_text)
        result.calculation_issues = self.check_calculation_accuracy(document_text)
        result.claim_comparisons = self.build_claim_comparisons(document_text, complaint_text)
        self._check_procedural_time_limits(document_text, result)
        law_mismatch_issues = self.check_law_article_mismatch(document_text)
        result.calculation_issues.extend(law_mismatch_issues)

        if self.law_kb.loaded or self.evidence_index.loaded:
            cross_ref_report = self.cross_ref_engine.cross_verify(
                document_text=document_text,
                manifest_path="",
                vault_root="",
                law_dir="",
            )
            for issue in cross_ref_report.issues:
                result.fact_source_issues.append(FactSourceIssue(
                    line_number=issue.line_number,
                    line_text=issue.claim_text[:200],
                    issue_type=f"交叉验证-{issue.match_type}",
                    h_code=issue.h_code,
                    suggestion=issue.suggestion,
                ))
            for lv in cross_ref_report.law_verifications:
                if not lv.local_found or not lv.is_current:
                    existing = [lci for lci in result.law_citation_issues
                                if lci.citation_text == lv.citation_text]
                    if not existing:
                        result.law_citation_issues.append(LawCitationItem(
                            citation_text=lv.citation_text,
                            law_name=lv.law_name,
                            article=lv.article,
                            is_replaced=not lv.is_current,
                            replaced_by=lv.replaced_by,
                            format_issues=[] if lv.local_found else ["本地法条库未找到"],
                            local_match_found=lv.local_found,
                        ))

        rule_flags = self._run_pattern_rules(document_text)
        dimension_map = self._group_flags_by_dimension(
            rule_flags,
            result.structure_issues,
            result.citation_frauds,
            result.claim_violations,
            result.syllogism_breaks,
            result.rhetoric_items,
            result.law_citation_issues,
            result.fact_source_issues,
            result.time_bar_issues,
            result.methodology_replacements,
            result.claim_comparisons,
        )
        result.dimensions = list(dimension_map.values())

        total = 0
        for dim in result.dimensions:
            dim.total_flags = len(dim.rule_flags)
            dim.critical_count = sum(1 for f in dim.rule_flags if f.severity == "critical")
            dim.high_count = sum(1 for f in dim.rule_flags if f.severity == "high")
            dim.medium_count = sum(1 for f in dim.rule_flags if f.severity == "medium")
            dim.low_count = sum(1 for f in dim.rule_flags if f.severity == "low")
            total += dim.total_flags

        result.total_flags = total
        result.hallucination_score = self._calculate_score(result)
        result.risk_grade, result.risk_description = self._determine_risk_grade(result.hallucination_score)

        logger.info(
            "RuleEngine.run_full_scan: total_flags=%d, score=%.1f, grade=%s",
            total, result.hallucination_score, result.risk_grade,
        )
        return result

    def check_structure(self, document_text: str) -> list[StructureCheckItem]:
        issues = []
        for rule_id, rule in STRUCTURE_CHECK_RULES.items():
            found = rule["heading"] in document_text
            if not found and "alt_patterns" in rule:
                for alt_pat in rule["alt_patterns"]:
                    if re.search(alt_pat, document_text):
                        found = True
                        break
            if not found:
                issues.append(StructureCheckItem(
                    heading=rule["heading"],
                    severity=rule["severity"],
                    h_code=rule["h_code"],
                    message=rule["message"],
                ))
                logger.info("check_structure: MISSING '%s'", rule["heading"])
        return issues

    def check_citation_fraud(self, document_text: str) -> list[CitationFraudItem]:
        if not self.evidence_index.loaded:
            logger.warning("check_citation_fraud: evidence index not loaded, skipping")
            return []
        return self.evidence_index.find_fraud_citations(document_text)

    def check_claim_boundary(self, document_text: str) -> list[ClaimBoundaryItem]:
        judgment_main = document_text
        main_match = re.search(r'# 四、判决如下.*?(?=#|$)', document_text, re.DOTALL)
        if main_match:
            judgment_main = main_match.group(0)

        return self.claim_parser.check_judgment_scope(judgment_main)

    def check_syllogism(self, document_text: str) -> list[SyllogismBreakItem]:
        breaks = []
        reasoning_match = re.search(r'# 三、本院认为.*?(?=# 四|#|$)', document_text, re.DOTALL)
        if not reasoning_match:
            return breaks

        reasoning_text = reasoning_match.group(0)
        lines = reasoning_text.split('\n')

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped or len(line_stripped) < 10:
                continue

            has_conclusion = any(k in line_stripped for k in ["应予支持", "不予支持", "应当支付", "确认", "驳回"])
            if not has_conclusion:
                continue

            has_law = "《" in line_stripped and "》" in line_stripped
            has_evidence = bool(re.search(r"见《|见证据|证据\d", line_stripped))

            if not has_law:
                breaks.append(SyllogismBreakItem(
                    line_number=i + 1,
                    line_text=line_stripped[:80],
                    missing_part="大前提（法律依据）",
                    h_code="H-3",
                ))

            if not has_evidence:
                breaks.append(SyllogismBreakItem(
                    line_number=i + 1,
                    line_text=line_stripped[:80],
                    missing_part="小前提（证据锚点）",
                    h_code="H-3",
                ))

        logger.info("check_syllogism: found %d breaks", len(breaks))
        return breaks

    def check_subjective_rhetoric(self, document_text: str) -> list[RhetoricItem]:
        items = []
        lines = document_text.split('\n')
        seen = set()

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            for severity_level, keywords in SUBJECTIVE_RHETORIC_KEYWORDS.items():
                if severity_level == "exceptions":
                    continue

                for keyword in keywords:
                    if keyword in line_stripped:
                        is_exception = False
                        for exc in SUBJECTIVE_RHETORIC_KEYWORDS.get("exceptions", []):
                            if re.search(exc, line_stripped):
                                is_exception = True
                                break

                        if not is_exception and keyword == "恶意":
                            _malice_pat = (
                                r'《[^》]+》.*恶意|恶意.*《[^》]+》'
                                r'|第[^条]+条.*恶意|恶意.*第[^条]+条'
                                r'|恶意.*减资|恶意.*涂黑|恶意.*遮挡'
                                r'|恶意.*欠薪|恶意.*妨碍|恶意.*规避'
                                r'|恶意.*遮掩|恶意.*遮蔽|恶意.*隐'
                                r'|举证妨碍.*恶意|恶意.*举证|恶意.*逃'
                                r'|减资.*恶意|恶意程度|恶意减资'
                                r'|恶意涂黑|恶意遮挡|恶意欠薪'
                                r'|恶意妨碍|恶意遮蔽|恶意隐匿'
                            )
                            if re.search(_malice_pat, line_stripped):
                                is_exception = True

                        if not is_exception and keyword in ("主观恶意", "主观意图", "主观目的"):
                            if re.search(r'《[^》]+》|第[^条]+条|法释|恶意程度|录音.*表述|录音.*证明', line_stripped):
                                is_exception = True

                        dedup_key = f"{keyword}:{i}"
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)

                        sev_map = {"high_severity": "high", "medium_severity": "medium", "low_severity": "low"}
                        suggestion = self._get_rhetoric_replacement(keyword, line_stripped)

                        items.append(RhetoricItem(
                            keyword=keyword,
                            line_number=i + 1,
                            line_text=line_stripped[:80],
                            severity=sev_map.get(severity_level, "medium"),
                            is_exception=is_exception,
                            suggestion=suggestion,
                        ))

        logger.info("check_subjective_rhetoric: found %d items", len(items))
        return items

    def check_law_citations(self, document_text: str) -> list[LawCitationItem]:
        issues = []

        citations = self.law_checker.extract_citations(document_text)
        for cit in citations:
            if cit.is_replaced or cit.format_issues or not cit.local_match_found:
                issues.append(cit)

        replaced = self.law_checker.check_replaced_laws(document_text)
        for rep in replaced:
            already = any(i.citation_text == rep.citation_text for i in issues)
            if not already:
                issues.append(rep)

        logger.info("check_law_citations: found %d issues", len(issues))
        return issues

    def check_fact_source_binding(self, document_text: str) -> list[FactSourceIssue]:
        issues = []
        fact_match = re.search(r"# 二、本院查明事实.*?(?=# 三|# 四|$)", document_text, re.DOTALL)
        if not fact_match:
            return issues

        lines = fact_match.group(0).split("\n")
        skip_prefixes = ("|", "-", "#", ">", "*", "```", "---")

        for i, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped or line_stripped.startswith(skip_prefixes):
                continue
            if len(line_stripped) < 10:
                continue

            has_citation = bool(re.search(r"[（\(]见", line_stripped))
            has_unsupported = "未见相关书证支持" in line_stripped

            if not has_citation and not has_unsupported:
                if re.search(r"[\u4e00-\u9fff]{5,}", line_stripped):
                    issues.append(FactSourceIssue(
                        line_number=i + 1,
                        line_text=line_stripped[:80],
                        issue_type="无证据引注",
                        h_code="H-1",
                        suggestion='标注证据来源，格式：（见《证据文件名.md》），或标注\u201c上诉人主张...，但截至本操作时未见相关书证支持\u201d',
                    ))

        logger.info("check_fact_source_binding: found %d issues", len(issues))
        return issues

    def check_time_bar(self, document_text: str) -> list[TimeBarIssue]:
        """检测劳动争议案件是否超过1年仲裁时效。"""
        issues = []

        if not _DATEUTIL_AVAILABLE:
            logger.info("check_time_bar: python-dateutil不可用，跳过时效检测")
            return issues

        is_labor_case = bool(re.search(
            r'劳动争议|劳动合同|劳动仲裁|劳动法|劳动合同法',
            document_text[:2000],
        ))
        if not is_labor_case:
            return issues

        resignation_dates = []
        arbitration_dates = []

        resignation_patterns = [
            r'(?:于|自|从)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:离职|解除|终止|辞退|辞呈|辞职|解雇|离开)',
            r'(?:离职|解除|终止|辞退|辞呈|辞职|解雇|离开)[，,]?\s*(?:于|自|从)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(?:于|自|从)?\s*(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})\s*(?:离职|解除|终止|辞退|辞呈|辞职|解雇|离开)',
            r'(?:离职|解除|终止|辞退|辞呈|辞职|解雇|离开)[，,]?\s*(?:于|自|从)?\s*(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})',
        ]
        for pat in resignation_patterns:
            for m in re.finditer(pat, document_text):
                try:
                    date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                    resignation_dates.append(date_str)
                except (ValueError, IndexError):
                    continue

        arbitration_patterns = [
            r'(?:于|自|从)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(?:申请仲裁|提起仲裁|仲裁申请|劳动仲裁)',
            r'(?:申请仲裁|提起仲裁|仲裁申请|劳动仲裁)[，,]?\s*(?:于|自|从)?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(?:于|自|从)?\s*(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})\s*(?:申请仲裁|提起仲裁|仲裁申请|劳动仲裁)',
            r'(?:申请仲裁|提起仲裁|仲裁申请|劳动仲裁)[，,]?\s*(?:于|自|从)?\s*(\d{4})[年\-./](\d{1,2})[月\-./](\d{1,2})',
        ]
        for pat in arbitration_patterns:
            for m in re.finditer(pat, document_text):
                try:
                    date_str = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                    arbitration_dates.append(date_str)
                except (ValueError, IndexError):
                    continue

        if not resignation_dates or not arbitration_dates:
            return issues

        seen_pairs = set()
        for res_date_str in resignation_dates:
            for arb_date_str in arbitration_dates:
                pair_key = f"{res_date_str}:{arb_date_str}"
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                try:
                    res_date = datetime.strptime(res_date_str, "%Y-%m-%d")
                    arb_date = datetime.strptime(arb_date_str, "%Y-%m-%d")
                except ValueError:
                    continue

                deadline = res_date + relativedelta(years=1)
                deadline_str = deadline.strftime("%Y-%m-%d")

                if arb_date > deadline:
                    gap_days = (arb_date - deadline).days
                    issues.append(TimeBarIssue(
                        resignation_date=res_date_str,
                        arbitration_date=arb_date_str,
                        deadline_date=deadline_str,
                        is_time_barred=True,
                        gap_days=gap_days,
                        h_code="H-2",
                        suggestion=f"该诉请可能已超过1年仲裁时效（离职日{res_date_str}，时效届满日{deadline_str}，"
                                   f"申请仲裁日{arb_date_str}，超出{gap_days}天）。"
                                   f"建议在说理部分论证时效中断或对方放弃时效抗辩的事由。",
                    ))

        logger.info("check_time_bar: found %d issues", len(issues))
        return issues

    def check_methodology_replacement(
        self,
        document_text: str,
        complaint_text: str = "",
    ) -> list[MethodologyReplacement]:
        """检测判决书是否替换了起诉状的法律方法论。"""
        replacements = []

        for rule in METHODOLOGY_REPLACEMENT_RULES:
            claim_basis = rule["claim_basis"]
            judgment_basis = rule["judgment_basis"]
            claim_pattern = rule.get("claim_pattern", "")
            judgment_pattern = rule.get("judgment_pattern", "")

            claim_found = False
            judgment_found = False

            if claim_pattern:
                claim_found = bool(re.search(claim_pattern, complaint_text))
            elif claim_basis:
                claim_found = claim_basis in complaint_text

            if judgment_pattern:
                judgment_found = bool(re.search(judgment_pattern, document_text))
            elif judgment_basis:
                judgment_found = judgment_basis in document_text

            if judgment_found and not claim_found:
                replacements.append(MethodologyReplacement(
                    claim_law_basis=claim_basis,
                    judgment_law_basis=judgment_basis,
                    claim_item=rule.get("claim_item", ""),
                    replacement_type=rule.get("replacement_type", "法律依据替换"),
                    impact_analysis=rule.get("impact_analysis", ""),
                    h_code=rule.get("h_code", "H-5"),
                    severity=rule.get("severity", "high"),
                ))

        logger.info("check_methodology_replacement: found %d replacements", len(replacements))
        return replacements

    def _run_pattern_rules(self, document_text: str) -> list[RuleFlag]:
        flags = []

        manifest_case_numbers: set[str] = set()
        related_case_numbers: set[str] = set()
        evidence_timeline: dict[str, str] = {}
        evidence_texts_content = ""
        law_kb_case_numbers: set[str] = set()

        if self.evidence_index and self.evidence_index.loaded:
            manifest_case_numbers = self.evidence_index.get_manifest_case_numbers()
            related_case_numbers = self.evidence_index.get_related_case_numbers(document_text)
            evidence_timeline = self.evidence_index.get_procedural_timeline()
            evidence_texts_content = " ".join(self.evidence_index.evidence_texts.values())

        if self.law_kb and self.law_kb.loaded:
            for _case in self.law_kb.cases:
                if _case.case_number:
                    law_kb_case_numbers.add(_case.case_number)
            _cn_pattern = re.compile(
                r"[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?第?\d+号"
            )
            for _article in self.law_kb.articles:
                for _m in _cn_pattern.finditer(_article.source_file):
                    law_kb_case_numbers.add(_m.group(0))
                for _m in _cn_pattern.finditer(_article.full_text):
                    law_kb_case_numbers.add(_m.group(0))

        verification_ctx: dict[str, object] = {
            "manifest_case_numbers": manifest_case_numbers,
            "related_case_numbers": related_case_numbers,
            "evidence_timeline": evidence_timeline,
            "evidence_texts_content": evidence_texts_content,
            "law_kb_case_numbers": law_kb_case_numbers,
        }

        for rule_id, rule in RULE_ENGINE_PATTERNS.items():
            pattern = rule["pattern"]
            section = rule["section"]
            severity = rule["severity"]
            h_code = rule["h_code"]
            message = rule["message"]

            search_text = self._get_section_text(document_text, section)

            if "requires_absent" in rule:
                absent_pattern = rule["requires_absent"]
                for i, line in enumerate(search_text.split('\n')):
                    if re.search(absent_pattern, line):
                        continue
                    line_matches = list(re.finditer(pattern, line))
                    for match in line_matches[:10]:
                        matched_text = match.group(0)

                        _skip = self._verify_before_flag(
                            rule_id, matched_text, line,
                            verification_ctx, document_text,
                        )
                        if _skip:
                            continue

                        if rule_id == "h1_fabricated_case_number":
                            after_match = line[match.end():match.end() + 20]
                            if re.search(r'[）)]?\s*（见《', after_match):
                                continue

                        flags.append(RuleFlag(
                            rule_id=rule_id,
                            h_code=h_code,
                            sub_type=rule.get("sub_type", ""),
                            severity=severity,
                            message=message,
                            evidence=matched_text[:100],
                            location=f"行{i+1}",
                            line_number=i + 1,
                        ))
            elif "exceptions" in rule:
                exception_pattern = rule["exceptions"]
                for i, line in enumerate(search_text.split('\n')):
                    if re.search(exception_pattern, line):
                        continue
                    line_matches = list(re.finditer(pattern, line))
                    for match in line_matches[:10]:
                        matched_text = match.group(0)

                        _skip = self._verify_before_flag(
                            rule_id, matched_text, line,
                            verification_ctx, document_text,
                        )
                        if _skip:
                            continue

                        flags.append(RuleFlag(
                            rule_id=rule_id,
                            h_code=h_code,
                            sub_type=rule.get("sub_type", ""),
                            severity=severity,
                            message=message,
                            evidence=matched_text[:100],
                            location=f"行{i+1}",
                            line_number=i + 1,
                        ))
            else:
                matches = list(re.finditer(pattern, search_text))
                if matches:
                    known_valid = rule.get("known_valid", [])
                    for match in matches[:10]:
                        matched_text = match.group(0)
                        if known_valid and matched_text in known_valid:
                            continue

                        start_pos = match.start()
                        line_num = search_text[:start_pos].count('\n') + 1

                        _skip = self._verify_before_flag(
                            rule_id, matched_text,
                            line_text="",
                            ctx=verification_ctx,
                            document_text=document_text,
                        )
                        if _skip:
                            continue

                        flags.append(RuleFlag(
                            rule_id=rule_id,
                            h_code=h_code,
                            sub_type=rule.get("sub_type", ""),
                            severity=severity,
                            message=message,
                            evidence=matched_text[:100],
                            location=f"行{line_num}",
                            line_number=line_num,
                        ))

        logger.info("_run_pattern_rules: %d flags", len(flags))
        return flags

    def _get_section_text(self, document_text: str, section: str) -> str:
        if section == "full":
            return document_text
        if section == "header":
            return document_text[:500]
        if section == "footer":
            return document_text[-500:]
        if section == "reasoning":
            m = re.search(r'# 三、本院认为.*?(?=# 四|#|$)', document_text, re.DOTALL)
            return m.group(0) if m else ""
        if section == "judgment_main":
            m = re.search(r'# 四、判决如下.*?(?=#|$)', document_text, re.DOTALL)
            return m.group(0) if m else ""
        if section == "body":
            return document_text
        return document_text

    @staticmethod
    def _is_case_number_verified(
        case_number: str,
        manifest_case_numbers: set[str],
        related_case_numbers: set[str],
        evidence_texts_content: str = "",
        law_kb_case_numbers: set[str] | None = None,
        verbose: bool = False,
    ) -> bool:
        if not case_number:
            if verbose:
                logger.debug("_is_case_number_verified: empty case_number input, returning False")
            return False

        def normalize(cn: str) -> str:
            return re.sub(r'[\s（）\(\)]', '', cn)

        cn_norm = normalize(case_number)
        if verbose:
            logger.debug(
                "_is_case_number_verified [VERBOSE]: input=%s, normalized=%s",
                case_number, cn_norm,
            )

        sources: list[tuple[str, set[str]]] = [
            ("manifest_case_numbers", manifest_case_numbers),
            ("related_case_numbers", related_case_numbers),
        ]
        if law_kb_case_numbers:
            sources.append(("law_kb_case_numbers", law_kb_case_numbers))

        for source_name, source_set in sources:
            if verbose:
                logger.debug(
                    "_is_case_number_verified [VERBOSE]: searching %s (size=%d)",
                    source_name, len(source_set) if isinstance(source_set, set) else 0,
                )
            for mc in source_set:
                mc_norm = normalize(mc)
                if mc_norm == cn_norm:
                    logger.info(
                        "_is_case_number_verified: %s found in %s (raw='%s')",
                        case_number, source_name, mc,
                    )
                    return True

        if verbose:
            logger.debug(
                "_is_case_number_verified [VERBOSE]: not found in %d source sets, trying inner match",
                len(sources),
            )

        inner_match = re.search(
            r'[（\(]?\d{4}[）)]?\s*[^\s，。；]*?民[^\s，。；]*?第?\d+号',
            case_number,
        )
        if inner_match:
            inner_cn = inner_match.group(0)
            inner_norm = normalize(inner_cn)
            if verbose:
                logger.debug(
                    "_is_case_number_verified [VERBOSE]: extracted inner=%s, normalized=%s",
                    inner_cn, inner_norm,
                )
            for source_name, source_set in sources:
                for mc in source_set:
                    if normalize(mc) == inner_norm:
                        logger.info(
                            "_is_case_number_verified: inner %s found in %s (raw='%s')",
                            inner_cn, source_name, mc,
                        )
                        return True

        if evidence_texts_content:
            if verbose:
                logger.debug(
                    "_is_case_number_verified [VERBOSE]: searching evidence_texts (len=%d)",
                    len(evidence_texts_content),
                )
            if case_number in evidence_texts_content:
                logger.info(
                    "_is_case_number_verified: %s found in evidence_texts",
                    case_number,
                )
                return True
            if inner_match and inner_match.group(0) in evidence_texts_content:
                logger.info(
                    "_is_case_number_verified: inner %s found in evidence_texts",
                    inner_match.group(0),
                )
                return True

        if verbose:
            logger.warning(
                "_is_case_number_verified [VERBOSE]: FAILED to verify '%s' - "
                "checked %d source sets + evidence_texts, returning False",
                case_number, len(sources),
            )

        return False

    def _is_law_article_in_kb(self, citation_text: str, verbose: bool = False) -> bool:
        """检查法律条文引用是否能在本地法条库中找到匹配。

        提取《XX法》第X条格式，在 law_kb.articles 中搜索匹配。
        找到则返回 True，表示该引用有本地库支撑，不应标记为幻觉。
        """
        law_match = re.search(r'《([^》]+)》\s*第\s*([一二三四五六七八九十百千零\d]+)\s*条', citation_text)
        if not law_match:
            if verbose:
                logger.debug(
                    "_is_law_article_in_kb [VERBOSE]: no law citation pattern in '%s'",
                    citation_text[:80],
                )
            return False

        law_name = law_match.group(1).strip()
        article_num_raw = law_match.group(2).strip()

        if verbose:
            logger.debug(
                "_is_law_article_in_kb [VERBOSE]: extracted 法名='%s', 条号='%s'",
                law_name, article_num_raw,
            )

        article_num_map = {
            "一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
            "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
        }
        article_num = article_num_raw
        for cn_digit, arabic in article_num_map.items():
            article_num = article_num.replace(cn_digit, arabic)

        if verbose:
            logger.debug(
                "_is_law_article_in_kb [VERBOSE]: arabic article_num='%s'",
                article_num,
            )

        if self.law_kb and self.law_kb.loaded:
            if verbose:
                logger.debug(
                    "_is_law_article_in_kb [VERBOSE]: searching law_kb.articles (count=%d)",
                    len(self.law_kb.articles),
                )
            for article in self.law_kb.articles:
                law_name_match = law_name in article.law_name or article.law_name in law_name
                article_match = article_num in article.article_number or article_num_raw in article.article_number

                if verbose:
                    logger.debug(
                        "_is_law_article_in_kb [VERBOSE]: comparing with article: "
                        "law_name='%s', article_number='%s', law_name_match=%s, article_match=%s",
                        article.law_name, article.article_number,
                        law_name_match, article_match,
                    )

                if law_name_match and article_match:
                    logger.info(
                        "_is_law_article_in_kb: 《%s》第%s条 verified in law_kb (source=%s)",
                        law_name, article_num_raw, article.source_file,
                    )
                    return True

        if self.law_checker:
            local_texts = getattr(self.law_checker, "local_law_texts", {})
            if verbose:
                logger.debug(
                    "_is_law_article_in_kb [VERBOSE]: searching local_law_texts (count=%d)",
                    len(local_texts),
                )
            search_str = f"《{law_name}》"
            for name_key, content in local_texts.items():
                if search_str in name_key or search_str in content:
                    article_pattern_full = r'第\s*' + re.escape(article_num_raw) + r'\s*条'
                    if re.search(article_pattern_full, content):
                        logger.info(
                            "_is_law_article_in_kb: 《%s》第%s条 verified in local_law_texts key=%s",
                            law_name, article_num_raw, name_key,
                        )
                        return True

        if verbose:
            logger.warning(
                "_is_law_article_in_kb [VERBOSE]: FAILED to verify 《%s》第%s条 - "
                "checked law_kb and local_law_texts, returning False",
                law_name, article_num_raw,
            )

        return False

    def _verify_before_flag(
        self,
        rule_id: str,
        matched_text: str,
        line_text: str,
        ctx: dict[str, object],
        document_text: str,
    ) -> bool:
        """统一验证调度器：在各规则标记幻觉之前，先尝试通过本地数据确认引用真实性。

        返回 True 表示该命中已被验证为真实引用，应当跳过（不标记为幻觉）。
        返回 False 表示本地未能验证，正常进入标记流程。
        """

        if rule_id == "h1_fabricated_case_number":
            manifest_cn = ctx.get("manifest_case_numbers", set())
            related_cn = ctx.get("related_case_numbers", set())
            ev_texts = str(ctx.get("evidence_texts_content", ""))
            law_kb_cn = ctx.get("law_kb_case_numbers", set())

            _manifest_size = len(manifest_cn) if isinstance(manifest_cn, set) else 0
            _related_size = len(related_cn) if isinstance(related_cn, set) else 0
            _law_kb_size = len(law_kb_cn) if isinstance(law_kb_cn, set) else 0
            _ev_len = len(ev_texts)

            if isinstance(law_kb_cn, set):
                result = self._is_case_number_verified(
                    matched_text, manifest_cn, related_cn, ev_texts, law_kb_cn,
                    verbose=self.verbose,
                )
            else:
                result = self._is_case_number_verified(
                    matched_text, manifest_cn, related_cn, ev_texts,
                    verbose=self.verbose,
                )
            if not result:
                logger.info(
                    "_verify_before_flag [REASON] h1_fabricated_case_number: "
                    "match='%s' | sources_checked=(manifest=%d, related=%d, law_kb=%d, ev_texts=%d_chars) | "
                    "all_exhausted=True | will_flag_as_hallucination=%s",
                    matched_text[:80], _manifest_size, _related_size, _law_kb_size, _ev_len,
                    "NO" if self.web_verifier else "YES",
                )

            if not result and self.web_verifier:
                try:
                    web_result = self.web_verifier.verify_online(
                        matched_text, target_type="案例", local_verified=False,
                    )
                    if web_result and web_result.is_verified:
                        logger.info(
                            "_verify_before_flag [WEB_OK] h1_fabricated_case_number '%s' "
                            "verified online via %s, skipping flag",
                            matched_text[:60], web_result.verification_source,
                        )
                        return True
                    else:
                        logger.info(
                            "_verify_before_flag [WEB_FAIL] h1_fabricated_case_number '%s' "
                            "online unverified, status=%s",
                            matched_text[:60], web_result.verification_status if web_result else "None",
                        )
                except Exception as e:
                    logger.warning(
                        "_verify_before_flag [WEB_ERR] h1_fabricated_case_number '%s': %s",
                        matched_text[:60], e,
                    )

            return result

        if rule_id == "h1_fabricated_judicial_doc_number":
            result = self._is_judicial_doc_verified(matched_text)
            if not result:
                logger.info(
                    "_verify_before_flag [REASON] h1_fabricated_judicial_doc_number: "
                    "match='%s' | _is_judicial_doc_verified=False | all_exhausted=True | "
                    "will_flag_as_hallucination=%s",
                    matched_text[:80], "NO" if self.web_verifier else "YES",
                )
            if not result and self.web_verifier:
                try:
                    web_result = self.web_verifier.verify_online(
                        matched_text, target_type="司法文书", local_verified=False,
                    )
                    if web_result and web_result.is_verified:
                        logger.info(
                            "_verify_before_flag [WEB_OK] h1_fabricated_judicial_doc_number '%s' "
                            "verified online via %s, skipping flag",
                            matched_text[:60], web_result.verification_source,
                        )
                        return True
                    else:
                        logger.info(
                            "_verify_before_flag [WEB_FAIL] h1_fabricated_judicial_doc_number '%s' "
                            "online unverified, status=%s",
                            matched_text[:60], web_result.verification_status if web_result else "None",
                        )
                except Exception as e:
                    logger.warning(
                        "_verify_before_flag [WEB_ERR] h1_fabricated_judicial_doc_number '%s': %s",
                        matched_text[:60], e,
                    )
            return result

        if rule_id == "h2_procedural_date_fabrication":
            evidence_timeline = ctx.get("evidence_timeline", {})
            if evidence_timeline and isinstance(evidence_timeline, dict):
                _in_timeline = self._is_procedural_date_in_evidence(
                    matched_text, evidence_timeline,
                )
                if not _in_timeline:
                    logger.info(
                        "_verify_before_flag [REASON] h2_procedural_date_fabrication: "
                        "match='%s' | timeline_entries=%d | date_not_in_timeline=True | "
                        "will_flag_as_hallucination=YES",
                        matched_text[:80], len(evidence_timeline),
                    )
                return _in_timeline
            logger.info(
                "_verify_before_flag [REASON] h2_procedural_date_fabrication: "
                "match='%s' | no_evidence_timeline_available=True | "
                "will_flag_as_hallucination=YES",
                matched_text[:80],
            )
            return False

        if rule_id == "h2_law_article_mismatch":
            _kb_ok = self._is_law_article_in_kb(matched_text, verbose=self.verbose)
            if _kb_ok:
                return True
            evidence_texts_content = str(ctx.get("evidence_texts_content", ""))
            _ev_found = False
            if evidence_texts_content:
                law_match = re.search(
                    r'《([^》]+)》\s*第\s*([一二三四五六七八九十百千零\d]+)\s*条',
                    matched_text,
                )
                if law_match and law_match.group(0) in evidence_texts_content:
                    logger.info(
                        "_verify_before_flag: %s found in evidence_texts_content, skipping",
                        matched_text,
                    )
                    return True
                elif law_match:
                    _ev_found = False

            logger.info(
                "_verify_before_flag [REASON] h2_law_article_mismatch: "
                "match='%s' | law_kb_found=%s | ev_texts_found=%s | ev_texts_len=%d | "
                "all_exhausted=True | will_flag_as_hallucination=%s",
                matched_text[:80], _kb_ok, _ev_found, len(evidence_texts_content),
                "NO" if self.web_verifier else "YES",
            )

            if self.web_verifier:
                try:
                    web_result = self.web_verifier.verify_online(
                        matched_text, target_type="法条", local_verified=False,
                    )
                    if web_result and web_result.is_verified:
                        logger.info(
                            "_verify_before_flag [WEB_OK] h2_law_article_mismatch '%s' "
                            "verified online via %s, skipping flag",
                            matched_text[:60], web_result.verification_source,
                        )
                        return True
                    else:
                        logger.info(
                            "_verify_before_flag [WEB_FAIL] h2_law_article_mismatch '%s' "
                            "online unverified, status=%s",
                            matched_text[:60], web_result.verification_status if web_result else "None",
                        )
                except Exception as e:
                    logger.warning(
                        "_verify_before_flag [WEB_ERR] h2_law_article_mismatch '%s': %s",
                        matched_text[:60], e,
                    )

            return False

        if rule_id == "h1_fabricated_fact_detail":
            if line_text and re.search(r'[（\(]见《?[^》\)]+》?[）\)]', line_text):
                logger.info(
                    "_verify_before_flag: fact_detail line has evidence citation, skipping",
                )
                return True
            logger.info(
                "_verify_before_flag [REASON] h1_fabricated_fact_detail: "
                "match='%s' | evidence_citation_in_line=False | "
                "will_flag_as_hallucination=YES",
                matched_text[:80],
            )
            return False

        if rule_id in ("h5_calculation_amount_mismatch", "h5_interest_calculation_anomaly"):
            if self.claim_parser.claim_limits:
                amount_match = re.search(
                    r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元', matched_text,
                )
                if amount_match:
                    amt_str = amount_match.group(1).replace(",", "").replace("，", "")
                    try:
                        amt_val = float(amt_str)
                        for claim_item, claim_max in self.claim_parser.claim_limits.items():
                            if abs(amt_val - claim_max) < 0.01:
                                logger.info(
                                    "_verify_before_flag: amount %s matches claim item '%s'=%s, skipping",
                                    amt_val, claim_item, claim_max,
                                )
                                return True
                    except (ValueError, TypeError):
                        pass
                if self.verbose:
                    logger.info(
                        "_verify_before_flag [REASON] %s: "
                        "match='%s' | claim_limits_checked=%d | no_match_found=True | "
                        "will_flag_as_hallucination=YES",
                        rule_id, matched_text[:80], len(self.claim_parser.claim_limits),
                    )
            else:
                logger.info(
                    "_verify_before_flag [REASON] %s: "
                    "match='%s' | no_claim_limits_available=True | "
                    "will_flag_as_hallucination=YES",
                    rule_id, matched_text[:80],
                )
            return False

        if rule_id == "h1_simulated_case_annotated":
            evidence_texts_content = str(ctx.get("evidence_texts_content", ""))
            if evidence_texts_content:
                cn_match = re.search(
                    r"[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?第?\d+号",
                    matched_text,
                )
                if cn_match and (cn_match.group(0) in evidence_texts_content):
                    logger.info(
                        "_verify_before_flag: h1_simulated_case_annotated '%s' "
                        "found in evidence_texts, skipping",
                        matched_text[:60],
                    )
                    return True
            logger.info(
                "_verify_before_flag [REASON] h1_simulated_case_annotated: "
                "match='%s' | evidence_texts_len=%d | case_not_found_in_evidence=True | "
                "will_flag_as_hallucination=YES",
                matched_text[:80], len(evidence_texts_content),
            )
            return False

        return False

    @staticmethod
    def _is_procedural_date_in_evidence(
        matched_text: str,
        evidence_timeline: dict[str, str],
    ) -> bool:
        """检查H-2程序日期杜撰规则的命中文本中的日期是否存在于证据时间线中。

        如果证据文件中记录了相同的程序性日期，则该日期引用有据可依，
        不应标记为杜撰，返回True以过滤该误报。
        """
        date_match = re.search(
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            matched_text,
        )
        if not date_match:
            return False

        try:
            doc_date = f"{date_match.group(1)}-{int(date_match.group(2)):02d}-{int(date_match.group(3)):02d}"
        except ValueError:
            return False

        for date_type, ev_date in evidence_timeline.items():
            if doc_date == ev_date:
                logger.info(
                    "_is_procedural_date_in_evidence: date %s matched evidence timeline '%s=%s', filtering H-2 flag",
                    doc_date, date_type, ev_date,
                )
                return True

        return False

    def _is_judicial_doc_verified(self, doc_number: str) -> bool:
        law_kb_articles = getattr(self.law_kb, "articles", []) if self.law_kb else []
        for article in law_kb_articles:
            if doc_number in getattr(article, "source_file", ""):
                logger.info(
                    "_is_judicial_doc_verified: %s found in law_kb.articles source_file=%s",
                    doc_number, article.source_file,
                )
                return True

        local_texts = getattr(self.law_checker, "local_law_texts", {}) if self.law_checker else {}
        for name_key, content in local_texts.items():
            if doc_number in name_key:
                logger.info(
                    "_is_judicial_doc_verified: %s found in law_checker.local_law_texts key=%s",
                    doc_number, name_key,
                )
                return True

        return False

    def _group_flags_by_dimension(
        self,
        rule_flags: list[RuleFlag],
        structure_issues: list[StructureCheckItem],
        citation_frauds: list[CitationFraudItem],
        claim_violations: list[ClaimBoundaryItem],
        syllogism_breaks: list[SyllogismBreakItem],
        rhetoric_items: list[RhetoricItem],
        law_citation_issues: list[LawCitationItem],
        fact_source_issues: list[FactSourceIssue] | None = None,
        time_bar_issues: list[TimeBarIssue] | None = None,
        methodology_replacements: list[MethodologyReplacement] | None = None,
        claim_comparisons: list[ClaimComparisonItem] | None = None,
    ) -> dict[str, HallucinationDimensionResult]:
        from .config import DIMENSION_TITLES

        dims: dict[str, HallucinationDimensionResult] = {}
        for dim_key in DIMENSION_TITLES:
            raw_code = dim_key.split("_")[0].upper()
            h_code_formatted = f"H-{raw_code[1:]}" if len(raw_code) > 1 and raw_code[0] == "H" else raw_code
            dims[dim_key] = HallucinationDimensionResult(
                dimension=dim_key,
                dimension_title=DIMENSION_TITLES[dim_key],
                h_code=h_code_formatted,
            )

        for flag in rule_flags:
            h_code = flag.h_code
            for dim_key in dims:
                if dim_key.startswith(h_code.lower().replace("-", "")):
                    dims[dim_key].rule_flags.append(flag)
                    break

        for si in structure_issues:
            h_code = si.h_code
            for dim_key in dims:
                if dim_key.startswith(h_code.lower().replace("-", "")):
                    dims[dim_key].rule_flags.append(RuleFlag(
                        rule_id="structure_check",
                        h_code=h_code,
                        sub_type="文书结构缺失",
                        severity=si.severity,
                        message=si.message,
                        evidence=si.heading,
                    ))
                    break

        for cf in citation_frauds:
            dims["h1_sourceless_fabrication"].rule_flags.append(RuleFlag(
                rule_id="citation_fraud",
                h_code="H-1",
                sub_type="引注欺诈",
                severity="high",
                message=f"引注欺诈：引用的证据源《{cf.citation}》不存在于证据清单中",
                evidence=cf.citation,
            ))

        for cv in claim_violations:
            dims["h5_claim_boundary_breach"].rule_flags.append(RuleFlag(
                rule_id="claim_boundary",
                h_code="H-5",
                sub_type=cv.violation_type,
                severity="high",
                message=f"诉求边界突破：{cv.violation_type}，项目'{cv.judgment_item}'金额{cv.judgment_amount}元"
                        + (f"，诉请上限{cv.claim_max}元" if cv.claim_max > 0 else "，无对应诉请"),
                evidence=f"{cv.judgment_item}: {cv.judgment_amount}元",
            ))

        for sb in syllogism_breaks:
            dims["h3_evidence_chain_break"].rule_flags.append(RuleFlag(
                rule_id="syllogism_break",
                h_code="H-3",
                sub_type=f"三段论缺失{sb.missing_part}",
                severity="high",
                message=f"说理断层：缺失{sb.missing_part}",
                evidence=sb.line_text,
                line_number=sb.line_number,
            ))

        for ri in rhetoric_items:
            if not ri.is_exception:
                dims["h4_subjective_rhetoric"].rule_flags.append(RuleFlag(
                    rule_id="rhetoric_check",
                    h_code="H-4",
                    sub_type="主观修辞",
                    severity=ri.severity,
                    message=f"主观修辞：'{ri.keyword}'",
                    evidence=ri.line_text,
                    line_number=ri.line_number,
                ))

        for lc in law_citation_issues:
            if lc.is_replaced:
                dims["h2_law_misapplication"].rule_flags.append(RuleFlag(
                    rule_id="replaced_law",
                    h_code="H-2",
                    sub_type="引用已废止法律",
                    severity="high",
                    message=f"引用已废止法律《{lc.law_name}》，已被{lc.replaced_by}取代",
                    evidence=lc.citation_text,
                ))
            for fmt_issue in lc.format_issues:
                if fmt_issue != "引用已废止法律":
                    dims["h2_law_misapplication"].rule_flags.append(RuleFlag(
                        rule_id="law_citation_format",
                        h_code="H-2",
                        sub_type="引用格式问题",
                        severity="medium",
                        message=f"法条引用格式问题：{fmt_issue}",
                        evidence=lc.citation_text,
                ))

        for fsi in fact_source_issues or []:
            dims["h1_sourceless_fabrication"].rule_flags.append(RuleFlag(
                rule_id="fact_source_binding",
                h_code="H-1",
                sub_type=fsi.issue_type,
                severity="high",
                message=f"事实缺乏证据绑定：{fsi.issue_type}",
                evidence=fsi.line_text,
                line_number=fsi.line_number,
            ))

        for tb in time_bar_issues or []:
            dims["h2_law_misapplication"].rule_flags.append(RuleFlag(
                rule_id="time_bar_check",
                h_code="H-2",
                sub_type="仲裁时效超期",
                severity="high",
                message=f"仲裁时效超期：离职日{tb.resignation_date}，申请仲裁日{tb.arbitration_date}，"
                        f"超出{tb.gap_days}天",
                evidence=f"离职{tb.resignation_date}→仲裁{tb.arbitration_date}",
            ))

        for mr in methodology_replacements or []:
            dims["h5_claim_boundary_breach"].rule_flags.append(RuleFlag(
                rule_id="methodology_replacement",
                h_code=mr.h_code,
                sub_type=mr.replacement_type,
                severity=mr.severity,
                message=f"法律方法论替换：起诉状援引{mr.claim_law_basis}，"
                        f"判决书实际使用{mr.judgment_law_basis}",
                evidence=f"{mr.claim_law_basis}→{mr.judgment_law_basis}",
            ))

        for cc in claim_comparisons or []:
            dims["h5_claim_boundary_breach"].rule_flags.append(RuleFlag(
                rule_id="claim_comparison",
                h_code="H-5",
                sub_type="诉请对比项",
                severity="info",
                message=f"诉请对比：{cc.item_name} 诉请{cc.claim_amount}元 vs 判决{cc.judgment_amount}元",
                evidence=f"{cc.item_name}: {cc.claim_amount}元→{cc.judgment_amount}元",
            ))

        return dims

    def _calculate_score(self, result: HallucinationDetectionResult) -> float:
        score = 0.0
        severity_weights = {
            "critical": 12.0,
            "high": 6.0,
            "medium": 3.0,
            "low": 1.0,
            "info": 0.5,
        }
        dim_weight_map = {
            "h1_sourceless_fabrication": 1.25,
            "h2_law_misapplication": 1.0,
            "h3_evidence_chain_break": 1.0,
            "h4_subjective_rhetoric": 0.5,
            "h5_claim_boundary_breach": 1.0,
            "h6_nontext_evidence_fail": 0.25,
        }

        rule_hit_counts: dict[str, int] = {}

        for dim in result.dimensions:
            dim_weight = dim_weight_map.get(dim.dimension, 1.0)
            for flag in dim.rule_flags:
                weight = severity_weights.get(flag.severity, 1.0)
                rule_key = f"{dim.dimension}:{flag.rule_id}"
                hit_count = rule_hit_counts.get(rule_key, 0)
                rule_hit_counts[rule_key] = hit_count + 1
                if hit_count == 0:
                    score += weight * dim_weight
                else:
                    score += weight * dim_weight * 0.3

        return min(score, 100.0)

    def _determine_risk_grade(self, score: float) -> tuple[str, str]:
        from .config import RISK_GRADES
        for grade, (low, high, desc) in RISK_GRADES.items():
            if low <= score < high:
                return grade, desc
        if score >= 50:
            return "F", RISK_GRADES["F"][2]
        return "A", RISK_GRADES["A"][2]

    def check_calculation_accuracy(self, document_text: str) -> list[CalculationIssue]:
        """检测关键计算项的准确性，包括加班工资、二倍工资差额、利息等。"""
        issues = []

        overtime_issues = self._check_overtime_calculation(document_text)
        issues.extend(overtime_issues)

        double_wage_issues = self._check_double_wage_calculation(document_text)
        issues.extend(double_wage_issues)

        summary_issues = self._check_summary_calculation(document_text)
        issues.extend(summary_issues)

        circular_issues = self._check_circular_calculation(document_text)
        issues.extend(circular_issues)

        logger.info("check_calculation_accuracy: found %d issues", len(issues))
        return issues

    def _check_overtime_calculation(self, text: str) -> list[CalculationIssue]:
        """核验加班工资计算。"""
        issues = []

        overtime_patterns = [
            r'加班工资\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'加班费\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'支付(?:原告|被告|当事人)?\s*加班工资\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'支付(?:原告|被告|当事人)?\s*加班费\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
        ]

        for pat in overtime_patterns:
            for match in re.finditer(pat, text):
                amount_str = match.group(1).replace(',', '').replace('，', '')
                try:
                    amount = float(amount_str)
                except ValueError:
                    continue

                line_num = text[:match.start()].count('\n') + 1

                monthly_wage = self._extract_monthly_wage(text)
                days_match = re.search(r'(\d+)\s*天(?:.*?加班|加班.*?天)', text)
                if not days_match:
                    days_match = re.search(r'加班\s*(\d+)\s*天', text)
                if not days_match:
                    days_match = re.search(r'(\d+)\s*天.*?加班', text)
                days = int(days_match.group(1)) if days_match else 0

                if monthly_wage > 0 and days > 0:
                    daily_rate = monthly_wage / 21.75
                    expected_rest = days * daily_rate * 2
                    expected_holiday = days * daily_rate * 3
                    expected_normal = days * daily_rate * 1.5

                    tolerances = [
                        ("休息日加班（2倍）", expected_rest),
                        ("法定节假日加班（3倍）", expected_holiday),
                        ("工作日延长（1.5倍）", expected_normal),
                    ]

                    matched = False
                    for label, expected in tolerances:
                        if abs(amount - expected) < max(1.0, expected * 0.02):
                            matched = True
                            break

                    if not matched and amount > 0:
                        issues.append(CalculationIssue(
                            item="加班工资",
                            amount=amount,
                            expected=expected_rest,
                            formula=f"月工资{monthly_wage:,.2f}÷21.75×{days}天×倍率",
                            issue_type="计算错误",
                            message=f"加班工资{amount:,.2f}元与按公式还原的金额不符"
                                    f"（休息日2倍={expected_rest:,.2f}，"
                                    f"法定节假日3倍={expected_holiday:,.2f}，"
                                    f"工作日1.5倍={expected_normal:,.2f}）",
                            severity="high",
                            h_code="H-5",
                            line_number=line_num,
                            parameters={"monthly_wage": monthly_wage, "days": days, "daily_rate": round(daily_rate, 2)},
                        ))

                break

        return issues

    def _check_double_wage_calculation(self, text: str) -> list[CalculationIssue]:
        """核验二倍工资差额计算。"""
        issues = []

        double_wage_patterns = [
            r'二倍工资差额\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'未签.*?劳动合同.*?工资差额\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'支付二倍工资差额\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
        ]

        for pat in double_wage_patterns:
            for match in re.finditer(pat, text):
                amount_str = match.group(1).replace(',', '').replace('，', '')
                try:
                    amount = float(amount_str)
                except ValueError:
                    continue

                line_num = text[:match.start()].count('\n') + 1

                monthly_base_match = re.search(
                    r'二倍工资.*?基数.*?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元/月',
                    text,
                )
                months_match = re.search(r'未签.*?(\d+)\s*个月', text)

                if not monthly_base_match:
                    monthly_base_match = re.search(
                        r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元/月.*?二倍工资',
                        text,
                    )

                if monthly_base_match and months_match:
                    monthly_base = float(monthly_base_match.group(1).replace(',', '').replace('，', ''))
                    months = int(months_match.group(1))
                    expected = monthly_base * months

                    if abs(amount - expected) > max(1.0, expected * 0.02):
                        issues.append(CalculationIssue(
                            item="二倍工资差额",
                            amount=amount,
                            expected=expected,
                            formula=f"{monthly_base:,.2f}元/月×{months}个月={expected:,.2f}元",
                            issue_type="计算错误",
                            message=f"二倍工资差额{amount:,.2f}元与按公式还原的金额{expected:,.2f}元不符",
                            severity="high",
                            h_code="H-5",
                            line_number=line_num,
                            parameters={"monthly_base": monthly_base, "months": months},
                        ))

                break

        return issues

    def _check_summary_calculation(self, text: str) -> list[CalculationIssue]:
        """核验判决主文中各项金额的加总。"""
        issues = []

        main_match = re.search(r'# 四、判决如下.*?(?=#|$)', text, re.DOTALL)
        if not main_match:
            main_match = re.search(r'判决如下[：:]*\s*(.*?)(?=如未按|$)', text, re.DOTALL)
        if not main_match:
            return issues

        main_text = main_match.group(0)

        item_amounts = []
        for match in re.finditer(
            r'(?:支付|赔偿|补足|给付)\s*([^，、\n：（\(]*?(?:金|费|工资|差额)?)(?:人民币\s*)?(?:共计|合计|)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            main_text,
        ):
            item_name = match.group(1).strip()
            amount_str = match.group(2).replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue
            if val > 0 and len(item_name) >= 2:
                item_amounts.append((item_name, val))

        for match in re.finditer(
            r'(?:共计|合计|总计|应支付|应赔偿)\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            main_text,
        ):
            total_str = match.group(1).replace(',', '').replace('，', '')
            try:
                total_amount = float(total_str)
            except ValueError:
                continue

            line_num = text[:match.start()].count('\n') + 1
            items_sum = sum(v for _, v in item_amounts)

            if items_sum > 0 and abs(total_amount - items_sum) > max(1.0, items_sum * 0.02):
                issues.append(CalculationIssue(
                    item="判决主文金额汇总",
                    amount=total_amount,
                    expected=items_sum,
                    formula=" + ".join(f"{n}{v:,.2f}" for n, v in item_amounts),
                    issue_type="计算错误",
                    message=f"判决主文合计金额{total_amount:,.2f}元与各项明细加总{items_sum:,.2f}元不符"
                            f"（差额{abs(total_amount - items_sum):,.2f}元）",
                    severity="high" if abs(total_amount - items_sum) / max(items_sum, 1) > 0.1 else "medium",
                    h_code="H-5",
                    line_number=line_num,
                    parameters={"item_count": len(item_amounts), "items_sum": items_sum},
                ))

        return issues

    def _check_circular_calculation(self, text: str) -> list[CalculationIssue]:
        """检测逻辑循环计算——将推定性金额用于计算惩罚性赔偿。"""
        issues = []

        bonus_in_double_wage = re.search(
            r'奖金.*?分摊.*?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元/月.*?二倍工资',
            text,
        )
        if not bonus_in_double_wage:
            bonus_in_double_wage = re.search(
                r'二倍工资.*?基数.*?奖金.*?(\d+(?:[,，]\d{3})*(?:\.\d+)?)',
                text,
            )

        if bonus_in_double_wage:
            try:
                bonus_amount = float(bonus_in_double_wage.group(1).replace(',', '').replace('，', ''))
            except ValueError:
                bonus_amount = 0

            if bonus_amount > 0:
                line_num = text[:bonus_in_double_wage.start()].count('\n') + 1
                issues.append(CalculationIssue(
                    item="二倍工资基数中的奖金分摊",
                    amount=bonus_amount,
                    expected=0,
                    formula="推定性奖金分摊 × 未签合同月数",
                    issue_type="逻辑循环",
                    message="将推定性的、尚未实际发生的奖金分摊纳入二倍工资基数，"
                            "存在逻辑循环风险——即'本判决认定的奖金标准'被用于计算"
                            "'未签劳动合同的惩罚性赔偿'，而这两者在诉讼中是同一审级同时确定的。"
                            "建议在判决理由中增加对'推定收入纳入二倍基数'的专门论证，"
                            "或提供保守路径和激进路径并行展示。",
                    severity="medium",
                    h_code="H-5",
                    line_number=line_num,
                    parameters={"bonus_monthly": bonus_amount},
                ))

        double_wage_as_penalty_base = re.search(
            r'以二倍工资差额\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元为基数.*?加付赔偿金',
            text,
        )
        if not double_wage_as_penalty_base:
            double_wage_as_penalty_base = re.search(
                r'二倍工资差额.*?基数.*?加付赔偿金',
                text,
            )

        if double_wage_as_penalty_base and not bonus_in_double_wage:
            try:
                base_amount = (
                    float(
                        double_wage_as_penalty_base.group(1)
                        .replace(',', '').replace('，', '')
                    )
                    if double_wage_as_penalty_base.lastindex else 0
                )
            except (ValueError, IndexError):
                base_amount = 0

            line_num = text[:double_wage_as_penalty_base.start()].count('\n') + 1
            issues.append(CalculationIssue(
                item="以二倍工资差额为加付赔偿金基数",
                amount=base_amount,
                expected=0,
                formula="二倍工资差额 × 加付比例",
                issue_type="逻辑循环",
                message="以二倍工资差额（推定性金额）作为加付赔偿金的计算基数，"
                        "存在逻辑循环风险——二倍工资差额本身就是推定性赔偿，"
                        "将其作为惩罚性赔偿的基数会导致'推定→惩罚→推定'的循环。"
                        "加付赔偿金的基数应为实际拖欠的劳动报酬，而非推定性金额。",
                severity="high",
                h_code="H-5",
                line_number=line_num,
                parameters={"base_amount": base_amount},
            ))

        return issues

    def _extract_monthly_wage(self, text: str) -> float:
        """从文书中提取月工资标准。"""
        patterns = [
            r'月工资\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'月工资\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元/月',
            r'月均.*?收入\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'固定工资\s*[：:]\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
        ]
        for pat in patterns:
            match = re.search(pat, text)
            if match:
                return float(match.group(1).replace(',', '').replace('，', ''))
        return 0.0

    _CLAIM_SYNONYMS: dict[str, list[str]] = {
        "工资": ["劳动报酬", "工资报酬", "劳动工资", "薪酬", "薪资"],
        "拖欠工资": ["欠付工资", "克扣工资", "未付工资", "拖欠劳动报酬"],
        "克扣绩效工资": ["拖欠绩效工资", "绩效工资差额", "扣减绩效工资"],
        "二倍工资": ["未签劳动合同二倍工资", "双倍工资", "二倍工资差额"],
        "赔偿金": ["损害赔偿金", "违约赔偿金"],
        "经济补偿金": ["经济补偿", "解除劳动合同经济补偿金", "离职补偿金"],
        "加付赔偿金": ["加付赔偿", "额外赔偿金"],
        "加班费": ["加班工资", "加班费差额", "加班劳动报酬"],
        "奖金": ["年终奖", "绩效奖金", "项目奖金", "年底奖金"],
        "二倍赔偿金": ["惩罚性赔偿金", "双倍赔偿金"],
    }

    @staticmethod
    def _compute_jaccard(s1: str, s2: str) -> float:
        chars1 = set(s1)
        chars2 = set(s2)
        if not chars1 or not chars2:
            return 0.0
        return len(chars1 & chars2) / len(chars1 | chars2)

    def _match_claim_item(
        self,
        judgment_name: str,
        claim_limits: dict[str, float],
    ) -> tuple[str | None, float]:
        """将判决项目名与诉请项目名进行模糊匹配。

        Returns:
            (best_claim_key, match_score): 最佳匹配的诉请项目名和匹配分数。
        """
        j_name = judgment_name.strip()

        for claim_item in claim_limits:
            if j_name == claim_item:
                return claim_item, 1.0
            if j_name in claim_item or claim_item in j_name:
                shorter = min(j_name, claim_item, key=len)
                score = len(shorter) / max(len(j_name), len(claim_item))
                if score >= 0.5:
                    return claim_item, score

        for claim_item in claim_limits:
            for syn_root, syn_list in self._CLAIM_SYNONYMS.items():
                all_terms = [syn_root] + syn_list
                j_in_syn = any(s in j_name for s in all_terms)
                c_in_syn = any(s in claim_item for s in all_terms)
                if j_in_syn and c_in_syn:
                    return claim_item, 0.8

        best_claim = None
        best_score = 0.0
        for claim_item in claim_limits:
            score = self._compute_jaccard(j_name, claim_item)
            if score > best_score:
                best_score = score
                best_claim = claim_item

        if best_score >= 0.4:
            return best_claim, best_score

        return None, 0.0

    def build_claim_comparisons(
        self,
        document_text: str,
        complaint_text: str = "",
    ) -> list[ClaimComparisonItem]:
        """构建判决金额与诉请金额的逐项对比表。"""
        comparisons = []

        if not self.claim_parser.claim_limits:
            return comparisons

        main_match = re.search(r'# 四、判决如下.*?(?=#|$)', document_text, re.DOTALL)
        if not main_match:
            main_match = re.search(r'判决如下[：:]*\s*(.*?)(?=如未按|$)', document_text, re.DOTALL)
        if not main_match:
            return comparisons

        main_text = main_match.group(0)

        judgment_items = []

        patterns = [
            r'(?:支付|赔偿|补足|给付)\s*'
            r'((?:二倍|加付|经济补偿|拖欠|克扣|加班|待岗|降薪|绩效)?'
            r'(?:工资|赔偿金|补偿金|差额|奖金|加班费|提成)?)\s*'
            r'(?:共计|合计|为)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'（[一二三四五六七八九十]+）[^\n]*?'
            r'(?:克扣|拖欠|降薪|待岗|加班|二倍|加付|绩效|年底|项目|股票|经济补偿)'
            r'[^\n]*?(?:工资|赔偿金|补偿金|差额|奖金|加班费|提成)'
            r'[^\n]*?(?:人民币\s*)?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            r'（[一二三四五六七八九十]+）[^\n]*?'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
        ]

        seen_positions = set()
        for pat in patterns:
            for match in re.finditer(pat, main_text):
                pos = match.start()
                if pos in seen_positions:
                    continue
                seen_positions.add(pos)

                if match.lastindex == 2:
                    item_name = match.group(1).strip()
                    amount_str = match.group(2)
                else:
                    amount_str = match.group(1)
                    item_name = re.sub(r'（[一二三四五六七八九十]+）|\s+|人民币|\.\d+元|[\d,，]+元', '', match.group(0))
                    item_name = item_name.strip('；：,.')

                amount_str = amount_str.replace(',', '').replace('，', '')
                try:
                    val = float(amount_str)
                except ValueError:
                    continue
                if val > 0 and len(item_name) >= 2:
                    judgment_items.append((item_name, val))

        for j_name, j_amount in judgment_items:
            best_claim, match_score = self._match_claim_item(j_name, self.claim_parser.claim_limits)

            if best_claim:
                claim_amount = self.claim_parser.claim_limits[best_claim]
                ratio = j_amount / claim_amount if claim_amount > 0 else 0

                if ratio <= 1.0:
                    nature = "一致"
                elif ratio <= 1.2:
                    nature = "轻微超出"
                elif ratio <= 2.0:
                    nature = "明显超出"
                else:
                    nature = "显著超出"

                strategy_eval = ""
                if ratio >= 2.0:
                    strategy_eval = (
                        "从劳动者代理人视角，若能通过同工同酬等法定方法确立更高的计算基数，"
                        "则超出部分在法理上是自洽的。问题不在于法理，而在于："
                        "起诉状本身使用的是更保守的算法，判决书应采用与起诉状一致的逻辑起点再行扩展，"
                        "而非完全替换方法论。建议在判决理由中增加过渡说明，"
                        "明确新旧算法的差异和采纳新算法的理由。"
                    )
                elif ratio > 1.0:
                    strategy_eval = "建议在判决理由中说明计算基数调整的依据。"

                comparisons.append(ClaimComparisonItem(
                    item_name=j_name,
                    claim_amount=claim_amount,
                    judgment_amount=j_amount,
                    claim_description=f"诉请上限{claim_amount:,.2f}元",
                    judgment_description=f"判决{ j_amount:,.2f}元",
                    deviation_ratio=round(ratio, 2),
                    nature=nature,
                    strategy_eval=strategy_eval,
                    is_consistent=(ratio <= 1.0),
                ))
            else:
                comparisons.append(ClaimComparisonItem(
                    item_name=j_name,
                    claim_amount=0.0,
                    judgment_amount=j_amount,
                    claim_description="无对应诉请",
                    judgment_description=f"判决{j_amount:,.2f}元",
                    deviation_ratio=0.0,
                    nature="项目越权",
                    strategy_eval="若该给付项目系二审中基于新事实或变更诉求而产生，"
                                  "应在判决理由中明确说明其诉请基础。",
                    is_consistent=False,
                ))

        return comparisons

    def _check_procedural_time_limits(
        self,
        document_text: str,
        result: HallucinationDetectionResult,
    ) -> None:
        """校验程序性时效——上诉期、仲裁起诉期等，并与证据时间线交叉验证。"""
        if not _DATEUTIL_AVAILABLE:
            return

        dates = self._extract_procedural_dates(document_text)
        if not dates:
            return

        evidence_timeline: dict[str, str] = {}
        if self.evidence_index and self.evidence_index.loaded:
            evidence_timeline = self.evidence_index.get_procedural_timeline()

        if evidence_timeline:
            for date_type, doc_date in list(dates.items()):
                ev_date = evidence_timeline.get(date_type)
                if ev_date and ev_date != doc_date:
                    result.time_bar_issues.append(TimeBarIssue(
                        resignation_date=ev_date,
                        arbitration_date=doc_date,
                        deadline_date="",
                        is_time_barred=False,
                        gap_days=0,
                        h_code="H-2",
                        suggestion=f"文书中的{date_type}日期{doc_date}与证据文件中的{date_type}日期{ev_date}不一致，"
                                   f"请核实哪个日期正确。",
                    ))

        if "送达" in dates and "上诉" in dates:
            delivery_date_str = dates["送达"]
            appeal_date_str = dates["上诉"]
            try:
                delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d")
                appeal_date = datetime.strptime(appeal_date_str, "%Y-%m-%d")
                deadline = delivery_date + relativedelta(days=15)
                if appeal_date > deadline:
                    gap = (appeal_date - deadline).days
                    result.time_bar_issues.append(TimeBarIssue(
                        resignation_date=delivery_date_str,
                        arbitration_date=appeal_date_str,
                        deadline_date=deadline.strftime("%Y-%m-%d"),
                        is_time_barred=True,
                        gap_days=gap,
                        h_code="H-2",
                        suggestion=f"上诉日期{appeal_date_str}超出一审判决送达后15日上诉期"
                                   f"（送达日{delivery_date_str}，届满日{deadline.strftime('%Y-%m-%d')}，"
                                   f"超出{gap}天）。请核实上诉日期是否正确，"
                                   f"或是否存在时效中断事由。",
                    ))
            except ValueError:
                pass

        if "仲裁裁决" in dates and "起诉" in dates:
            arb_date_str = dates["仲裁裁决"]
            suit_date_str = dates["起诉"]
            try:
                arb_date = datetime.strptime(arb_date_str, "%Y-%m-%d")
                suit_date = datetime.strptime(suit_date_str, "%Y-%m-%d")
                deadline = arb_date + relativedelta(days=15)
                if suit_date > deadline:
                    gap = (suit_date - deadline).days
                    result.time_bar_issues.append(TimeBarIssue(
                        resignation_date=arb_date_str,
                        arbitration_date=suit_date_str,
                        deadline_date=deadline.strftime("%Y-%m-%d"),
                        is_time_barred=True,
                        gap_days=gap,
                        h_code="H-2",
                        suggestion=f"起诉日期{suit_date_str}超出收到仲裁裁决后15日起诉期"
                                   f"（裁决日{arb_date_str}，届满日{deadline.strftime('%Y-%m-%d')}，"
                                   f"超出{gap}天）。请核实起诉日期是否正确。",
                    ))
            except ValueError:
                pass

    def _extract_procedural_dates(self, text: str) -> dict[str, str]:
        """从文书中提取程序性日期。"""
        dates = {}

        delivery_patterns = [
            r'(?:一审判决|判决书)[^\n]*?送达[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?送达[^\n]*?(?:一审判决|判决书)',
        ]
        for pat in delivery_patterns:
            match = re.search(pat, text)
            if match:
                try:
                    dates["送达"] = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                except (ValueError, IndexError):
                    pass
                break

        appeal_patterns = [
            r'(?:提起上诉|上诉)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:提起上诉|上诉)',
        ]
        for pat in appeal_patterns:
            match = re.search(pat, text)
            if match:
                try:
                    dates["上诉"] = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                except (ValueError, IndexError):
                    pass
                break

        arb_patterns = [
            r'(?:仲裁裁决|裁决书)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:仲裁裁决|裁决书)',
        ]
        for pat in arb_patterns:
            match = re.search(pat, text)
            if match:
                try:
                    dates["仲裁裁决"] = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                except (ValueError, IndexError):
                    pass
                break

        suit_patterns = [
            r'(?:向.*?法院起诉|提起诉讼)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:向.*?法院起诉|提起诉讼)',
        ]
        for pat in suit_patterns:
            match = re.search(pat, text)
            if match:
                try:
                    dates["起诉"] = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                except (ValueError, IndexError):
                    pass
                break

        return dates

    def check_law_article_mismatch(self, document_text: str) -> list[CalculationIssue]:
        """检测法条张冠李戴——法条引用与事实场景不匹配。"""
        issues = []

        for law_name, articles in LAW_ARTICLE_COMPATIBILITY.items():
            for article, info in articles.items():
                pattern = re.escape(law_name) + r'.*?' + re.escape(article)
                if re.search(pattern, document_text):
                    mismatch_keywords = info.get("常见张冠李戴", "")
                    if mismatch_keywords:
                        for keyword in mismatch_keywords.split("；"):
                            keyword = keyword.strip()
                            if not keyword:
                                continue
                            context_window = 200
                            for match in re.finditer(pattern, document_text):
                                start = max(0, match.start() - context_window)
                                end = min(len(document_text), match.end() + context_window)
                                context = document_text[start:end]

                                kw_parts = [k.strip() for k in keyword.split("、") if k.strip()]
                                for kw in kw_parts:
                                    if kw and kw in context:
                                        line_num = document_text[:match.start()].count('\n') + 1
                                        issues.append(CalculationIssue(
                                            item=f"{law_name}{article}张冠李戴",
                                            amount=0,
                                            expected=0,
                                            formula="",
                                            issue_type="法条张冠李戴",
                                            message=f"{law_name}{article}的适用场景为"
                                                    f"'{info['适用场景']}'，"
                                                    f"但上下文中出现'{kw}'，"
                                                    f"存在张冠李戴风险：{mismatch_keywords}",
                                            severity="high",
                                            h_code="H-2",
                                            line_number=line_num,
                                            parameters={"law": law_name, "article": article},
                                        ))
                                        break

        logger.info("check_law_article_mismatch: found %d issues", len(issues))
        return issues

    def _get_rhetoric_replacement(self, keyword: str, line_text: str) -> str:
        replacements = {
            "恶意": "不当",
            "卑劣": "（需删除）",
            "无耻": "（需删除）",
            "明目张胆": "（需删除）",
            "极其恶劣": "（需删除）",
            "触目惊心": "（需删除）",
            "令人愤慨": "（需删除）",
            "肆无忌惮": "（需删除）",
            "丧心病狂": "（需删除）",
            "蓄意": "客观上",
            "故意隐瞒": "未予披露",
            "主观意图": "（需删除，改为描述客观行为）",
            "主观恶意": "（需删除，改为描述客观行为）",
            "令人震惊": "（需删除）",
            "性质恶劣": "（需删除，改为具体描述）",
            "情节恶劣": "（需删除，改为具体描述）",
        }
        return replacements.get(keyword, "（需替换为中性表述）")
