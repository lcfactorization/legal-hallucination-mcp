"""对模拟判决书运行全量幻觉检测，生成报告（支持V38~V42及多版本比对）。"""

import json
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.report_builder import ReportBuilder, generate_report_filename
from _paths import get_vault_root, get_output_dir

VAULT_ROOT = get_vault_root()
MCP_ROOT = os.path.dirname(os.path.abspath(__file__))

DOCUMENTS = [
    {
        "name": "V40",
        "path": os.path.join(VAULT_ROOT, "V40_模拟二审判决书_苏06民终6271号劳动争议_20260525.md"),
    },
    {
        "name": "V42",
        "path": os.path.join(VAULT_ROOT, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260528.md"),
    },
]

MANIFEST_PATH = os.path.join(VAULT_ROOT, ".trae", "evidence_manifest.md")

PROGRESS_MILESTONES = [50, 100]


def _score_complaint_candidate(fp: str, text: str, vault_root: str) -> tuple[int, ...]:
    """评分越低优先级越高。

    评分规则（按优先级排序）：
    1. 根目录优先（depth=0）
    2. 文件名包含"起诉状"优先于"上诉状"
    3. 纯文本格式优先（字符数适中，<10000）
    4. 排除 graphify/converted 目录
    5. 包含标准"诉讼请求"标题优先
    """
    rel_depth = fp.count(os.sep) - vault_root.count(os.sep)
    is_converted = "converted" in fp or "graphify" in fp
    has_complaint = "起诉状" in os.path.basename(fp)
    has_appeal = "上诉状" in os.path.basename(fp)
    has_claim_section = "诉讼请求" in text[:5000] if text else False
    is_plaintext = len(text) < 10000 if text else False

    priority = 0 if not is_converted else 10
    type_score = 0 if has_complaint else (1 if has_appeal else 2)
    depth_score = rel_depth
    format_score = 0 if is_plaintext else 1
    section_score = 0 if has_claim_section else 1

    return (priority, type_score, depth_score, format_score, section_score)


def find_complaint_text(vault_root: str) -> str:
    search_dirs = [vault_root, os.path.join(vault_root, "案件")]
    candidates = []
    for search_dir in search_dirs:
        if not os.path.exists(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            for f in files:
                if f.endswith(".md") and ("起诉状" in f or "上诉状" in f):
                    fp = os.path.join(root, f)
                    try:
                        with open(fp, "r", encoding="utf-8") as fh:
                            text = fh.read()
                    except Exception:
                        continue
                    score = _score_complaint_candidate(fp, text, vault_root)
                    candidates.append((score, fp, text))
    if not candidates:
        return ""
    candidates.sort(key=lambda x: x[0])
    return candidates[0][2]


def run_single_detection(
    engine: RuleEngine,
    builder: ReportBuilder,
    doc_info: dict,
    complaint_text: str,
    manifest_path: str,
    vault_root: str,
) -> dict:
    doc_path = doc_info["path"]
    doc_name = doc_info["name"]

    if not os.path.exists(doc_path):
        print(f"[SKIP] 文件不存在：{doc_path}")
        return {"name": doc_name, "success": False, "error": f"文件不存在: {doc_path}"}

    print(f"\n{'='*60}")
    print(f"[检测] {doc_name}: {os.path.basename(doc_path)}")
    print(f"{'='*60}")

    with open(doc_path, "r", encoding="utf-8") as f:
        document_text = f.read()

    print(f"[INFO] 文档长度={len(document_text)} 字符")

    t0 = time.time()
    result = engine.run_full_scan(
        document_text=document_text,
        complaint_text=complaint_text,
        manifest_path=manifest_path,
        vault_root=vault_root,
    )
    elapsed = time.time() - t0

    result.document_path = doc_path
    result.manifest_path = manifest_path

    try:
        std_filename = generate_report_filename(
            agent_name="TraeCN",
            llm_name="GLM-5.1",
            content_summary=f"法律文书幻觉检测报告_{doc_name}",
            version="v2.0",
        )
    except ValueError:
        std_filename = f"hallucination_report_{doc_name}.md"

    out_path = os.path.join(MCP_ROOT, "output", std_filename)

    report_md = builder.build_report(result)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"[结果] 总标记数={result.total_flags}, 幻觉评分={result.hallucination_score:.1f}, 风险等级={result.risk_grade}")
    print(f"[耗时] {elapsed:.2f}s")
    print(f"[输出] 报告已保存至：{out_path}")
    print(f"[文件名] 标准格式验证: {'通过' if std_filename != f'hallucination_report_{doc_name}.md' else '回退'}")

    print(f"\n--- 各维度摘要 ---")
    for dim in result.dimensions:
        print(f"  {dim.h_code} {dim.dimension_title}: 标记={dim.total_flags}, "
              f"严重={dim.critical_count}, 高={dim.high_count}, 中={dim.medium_count}, 低={dim.low_count}")

    print(f"\n--- 特殊检测项 ---")
    print(f"  结构问题: {len(result.structure_issues)}")
    print(f"  引注欺诈: {len(result.citation_frauds)}")
    print(f"  诉请违规: {len(result.claim_violations)}")
    print(f"  三段论断裂: {len(result.syllogism_breaks)}")
    print(f"  修辞过度: {len(result.rhetoric_items)}")
    print(f"  法条引用问题: {len(result.law_citation_issues)}")
    print(f"  事实来源问题: {len(result.fact_source_issues)}")
    print(f"  时效问题: {len(result.time_bar_issues)}")
    print(f"  方法论替换: {len(result.methodology_replacements)}")
    print(f"  利息基数: {len(result.interest_base_items)}")
    print(f"  计算核验问题: {len(result.calculation_issues)}")
    print(f"  诉请对比项: {len(result.claim_comparisons)}")

    return {
        "name": doc_name,
        "success": True,
        "output_path": out_path,
        "filename": std_filename,
        "total_flags": result.total_flags,
        "hallucination_score": result.hallucination_score,
        "risk_grade": result.risk_grade,
        "elapsed_seconds": round(elapsed, 2),
        "dimensions": [
            {
                "h_code": d.h_code,
                "title": d.dimension_title,
                "flags": d.total_flags,
                "critical": d.critical_count,
                "high": d.high_count,
                "medium": d.medium_count,
                "low": d.low_count,
            }
            for d in result.dimensions
        ],
    }


def run_comparison(results: list[dict]) -> None:
    if len(results) < 2:
        print("[SKIP] 不足2份报告，无法比对")
        return

    a, b = results[0], results[1]
    if not a.get("success") or not b.get("success"):
        print("[SKIP] 有报告生成失败，跳过比对")
        return

    print(f"\n{'='*60}")
    print(f"[比对] {a['name']} vs {b['name']}")
    print(f"{'='*60}")

    score_diff = a["hallucination_score"] - b["hallucination_score"]
    print(f"  幻觉评分: {a['name']}={a['hallucination_score']:.1f}, {b['name']}={b['hallucination_score']:.1f}, 差值={score_diff:.1f}")
    print(f"  风险等级: {a['name']}={a['risk_grade']}, {b['name']}={b['risk_grade']}")
    print(f"  总标记数: {a['name']}={a['total_flags']}, {b['name']}={b['total_flags']}")

    dims_a = {d["h_code"]: d for d in a.get("dimensions", [])}
    dims_b = {d["h_code"]: d for d in b.get("dimensions", [])}

    print(f"\n  --- 维度对比 ---")
    for h_code in sorted(set(list(dims_a.keys()) + list(dims_b.keys()))):
        da = dims_a.get(h_code, {"flags": 0, "title": "?"})
        db = dims_b.get(h_code, {"flags": 0, "title": "?"})
        diff = da["flags"] - db["flags"]
        marker = "改善" if diff > 0 else "恶化" if diff < 0 else "持平"
        print(f"    {h_code} {da['title']}: {da['flags']}→{db['flags']} ({marker})")

    comparison_path = os.path.join(MCP_ROOT, "output", f"comparison_{a['name']}_vs_{b['name']}.json")
    comparison_data = {
        "version_a": a["name"],
        "version_b": b["name"],
        "score_a": a["hallucination_score"],
        "score_b": b["hallucination_score"],
        "score_diff": round(score_diff, 1),
        "grade_a": a["risk_grade"],
        "grade_b": b["risk_grade"],
        "flags_a": a["total_flags"],
        "flags_b": b["total_flags"],
        "dimensions": {
            h_code: {
                "a_flags": dims_a.get(h_code, {}).get("flags", 0),
                "b_flags": dims_b.get(h_code, {}).get("flags", 0),
                "diff": dims_a.get(h_code, {}).get("flags", 0) - dims_b.get(h_code, {}).get("flags", 0),
            }
            for h_code in sorted(set(list(dims_a.keys()) + list(dims_b.keys())))
        },
    }
    with open(comparison_path, "w", encoding="utf-8") as f:
        json.dump(comparison_data, f, ensure_ascii=False, indent=2)
    print(f"\n[输出] 比对结果已保存至：{comparison_path}")


def main():
    engine = RuleEngine()
    builder = ReportBuilder()

    complaint_text = find_complaint_text(VAULT_ROOT)
    if complaint_text:
        print(f"[INFO] 已装载起诉状/上诉状文本，长度={len(complaint_text)}")
    else:
        print("[WARN] 未找到起诉状/上诉状文本，诉请边界检测将受限")

    os.makedirs(os.path.join(MCP_ROOT, "output"), exist_ok=True)

    total = len(DOCUMENTS)
    results = []

    for i, doc_info in enumerate(DOCUMENTS):
        r = run_single_detection(
            engine=engine,
            builder=builder,
            doc_info=doc_info,
            complaint_text=complaint_text,
            manifest_path=MANIFEST_PATH,
            vault_root=VAULT_ROOT,
        )
        results.append(r)

        progress = int((i + 1) / total * 100)
        if progress in PROGRESS_MILESTONES:
            print(f"\n{'*'*60}")
            print(f"[里程碑] 进度 {progress}% — 已完成 {i+1}/{total} 份文档检测")
            for prev_r in results:
                if prev_r.get("success"):
                    print(f"  {prev_r['name']}: 评分={prev_r['hallucination_score']:.1f}, "
                          f"等级={prev_r['risk_grade']}, 标记={prev_r['total_flags']}, "
                          f"输出={prev_r.get('filename', 'N/A')}")
            print(f"{'*'*60}")

    run_comparison(results)

    print(f"\n{'='*60}")
    print("[完成] 全部检测流程结束")
    print(f"{'='*60}")
    for r in results:
        if r.get("success"):
            print(f"  {r['name']}: {r['output_path']}")


if __name__ == "__main__":
    main()
