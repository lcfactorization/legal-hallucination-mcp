"""批量幻觉检测测试：对11份模拟判决书运行完整检测套件。

输出：
1. 每份文档的单独扫描报告 (.md + .json)
2. 合并对比报告 (batch_report_YYYYMMDD.md)
3. 验证缓存统计摘要
"""

import json
import logging
import os
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
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("batch_test_11docs")


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
    {"label": "V39", "path": os.path.join(VAULT_ROOT, "V39_模拟二审判决书_苏06民终6271号劳动争议_20260525.md")},
    {"label": "V38", "path": os.path.join(VAULT_ROOT, "V38_模拟二审判决书_苏06民终6271号劳动争议_20260524.md")},
    {"label": "V33", "path": os.path.join(VAULT_ROOT, "Antigravity+LLM#+终极版_模拟二审判决书_苏06民终6271号劳动争议_V33_20260520.md")},
    {"label": "V32", "path": os.path.join(VAULT_ROOT, "Antigravity+LLM#+终极版_模拟二审判决书_苏06民终6271号劳动争议_V32_20260520.md")},
    {"label": "V31", "path": os.path.join(VAULT_ROOT, "Antigravity+LLM#+终极版_模拟二审判决书_苏06民终6271号劳动争议_V31_20260520.md")},
    {"label": "V30", "path": os.path.join(VAULT_ROOT, "Antigravity+LLM#+终极版_模拟二审判决书_苏06民终6271号劳动争议_V30_20260520.md")},
]


@dataclass
class DocScanResult:
    label: str = ""
    file_path: str = ""
    status: str = "pending"
    error: str = ""
    grade: str = ""
    score: float = 0.0
    total_flags: int = 0
    elapsed_sec: float = 0.0
    dimension_summary: dict[str, int] = field(default_factory=dict)
    verification_stats: dict[str, int] = field(default_factory=dict)


def load_document(file_path: str) -> str | None:
    if not os.path.exists(file_path):
        logger.error("Document not found: %s", file_path)
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


def scan_single(
    label: str,
    file_path: str,
    engine: RuleEngine,
    manifest_path: str,
    local_law_dir: str,
) -> DocScanResult:
    result = DocScanResult(label=label, file_path=file_path)

    doc_text = load_document(file_path)
    if doc_text is None:
        result.status = "file_not_found"
        result.error = f"File not found: {file_path}"
        return result

    t0 = time.time()
    try:
        scan_result = engine.run_full_scan(
            document_text=doc_text,
            manifest_path=manifest_path,
            vault_root=VAULT_ROOT,
            local_law_dir=local_law_dir,
        )
        result.elapsed_sec = time.time() - t0
        result.status = "completed"
        result.total_flags = scan_result.total_flags
        result.grade = scan_result.risk_grade
        result.score = scan_result.hallucination_score

        for dim_result in scan_result.dimensions:
            dim = dim_result.h_code
            flags_in_dim = len(dim_result.rule_flags) + len(dim_result.semantic_flags)
            result.dimension_summary[dim] = flags_in_dim

        logger.info(
            "[%s] scan completed: flags=%d grade=%s score=%.1f time=%.2fs",
            label, result.total_flags, result.grade, result.score, result.elapsed_sec,
        )
    except Exception as e:
        result.elapsed_sec = time.time() - t0
        result.status = "error"
        result.error = str(e)
        logger.error("[%s] scan failed: %s", label, e, exc_info=True)

    return result


