"""交叉验证引擎 — 对原始文件进行核对，多源比对，提取可能的幻觉。

桥接架构：不调用任何LLM。纯规则匹配、数值比对和文本相似度计算。
将判决书中的事实陈述与证据材料、法条原文、案例信息进行交叉验证。
"""

import logging
import re
from datetime import datetime

from pydantic import BaseModel, Field

from .evidence_index import EvidenceIndex
from .law_knowledge_base import CaseVerificationResult, LawKnowledgeBase, LawVerificationResult
from .vector_index import VectorIndex
from .web_verifier import WebVerificationResult, WebVerifier

logger = logging.getLogger("legal-hallucination")


class CrossReferenceIssue(BaseModel):
    source_type: str = Field(default="", description="来源类型: 证据/法条/案例/学术/原则")
    source_name: str = Field(default="", description="来源名称")
    claim_text: str = Field(default="", description="判决书中的陈述")
    source_text: str = Field(default="", description="来源文件中的原文")
    match_type: str = Field(default="", description="匹配类型: 完全一致/部分一致/不一致/未找到")
    discrepancy: str = Field(default="", description="差异描述")
    severity: str = Field(default="medium", description="严重度: critical/high/medium/low/info")
    h_code: str = Field(default="H-1", description="幻觉维度编号")
    suggestion: str = Field(default="", description="修正建议")
    line_number: int = Field(default=0, description="行号")
    confidence: float = Field(default=0.0, description="匹配置信度 0-1")


class CrossReferenceReport(BaseModel):
    document_path: str = Field(default="")
    verification_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    total_claims: int = Field(default=0, description="提取的事实陈述总数")
    verified_claims: int = Field(default=0, description="已验证的事实陈述数")
    issues: list[CrossReferenceIssue] = Field(default_factory=list)
    law_verifications: list[LawVerificationResult] = Field(default_factory=list)
    case_verifications: list[CaseVerificationResult] = Field(default_factory=list)
    web_verifications: list[WebVerificationResult] = Field(default_factory=list)
    summary: str = Field(default="", description="交叉验证摘要")


