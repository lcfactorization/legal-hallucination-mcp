# ruff: noqa: E501
"""诉请解析器 — 从起诉状文本中提取诉请金额上限。

桥接架构：不调用任何LLM。纯正则表达式提取。
支持阿拉伯数字、千分位、中文大写金额提取。
支持利息基数提取。
"""

import logging
import re

from .models import ClaimBoundaryItem, InterestBaseItem

logger = logging.getLogger("legal-hallucination")

_CN2AN_AVAILABLE = False
try:
    import cn2an
    _CN2AN_AVAILABLE = True
except ImportError:
    logger.info("cn2an未安装，中文大写金额解析不可用。安装方式：pip install cn2an")

_CLAIM_SECTION_PATTERNS = [
    r'(?:诉讼请求|起诉请求|上诉请求|请求)[：:]\s*(.*?)(?=(?:事实和理由|事实与理由|理由|$))',
    r'(?:一、|1[、.．])\s*(?:诉讼请求|起诉请求)[^\n]*\n(.*?)(?=(?:二、|2[、.．])\s*(?:事实|理由|$))',
]

_JUDGMENT_MAIN_PATTERNS = [
    r'(?:判决如下|裁定如下|裁判如下|裁判给付)[：:]*\s*(.*?)(?=如未按|$)',
    r'(?:六[、.．]\s*(?:二审)?(?:裁判|判决|裁定))(.*?)(?=如未按|$)',
]

_NOISE_CHARS = re.compile(r'[=\+\-\*\/\|`#_~\[\]{}<>]')

_CHINESE_NUMERAL_RE = re.compile(
    r'([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整圆]+)'
)

_ARABIC_AMOUNT_RE = re.compile(
    r'(\d{1,3}(?:[,，]\d{3})*(?:\.\d+)?)'
)


def normalize_amount(text: str) -> float:
    """统一提取阿拉伯数字或中文大写金额，返回浮点数。"""
    arabic_match = _ARABIC_AMOUNT_RE.search(text)
    if arabic_match:
        raw = arabic_match.group(1).replace(',', '').replace('，', '')
        try:
            return float(raw)
        except ValueError:
            pass

    if _CN2AN_AVAILABLE:
        chinese_match = _CHINESE_NUMERAL_RE.search(text)
        if chinese_match:
            ch_str = chinese_match.group(1)
            for attempt in (ch_str, re.sub(r'[元整圆]$', '', ch_str)):
                try:
                    return float(cn2an.cn2an(attempt, 'smart'))
                except (ValueError, Exception):
                    continue

    return 0.0


def extract_interest_base(text: str) -> list[InterestBaseItem]:
    """提取"以……为基数"中的本金金额，用于利息计算核对。"""
    items = []
    pattern = r'以\s*([^为]+?)\s*为基数[，,]?\s*(?:按照?|按)\s*([^，,]+?)(?:计算|计息)'
    for match in re.finditer(pattern, text):
        base_text = match.group(1).strip()
        rate_text = match.group(2).strip()
        base_amount = normalize_amount(base_text)
        start_pos = match.start()
        line_num = text[:start_pos].count('\n') + 1
        items.append(InterestBaseItem(
            base_amount=base_amount,
            base_text=base_text,
            rate_text=rate_text,
            period_text="",
            line_number=line_num,
        ))

    simple_pattern = r'以\s*([^为]+?)\s*为基数'
    existing_bases = {item.base_text for item in items}
    for match in re.finditer(simple_pattern, text):
        base_text = match.group(1).strip()
        if base_text in existing_bases:
            continue
        base_amount = normalize_amount(base_text)
        start_pos = match.start()
        line_num = text[:start_pos].count('\n') + 1
        items.append(InterestBaseItem(
            base_amount=base_amount,
            base_text=base_text,
            rate_text="",
            period_text="",
            line_number=line_num,
        ))

    return items


