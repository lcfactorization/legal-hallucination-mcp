import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.claim_parser import ClaimParser
from legal_hallucination_mcp.law_citation_checker import LawCitationChecker
from legal_hallucination_mcp.law_knowledge_base import LawKnowledgeBase
from legal_hallucination_mcp.cross_reference_engine import CrossReferenceEngine
from legal_hallucination_mcp.web_verifier import WebVerifier
from legal_hallucination_mcp.report_builder import ReportBuilder
from legal_hallucination_mcp.workflow_orchestrator import WorkflowOrchestrator
from _paths import get_vault_root, get_manifest_path, get_law_dir

VAULT_ROOT = get_vault_root()
DOC_PATH = os.path.join(VAULT_ROOT, "V41_模拟二审判决书_苏06民终6271号劳动争议_20260527.md")
MANIFEST_PATH = get_manifest_path()
LAW_DIR = get_law_dir()

with open(DOC_PATH, "r", encoding="utf-8") as f:
    doc_text = f.read()

evidence_index = EvidenceIndex()
claim_parser = ClaimParser()
law_checker = LawCitationChecker()
law_kb = LawKnowledgeBase()
web_verifier = WebVerifier()
cross_ref_engine = CrossReferenceEngine(evidence_index, law_kb, web_verifier)

engine = RuleEngine(evidence_index, claim_parser, law_checker, law_kb, cross_ref_engine)

result = engine.run_full_scan(
    document_text=doc_text,
    manifest_path=MANIFEST_PATH,
    vault_root=VAULT_ROOT,
    local_law_dir=LAW_DIR if os.path.isdir(LAW_DIR) else "",
)

print(f"检测完成")
print(f"总标记数: {result.total_flags}")
print(f"幻觉评分: {result.hallucination_score:.1f}")
print(f"风险等级: {result.risk_grade} - {result.risk_description}")
print("=" * 80)

for dim in result.dimensions:
    print(f"\n{'='*60}")
    print(f"维度: {dim.dimension} (标题: {dim.dimension_title})")
    print(f"  标记数: {dim.total_flags}, 严重: {dim.critical_count}, 高: {dim.high_count}, 中: {dim.medium_count}, 低: {dim.low_count}")
    for i, f in enumerate(dim.rule_flags[:10], 1):
        print(f"  [{i}] 类型={f.sub_type or 'N/A'} | 严重度={f.severity}")
        print(f"      消息: {f.message[:120]}")
    if len(dim.rule_flags) > 10:
        print(f"  ... 还有 {len(dim.rule_flags) - 10} 项")

print(f"\n结构问题: {len(result.structure_issues)}")
for s in result.structure_issues:
    print(f"  - {s.heading}: {s.message}")

print(f"\n引注欺诈: {len(result.citation_frauds)}")
for c in result.citation_frauds:
    print(f"  - {c.citation[:80]} -> 匹配={c.matched}, 最近匹配={c.closest_match}")

print(f"\n诉请边界: {len(result.claim_violations)}")
for v in result.claim_violations:
    print(f"  - {v.judgment_item}: 判决{v.judgment_amount} vs 诉请{v.claim_max} ({v.violation_type})")

print(f"\n三段论断裂: {len(result.syllogism_breaks)}")
for s in result.syllogism_breaks:
    print(f"  - 行{s.line_number}: {s.break_type}")

print(f"\n主观修辞: {len(result.rhetoric_items)}")
for r in result.rhetoric_items[:10]:
    print(f"  - 行{r.line_number}: {r.rhetoric_word} ({r.category})")
if len(result.rhetoric_items) > 10:
    print(f"  ... 还有 {len(result.rhetoric_items) - 10} 项")

print(f"\n法条引用问题: {len(result.law_citation_issues)}")
for l in result.law_citation_issues:
    print(f"  - {l.citation_text[:60]}: 替代={l.is_replaced}, 本地匹配={l.local_match_found}")

print(f"\n事实来源问题: {len(result.fact_source_issues)}")
for f in result.fact_source_issues[:10]:
    print(f"  - 行{f.line_number}: {f.issue_type} ({f.h_code})")
if len(result.fact_source_issues) > 10:
    print(f"  ... 还有 {len(result.fact_source_issues) - 10} 项")

print(f"\n时效问题: {len(result.time_bar_issues)}")
for t in result.time_bar_issues:
    print(f"  - {t.issue_type}: {t.message[:80]}")

print(f"\n计算问题: {len(result.calculation_issues)}")
for c in result.calculation_issues[:10]:
    print(f"  - {c.issue_type}: {c.message[:80]}")
if len(result.calculation_issues) > 10:
    print(f"  ... 还有 {len(result.calculation_issues) - 10} 项")

output = {
    "document": "V41_模拟二审判决书_苏06民终6271号劳动争议_20260527",
    "total_flags": result.total_flags,
    "hallucination_score": result.hallucination_score,
    "risk_grade": result.risk_grade,
    "risk_description": result.risk_description,
    "dimensions": [
        {
            "name": d.dimension,
            "title": d.dimension_title,
            "total_flags": d.total_flags,
            "critical": d.critical_count,
            "high": d.high_count,
            "medium": d.medium_count,
            "low": d.low_count,
            "flags": [
                {"sub_type": f.sub_type, "severity": f.severity, "message": f.message[:200]}
                for f in d.rule_flags[:20]
            ],
        }
        for d in result.dimensions
    ],
    "structure_issues": [
        {"heading": s.heading, "message": s.message}
        for s in result.structure_issues
    ],
    "citation_frauds": [
        {"citation": c.citation[:100], "matched": c.matched, "closest": c.closest_match}
        for c in result.citation_frauds
    ],
    "claim_violations": [
        {"item": v.judgment_item, "judgment": v.judgment_amount, "claim_max": v.claim_max, "type": v.violation_type}
        for v in result.claim_violations
    ],
    "rhetoric_count": len(result.rhetoric_items),
    "law_citation_issues": [
        {"citation": l.citation_text[:80], "replaced": l.is_replaced, "local_found": l.local_match_found}
        for l in result.law_citation_issues
    ],
    "fact_source_count": len(result.fact_source_issues),
    "time_bar_issues": [
        {"type": t.issue_type, "message": t.message[:100]}
        for t in result.time_bar_issues
    ],
    "calculation_issues": [
        {"type": c.issue_type, "message": c.message[:100]}
        for c in result.calculation_issues
    ],
}

output_path = os.path.join(VAULT_ROOT, "legal-hallucination-mcp", "detection_result_v41.json")
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n检测结果已保存到: {output_path}")

workflow = WorkflowOrchestrator()
sections = {}
for heading in ["一、", "二、", "三、", "四、", "五、", "六、"]:
    idx = doc_text.find(f"## {heading}")
    if idx >= 0:
        next_idx = doc_text.find("\n## ", idx + 4)
        if next_idx < 0:
            next_idx = len(doc_text)
        key = heading.strip("、")
        sections[key] = doc_text[idx:next_idx]

sections["full_text"] = doc_text

run = workflow.create_workflow(
    document_name="V41_模拟二审判决书_苏06民终6271号",
    document_sections=sections,
    evidence_manifest_path=MANIFEST_PATH,
    vault_root=VAULT_ROOT,
)

print(f"\n工作流ID: {run.run_id}")
print(f"总任务数: {run.total_tasks}")
print(f"总令牌预算: {run.total_tokens_budget}")

parallel = workflow.get_parallel_tasks(run.run_id)
print(f"可并行任务: {len(parallel)}")
for t in parallel:
    print(f"  - {t['name']} (维度: {t['dimension_codes']}, 令牌预算: {t['token_budget']})")