class CrossReferenceEngine:
    def __init__(
        self,
        evidence_index: EvidenceIndex = None,
        law_kb: LawKnowledgeBase = None,
        web_verifier: WebVerifier = None,
        vector_index: VectorIndex = None,
    ):
        self.evidence_index = evidence_index or EvidenceIndex()
        self.law_kb = law_kb or LawKnowledgeBase()
        self.web_verifier = web_verifier or WebVerifier()
        self.vector_index = vector_index

    def cross_verify(
        self,
        document_text: str,
        manifest_path: str = "",
        vault_root: str = "",
        law_dir: str = "",
        online_verify: bool = False,
    ) -> CrossReferenceReport:
        report = CrossReferenceReport()

        if manifest_path:
            self.evidence_index.load(manifest_path, vault_root)

        if law_dir:
            self.law_kb.load_from_directory(law_dir)

        fact_section = self._extract_fact_section(document_text)
        if not fact_section:
            fact_section = document_text

        claims = self._extract_claims(fact_section)
        report.total_claims = len(claims)

        for claim in claims:
            issues = self._verify_claim(claim, document_text)
            report.issues.extend(issues)

        report.verified_claims = report.total_claims - len([
            i for i in report.issues if i.match_type == "未找到"
        ])

        report.law_verifications = self._verify_law_citations(document_text)
        report.case_verifications = self._verify_case_numbers(document_text)

        if online_verify:
            citations = [lv.citation_text for lv in report.law_verifications]
            report.web_verifications = self.web_verifier.batch_verify_citations(citations)

            case_nums = [cv.case_number for cv in report.case_verifications if cv.case_number]
            if case_nums:
                report.web_verifications.extend(
                    self.web_verifier.batch_verify_cases(case_nums)
                )

        report.summary = self._generate_summary(report)

        logger.info(
            "CrossReferenceEngine.cross_verify: claims=%d, verified=%d, issues=%d",
            report.total_claims, report.verified_claims, len(report.issues),
        )

        return report

    def _extract_fact_section(self, document_text: str) -> str:
        patterns = [
            r'# 二、本院查明事实\s*(.*?)(?=# 三、|# 四、|$)',
            r'本院查明\s*(.*?)(?=本院认为|$)',
            r'经审理查明\s*(.*?)(?=本院认为|$)',
        ]

        for pat in patterns:
            match = re.search(pat, document_text, re.DOTALL)
            if match:
                return match.group(1).strip()

        return ""

    def _extract_claims(self, fact_section: str) -> list[dict]:
        claims = []

        sentences = re.split(r'[。；\n]', fact_section)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 5:
                continue

            has_evidence_cite = bool(re.search(r'[（\(]见《?[^》\)]+》?[）\)]', sentence))
            has_amount = bool(re.search(r'\d+(?:[,，]\d{3})*(?:\.\d+)?\s*元', sentence))
            has_date = bool(re.search(r'\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日', sentence))
            has_fact_keyword = bool(re.search(
                r'原告|被告|上诉人|被上诉人|申请人|被申请人|劳动者|用人单位',
                sentence,
            ))

            if has_evidence_cite or has_amount or has_date or has_fact_keyword:
                evidence_cite = ""
                cite_match = re.search(r'[（\(]见《?([^》\)]+?)》?[）\)]', sentence)
                if cite_match:
                    evidence_cite = cite_match.group(1).strip()

                line_num = fact_section[:fact_section.find(sentence)].count('\n') + 1 if sentence in fact_section else 0

                claims.append({
                    "text": sentence,
                    "evidence_cite": evidence_cite,
                    "has_amount": has_amount,
                    "has_date": has_date,
                    "line_number": line_num,
                })

        return claims

    def _verify_claim(self, claim: dict, full_text: str) -> list[CrossReferenceIssue]:
        issues = []
        claim_text = claim["text"]
        evidence_cite = claim["evidence_cite"]

        if evidence_cite:
            evidence_issue = self._verify_evidence_cite(claim_text, evidence_cite, claim["line_number"])
            if evidence_issue:
                issues.append(evidence_issue)

        if claim["has_amount"]:
            amount_issue = self._verify_amounts(claim_text, claim["line_number"])
            if amount_issue:
                issues.append(amount_issue)

        if claim["has_date"]:
            date_issue = self._verify_dates(claim_text, claim["line_number"])
            if date_issue:
                issues.append(date_issue)

        return issues

    def _verify_evidence_cite(
        self,
        claim_text: str,
        evidence_cite: str,
        line_number: int,
    ) -> CrossReferenceIssue | None:
        if not self.evidence_index.loaded:
            return None

        is_valid = self.evidence_index.check_citation(evidence_cite)

        if is_valid:
            evidence_content = self._get_evidence_content(evidence_cite)
            if evidence_content:
                similarity = self._compute_text_similarity(claim_text, evidence_content)
                if similarity < 0.2:
                    return CrossReferenceIssue(
                        source_type="证据",
                        source_name=evidence_cite,
                        claim_text=claim_text[:100],
                        source_text=evidence_content[:100],
                        match_type="不一致",
                        discrepancy=f"判决书陈述与证据《{evidence_cite}》内容相似度极低（{similarity:.2f}），"
                                    f"可能存在事实编造或张冠李戴",
                        severity="high",
                        h_code="H-1",
                        suggestion=f"核实《{evidence_cite}》中是否确实包含此事实陈述；"
                                   f"如不包含，应删除或修改该陈述",
                        line_number=line_number,
                        confidence=similarity,
                    )
                elif similarity < 0.4:
                    return CrossReferenceIssue(
                        source_type="证据",
                        source_name=evidence_cite,
                        claim_text=claim_text[:100],
                        source_text=evidence_content[:100],
                        match_type="部分一致",
                        discrepancy=f"判决书陈述与证据《{evidence_cite}》内容相似度较低（{similarity:.2f}），"
                                    f"可能存在事实细节偏差",
                        severity="medium",
                        h_code="H-1",
                        suggestion=f"核实《{evidence_cite}》中的原始表述，确保事实陈述准确",
                        line_number=line_number,
                        confidence=similarity,
                    )
            return None
        else:
            closest = self.evidence_index.find_closest_match(evidence_cite)
            return CrossReferenceIssue(
                source_type="证据",
                source_name=evidence_cite,
                claim_text=claim_text[:100],
                source_text="",
                match_type="未找到",
                discrepancy=f"证据《{evidence_cite}》不在证据索引清单中，"
                            f"可能为杜撰的证据引注"
                            + (f"；最接近的有效证据为《{closest}》" if closest else ""),
                severity="critical",
                h_code="H-1",
                suggestion=f"删除对《{evidence_cite}》的引用，或将其替换为证据清单中的有效证据"
                           + (f"《{closest}》" if closest else ""),
                line_number=line_number,
                confidence=0.0,
            )

    def _get_evidence_content(self, evidence_cite: str) -> str:
        cite_clean = evidence_cite.strip()
        for name, content in self.evidence_index.evidence_texts.items():
            if cite_clean in name or name in cite_clean:
                return content
        return ""

    def _compute_text_similarity(self, text1: str, text2: str) -> float:
        if self.vector_index and self.vector_index.loaded and self.vector_index.documents:
            results = self.vector_index.search(text1, doc_types=["证据"], top_k=3, min_score=0.1)
            if results:
                return max(r.score for r in results)

        chars1 = set(re.findall(r'[\u4e00-\u9fff]', text1))
        chars2 = set(re.findall(r'[\u4e00-\u9fff]', text2))

        if not chars1 or not chars2:
            return 0.0

        intersection = chars1 & chars2
        union = chars1 | chars2

        return len(intersection) / len(union)

    def _verify_amounts(
        self,
        claim_text: str,
        line_number: int,
    ) -> CrossReferenceIssue | None:
        amounts = re.findall(r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元', claim_text)

        if not amounts:
            return None

        for amount_str in amounts:
            try:
                amount = float(amount_str.replace(',', '').replace('，', ''))
            except ValueError:
                continue

            if amount > 0:
                evidence_cite_match = re.search(r'[（\(]见《?([^》\)]+?)》?[）\)]', claim_text)
                if evidence_cite_match:
                    evidence_name = evidence_cite_match.group(1).strip()
                    evidence_content = self._get_evidence_content(evidence_name)

                    if evidence_content:
                        evidence_amounts = re.findall(
                            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
                            evidence_content,
                        )
                        evidence_values = []
                        for ea in evidence_amounts:
                            try:
                                evidence_values.append(float(ea.replace(',', '').replace('，', '')))
                            except ValueError:
                                continue

                        if evidence_values:
                            closest_match = min(
                                evidence_values,
                                key=lambda x: abs(x - amount),
                            )
                            if abs(closest_match - amount) > max(1.0, amount * 0.01):
                                return CrossReferenceIssue(
                                    source_type="证据",
                                    source_name=evidence_name,
                                    claim_text=claim_text[:100],
                                    source_text=f"证据中最接近的金额：{closest_match:,.2f}元",
                                    match_type="不一致",
                                    discrepancy=f"判决书金额{amount:,.2f}元与证据《{evidence_name}》中"
                                                f"最接近的金额{closest_match:,.2f}元不一致，"
                                                f"差额{abs(closest_match - amount):,.2f}元",
                                    severity="high",
                                    h_code="H-5",
                                    suggestion=f"核实{amount:,.2f}元的计算依据和来源",
                                    line_number=line_number,
                                    confidence=0.7,
                                )

        return None

    def _verify_dates(
        self,
        claim_text: str,
        line_number: int,
    ) -> CrossReferenceIssue | None:
        dates_in_claim = re.findall(
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            claim_text,
        )

        if not dates_in_claim:
            return None

        evidence_cite_match = re.search(r'[（\(]见《?([^》\)]+?)》?[）\)]', claim_text)
        if not evidence_cite_match:
            return None

        evidence_name = evidence_cite_match.group(1).strip()
        evidence_content = self._get_evidence_content(evidence_name)

        if not evidence_content:
            return None

        dates_in_evidence = re.findall(
            r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            evidence_content,
        )

        if not dates_in_evidence:
            return None

        for year, month, day in dates_in_claim:
            claim_date = f"{year}-{int(month):02d}-{int(day):02d}"
            found_match = False
            for ey, em, ed in dates_in_evidence:
                evidence_date = f"{ey}-{int(em):02d}-{int(ed):02d}"
                if claim_date == evidence_date:
                    found_match = True
                    break

            if not found_match and dates_in_evidence:
                return CrossReferenceIssue(
                    source_type="证据",
                    source_name=evidence_name,
                    claim_text=claim_text[:100],
                    source_text=(
                        "证据中的日期："
                        + ', '.join(f'{y}-{int(m):02d}-{int(d):02d}' for y, m, d in dates_in_evidence[:5])
                    ),
                    match_type="不一致",
                    discrepancy=f"判决书日期{claim_date}在证据《{evidence_name}》中未找到匹配",
                    severity="high",
                    h_code="H-2",
                    suggestion=f"核实日期{claim_date}的来源，可能为杜撰或错误",
                    line_number=line_number,
                    confidence=0.6,
                )

        return None

    def _verify_law_citations(self, document_text: str) -> list[LawVerificationResult]:
        results = []

        citations = re.findall(
            r'《[^》]+》第[一二三四五六七八九十百千零\d]+条',
            document_text,
        )

        seen = set()
        for citation in citations:
            if citation in seen:
                continue
            seen.add(citation)

            result = self.law_kb.verify_citation(citation)
            results.append(result)

        return results

    def _verify_case_numbers(self, document_text: str) -> list[CaseVerificationResult]:
        results = []

        case_numbers = re.findall(
            r'[（(]\d{4}[）)]\w+\d+号',
            document_text,
        )

        seen = set()
        for case_num in case_numbers:
            if case_num in seen:
                continue
            seen.add(case_num)

            result = self.law_kb.verify_case_number(case_num)
            results.append(result)

        return results

    def _generate_summary(self, report: CrossReferenceReport) -> str:
        total = report.total_claims
        verified = report.verified_claims
        issues = len(report.issues)

        critical = len([i for i in report.issues if i.severity == "critical"])
        high = len([i for i in report.issues if i.severity == "high"])
        medium = len([i for i in report.issues if i.severity == "medium"])

        law_total = len(report.law_verifications)
        law_not_found = len([lv for lv in report.law_verifications if not lv.local_found])
        law_replaced = len([lv for lv in report.law_verifications if not lv.is_current])

        case_total = len(report.case_verifications)
        case_not_found = len([cv for cv in report.case_verifications if not cv.local_found])

        summary_parts = [
            f"交叉验证完成：共提取{total}项事实陈述，已验证{verified}项，发现{issues}项问题",
            f"问题分布：致命级{critical}项、高级{high}项、中级{medium}项",
            f"法条验证：共{law_total}条引用，本地未找到{law_not_found}条，已废止{law_replaced}条",
            f"案例验证：共{case_total}个案号，本地未找到{case_not_found}个",
        ]

        if report.web_verifications:
            web_pending = len([wv for wv in report.web_verifications if wv.verification_status == "待在线验证"])
            summary_parts.append(f"在线验证：{web_pending}项待验证")

        return "；".join(summary_parts) + "。"
