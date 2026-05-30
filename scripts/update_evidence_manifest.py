"""证据清单更新CLI — 交互式管理evidence_manifest.md

用法:
  python update_evidence_manifest.py --check                    # 一致性检查
  python update_evidence_manifest.py --add "路径1" "路径2"      # 添加新证据
  python update_evidence_manifest.py --add "路径1" --approve    # 添加并直接更新
  python update_evidence_manifest.py --update "证据30" --path "新路径"  # 更新条目
  python update_evidence_manifest.py --from-report 报告.md      # 从检测报告提取缺失证据
  python update_evidence_manifest.py --diff evidence_manifest_v2.md  # 对比差异
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from legal_hallucination_mcp.evidence_manifest_updater import (
    EvidenceEntry,
    EvidenceManifestUpdater,
)
from _paths import get_vault_root, get_manifest_path

MANIFEST_PATH = get_manifest_path()
VAULT_ROOT = get_vault_root()


def cmd_check(updater: EvidenceManifestUpdater):
    issues = updater.check_consistency()
    if not issues:
        print("✅ 一致性检查通过，无问题")
        return

    print(f"⚠️ 发现 {len(issues)} 个一致性问题：\n")
    for i, issue in enumerate(issues, 1):
        icon = {"重复条目": "🔴", "编号重复": "🔴", "文件缺失": "🟡", "编号跳跃": "🟠"}.get(
            issue["type"], "⚪"
        )
        print(f"  {icon} [{i}] {issue['type']}: {issue['entry']}")
        print(f"      {issue['detail']}")


def cmd_add(updater: EvidenceManifestUpdater, paths: list[str], approve: bool):
    new_entries = []
    for p in paths:
        if not os.path.isabs(p):
            p = os.path.join(VAULT_ROOT, p)
        entry = EvidenceEntry(p, category="证据类")
        new_entries.append(entry)

    duplicates = updater.check_duplicates(new_entries)
    if duplicates:
        print(f"⚠️ 发现 {len(duplicates)} 个重复条目（将被跳过）：")
        for d in duplicates:
            print(f"  - {d.name_no_ext}")
        print()

    result = updater.add_entries(new_entries, auto_approve=approve)

    if result["added"]:
        print(f"✅ 已添加 {len(result['added'])} 个条目：")
        for name in result["added"]:
            print(f"  + {name}")
    if result["skipped_missing"]:
        print(f"⚠️ 跳过 {len(result['skipped_missing'])} 个文件缺失的条目：")
        for name in result["skipped_missing"]:
            print(f"  ✗ {name}")

    if result["output_path"]:
        if approve:
            print(f"\n📝 已更新: {result['output_path']}")
        else:
            print(f"\n📝 新版本已生成: {result['output_path']}")
            print("   请检查后手动替换原文件，或使用 --approve 直接更新")


def cmd_update(updater: EvidenceManifestUpdater, name: str, path: str | None, desc: str | None, category: str | None, approve: bool):
    result = updater.update_entry(
        name_pattern=name,
        new_path=path,
        new_description=desc,
        new_category=category,
        auto_approve=approve,
    )

    if not result["success"]:
        print(f"❌ {result.get('error', '更新失败')}")
        return

    if not result.get("changes"):
        print("ℹ️ 无变更")
        return

    print(f"✅ 已更新 '{result['target']}'：")
    for change in result["changes"]:
        print(f"  - {change}")

    if result.get("output_path"):
        if approve:
            print(f"\n📝 已更新: {result['output_path']}")
        else:
            print(f"\n📝 新版本已生成: {result['output_path']}")


def cmd_from_report(updater: EvidenceManifestUpdater, report_path: str, approve: bool):
    if not os.path.exists(report_path):
        print(f"❌ 报告文件不存在: {report_path}")
        return

    with open(report_path, "r", encoding="utf-8") as f:
        report_text = f.read()

    missing = updater.extract_missing_evidence_from_report(report_text)
    if not missing:
        print("ℹ️ 未从报告中提取到缺失证据条目")
        return

    print(f"📋 从报告中提取到 {len(missing)} 个缺失证据条目：\n")
    for i, entry in enumerate(missing, 1):
        exists_icon = "✅" if entry.exists else "❌"
        print(f"  {exists_icon} [{i}] {entry.name_no_ext} ({entry.category})")
        print(f"      路径: {entry.path}")

    duplicates = updater.check_duplicates(missing)
    unique = [e for e in missing if e not in duplicates]

    if duplicates:
        print(f"\n⚠️ {len(duplicates)} 个条目已存在于清单中（将被跳过）：")
        for d in duplicates:
            print(f"  - {d.name_no_ext}")

    if not unique:
        print("\nℹ️ 无新条目需要添加")
        return

    if not approve:
        print(f"\n是否添加 {len(unique)} 个新条目？")
        print("  使用 --approve 确认添加并更新原文件")
        print("  不加 --approve 将生成新版本文件供检查")

    result = updater.add_entries(unique, auto_approve=approve)

    if result["added"]:
        print(f"\n✅ 已添加 {len(result['added'])} 个条目")
    if result["output_path"]:
        if approve:
            print(f"📝 已更新: {result['output_path']}")
        else:
            print(f"📝 新版本已生成: {result['output_path']}")


def cmd_diff(updater: EvidenceManifestUpdater, other_path: str):
    diffs = updater.diff(other_path)
    if not diffs:
        print("✅ 两个文件无差异")
        return

    print(f"📊 发现 {len(diffs)} 处差异：\n")
    for i, d in enumerate(diffs, 1):
        icon = {"新增": "🟢", "删除": "🔴", "修改": "🟡", "error": "❌"}.get(d["type"], "⚪")
        print(f"  {icon} [{i}] {d['type']}: {d['entry']}")
        if d["type"] == "修改":
            for change in d.get("changes", []):
                print(f"      {change}")
        elif d["type"] in ("新增", "删除"):
            print(f"      路径: {d.get('path', 'N/A')}")


def main():
    parser = argparse.ArgumentParser(description="证据清单更新工具")
    parser.add_argument("--manifest", default=MANIFEST_PATH, help="证据清单路径")
    parser.add_argument("--vault", default=VAULT_ROOT, help="Vault根目录")
    parser.add_argument("--approve", action="store_true", help="直接更新原文件（否则生成新版本）")

    sub = parser.add_mutually_exclusive_group(required=True)
    sub.add_argument("--check", action="store_true", help="一致性检查")
    sub.add_argument("--add", nargs="+", metavar="PATH", help="添加新证据文件路径")
    sub.add_argument("--update", metavar="NAME", help="更新指定条目")
    sub.add_argument("--from-report", metavar="REPORT", help="从检测报告提取缺失证据")
    sub.add_argument("--diff", metavar="OTHER", help="对比两个清单文件的差异")

    parser.add_argument("--path", help="更新条目的新路径")
    parser.add_argument("--desc", help="更新条目的新描述")
    parser.add_argument("--category", help="更新条目的新类别")

    args = parser.parse_args()

    updater = EvidenceManifestUpdater(args.manifest, args.vault)
    load_result = updater.load()
    if not load_result["success"]:
        print(f"❌ 加载清单失败: {args.manifest}")
        sys.exit(1)

    print(f"📂 已加载清单: {args.manifest} ({load_result['entries']} 条)\n")

    if args.check:
        cmd_check(updater)
    elif args.add:
        cmd_add(updater, args.add, args.approve)
    elif args.update:
        cmd_update(updater, args.update, args.path, args.desc, args.category, args.approve)
    elif args.from_report:
        cmd_from_report(updater, args.from_report, args.approve)
    elif args.diff:
        cmd_diff(updater, args.diff)


if __name__ == "__main__":
    main()