class ClaimParser:
    def __init__(self):
        self.claim_limits: dict[str, float] = {}

    def parse(self, complaint_text: str, amended_text: str = "") -> dict[str, float]:
        self.claim_limits = {}
        if not complaint_text:
            return self.claim_limits

        claim_section = self._extract_claim_section(complaint_text)
        if claim_section:
            self._parse_layer1(claim_section)
            self._parse_layer2(claim_section)
            self._parse_layer3(claim_section)
            self._parse_chinese_amounts(claim_section)
        else:
            self._parse_layer1(complaint_text)
            self._parse_layer2(complaint_text)
            self._parse_layer3(complaint_text)
            self._parse_chinese_amounts(complaint_text)

        if amended_text:
            self._parse_amended_claims(amended_text)

        self._deduplicate_limits()

        logger.info("ClaimParser.parse: extracted %d claim limits", len(self.claim_limits))
        return self.claim_limits

    def _parse_amended_claims(self, amended_text: str):
        """解析变更诉求申请书，更新诉请上限。

        支持：增加/追加、变更/替换、放弃/撤回 三种变更类型。
        """
        amended_section = self._extract_claim_section(amended_text)
        if not amended_section:
            amended_section = amended_text

        self._parse_layer1(amended_section)
        self._parse_layer2(amended_section)
        self._parse_layer3(amended_section)
        self._parse_chinese_amounts(amended_section)

        increase_pattern = (
            r'(?:增加|追加|提高)[^\n]*?'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
        )
        for match in re.finditer(increase_pattern, amended_section):
            amount_str = match.group(1).replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue
            context = match.group(0)
            for keyword in ("工资", "赔偿金", "差额", "奖金", "补偿金", "加班费", "二倍工资", "加付赔偿金"):
                if keyword in context:
                    if keyword not in self.claim_limits or val > self.claim_limits[keyword]:
                        self.claim_limits[keyword] = val
                    break

        replace_pattern = (
            r'(?:变更|改为|调整为|变更为)[^\n]*?'
            r'((?:拖欠|克扣|降薪|待岗|加班|二倍|加付|绩效|经济补偿|工资|赔偿金|差额|奖金)[^，、\n：]*?)'
            r'\s*(?:共计|合计|为|：|:)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
        )
        for match in re.finditer(replace_pattern, amended_section):
            item_name = match.group(1).strip()
            amount_str = match.group(2).replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue
            if len(item_name) >= 2:
                self.claim_limits[item_name] = val

        waive_pattern = (
            r'(?:放弃|撤回|撤诉)[^\n]*?'
            r'((?:拖欠|克扣|降薪|待岗|加班|二倍|加付|绩效|经济补偿|工资|赔偿金|差额|奖金)[^，、\n：]*?)'
            r'(?:的?诉请|的?请求|的?主张)?'
        )
        for match in re.finditer(waive_pattern, amended_section):
            item_name = match.group(1).strip()
            if len(item_name) >= 2 and item_name in self.claim_limits:
                del self.claim_limits[item_name]

        logger.info("_parse_amended_claims: updated to %d claim limits", len(self.claim_limits))

    def _extract_claim_section(self, text: str) -> str:
        for pat in _CLAIM_SECTION_PATTERNS:
            m = re.search(pat, text, re.DOTALL)
            if m:
                return m.group(1)
        return ""

    def _deduplicate_limits(self):
        merged = {}
        sorted_items = sorted(self.claim_limits.items(), key=lambda x: -x[1])
        for item, val in sorted_items:
            item_clean = item.strip().lstrip('0123456789、.．）)')
            item_clean = re.sub(r'^(?:判令|确认|责令|要求|请求)\s*', '', item_clean)
            item_clean = re.sub(r'^(?:两?被?告?|上诉人|原告|申请人)\s*', '', item_clean)
            item_clean = re.sub(r'^(?:支付|赔偿|补足|给付|返还)\s*', '', item_clean)
            item_clean = item_clean.strip()
            if len(item_clean) < 2:
                continue
            is_dup = False
            for existing in list(merged.keys()):
                if item_clean in existing or existing in item_clean:
                    is_dup = True
                    break
            if not is_dup:
                merged[item_clean] = val
        self.claim_limits = merged

    def _parse_layer1(self, text: str):
        item_pattern = r'(?:\d+[、.．）)])\s*(?:判令|确认)?\s*(?:两?被?告?|上诉人)?[^\n]*?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
        for match in re.finditer(item_pattern, text):
            amount_str = match.group(1).replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue

            full_item = match.group(0)
            name_match = re.search(r'(?:支付|赔偿|补足)([^，、\n：]*?(?:金|费|工资|差额)?)(?:人民币\s*)?(?:\d)', full_item)
            if name_match:
                item_name = name_match.group(1).strip().rstrip('及与')
                if 2 < len(item_name) <= 20:
                    if item_name not in self.claim_limits or val > self.claim_limits[item_name]:
                        self.claim_limits[item_name] = val

    def _parse_layer2(self, text: str):
        simple_pattern = (
            r'([^，、\n：（\(]{2,15}?'
            r'(?:工资|赔偿金|差额|奖金|提成|期权|加班费|补偿金|二倍工资|加付赔偿金))'
            r'\s*(?:人民币\s*)?(?:共计|合计|为|：|:)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
        )
        for item, amount in re.findall(simple_pattern, text):
            item_clean = item.strip()
            if _NOISE_CHARS.search(item_clean):
                continue
            amount_str = amount.replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue
            if len(item_clean) > 2 and not any(x in item_clean for x in ["第", "条", "本案", "="]):
                if item_clean not in self.claim_limits or val > self.claim_limits[item_clean]:
                    self.claim_limits[item_clean] = val

    def _parse_layer3(self, text: str):
        fallback_pattern = (
            r'((?:拖欠|克扣|降薪|待岗|加班|二倍|加付|绩效|年底|项目|股票|经济补偿)'
            r'[^，、\n：人民币]*?)\s*(?:人民币\s*)?(?:共计|合计|为|：|:)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元'
        )
        for item, amount in re.findall(fallback_pattern, text):
            item_clean = item.strip()
            if _NOISE_CHARS.search(item_clean):
                continue
            amount_str = amount.replace(',', '').replace('，', '')
            try:
                val = float(amount_str)
            except ValueError:
                continue
            if len(item_clean) > 2:
                if item_clean not in self.claim_limits or val > self.claim_limits[item_clean]:
                    self.claim_limits[item_clean] = val

    def _parse_chinese_amounts(self, text: str):
        if not _CN2AN_AVAILABLE:
            return

        chinese_pattern = (
            r'((?:拖欠|克扣|降薪|待岗|加班|二倍|加付|绩效|年底|项目|股票|经济补偿|赔偿金|工资|差额|奖金)'
            r'[^，、\n：]*?)\s*(?:共计|合计|为|：|:)?\s*'
            r'([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整圆]+)\s*元'
        )
        for item, ch_amount in re.findall(chinese_pattern, text):
            item_clean = item.strip()
            if _NOISE_CHARS.search(item_clean):
                continue
            try:
                val = float(cn2an.cn2an(ch_amount, 'smart'))
            except (ValueError, Exception):
                continue
            if len(item_clean) > 2:
                if item_clean not in self.claim_limits or val > self.claim_limits[item_clean]:
                    self.claim_limits[item_clean] = val

    def _extract_judgment_main(self, text: str) -> str:
        for pat in _JUDGMENT_MAIN_PATTERNS:
            m = re.search(pat, text, re.DOTALL)
            if m:
                return m.group(1)
        return text

    def check_judgment_scope(self, judgment_main_text: str) -> list[ClaimBoundaryItem]:
        violations = []
        if not judgment_main_text or not self.claim_limits:
            return violations

        actual_judgment = self._extract_judgment_main(judgment_main_text)

        draft_awards = re.findall(
            r'(?:支付|赔偿)\s*([^，、\n：（\(]*?(?:金|费|工资|差额)?)(?:人民币\s*)?(?:共计|合计|)?\s*'
            r'(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元',
            actual_judgment,
        )

        for item, amount in draft_awards:
            item_clean = item.strip()
            item_clean = re.sub(r'^(?:原告|被告|上诉人|被上诉人|申请人|被申请人)\s*', '', item_clean)
            item_clean = item_clean.strip()
            amount_str = amount.replace(',', '').replace('，', '')
            try:
                award_val = float(amount_str)
            except ValueError:
                continue

            if award_val == 0.0 or len(item_clean) < 2:
                continue

            if _NOISE_CHARS.search(item_clean):
                continue

            if any(x in item_clean for x in ['**', '##', '```', '|', '合计', '基数']):
                continue

            matched_claim = None
            best_overlap = 0
            for claim_item in self.claim_limits:
                if claim_item in item_clean or item_clean in claim_item:
                    overlap = len(min(claim_item, item_clean, key=len))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        matched_claim = claim_item

            if not matched_claim:
                matched_claim = self._fuzzy_match(item_clean)
                if matched_claim:
                    best_overlap = len(matched_claim)

            if not matched_claim:
                violations.append(ClaimBoundaryItem(
                    judgment_item=item_clean,
                    judgment_amount=award_val,
                    matched_claim="",
                    claim_max=0.0,
                    violation_type="项目越权",
                    excess_amount=award_val,
                ))
            else:
                max_allowed = self.claim_limits[matched_claim]
                if award_val > max_allowed:
                    violations.append(ClaimBoundaryItem(
                        judgment_item=item_clean,
                        judgment_amount=award_val,
                        matched_claim=matched_claim,
                        claim_max=max_allowed,
                        violation_type="金额冒顶",
                        excess_amount=award_val - max_allowed,
                    ))

        chinese_awards = re.findall(
            r'(?:支付|赔偿)\s*([^，、\n：（\(]*?(?:金|费|工资|差额)?)(?:人民币\s*)?(?:共计|合计|)?\s*'
            r'([零壹贰叁肆伍陆柒捌玖拾佰仟万亿元角分整圆]+)\s*元',
            actual_judgment,
        )
        if _CN2AN_AVAILABLE:
            for item, ch_amount in chinese_awards:
                item_clean = item.strip()
                try:
                    award_val = float(cn2an.cn2an(ch_amount, 'smart'))
                except (ValueError, Exception):
                    continue

                if award_val == 0.0 or len(item_clean) < 2:
                    continue
                if _NOISE_CHARS.search(item_clean):
                    continue
                if any(x in item_clean for x in ['**', '##', '```', '|', '合计', '基数']):
                    continue

                matched_claim = None
                best_overlap = 0
                for claim_item in self.claim_limits:
                    if claim_item in item_clean or item_clean in claim_item:
                        overlap = len(min(claim_item, item_clean, key=len))
                        if overlap > best_overlap:
                            best_overlap = overlap
                            matched_claim = claim_item

                if not matched_claim:
                    matched_claim = self._fuzzy_match(item_clean)

                if not matched_claim:
                    already = any(v.judgment_item == item_clean for v in violations)
                    if not already:
                        violations.append(ClaimBoundaryItem(
                            judgment_item=item_clean,
                            judgment_amount=award_val,
                            matched_claim="",
                            claim_max=0.0,
                            violation_type="项目越权",
                            excess_amount=award_val,
                        ))
                else:
                    max_allowed = self.claim_limits[matched_claim]
                    if award_val > max_allowed:
                        already = any(
                            v.judgment_item == item_clean and v.matched_claim == matched_claim
                            for v in violations
                        )
                        if not already:
                            violations.append(ClaimBoundaryItem(
                                judgment_item=item_clean,
                                judgment_amount=award_val,
                                matched_claim=matched_claim,
                                claim_max=max_allowed,
                                violation_type="金额冒顶",
                                excess_amount=award_val - max_allowed,
                            ))

        logger.info("check_judgment_scope: found %d violations", len(violations))
        return violations

    def _fuzzy_match(self, item_name: str) -> str | None:
        """模糊匹配判决项目与诉请项目，基于关键词重叠度。"""
        if not item_name or len(item_name) < 2:
            return None

        item_keywords = self._extract_match_keywords(item_name)
        if not item_keywords:
            return None

        best_match = None
        best_score = 0

        for claim_item in self.claim_limits:
            claim_keywords = self._extract_match_keywords(claim_item)
            if not claim_keywords:
                continue

            overlap = item_keywords & claim_keywords
            if not overlap:
                continue

            score = len(overlap) / max(len(item_keywords), len(claim_keywords))
            if score > best_score and score >= 0.3:
                best_score = score
                best_match = claim_item

        return best_match

    @staticmethod
    def _extract_match_keywords(text: str) -> set[str]:
        if not text or len(text) < 2:
            return set()
        keywords = set()
        for n in (2, 3, 4):
            for i in range(len(text) - n + 1):
                chunk = text[i:i + n]
                if re.match(r'^[\u4e00-\u9fff]+$', chunk):
                    keywords.add(chunk)
        return keywords

    def get_claim_limits_json(self) -> dict:
        return dict(sorted(self.claim_limits.items()))
