"""Vault镜像管理器 — 在MCP项目文件夹中建立证据文件副本，实现优先级路径查找。

优先级次序：
1. 项目文件夹（判决书所在根目录）下的相对路径
2. MCP项目文件夹下vault_mirror中的副本
3. 原始Obsidian Vault中的绝对路径

运行幻觉检测之前，先进行项目文件夹和vault_mirror的一致性和互补性检测。
"""

import filecmp
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("legal-hallucination")

MCP_ROOT = os.path.dirname(os.path.abspath(__file__))
VAULT_MIRROR = os.path.join(MCP_ROOT, "vault_mirror")

from _paths import get_vault_root, get_law_dir
VAULT_ROOT = get_vault_root()
LAW_DIR = get_law_dir()
PANDOC_DIR = os.environ.get("PANDOC_DIR", "")

MIRROR_MAP = {
    ".trae/evidence_manifest.md": os.path.join(VAULT_ROOT, ".trae", "evidence_manifest.md"),
    ".trae/rules/legals.md": os.path.join(VAULT_ROOT, ".trae", "rules", "legals.md"),
}


@dataclass
class MirrorDiff:
    path: str = ""
    project_exists: bool = False
    mirror_exists: bool = False
    vault_exists: bool = False
    project_mirror_match: bool = False
    project_vault_match: bool = False
    mirror_vault_match: bool = False
    status: str = ""


@dataclass
class MirrorSyncResult:
    total_files: int = 0
    copied: int = 0
    skipped: int = 0
    missing_source: int = 0
    diffs: list[MirrorDiff] = field(default_factory=list)


