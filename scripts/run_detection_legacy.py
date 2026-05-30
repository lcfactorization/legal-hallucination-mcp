import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.claim_parser import ClaimParser
from legal_hallucination_mcp.law_citation_checker import LawCitationChecker
from legal_hallucination_mcp.report_builder import ReportBuilder
from _paths import get_vault_root, get_manifest_path, get_output_dir

vault_root = get_vault_root()
doc_paths = [
    os.path.join(vault_root, 'V40_模拟二审判决书_苏06民终6271号劳动争议_20260525.md'),
    os.path.join(vault_root, 'V42_模拟二审判决书_苏06民终6271号劳动争议_20260528.md'),
]

manifest_path = get_manifest_path()
output_dir = get_output_dir()


def _score_complaint_candidate(fp: str, text: str, vault_root: str) -> tuple[int, ...]:
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
    candidates = []
    for root, dirs, files in os.walk(vault_root):
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


complaint_text = find_complaint_text(vault_root)
if complaint_text:
    print(f"[INFO] 已装载起诉状/上诉状文本，长度={len(complaint_text)} 字符")
else:
    print("[WARN] 未找到起诉状/上诉状，诉请对比将不可用")

for doc_path in doc_paths:
    basename = os.path.basename(doc_path)
    print(f'=== {basename} ===')

    with open(doc_path, 'r', encoding='utf-8') as f:
        doc_text = f.read()

    engine = RuleEngine(EvidenceIndex(), ClaimParser(), LawCitationChecker())
    result = engine.run_full_scan(
        document_text=doc_text,
        complaint_text=complaint_text,
        manifest_path=manifest_path,
        vault_root=vault_root,
    )

    print(f'Score: {result.hallucination_score:.1f}, Grade: {result.risk_grade}, Flags: {result.total_flags}')
    for dim in result.dimensions:
        print(f'  {dim.h_code} {dim.dimension_title}: {dim.total_flags} flags')

    builder = ReportBuilder()
    report = builder.build_report(result)

    if 'V40' in basename:
        report_name = 'V40_hallucination_report.md'
    else:
        report_name = 'V42_hallucination_report.md'

    report_path = os.path.join(output_dir, report_name)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f'Saved: {report_path}')
    print()
