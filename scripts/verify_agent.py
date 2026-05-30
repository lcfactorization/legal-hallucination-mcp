"""法律文书自动化审计脚本 — MCP内置版本。

等价于工作区 scripts/verify_agent.py 的 AdvancedLegalHarness，
实现诉请边界审计和三段论完整性审计，输出 [AUDIT_PASSED] 或 [AUDIT_FAILED]。

可独立运行，也可通过 MCP 工具 verify_judgment_draft 调用。
"""

import json
import logging
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SRC_PATH = os.path.join(PROJECT_ROOT, "src")
if SRC_PATH not in sys.path:
    sys.path.insert(0, SRC_PATH)

from legal_hallucination_mcp.claim_parser import ClaimParser

logger = logging.getLogger("legal-hallucination.verify")


class LegalHarness:
    def __init__(self, vault_root: str = "", manifest_path: str = "", draft_path: str = ""):
        self.vault_root = vault_root or os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.manifest_path = manifest_path or os.path.join(self.vault_root, ".trae", "evidence_manifest.md")
        self.draft_path = draft_path or os.path.join(self.vault_root, "output", "judgment_draft.md")

        self.valid_filenames: set[str] = set()
        self.all_evidence_text = ""
        self.draft_text = ""
        self.claim_parser = ClaimParser()
        self.claim_limits: dict[str, float] = {}

    def load_and_compile(self) -> list[str]:
        warnings = []
        if not os.path.exists(self.manifest_path):
            return [f"未找到证据索引清单: {self.manifest_path}"]

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest_content = f.read()

        raw_paths = re.findall(r"`([^`]+)`", manifest_content)
        claim_files_content = ""

        for r_path in raw_paths:
            if os.path.isabs(r_path):
                full_path = os.path.normpath(r_path)
            else:
                full_path = os.path.normpath(os.path.join(self.vault_root, r_path))

            base_name = os.path.basename(full_path)
            self.valid_filenames.add(base_name)

            if os.path.exists(full_path):
                try:
                    with open(full_path, "r", encoding="utf-8") as ef:
                        file_text = ef.read()
                        self.all_evidence_text += f"\n--- FILE: {base_name} ---\n" + file_text
                        if any(k in base_name for k in ["变更", "诉讼请求", "起诉状", "上诉状"]):
                            claim_files_content += f"\n{file_text}\n"
                except Exception as e:
                    warnings.append(f"读取文件失败 {base_name}: {e}")
            else:
                warnings.append(f"清单登记文件暂不存在: {base_name}")

        self.claim_parser.parse(claim_files_content)
        self.claim_limits = self.claim_parser.claim_limits
        logger.info("load_and_compile: 诉请解析完成, claim_limits=%s", json.dumps(self.claim_limits, ensure_ascii=False))

        if os.path.exists(self.draft_path):
            with open(self.draft_path, "r", encoding="utf-8") as f:
                self.draft_text = f.read()
        else:
            warnings.append(f"未找到判决书草稿: {self.draft_path}")

        return warnings

    def verify_structure(self) -> list[str]:
        errors = []
        if not self.draft_text:
            return ["判决书草稿为空"]

        required_headings = [
            ("# 一、当事人的诉讼请求与主张", r"[#]+\s*一[、.．]\s*(?:当事人的)?(?:诉讼请求|诉辩|诉请|诉辩主张)"),
            ("# 二、本院查明事实", r"[#]+\s*二[、.．]\s*(?:本院)?(?:查明|认定)(?:的)?事实"),
            ("# 三、本院认为", r"[#]+\s*三[、.．]\s*(?:本院认为|争议焦点|裁判理由)"),
            ("# 四、判决如下", r"[#]+\s*四[、.．]\s*(?:判决|裁定|裁判)(?:如下|结果|主文)"),
        ]

        for heading, alt in required_headings:
            if heading not in self.draft_text and not re.search(alt, self.draft_text):
                errors.append(f"文书结构缺失：缺少必需段落标题「{heading}」")

        return errors

    def verify_citation_fraud(self) -> list[str]:
        errors = []
        if not self.draft_text:
            return errors

        citations = re.findall(r"[（\(]见《?([^》\)]+?)》?[）\)]", self.draft_text)
        for cite in citations:
            cite_clean = cite.strip()
            cite_normalized = re.sub(r"(证据\d+)", r"\1_", cite_clean)
            matched = False
            for f in self.valid_filenames:
                f_base = os.path.splitext(f)[0]
                if (cite_clean in f or f in cite_clean
                        or cite_clean in f_base or f_base in cite_clean
                        or cite_normalized in f_base or f_base in cite_normalized):
                    matched = True
                    break
            if not matched:
                errors.append(f"引注欺诈：文书中引用的证据源《{cite_clean}》不存在于证据索引清单中")

        return errors

    def verify_strict_scope_containment(self) -> list[str]:
        errors = []
        if not self.draft_text:
            return errors

        main_part_match = re.search(r"# 四、判决如下.*?(?=#|$)", self.draft_text, re.DOTALL)
        judgment_main = main_part_match.group(0) if main_part_match else self.draft_text

        logger.info("verify_strict_scope_containment: judgment_main_len=%d, claim_limits_cnt=%d",
                    len(judgment_main), len(self.claim_limits))

        violations = self.claim_parser.check_judgment_scope(judgment_main)

        for v in violations:
            if v.violation_type == "项目越权":
                errors.append(f"越权裁判：判决主文支持了未曾主张的项目「{v.judgment_item} {v.judgment_amount}元」，违反不告不理原则")
            elif v.violation_type == "金额冒顶":
                errors.append(
                    f"超诉请裁判：项目「{v.judgment_item}」判决支持 {v.judgment_amount}元，"
                    f"但诉状最高上限仅为 {v.claim_max}元"
                )

        logger.info("verify_strict_scope_containment: 检测完成, errors=%d", len(errors))
        return errors

    def verify_syllogism_complete_chain(self) -> list[str]:
        errors = []
        if not self.draft_text:
            return errors

        reasoning_match = re.search(r"# 三、本院认为.*?(?=# 四|# 五|$)", self.draft_text, re.DOTALL)
        if reasoning_match:
            lines = reasoning_match.group(0).split("\n")
            for i, line in enumerate(lines):
                line = line.strip()
                if any(k in line for k in ["应予支持", "不予支持", "应当支付", "确认"]) and len(line) > 10:
                    has_law = "\u300a" in line and "\u300b" in line
                    has_evidence = "见\u300a" in line or "证据" in line
                    if not has_law:
                        errors.append(f"三段论断裂：说理缺乏大前提（法律依据），行号 {i + 1}:「{line[:30]}...」")
                    if not has_evidence:
                        errors.append(f"三段论断裂：说理缺乏小前提（证据锚点），行号 {i + 1}:「{line[:30]}...」")

        return errors

    def verify_fact_source_binding(self) -> list[str]:
        errors = []
        if not self.draft_text:
            return errors

        fact_match = re.search(r"# 二、本院查明事实.*?(?=# 三|# 四|$)", self.draft_text, re.DOTALL)
        if fact_match:
            lines = fact_match.group(0).split("\n")
            for i, line in enumerate(lines):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if len(line) < 10:
                    continue
                has_citation = bool(re.search(r"[（\(]见", line))
                has_unsupported = "未见相关书证支持" in line
                if not has_citation and not has_unsupported and not line.startswith("|") and not line.startswith("-"):
                    if re.search(r"[\u4e00-\u9fff]{5,}", line):
                        errors.append(
                            f"事实缺乏证据绑定：第{i + 1}行的事实陈述无证据引注，"
                            f"违反封闭宇宙规则「{line[:30]}...」"
                        )

        return errors

    def run_all_checks(self) -> dict:
        warnings = self.load_and_compile()

        all_errors = []
        all_errors.extend(self.verify_structure())
        all_errors.extend(self.verify_citation_fraud())
        all_errors.extend(self.verify_strict_scope_containment())
        all_errors.extend(self.verify_syllogism_complete_chain())
        all_errors.extend(self.verify_fact_source_binding())

        passed = len(all_errors) == 0

        result = {
            "status": "AUDIT_PASSED" if passed else "AUDIT_FAILED",
            "passed": passed,
            "total_errors": len(all_errors),
            "errors": all_errors,
            "warnings": warnings,
            "claim_limits": {k: v for k, v in self.claim_limits.items()},
            "valid_evidence_count": len(self.valid_filenames),
        }

        return result


def main():
    vault_root = sys.argv[1] if len(sys.argv) > 1 else ""
    manifest_path = sys.argv[2] if len(sys.argv) > 2 else ""
    draft_path = sys.argv[3] if len(sys.argv) > 3 else ""

    harness = LegalHarness(vault_root=vault_root, manifest_path=manifest_path, draft_path=draft_path)
    result = harness.run_all_checks()

    print(json.dumps(result, ensure_ascii=False, indent=2))

    if result["passed"]:
        print("\n[AUDIT_PASSED] 当前文书通过全部审计检查")
        sys.exit(0)
    else:
        print(f"\n[AUDIT_FAILED] 共捕获 {result['total_errors']} 处违规")
        for i, err in enumerate(result["errors"], 1):
            print(f"  {i}. {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
