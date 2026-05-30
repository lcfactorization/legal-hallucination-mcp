# ruff: noqa: E501
"""MCP 服务器 v0.1.0 — 法律文书幻觉检测桥接架构。

本 MCP 服务器是 AI Agent 与幻觉检测技能之间的桥接层。
服务器自身不调用任何 LLM API，仅负责：
  1. 运行规则引擎执行确定性幻觉检测（正则表达式 + 数值比对）
  2. 加载并渲染技能模板 → 返回提示词供Agent发送给自身LLM
  3. 解析Agent返回的LLM响应 → 返回结构化幻觉数据
  4. 计算幻觉风险评分并生成检测报告
  5. 管理证据索引、诉请解析和法条引用校验

Agent 自行决定调用顺序和参数。
Agent 使用自身LLM处理服务器返回的提示词。
"""

import json
import logging
import os
import re
import threading
import time

from mcp.server.fastmcp import FastMCP

from .claim_parser import ClaimParser
from .config import (
    DIMENSION_ORDER,
    DIMENSION_TITLES,
    DIMENSION_WEIGHTS,
    ErrorCode,
    make_error,
)
from .cross_reference_engine import CrossReferenceEngine
from .evidence_index import EvidenceIndex
from .law_citation_checker import LawCitationChecker
from .law_knowledge_base import LawKnowledgeBase
from .report_builder import ReportBuilder, generate_report_filename
from .response_parser import ResponseParser
from .rule_engine import RuleEngine
from .skill_runner import SkillLoader, TemplateRenderer, build_system_prompt
from .vector_index import VectorIndex
from .web_verifier import WebVerifier
from .workflow_orchestrator import TaskStatus, WorkflowOrchestrator

logger = logging.getLogger("legal-hallucination")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

mcp = FastMCP("legal-hallucination")

_state_lock = threading.Lock()

_evidence_index = EvidenceIndex()
_claim_parser = ClaimParser()
_law_checker = LawCitationChecker()
_law_kb = LawKnowledgeBase()
_web_verifier = WebVerifier()
_vector_index = VectorIndex()
_cross_ref_engine = CrossReferenceEngine(_evidence_index, _law_kb, _web_verifier, _vector_index)
_workflow_orchestrator = WorkflowOrchestrator()
_rule_engine = RuleEngine(
    _evidence_index, _claim_parser, _law_checker,
    _law_kb, _cross_ref_engine, _web_verifier, _vector_index,
)
_parser = ResponseParser()
_builder = ReportBuilder()
_loader = SkillLoader()
_renderer = TemplateRenderer(_loader)


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
    non_cjk = len(text) - cjk
    return int(cjk / 1.5 + non_cjk / 4.0)


def _build_fallback_prompt(dimension: str) -> str:
    from .config import DIMENSION_TITLES
    title = DIMENSION_TITLES.get(dimension, dimension)
    return (
        f"# {title}幻觉检测\n\n"
        f"请检测裁判文书中【{title}】维度的幻觉问题。\n\n"
        f"## 检测要求\n\n"
        f"1. 逐项检查文书中是否存在{title}相关的幻觉\n"
        f"2. 每个检测到的幻觉项必须引用文书原文\n"
        f"3. 输出格式为严格的JSON对象\n\n"
        f"## 输出格式\n\n"
        f"```json\n"
        f'{{"hallucination_items": [{{"item_name": "", '
        f'"description": "", "severity": "medium", '
        f'"evidence": "", "legal_basis": "", '
        f'"suggestion": ""}}], "score": 100, "reasoning": ""}}\n'
        f"```\n"
    )


def _extract_sections(document_text: str) -> dict:
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


# ── MCP Resources ──────────────────────────────────────────────


@mcp.resource("hallucination://dimensions")
def get_dimensions_resource() -> str:
    dims = _loader.list_dimensions()
    return json.dumps(dims, ensure_ascii=False, indent=2) if dims else json.dumps(
        [{"name": k, "title": v, "weight": DIMENSION_WEIGHTS.get(k, 0)}
         for k, v in DIMENSION_TITLES.items()], ensure_ascii=False, indent=2
    )


@mcp.resource("hallucination://weights")
def get_weights_resource() -> str:
    return json.dumps(DIMENSION_WEIGHTS, ensure_ascii=False, indent=2)


@mcp.resource("hallucination://rule-patterns")
def get_rule_patterns_resource() -> str:
    from .config import RULE_ENGINE_PATTERNS, STRUCTURE_CHECK_RULES
    return json.dumps({
        "rule_patterns": {
            k: {kk: vv for kk, vv in v.items() if kk != "pattern"}
            for k, v in RULE_ENGINE_PATTERNS.items()
        },
        "structure_rules": STRUCTURE_CHECK_RULES,
    }, ensure_ascii=False, indent=2)


# ── MCP Tools ──────────────────────────────────────────────────


