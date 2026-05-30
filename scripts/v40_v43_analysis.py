"""V40~V43 五份模拟二审判决书综合幻觉分析。

输出：
1. 每份文档的完整扫描结果
2. 版本间维度对比矩阵
3. H-1 案号验证轨迹（逐案号追踪验证路径）
4. H-2 日期验证轨迹
5. H-5 计算验证轨迹
6. 本地验证 vs 联网验证效果统计
7. 综合改进建议
"""

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from legal_hallucination_mcp.claim_parser import ClaimParser
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.law_citation_checker import LawCitationChecker
from legal_hallucination_mcp.law_knowledge_base import LawKnowledgeBase
from legal_hallucination_mcp.report_builder import generate_report_filename
from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.web_verifier import WebVerifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("v40_v43_analysis")

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
VAULT_ROOT = os.environ.get("VAULT_ROOT", str(Path(PROJECT_ROOT).parent))
MANIFEST_PATH = os.path.join(VAULT_ROOT, ".trae", "evidence_manifest.md")
LOCAL_LAW_DIR = os.path.join(PROJECT_ROOT, "vault_mirror", "案件", "法律法规")
OUTPUT_DIR = os.path.join(Path(__file__).resolve().parent, "output")

DOCUMENTS: list[dict[str, str]] = [
    {"label": "V43", "path": os.path.join(VAULT_ROOT, "V43_模拟二审判决书_苏06民终6271号劳动争议_20260528.md")},
    {"label": "V42", "path": os.path.join(VAULT_ROOT, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260527.md")},
    {"label": "V41", "path": os.path.join(VAULT_ROOT, "V41_模拟二审判决书_苏06民终6271号劳动争议_20260527.md")},
    {"label": "V40P1", "path": os.path.join(VAULT_ROOT, "V40P1_模拟二审判决书_苏06民终6271号劳动争议_20260527.md")},
    {"label": "V40", "path": os.path.join(VAULT_ROOT, "V40_模拟二审判决书_苏06民终6271号劳动争议_20260525.md")},
]


@dataclass
class DetailedScanResult:
    label: str = ""
    file_path: str = ""
    status: str = "pending"
    error: str = ""
    grade: str = ""
    score: float = 0.0
    total_flags: int = 0
    elapsed_sec: float = 0.0
    dimension_summary: dict[str, int] = field(default_factory=dict)
    h1_flags: list[dict] = field(default_factory=list)
    h2_flags: list[dict] = field(default_factory=list)
    h5_flags: list[dict] = field(default_factory=list)
    h6_flags: list[dict] = field(default_factory=list)
    structure_issues: list[dict] = field(default_factory=list)
    claim_violations: list[dict] = field(default_factory=list)
    law_citation_issues: list[dict] = field(default_factory=list)
    cross_ref_issues: list[dict] = field(default_factory=list)
    case_numbers_found: list[str] = field(default_factory=list)
    law_articles_found: list[str] = field(default_factory=list)


def scan_detailed(
    label: str,
    file_path: str,
    engine: RuleEngine,
    manifest_path: str,
    local_law_dir: str,
) -> DetailedScanResult:
    result = DetailedScanResult(label=label, file_path=file_path)

    if not os.path.exists(file_path):
        result.status = "file_not_found"
        result.error = f"File not found: {file_path}"
        return result

    with open(file_path, "r", encoding="utf-8") as f:
        doc_text = f.read()

    t0 = time.time()
    try:
        scan = engine.run_full_scan(
            document_text=doc_text,
            manifest_path=manifest_path,
            vault_root=VAULT_ROOT,
            local_law_dir=local_law_dir,
        )
        result.elapsed_sec = time.time() - t0
        result.status = "completed"
        result.total_flags = scan.total_flags
        result.grade = scan.risk_grade
        result.score = scan.hallucination_score

        for dim in scan.dimensions:
            result.dimension_summary[dim.h_code] = dim.total_flags
            for flag in dim.rule_flags:
                flag_dict = {
                    "rule_id": flag.rule_id,
                    "evidence": flag.evidence[:120],
                    "message": flag.message[:120],
                    "severity": flag.severity,
                    "line_number": flag.line_number,
                }
                if dim.h_code == "H-1":
                    result.h1_flags.append(flag_dict)
                elif dim.h_code == "H-2":
                    result.h2_flags.append(flag_dict)
                elif dim.h_code == "H-5":
                    result.h5_flags.append(flag_dict)
                elif dim.h_code == "H-6":
                    result.h6_flags.append(flag_dict)

        for item in scan.structure_issues:
            result.structure_issues.append({"issue_type": item.issue_type, "detail": item.detail[:100]})

        for item in scan.claim_violations:
            result.claim_violations.append({
                "claim_item": item.judgment_item,
                "claimed_amount": item.claim_max,
                "judgment_amount": item.judgment_amount,
                "violation_type": item.violation_type,
            })

        for item in scan.law_citation_issues:
            result.law_citation_issues.append({
                "citation": item.citation_text[:80],
                "issue_type": item.format_issues[0] if item.format_issues else ("replaced" if item.is_replaced else "unknown"),
            })

        result.cross_ref_issues = scan.cross_ref_issues[:10]

        cn_pattern = re.compile(r"[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?第?\d+号")
        result.case_numbers_found = list(set(cn_pattern.findall(doc_text)))

        law_pattern = re.compile(r'《[^》]+》\s*第[一二三四五六七八九十百千零\d]+条')
        result.law_articles_found = list(set(law_pattern.findall(doc_text)))

        logger.info(
            "[%s] completed: flags=%d grade=%s score=%.1f H1=%d H2=%d H5=%d H6=%d time=%.2fs",
            label, result.total_flags, result.grade, result.score,
            len(result.h1_flags), len(result.h2_flags),
            len(result.h5_flags), len(result.h6_flags),
            result.elapsed_sec,
        )
    except Exception as e:
        result.elapsed_sec = time.time() - t0
        result.status = "error"
        result.error = str(e)
        logger.error("[%s] failed: %s", label, e, exc_info=True)

    return result


def generate_comprehensive_report(results: list[DetailedScanResult], output_dir: str) -> str:
    now = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"v40_v43_comprehensive_{now}.md")

    lines = [
        "# V40~V43 模拟二审判决书综合幻觉分析报告",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"案号：(2025)苏06民终6271号 劳动争议",
        f"文档数量：{len(results)}",
        "",
        "---",
        "",
        "## 一、扫描总览",
        "",
        "| 版本 | 总分 | 等级 | 总标记 | H-1 | H-2 | H-3 | H-4 | H-5 | H-6 | 耗时 |",
        "|------|------|------|--------|-----|-----|-----|-----|-----|-----|------|",
    ]

    for r in results:
        dims = ["H-1", "H-2", "H-3", "H-4", "H-5", "H-6"]
        dim_vals = [str(r.dimension_summary.get(d, 0)) for d in dims]
        elapsed = f"{r.elapsed_sec:.2f}s"
        lines.append(
            f"| {r.label} | {r.score:.1f} | {r.grade} | {r.total_flags} | "
            + " | ".join(dim_vals) + f" | {elapsed} |"
        )

    lines.append("")

    lines.append("## 二、H-1 案号验证轨迹")
    lines.append("")
    lines.append("追踪每个版本中出现的案号及其验证路径：")
    lines.append("")

    all_case_numbers: set[str] = set()
    for r in results:
        all_case_numbers.update(r.case_numbers_found)

    for cn in sorted(all_case_numbers):
        lines.append(f"### 案号：{cn}")
        lines.append("")
        lines.append("| 版本 | 是否被标记为H-1 | 标记规则ID | 匹配文本 |")
        lines.append("|------|----------------|-----------|----------|")
        for r in results:
            flagged = False
            for flag in r.h1_flags:
                if cn[:10] in flag["evidence"] or cn in flag["evidence"]:
                    lines.append(
                        f"| {r.label} | ⚠️ 是 | {flag['rule_id']} | {flag['evidence'][:60]} |"
                    )
                    flagged = True
            if not flagged:
                if cn in r.case_numbers_found:
                    lines.append(f"| {r.label} | ✅ 未标记（已验证） | - | {cn[:60]} |")
                else:
                    lines.append(f"| {r.label} | - (案号未出现) | - | - |")
        lines.append("")

    lines.append("## 三、H-2 程序日期验证轨迹")
    lines.append("")
    lines.append("| 版本 | H-2标记数 | 典型标记文本 |")
    lines.append("|------|----------|-------------|")
    for r in results:
        sample = r.h2_flags[0]["evidence"][:60] if r.h2_flags else "-"
        lines.append(f"| {r.label} | {len(r.h2_flags)} | {sample} |")
    lines.append("")

    lines.append("## 四、H-5 计算与逻辑验证轨迹")
    lines.append("")
    lines.append("| 版本 | H-5标记数 | 典型标记文本 |")
    lines.append("|------|----------|-------------|")
    for r in results:
        sample = r.h5_flags[0]["evidence"][:60] if r.h5_flags else "-"
        lines.append(f"| {r.label} | {len(r.h5_flags)} | {sample} |")
    lines.append("")

    lines.append("## 五、法条引用验证轨迹")
    lines.append("")
    all_law_articles: set[str] = set()
    for r in results:
        all_law_articles.update(r.law_articles_found)

    lines.append(f"共发现 **{len(all_law_articles)}** 个不同法条引用：")
    lines.append("")
    for art in sorted(all_law_articles):
        versions_with = [r.label for r in results if art in r.law_articles_found]
        lines.append(f"- `{art}` — 出现于：{', '.join(versions_with) if versions_with else '无'}")
    lines.append("")

    lines.append("## 六、诉求金额越界检测")
    lines.append("")
    has_violations = any(r.claim_violations for r in results)
    if has_violations:
        lines.append("| 版本 | 诉请项目 | 诉请金额 | 判决金额 | 越界类型 |")
        lines.append("|------|---------|---------|---------|---------|")
        for r in results:
            for v in r.claim_violations:
                lines.append(
                    f"| {r.label} | {v['claim_item'][:30]} | {v['claimed_amount']} | "
                    f"{v['judgment_amount']} | {v['violation_type']} |"
                )
    else:
        lines.append("✅ 所有版本均未检测到诉审不一致（超诉请裁判）问题。")
    lines.append("")

    lines.append("## 七、结构完整性检测")
    lines.append("")
    for r in results:
        if r.structure_issues:
            lines.append(f"### {r.label}")
            for issue in r.structure_issues:
                lines.append(f"- [{issue['issue_type']}] {issue['detail']}")
            lines.append("")
        else:
            lines.append(f"### {r.label}: ✅ 结构完整")
            lines.append("")

    lines.append("## 八、版本演进趋势")
    lines.append("")
    lines.append("### 8.1 幻觉风险评分趋势")
    lines.append("```")
    for r in sorted(results, key=lambda x: x.label):
        bar_len = int(r.score / 100 * 50) if r.score > 0 else 1
        bar = "█" * max(bar_len, 1)
        lines.append(f"{r.label}: {r.score:6.1f} |{bar}")
    lines.append("```")
    lines.append("")

    lines.append("### 8.2 各维度标记数趋势")
    lines.append("```")
    dims = ["H-1", "H-2", "H-3", "H-4", "H-5", "H-6"]
    header = f"{'版本':<6}" + "".join(f"{d:>5}" for d in dims) + f"{'总计':>6}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in sorted(results, key=lambda x: x.label):
        vals = "".join(f"{r.dimension_summary.get(d, 0):>5}" for d in dims)
        lines.append(f"{r.label:<6}{vals}{r.total_flags:>6}")
    lines.append("```")
    lines.append("")

    lines.append("### 8.3 关键改进指标")
    lines.append("")
    sorted_results = sorted(results, key=lambda x: x.label)
    if len(sorted_results) >= 2:
        oldest = sorted_results[0]
        newest = sorted_results[-1]
        score_delta = newest.score - oldest.score
        flags_delta = newest.total_flags - oldest.total_flags
        h1_delta = len(newest.h1_flags) - len(oldest.h1_flags)
        h2_delta = len(newest.h2_flags) - len(oldest.h2_flags)
        h5_delta = len(newest.h5_flags) - len(oldest.h5_flags)

        lines.append(f"| 指标 | {oldest.label} | {newest.label} | 变化 |")
        lines.append("|------|------|------|------|")
        lines.append(f"| 风险评分 | {oldest.score:.1f} | {newest.score:.1f} | {score_delta:+.1f} |")
        lines.append(f"| 总标记数 | {oldest.total_flags} | {newest.total_flags} | {flags_delta:+d} |")
        lines.append(f"| H-1 案号造假 | {len(oldest.h1_flags)} | {len(newest.h1_flags)} | {h1_delta:+d} |")
        lines.append(f"| H-2 日期杜撰 | {len(oldest.h2_flags)} | {len(newest.h2_flags)} | {h2_delta:+d} |")
        lines.append(f"| H-5 计算异常 | {len(oldest.h5_flags)} | {len(newest.h5_flags)} | {h5_delta:+d} |")
    lines.append("")

    lines.append("## 九、本地验证效果统计")
    lines.append("")
    lines.append("本批次启用验证层级：")
    lines.append("- ✅ 案号本地验证（manifest + related + law_kb + evidence_texts）")
    lines.append("- ✅ 法条本地验证（law_kb.articles + local_law_texts）")
    lines.append("- ✅ 证据引注跳过（fact_detail 行内 `见《` 检测）")
    lines.append("- ✅ 诉求金额校验（claim_limits 匹配）")
    lines.append("- ✅ 联网二次验证（WebVerifier HTTP → 权威网站）")
    lines.append("- ✅ 详细调试日志（[REASON]/[WEB_OK]/[WEB_FAIL]/[WEB_ERR]）")
    lines.append("")
    lines.append("验证优先级链路：本地文件 → law_kb → 权威网站HTTP → 标记幻觉")
    lines.append("")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Comprehensive report written to %s", report_path)
    return report_path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    web_verifier = WebVerifier()
    engine = RuleEngine(
        evidence_index=EvidenceIndex(),
        claim_parser=ClaimParser(),
        law_checker=LawCitationChecker(),
        law_kb=LawKnowledgeBase(),
        web_verifier=web_verifier,
        verbose=True,
    )

    valid_docs = []
    for doc in DOCUMENTS:
        if os.path.exists(doc["path"]):
            valid_docs.append(doc)
        else:
            logger.warning("Skipping missing file: [%s] %s", doc["label"], doc["path"])

    logger.info("Starting comprehensive analysis of %d documents", len(valid_docs))

    results: list[DetailedScanResult] = []
    for doc in valid_docs:
        logger.info("=== Analyzing [%s] ===", doc["label"])
        r = scan_detailed(
            label=doc["label"],
            file_path=doc["path"],
            engine=engine,
            manifest_path=MANIFEST_PATH,
            local_law_dir=LOCAL_LAW_DIR,
        )
        results.append(r)

    completed = sum(1 for r in results if r.status == "completed")
    logger.info("Analysis finished: %d/%d completed", completed, len(results))

    vstats = web_verifier.get_verification_summary()
    logger.info("WebVerification cache: %s", vstats)

    report_path = generate_comprehensive_report(results, OUTPUT_DIR)
    print(f"\n📄 综合报告: {report_path}")

    data_path = os.path.join(OUTPUT_DIR, f"v40_v43_data_{time.strftime('%Y%m%d_%H%M%S')}.json")
    serializable = []
    for r in results:
        serializable.append({
            "label": r.label,
            "status": r.status,
            "grade": r.grade,
            "score": r.score,
            "total_flags": r.total_flags,
            "dimension_summary": r.dimension_summary,
            "h1_flags": r.h1_flags,
            "h2_flags": r.h2_flags,
            "h5_flags": r.h5_flags,
            "h6_flags": r.h6_flags,
            "case_numbers_found": r.case_numbers_found,
            "law_articles_found": r.law_articles_found,
            "claim_violations": r.claim_violations,
            "structure_issues": r.structure_issues,
        })
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({"results": serializable, "verification_stats": vstats}, f, ensure_ascii=False, indent=2)
    print(f"📊 数据文件: {data_path}")

    return 0 if completed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())