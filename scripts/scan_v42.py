import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.claim_parser import ClaimParser
from legal_hallucination_mcp.law_citation_checker import LawCitationChecker
from legal_hallucination_mcp.report_builder import ReportBuilder
from _paths import get_vault_root, get_manifest_path, get_output_dir

vault_root = get_vault_root()
doc_path = os.path.join(vault_root, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260527.md")
manifest_path = get_manifest_path()
output_dir = get_output_dir()

print(f"=== {os.path.basename(doc_path)} ===")
start = time.time()

with open(doc_path, "r", encoding="utf-8") as f:
    doc_text = f.read()

ei = EvidenceIndex(manifest_path=manifest_path, vault_root=vault_root)
ei.load()

engine = RuleEngine(evidence_index=ei)
result = engine.run_full_scan(document_text=doc_text)

elapsed = time.time() - start
print(f"Score: {result.hallucination_score:.1f}, Grade: {result.risk_grade}, Flags: {result.total_flags}, Time: {elapsed:.2f}s")
for dim in result.dimensions:
    if dim.total_flags > 0:
        print(f"  {dim.h_code} {dim.dimension_title}: {dim.total_flags} flags")
        for flag in dim.rule_flags[:3]:
            print(f"    [{flag.severity}] {flag.sub_type}: {flag.evidence[:80]}")
        if dim.total_flags > 3:
            print(f"    ... and {dim.total_flags - 3} more")

builder = ReportBuilder()
report = builder.build_report(result)

report_path = os.path.join(output_dir, "V42_hallucination_report.md")
with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)
print(f"\nSaved: {report_path}")

h1_fabricated = []
for dim in result.dimensions:
    if dim.dimension == "h1_sourceless_fabrication":
        for f in dim.rule_flags:
            if f.sub_type == "类案案号杜撰":
                h1_fabricated.append(f.evidence[:80])

if h1_fabricated:
    print(f"\n⚠️ 仍有类案案号杜撰误报: {len(h1_fabricated)}个")
    for e in h1_fabricated:
        print(f"  - {e}")
else:
    print("\n✅ 类案案号杜撰误报已清零!")
