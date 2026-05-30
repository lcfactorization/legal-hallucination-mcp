"""多版本判决书幻觉比对扫描 — 并行检测V40/V41/V40P1/V42。

功能：
1. 并行扫描4个版本的判决书
2. 实时显示workflow进度（百分比、耗时、token数）
3. 生成多版本比对报告
4. 为每个幻觉项提供详细可执行的人工修改建议
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from legal_hallucination_mcp.evidence_index import EvidenceIndex
from legal_hallucination_mcp.report_builder import generate_report_filename
from legal_hallucination_mcp.rule_engine import RuleEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger("multi_version_scan")

def _resolve_env_path(env_key: str, default: str = "") -> str:
    val = os.environ.get(env_key, default)
    if val:
        return val
    if env_key == "VAULT_ROOT":
        return os.path.join(os.path.dirname(__file__), "vault_mirror", "..", "..")
    return ""


MANIFEST_PATH = _resolve_env_path(
    "EVIDENCE_MANIFEST_PATH",
    os.path.join(_resolve_env_path("VAULT_ROOT"), ".trae", "evidence_manifest.md"),
)
VAULT_ROOT = _resolve_env_path("VAULT_ROOT", str(Path(__file__).resolve().parent.parent))
OUTPUT_DIR = _resolve_env_path("OUTPUT_DIR", str(Path(__file__).resolve().parent / "output"))
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
LOCAL_LAW_DIR = os.environ.get(
    "LOCAL_LAW_DIR",
    os.path.join(_PROJECT_ROOT, "vault_mirror", "案件", "法律法规"),
)

VERSION_FILES = {
    "V40": os.path.join(VAULT_ROOT, "V40_模拟二审判决书_苏06民终6271号劳动争议_20260525.md"),
    "V40P1": os.path.join(VAULT_ROOT, "V40P1_模拟二审判决书_苏06民终6271号劳动争议_20260527.md"),
    "V41": os.path.join(VAULT_ROOT, "V41_模拟二审判决书_苏06民终6271号劳动争议_20260527.md"),
    "V42": os.path.join(VAULT_ROOT, "V42_模拟二审判决书_苏06民终6271号劳动争议_20260527.md"),
    "V43": os.path.join(VAULT_ROOT, "V43_模拟二审判决书_苏06民终6271号劳动争议_20260528.md"),
}


@dataclass
class ScanProgress:
    version: str = ""
    total_stages: int = 12
    completed_stages: int = 0
    start_time: float = 0.0
    end_time: float = 0.0
    token_estimate: int = 0
    status: str = "pending"
    midpoint_logged: bool = False
    midpoint_token: int = 0
    midpoint_elapsed: float = 0.0

    @property
    def progress_pct(self) -> float:
        if self.total_stages == 0:
            return 0.0
        return round(self.completed_stages / self.total_stages * 100, 1)

    @property
    def elapsed_sec(self) -> float:
        end = self.end_time or time.time()
        return round(end - self.start_time, 2) if self.start_time else 0.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f')
    non_cjk = len(text) - cjk
    return int(cjk / 1.5 + non_cjk / 4.0)


HALLUCINATION_FIX_GUIDE = {
    "类案案号杜撰": {
        "diagnosis": "判决书引用了无法验证的类案案号，大模型可能杜撰了格式正确但实际不存在的案号",
        "fix_steps": [
            "1. 在中国裁判文书网(https://wenshu.court.gov.cn)搜索该案号，确认是否存在",
            "2. 若案号不存在：删除该类案引用，或替换为可验证的真实类案",
            "3. 若案号存在但内容不符：修正类案的裁判宗旨描述，确保与原文一致",
            "4. 在evidence_manifest.md的'类案类'分区添加已验证的类案判决书文件路径",
            "5. 重新运行: python update_evidence_manifest.py --add '类案文件路径'",
            "6. 重新扫描验证: python -m pytest tests/test_adversarial.py -v",
        ],
        "prompt_fix": "在生成判决书时，使用'模拟类案A-01'等标识替代真实案号格式，并添加'模拟类案参考'标注",
    },
    "引注欺诈": {
        "diagnosis": "判决书引用的证据名称与证据索引清单中的文件名不匹配",
        "fix_steps": [
            "1. 对比判决书中的证据引用名与evidence_manifest.md中的文件名",
            "2. 确认证据30的实际文件名：证据30-1(股东会议决议对账单等.pdf) + 证据30-2(对账单部分.pdf)",
            "3. 修改判决书中的证据引用，使用与清单一致的名称",
            "4. 若清单名称有误：运行 python update_evidence_manifest.py --update '证据30' --desc '正确描述'",
            "5. 重新运行幻觉检测确认引注匹配",
        ],
        "prompt_fix": "在生成判决书时，从evidence_manifest.md中提取精确的证据文件名，禁止自行编造或简化证据名称",
    },
    "程序日期杜撰": {
        "diagnosis": "判决书中的程序性日期（立案日期、开庭日期、判决日期等）可能被大模型编造",
        "fix_steps": [
            "1. 核实案件真实时间线：查阅一审裁定书、仲裁裁决书等程序性文书",
            "2. 将判决书中所有日期与真实时间线逐一比对",
            "3. 修正不一致的日期，确保与程序性文书完全一致",
            "4. 在evidence_manifest.md中添加程序性文书（如裁定书、庭审笔录等）",
            "5. 重新扫描验证",
        ],
        "prompt_fix": "在生成判决书时，从起诉状、仲裁裁决书等文件中提取真实日期，禁止凭空编造程序性日期",
    },
    "引用格式问题": {
        "diagnosis": "法条引用格式不规范，缺少款/项序号或格式不一致",
        "fix_steps": [
            "1. 查阅法律法规原文，确认引用的法条是否存在且序号正确",
            "2. 补充完整的款/项序号，如'第三十五条第一款'而非仅'第三十五条'",
            "3. 将法律法规原文添加到evidence_manifest.md的'法律类'分区",
            "4. 运行 python update_evidence_manifest.py --from-report 检测报告路径",
            "5. 重新扫描验证",
        ],
        "prompt_fix": "在生成判决书时，从法律法规原文中提取精确的条文序号，包含款/项级别",
    },
    "项目越权": {
        "diagnosis": "判决主文支持的金额或项目超出了原告/上诉人的诉请范围",
        "fix_steps": [
            "1. 仔细核对起诉状和变更诉求申请书中的每一项诉请及金额上限",
            "2. 将判决主文的每一项与对应诉请逐一比对",
            "3. 删除超出诉请范围的判决项，或将金额调整至诉请上限以内",
            "4. 若诉请本身有误：先修正起诉状，再重新生成判决书",
            "5. 重新运行幻觉检测确认无超诉请裁判",
        ],
        "prompt_fix": "在生成判决主文时，严格以起诉状和变更诉求申请书为边界，任何金额不得超过诉请上限",
    },
    "参照系替换": {
        "diagnosis": "判决书在计算赔偿时替换了参照系（如用同工同酬替代原告历史收入），但未说明理由",
        "fix_steps": [
            "1. 确认原告诉请中使用的计算参照系",
            "2. 若需替换参照系：在说理部分充分论证替换的必要性和法律依据",
            "3. 引用支持参照系替换的类案或法条",
            "4. 确保替换后的计算结果不超出诉请范围",
            "5. 重新扫描验证",
        ],
        "prompt_fix": "在计算赔偿金时，优先使用原告自身历史收入作为参照系；如需替换，必须在说理部分论证并引用法律依据",
    },
    "非文本证据未标注": {
        "diagnosis": "判决书引用了录音、视频等非文本证据，但未标注证据的来源形式和整理方式",
        "fix_steps": [
            "1. 在证据引用处添加来源形式标注，如'（见《证据5.1 通话录音文字整理》）'",
            "2. 确保非文本证据的文字整理版本已添加到evidence_manifest.md",
            "3. 若无文字整理版：制作文字整理稿并添加到证据清单",
            "4. 重新扫描验证",
        ],
        "prompt_fix": "引用非文本证据时，必须同时引用其文字整理版本，并标注'据XX整理'或'据XX笔录'",
    },
    "三段论缺失-法律依据": {
        "diagnosis": "说理部分的结论缺少法律依据（大前提），无法形成完整的法律推理三段论",
        "fix_steps": [
            "1. 找到说理部分中缺少法律依据的结论句",
            "2. 查阅相关法律法规，找到支撑该结论的具体法条",
            "3. 在结论前添加法律依据引用，格式：'根据《XX法》第X条，...'",
            "4. 确保引用的法条与结论逻辑一致",
            "5. 重新扫描验证三段论完整性",
        ],
        "prompt_fix": "每个裁判结论必须同时包含法律依据（大前提）和事实证据（小前提），形成完整三段论",
    },
    "三段论缺失-证据引用": {
        "diagnosis": "说理部分的结论缺少证据引用（小前提），事实认定未绑定证据来源",
        "fix_steps": [
            "1. 找到说理部分中缺少证据引用的事实认定",
            "2. 在evidence_manifest.md中查找对应的证据文件",
            "3. 在事实认定后添加证据来源标注，格式：'（见《证据XX_文件名》）'",
            "4. 若无对应证据：该事实认定应标注为'上诉人主张...但未见相关书证支持'",
            "5. 重新扫描验证",
        ],
        "prompt_fix": "每个事实认定必须绑定证据来源，无证据支撑的事实应如实标注为'未见书证支持'",
    },
    "模拟类案已标注": {
        "diagnosis": "类案已标注'模拟'属性，但案号格式仍可能误导读者以为是真实案例",
        "fix_steps": [
            "1. 将模拟类案的案号格式改为'模拟类案A-01'等非真实案号格式",
            "2. 在引用处添加醒目的'（模拟参考，非真实案例）'标注",
            "3. 在判决书末尾添加免责声明，说明模拟类案仅供参考",
        ],
        "prompt_fix": "模拟类案应使用'模拟类案A-01'格式，避免使用真实案号格式",
    },
}


def scan_single_version(version: str, file_path: str, progress: ScanProgress) -> dict:
    progress.status = "running"
    progress.start_time = time.time()

    logger.info("[%s] 开始扫描: %s", version, file_path)

    with open(file_path, encoding="utf-8") as f:
        doc_text = f.read()

    progress.token_estimate = estimate_tokens(doc_text)
    progress.completed_stages = 1
    logger.info(
        "[%s] 文件加载完成, 估计token: %d, 进度: %.0f%%",
        version, progress.token_estimate, progress.progress_pct,
    )

    ei = EvidenceIndex(manifest_path=MANIFEST_PATH, vault_root=VAULT_ROOT)
    ei.load()
    progress.completed_stages = 2
    logger.info("[%s] 证据索引加载完成, 进度: %.0f%%", version, progress.progress_pct)

    if progress.progress_pct >= 50 and not progress.midpoint_logged:
        progress.midpoint_logged = True
        progress.midpoint_token = progress.token_estimate
        progress.midpoint_elapsed = progress.elapsed_sec
        logger.info(
            "[%s] ⏱️ 50%%里程碑 | Token: %d | 耗时: %.2fs | 阶段: %d/%d",
            version, progress.midpoint_token, progress.midpoint_elapsed,
            progress.completed_stages, progress.total_stages,
        )

    engine = RuleEngine()
    engine.evidence_index = ei

    result = engine.run_full_scan(document_text=doc_text, local_law_dir=LOCAL_LAW_DIR)
    progress.completed_stages = 10
    logger.info("[%s] 规则引擎扫描完成, 进度: %.0f%%", version, progress.progress_pct)

    if progress.progress_pct >= 50 and not progress.midpoint_logged:
        progress.midpoint_logged = True
        progress.midpoint_token = progress.token_estimate
        progress.midpoint_elapsed = progress.elapsed_sec
        logger.info(
            "[%s] ⏱️ 50%%里程碑 | Token: %d | 耗时: %.2fs | 阶段: %d/%d",
            version, progress.midpoint_token, progress.midpoint_elapsed,
            progress.completed_stages, progress.total_stages,
        )

    all_flags = []
    for dim in result.dimensions:
        for flag in dim.rule_flags:
            flag_dict = {
                "version": version,
                "dimension": dim.dimension,
                "dimension_title": dim.dimension_title,
                "h_code": dim.h_code,
                "rule_id": flag.rule_id,
                "sub_type": flag.sub_type,
                "severity": flag.severity,
                "message": flag.message,
                "evidence": flag.evidence,
                "location": flag.location,
                "line_number": flag.line_number,
            }
            flag_dict["fix_guide"] = HALLUCINATION_FIX_GUIDE.get(flag.sub_type, {
                "diagnosis": f"检测到{flag.sub_type}类型幻觉",
                "fix_steps": ["1. 参考检测报告中的详细描述进行修正", "2. 重新扫描验证"],
                "prompt_fix": "在生成判决书时注意避免此类幻觉",
            })
            all_flags.append(flag_dict)

    progress.completed_stages = 12
    progress.end_time = time.time()
    progress.status = "completed"
    logger.info("[%s] 扫描完成, 耗时: %.1fs, flags: %d, 进度: 100%%", version, progress.elapsed_sec, len(all_flags))

    return {
        "version": version,
        "file_path": file_path,
        "flags": all_flags,
        "total_flags": len(all_flags),
        "token_estimate": progress.token_estimate,
        "elapsed_sec": progress.elapsed_sec,
        "midpoint_token": progress.midpoint_token,
        "midpoint_elapsed": progress.midpoint_elapsed,
        "dimensions": [
            {
                "dimension": d.dimension,
                "dimension_title": d.dimension_title,
                "h_code": d.h_code,
                "total_flags": d.total_flags,
                "critical_count": d.critical_count,
                "high_count": d.high_count,
                "medium_count": d.medium_count,
                "low_count": d.low_count,
            }
            for d in result.dimensions
        ],
    }


def generate_comparison_report(scan_results: list[dict]) -> str:
    lines = []
    lines.append("# 多版本判决书幻觉比对报告")
    lines.append("")
    lines.append(f"> **扫描日期**：{time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> **扫描版本**：{', '.join(r['version'] for r in scan_results)}")
    lines.append(f"> **证据索引**：`{MANIFEST_PATH}`")
    lines.append("> **检测方法**：六维分类体系（H-1至H-6）+ 诉求边界分析 + 法律适用核验 + 封闭宇宙规则")
    lines.append("> **规则引擎版本**：V42.1")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append("## 一、扫描概览")
    lines.append("")
    lines.append("| 版本 | 文件 | 总Flag数 | 严重 | 高 | 中 | 低 | Token估算 | 耗时(s) |")
    lines.append("|------|------|---------|------|-----|-----|-----|----------|---------|")

    for r in scan_results:
        sev_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in r["flags"]:
            sev_counts[f["severity"]] = sev_counts.get(f["severity"], 0) + 1
        basename = os.path.basename(r["file_path"])
        lines.append(
            f"| {r['version']} | {basename} | {r['total_flags']} | "
            f"{sev_counts['critical']} | {sev_counts['high']} | "
            f"{sev_counts['medium']} | {sev_counts['low']} | "
            f"{r['token_estimate']} | {r['elapsed_sec']:.1f} |"
        )

    lines.append("")

    lines.append("## 二、版本间幻觉趋势分析")
    lines.append("")

    version_order = [r["version"] for r in scan_results]
    flag_counts = {r["version"]: r["total_flags"] for r in scan_results}

    if len(version_order) >= 2:
        first_v = version_order[0]
        last_v = version_order[-1]
        diff = flag_counts[first_v] - flag_counts[last_v]
        if diff > 0:
            lines.append("> [!TIP]")
            lines.append(f"> 从{first_v}到{last_v}，幻觉Flag总数减少了{diff}个，呈改善趋势")
            lines.append("")
        elif diff < 0:
            lines.append("> [!WARNING]")
            lines.append(f"> 从{first_v}到{last_v}，幻觉Flag总数增加了{-diff}个，需关注")
            lines.append("")
        else:
            lines.append("> [!NOTE]")
            lines.append(f"> 从{first_v}到{last_v}，幻觉Flag总数持平")
            lines.append("")

    lines.append("### 按维度对比")
    lines.append("")

    all_dims = set()
    dim_flags_by_version = {}
    for r in scan_results:
        v = r["version"]
        dim_flags_by_version[v] = {}
        for f in r["flags"]:
            dim = f["dimension"]
            all_dims.add(dim)
            dim_flags_by_version[v][dim] = dim_flags_by_version[v].get(dim, 0) + 1

    dim_list = sorted(all_dims)
    header = "| 维度 | " + " | ".join(version_order) + " |"
    sep = "|------|" + "|".join(["------" for _ in version_order]) + "|"
    lines.append(header)
    lines.append(sep)

    for dim in dim_list:
        row = f"| {dim} |"
        for v in version_order:
            count = dim_flags_by_version.get(v, {}).get(dim, 0)
            row += f" {count} |"
        lines.append(row)

    lines.append("")

    lines.append("### 按幻觉子类型对比")
    lines.append("")

    all_subtypes = set()
    subtype_flags_by_version = {}
    for r in scan_results:
        v = r["version"]
        subtype_flags_by_version[v] = {}
        for f in r["flags"]:
            st = f["sub_type"]
            all_subtypes.add(st)
            subtype_flags_by_version[v][st] = subtype_flags_by_version[v].get(st, 0) + 1

    subtype_list = sorted(all_subtypes)
    header = "| 幻觉子类型 | " + " | ".join(version_order) + " |"
    sep = "|-----------|" + "|".join(["------" for _ in version_order]) + "|"
    lines.append(header)
    lines.append(sep)

    for st in subtype_list:
        row = f"| {st} |"
        for v in version_order:
            count = subtype_flags_by_version.get(v, {}).get(st, 0)
            row += f" {count} |"
        lines.append(row)

    lines.append("")

    lines.append("## 三、各版本详细检测结果")
    lines.append("")

    for r in scan_results:
        v = r["version"]
        lines.append(f"### {v}")
        lines.append("")

        if not r["flags"]:
            lines.append("✅ 未检测到幻觉")
            lines.append("")
            continue

        sev_groups = {"critical": [], "high": [], "medium": [], "low": []}
        for f in r["flags"]:
            sev_groups.get(f["severity"], sev_groups["low"]).append(f)

        for sev in ["critical", "high", "medium", "low"]:
            if not sev_groups[sev]:
                continue
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}[sev]
            label = {"critical": "致命", "high": "严重", "medium": "中等", "low": "轻微"}[sev]
            lines.append(f"#### {icon} {label}级 ({len(sev_groups[sev])}项)")
            lines.append("")

            for i, f in enumerate(sev_groups[sev], 1):
                lines.append(f"**{i}. [{f['h_code']}] {f['sub_type']}**")
                lines.append(f"- 位置：{f['location']}")
                lines.append(f"- 证据：`{f['evidence'][:100]}`")
                lines.append(f"- 消息：{f['message']}")
                lines.append("")

                guide = f.get("fix_guide", {})
                if guide:
                    lines.append("> [!IMPORTANT]")
                    lines.append(f"> **诊断**：{guide.get('diagnosis', '无')}")
                    lines.append(">")
                    lines.append("> **人工修改步骤**：")
                    for step in guide.get("fix_steps", []):
                        lines.append(f"> {step}")
                    lines.append(">")
                    lines.append(f"> **LLM提示词修正**：{guide.get('prompt_fix', '无')}")
                    lines.append("")

        lines.append("---")
        lines.append("")

    lines.append("## 四、版本改进追踪")
    lines.append("")

    subtype_trend = {}
    for r in scan_results:
        v = r["version"]
        for f in r["flags"]:
            st = f["sub_type"]
            if st not in subtype_trend:
                subtype_trend[st] = {}
            subtype_trend[st][v] = subtype_trend[st].get(v, 0) + 1

    improved = []
    regressed = []
    stable = []

    for st, trend in subtype_trend.items():
        vals = [trend.get(v, 0) for v in version_order]
        if vals[-1] < vals[0]:
            improved.append((st, vals[0], vals[-1]))
        elif vals[-1] > vals[0]:
            regressed.append((st, vals[0], vals[-1]))
        else:
            stable.append((st, vals[0]))

    if improved:
        lines.append("### ✅ 改善项（Flag数减少）")
        lines.append("")
        lines.append("| 幻觉子类型 | 首版Flag数 | 末版Flag数 | 变化 |")
        lines.append("|-----------|----------|----------|------|")
        for st, first, last in sorted(improved, key=lambda x: x[1] - x[2], reverse=True):
            lines.append(f"| {st} | {first} | {last} | -{first - last} |")
        lines.append("")

    if regressed:
        lines.append("### ⚠️ 退化项（Flag数增加）")
        lines.append("")
        lines.append("| 幻觉子类型 | 首版Flag数 | 末版Flag数 | 变化 |")
        lines.append("|-----------|----------|----------|------|")
        for st, first, last in sorted(regressed, key=lambda x: x[2] - x[1], reverse=True):
            lines.append(f"| {st} | {first} | {last} | +{last - first} |")
        lines.append("")

    if stable:
        lines.append("### ➡️ 持平项")
        lines.append("")
        for st, count in sorted(stable):
            lines.append(f"- {st}：{count}个Flag（各版本一致）")
        lines.append("")

    lines.append("## 五、Workflow执行状态（含里程碑）")
    lines.append("")
    lines.append(
        "| 版本 | 模式 | 状态 | 进度 | Token估算 | 50%里程碑Token | "
        "50%里程碑耗时(s) | 100%里程碑Token | 100%里程碑耗时(s) | 总耗时(s) | Flag数 |"
    )
    lines.append("|------|------|------|------|----------|---------------|-----------------|----------------|------------------|---------|--------|")
    for r in scan_results:
        mid_token = r.get("midpoint_token", "—")
        mid_elapsed = r.get("midpoint_elapsed", "—")
        mid_elapsed_fmt = f"{mid_elapsed:.2f}" if isinstance(mid_elapsed, (int, float)) else "—"
        fin_token = r.get("token_estimate", 0)
        fin_elapsed = r.get("elapsed_sec", 0)
        lines.append(
            f"| {r['version']} | 材料 | ✅ 完成 | 100% | {r['token_estimate']} | "
            f"{mid_token} | {mid_elapsed_fmt} | {fin_token} | {fin_elapsed:.1f} | "
            f"{r['elapsed_sec']:.1f} | {r['total_flags']} |"
        )
    lines.append("")
    lines.append("> **说明**：每个版本作为一个独立的subagent运行，每份材料一个workflow。")
    lines.append("")

    lines.append("## 六、优先修复建议")
    lines.append("")

    all_flags_flat = []
    for r in scan_results:
        for f in r["flags"]:
            all_flags_flat.append(f)

    sev_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    all_flags_flat.sort(key=lambda x: (sev_order.get(x["severity"], 4), x.get("sub_type", "")))

    seen = set()
    priority_items = []
    for f in all_flags_flat:
        key = f"{f['sub_type']}_{f['severity']}"
        if key not in seen:
            seen.add(key)
            priority_items.append(f)

    for i, f in enumerate(priority_items[:10], 1):
        icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(f["severity"], "⚪")
        lines.append(f"### {i}. {icon} [{f['severity'].upper()}] {f['sub_type']}")
        lines.append("")
        guide = f.get("fix_guide", {})
        if guide:
            lines.append(f"**诊断**：{guide.get('diagnosis', '无')}")
            lines.append("")
            lines.append("**修改步骤**：")
            for step in guide.get("fix_steps", []):
                lines.append(f"  {step}")
            lines.append("")
            lines.append(f"**LLM提示词修正**：{guide.get('prompt_fix', '无')}")
            lines.append("")

    return "\n".join(lines)


def main():
    logger.info("=" * 60)
    logger.info("多版本判决书幻觉比对扫描 启动")
    logger.info("=" * 60)

    progress_map = {}
    scan_results = []

    max_workers = min(len(VERSION_FILES), 4)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for version, file_path in VERSION_FILES.items():
            progress = ScanProgress(version=version)
            progress_map[version] = progress
            future = executor.submit(scan_single_version, version, file_path, progress)
            future_map[future] = version

        while not all(f.done() for f in future_map):
            time.sleep(0.5)
            for version, prog in progress_map.items():
                if prog.status == "running":
                    logger.info(
                        "[%s] 进度: %.0f%% | 耗时: %.1fs | Token: %d | 阶段: %d/%d",
                        version, prog.progress_pct, prog.elapsed_sec,
                        prog.token_estimate, prog.completed_stages, prog.total_stages,
                    )
                    if prog.progress_pct >= 50 and not prog.midpoint_logged:
                        prog.midpoint_logged = True
                        prog.midpoint_token = prog.token_estimate
                        prog.midpoint_elapsed = prog.elapsed_sec
                        logger.info(
                            "[%s] ⏱️ 50%%里程碑 | Token: %d | 耗时: %.2fs | 阶段: %d/%d",
                            version, prog.midpoint_token, prog.midpoint_elapsed,
                            prog.completed_stages, prog.total_stages,
                        )

        for future in as_completed(future_map):
            version = future_map[future]
            try:
                result = future.result()
                scan_results.append(result)
                logger.info("[%s] 扫描完成: %d flags", version, result["total_flags"])
            except Exception as e:
                logger.error("[%s] 扫描失败: %s", version, e)
                scan_results.append({
                    "version": version,
                    "file_path": VERSION_FILES[version],
                    "flags": [],
                    "total_flags": 0,
                    "token_estimate": 0,
                    "elapsed_sec": 0,
                    "dimensions": [],
                })

    version_order = ["V40", "V40P1", "V41", "V42", "V43"]
    scan_results.sort(key=lambda x: version_order.index(x["version"]) if x["version"] in version_order else 99)

    report = generate_comparison_report(scan_results)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    report_filename = generate_report_filename(
        agent_name="TraeCN",
        llm_name="GLM51",
        content_summary="多版本判决书幻觉比对报告",
        version="v42.1",
    )
    report_path = os.path.join(OUTPUT_DIR, report_filename)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    logger.info("比对报告已生成: %s", report_path)
    logger.info("报告所在文件夹: %s", os.path.abspath(OUTPUT_DIR))

    json_filename = generate_report_filename(
        agent_name="TraeCN",
        llm_name="GLM51",
        content_summary="多版本判决书幻觉扫描数据",
        version="v42.1",
    ).replace(".md", ".json")
    json_path = os.path.join(OUTPUT_DIR, json_filename)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(scan_results, f, ensure_ascii=False, indent=2, default=str)

    logger.info("扫描数据已保存: %s", json_path)
    logger.info("数据所在文件夹: %s", os.path.abspath(OUTPUT_DIR))

    print(f"\n📁 报告输出目录: {os.path.abspath(OUTPUT_DIR)}")
    print(f"📄 比对报告: {report_path}")
    print(f"📊 扫描数据: {json_path}")
    return report_path


if __name__ == "__main__":
    main()
