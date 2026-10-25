"""文书段落提取器 — 从裁判文书全文提取核心段落。

原 server._extract_sections 的共享实现（v0.4.0 起），
供 MCP 工具层与规则引擎（H-7/H-8 一致性检查）复用，
避免 server -> rule_engine 的循环导入。
"""

import re


def extract_sections(document_text: str) -> dict:
    sections = {}

    plaintiff = re.search(
        r"(?:原告|公诉机关|申请人|上诉人).{0,10}(?:诉称|指控|称)[：:]\s*(.*?)(?=\n\n|被告.{0,10}辩称|被上诉人)",
        document_text, re.DOTALL,
    )
    sections["plaintiff_claim"] = plaintiff.group(1).strip() if plaintiff else ""

    defendant = re.search(
        r"(?:被告|被申请人|被上诉人).{0,10}辩称[：:]\s*(.*?)(?=\n\n|本院查明|经审理)",
        document_text, re.DOTALL,
    )
    sections["defendant_defense"] = defendant.group(1).strip() if defendant else ""

    court_finding = re.search(
        r"(?:本院查明|经审理查明|经审理认定)[：:]\s*(.*?)(?=\n\n|上述事实|证据如下|本院认为)",
        document_text, re.DOTALL,
    )
    sections["court_finding"] = court_finding.group(1).strip() if court_finding else ""

    evidence = re.search(
        r"(?:上述事实|证据如下|有下列证据)[，：:]\s*(.*?)(?=\n\n|本院认为|判决如下)",
        document_text, re.DOTALL,
    )
    sections["evidence_analysis"] = evidence.group(1).strip() if evidence else ""

    reasoning = re.search(
        r"(?:# 三、本院认为|本院认为)[，：:]\s*(.*?)(?=依照|# 四|判决如下|裁定如下)",
        document_text, re.DOTALL,
    )
    sections["reasoning"] = reasoning.group(1).strip() if reasoning else ""

    judgment_main = re.search(
        r"(?:# 四、判决如下|判决如下)[：:]\s*(.*)",
        document_text, re.DOTALL,
    )
    sections["judgment_main"] = judgment_main.group(1).strip() if judgment_main else ""

    case_info = {}
    case_num = re.search(r"[(（]([\d年]+[^)）]+)[)）]", document_text[:500])
    if case_num:
        case_info["case_number"] = case_num.group(1)
    court_match = re.search(r"([\u4e00-\u9fff]+人民法院|[\u4e00-\u9fff]+仲裁委员会)", document_text[:500])
    if court_match:
        case_info["court"] = court_match.group(1)
    sections["case_info"] = case_info

    filled = sum(
        1 for v in sections.values()
        if v and (isinstance(v, str) and len(v) > 10 or isinstance(v, dict) and v)
    )
    sections["extraction_confidence"] = round(filled / 6, 2) if filled else 0.0

    return sections
