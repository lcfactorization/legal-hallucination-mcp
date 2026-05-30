import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.report_builder import ReportBuilder, generate_report_filename
from _paths import get_vault_root, get_manifest_path, get_output_dir

vault_root = get_vault_root()
doc_path = os.path.join(vault_root, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260528.md")
manifest_path = get_manifest_path()
output_dir = get_output_dir()

print(f"=== V42 修复后验证 ===")
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
        for flag in dim.rule_flags[:5]:
            print(f"    [{flag.severity}] {flag.sub_type}: {flag.evidence[:80]}")
        if dim.total_flags > 5:
            print(f"    ... and {dim.total_flags - 5} more")

h1_fraud = []
for dim in result.dimensions:
    if dim.dimension == "h1_sourceless_fabrication":
        for f in dim.rule_flags:
            if f.sub_type in ("引注欺诈", "类案案号杜撰"):
                h1_fraud.append((f.sub_type, f.evidence[:80]))

if h1_fraud:
    print(f"\n⚠️ 仍有H-1引注问题: {len(h1_fraud)}个")
    for sub, ev in h1_fraud:
        print(f"  - [{sub}] {ev}")
else:
    print("\n✅ H-1引注欺诈已清零!")

h2_dates = []
for dim in result.dimensions:
    if dim.dimension == "h2_law_misapplication":
        for f in dim.rule_flags:
            if f.sub_type == "程序日期杜撰":
                h2_dates.append(f.evidence[:80])

if h2_dates:
    print(f"\n⚠️ 仍有程序日期杜撰: {len(h2_dates)}个")
    for ev in h2_dates:
        print(f"  - {ev}")
else:
    print("\n✅ 程序日期杜撰已清零!")

builder = ReportBuilder()
report = builder.build_report(result)
report_filename = generate_report_filename(
    agent_name="TraeCN",
    llm_name="GLM51",
    content_summary="V42判决书幻觉检测报告",
    version="v2.0",
)
report_path = os.path.join(output_dir, report_filename)
with open(report_path, "w", encoding="utf-8") as f:
    f.write(report)
print(f"\n📄 报告已保存: {report_path}")
print(f"📁 报告所在文件夹: {os.path.abspath(output_dir)}")