@mcp.tool()
def list_dimensions() -> str:
    """列出所有可用的幻觉检测维度及其元数据。

    返回 JSON 字符串，包含所有维度的元数据列表和风险等级信息。
    """
    try:
        dims = _loader.list_dimensions()
        if not dims:
            dims = [
                {"name": k, "title": v, "weight": DIMENSION_WEIGHTS.get(k, 0), "order": DIMENSION_ORDER.get(k, 0)}
                for k, v in DIMENSION_TITLES.items()
            ]
        return json.dumps({"success": True, "total": len(dims), "dimensions": dims}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("list_dimensions: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"列出维度异常：{e}", retryable=True)


@mcp.tool()
def extract_document_sections(document_full_text: str) -> str:
    """从裁判文书全文中提取各核心段落，供后续检测使用。

    document_full_text: 裁判文书全文
    """
    try:
        sections = _extract_sections(document_full_text)
        return json.dumps({"success": True, **sections}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("extract_document_sections: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"提取异常：{e}", retryable=True)


@mcp.tool()
def load_evidence_manifest(
    manifest_path: str,
    vault_root: str = "",
) -> str:
    """装载证据索引清单，构建有效证据文件名集合。

    manifest_path: evidence_manifest.md 的绝对路径
    vault_root: 工作区根目录（用于解析相对路径）
    """
    try:
        logger.info(
            "[LOCK] load_evidence_manifest: acquiring _state_lock, "
            "manifest_path=%s, vault_root=%s, thread=%s",
            manifest_path, vault_root, threading.current_thread().name,
        )
        with _state_lock:
            logger.info(
                "[LOCK] load_evidence_manifest: lock acquired, thread=%s",
                threading.current_thread().name,
            )
            result = _evidence_index.load(manifest_path, vault_root)
            _rule_engine.evidence_index = _evidence_index
            logger.info(
                "[LOCK] load_evidence_manifest: state updated, loaded=%d, thread=%s",
                result.get("loaded", 0), threading.current_thread().name,
            )
        logger.info(
            "[LOCK] load_evidence_manifest: lock released, thread=%s",
            threading.current_thread().name,
        )
        return json.dumps({"success": True, **result}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("load_evidence_manifest: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"装载异常：{e}")


@mcp.tool()
def check_citation_fraud(document_text: str) -> str:
    """检测文书中的引注欺诈——引用的证据源不在证据清单中。

    前提：需先调用 load_evidence_manifest 装载证据索引。

    document_text: 裁判文书全文
    """
    try:
        frauds = _rule_engine.check_citation_fraud(document_text)
        items = [f.model_dump() for f in frauds]
        return json.dumps({
            "success": True,
            "total_frauds": len(frauds),
            "frauds": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_citation_fraud: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def check_claim_boundary(
    complaint_text: str,
    judgment_main_text: str,
) -> str:
    """检测判决主文是否超出诉请边界（项目越权/金额冒顶）。

    complaint_text: 起诉状/上诉状全文
    judgment_main_text: 判决主文部分（"四、判决如下"之后的内容）
    """
    try:
        _claim_parser.parse(complaint_text)
        violations = _claim_parser.check_judgment_scope(judgment_main_text)
        items = [v.model_dump() for v in violations]
        claim_limits = _claim_parser.get_claim_limits_json()
        return json.dumps({
            "success": True,
            "claim_limits": claim_limits,
            "total_violations": len(violations),
            "violations": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_claim_boundary: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def check_syllogism(document_text: str) -> str:
    """检测说理部分的三段论完整性（大前提法律依据+小前提证据锚点）。

    document_text: 裁判文书全文
    """
    try:
        breaks = _rule_engine.check_syllogism(document_text)
        items = [b.model_dump() for b in breaks]
        return json.dumps({
            "success": True,
            "total_breaks": len(breaks),
            "breaks": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_syllogism: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def check_structure(document_text: str) -> str:
    """检测文书是否包含四个必需的标准段落标题。

    document_text: 裁判文书全文
    """
    try:
        issues = _rule_engine.check_structure(document_text)
        items = [i.model_dump() for i in issues]
        return json.dumps({
            "success": True,
            "total_issues": len(issues),
            "issues": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_structure: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def check_subjective_rhetoric(document_text: str) -> str:
    """检测文书中的主观修辞和情感化语言。

    document_text: 裁判文书全文
    """
    try:
        items = _rule_engine.check_subjective_rhetoric(document_text)
        results = [r.model_dump() for r in items]
        non_exception_count = sum(1 for r in items if not r.is_exception)
        exception_count = sum(1 for r in items if r.is_exception)
        return json.dumps({
            "success": True,
            "total_items": len(items),
            "non_exception_count": non_exception_count,
            "exception_count": exception_count,
            "items": results,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_subjective_rhetoric: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def check_law_citations(
    document_text: str,
    local_law_dir: str = "",
) -> str:
    """检测法条引用问题（已废止法律、格式不规范）。

    document_text: 裁判文书全文
    local_law_dir: 本地法律法规库目录路径（可选）
    """
    try:
        if local_law_dir:
            logger.info(
                "[LOCK] check_law_citations: acquiring _state_lock for law_checker.load, "
                "dir=%s, thread=%s",
                local_law_dir, threading.current_thread().name,
            )
            with _state_lock:
                logger.info(
                    "[LOCK] check_law_citations: lock acquired, thread=%s",
                    threading.current_thread().name,
                )
                _law_checker.load_local_laws(local_law_dir)
                logger.info(
                    "[LOCK] check_law_citations: law_checker loaded, thread=%s",
                    threading.current_thread().name,
                )
            logger.info(
                "[LOCK] check_law_citations: lock released, thread=%s",
                threading.current_thread().name,
            )
        issues = _rule_engine.check_law_citations(document_text)
        items = [i.model_dump() for i in issues]
        return json.dumps({
            "success": True,
            "total_issues": len(issues),
            "issues": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("check_law_citations: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检测异常：{e}")


@mcp.tool()
def run_rule_engine_full(
    document_text: str,
    manifest_path: str = "",
    complaint_text: str = "",
    amended_text: str = "",
    vault_root: str = "",
    local_law_dir: str = "",
) -> str:
    """规则引擎全量扫描——六维幻觉检测（纯规则，零LLM，即时返回）。

    document_text: 裁判文书全文
    manifest_path: evidence_manifest.md 路径（可选，用于引注欺诈检测）
    complaint_text: 起诉状/上诉状全文（可选，用于诉请边界检测）
    amended_text: 变更诉求申请书全文（可选，用于更新诉请上限）
    vault_root: 工作区根目录（可选）
    local_law_dir: 本地法律法规库目录（可选）
    """
    try:
        result = _rule_engine.run_full_scan(
            document_text=document_text,
            complaint_text=complaint_text,
            amended_text=amended_text,
            manifest_path=manifest_path,
            vault_root=vault_root,
            local_law_dir=local_law_dir,
        )
        result.report_markdown = _builder.build_report(result)
        return json.dumps({
            "success": True,
            "total_flags": result.total_flags,
            "hallucination_score": result.hallucination_score,
            "risk_grade": result.risk_grade,
            "risk_description": result.risk_description,
            "dimensions": [
                {
                    "dimension": d.dimension,
                    "title": d.dimension_title,
                    "h_code": d.h_code,
                    "total_flags": d.total_flags,
                    "critical": d.critical_count,
                    "high": d.high_count,
                    "medium": d.medium_count,
                    "low": d.low_count,
                }
                for d in result.dimensions
            ],
            "structure_issues": len(result.structure_issues),
            "citation_frauds": len(result.citation_frauds),
            "claim_violations": len(result.claim_violations),
            "syllogism_breaks": len(result.syllogism_breaks),
            "rhetoric_items": len(result.rhetoric_items),
            "law_citation_issues": len(result.law_citation_issues),
            "report_markdown": result.report_markdown,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("run_rule_engine_full: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"全量扫描异常：{e}", retryable=True)


@mcp.tool()
def render_dimension_prompt(
    dimension: str,
    sections: dict | None = None,
    include_anchors: bool = True,
    anchor_count: int = 3,
) -> str:
    """渲染指定维度的检测 Prompt 模板，供 AI Agent 发送给自己的 LLM。

    dimension: 维度标识（如 'h1_sourceless_fabrication'）
    sections: 预处理后的文书段落字典
    include_anchors: 是否包含锚定示例
    anchor_count: 锚定示例数量
    """
    try:
        skill_name = f"dimensions/{dimension}"
        try:
            meta, body = _loader.load(skill_name)
        except FileNotFoundError:
            from .config import DIMENSION_TITLES, DIMENSION_WEIGHTS
            meta = type('SkillMeta', (), {
                'name': dimension,
                'title': DIMENSION_TITLES.get(dimension, dimension),
                'weight': DIMENSION_WEIGHTS.get(dimension, 0.0),
                'full_score': 100,
            })()
            body = _build_fallback_prompt(dimension)

        rendered = _renderer.render(body, sections or {})
        system_prompt = build_system_prompt(meta)

        anchors = []
        if include_anchors:
            anchors = _loader.load_anchors(dimension)[:anchor_count]

        total_chars = len(system_prompt) + len(rendered)

        output_schema = {
            "type": "object",
            "properties": {
                "hallucination_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "description": {"type": "string"},
                            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                            "evidence": {"type": "string"},
                            "legal_basis": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                    },
                },
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
                "reasoning": {"type": "string"},
            },
            "required": ["hallucination_items", "score", "reasoning"],
        }

        return json.dumps({
            "success": True,
            "dimension": dimension,
            "dimension_title": getattr(meta, 'title', ''),
            "weight": getattr(meta, 'weight', 0.0),
            "full_score": getattr(meta, 'full_score', 100),
            "system_prompt": system_prompt,
            "user_prompt": rendered,
            "anchor_examples": anchors,
            "output_schema": output_schema,
            "token_estimate": _estimate_tokens(" " * total_chars),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("render_dimension_prompt: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"渲染异常：{e}")


@mcp.tool()
def parse_hallucination_result(
    dimension: str,
    llm_response: str,
) -> str:
    """解析 LLM 返回的幻觉检测结果为结构化数据。

    dimension: 维度标识
    llm_response: LLM 返回的文本
    """
    try:
        flags = _parser.parse_hallucination_result(dimension, llm_response)
        items = [f.model_dump() for f in flags]
        return json.dumps({
            "success": True,
            "dimension": dimension,
            "total_flags": len(flags),
            "flags": items,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("parse_hallucination_result: %s", e, exc_info=True)
        return make_error(ErrorCode.PARSE_FAILED, f"解析异常：{e}")


@mcp.tool()
def calculate_hallucination_score(
    rule_results_json: str,
    semantic_results_json: str = "[]",
) -> str:
    """计算幻觉风险综合评分。

    rule_results_json: 规则引擎结果 JSON（来自 run_rule_engine_full 的返回值）
    semantic_results_json: 语义检测结果 JSON 数组（来自 parse_hallucination_result 的返回值）
    """
    try:
        rule_data = json.loads(rule_results_json)
        semantic_data = json.loads(semantic_results_json) if semantic_results_json else []

        base_score = rule_data.get("hallucination_score", 0.0)
        _risk_grade = rule_data.get("risk_grade", "A")

        semantic_penalty = 0.0
        for sem in semantic_data:
            flags = sem.get("flags", [])
            for flag in flags:
                sev = flag.get("severity", "medium")
                weight_map = {"critical": 5.0, "high": 3.0, "medium": 1.5, "low": 0.5}
                semantic_penalty += weight_map.get(sev, 1.0)

        total_score = min(base_score + semantic_penalty, 100.0)

        from .config import RISK_GRADES
        final_grade = "F"
        final_desc = RISK_GRADES["F"][2]
        for grade, (low, high, desc) in RISK_GRADES.items():
            if low <= total_score < high:
                final_grade = grade
                final_desc = desc
                break

        return json.dumps({
            "success": True,
            "rule_score": base_score,
            "semantic_penalty": semantic_penalty,
            "total_score": round(total_score, 1),
            "risk_grade": final_grade,
            "risk_description": final_desc,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("calculate_hallucination_score: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"计算异常：{e}")


@mcp.tool()
def estimate_tokens(
    skill_name: str | None = None,
    materials_chars: int = 0,
) -> str:
    """预估 Skill 的 token 用量。

    skill_name: Skill 名称
    materials_chars: 案件材料字符数
    """
    try:
        breakdown = {
            "system_prompt": 0,
            "user_prompt_template": 0,
            "materials": _estimate_tokens(" " * materials_chars) if materials_chars else 0,
        }

        if skill_name:
            try:
                meta, body = _loader.load(f"dimensions/{skill_name}")
                sys_prompt = build_system_prompt(meta)
                breakdown["system_prompt"] = _estimate_tokens(sys_prompt)
                breakdown["user_prompt_template"] = _estimate_tokens(body)
            except FileNotFoundError:
                pass

        total_tokens = sum(v for v in breakdown.values() if isinstance(v, int))

        return json.dumps({
            "estimated_tokens": total_tokens,
            "breakdown": breakdown,
            "fits_in_128k": total_tokens < 120_000,
            "fits_in_200k": total_tokens < 190_000,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("estimate_tokens: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"估算异常：{e}")


def main():
    mcp.run()


@mcp.tool()
def verify_judgment_draft(
    vault_root: str = "",
    manifest_path: str = "",
    draft_path: str = "",
) -> str:
    """对判决书草稿执行自动化审计，等价于 verify_agent.py 的 AdvancedLegalHarness。

    审计内容包括：
    1. 文书结构检测：是否包含四个必需段落标题
    2. 引注欺诈检测：证据引注是否在证据索引清单中
    3. 诉请边界审计：判决金额是否超出诉请上限
    4. 三段论完整性审计：说理部分是否同时包含法律依据和证据锚点
    5. 事实来源绑定检测：查明事实部分每句是否标注证据来源

    输出 [AUDIT_PASSED] 或 [AUDIT_FAILED]。

    vault_root: 工作区根目录（默认自动检测）
    manifest_path: 证据索引清单路径（默认 vault_root/.trae/evidence_manifest.md）
    draft_path: 判决书草稿路径（默认 vault_root/output/judgment_draft.md）
    """
    try:
        from .verify_agent import LegalHarness
        harness = LegalHarness(vault_root=vault_root, manifest_path=manifest_path, draft_path=draft_path)
        result = harness.run_all_checks()
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("verify_judgment_draft: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"审计异常：{e}")


@mcp.tool()
def get_closed_universe_prompt(  # noqa: E501
    manifest_path: str = "",
    vault_root: str = "",
) -> str:
    """获取封闭宇宙规则提示词，强制大模型生成的每一句法律事实必须源自证据索引清单。

    返回的提示词可直接注入到 Agent 的系统提示中，确保：
    - 每句法律事实必须完全源自 evidence_manifest.md 中的证据文件
    - 绝对禁止常识性推导、主观脑补或艺术加工
    - 缺乏证据的事实必须标注"上诉人主张...，但截至本操作时未见相关书证支持"
    - 禁止盲写条文，所有法条引用必须匹配工作区法律法规库或权威网站

    manifest_path: 证据索引清单路径
    vault_root: 工作区根目录
    """
    try:
        actual_manifest = manifest_path

        if actual_manifest and os.path.exists(actual_manifest):
            with open(actual_manifest, encoding="utf-8") as f:
                manifest_content = f.read()
        else:
            manifest_content = "（未指定证据索引清单路径，请提供 manifest_path 参数）"

        evidence_list = re.findall(r"`([^`]+)`", manifest_content)
        evidence_names = [os.path.basename(p) for p in evidence_list]

        prompt = f"""# 封闭宇宙规则（Closed-Universe Rule）

## 核心原则

大模型生成的每一句法律事实，必须完全源自证据索引清单（evidence_manifest.md）中所列出并存在于工作区中的真实文件。绝对禁止任何常识性推导、主观脑补或艺术加工。

## 当前案件证据索引

以下为证据索引清单中登记的全部有效证据文件：

{chr(10).join(f'- {name}' for name in evidence_names) if evidence_names else '（清单为空或未装载）'}

共 {len(evidence_names)} 项证据。

## 强制规则

1. **证据与事实绑定**：生成的每一句法律事实，必须在句末以括号形式强制标注证据来源。格式规范为：（见《核心证据文件名.md》）。若某一事实缺乏直接证据或存在争议，必须如实表述为："上诉人主张...，但截至本操作时未见相关书证支持。"

2. **禁止盲写条文**：严禁凭记忆引用法条、案例号。所有引用的法律法规必须匹配工作区中的法律法规文件夹，或通过网络检索自权威网站中的真实数据。

3. **诉审一致原则**：判决主文所支持的金额和项目，必须严格限制在起诉状、上诉状、变更诉求申请书等原始文件界定的范畴内。绝对禁止超诉请裁判。

4. **中立与公平**：评价用词必须保持司法审判的机械性、严谨性，禁止使用"极其恶劣""明目张胆"等主观带有强烈感情色彩的修辞。

5. **强制文书结构**：生成的判决书草稿必须严格包含且仅包含以下四个标准 Markdown 一级标题：
   - # 一、当事人的诉讼请求与主张
   - # 二、本院查明事实
   - # 三、本院认为（说理部分）
   - # 四、判决如下（判决主文）

6. **零幻觉底线**：无法验证的内容，宁可标注"待验证"或删除，绝不编造。宁可文书简短，不可凭空增补。
"""
        return prompt
    except Exception as e:
        logger.error("get_closed_universe_prompt: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"生成提示词异常：{e}")


@mcp.tool()
def get_judgment_draft_template(
    case_type: str = "劳动争议",
    manifest_path: str = "",
    vault_root: str = "",
) -> str:
    """获取判决书草稿模板，包含强制四段式结构和证据绑定占位符。

    模板严格遵循封闭宇宙规则，每个段落都包含证据绑定提示，
    确保大模型生成时不会遗漏必需的结构和证据引注。

    case_type: 案件类型（如"劳动争议""合同纠纷""侵权纠纷"）
    manifest_path: 证据索引清单路径（用于在模板中列出可用证据）
    vault_root: 工作区根目录
    """
    try:
        evidence_names = []
        if manifest_path and os.path.exists(manifest_path):
            with open(manifest_path, encoding="utf-8") as f:
                manifest_content = f.read()
            evidence_list = re.findall(r"`([^`]+)`", manifest_content)
            evidence_names = [os.path.basename(p) for p in evidence_list]

        evidence_block = ""
        if evidence_names:
            evidence_block = f"""
### 可用证据清单（仅限引用以下证据）

{chr(10).join(f'- {name}' for name in evidence_names)}

> 注意：仅可引用以上证据，不得引用任何未列入清单的材料。
"""

        template = f"""# 判决书草稿模板（{case_type}）

> 本模板严格遵循封闭宇宙规则和强制文书结构协议。
> 每个段落都包含证据绑定提示，确保生成内容有据可依。
> 生成后请运行 verify_judgment_draft 工具进行自动化审计。
{evidence_block}
---

# 一、当事人的诉讼请求与主张

> 本部分必须完整、不遗漏地列出原告/上诉人的具体诉请项目及金额上限。
> 金额数字必须与起诉状/上诉状/变更诉求申请书完全一致，不得编造或修改。

## 上诉人（原审原告）诉请

1. [诉请项目1]：[金额]元
2. [诉请项目2]：[金额]元
...

## 被上诉人（原审被告）答辩

[被上诉人的答辩意见]

---

# 二、本院查明事实

> 本部分每一行事实确认，句末必须强制括号标注来源，格式如：（见《文件名.md》）
> 若某一事实缺乏直接证据，必须标注："上诉人主张...，但截至本操作时未见相关书证支持。"
> 绝对禁止无证据来源的事实陈述。

[事实1]（见《证据XX_证据名称.md》）

[事实2]（见《证据XX_证据名称.md》）

[上诉人主张XXX，但截至本操作时未见相关书证支持。]

---

# 三、本院认为（说理部分）

> 本部分每一项支持或驳回结论，必须在同一段落内同时包含：
> - 大前提：适用的法律依据《XX法》第X条
> - 小前提：支撑的事实见《XX证据.md》
> 缺少任一要素即为三段论断裂。

关于[争议焦点1]：

根据《[法律名称]》第[条号]条的规定，[法律要件]。本案中，[事实认定]（见《证据XX.md》），故[裁判结论]。

关于[争议焦点2]：

根据《[法律名称]》第[条号]条的规定，[法律要件]。本案中，[事实认定]（见《证据XX.md》），故[裁判结论]。

---

# 四、判决如下（判决主文）

> 本部分为金钱项的最终裁决。
> 任意一项金额和项目，绝对不得超出第一部分或诉求变更申请书中的额度上限。
> 每项判决必须有对应的诉请项目。

一、[被上诉人/上诉人]于本判决生效之日起[XX]日内支付[项目名称][金额]元；

二、[被上诉人/上诉人]于本判决生效之日起[XX]日内支付[项目名称][金额]元；

...

如未按本判决指定的期间履行给付金钱义务，应当依照《中华人民共和国民事诉讼法》第二百六十四条之规定，加倍支付迟延履行期间的债务利息。

---

> 本判决书草稿由AI辅助生成，请运行 verify_judgment_draft 工具进行自动化审计，
> 确保文书通过 [AUDIT_PASSED] 后方可提交人工审核。
"""
        return template
    except Exception as e:
        logger.error("get_judgment_draft_template: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"生成模板异常：{e}")


@mcp.tool()
def load_law_knowledge_base(
    law_dir: str,
) -> str:
    """装载法律法规知识库，构建条文级索引。

    law_dir: 法律法规库目录路径（包含法律、司法解释、部门规章等文件）
    """
    try:
        result = _law_kb.load_from_directory(law_dir)
        _rule_engine.law_kb = _law_kb
        _cross_ref_engine.law_kb = _law_kb
        return json.dumps({"success": True, **result}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("load_law_knowledge_base: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"装载异常：{e}")


@mcp.tool()
def verify_law_citation_online(
    citation_text: str,
) -> str:
    """对法条引用进行在线验证，生成验证提示词供Agent发送给自身LLM。

    citation_text: 法条引用原文（如"《中华人民共和国劳动合同法》第82条"）
    """
    try:
        local_result = _law_kb.verify_citation(citation_text)
        prompt = _web_verifier.get_law_verification_prompt(
            citation_text,
            local_info=f"本地库查找：{'已找到' if local_result.local_found else '未找到'}；"
                       f"是否现行有效：{'是' if local_result.is_current else '否'}；"
                       f"差异：{local_result.discrepancy or '无'}",
        )
        return json.dumps({
            "success": True,
            "citation_text": citation_text,
            "local_result": local_result.model_dump(),
            "verification_prompt": prompt,
            "suggestion": "请将 verification_prompt 发送给自身LLM进行在线验证，"
                          "然后将LLM返回的结果通过 parse_verification_response 工具解析。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("verify_law_citation_online: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"验证异常：{e}")


@mcp.tool()
def verify_case_number_online(
    case_number: str,
    court: str = "",
    judgment_date: str = "",
    key_holding: str = "",
) -> str:
    """对案例案号进行在线验证，生成验证提示词供Agent发送给自身LLM。

    case_number: 案号（如"(2024)苏01民终1234号"）
    court: 审理法院（可选）
    judgment_date: 裁判日期（可选）
    key_holding: 裁判要旨（可选）
    """
    try:
        local_result = _law_kb.verify_case_number(case_number)
        prompt = _web_verifier.get_case_verification_prompt(
            case_number, court, judgment_date, key_holding,
        )
        return json.dumps({
            "success": True,
            "case_number": case_number,
            "local_result": local_result.model_dump(),
            "verification_prompt": prompt,
            "suggestion": "请将 verification_prompt 发送给自身LLM进行在线验证，"
                          "然后将LLM返回的结果通过 parse_verification_response 工具解析。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("verify_case_number_online: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"验证异常：{e}")


@mcp.tool()
def parse_verification_response(
    target: str,
    target_type: str,
    llm_response: str,
) -> str:
    """解析LLM返回的在线验证结果为结构化数据。

    target: 验证目标（法条引用或案号）
    target_type: 验证类型（"法条"或"案例"）
    llm_response: LLM返回的验证结果文本
    """
    try:
        result = _web_verifier.parse_verification_response(target, target_type, llm_response)
        return json.dumps({
            "success": True,
            "result": result.model_dump(),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("parse_verification_response: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"解析异常：{e}")


@mcp.tool()
def cross_verify_document(
    document_text: str,
    manifest_path: str = "",
    vault_root: str = "",
    law_dir: str = "",
) -> str:
    """对判决书进行交叉验证——将事实陈述与证据材料、法条原文、案例信息进行多源比对。

    document_text: 裁判文书全文
    manifest_path: 证据索引清单路径（可选）
    vault_root: 工作区根目录（可选）
    law_dir: 法律法规库目录路径（可选）
    """
    try:
        report = _cross_ref_engine.cross_verify(
            document_text=document_text,
            manifest_path=manifest_path,
            vault_root=vault_root,
            law_dir=law_dir,
        )
        return json.dumps({
            "success": True,
            "total_claims": report.total_claims,
            "verified_claims": report.verified_claims,
            "total_issues": len(report.issues),
            "law_verifications": len(report.law_verifications),
            "case_verifications": len(report.case_verifications),
            "summary": report.summary,
            "issues": [
                {
                    "source_type": i.source_type,
                    "source_name": i.source_name,
                    "match_type": i.match_type,
                    "severity": i.severity,
                    "discrepancy": i.discrepancy[:100],
                    "suggestion": i.suggestion[:100],
                }
                for i in report.issues
            ],
            "law_verification_results": [
                {
                    "citation_text": lv.citation_text,
                    "local_found": lv.local_found,
                    "is_current": lv.is_current,
                    "discrepancy": lv.discrepancy[:100],
                }
                for lv in report.law_verifications
            ],
            "case_verification_results": [
                {
                    "case_number": cv.case_number,
                    "local_found": cv.local_found,
                    "is_real": cv.is_real,
                }
                for cv in report.case_verifications
            ],
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("cross_verify_document: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"交叉验证异常：{e}")


@mcp.tool()
def run_cross_verification(
    document_text: str,
    manifest_path: str = "",
    vault_root: str = "",
    law_dir: str = "",
    online_verify: bool = False,
) -> str:
    """对判决书执行完整的交叉验证，返回结构化的CrossReferenceReport。

    与cross_verify_document不同，本工具返回完整的法条验证结果（含confidence、verification_status）
    和案例验证结果（含court、judgment_date、key_holding），适合Agent进行深度分析。

    document_text: 裁判文书全文
    manifest_path: 证据索引清单路径（可选）
    vault_root: 工作区根目录（可选）
    law_dir: 法律法规库目录路径（可选）
    online_verify: 是否启用在线验证（默认False）
    """
    try:
        report = _cross_ref_engine.cross_verify(
            document_text=document_text,
            manifest_path=manifest_path,
            vault_root=vault_root,
            law_dir=law_dir,
            online_verify=online_verify,
        )
        return json.dumps({
            "success": True,
            "verification_time": report.verification_time,
            "total_claims": report.total_claims,
            "verified_claims": report.verified_claims,
            "total_issues": len(report.issues),
            "summary": report.summary,
            "issues": [
                {
                    "source_type": i.source_type,
                    "source_name": i.source_name,
                    "claim_text": i.claim_text[:200],
                    "source_text": i.source_text[:200],
                    "match_type": i.match_type,
                    "discrepancy": i.discrepancy[:200],
                    "severity": i.severity,
                    "h_code": i.h_code,
                    "suggestion": i.suggestion[:200],
                    "line_number": i.line_number,
                    "confidence": i.confidence,
                }
                for i in report.issues
            ],
            "law_verifications": [
                {
                    "citation_text": lv.citation_text,
                    "law_name": lv.law_name,
                    "article": lv.article,
                    "local_found": lv.local_found,
                    "online_verified": lv.online_verified,
                    "is_current": lv.is_current,
                    "replaced_by": lv.replaced_by,
                    "effective_date": lv.effective_date,
                    "verification_source": lv.verification_source,
                    "verification_time": lv.verification_time,
                    "discrepancy": lv.discrepancy[:200],
                    "confidence": lv.confidence,
                    "verification_status": getattr(lv, 'verification_status', ''),
                }
                for lv in report.law_verifications
            ],
            "case_verifications": [
                {
                    "case_number": cv.case_number,
                    "local_found": cv.local_found,
                    "online_verified": cv.online_verified,
                    "is_real": cv.is_real,
                    "court": cv.court,
                    "judgment_date": cv.judgment_date,
                    "key_holding": cv.key_holding[:200],
                    "verification_source": cv.verification_source,
                    "verification_time": cv.verification_time,
                    "confidence": cv.confidence,
                }
                for cv in report.case_verifications
            ],
            "web_verifications": [
                {
                    "target": wv.target,
                    "target_type": wv.target_type,
                    "is_verified": wv.is_verified,
                    "source_url": wv.source_url,
                    "confidence": wv.confidence,
                }
                for wv in report.web_verifications
            ] if report.web_verifications else [],
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("run_cross_verification: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"交叉验证异常：{e}")


@mcp.tool()
def batch_detect(
    documents_json: str,
    manifest_path: str = "",
    complaint_text: str = "",
    amended_text: str = "",
    vault_root: str = "",
    local_law_dir: str = "",
    rate_limit_delay: float = 0.5,
) -> str:
    """批量检测多份文书的幻觉风险，返回每份文书的检测结果摘要。

    适用于类案比对、多版本比对等场景，避免逐个调用run_rule_engine_full。

    documents_json: JSON数组，每个元素为{"id": "文档标识", "text": "文书全文"}
    manifest_path: 证据索引清单路径（可选）
    complaint_text: 起诉状/上诉状全文（可选）
    amended_text: 变更诉求申请书全文（可选）
    vault_root: 工作区根目录（可选）
    local_law_dir: 本地法律法规库目录（可选）
    rate_limit_delay: 批次间延迟秒数（默认0.5秒，防止资源过载）
    """
    try:
        docs = json.loads(documents_json)
        if not isinstance(docs, list):
            return make_error(ErrorCode.INVALID_INPUT, "documents_json必须是JSON数组")

        if manifest_path:
            logger.info(
                "[LOCK] batch_detect: acquiring _state_lock for evidence_index.load, "
                "thread=%s",
                threading.current_thread().name,
            )
            with _state_lock:
                _evidence_index.load(manifest_path, vault_root)
                _rule_engine.evidence_index = _evidence_index
                logger.info(
                    "[LOCK] batch_detect: evidence_index loaded, thread=%s",
                    threading.current_thread().name,
                )

        if local_law_dir:
            logger.info(
                "[LOCK] batch_detect: acquiring _state_lock for law state update, "
                "thread=%s",
                threading.current_thread().name,
            )
            with _state_lock:
                _law_checker.load_local_laws(local_law_dir)
                _law_kb.load_from_directory(local_law_dir)
                _rule_engine.law_checker = _law_checker
                _rule_engine.law_kb = _law_kb
                logger.info(
                    "[LOCK] batch_detect: law state updated, thread=%s",
                    threading.current_thread().name,
                )

        if complaint_text:
            _claim_parser.parse(complaint_text, amended_text=amended_text)

        results = []
        for doc in docs:
            doc_id = doc.get("id", "unknown")
            doc_text = doc.get("text", "")
            if not doc_text:
                results.append({
                    "id": doc_id,
                    "success": False,
                    "error": "文档内容为空",
                })
                continue

            result = _rule_engine.run_full_scan(
                document_text=doc_text,
                complaint_text=complaint_text,
                amended_text=amended_text,
                manifest_path="",
                vault_root="",
                local_law_dir=local_law_dir,
            )

            if rate_limit_delay > 0 and doc != docs[-1]:
                time.sleep(rate_limit_delay)

            results.append({
                "id": doc_id,
                "success": True,
                "total_flags": result.total_flags,
                "hallucination_score": result.hallucination_score,
                "risk_grade": result.risk_grade,
                "risk_description": result.risk_description,
                "dimensions": [
                    {
                        "dimension": d.dimension,
                        "title": d.dimension_title,
                        "total_flags": d.total_flags,
                        "critical": d.critical_count,
                        "high": d.high_count,
                        "medium": d.medium_count,
                        "low": d.low_count,
                    }
                    for d in result.dimensions
                ],
                "structure_issues": len(result.structure_issues),
                "citation_frauds": len(result.citation_frauds),
                "claim_violations": len(result.claim_violations),
                "syllogism_breaks": len(result.syllogism_breaks),
                "rhetoric_items": len(result.rhetoric_items),
                "law_citation_issues": len(result.law_citation_issues),
            })

        scores = [r["hallucination_score"] for r in results if r.get("success")]
        avg_score = sum(scores) / len(scores) if scores else 0.0

        return json.dumps({
            "success": True,
            "total_documents": len(docs),
            "processed_documents": len(results),
            "average_score": round(avg_score, 1),
            "results": results,
        }, ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        return make_error(ErrorCode.PARSE_FAILED, f"documents_json解析失败：{e}")
    except Exception as e:
        logger.error("batch_detect: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"批量检测异常：{e}")


@mcp.tool()
def compare_documents(
    document_a_text: str,
    document_b_text: str,
    label_a: str = "文档A",
    label_b: str = "文档B",
    manifest_path: str = "",
    complaint_text: str = "",
    vault_root: str = "",
    local_law_dir: str = "",
) -> str:
    """比对两份文书的幻觉检测结果，生成差异对比报告。

    适用于多版本判决书比对（如V40 vs V42），检测幻觉修复效果。

    document_a_text: 文档A全文（通常是旧版本）
    document_b_text: 文档B全文（通常是新版本）
    label_a: 文档A标签（如"V40"）
    label_b: 文档B标签（如"V42"）
    manifest_path: 证据索引清单路径（可选）
    complaint_text: 起诉状全文（可选）
    vault_root: 工作区根目录（可选）
    local_law_dir: 本地法律法规库目录（可选）
    """
    try:
        if manifest_path:
            logger.info(
                "[LOCK] compare_documents: acquiring _state_lock for evidence_index.load, "
                "thread=%s",
                threading.current_thread().name,
            )
            with _state_lock:
                _evidence_index.load(manifest_path, vault_root)
                _rule_engine.evidence_index = _evidence_index
                logger.info(
                    "[LOCK] compare_documents: evidence_index loaded, thread=%s",
                    threading.current_thread().name,
                )

        if local_law_dir:
            logger.info(
                "[LOCK] compare_documents: acquiring _state_lock for law state update, "
                "thread=%s",
                threading.current_thread().name,
            )
            with _state_lock:
                _law_checker.load_local_laws(local_law_dir)
                _law_kb.load_from_directory(local_law_dir)
                logger.info(
                    "[LOCK] compare_documents: law state updated, thread=%s",
                    threading.current_thread().name,
                )

        if complaint_text:
            _claim_parser.parse(complaint_text)

        result_a = _rule_engine.run_full_scan(
            document_text=document_a_text,
            complaint_text=complaint_text,
            manifest_path="",
            vault_root="",
            local_law_dir=local_law_dir,
        )

        result_b = _rule_engine.run_full_scan(
            document_text=document_b_text,
            complaint_text=complaint_text,
            manifest_path="",
            vault_root="",
            local_law_dir=local_law_dir,
        )

        dim_comparison = []
        for dim_a in result_a.dimensions:
            dim_b = next(
                (d for d in result_b.dimensions if d.dimension == dim_a.dimension),
                None,
            )
            if dim_b:
                flag_diff = dim_a.total_flags - dim_b.total_flags
                dim_comparison.append({
                    "dimension": dim_a.dimension,
                    "title": dim_a.dimension_title,
                    f"{label_a}_flags": dim_a.total_flags,
                    f"{label_b}_flags": dim_b.total_flags,
                    "diff": flag_diff,
                    "improvement": flag_diff > 0,
                })

        score_diff = result_a.hallucination_score - result_b.hallucination_score

        return json.dumps({
            "success": True,
            "comparison": {
                f"{label_a}_score": result_a.hallucination_score,
                f"{label_b}_score": result_b.hallucination_score,
                "score_diff": round(score_diff, 1),
                "score_improvement": score_diff > 0,
                f"{label_a}_grade": result_a.risk_grade,
                f"{label_b}_grade": result_b.risk_grade,
            },
            "dimension_comparison": dim_comparison,
            f"{label_a}_total_flags": result_a.total_flags,
            f"{label_b}_total_flags": result_b.total_flags,
            f"{label_a}_structure_issues": len(result_a.structure_issues),
            f"{label_b}_structure_issues": len(result_b.structure_issues),
            f"{label_a}_citation_frauds": len(result_a.citation_frauds),
            f"{label_b}_citation_frauds": len(result_b.citation_frauds),
            f"{label_a}_claim_violations": len(result_a.claim_violations),
            f"{label_b}_claim_violations": len(result_b.claim_violations),
            f"{label_a}_syllogism_breaks": len(result_a.syllogism_breaks),
            f"{label_b}_syllogism_breaks": len(result_b.syllogism_breaks),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("compare_documents: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"比对异常：{e}")


@mcp.tool()
def search_applicable_law(
    dispute_type: str,
    keywords: str = "",
) -> str:
    """搜索适用法律法规，从法律知识库中检索相关条文。

    dispute_type: 纠纷类型（如"劳动争议""劳动合同""工资报酬""违法解除""二倍工资"等）
    keywords: 搜索关键词，多个关键词用逗号分隔（可选）
    """
    try:
        kw_list = [k.strip() for k in keywords.split(",") if k.strip()] if keywords else []
        articles = _law_kb.search_applicable_law(dispute_type, kw_list)
        return json.dumps({
            "success": True,
            "dispute_type": dispute_type,
            "keywords": kw_list,
            "total_articles": len(articles),
            "articles": [
                {
                    "law_name": a.law_name,
                    "article_number": a.article_number,
                    "full_text": a.full_text[:200],
                    "law_type": a.law_type,
                    "hierarchy": a.hierarchy,
                    "is_procedural": a.is_procedural,
                    "verification_status": a.verification_status,
                }
                for a in articles[:30]
            ],
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("search_applicable_law: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"搜索异常：{e}")


@mcp.tool()
def get_law_kb_statistics() -> str:
    """获取法律知识库的统计信息。"""
    try:
        stats = _law_kb.get_statistics()
        return json.dumps({"success": True, **stats}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_law_kb_statistics: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"统计异常：{e}")


@mcp.tool()
def get_authoritative_sources(
    source_type: str = "",
) -> str:
    """获取权威验证来源列表。

    source_type: 来源类型（"法律法规""案例""学术参考"，空字符串返回全部）
    """
    try:
        sources = _web_verifier.get_authoritative_sources(source_type)
        return json.dumps({"success": True, "sources": sources}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_authoritative_sources: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"获取异常：{e}")


@mcp.tool()
def generate_report_filename_tool(
    agent_name: str = "TraeCN",
    llm_name: str = "",
    content_summary: str = "法律文书幻觉检测报告",
    version: str = "v2.0",
) -> str:
    """生成符合规范的报告文件名。

    文件名格式强制规范：{AI Agent名}_{LLM名}_{简体中文内容描述}_{版本号}_{YYYYMMDD}.md
    示例：TraeCN_GLM51_法律文书幻觉检测报告_v2.0_20260529.md
    约束：Agent名和LLM名必须为英文/数字组合且不可为空，内容描述必须包含简体中文，版本号以v开头，日期自动取当天

    agent_name: AI Agent名称（英文/数字，必填，默认TraeCN）
    llm_name: LLM名称和版本号（英文/数字，必填，如GLM51、Claude4Sonnet）
    content_summary: 报告内容概要（简体中文，必填）
    version: 报告版本号（以v开头，默认v2.0）
    """
    try:
        filename = generate_report_filename(agent_name, llm_name, content_summary, version)
        return json.dumps({"success": True, "filename": filename}, ensure_ascii=False, indent=2)
    except ValueError as e:
        return make_error(ErrorCode.VALIDATION_ERROR, str(e))
    except Exception as e:
        logger.error("generate_report_filename_tool: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"生成异常：{e}")


@mcp.tool()
def save_hallucination_report(
    report_markdown: str,
    output_dir: str,
    agent_name: str = "TraeCN",
    llm_name: str = "",
    content_summary: str = "法律文书幻觉检测报告",
    version: str = "v2.0",
) -> str:
    """将幻觉检测报告保存为Markdown文件，文件名强制遵守规范格式。

    文件名格式：{AI Agent名}_{LLM名}_{简体中文内容描述}_{版本号}_{YYYYMMDD}.md
    保存后返回报告文件的完整绝对路径和所在文件夹路径。

    report_markdown: 报告的Markdown全文内容
    output_dir: 报告输出目录的绝对路径
    agent_name: AI Agent名称（英文/数字，必填，默认TraeCN）
    llm_name: LLM名称和版本号（英文/数字，必填，如GLM51、Claude4Sonnet）
    content_summary: 报告内容概要（简体中文，必填）
    version: 报告版本号（以v开头，默认v2.0）
    """
    try:
        import os as _os

        filename = generate_report_filename(agent_name, llm_name, content_summary, version)
        abs_output_dir = _os.path.abspath(output_dir)
        _os.makedirs(abs_output_dir, exist_ok=True)
        report_path = _os.path.join(abs_output_dir, filename)

        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_markdown)

        logger.info("报告已保存: %s", report_path)

        return json.dumps({
            "success": True,
            "filename": filename,
            "report_path": report_path,
            "output_directory": abs_output_dir,
            "file_size_bytes": _os.path.getsize(report_path),
        }, ensure_ascii=False, indent=2)
    except ValueError as e:
        return make_error(ErrorCode.VALIDATION_ERROR, str(e))
    except Exception as e:
        logger.error("save_hallucination_report: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"保存报告异常：{e}")


@mcp.tool()
def build_vector_index(
    law_dir: str = "",
    manifest_path: str = "",
    vault_root: str = "",
    index_dir: str = "",
) -> str:
    """构建向量索引，将法律文本矢量化，适合LLM检索格式。

    支持两种模式：
    1. 向量索引模式：使用本地Embedding模型（如bge-large-zh-v1.5）将文本矢量化
    2. 关键词索引模式：当Embedding模型不可用时自动降级

    law_dir: 法律法规库目录路径（可选）
    manifest_path: 证据索引清单路径（可选）
    vault_root: 工作区根目录（可选）
    index_dir: 索引保存目录路径（可选，默认在MCP目录下的vector_index）
    """
    try:
        if law_dir and os.path.exists(law_dir):
            logger.info(
                "[LOCK] build_vector_index: acquiring _state_lock for law_kb.load, "
                "thread=%s",
                threading.current_thread().name,
            )
            with _state_lock:
                logger.info(
                    "[LOCK] build_vector_index: lock acquired, thread=%s",
                    threading.current_thread().name,
                )
                if not _law_kb.loaded:
                    _law_kb.load_from_directory(law_dir)
                logger.info(
                    "[LOCK] build_vector_index: law_kb state checked, loaded=%s, "
                    "thread=%s",
                    _law_kb.loaded, threading.current_thread().name,
                )
            logger.info(
                "[LOCK] build_vector_index: lock released, thread=%s",
                threading.current_thread().name,
            )
            _vector_index.index_law_articles(_law_kb.articles)
            _vector_index.index_cases(_law_kb.cases)
            _vector_index.index_academic_refs(_law_kb.academic_refs)
            _vector_index.index_principles(_law_kb.principles)

        if manifest_path and os.path.exists(manifest_path):
            _vector_index.index_evidence_files(manifest_path, vault_root)

        save_dir = index_dir
        if not save_dir:
            mcp_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            save_dir = os.path.join(mcp_root, "vector_index")

        save_result = _vector_index.save_index(save_dir)
        stats = _vector_index.get_statistics()

        return json.dumps({
            "success": True,
            "save_result": save_result,
            "statistics": stats.model_dump(),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("build_vector_index: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"构建索引异常：{e}")


@mcp.tool()
def search_vector_index(
    query: str,
    doc_types: str = "",
    top_k: int = 10,
) -> str:
    """在向量索引中检索相关法律文献。

    query: 查询文本（如"违法解除劳动合同赔偿金计算"）
    doc_types: 文档类型过滤，逗号分隔（如"法条,案例"，空字符串不过滤）
    top_k: 返回结果数量上限
    """
    try:
        types_filter = [t.strip() for t in doc_types.split(",") if t.strip()] if doc_types else None
        results = _vector_index.search(query, types_filter, top_k)

        return json.dumps({
            "success": True,
            "query": query,
            "total_results": len(results),
            "results": [r.model_dump() for r in results],
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("search_vector_index: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"检索异常：{e}")


@mcp.tool()
def build_llm_context(
    query: str,
    doc_types: str = "",
    top_k: int = 5,
    max_chars: int = 8000,
) -> str:
    """构建适合LLM检索的法律文献上下文，直接注入到提示词中。

    将检索结果格式化为Markdown文本，包含来源类型、标题、相关度、
    匹配关键词和内容摘要，适合作为LLM的参考上下文。

    query: 查询文本
    doc_types: 文档类型过滤，逗号分隔（可选）
    top_k: 返回结果数量上限
    max_chars: 最大字符数限制
    """
    try:
        types_filter = [t.strip() for t in doc_types.split(",") if t.strip()] if doc_types else None
        context = _vector_index.build_context_for_llm(query, types_filter, top_k, max_chars)

        return json.dumps({
            "success": True,
            "query": query,
            "context_length": len(context),
            "token_estimate": _estimate_tokens(context),
            "context": context,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("build_llm_context: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"构建上下文异常：{e}")


@mcp.tool()
def get_vector_index_stats() -> str:
    """获取向量索引的统计信息。"""
    try:
        stats = _vector_index.get_statistics()
        return json.dumps({"success": True, **stats.model_dump()}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_vector_index_stats: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"统计异常：{e}")


@mcp.tool()
def init_case_workspace(
    vault_root: str,
    case_name: str,
    evidence_files: list[str] | None = None,
    complaint_file: str = "",
) -> str:
    """初始化新案件工作区，创建证据索引清单和目录结构。

    自动创建以下文件和目录：
    - .trae/evidence_manifest.md：证据索引清单
    - output/：判决书草稿输出目录
    - scripts/：审计脚本目录（含verify_agent.py）
    - data/：法条数据库目录

    vault_root: 工作区根目录
    case_name: 案件名称（如"张三_劳动争议"）
    evidence_files: 证据文件路径列表
    complaint_file: 起诉状/上诉状文件路径
    """
    try:
        import os as _os

        vault = _os.path.abspath(vault_root)
        case_dir = _os.path.join(vault, case_name)
        _os.makedirs(case_dir, exist_ok=True)

        tra_dir = _os.path.join(case_dir, ".trae")
        _os.makedirs(tra_dir, exist_ok=True)

        output_dir = _os.path.join(case_dir, "output")
        _os.makedirs(output_dir, exist_ok=True)

        scripts_dir = _os.path.join(case_dir, "scripts")
        _os.makedirs(scripts_dir, exist_ok=True)

        data_dir = _os.path.join(case_dir, "data")
        _os.makedirs(data_dir, exist_ok=True)

        manifest_path = _os.path.join(tra_dir, "evidence_manifest.md")
        manifest_lines = ["# 证据索引清单", ""]

        if complaint_file:
            manifest_lines.append("## 诉状类")
            manifest_lines.append(f"- `{complaint_file}`")
            manifest_lines.append("")

        if evidence_files:
            manifest_lines.append("## 证据类")
            for ef in evidence_files:
                manifest_lines.append(f"- `{ef}`")
            manifest_lines.append("")

        manifest_lines.append("## 法律依据类")
        manifest_lines.append("")

        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write("\n".join(manifest_lines))

        result = {
            "status": "initialized",
            "case_dir": case_dir,
            "manifest_path": manifest_path,
            "output_dir": output_dir,
            "scripts_dir": scripts_dir,
            "data_dir": data_dir,
            "evidence_count": len(evidence_files or []),
            "message": f"案件工作区已初始化：{case_name}",
        }

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("init_case_workspace: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"初始化异常：{e}")


# ── 工作流编排工具 ──────────────────────────────────────────


@mcp.tool()
def create_detection_workflow(
    document_full_text: str,
    document_name: str = "",
    evidence_manifest_path: str = "",
    vault_root: str = "",
    max_context_tokens: int = 128000,
) -> str:
    """创建多子代理并行检测工作流，将六维检测任务分解为独立子任务。

    借鉴claude-code的coordinator模式，将幻觉检测任务分解为多个子代理并行执行，
    每个子代理负责一个阶段的检测，减少单次LLM调用的上下文长度。

    工作流包含8个阶段：
    1. 文书结构检测（关键）
    2. 证据引注与事实来源检测（关键，H-1）
    3. 法条引用与法律适用检测（高，H-2）
    4. 三段论完整性检测（高，H-3）
    5. 主观修辞检测（中，H-4）
    6. 诉求边界检测（关键，H-5）
    7. 交叉验证与原始文件核对（高）
    8. 程序时效检测（高）

    document_full_text: 判决书全文
    document_name: 文档名称
    evidence_manifest_path: 证据索引清单路径
    vault_root: 工作区根目录
    max_context_tokens: LLM最大上下文令牌数
    """
    try:
        sections = _extract_sections(document_full_text)
        sections["full_text"] = document_full_text

        orchestrator = _workflow_orchestrator
        if max_context_tokens != 128000:
            orchestrator = WorkflowOrchestrator(max_context_tokens=max_context_tokens)

        run = orchestrator.create_workflow(
            document_name=document_name or "未命名文档",
            document_sections=sections,
            evidence_manifest_path=evidence_manifest_path,
            vault_root=vault_root,
        )

        _workflow_orchestrator._runs[run.run_id] = run
        _workflow_orchestrator._active_run_id = run.run_id

        parallel_tasks = orchestrator.get_parallel_tasks(run.run_id)

        return json.dumps({
            "success": True,
            "run_id": run.run_id,
            "total_tasks": run.total_tasks,
            "total_tokens_budget": run.total_tokens_budget,
            "available_parallel_tasks": len(parallel_tasks),
            "parallel_tasks": parallel_tasks,
            "orchestration_mode": _workflow_orchestrator.orchestration_mode.value,
            "message": "工作流已创建。请对每个parallel_task使用Agent工具并行执行检测，"
                       "完成后调用update_workflow_task更新状态，最后调用aggregate_workflow_results汇总结果。",
        }, ensure_ascii=False, indent=2)
    except RuntimeError as e:
        return make_error(ErrorCode.VALIDATION_ERROR, str(e))
    except Exception as e:
        logger.error("create_detection_workflow: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"创建工作流异常：{e}")


@mcp.tool()
def get_workflow_tasks(run_id: str) -> str:
    """获取工作流中可并行执行的子任务列表。

    返回所有状态为pending且依赖已满足的子任务，供Agent并行调度执行。

    run_id: 工作流运行ID
    """
    try:
        tasks = _workflow_orchestrator.get_parallel_tasks(run_id)
        if not tasks:
            status = _workflow_orchestrator.get_workflow_status(run_id)
            if "error" in status:
                return make_error(ErrorCode.NOT_FOUND, status["error"])
            return json.dumps({
                "success": True,
                "run_id": run_id,
                "available_tasks": 0,
                "message": "无可执行的并行任务。工作流可能已完成或所有任务正在运行。",
                "workflow_status": status,
            }, ensure_ascii=False, indent=2)

        return json.dumps({
            "success": True,
            "run_id": run_id,
            "available_tasks": len(tasks),
            "tasks": tasks,
            "instruction": "请对每个任务使用Agent工具并行执行。每个任务的prompt_template字段包含完整的检测提示词。"
                           "完成后调用update_workflow_task更新状态。",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_workflow_tasks: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"获取任务异常：{e}")


@mcp.tool()
def update_workflow_task(
    run_id: str,
    task_id: str,
    status: str,
    result_json: str = "",
    error: str = "",
) -> str:
    """更新工作流子任务的状态和结果。

    Agent完成一个子任务的检测后，调用此工具更新状态。

    run_id: 工作流运行ID
    task_id: 子任务ID
    status: 新状态（completed/failed/cancelled）
    result_json: 检测结果的JSON字符串（可选）
    error: 错误信息（可选）
    """
    try:
        result = None
        if result_json:
            try:
                result = json.loads(result_json)
            except json.JSONDecodeError:
                result = {"raw_result": result_json}

        task_status = TaskStatus(status)
        _workflow_orchestrator.update_task_status(
            run_id=run_id,
            task_id=task_id,
            status=task_status,
            result=result,
            error=error,
        )

        workflow_status = _workflow_orchestrator.get_workflow_status(run_id)

        return json.dumps({
            "success": True,
            "run_id": run_id,
            "task_id": task_id,
            "new_status": status,
            "workflow_progress": workflow_status.get("progress_pct", 0),
            "workflow_status": workflow_status.get("status", "unknown"),
            "remaining_tasks": (
                workflow_status.get("total_tasks", 0)
                - workflow_status.get("completed_tasks", 0)
                - workflow_status.get("failed_tasks", 0)
            ),
        }, ensure_ascii=False, indent=2)
    except ValueError as e:
        return make_error(ErrorCode.VALIDATION_ERROR, f"无效状态值：{e}")
    except Exception as e:
        logger.error("update_workflow_task: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"更新任务异常：{e}")


@mcp.tool()
def get_workflow_status(run_id: str) -> str:
    """获取工作流的详细状态，包括所有子任务的进度。

    run_id: 工作流运行ID
    """
    try:
        status = _workflow_orchestrator.get_workflow_status(run_id)
        if "error" in status:
            return make_error(ErrorCode.NOT_FOUND, status["error"])
        return json.dumps({"success": True, **status}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_workflow_status: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"获取状态异常：{e}")


@mcp.tool()
def aggregate_workflow_results(run_id: str) -> str:
    """汇总工作流所有子任务的检测结果，生成综合评估。

    所有子任务完成后调用此工具，汇总各维度检测结果，
    计算风险等级，生成综合修正建议。

    run_id: 工作流运行ID
    """
    try:
        aggregated = _workflow_orchestrator.aggregate_results(run_id)
        if "error" in aggregated:
            return make_error(ErrorCode.NOT_FOUND, aggregated["error"])

        return json.dumps({"success": True, **aggregated}, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("aggregate_workflow_results: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"汇总结果异常：{e}")


@mcp.tool()
def list_workflows() -> str:
    """列出所有工作流的概要信息。"""
    try:
        workflows = _workflow_orchestrator.list_workflows()
        return json.dumps(
            {"success": True, "total": len(workflows), "workflows": workflows},
            ensure_ascii=False, indent=2,
        )
    except Exception as e:
        logger.error("list_workflows: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"列出工作流异常：{e}")


@mcp.tool()
def set_orchestration_mode(
    mode: str = "internal",
) -> str:
    """设置工作流编排模式，控制MCP内部workflow与外部编排器（如Claude Code）的协作方式。

    三种模式：
    - internal（默认）：MCP内部编排workflow，适用于独立使用MCP的场景
    - external：外部编排器模式，MCP只允许一个活跃workflow，防止与Claude Code的workflow冲突
    - passive：被动模式，MCP workflow完全禁用，所有检测任务由外部编排器调度

    推荐策略：当Claude Code触发workflow编排时，自动将MCP设为external或passive模式，
    避免两个编排器同时运行导致冲突。

    mode: 编排模式（internal/external/passive）
    """
    try:
        result = _workflow_orchestrator.set_orchestration_mode(mode)
        if "error" in result:
            return make_error(ErrorCode.VALIDATION_ERROR, result["error"])
        return json.dumps({
            "success": True,
            "mode": result["mode"],
            "description": {
                "internal": "MCP内部编排，独立运行workflow",
                "external": "外部编排器模式，MCP只允许一个活跃workflow，防止冲突",
                "passive": "被动模式，MCP workflow完全禁用，由外部编排器调度",
            }.get(result["mode"], ""),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("set_orchestration_mode: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"设置编排模式异常：{e}")


@mcp.tool()
def get_orchestration_mode() -> str:
    """获取当前工作流编排模式及活跃workflow状态。

    返回当前模式（internal/external/passive）、活跃workflow ID、
    以及环境变量ORCHESTRATOR_MODE的值。
    """
    try:
        active_run = None
        if _workflow_orchestrator._active_run_id:
            run = _workflow_orchestrator._runs.get(_workflow_orchestrator._active_run_id)
            if run:
                active_run = {
                    "run_id": run.run_id,
                    "document_name": run.document_name,
                    "status": run.status.value,
                    "progress_pct": run.progress_pct,
                }

        return json.dumps({
            "success": True,
            "mode": _workflow_orchestrator.orchestration_mode.value,
            "env_orchestrator_mode": os.environ.get("ORCHESTRATOR_MODE", ""),
            "active_workflow": active_run,
            "total_workflows": len(_workflow_orchestrator._runs),
            "recommendation": {
                "internal": "MCP独立运行，无需外部编排器",
                "external": "Claude Code等外部编排器已激活，MCP限制为单workflow",
                "passive": "MCP workflow已禁用，所有任务由外部编排器调度",
            }.get(_workflow_orchestrator.orchestration_mode.value, ""),
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_orchestration_mode: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"获取编排模式异常：{e}")


@mcp.tool()
def export_detection_result(
    detection_result_json: str,
    format: str = "json",
) -> str:
    """将检测结果导出为JSON或CSV格式。

    detection_result_json: run_rule_engine_full返回的JSON字符串
    format: 导出格式，可选"json"或"csv"
    """
    try:
        data = json.loads(detection_result_json)
        if not data.get("success"):
            return make_error(ErrorCode.INVALID_INPUT, "检测结果JSON无效")

        from .models import HallucinationDetectionResult
        result_dict = data.get("result", data)
        result = (
            HallucinationDetectionResult(**result_dict)
            if isinstance(result_dict, dict)
            else HallucinationDetectionResult()
        )

        if format.lower() == "csv":
            return _builder.export_csv(result)
        else:
            return _builder.export_json(result)
    except Exception as e:
        logger.error("export_detection_result: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"导出异常：{e}")


@mcp.tool()
def get_detection_config() -> str:
    """获取当前检测配置参数。

    返回所有可配置的检测阈值和参数，以及环境变量覆盖状态。
    """
    try:
        from .config import DEFAULT_CONFIG, DetectionConfig
        current = DetectionConfig.from_env()
        diff = {}
        for field_name in DEFAULT_CONFIG.model_fields:
            default_val = getattr(DEFAULT_CONFIG, field_name)
            current_val = getattr(current, field_name)
            if default_val != current_val:
                diff[field_name] = {"default": default_val, "current": current_val, "overridden": True}

        return json.dumps({
            "success": True,
            "config": current.model_dump(),
            "overrides": diff,
            "env_prefix": "LH_",
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("get_detection_config: %s", e, exc_info=True)
        return make_error(ErrorCode.INTERNAL_ERROR, f"获取配置异常：{e}")


if __name__ == "__main__":
    main()
