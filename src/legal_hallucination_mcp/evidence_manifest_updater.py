"""证据清单自动更新模块 — 管理evidence_manifest.md的版本化更新。

核心功能：
1. 解析现有evidence_manifest.md
2. 接受新证据条目（文件路径、描述、类别）
3. 一致性检查：去重、路径验证、编号连续性
4. 生成新版本（需确认则原地更新，否则生成v2副本）
5. 支持从判决书/检测报告中自动提取缺失证据
"""

import datetime
import logging
import os
import re
import shutil

logger = logging.getLogger("legal-hallucination")


class EvidenceEntry:
    def __init__(self, path: str, category: str = "证据类", description: str = ""):
        self.path = os.path.normpath(path)
        self.category = category
        self.description = description
        self.basename = os.path.basename(self.path)
        self.name_no_ext = os.path.splitext(self.basename)[0]
        self.exists = os.path.exists(self.path)

    def __eq__(self, other):
        if not isinstance(other, EvidenceEntry):
            return False
        return self.path == other.path or self.name_no_ext == other.name_no_ext

    def __hash__(self):
        return hash(self.name_no_ext)

    def __repr__(self):
        return f"EvidenceEntry({self.name_no_ext}, cat={self.category}, exists={self.exists})"


class EvidenceManifestUpdater:
    SECTION_ORDER = ["诉状类", "证据类", "法律类", "类案类", "其他"]

    def __init__(self, manifest_path: str, vault_root: str = ""):
        self.manifest_path = manifest_path
        self.vault_root = vault_root or os.path.dirname(manifest_path)
        self.entries: list[EvidenceEntry] = []
        self.raw_content = ""
        self.raw_sections: dict[str, list[str]] = {}
        self.loaded = False

    def load(self) -> dict:
        if not os.path.exists(self.manifest_path):
            logger.error("Manifest not found: %s", self.manifest_path)
            return {"success": False, "entries": 0}

        with open(self.manifest_path, encoding="utf-8") as f:
            self.raw_content = f.read()

        self.entries = []
        self.raw_sections = {}

        current_section = "未分类"
        for line in self.raw_content.split("\n"):
            section_match = re.match(r"^##\s+(.+)$", line)
            if section_match:
                current_section = section_match.group(1).strip()
                self.raw_sections[current_section] = []
                continue

            entry_match = re.match(r"^-\s+`([^`]+)`(?:\s*(.*))?$", line)
            if entry_match:
                path = entry_match.group(1)
                desc = entry_match.group(2).strip() if entry_match.group(2) else ""
                entry = EvidenceEntry(path, category=current_section, description=desc)
                self.entries.append(entry)
                if current_section not in self.raw_sections:
                    self.raw_sections[current_section] = []
                self.raw_sections[current_section].append(line)

        self.loaded = True
        logger.info("Manifest loaded: %d entries from %s", len(self.entries), self.manifest_path)
        return {"success": True, "entries": len(self.entries)}

    def check_duplicates(self, new_entries: list[EvidenceEntry]) -> list[EvidenceEntry]:
        existing_names = {e.name_no_ext for e in self.entries}
        existing_paths = {e.path for e in self.entries}
        duplicates = []
        for entry in new_entries:
            if entry.name_no_ext in existing_names or entry.path in existing_paths:
                duplicates.append(entry)
        return duplicates

    def check_consistency(self) -> list[dict]:
        issues = []
        seen_names = set()
        seen_numbers = set()

        for entry in self.entries:
            if entry.name_no_ext in seen_names:
                issues.append({
                    "type": "重复条目",
                    "entry": entry.name_no_ext,
                    "detail": f"条目 '{entry.name_no_ext}' 在清单中出现多次",
                })
            seen_names.add(entry.name_no_ext)

            num_match = re.search(r"证据(\d+)", entry.name_no_ext)
            if num_match:
                num = int(num_match.group(1))
                if num in seen_numbers:
                    issues.append({
                        "type": "编号重复",
                        "entry": entry.name_no_ext,
                        "detail": f"证据编号 {num} 重复",
                    })
                seen_numbers.add(num)

            if not entry.exists:
                issues.append({
                    "type": "文件缺失",
                    "entry": entry.name_no_ext,
                    "detail": f"文件不存在: {entry.path}",
                })

        if seen_numbers:
            max_num = max(seen_numbers)
            missing_nums = set(range(1, max_num + 1)) - seen_numbers
            for num in sorted(missing_nums):
                issues.append({
                    "type": "编号跳跃",
                    "entry": f"证据{num}",
                    "detail": f"证据编号 {num} 缺失（1-{max_num}范围内）",
                })

        return issues

    def add_entries(
        self,
        new_entries: list[EvidenceEntry],
        auto_approve: bool = False,
    ) -> dict:
        duplicates = self.check_duplicates(new_entries)
        unique_new = [e for e in new_entries if e not in duplicates]

        result = {
            "added": [],
            "duplicates": [d.name_no_ext for d in duplicates],
            "skipped_missing": [],
            "output_path": "",
        }

        for entry in unique_new:
            if not entry.exists:
                result["skipped_missing"].append(entry.name_no_ext)
                logger.warning("Skipping missing file: %s", entry.path)
                continue

        valid_new = [e for e in unique_new if e.exists]

        if not valid_new and not duplicates:
            logger.info("No valid new entries to add")
            return result

        if auto_approve:
            output_path = self.manifest_path
            backup_path = self.manifest_path + f".bak.{datetime.date.today().strftime('%Y%m%d')}"
            shutil.copy2(self.manifest_path, backup_path)
            logger.info("Backup created: %s", backup_path)
        else:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base, ext = os.path.splitext(self.manifest_path)
            output_path = f"{base}_v2_{ts}{ext}"
            logger.info("New version will be created: %s", output_path)

        for entry in valid_new:
            self.entries.append(entry)
            result["added"].append(entry.name_no_ext)

        new_content = self._render_manifest()

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        result["output_path"] = output_path
        logger.info("Manifest written: %s (%d entries added)", output_path, len(valid_new))

        return result

    def update_entry(
        self,
        name_pattern: str,
        new_path: str | None = None,
        new_description: str | None = None,
        new_category: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        target = None
        for entry in self.entries:
            if name_pattern in entry.name_no_ext or name_pattern in entry.basename:
                target = entry
                break

        if not target:
            return {"success": False, "error": f"未找到匹配 '{name_pattern}' 的条目"}

        changes = []
        if new_path and new_path != target.path:
            old_path = target.path
            target.path = os.path.normpath(new_path)
            target.basename = os.path.basename(target.path)
            target.name_no_ext = os.path.splitext(target.basename)[0]
            target.exists = os.path.exists(target.path)
            changes.append(f"路径: {old_path} → {target.path}")

        if new_description is not None:
            old_desc = target.description
            target.description = new_description
            changes.append(f"描述: {old_desc} → {new_description}")

        if new_category is not None and new_category != target.category:
            old_cat = target.category
            target.category = new_category
            changes.append(f"类别: {old_cat} → {new_category}")

        if not changes:
            return {"success": True, "message": "无变更", "changes": []}

        if auto_approve:
            output_path = self.manifest_path
            backup_path = self.manifest_path + f".bak.{datetime.date.today().strftime('%Y%m%d')}"
            shutil.copy2(self.manifest_path, backup_path)
        else:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            base, ext = os.path.splitext(self.manifest_path)
            output_path = f"{base}_v2_{ts}{ext}"

        new_content = self._render_manifest()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "success": True,
            "target": target.name_no_ext,
            "changes": changes,
            "output_path": output_path,
        }

    def extract_missing_evidence_from_report(self, report_text: str) -> list[EvidenceEntry]:
        missing = []

        patterns = [
            r"需添加到证据清单[：:]\s*([^\n，；]+)",
            r"缺失证据[：:]\s*([^\n，；]+)",
            r"未在清单中[：:（(]\s*([^)）\n]+)",
            r"证据清单中未列明[：:]\s*([^\n，；]+)",
        ]

        for pat in patterns:
            for m in re.finditer(pat, report_text):
                name = m.group(1).strip()
                if name and len(name) > 2:
                    if not name.endswith(".md"):
                        name = name + ".md"
                    full_path = os.path.join(self.vault_root, name)
                    entry = EvidenceEntry(full_path, category="证据类")
                    missing.append(entry)

        case_pattern = re.findall(
            r"[（\(](\d{4})[）)]\s*([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤川青藏琼宁]\d{2,3}民[^\s，。；]*?\d+号)",
            report_text,
        )
        for year, case_num in case_pattern:
            case_name = f"类案_{year}_{case_num}.md"
            full_path = os.path.join(self.vault_root, case_name)
            entry = EvidenceEntry(full_path, category="类案类")
            missing.append(entry)

        law_pattern = re.findall(
            r"《([^》]+)》",
            report_text,
        )
        law_keywords = ["法", "条例", "规定", "解释", "办法", "意见", "通知"]
        seen_laws = set()
        for law_name in law_pattern:
            if any(k in law_name for k in law_keywords) and law_name not in seen_laws:
                seen_laws.add(law_name)
                law_file = f"法律_{law_name}.md"
                law_file = re.sub(r"[\\/:*?\"<>|]", "_", law_file)
                full_path = os.path.join(self.vault_root, law_file)
                entry = EvidenceEntry(full_path, category="法律类")
                missing.append(entry)

        deduped = list({e.name_no_ext: e for e in missing}.values())
        logger.info("Extracted %d missing evidence entries from report", len(deduped))
        return deduped

    def _render_manifest(self) -> str:
        grouped: dict[str, list[EvidenceEntry]] = {}
        for entry in self.entries:
            cat = entry.category
            if cat not in grouped:
                grouped[cat] = []
            grouped[cat].append(entry)

        lines = ["# 证据索引清单", ""]

        for section in self.SECTION_ORDER:
            if section in grouped and grouped[section]:
                lines.append(f"## {section}")
                for entry in grouped[section]:
                    desc_part = f" {entry.description}" if entry.description else ""
                    lines.append(f"- `{entry.path}`{desc_part}")
                lines.append("")

        for section, entries in grouped.items():
            if section not in self.SECTION_ORDER and entries:
                lines.append(f"## {section}")
                for entry in entries:
                    desc_part = f" {entry.description}" if entry.description else ""
                    lines.append(f"- `{entry.path}`{desc_part}")
                lines.append("")

        return "\n".join(lines)

    def diff(self, other_path: str) -> list[dict]:
        if not os.path.exists(other_path):
            return [{"type": "error", "detail": f"文件不存在: {other_path}"}]

        other_updater = EvidenceManifestUpdater(other_path, self.vault_root)
        other_updater.load()

        our_names = {e.name_no_ext for e in self.entries}
        other_names = {e.name_no_ext for e in other_updater.entries}

        added = other_names - our_names
        removed = our_names - other_names

        diffs = []
        for name in sorted(added):
            entry = next(e for e in other_updater.entries if e.name_no_ext == name)
            diffs.append({"type": "新增", "entry": name, "category": entry.category, "path": entry.path})

        for name in sorted(removed):
            entry = next(e for e in self.entries if e.name_no_ext == name)
            diffs.append({"type": "删除", "entry": name, "category": entry.category, "path": entry.path})

        common = our_names & other_names
        for name in sorted(common):
            old = next(e for e in self.entries if e.name_no_ext == name)
            new = next(e for e in other_updater.entries if e.name_no_ext == name)
            changes = []
            if old.path != new.path:
                changes.append(f"路径: {old.path} → {new.path}")
            if old.category != new.category:
                changes.append(f"类别: {old.category} → {new.category}")
            if old.description != new.description:
                changes.append(f"描述: {old.description} → {new.description}")
            if changes:
                diffs.append({"type": "修改", "entry": name, "changes": changes})

        return diffs
