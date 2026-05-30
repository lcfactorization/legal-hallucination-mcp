"""证据索引 — 装载证据清单并构建有效证据文件名集合。

桥接架构：不调用任何LLM。纯文件读取与字符串匹配。
"""

import hashlib
import json
import logging
import os
import re

from .models import CitationFraudItem

logger = logging.getLogger("legal-hallucination")


class EvidenceIndex:
    MIRROR_ROOT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vault_mirror"
    )

    def __init__(self, manifest_path: str = "", vault_root: str = "", project_root: str = ""):
        self.manifest_path = manifest_path
        self.vault_root = vault_root
        self.project_root = project_root
        self.valid_filenames: set[str] = set()
        self.valid_basenames: set[str] = set()
        self.evidence_texts: dict[str, str] = {}
        self.loaded = False
        self._cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache"
        )
        self._manifest_hash: str = ""

    def load(self, manifest_path: str = "", vault_root: str = "") -> dict:
        mp = manifest_path or self.manifest_path
        vr = vault_root or self.vault_root

        if not mp:
            logger.warning("EvidenceIndex.load: manifest_path is empty")
            return {"success": False, "valid_count": 0, "loaded_count": 0, "missing": []}

        if not os.path.exists(mp):
            logger.error("EvidenceIndex.load: manifest not found: %s", mp)
            return {"success": False, "valid_count": 0, "loaded_count": 0, "missing": [mp]}

        with open(mp, encoding="utf-8") as f:
            content = f.read()

        content_hash = hashlib.md5(content.encode()).hexdigest()
        cache_file = os.path.join(self._cache_dir, f"evidence_index_{content_hash}.json")

        if os.path.exists(cache_file):
            try:
                with open(cache_file, encoding="utf-8") as cf:
                    cached = json.load(cf)
                self.valid_filenames = set(cached.get("valid_filenames", []))
                self.valid_basenames = set(cached.get("valid_basenames", []))
                self.evidence_texts = cached.get("evidence_texts", {})
                self._manifest_hash = content_hash
                if self.evidence_texts:
                    self.loaded = True
                    logger.info(
                        "EvidenceIndex.load: restored from cache, valid=%d, texts=%d",
                        len(self.valid_filenames), len(self.evidence_texts),
                    )
                    return {
                        "success": True,
                        "valid_count": len(self.valid_filenames),
                        "loaded_count": len(self.valid_filenames),
                        "missing": [],
                        "from_cache": True,
                    }
                else:
                    logger.info("EvidenceIndex.load: cache missing texts, rebuilding")
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("EvidenceIndex: cache read failed, rebuilding: %s", e)

        raw_paths = re.findall(r"-\s+`([^`]+)`", content)

        loaded_count = 0
        missing_files = []

        for r_path in raw_paths:
            full_path = self._resolve_evidence_path(r_path, vr)

            base_name = os.path.basename(full_path)
            name_no_ext = os.path.splitext(base_name)[0]

            self.valid_filenames.add(base_name)
            self.valid_basenames.add(name_no_ext)

            if os.path.exists(full_path):
                try:
                    with open(full_path, encoding="utf-8") as ef:
                        file_text = ef.read()
                    self.evidence_texts[name_no_ext] = file_text
                    loaded_count += 1
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning("EvidenceIndex: failed to read %s: %s", base_name, e)
                    missing_files.append(base_name)
            else:
                logger.info("EvidenceIndex: file not found: %s", base_name)
                missing_files.append(base_name)

        self.loaded = True
        self._manifest_hash = content_hash
        logger.info(
            "EvidenceIndex.load: valid=%d, loaded=%d, missing=%d",
            len(self.valid_filenames), loaded_count, len(missing_files),
        )

        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            cache_data = {
                "valid_filenames": list(self.valid_filenames),
                "valid_basenames": list(self.valid_basenames),
                "evidence_texts": self.evidence_texts,
                "manifest_hash": content_hash,
            }
            with open(cache_file, "w", encoding="utf-8") as cf:
                json.dump(cache_data, cf, ensure_ascii=False, indent=2)
        except OSError as e:
            logger.warning("EvidenceIndex: cache write failed: %s", e)

        return {
            "success": True,
            "valid_count": len(self.valid_filenames),
            "loaded_count": loaded_count,
            "missing": missing_files,
        }

    def check_citation(self, citation: str) -> bool:
        if not self.loaded:
            return False

        cite_clean = citation.strip()
        cite_normalized = re.sub(r"(证据\d+)", r"\1_", cite_clean)

        for f in self.valid_filenames:
            f_base = os.path.splitext(f)[0]
            if (cite_clean in f or f in cite_clean
                    or cite_clean in f_base or f_base in cite_clean
                    or cite_normalized in f_base or f_base in cite_normalized):
                return True

        if len(cite_clean) <= 4:
            for f_base in self.valid_basenames:
                if self._short_cite_match(cite_clean, f_base):
                    return True

        return False

    def check_citation_with_confidence(self, citation: str) -> tuple[bool, float]:
        """检查引注是否有效，同时返回匹配置信度。

        Returns:
            (is_valid, confidence): is_valid为是否匹配成功，
            confidence为0.0~1.0的匹配置信度评分。
        """
        if not self.loaded:
            return False, 0.0

        cite_clean = citation.strip()
        cite_normalized = re.sub(r"(证据\d+)", r"\1_", cite_clean)

        best_confidence = 0.0

        for f in self.valid_filenames:
            f_base = os.path.splitext(f)[0]
            if cite_clean in f or cite_clean in f_base:
                best_confidence = max(best_confidence, 1.0)
            elif f in cite_clean or f_base in cite_clean:
                best_confidence = max(best_confidence, 0.9)
            elif cite_normalized in f_base or f_base in cite_normalized:
                best_confidence = max(best_confidence, 0.85)

        if best_confidence >= 1.0:
            return True, 1.0

        if len(cite_clean) <= 4:
            for f_base in self.valid_basenames:
                if self._short_cite_match(cite_clean, f_base):
                    return True, 0.75

        if best_confidence > 0:
            return True, best_confidence

        return False, 0.0

    @staticmethod
    def _short_cite_match(short_cite: str, filename_base: str) -> bool:
        """短引注（≤4字符）的增强匹配逻辑。

        处理"证据1"匹配"证据1_xxx.md"、"微信"匹配"证据1_微信聊天记录"等场景。
        """
        m = re.match(r'(证据)(\d+)', short_cite)
        if m:
            prefix = m.group(1)
            num = m.group(2)
            pattern = f'{prefix}{num}[_-]'
            if re.search(pattern, filename_base):
                return True
            if f'{prefix}{num}' == filename_base[:len(prefix) + len(num)]:
                return True

        if short_cite in filename_base:
            return True

        return False

    def find_closest_match(self, citation: str) -> str:
        cite_clean = citation.strip()
        best_match = ""
        best_score = 0

        for f_base in self.valid_basenames:
            cite_chars = set(cite_clean)
            base_chars = set(f_base)
            overlap = len(cite_chars & base_chars)
            total = max(len(cite_chars | base_chars), 1)
            jaccard = overlap / total

            cite_norm = re.sub(r'(证据\d+)', r'\1_', cite_clean)
            prefix_bonus = 0.0
            if cite_norm in f_base or f_base in cite_norm:
                prefix_bonus = 0.3

            score = jaccard + prefix_bonus

            if score > best_score:
                best_score = score
                best_match = f_base

        return best_match if best_score > 0.3 else ""

    def find_fraud_citations(self, document_text: str) -> list[CitationFraudItem]:
        if not self.loaded:
            logger.warning("find_fraud_citations: evidence index not loaded")
            return []

        citations = re.findall(r"[（\(]见《?([^》\)]+?)》?[）\)]", document_text)
        frauds = []

        for cite in citations:
            cite_clean = cite.strip()
            if len(cite_clean) < 2:
                continue

            matched = self.check_citation(cite_clean)
            if not matched:
                cite_normalized = re.sub(r"(证据\d+)", r"\1_", cite_clean)
                closest = self.find_closest_match(cite_clean)
                frauds.append(CitationFraudItem(
                    citation=cite_clean,
                    normalized=cite_normalized,
                    matched=False,
                    closest_match=closest,
                ))
                logger.info("find_fraud_citations: FRAUD detected: '%s'", cite_clean)

        return frauds

    def get_claim_texts(self) -> str:
        claim_keywords = ["变更", "诉讼请求", "起诉状", "上诉状"]
        texts = []
        for name, content in self.evidence_texts.items():
            if any(k in name for k in claim_keywords):
                texts.append(content)
        return "\n".join(texts)

    def get_manifest_case_numbers(self) -> set[str]:
        if not self.manifest_path or not os.path.exists(self.manifest_path):
            return set()

        with open(self.manifest_path, encoding="utf-8") as f:
            content = f.read()

        case_num_pattern = re.compile(
            r"[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?第?\d+号"
        )
        numbers = set()
        for m in case_num_pattern.finditer(content):
            numbers.add(m.group(0))

        for name, text in self.evidence_texts.items():
            for m in case_num_pattern.finditer(text):
                numbers.add(m.group(0))

        return numbers

    def get_related_case_numbers(self, document_text: str) -> set[str]:
        related = set()

        case_num_pattern = re.compile(
            r"[（\(]?\d{4}[）)]?\s*([^\s，。；]+?)民[^\s，。；]*?第?(\d+)号"
        )

        doc_cases = {}
        for m in case_num_pattern.finditer(document_text):
            full = m.group(0)
            court = m.group(1)
            num = m.group(2)
            doc_cases[full] = (court, num)

        manifest_cases = self.get_manifest_case_numbers()

        for case_num in doc_cases:
            if case_num in manifest_cases:
                related.add(case_num)
                continue

            for mf in manifest_cases:
                if self._are_related_cases(case_num, mf):
                    related.add(case_num)
                    break

        return related

    @staticmethod
    def _are_related_cases(case_a: str, case_b: str) -> bool:
        pattern = re.compile(
            r"[（\(]?(\d{4})[）)]?\s*([^\s，。；]+?)民([^\s，。；]*?)第?(\d+)号"
        )
        ma = pattern.match(case_a)
        mb = pattern.match(case_b)
        if not ma or not mb:
            return False

        year_a, court_a, level_a, num_a = ma.group(1), ma.group(2), ma.group(3), ma.group(4)
        year_b, court_b, level_b, num_b = mb.group(1), mb.group(2), mb.group(3), mb.group(4)

        if court_a == court_b and year_a == year_b:
            return True

        if court_a == court_b and num_a == num_b:
            return True

        if court_a == court_b and level_a != level_b:
            return True

        return False

    def get_procedural_timeline(self) -> dict[str, str]:
        """从已加载的证据文件中提取程序性日期，供H-2维度交叉验证使用。

        Returns:
            字典，key为日期类型（如"受理""立案""开庭""宣判""送达""上诉""仲裁裁决""起诉"），
            value为"YYYY-MM-DD"格式的日期字符串。
        """
        timeline: dict[str, str] = {}

        if not self.loaded:
            return timeline

        date_type_patterns = {
            "受理": [
                r'(?:法院|本院)[^\n]*?于?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?受理',
                r'受理[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            ],
            "立案": [
                r'(?:法院|本院)[^\n]*?于?\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?立案',
                r'立案[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
            ],
            "开庭": [
                r'(?:公开|不公开)?[^\n]*?开庭[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?开庭',
            ],
            "宣判": [
                r'(?:宣判|宣告判决)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:宣判|宣告判决)',
            ],
            "送达": [
                r'(?:送达|送达判决书)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?送达',
            ],
            "上诉": [
                r'(?:提起上诉|上诉)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:提起上诉|上诉)',
            ],
            "仲裁裁决": [
                r'(?:仲裁裁决|裁决书)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:仲裁裁决|裁决书)',
            ],
            "起诉": [
                r'(?:向.*?法院起诉|提起诉讼)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日',
                r'(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:向.*?法院起诉|提起诉讼)',
            ],
        }

        for _name, content in self.evidence_texts.items():
            for date_type, patterns in date_type_patterns.items():
                if date_type in timeline:
                    continue
                for pat in patterns:
                    m = re.search(pat, content)
                    if m:
                        try:
                            timeline[date_type] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                        except (ValueError, IndexError):
                            pass
                        break

        if timeline:
            logger.info("get_procedural_timeline: extracted %d dates from evidence", len(timeline))

        return timeline

    def _resolve_evidence_path(self, rel_path: str, vault_root: str) -> str:
        if os.path.isabs(rel_path):
            abs_path = os.path.normpath(rel_path)
            if os.path.exists(abs_path):
                return abs_path
            base = os.path.basename(abs_path)
            mirror_hit = self._find_in_mirror(base)
            if mirror_hit:
                logger.info("EvidenceIndex: 绝对路径缺失，使用vault_mirror替代: %s → %s", base, mirror_hit)
                return mirror_hit
            return abs_path

        candidates = []
        if self.project_root:
            candidates.append(os.path.normpath(os.path.join(self.project_root, rel_path)))
        candidates.append(os.path.normpath(os.path.join(self.MIRROR_ROOT, rel_path)))
        candidates.append(os.path.normpath(os.path.join(vault_root, rel_path)))

        for c in candidates:
            if os.path.exists(c):
                return c

        base = os.path.basename(rel_path)
        mirror_hit = self._find_in_mirror(base)
        if mirror_hit:
            logger.info("EvidenceIndex: 相对路径缺失，使用vault_mirror模糊匹配: %s → %s", base, mirror_hit)
            return mirror_hit

        return candidates[-1]

    @staticmethod
    def _walk_limited(root_dir: str, max_depth: int = 5):
        root_dir = os.path.normpath(root_dir)
        root_depth = root_dir.count(os.sep)
        for root, dirs, files in os.walk(root_dir):
            current_depth = os.path.normpath(root).count(os.sep) - root_depth
            if current_depth >= max_depth:
                dirs.clear()
            yield root, dirs, files

    def _find_in_mirror(self, filename: str) -> str:
        if not os.path.isdir(self.MIRROR_ROOT):
            return ""
        for root, _dirs, files in self._walk_limited(self.MIRROR_ROOT):
            for f in files:
                if f == filename:
                    return os.path.join(root, f)
        name_no_ext = os.path.splitext(filename)[0]
        for root, _dirs, files in self._walk_limited(self.MIRROR_ROOT):
            for f in files:
                f_no_ext = os.path.splitext(f)[0]
                if name_no_ext in f_no_ext or f_no_ext in name_no_ext:
                    return os.path.join(root, f)
        return ""
