"""法条引用校验器 — 校验法条引用格式与本地法规库匹配。

桥接架构：不调用任何LLM。纯正则表达式与本地文件匹配。
"""

import logging
import os
import re

from .config import REPLACED_LAWS
from .models import LawCitationItem

logger = logging.getLogger("legal-hallucination")


class LawCitationChecker:
    def __init__(self, local_law_dir: str = ""):
        self.local_law_dir = local_law_dir
        self.local_law_texts: dict[str, str] = {}
        self.local_law_index: dict[str, str] = {}
        self.loaded = False

    def load_local_laws(self, local_law_dir: str = "") -> dict:
        ld = local_law_dir or self.local_law_dir
        if not ld or not os.path.exists(ld):
            logger.info("LawCitationChecker: local_law_dir not found: %s", ld)
            return {"success": False, "loaded": 0}

        count = 0
        ld_norm = os.path.normpath(ld)
        ld_depth = ld_norm.count(os.sep)
        for root, dirs, files in os.walk(ld):
            current_depth = os.path.normpath(root).count(os.sep) - ld_depth
            if current_depth >= 3:
                dirs.clear()
            for fname in files:
                if fname.endswith((".md", ".txt")):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, encoding="utf-8") as f:
                            content = f.read()
                        name_no_ext = os.path.splitext(fname)[0]
                        self.local_law_texts[name_no_ext] = content
                        self.local_law_index[name_no_ext] = fpath
                        count += 1
                    except (OSError, UnicodeDecodeError) as e:
                        logger.warning("LawCitationChecker: failed to read %s: %s", fname, e)

        self.loaded = True
        logger.info("LawCitationChecker: loaded %d law texts from %s", count, ld)
        return {"success": True, "loaded": count}

    def extract_citations(self, text: str) -> list[LawCitationItem]:
        citations = []

        law_pattern = r'《([^》]+)》第([一二三四五六七八九十百千零\d]+)条'
        for match in re.finditer(law_pattern, text):
            law_name = match.group(1)
            article = match.group(2)
            full_text = match.group(0)

            is_replaced = False
            replaced_by = ""
            for old_law, info in REPLACED_LAWS.items():
                if old_law in law_name:
                    is_replaced = True
                    replaced_by = info["replaced_by"] if isinstance(info, dict) else str(info)
                    break

            local_match = self._check_local_match(law_name, article)

            format_issues = self._check_format_issues(text, match.start(), law_name, full_text)

            citations.append(LawCitationItem(
                citation_text=full_text,
                law_name=law_name,
                article=article,
                is_replaced=is_replaced,
                replaced_by=replaced_by,
                format_issues=format_issues,
                local_match_found=local_match,
            ))

        ji_pattern = r'《([^》]+)》（法释〔(\d{4})〕(\d+)号）'
        for match in re.finditer(ji_pattern, text):
            law_name = match.group(1)
            year = match.group(2)
            number = match.group(3)
            full_text = match.group(0)

            citations.append(LawCitationItem(
                citation_text=full_text,
                law_name=law_name,
                article=f"法释〔{year}〕{number}号",
                is_replaced=False,
                replaced_by="",
                format_issues=[],
                local_match_found=self._check_local_match(law_name, ""),
            ))

        admin_pattern = r'《([^》]+)》（([^）]*〔\d{4}〕\d+号)）'
        for match in re.finditer(admin_pattern, text):
            law_name = match.group(1)
            doc_number = match.group(2)
            full_text = match.group(0)

            if "法释" in doc_number:
                continue

            is_replaced = False
            replaced_by = ""
            for old_law, info in REPLACED_LAWS.items():
                if old_law in law_name:
                    is_replaced = True
                    replaced_by = info["replaced_by"] if isinstance(info, dict) else str(info)
                    break

            citations.append(LawCitationItem(
                citation_text=full_text,
                law_name=law_name,
                article=doc_number,
                is_replaced=is_replaced,
                replaced_by=replaced_by,
                format_issues=[],
                local_match_found=self._check_local_match(law_name, ""),
            ))

        logger.info("extract_citations: found %d citations", len(citations))
        return citations

    def _check_local_match(self, law_name: str, article: str) -> bool:
        if not self.loaded:
            return False

        for name_key in self.local_law_texts:
            if law_name in name_key or name_key in law_name:
                if not article:
                    return True
                content = self.local_law_texts[name_key]
                article_pattern = f"第{article}条"
                if article_pattern in content:
                    return True

        return False

    def _check_format_issues(self, text: str, match_pos: int, law_name: str, full_text: str) -> list[str]:
        issues = []

        before_text = text[:match_pos]
        has_earlier_full_name = law_name in before_text
        has_earlier_shortcut = "以下简称" in before_text and law_name in before_text

        if not has_earlier_full_name and not has_earlier_shortcut:
            if len(law_name) > 6 and "中华人民共和国" in law_name:
                short_name = law_name.replace("中华人民共和国", "").strip()
                if short_name and short_name not in before_text:
                    issues.append("首次引用法律全称但未标注简称")

        return issues

    def check_replaced_laws(self, text: str) -> list[LawCitationItem]:
        items = []
        for old_law, info in REPLACED_LAWS.items():
            pattern = f'《{old_law}》'
            if pattern in text:
                replaced_by = info["replaced_by"] if isinstance(info, dict) else str(info)
                items.append(LawCitationItem(
                    citation_text=pattern,
                    law_name=old_law,
                    article="",
                    is_replaced=True,
                    replaced_by=replaced_by,
                    format_issues=["引用已废止法律"],
                    local_match_found=False,
                ))
        return items
