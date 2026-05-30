import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from _paths import get_vault_root, get_manifest_path, get_output_dir

vault_root = get_vault_root()
doc_path = os.path.join(vault_root, "V40_模拟二审判决书_苏06民终6271号劳动争议_20260525.md")
manifest_path = get_manifest_path()
output_dir = get_output_dir()

with open(doc_path, "r", encoding="utf-8") as f:
    doc_text = f.read()

ei = EvidenceIndex(manifest_path=manifest_path, vault_root=vault_root)
ei.load()
engine = RuleEngine(evidence_index=ei)
result = engine.run_full_scan(document_text=doc_text)

h2_flags = []
for dim in result.dimensions:
    if dim.dimension == "h2_law_misapplication":
        for f in dim.rule_flags:
            h2_flags.append({
                "sub_type": f.sub_type,
                "severity": f.severity,
                "evidence": f.evidence[:150],
                "line_number": f.line_number,
                "message": f.message,
            })

with open(os.path.join(output_dir, "v40_h2_flags.json"), "w", encoding="utf-8") as f:
    json.dump(h2_flags, f, ensure_ascii=False, indent=2)

print(f"Total H-2 flags: {len(h2_flags)}")
for i, f in enumerate(h2_flags):
    print(f'{i+1}. [{f["severity"]}] {f["sub_type"]} @ L{f["line_number"]}: {f["evidence"][:80]}')
