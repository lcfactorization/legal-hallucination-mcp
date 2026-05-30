"""响应解析器 — 从LLM/Agent响应中提取结构化幻觉检测结果。

桥接架构：不调用任何LLM。
"""

import json
import logging
import re

from .models import RuleFlag

logger = logging.getLogger("legal-hallucination")


class ResponseParser:
    def parse_hallucination_result(self, dimension: str, response: str) -> list[RuleFlag]:
        logger.info("parse_hallucination_result: dimension=%s, response_len=%d", dimension, len(response))

        response = self._strip_json_wrapper(response)
        flags = []

        sections = re.split(r"\n####\s+\*?\*?\d+\.?\s*", response)
        if len(sections) < 2:
            sections = re.split(r"\n###\s+", response)

        for section in sections[1:]:
            section = section.strip()
            if not section or len(section) < 20:
                continue

            first_line = re.sub(r"[#*]", "", section.split("\n")[0]).strip()
            if re.match(r"^(总结|综合结论|总体评价)", first_line):
                continue

            item_name = self._extract_field(section, r"异常项[：:]\s*", 100)
            if not item_name:
                item_name = first_line[:60]

            description = self._extract_field(section, r"具体表现\*?\*?[：:]", 3000)
            if not description:
                description = self._extract_field(section, r"异常表现\*?\*?[：:]", 3000)

            severity_text = self._extract_field(section, r"严重度\*?\*?[：:]", 100)
            severity = self._map_severity(severity_text)

            evidence = self._extract_field(section, r"原文(?:引用|定位)\*?\*?[：:]", 2000)
            _legal_basis = self._extract_field(section, r"法律依据\*?\*?[：:]", 2000)
            _suggestion = self._extract_field(section, r"(?:修复|改进|建议)\*?\*?[：:]", 2000)

            h_code = dimension.split("_")[0].upper().replace("H", "H-")

            flags.append(RuleFlag(
                rule_id=f"semantic_{dimension}",
                h_code=h_code,
                sub_type=item_name,
                severity=severity,
                message=description[:200] if description else item_name,
                evidence=evidence[:200] if evidence else "",
            ))

        if not flags:
            flags.append(RuleFlag(
                rule_id=f"semantic_{dimension}",
                h_code=dimension.split("_")[0].upper().replace("H", "H-"),
                sub_type=f"{dimension} 语义检测结果",
                severity="info",
                message=response[:500],
            ))

        logger.info("parse_hallucination_result: dimension=%s, flags=%d", dimension, len(flags))
        return flags

    def _strip_json_wrapper(self, text: str) -> str:
        stripped = text.strip()
        fence_match = re.match(r'^```(?:json)?\s*\n(.*?)\n```\s*$', stripped, re.DOTALL)
        if fence_match:
            inner = fence_match.group(1).strip()
            try:
                obj = json.loads(inner)
                if isinstance(obj, dict):
                    for key in ("content", "response", "result", "text", "output"):
                        if key in obj and isinstance(obj[key], str):
                            return obj[key]
                return json.dumps(obj, ensure_ascii=False, indent=2)
            except (json.JSONDecodeError, ValueError):
                return inner
        return text

    def _extract_field(self, text: str, pattern: str, max_len: int = 2000) -> str:
        boundary = r"(?=\n\*\s+\*\*|\n####|\n###|\Z)"
        m = re.search(rf"{pattern}\s*\n?(.+?){boundary}", text, re.DOTALL)
        if not m:
            m = re.search(rf"{pattern}\s*(.+?){boundary}", text, re.DOTALL)
        if m:
            value = m.group(1).strip()
            value = re.sub(r"\n\*\s+", "\n", value)
            value = re.sub(r"\*{1,3}", "", value)
            return value[:max_len]
        return ""

    @staticmethod
    def _map_severity(text: str) -> str:
        if not text:
            return "medium"
        t = text.lower()
        if "致命" in t or "critical" in t:
            return "critical"
        if "严重" in t or "high" in t or "确定" in t or "高度可能" in t:
            return "high"
        if "中等" in t or "medium" in t or "可能" in t:
            return "medium"
        if "轻微" in t or "low" in t or "疑似" in t:
            return "low"
        return "medium"