def ensure_mirror_dirs():
    dirs = [
        os.path.join(VAULT_MIRROR, ".trae", "rules"),
        os.path.join(VAULT_MIRROR, "证据材料"),
        os.path.join(VAULT_MIRROR, "案件", "法律法规"),
        os.path.join(VAULT_MIRROR, "加付赔偿金"),
        os.path.join(VAULT_MIRROR, "类案"),
        os.path.join(VAULT_MIRROR, "output"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return dirs


def sync_mirror(force: bool = False) -> MirrorSyncResult:
    result = MirrorSyncResult()
    ensure_mirror_dirs()

    for rel_path, src_path in MIRROR_MAP.items():
        result.total_files += 1
        dst = os.path.join(VAULT_MIRROR, rel_path)
        diff = MirrorDiff(path=rel_path, vault_exists=os.path.exists(src_path))

        if os.path.exists(src_path):
            if os.path.exists(dst):
                if filecmp.cmp(src_path, dst, shallow=False):
                    diff.mirror_vault_match = True
                    diff.status = "一致"
                    if not force:
                        result.skipped += 1
                        result.diffs.append(diff)
                        continue
                else:
                    diff.mirror_vault_match = False
                    diff.status = "已更新"
            else:
                diff.status = "新建"

            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src_path, dst)
            diff.mirror_exists = True
            result.copied += 1
        else:
            diff.status = "源文件缺失"
            result.missing_source += 1

        result.diffs.append(diff)

    law_src = LAW_DIR
    law_dst = os.path.join(VAULT_MIRROR, "案件", "法律法规")
    if os.path.isdir(law_src):
        for f in os.listdir(law_src):
            if f.endswith((".md", ".txt")):
                result.total_files += 1
                src = os.path.join(law_src, f)
                dst = os.path.join(law_dst, f)
                if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                    result.skipped += 1
                    continue
                shutil.copy2(src, dst)
                result.copied += 1

    evidence_src = os.path.join(VAULT_ROOT, "证据材料")
    evidence_dst = os.path.join(VAULT_MIRROR, "证据材料")
    if os.path.isdir(evidence_src):
        for f in os.listdir(evidence_src):
            if f.endswith((".md", ".txt")):
                result.total_files += 1
                src = os.path.join(evidence_src, f)
                dst = os.path.join(evidence_dst, f)
                if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                    result.skipped += 1
                    continue
                shutil.copy2(src, dst)
                result.copied += 1

    jiafu_src = os.path.join(VAULT_ROOT, "加付赔偿金")
    jiafu_dst = os.path.join(VAULT_MIRROR, "加付赔偿金")
    if os.path.isdir(jiafu_src):
        for f in os.listdir(jiafu_src):
            if f.endswith((".md", ".txt")):
                result.total_files += 1
                src = os.path.join(jiafu_src, f)
                dst = os.path.join(jiafu_dst, f)
                if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                    result.skipped += 1
                    continue
                shutil.copy2(src, dst)
                result.copied += 1

    if os.path.isdir(PANDOC_DIR):
        leian_dst = os.path.join(VAULT_MIRROR, "类案")
        for f in os.listdir(PANDOC_DIR):
            if "民终" in f and f.endswith(".md"):
                result.total_files += 1
                src = os.path.join(PANDOC_DIR, f)
                dst = os.path.join(leian_dst, f)
                if os.path.exists(dst) and filecmp.cmp(src, dst, shallow=False):
                    result.skipped += 1
                    continue
                shutil.copy2(src, dst)
                result.copied += 1

    logger.info("sync_mirror: total=%d, copied=%d, skipped=%d, missing=%d",
                result.total_files, result.copied, result.skipped, result.missing_source)
    return result


def resolve_path(rel_path: str, project_root: str = "") -> tuple[str, str]:
    candidates = []

    if project_root:
        candidates.append(("项目文件夹", os.path.join(project_root, rel_path)))

    candidates.append(("vault_mirror", os.path.join(VAULT_MIRROR, rel_path)))

    if os.path.isabs(rel_path):
        candidates.append(("绝对路径", rel_path))
    else:
        candidates.append(("Vault根目录", os.path.join(VAULT_ROOT, rel_path)))

    for source_name, full_path in candidates:
        if os.path.exists(full_path):
            return full_path, source_name

    missing = [f"{name}: {path}" for name, path in candidates]
    logger.warning("resolve_path: 文件未找到: %s → 尝试了 %s", rel_path, "; ".join(missing))
    return "", "未找到"


def check_consistency(project_root: str = "") -> list[MirrorDiff]:
    diffs = []

    for rel_path, vault_path in MIRROR_MAP.items():
        diff = MirrorDiff(path=rel_path)

        mirror_path = os.path.join(VAULT_MIRROR, rel_path)
        diff.mirror_exists = os.path.exists(mirror_path)
        diff.vault_exists = os.path.exists(vault_path)

        if project_root:
            project_path = os.path.join(project_root, rel_path)
            diff.project_exists = os.path.exists(project_path)
            if diff.project_exists and diff.mirror_exists:
                diff.project_mirror_match = filecmp.cmp(project_path, mirror_path, shallow=False)
            if diff.project_exists and diff.vault_exists:
                diff.project_vault_match = filecmp.cmp(project_path, vault_path, shallow=False)

        if diff.mirror_exists and diff.vault_exists:
            diff.mirror_vault_match = filecmp.cmp(mirror_path, vault_path, shallow=False)

        if diff.project_exists and diff.mirror_exists and not diff.project_mirror_match:
            diff.status = "项目与镜像不一致"
        elif diff.mirror_exists and diff.vault_exists and not diff.mirror_vault_match:
            diff.status = "镜像与Vault不一致"
        elif diff.project_exists or diff.mirror_exists or diff.vault_exists:
            diff.status = "一致"
        else:
            diff.status = "全部缺失"

        diffs.append(diff)

    return diffs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")

    print("=== 同步vault_mirror ===")
    result = sync_mirror()
    print(f"总文件: {result.total_files}, 复制: {result.copied}, 跳过: {result.skipped}, 缺失: {result.missing_source}")

    print("\n=== 一致性检查 ===")
    diffs = check_consistency()
    for d in diffs:
        print(f"  {d.path}: {d.status}")

    print("\n=== 路径解析测试 ===")
    test_paths = [
        ".trae/evidence_manifest.md",
        "案件/法律法规/法释〔2025〕12号 劳动争议司法解释二.md",
        "类案/（2020）苏03民终3088号.md",
    ]
    for tp in test_paths:
        resolved, source = resolve_path(tp)
        print(f"  {tp} → {source}: {resolved or '未找到'}")