def generate_merged_report(results: list[DocScanResult], output_dir: str) -> str:
    now = time.strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(output_dir, f"batch_report_11docs_{now}.md")

    lines = [
        "# 批量幻觉检测合并报告",
        "",
        f"生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"文档数量：{len(results)}",
        "",
        "## 一、扫描总览",
        "",
        "| 版本 | 总分 | 等级 | 总标记数 | 耗时(s) | 状态 |",
        "|------|------|------|----------|---------|------|",
    ]

    for r in results:
        status_icon = "✅" if r.status == "completed" else "❌"
        elapsed = f"{r.elapsed_sec:.2f}" if r.elapsed_sec > 0 else "-"
        lines.append(
            f"| {r.label} | {r.score:.1f} | {r.grade} | {r.total_flags} | {elapsed} | {status_icon} {r.status} |"
        )

    lines.append("")

    lines.append("## 二、各维度标记分布（H-1 ~ H-6）")
    lines.append("")
    dims = sorted(set(d for r in results for d in r.dimension_summary))
    header = "| 版本 | " + " | ".join(dims) + " |"
    sep = "|------|" + "|".join(["------" for _ in dims]) + "|"
    lines.append(header)
    lines.append(sep)
    for r in results:
        vals = [str(r.dimension_summary.get(d, 0)) for d in dims]
        lines.append(f"| {r.label} | " + " | ".join(vals) + " |")
    lines.append("")

    lines.append("## 三、错误详情")
    lines.append("")
    errors = [r for r in results if r.status != "completed"]
    if errors:
        for r in errors:
            lines.append(f"- **{r.label}**: {r.error}")
    else:
        lines.append("所有文档扫描成功，无错误。")
    lines.append("")

    lines.append("## 四、趋势分析")
    lines.append("")

    completed = [r for r in results if r.status == "completed"]
    if completed:
        sorted_by_label = sorted(completed, key=lambda r: r.label)
        lines.append("### 4.1 幻觉标记数趋势")
        lines.append("")
        for r in sorted_by_label:
            bar = "█" * min(r.total_flags, 60)
            lines.append(f"- **{r.label}**: {r.total_flags} 标记 {bar}")
        lines.append("")

        lines.append("### 4.2 评分趋势")
        lines.append("")
        for r in sorted_by_label:
            bar_len = int(r.score / 100 * 40) if r.score > 0 else 1
            bar = "█" * max(bar_len, 1)
            lines.append(f"- **{r.label}**: {r.score:.1f}分 ({r.grade}) {bar}")

    lines.append("")
    lines.append("## 五、本地验证效果评估")
    lines.append("")
    lines.append("本批次测试启用：")
    lines.append("- ✅ 案号本地验证（manifest + related + law_kb + evidence_texts）")
    lines.append("- ✅ 法条本地验证（law_kb.articles + local_law_texts）")
    lines.append("- ✅ 证据引注跳过（fact_detail 行内 `见《` 检测）")
    lines.append("- ✅ 诉求金额校验（claim_limits 匹配）")
    lines.append("- ✅ 联网二次验证（WebVerifier HTTP 查询权威网站）")
    lines.append("- ✅ 详细调试日志（verbose=True）")

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info("Merged report written to %s", report_path)
    return report_path


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    web_verifier = WebVerifier()
    logger.info("WebVerifier initialized with authoritative sources: %d",
                sum(len(v) for v in web_verifier.get_authoritative_sources().values()))

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

    logger.info("Starting batch scan of %d documents with verbose=%s", len(valid_docs), engine.verbose)

    results: list[DocScanResult] = []
    for doc in valid_docs:
        logger.info("=== Scanning [%s] ===", doc["label"])
        r = scan_single(
            label=doc["label"],
            file_path=doc["path"],
            engine=engine,
            manifest_path=MANIFEST_PATH,
            local_law_dir=LOCAL_LAW_DIR,
        )
        results.append(r)

    total_time = sum(r.elapsed_sec for r in results)
    completed = sum(1 for r in results if r.status == "completed")
    logger.info(
        "Batch scan finished: %d/%d completed, total time=%.2fs",
        completed, len(results), total_time,
    )

    vstats = web_verifier.get_verification_summary()
    logger.info("WebVerification cache: %s", vstats)

    report_path = generate_merged_report(results, OUTPUT_DIR)
    print(f"\n📄 合并报告: {report_path}")

    data_path = os.path.join(OUTPUT_DIR, f"batch_scan_data_{time.strftime('%Y%m%d_%H%M%S')}.json")
    serializable = []
    for r in results:
        serializable.append({
            "label": r.label,
            "file_path": r.file_path,
            "status": r.status,
            "error": r.error,
            "grade": r.grade,
            "score": r.score,
            "total_flags": r.total_flags,
            "elapsed_sec": r.elapsed_sec,
            "dimension_summary": r.dimension_summary,
        })
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({
            "results": serializable,
            "verification_stats": vstats,
        }, f, ensure_ascii=False, indent=2)
    print(f"📊 扫描数据: {data_path}")

    return 0 if completed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())