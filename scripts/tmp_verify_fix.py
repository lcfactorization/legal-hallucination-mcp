"""临时测试脚本：验证 _is_judicial_doc_verified 是否能找到本地法规库中的法释〔2025〕12号。"""
import sys, os, logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")

os.chdir(os.path.dirname(os.path.abspath(__file__)))

VAULT_ROOT = r"C:\Users\stere\Documents\Obsidian Vault"
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAW_DIR = os.path.join(_PROJECT_ROOT, "vault_mirror", "案件", "法律法规")
print(f"LAW_DIR={LAW_DIR}")
print(f"EXISTS={os.path.exists(LAW_DIR)}")

sys.path.insert(0, "src")
from legal_hallucination_mcp.rule_engine import RuleEngine
from legal_hallucination_mcp.evidence_index import EvidenceIndex

MANIFEST = os.path.join(VAULT_ROOT, ".trae", "evidence_manifest.md")

ei = EvidenceIndex(manifest_path=MANIFEST, vault_root=VAULT_ROOT)
ei.load()

engine = RuleEngine()
engine.evidence_index = ei

print(f"Before load: law_kb.articles={len(engine.law_kb.articles)}, checker.loaded={engine.law_checker.loaded}")

engine.law_checker.load_local_laws(LAW_DIR)
engine.law_kb.load_from_directory(LAW_DIR)

print(f"After load: law_kb.articles={len(engine.law_kb.articles)}, checker.loaded={engine.law_checker.loaded}")
print(f"local_law_texts keys (filtered): {[k for k in engine.law_checker.local_law_texts.keys() if '12' in k]}")
print(f"law_kb articles source_files (filtered): {[a.source_file for a in engine.law_kb.articles if '法释' in a.source_file][:5]}")

result = engine._is_judicial_doc_verified("法释〔2025〕12号")
print(f"is_verified=法释〔2025〕12号: {result}")

print("TEST: PASS" if result else "TEST: FAIL")