import re
import sys
import os
import shutil
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def fix_law_citation_format(text: str) -> tuple[str, list[str]]:
    patterns = [
        (r'《中华人民共和国劳动合同法实施条例》', '《中华人民共和国劳动合同法实施条例》（以下简称《劳动合同法实施条例》）'),
        (r'《中华人民共和国民事诉讼法》', '《中华人民共和国民事诉讼法》（以下简称《民事诉讼法》）'),
        (r'《中华人民共和国劳动合同法》', '《中华人民共和国劳动合同法》（以下简称《劳动合同法》）'),
        (r'《中华人民共和国民法典》', '《中华人民共和国民法典》（以下简称《民法典》）'),
        (r'《中华人民共和国劳动争议调解仲裁法》', '《中华人民共和国劳动争议调解仲裁法》（以下简称《劳动争议调解仲裁法》）'),
        (r'《中华人民共和国职业病防治法》', '《中华人民共和国职业病防治法》（以下简称《职业病防治法》）'),
        (r'《中华人民共和国劳动法》', '《中华人民共和国劳动法》（以下简称《劳动法》）'),
    ]

    applied = []
    result = text

    for pattern, replacement in patterns:
        if pattern in result:
            first_idx = result.index(pattern)
            before = result[:first_idx]
            after = result[first_idx + len(pattern):]
            result = before + replacement + after
            law_name = pattern.replace('《中华人民共和国', '').replace('》', '')
            applied.append(law_name)

    return result, applied


def fix_procedural_dates(text: str) -> tuple[str, list[str]]:
    fixes = []

    procedural_date_patterns = [
        (r'本院于2025年11月18日立案后', '本院于2025年11月18日立案后（见《二审立案通知书.md》）'),
        (r'于2025年11月28日、2026年1月9日进行庭询谈话', '于2025年11月28日、2026年1月9日进行庭询谈话（见《二审庭审笔录.md》）'),
        (r'于2026年5月11日公开开庭进行了审理', '于2026年5月11日公开开庭进行了审理（见《二审开庭笔录.md》）'),
    ]

    result = text
    for pattern, replacement in procedural_date_patterns:
        if re.search(pattern, result):
            result = re.sub(pattern, replacement, result, count=1)
            fixes.append(f"程序日期标注: {pattern[:30]}...")

    return result, fixes


def fix_claim_dates(text: str) -> tuple[str, list[str]]:
    fixes = []

    claim_date_patterns = [
        (r'自2021年12月1日起至今存在事实劳动关系', '自2021年12月1日起至今存在事实劳动关系（见《证据1_Offer微信记录.md》，原告主张入职日期）'),
    ]

    result = text
    for pattern, replacement in claim_date_patterns:
        if re.search(pattern, result):
            result = re.sub(pattern, replacement, result, count=1)
            fixes.append(f"入职日期标注: {pattern[:30]}...")

    return result, fixes


def fix_judgment_date(text: str) -> tuple[str, list[str]]:
    fixes = []

    result = text
    pattern = r'一审2025年4月28日'
    if re.search(pattern, result):
        result = re.sub(pattern, '一审法院于2025年4月28日作出（2025）苏0602民初4514号民事判决（见《一审判决书.md》）', result, count=1)
        fixes.append("一审判决日期标注")

    return result, fixes


def fix_labor_relation_cutoff(text: str) -> tuple[str, list[str]]:
    fixes = []

    result = text
    pattern = r'将劳动关系截断至2024年11月30日'
    if re.search(pattern, result):
        result = re.sub(
            pattern,
            '将劳动关系截断至2024年11月30日（见《劳动合同.md》第四条，合同约定终止日期）',
            result,
        )
        fixes.append("劳动关系截止日期标注")

    return result, fixes


def run_batch_fix(doc_path: str) -> None:
    print(f"=== 批量修复: {os.path.basename(doc_path)} ===")

    with open(doc_path, "r", encoding="utf-8") as f:
        original = f.read()

    result = original
    all_fixes = []

    result, fixes = fix_law_citation_format(result)
    all_fixes.extend([f"法条简称: {f}" for f in fixes])

    result, fixes = fix_procedural_dates(result)
    all_fixes.extend(fixes)

    result, fixes = fix_claim_dates(result)
    all_fixes.extend(fixes)

    result, fixes = fix_judgment_date(result)
    all_fixes.extend(fixes)

    result, fixes = fix_labor_relation_cutoff(result)
    all_fixes.extend(fixes)

    if result == original:
        print("无需修复，原文未变更。")
        return

    backup_path = doc_path.replace(".md", f"_backup_{datetime.now().strftime('%Y%m%d%H%M%S')}.md")
    shutil.copy2(doc_path, backup_path)
    print(f"备份: {os.path.basename(backup_path)}")

    with open(doc_path, "w", encoding="utf-8") as f:
        f.write(result)

    print(f"\n修复完成，共 {len(all_fixes)} 项:")
    for i, fix in enumerate(all_fixes, 1):
        print(f"  {i}. {fix}")

    print(f"\n文件已更新: {doc_path}")


if __name__ == "__main__":
    from _paths import get_vault_root
    vault_root = get_vault_root()
    docs = [
        os.path.join(vault_root, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260528.md"),
    ]

    for doc in docs:
        if os.path.exists(doc):
            run_batch_fix(doc)
        else:
            print(f"文件不存在: {doc}")
