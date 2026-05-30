# ruff: noqa: E501
"""报告生成器 — 生成结构化幻觉检测报告（V39标准版）。

桥接架构：不调用任何LLM。
对齐V39_幻觉检测报告_20260525.md的详细程度和格式规范。
每个幻觉项均输出结构化分析卡片，包含状态/幻觉类型/风险等级/问题分析/修正建议。
新增：逐项对比表、计算核验、策略评估、与既往版本改进对照、
      非文本证据穿透验证表、汇总统计、最需优先修正排名、继续有效的优势。
v2.0新增：GitHub Alerts格式（Typora兼容）、交叉验证章节、
      法律知识库验证章节、报告文件名规范、法律原则引用。
v2.1新增：JSON/CSV格式导出功能。
"""

import csv
import io
import json
import logging
import re
from datetime import datetime

from .models import HallucinationDetectionResult

logger = logging.getLogger("legal-hallucination")

SEV_ICONS = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢", "info": "ℹ️"}
SEV_LABELS = {"critical": "致命", "high": "严重", "medium": "中等", "low": "轻微", "info": "提示"}
SEV_STATUS = {"critical": "确认幻觉", "high": "确认幻觉", "medium": "疑似幻觉", "low": "提示", "info": "提示"}


REPORT_FILENAME_PATTERN = re.compile(
    r"^[A-Za-z0-9\-_.]+_[A-Za-z0-9\-_.]+_.+_[vV]\d+(\.\d+)*_\d{8}\.md$"
)

REPORT_FILENAME_SPEC = (
    "文件名格式规范：{AI Agent名}_{LLM名}_{简体中文内容描述}_{版本号}_{YYYYMMDD}.md\n"
    "示例：TraeCN_GLM-5.1_法律文书幻觉检测报告_v2.0_20260529.md\n"
    "约束：Agent名和LLM名为英文/数字/连字符/点号，内容描述为简体中文，版本号以v开头，日期8位数字"
)


def generate_report_filename(
    agent_name: str = "TraeCN",
    llm_name: str = "",
    content_summary: str = "法律文书幻觉检测报告",
    version: str = "v2.0",
    date: str = "",
) -> str:
    if not agent_name or not re.match(r"^[A-Za-z0-9\-_.]+$", agent_name):
        raise ValueError(f"agent_name必须为英文/数字/连字符/点号组合，当前值: '{agent_name}'。{REPORT_FILENAME_SPEC}")
    if not llm_name or not re.match(r"^[A-Za-z0-9\-_.]+$", llm_name):
        raise ValueError(f"llm_name必须为英文/数字/连字符/点号组合且不可为空，当前值: '{llm_name}'。{REPORT_FILENAME_SPEC}")
    if not content_summary or not re.search(r"[\u4e00-\u9fff]", content_summary):
        raise ValueError(f"content_summary必须包含简体中文描述，当前值: '{content_summary}'。{REPORT_FILENAME_SPEC}")
    if not re.match(r"^[vV]\d+(\.\d+)*$", version):
        raise ValueError(f"version必须以v/V开头后跟数字版本号，当前值: '{version}'。{REPORT_FILENAME_SPEC}")
    if not date:
        date = datetime.now().strftime("%Y%m%d")
    if not re.match(r"^\d{8}$", date):
        raise ValueError(f"date必须为8位数字YYYYMMDD格式，当前值: '{date}'。{REPORT_FILENAME_SPEC}")

    safe_summary = re.sub(r'[\\/:*?"<>|\s]+', '_', content_summary.strip())
    filename = f"{agent_name}_{llm_name}_{safe_summary}_{version}_{date}.md"

    if not REPORT_FILENAME_PATTERN.match(filename):
        raise ValueError(f"生成的文件名不符合规范: '{filename}'。{REPORT_FILENAME_SPEC}")

    return filename


class ReportBuilder:
    def build_report(
        self,
        result: HallucinationDetectionResult,
        previous_versions: list[dict] | None = None,
    ) -> str:
        lines = []

        lines.append("# 法律文书幻觉全方位检测报告")
        lines.append("")
        lines.append(f"> **检测文件**：`{result.document_path or '未指定'}`")
        lines.append(f"> **证据索引**：`{result.manifest_path or '未加载'}`")
        lines.append(f"> **检测日期**：{result.detection_time}")
        lines.append("> **检测方法**：六维分类体系（H-1至H-6）+ 诉求边界分析 + 法律适用核验 + 封闭宇宙规则 + 策略透镜 + 交叉验证 + 法律知识库")
        lines.append(f"> **AI Agent**：{result.agent_name}")
        if result.llm_name:
            lines.append(f"> **LLM**：{result.llm_name}")
        lines.append(f"> **报告版本**：{result.report_version}")
        lines.append("")
        lines.append("---")
        lines.append("")

        self._build_overview(lines, result)
        self._build_structure_check(lines, result)
        self._build_claim_consistency(lines, result)
        self._build_calculation_verification(lines, result)
        self._build_cross_reference(lines, result)
        self._build_law_kb_verification(lines, result)
        self._build_h1_section(lines, result)
        self._build_h2_section(lines, result)
        self._build_h3_section(lines, result)
        self._build_h4_section(lines, result)
        self._build_h5_section(lines, result)
        self._build_h6_section(lines, result)
        self._build_nontext_evidence_table(lines, result)
        self._build_summary_stats(lines, result)
        self._build_version_comparison(lines, result, previous_versions or [])
        self._build_suggestions(lines, result)
        self._build_priority_fixes(lines, result)
        self._build_checklist(lines, result)

        lines.append("---")
        lines.append("")
        lines.append(f"*本报告由法律文书幻觉检测系统 v2.0 自动生成，检测时间：{result.detection_time}*")
        lines.append("*报告遵循封闭宇宙规则与诉审一致原则，所有检测项均可追溯至证据索引清单。*")
        lines.append("*基于六维分类体系（H-1至H-6）+ 诉求边界分析 + 法律适用核验 + 策略透镜 + 交叉验证 + 法律知识库*")

        report = "\n".join(lines)
        logger.info("build_report: report length=%d", len(report))
        return report

    def _sev(self, severity: str) -> str:
        return SEV_LABELS.get(severity, severity)

    def _icon(self, severity: str) -> str:
        return SEV_ICONS.get(severity, "⚪")

    def _status(self, severity: str) -> str:
        return SEV_STATUS.get(severity, "待定")

    def _write_card(self, lines: list, idx: int, h_code: str, sub_type: str,
                    severity: str, analysis: str, location: str = "",
                    original_text: str = "", suggestion: str = "",
                    strategy_eval: str = "", comparison_note: str = ""):
        lines.append(f"#### 幻觉 {h_code}-{idx}：{sub_type}")
        lines.append("")
        status = self._status(severity)
        if severity in ("critical", "high"):
            status_label = f"**[{status}]**"
        elif severity == "medium":
            status_label = f"**[{status}]**"
        else:
            status_label = f"[{status}]"

        lines.append(f"> **状态**：{status_label}")
        lines.append(f"> **幻觉类型**：{h_code}（{sub_type}）")
        lines.append(f"> **风险等级**：**{self._sev(severity)}**")
        lines.append(f"> **问题分析**：{analysis}")
        if location:
            lines.append(f"> **出现位置**：{location}")
        if original_text:
            lines.append(f"> **原文**：{original_text}")
        if strategy_eval:
            lines.append(f"> **策略评估**：{strategy_eval}")
        if comparison_note:
            lines.append(f"> **与前版对比**：{comparison_note}")
        lines.append("")
        lines.append("##### 修正建议")
        lines.append("")
        if suggestion:
            lines.append(f"- {self._icon(severity)} {suggestion}")
        else:
            lines.append(f"- {self._icon(severity)} 请根据上述分析修正该处表述。")
        lines.append("")

    def _build_overview(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 一、总体评价")
        lines.append("")

        grade_desc = {
            "A": "极低风险：文书几乎无幻觉痕迹",
            "B": "低风险：存在少量轻微幻觉，不影响裁判结论",
            "C": "中风险：存在多处幻觉，可能影响裁判公正性",
            "D": "高风险：幻觉密集，裁判结论可信度存疑",
            "F": "极高风险：幻觉泛滥，文书基本不可信",
        }

        lines.append(f"**幻觉风险评分**：{result.hallucination_score:.1f}/100")
        lines.append(f"**风险等级**：{result.risk_grade} — {grade_desc.get(result.risk_grade, result.risk_description)}")
        lines.append(f"**检出标志总数**：{result.total_flags}")
        lines.append("")

        critical_total = sum(d.critical_count for d in result.dimensions)
        high_total = sum(d.high_count for d in result.dimensions)
        medium_total = sum(d.medium_count for d in result.dimensions)
        low_total = sum(d.low_count for d in result.dimensions)

        if critical_total > 0:
            lines.append(f"⚠️ **致命级幻觉 {critical_total} 处**，需立即修复。")
        if high_total > 0:
            lines.append(f"⚠️ **严重级幻觉 {high_total} 处**，需优先修复。")
        if medium_total > 0:
            lines.append(f"⚡ **中等级幻觉 {medium_total} 处**，建议修复。")
        if low_total > 0:
            lines.append(f"💡 **轻微级提示 {low_total} 处**，可择优修复。")
        if critical_total == 0 and high_total == 0:
            lines.append("✅ 未检测到致命或严重级幻觉，文书整体质量良好。")
        lines.append("")

        lines.append("### 各维度检测汇总")
        lines.append("")
        lines.append("| 维度 | 标题 | 致命 | 严重 | 中等 | 轻微 | 总计 |")
        lines.append("|:---|:---|:---:|:---:|:---:|:---:|:---:|")
        for dim in result.dimensions:
            lines.append(
                f"| {dim.h_code} | {dim.dimension_title} | "
                f"{dim.critical_count} | {dim.high_count} | "
                f"{dim.medium_count} | {dim.low_count} | {dim.total_flags} |"
            )
        lines.append("")

        lines.append("### 检测项统计")
        lines.append("")
        lines.append("| 检测项 | 数量 | 说明 |")
        lines.append("|:---|:---:|:---|")
        lines.append(f"| 文书结构缺失 | {len(result.structure_issues)} | 缺少必需的四段式标准标题 |")
        lines.append(f"| 引注欺诈（H-1） | {len(result.citation_frauds)} | 引用的证据源不在证据索引清单中 |")
        lines.append(f"| 事实来源未绑定（H-1） | {len(result.fact_source_issues)} | 事实陈述未标注证据来源 |")
        lines.append(f"| 法条引用问题（H-2） | {len(result.law_citation_issues)} | 已废止法律/格式不规范/方法论替换 |")
        lines.append(f"| 三段论断裂（H-3） | {len(result.syllogism_breaks)} | 说理缺失法律依据或证据锚点 |")
        lines.append(f"| 主观修辞（H-4） | {len([r for r in result.rhetoric_items if not r.is_exception])} | 不符合司法中立原则的主观修辞 |")
        lines.append(f"| 诉请边界突破（H-5） | {len(result.claim_violations)} | 判决超出起诉状/上诉状诉请范围 |")
        lines.append(f"| 仲裁时效问题（H-2） | {len(result.time_bar_issues)} | 劳动争议仲裁时效超期 |")
        lines.append(f"| 法律方法论替换（H-5） | {len(result.methodology_replacements)} | 判决书替换了起诉状的法律方法论 |")
        lines.append(f"| 利息基数提取 | {len(result.interest_base_items)} | 以XX元为基数的利息计算 |")
        lines.append(f"| 计算核验问题 | {len(result.calculation_issues)} | 加班工资/二倍工资/金额汇总等计算错误 |")
        lines.append(f"| 诉请对比项 | {len(result.claim_comparisons)} | 判决金额与诉请金额逐项对比 |")
        lines.append("")

        lines.append("### 检测方法说明")
        lines.append("")
        lines.append("本报告采用以下检测方法：")
        lines.append("- **规则引擎**：基于正则表达式、模式匹配和数值比对的确定性检测，零LLM依赖，即时返回")
        lines.append("- **封闭宇宙规则**：大模型生成的每一句法律事实，必须完全源自证据索引清单中所列出的真实文件")
        lines.append("- **诉审一致原则**：判决主文所支持的项目和金额，必须严格限制在起诉状/上诉状界定的范畴内")
        lines.append("- **三段论完整性**：说理部分每一项支持或驳回结论，必须同时包含法律依据（大前提）和证据锚点（小前提）")
        lines.append("- **策略透镜**：从劳动者代理人/当事人视角评估检测项的策略价值，区分\u201c严格幻觉\u201d与\u201c策略性选择\u201d")
        lines.append("- **递减惩罚评分**：同一规则首次命中按全权重计分，后续命中按30%权重计分，避免评分虚高")
        lines.append("- **计算核验**：对加班工资、二倍工资差额等关键计算项进行公式还原和数值校验")
        lines.append("")

    def _build_structure_check(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 二、文书结构检测")
        lines.append("")
        lines.append("检测文书是否包含四个必需的标准段落标题（强制文书结构协议）：")
        lines.append("- `# 一、当事人的诉讼请求与主张`")
        lines.append("- `# 二、本院查明事实`")
        lines.append("- `# 三、本院认为（说理部分）`")
        lines.append("- `# 四、判决如下（判决主文）`")
        lines.append("")

        if result.structure_issues:
            idx = 0
            for si in result.structure_issues:
                idx += 1
                self._write_card(
                    lines, idx, si.h_code, "文书结构缺失",
                    si.severity,
                    f"缺少必需的段落标题\u201c{si.heading}\u201d。"
                    f"为了让自动化审计底座能够无误解析，"
                    f"生成的判决书草稿必须严格包含且仅包含四个标准Markdown一级标题。"
                    f"当前缺失\u201c{si.heading}\u201d，将导致后续检测环节无法定位对应段落。",
                    suggestion=f"补充段落标题：{si.heading}",
                )
        else:
            lines.append("✅ 文书结构完整，四个必需段落标题均已包含。")
            lines.append("")

    def _build_claim_consistency(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 三、与起诉状事实一致性核验")
        lines.append("")
        lines.append("将判决主文各项给付与起诉状/上诉状原始请求逐项对照，"
                      "检测是否存在项目越权或金额冒顶。"
                      "诉审一致原则要求：判决支持的项目不得超出诉请范围；"
                      "判决支持的金额在数理上必须小于或等于诉请的最大上限。")
        lines.append("")

        if result.claim_comparisons:
            lines.append("### 判决金额与诉请金额逐项对比（精确版）")
            lines.append("")
            lines.append("| 序号 | 给付项目 | 判决金额 | 诉请描述 | 诉请上限 | 偏差倍数 | 性质 | 策略评估 |")
            lines.append("|:---:|:---|---:|:---|---:|:---:|:---|:---|")
            for idx, cc in enumerate(result.claim_comparisons, 1):
                ratio_str = f"{cc.deviation_ratio:.2f}\u00d7" if cc.deviation_ratio > 0 else "\u2014"
                strategy_val = cc.strategy_eval[:30] if cc.strategy_eval else "\u2014"
                lines.append(
                    f"| {idx} | {cc.item_name} | {cc.judgment_amount:,.2f} | "
                    f"{cc.claim_description} | {cc.claim_amount:,.2f} | "
                    f"{ratio_str} | {cc.nature} | {strategy_val} |"
                )
            lines.append("")
            lines.append("> **说明**：偏差倍数 = 判决金额 \u00f7 诉请上限。"
                          "当偏差倍数 > 1.0 时，判决金额超出诉请上限，违反诉审一致原则。"
                          "策略评估从劳动者代理人视角分析超出部分的法理自洽性。")
            lines.append("")

        if result.claim_violations:
            lines.append("### 诉请边界突破项")
            lines.append("")
            lines.append("| 序号 | 给付项目 | 判决金额 | 诉请项目 | 诉请上限 | 超出金额 | 偏差倍数 | 性质 |")
            lines.append("|:---:|:---|---:|:---|---:|---:|:---:|:---|")
            for idx, cv in enumerate(result.claim_violations, 1):
                if cv.claim_max > 0 and cv.excess_amount > 0:
                    ratio = cv.judgment_amount / cv.claim_max
                    ratio_str = f"{ratio:.2f}\u00d7"
                    if ratio >= 3.0:
                        nature = "**显著超出**"
                    elif ratio >= 1.5:
                        nature = "**明显超出**"
                    elif ratio > 1.0:
                        nature = "轻微超出"
                    else:
                        nature = "一致"
                elif cv.violation_type == "项目越权":
                    ratio_str = "\u2014"
                    nature = "**项目越权**"
                else:
                    ratio_str = "1.00\u00d7"
                    nature = "一致"
                lines.append(
                    f"| {idx} | {cv.judgment_item} | {cv.judgment_amount:,.2f} | "
                    f"{cv.matched_claim or '无对应'} | "
                    f"{cv.claim_max:,.2f} | {cv.excess_amount:,.2f} | {ratio_str} | {nature} |"
                )
            lines.append("")

            lines.append("### 逐项详细分析")
            lines.append("")
            idx = 0
            for cv in result.claim_violations:
                idx += 1
                if cv.violation_type == "项目越权":
                    analysis = (f"判决项目\u201c{cv.judgment_item}\u201d在起诉状/上诉状中无对应诉请，"
                               f"属于项目越权裁判。判决金额{cv.judgment_amount:,.2f}元"
                               f"系凭空创设的给付项目，违反诉审一致原则。"
                               f"起诉状中不存在该诉请项目，判决书不得自行创设新的给付类型。")
                    strategy_eval = ("若该给付项目系二审中基于新事实或变更诉求而产生，"
                                    "应在判决理由中明确说明其诉请基础，"
                                    "并确认是否存在变更诉求申请书。"
                                    "若无变更诉求，则该项目属于超诉请裁判，必须删除。")
                    suggestion = (f"删除判决项目\u201c{cv.judgment_item}\u201d，"
                                  f"或确认是否存在变更诉求申请书。"
                                  f"若无变更诉求，则该项目属于超诉请裁判，必须删除。")
                elif cv.violation_type == "金额冒顶":
                    ratio = cv.judgment_amount / cv.claim_max if cv.claim_max > 0 else 0
                    analysis = (f"判决金额{cv.judgment_amount:,.2f}元超出诉请上限"
                               f"{cv.claim_max:,.2f}元，超出{cv.excess_amount:,.2f}元"
                               f"（偏差{ratio:.2f}倍）。"
                               f"这属于金额冒顶，违反诉审一致原则中"
                               f"\u201c判决支持的金额必须\u2264诉请最大上限\u201d的硬性要求。"
                               f"即使法理上成立，也必须在判决理由中充分论证超出部分的合法性和必要性。")
                    if ratio >= 3.0:
                        strategy_eval = (f"从劳动者代理人视角，若能通过同工同酬等法定方法"
                                        f"确立更高的计算基数，则超出部分在法理上是自洽的。"
                                        f"问题不在于法理，而在于：起诉状本身使用的是更保守的算法，"
                                        f"判决书应采用与起诉状一致的逻辑起点再行扩展，"
                                        f"而非完全替换方法论。偏差{ratio:.2f}倍属于显著超出，"
                                        f"建议在判决理由中增加过渡说明，"
                                        f"明确新旧算法的差异和采纳新算法的理由。")
                    elif ratio >= 1.5:
                        strategy_eval = (f"偏差{ratio:.2f}倍属于明显超出。"
                                        f"建议在判决理由中说明计算基数调整的依据，"
                                        f"并确认是否存在变更诉求申请书。")
                    else:
                        strategy_eval = ""
                    suggestion = (f"将判决金额调整至诉请上限{cv.claim_max:,.2f}元以内，"
                                  f"或在判决理由中充分论证超出部分的合法性和必要性，"
                                  f"并确认是否存在变更诉求申请书。"
                                  f"若保留超出部分，需明确说明新旧算法的差异和采纳新算法的理由。")
                else:
                    analysis = f"诉请边界突破：{cv.violation_type}"
                    suggestion = "修正该处判决，确保诉审一致。"
                    strategy_eval = ""

                self._write_card(
                    lines, idx, "H5", cv.violation_type,
                    "high", analysis,
                    original_text=f"{cv.judgment_item}: {cv.judgment_amount:,.2f}元",
                    suggestion=suggestion,
                    strategy_eval=strategy_eval,
                )
        elif not result.claim_comparisons:
            lines.append("✅ 判决主文未超出诉请范围，诉审一致原则得到遵守。")
            lines.append("")

        if result.methodology_replacements:
            lines.append("### 法律方法论替换检测")
            lines.append("")
            lines.append("以下检测到判决书中的法律方法论与起诉状不一致，可能构成方法论替换：")
            lines.append("")
            lines.append("| 序号 | 诉请项目 | 起诉状依据 | 判决书依据 | 替换类型 | 严重度 |")
            lines.append("|:---:|:---|:---|:---|:---|:---:|")
            for idx, mr in enumerate(result.methodology_replacements, 1):
                lines.append(
                    f"| {idx} | {mr.claim_item} | {mr.claim_law_basis} | "
                    f"{mr.judgment_law_basis} | {mr.replacement_type} | "
                    f"{self._sev(mr.severity)} |"
                )
            lines.append("")

            for idx, mr in enumerate(result.methodology_replacements, 1):
                analysis = (f"起诉状援引{mr.claim_law_basis}作为{mr.claim_item}的法律依据，"
                           f"判决书转而引用{mr.judgment_law_basis}。"
                           f"这属于{mr.replacement_type}。{mr.impact_analysis}")
                strategy_eval = ("若变更方法论系基于二审新事实或法律重新定性，"
                                "应在判决理由中明确论证变更的必要性。"
                                "若仅为计算便利而替换方法论，则需论证新旧方法论的经济效果差异。")
                suggestion = (f"明确说明为何不适用{mr.claim_law_basis}而采用{mr.judgment_law_basis}，"
                              f"论证新旧法律依据的区别及采用新依据的理由。"
                              f"若保留新方法论，需论证其与原方法论的经济效果差异。")
                self._write_card(
                    lines, idx, mr.h_code, f"方法论替换：{mr.replacement_type}",
                    mr.severity, analysis,
                    suggestion=suggestion,
                    strategy_eval=strategy_eval,
                )

    def _build_calculation_verification(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 四、关键计算项核验")
        lines.append("")
        lines.append("对判决书中的关键计算项（加班工资、二倍工资差额、利息基数等）"
                      "进行公式还原和数值校验，检测是否存在计算错误或逻辑循环。"
                      "大模型在数值计算方面存在固有缺陷，常出现计算错误、数字不一致、"
                      "逻辑循环等问题，因此所有涉及金额的计算项均需独立核验。")
        lines.append("")

        if result.interest_base_items:
            lines.append("### 利息基数提取与核验")
            lines.append("")
            lines.append("| 序号 | 基数原文 | 基数金额 | 利率说明 | 行号 |")
            lines.append("|:---:|:---|---:|:---|:---:|")
            for idx, ib in enumerate(result.interest_base_items, 1):
                lines.append(
                    f"| {idx} | {ib.base_text} | {ib.base_amount:,.2f} | "
                    f"{ib.rate_text or '未提取'} | {ib.line_number} |"
                )
            lines.append("")
            lines.append("> **核验说明**：利息基数金额应与判决主文中对应项目的给付金额一致。"
                          "若基数金额与主文金额不符，可能存在计算错误。"
                          "同时需注意：将推定性的、尚未实际发生的奖金分摊纳入二倍工资基数，"
                          "存在逻辑循环的风险——即\u201c本判决认定的奖金标准\u201d"
                          "被用于计算\u201c未签劳动合同的惩罚性赔偿\u201d，"
                          "而这两者在诉讼中是同一审级同时确定的。")
            lines.append("")

        if result.calculation_issues:
            lines.append("### 计算核验详细分析")
            lines.append("")
            lines.append("| 序号 | 计算项 | 问题类型 | 判决金额 | 预期金额 | 严重度 |")
            lines.append("|:---:|:---|:---|---:|---:|:---:|")
            for idx, ci in enumerate(result.calculation_issues, 1):
                lines.append(
                    f"| {idx} | {ci.item} | {ci.issue_type} | "
                    f"{ci.amount:,.2f} | {ci.expected:,.2f} | {self._sev(ci.severity)} |"
                )
            lines.append("")

            idx = 0
            for ci in result.calculation_issues:
                idx += 1
                if ci.issue_type == "计算错误":
                    analysis = (f"{ci.item}的判决金额{ci.amount:,.2f}元与按公式还原的预期金额"
                               f"{ci.expected:,.2f}元不符。"
                               f"还原公式：{ci.formula}。"
                               f"差额：{abs(ci.amount - ci.expected):,.2f}元。"
                               f"大模型在数值计算方面存在固有缺陷，"
                               f"所有涉及金额的计算项均需独立核验。")
                    suggestion = (f"核实{ci.item}的计算过程，确保公式和参数正确。"
                                  f"还原公式：{ci.formula}。"
                                  f"若参数来自证据，需核对证据中的原始数值。")
                elif ci.issue_type == "逻辑循环":
                    analysis = ci.message
                    suggestion = ("在判决理由中增加对推定收入纳入计算基数的专门论证，"
                                  "或提供保守路径和激进路径并行展示。")
                elif ci.issue_type == "参数缺失":
                    analysis = ci.message
                    suggestion = "补充缺失的计算参数，确保计算过程可还原。"
                elif ci.issue_type == "法条张冠李戴":
                    analysis = ci.message
                    suggestion = ("核实法条引用的适用场景，确保法条内容与引用目的匹配。"
                                  "若存在张冠李戴，替换为正确的法律依据。")
                else:
                    analysis = ci.message
                    suggestion = "核实该计算项的准确性。"

                self._write_card(
                    lines, idx, ci.h_code, ci.issue_type,
                    ci.severity, analysis,
                    location=f"行{ci.line_number}" if ci.line_number else "",
                    suggestion=suggestion,
                )

        h5_violations = result.claim_violations
        overtime_related = [cv for cv in h5_violations
                            if any(k in cv.judgment_item for k in ("加班", "工资差额", "二倍"))]
        if overtime_related:
            lines.append("### 加班工资与二倍工资计算核验")
            lines.append("")
            lines.append("以下项目涉及加班工资或二倍工资差额计算，需核验其公式是否正确：")
            lines.append("")
            for cv in overtime_related:
                lines.append(f"- **{cv.judgment_item}**：判决金额 {cv.judgment_amount:,.2f} 元"
                             f"（诉请上限 {cv.claim_max:,.2f} 元）")
            lines.append("")
            lines.append("> **核验公式**：加班工资 = 月工资 \u00f7 21.75 \u00d7 加班天数 \u00d7 倍率（"
                          "工作日延长1.5倍、休息日2倍、法定节假日3倍）。"
                          "二倍工资差额 = 月工资 \u00d7 未签合同月数（最长11个月）。"
                          "需核对判决书中使用的月工资基数、天数和倍率是否与证据一致。")
            lines.append("")

        if not result.interest_base_items and not result.calculation_issues and not overtime_related:
            lines.append("✅ 未检测到需要核验的关键计算项，或文书不包含相关计算。")
            lines.append("")

    def _build_cross_reference(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 五、交叉验证与原始文件核对")
        lines.append("")
        lines.append("将判决书中的事实陈述与证据材料、法条原文、案例信息进行交叉验证，"
                      "检测是否存在事实编造、金额不一致、日期不匹配等问题。"
                      "交叉验证是幻觉检测的核心环节，通过多源比对确保事实陈述的准确性。")
        lines.append("")

        cross_ref_issues = result.cross_ref_issues
        if cross_ref_issues:
            lines.append("### 交叉验证问题清单")
            lines.append("")
            lines.append("| 序号 | 来源类型 | 来源名称 | 匹配类型 | 严重度 | 差异描述 |")
            lines.append("|:---:|:---|:---|:---|:---:|:---|")
            for idx, issue in enumerate(cross_ref_issues, 1):
                lines.append(
                    f"| {idx} | {issue.get('source_type', '')} | "
                    f"{issue.get('source_name', '')} | "
                    f"{issue.get('match_type', '')} | "
                    f"{self._sev(issue.get('severity', 'medium'))} | "
                    f"{issue.get('discrepancy', '')[:60]} |"
                )
            lines.append("")

            idx = 0
            for issue in cross_ref_issues:
                idx += 1
                match_type = issue.get("match_type", "")
                if match_type == "未找到":
                    analysis = (f"判决书引用的{issue.get('source_type', '证据')}"
                               f"《{issue.get('source_name', '')}》在原始文件中未找到匹配，"
                               f"可能为杜撰的引注。该引注所支撑的事实陈述缺乏证据基础，"
                               f"违反封闭宇宙规则。")
                    suggestion = issue.get("suggestion", "删除该引注或替换为证据清单中的有效证据")
                elif match_type == "不一致":
                    analysis = (f"判决书陈述与{issue.get('source_type', '证据')}"
                               f"《{issue.get('source_name', '')}》的原始内容不一致。"
                               f"差异：{issue.get('discrepancy', '')}。"
                               f"可能存在事实编造或张冠李戴。")
                    suggestion = issue.get("suggestion", "核实原始文件内容，修正判决书中的陈述")
                elif match_type == "部分一致":
                    analysis = (f"判决书陈述与{issue.get('source_type', '证据')}"
                               f"《{issue.get('source_name', '')}》的原始内容部分一致，"
                               f"但存在细节偏差。差异：{issue.get('discrepancy', '')}。"
                               f"可能存在事实细节的杜撰或修改。")
                    suggestion = issue.get("suggestion", "核对原始文件，确保事实陈述准确")
                else:
                    analysis = f"交叉验证结果：{match_type}。{issue.get('discrepancy', '')}"
                    suggestion = issue.get("suggestion", "")

                self._write_card(
                    lines, idx, issue.get("h_code", "H-1"),
                    f"交叉验证-{match_type}",
                    issue.get("severity", "medium"),
                    analysis,
                    suggestion=suggestion,
                )

            lines.append("> [!WARNING]")
            lines.append("> 交叉验证发现不一致项，请务必核对原始文件后再行修改。")
            lines.append("> 人工审核时应将判决书陈述与原始证据逐字比对，确保无遗漏。")
            lines.append("")
        else:
            lines.append("> [!TIP]")
            lines.append("> 交叉验证未发现问题，或未启用交叉验证功能。")
            lines.append("> 建议在检测时提供法律法规目录路径以启用完整的交叉验证。")
            lines.append("")

    def _build_law_kb_verification(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 六、法律知识库验证")
        lines.append("")
        lines.append("利用法律法规数据库对判决书中的法条引用进行验证，"
                      "检测是否存在已废止法律、法条内容不一致、法条张冠李戴等问题。"
                      "法律知识库覆盖上位法、中位法、下位法，包括法律、行政法规、"
                      "部门规章、司法解释、地方法规等各层级。")
        lines.append("")

        law_verifications = result.law_verifications
        if law_verifications:
            lines.append("### 法条引用验证结果")
            lines.append("")
            lines.append("| 序号 | 法条引用 | 本地库 | 是否现行有效 | 差异 | 置信度 |")
            lines.append("|:---:|:---|:---:|:---:|:---|:---:|")
            for idx, lv in enumerate(law_verifications, 1):
                local_str = "✅" if lv.get("local_found") else "❌"
                current_str = "是" if lv.get("is_current", True) else "否"
                confidence = lv.get("confidence", 0)
                lines.append(
                    f"| {idx} | {lv.get('citation_text', '')} | {local_str} | "
                    f"{current_str} | {lv.get('discrepancy', '')[:50]} | {confidence:.1f} |"
                )
            lines.append("")

            replaced_laws = [lv for lv in law_verifications if not lv.get("is_current", True)]
            if replaced_laws:
                lines.append("> [!CAUTION]")
                lines.append("> 检测到已废止法律的引用！已废止法律的引用属于法律适用错误，"
                             "将导致裁判依据不成立。必须立即替换为现行有效的法律。")
                lines.append("")
                for lv in replaced_laws:
                    lines.append(f"- **{lv.get('citation_text', '')}** → "
                                 f"已被 {lv.get('replaced_by', '未知')} 替代")
                lines.append("")

            not_found = [lv for lv in law_verifications if not lv.get("local_found")]
            if not_found:
                lines.append("> [!WARNING]")
                lines.append("> 以下法条在本地法律知识库中未找到，需联网验证：")
                lines.append("")
                for lv in not_found:
                    lines.append(f"- {lv.get('citation_text', '')}")
                lines.append("")
                lines.append("建议使用以下权威来源进行在线验证：")
                lines.append("- 国家法律法规数据库：https://flk.npc.gov.cn/")
                lines.append("- 最高人民法院：https://www.court.gov.cn/")
                lines.append("- 北大法宝：https://www.pkulaw.com/")
                lines.append("")

        case_verifications = result.case_verifications
        if case_verifications:
            lines.append("### 案例引用验证结果")
            lines.append("")
            lines.append("| 序号 | 案号 | 本地库 | 是否真实 | 置信度 |")
            lines.append("|:---:|:---|:---:|:---:|:---:|")
            for idx, cv in enumerate(case_verifications, 1):
                local_str = "✅" if cv.get("local_found") else "❌"
                real_str = "是" if cv.get("is_real", True) else "否"
                confidence = cv.get("confidence", 0)
                lines.append(
                    f"| {idx} | {cv.get('case_number', '')} | {local_str} | "
                    f"{real_str} | {confidence:.1f} |"
                )
            lines.append("")

            suspected_fake = [cv for cv in case_verifications if not cv.get("is_real", True)]
            if suspected_fake:
                lines.append("> [!CAUTION]")
                lines.append("> 检测到疑似杜撰的案例案号！杜撰案例属于严重的幻觉，"
                             "将导致裁判依据不成立。")
                lines.append("")
                for cv in suspected_fake:
                    lines.append(f"- **{cv.get('case_number', '')}** — 疑似杜撰")
                lines.append("")
                lines.append("建议使用以下权威来源进行在线验证：")
                lines.append("- 中国裁判文书网：https://wenshu.court.gov.cn/")
                lines.append("- 人民法院案例库：https://rmfyalk.cn/")
                lines.append("")

        if not law_verifications and not case_verifications:
            lines.append("> [!NOTE]")
            lines.append("> 法律知识库验证未启用或未发现问题。")
            lines.append("> 建议在检测时提供法律法规目录路径以启用完整的法律知识库验证。")
            lines.append("")

        lines.append("### 适用法律原则参考")
        lines.append("")
        lines.append("以下法律原则在裁判说理中具有重要参考价值，"
                      "判决书应确保不违反这些基本原则：")
        lines.append("")
        principles = [
            ("任何人不应该从违法行为中获利", "法治基本原则，源于罗马法'不法行为不产生权利'格言"),
            ("诚实信用原则", "《民法典》第七条，民事主体从事民事活动应遵循诚信原则"),
            ("公平原则", "《民法典》第六条，合理确定各方权利和义务"),
            ("有利于劳动者原则", "劳动法立法宗旨，当法律条文存在多种解释时采取有利于劳动者的解释"),
            ("特别法优于一般法", "《立法法》第九十二条，劳动法优先于民法典适用"),
            ("新法优于旧法", "《立法法》第九十二条，新的规定优先于旧的规定"),
        ]
        lines.append("| 原则 | 说明 |")
        lines.append("|:---|:---|")
        for name, desc in principles:
            lines.append(f"| {name} | {desc} |")
        lines.append("")

    def _build_h1_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 七、H-1：无源编造事实检测")
        lines.append("")
        lines.append("检测文书中的事实陈述是否编造了证据索引清单中不存在的证据来源，"
                      "以及事实陈述是否绑定了具体的证据来源（封闭宇宙规则）。"
                      "封闭宇宙规则要求：大模型生成的每一句法律事实，"
                      "必须完全源自证据索引清单中所列出并存在于工作区中的真实文件，"
                      "绝对禁止任何常识性推导、主观脑补或艺术加工。")
        lines.append("")

        h1_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-1":
                h1_flags = dim.rule_flags
                break

        idx = 0

        if result.citation_frauds:
            lines.append("### 引注欺诈项")
            lines.append("")
            lines.append("以下引注引用的证据源不在证据索引清单中，属于无源编造：")
            lines.append("")
            for cf in result.citation_frauds:
                idx += 1
                if cf.closest_match:
                    analysis = (f"引注《{cf.citation}》不在证据索引清单中。"
                               f"最接近的有效文件为《{cf.closest_match}》，"
                               f"可能是名称拼写错误或证据已补充提交但清单未更新。"
                               f"在封闭宇宙规则下，任何不在证据索引清单中的引注均视为无源编造。")
                    suggestion = (f"《{cf.citation}》\u2192 建议修正为《{cf.closest_match}》"
                                  f"或确认该证据是否已补充提交并更新证据索引清单")
                else:
                    analysis = (f"引注《{cf.citation}》不在任何证据清单中，"
                                f"属于无源编造事实。该引注所支撑的事实陈述缺乏证据基础，"
                                f"违反封闭宇宙规则。该事实可能系大模型基于常识推导或主观脑补生成。")
                    suggestion = (f"《{cf.citation}》\u2192 该证据不在任何证据清单中，"
                                  f"建议删除相关事实陈述或标注"
                                  f"\u201c上诉人主张...，但截至本操作时未见相关书证支持\u201d")
                self._write_card(
                    lines, idx, "H1", "引注欺诈",
                    "high", analysis,
                    suggestion=suggestion,
                    original_text=f"《{cf.citation}》",
                )

        h1_fact_flags = [f for f in h1_flags if f.rule_id in ("fact_source_binding", "h1_fact_source_unbound")]
        if h1_fact_flags:
            lines.append("### 事实来源未绑定证据")
            lines.append("")
            lines.append("以下事实陈述未标注证据来源，违反封闭宇宙规则"
                        "（大模型生成的每一句法律事实，必须完全源自证据索引清单中所列出的真实文件）：")
            lines.append("")
            for flag in h1_fact_flags:
                idx += 1
                analysis = ("该事实陈述未标注证据来源，也未说明无证据支持，"
                           "违反封闭宇宙规则。在缺乏证据引注的情况下，"
                           "该事实可能系大模型基于常识推导或主观脑补生成。"
                           "根据操作手册要求，缺乏证据引注的事实陈述必须标注"
                           "\u201c当事人主张...，但截至本操作时未见相关书证支持\u201d。")
                self._write_card(
                    lines, idx, "H1", flag.sub_type or "事实来源未绑定",
                    flag.severity, analysis,
                    location=f"行{flag.line_number}",
                    original_text=flag.evidence[:100] if flag.evidence else "",
                    suggestion="为该事实陈述补充证据引注，格式：（见《证据文件名.md》），"
                               "或标注\u201c当事人主张...，但截至本操作时未见相关书证支持\u201d",
                )

        h1_version_flags = [f for f in h1_flags if f.rule_id == "h1_internal_version_ref"]
        if h1_version_flags:
            lines.append("### 内部版本引用幻觉 **[新类型]**")
            lines.append("")
            lines.append("判决书正文中出现AI生成文档内部版本号引用，暴露LLM生成物本质：")
            lines.append("")
            for flag in h1_version_flags:
                idx += 1
                analysis = ("判决书正文中出现了对AI生成文档内部版本号的引用"
                           "（如\u201cV38版本\u201d\u201c本版本\u201d等）。"
                           "在现实司法文书中，法院绝不可能引用\u201c本院此前某个AI生成的模拟版本\u201d"
                           "作为说理依据。这些语句暴露了该文书是LLM生成物的本质"
                           "\u2014\u2014法院不会知道内部版本号的存在，"
                           "更不会在判决理由中对比不同版本的计算差异。"
                           "这是最严重的、颠覆文书可信度的幻觉。")
                self._write_card(
                    lines, idx, "H1", "内部版本引用",
                    "critical", analysis,
                    location=f"行{flag.line_number}",
                    original_text=flag.evidence[:100] if flag.evidence else "",
                    suggestion="全部删除对V38、V39等内部版本号的引用。"
                               "加班工资说明段改为简明注释（如\u201c本判决按起诉状分段计算\u201d）。"
                               "版本说明仅保留于文书末尾的AI生成声明区域。",
                )

        h1_fabricated_flags = [f for f in h1_flags if f.rule_id == "h1_fabricated_judicial_doc_number"]
        if h1_fabricated_flags:
            lines.append("### 司法解释文号虚构")
            lines.append("")
            lines.append("以下司法解释文号需联网验证存在性：")
            lines.append("")
            for flag in h1_fabricated_flags:
                idx += 1
                analysis = (f"引用的司法解释文号\u201c{flag.evidence[:60]}\u201d需联网验证其真实存在性。"
                           f"历史案例中曾出现虚构\u201c法释〔2025〕12号\u201d等不存在的司法解释文号。"
                           f"若该文号不存在，则属于严重的法条虚构幻觉，"
                           f"将导致裁判依据不成立。")
                self._write_card(
                    lines, idx, "H1", "法条虚构",
                    "high", analysis,
                    location=f"行{flag.line_number}",
                    original_text=flag.evidence[:100] if flag.evidence else "",
                    suggestion="联网验证该司法解释文号的真实性。若不存在，立即删除引用并替换为真实法律依据。",
                )

        if idx == 0:
            lines.append("✅ 未检测到H-1类幻觉，所有事实引注均在证据索引清单中找到对应条目，"
                          "所有事实陈述均已绑定证据来源。")
            lines.append("")

    def _build_h2_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 八、H-2：法律适用错误检测")
        lines.append("")
        lines.append("检测法条引用是否存在已废止法律、格式不规范、法律方法论替换、"
                      "仲裁时效超期、程序时效校验、法条张冠李戴等问题。"
                      "大模型在法条引用方面常出现以下幻觉：杜撰不存在的法条、"
                      "将A法的条款张冠李戴到B法事实上、无视法定时效、"
                      "杜撰程序性日期等。")
        lines.append("")

        idx = 0

        if result.time_bar_issues:
            lines.append("### 仲裁时效与程序时效检测")
            lines.append("")
            lines.append("劳动争议案件有严格的1年仲裁时效，民事案件有15日上诉期等程序时效。"
                          "以下检测到可能超过时效的问题：")
            lines.append("")
            lines.append("| 序号 | 起始日期 | 关键日期 | 时效届满日期 | 超出天数 | 时效类型 |")
            lines.append("|:---:|:---|:---|:---|:---:|:---|")
            for ti_idx, ti in enumerate(result.time_bar_issues, 1):
                if "上诉" in ti.suggestion:
                    time_type = "上诉期"
                elif "起诉" in ti.suggestion:
                    time_type = "仲裁裁决起诉期"
                else:
                    time_type = "仲裁时效"
                lines.append(
                    f"| {ti_idx} | {ti.resignation_date} | {ti.arbitration_date} | "
                    f"{ti.deadline_date} | {ti.gap_days} | {time_type} |"
                )
            lines.append("")
            for ti in result.time_bar_issues:
                idx += 1
                analysis = (f"该诉请可能已超过法定时效。起始日{ti.resignation_date}，"
                           f"时效届满日{ti.deadline_date}，关键日期{ti.arbitration_date}，"
                           f"超出{ti.gap_days}天。"
                           f"若超过时效且对方提出时效抗辩，法院应驳回该诉请。"
                           f"大模型常无视时效规定，杜撰日期或倒推时间线以规避时效限制。")
                strategy_eval = ("若存在时效中断事由（如劳动者在此期间曾主张权利、"
                                "用人单位承诺支付等），则时效重新计算。"
                                "建议在说理部分论证时效中断事由。"
                                "同时需核实关键日期是否真实——大模型可能杜撰日期以规避时效。")
                self._write_card(
                    lines, idx, "H2", "法定时效超期",
                    "high", analysis,
                    suggestion=ti.suggestion,
                    strategy_eval=strategy_eval,
                )

        law_mismatch_issues = [ci for ci in result.calculation_issues if ci.issue_type == "法条张冠李戴"]
        if law_mismatch_issues:
            lines.append("### 法条张冠李戴检测")
            lines.append("")
            lines.append("大模型常将A法的条款张冠李戴到B法的事实上。"
                          "以下检测到法条引用与事实场景不匹配的情况：")
            lines.append("")
            for ci in law_mismatch_issues:
                idx += 1
                analysis = ci.message
                suggestion = ("核实法条引用的适用场景，确保法条内容与引用目的匹配。"
                              "若存在张冠李戴，替换为正确的法律依据。"
                              "特别注意劳动法作为特别法，其赔偿体系是否穷尽了民法一般规则的适用空间。")
                self._write_card(
                    lines, idx, "H2", "法条张冠李戴",
                    ci.severity, analysis,
                    location=f"行{ci.line_number}" if ci.line_number else "",
                    suggestion=suggestion,
                )

        replaced = [lc for lc in result.law_citation_issues if lc.is_replaced]
        if replaced:
            lines.append("### 已废止法律引用")
            lines.append("")
            for lc in replaced:
                idx += 1
                analysis = (f"引用了已废止的《{lc.law_name}》，"
                           f"该法已被{lc.replaced_by}取代。"
                           f"引用已废止法律属于法律适用错误，将导致裁判依据不成立。"
                           f"根据法不溯及既往原则，应引用裁判时点有效的法律。")
                self._write_card(
                    lines, idx, "H2", "引用已废止法律",
                    "high", analysis,
                    original_text=lc.citation_text,
                    suggestion=f"将《{lc.law_name}》替换为{lc.replaced_by}，"
                               f"并核对具体条款内容是否一致。",
                )

        format_issues = []
        for lc in result.law_citation_issues:
            for fi in lc.format_issues:
                if fi != "引用已废止法律":
                    format_issues.append((lc, fi))

        if format_issues:
            lines.append("### 法条引用格式问题")
            lines.append("")
            for lc, fi in format_issues:
                idx += 1
                if "简称" in fi:
                    short_name = lc.law_name.replace('中华人民共和国', '').strip()
                    analysis = (f"首次引用法律全称《{lc.law_name}》时未标注简称。"
                               f"司法文书写作规范要求首次引用法律全称时标注简称，"
                               f"如\u201c以下简称《{short_name}》\u201d。")
                    suggestion = (f"首次引用时标注简称："
                                  f"\u201c《{lc.law_name}》（以下简称《{short_name}》）\u201d")
                else:
                    analysis = f"法条引用格式问题：{fi}"
                    suggestion = "修正法条引用格式，使其符合司法文书写作规范。"
                self._write_card(
                    lines, idx, "H2", "法条引用格式",
                    "medium", analysis,
                    original_text=lc.citation_text,
                    suggestion=suggestion,
                )

        h2_method_flags = [f for f in result.dimensions
                           if f.h_code == "H-2"
                           for f in f.rule_flags
                           if f.rule_id == "h2_methodology_replace"]
        if h2_method_flags:
            lines.append("### 法律方法论替换")
            lines.append("")
            lines.append("以下检测到法律方法论与起诉状不一致，可能构成方法论替换：")
            lines.append("")
            for flag in h2_method_flags:
                idx += 1
                analysis = (f"检测到法律方法论替换：{flag.message}。"
                           f"起诉状中采用的法律依据与判决书中采用的法律依据不同，"
                           f"可能影响当事人的诉讼策略和权利保障。"
                           f"若变更方法论，应在判决理由中充分论证变更的必要性。")
                self._write_card(
                    lines, idx, "H2", "法律方法论替换",
                    flag.severity, analysis,
                    location=f"行{flag.line_number}",
                    original_text=flag.evidence[:100] if flag.evidence else "",
                    suggestion="明确说明为何不适用起诉状援引的法律依据而采用新的法律依据，"
                               "论证新旧法律依据的区别及采用新依据的理由。",
                )

        if idx == 0:
            lines.append("✅ 未检测到法条引用问题，所有引用均为现行有效法律且格式规范。")
            lines.append("")

    def _build_h3_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 九、H-3：证据链断裂检测")
        lines.append("")
        lines.append("检测说理部分是否同时包含法律依据（大前提）和证据锚点（小前提），"
                      "即三段论完整性。每一项支持或驳回结论，必须在同一段落内同时包含"
                      "适用的法律大前提《XX法》第X条和支撑的事实小前提见《XX证据.md》。"
                      "三段论断裂将导致裁判结论缺乏逻辑支撑。")
        lines.append("")

        if result.syllogism_breaks:
            idx = 0
            for sb in result.syllogism_breaks:
                idx += 1
                if "大前提" in sb.missing_part:
                    analysis = ("该行包含裁判结论但未引用具体法条（缺失大前提）。"
                               "裁判结论必须有法律依据支撑，否则属于\u201c无法裁判\u201d。"
                               "该行仅有事实和结论，缺少连接二者的法律规范。"
                               "三段论要求：大前提（法律依据）+ 小前提（证据事实）\u2192 结论。")
                    suggestion = ("补充适用的法律条文作为大前提，"
                                  "确保结论在同一段落内有法律依据支撑。"
                                  "格式：\u201c根据《XX法》第X条，……\u201d")
                elif "小前提" in sb.missing_part:
                    analysis = ("该行包含裁判结论但未引用具体证据（缺失小前提）。"
                               "裁判结论必须有证据支撑，否则属于\u201c无源裁判\u201d。"
                               "该行仅有法律依据和结论，缺少连接二者的证据事实。"
                               "三段论要求：大前提（法律依据）+ 小前提（证据事实）\u2192 结论。")
                    suggestion = ("补充证据引注作为小前提，"
                                  "确保结论在同一段落内有证据锚点支撑。"
                                  "格式：\u201c……（见《XX证据.md》）\u201d")
                else:
                    analysis = f"说理断层：{sb.missing_part}"
                    suggestion = f"补充缺失的{sb.missing_part}"

                self._write_card(
                    lines, idx, "H3", f"三段论缺失{sb.missing_part}",
                    "high", analysis,
                    location=f"行{sb.line_number}",
                    original_text=sb.line_text[:100] if sb.line_text else "",
                    suggestion=suggestion,
                )
        else:
            lines.append("✅ 说理部分三段论完整，所有裁判结论均同时包含法律依据和证据锚点。")
            lines.append("")

    def _build_h4_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十、H-4：主观臆断/修辞过度检测")
        lines.append("")
        lines.append("检测文书中是否存在不符合司法中立原则的主观修辞和情感化语言。"
                      "评价用词必须保持司法审判的机械性、严谨性，禁止使用主观带有强烈感情色彩的修辞。"
                      "同时，从策略透镜视角评估某些修辞的保留价值。")
        lines.append("")

        non_exception = [r for r in result.rhetoric_items if not r.is_exception]
        exceptions = [r for r in result.rhetoric_items if r.is_exception]

        idx = 0

        if non_exception:
            lines.append("### 需替换的主观修辞")
            lines.append("")
            for ri in non_exception:
                idx += 1
                if ri.keyword in ("恶意", "主观恶意"):
                    analysis = (f"\u201c{ri.keyword}\u201d为对当事人主观状态的定性，需有证据支撑。"
                               f"在民事诉讼中，\u201c恶意\u201d有特定含义，"
                               f"若无直接证据证明当事人主观状态，使用该词属于主观推断。"
                               f"但在一份判决书中密集使用\u201c恶意\u201d修辞，"
                               f"可能被上诉法院认为超出客观中性的司法文书写作规范。")
                    strategy_eval = ("**激进路径**（保留策略价值）：保留\u201c恶意\u201d定性，"
                                    "但将出现次数控制在2处以内——强化法庭对用人单位行为恶劣性的认知。"
                                    "**保守路径**（追求可信度）：全部替换为客观行为描述。")
                    suggestion = (f"将\u201c{ri.keyword}\u201d替换为客观行为描述。"
                                  f"若保留，建议将出现次数控制在2处以内。")
                elif ri.keyword in ("逾期", "迟至", "拖延", "怠于"):
                    analysis = (f"\u201c{ri.keyword}\u201d隐含了对法庭内部程序事实的主观推断。"
                               f"上诉人视角只能确认提交和收到的时间差，"
                               f"中间间隔的原因不得而知\u2014\u2014是否逾期、是否迟延，"
                               f"无法从现有证据中确定。\u201c逾期\u201d\u201c迟至\u201d"
                               f"均系对未知程序事实的主观推断。")
                    strategy_eval = ("该问题在多个版本中反复出现，属于系统性修正不足。"
                                    "建议采用高亮提示方案，将主观推断替换为客观时间描述。")
                    suggestion = (f"将\u201c{ri.keyword}\u201d替换为客观时间描述，"
                                  f"或标注\u201c【需法庭确认：……中间间隔原因有待法庭查明。】\u201d")
                else:
                    analysis = (f"使用了主观感情色彩词汇\u201c{ri.keyword}\u201d，"
                               f"违反司法中立性原则。司法文书应保持客观、机械、严谨，"
                               f"禁止使用主观带有强烈感情色彩的修辞。")
                    suggestion = ri.suggestion or f"将\u201c{ri.keyword}\u201d替换为中性客观表述"

                self._write_card(
                    lines, idx, "H4", "主观修辞",
                    ri.severity, analysis,
                    location=f"行{ri.line_number}",
                    original_text=ri.line_text[:100] if ri.line_text else "",
                    suggestion=suggestion,
                    strategy_eval=strategy_eval if ri.keyword in ("恶意", "主观恶意", "逾期", "迟至", "拖延") else "",
                )

        if exceptions:
            lines.append("### 法条保留例外（无需替换）")
            lines.append("")
            lines.append("以下关键词虽命中修辞检测，但属于法律术语必要用法，无需替换：")
            lines.append("")
            for ri in exceptions:
                lines.append(f"- ✅ **{ri.keyword}** 行{ri.line_number}：法条原文保留")
            lines.append("")

        if idx == 0 and not exceptions:
            lines.append("✅ 未检测到主观修辞问题，文书表述保持客观中立。")
            lines.append("")

    def _build_h5_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十一、H-5：诉求边界突破检测")
        lines.append("")
        lines.append("检测判决主文是否超出起诉状/上诉状的诉请范围。"
                      "判决支持的项目不得超出诉请范围；判决支持的金额在数理上必须小于或等于诉请的最大上限，"
                      "严禁凭空为任何一方编造或多判任何金钱请求（诉审一致原则）。")
        lines.append("")

        idx = 0

        if result.claim_violations:
            for cv in result.claim_violations:
                idx += 1
                if cv.violation_type == "项目越权":
                    analysis = (f"判决项目\u201c{cv.judgment_item}\u201d在起诉状/上诉状中无对应诉请，"
                               f"属于项目越权裁判。判决金额{cv.judgment_amount:,.2f}元"
                               f"系凭空创设的给付项目，违反诉审一致原则。")
                    strategy_eval = ("若该给付项目系二审中基于新事实或变更诉求而产生，"
                                    "应在判决理由中明确说明其诉请基础，"
                                    "并确认是否存在变更诉求申请书。")
                    suggestion = (f"删除判决项目\u201c{cv.judgment_item}\u201d，"
                                  f"或确认是否存在变更诉求申请书。"
                                  f"若无变更诉求，则该项目属于超诉请裁判，必须删除。")
                elif cv.violation_type == "金额冒顶":
                    ratio = cv.judgment_amount / cv.claim_max if cv.claim_max > 0 else 0
                    analysis = (f"判决金额{cv.judgment_amount:,.2f}元超出诉请上限"
                               f"{cv.claim_max:,.2f}元，超出{cv.excess_amount:,.2f}元"
                               f"（偏差{ratio:.2f}倍）。"
                               f"这属于金额冒顶，违反诉审一致原则中"
                               f"\u201c判决支持的金额必须\u2264诉请最大上限\u201d的硬性要求。")
                    if ratio >= 3.0:
                        strategy_eval = ("从劳动者代理人视角，若能通过同工同酬等法定方法"
                                        "确立更高的计算基数，则超出部分在法理上是自洽的。"
                                        "问题不在于法理，而在于：起诉状本身使用的是更保守的算法，"
                                        "判决书应采用与起诉状一致的逻辑起点再行扩展，"
                                        "而非完全替换方法论。建议在判决理由中增加过渡说明，"
                                        "明确新旧算法的差异和采纳新算法的理由。")
                    else:
                        strategy_eval = ""
                    suggestion = (f"将判决金额调整至诉请上限{cv.claim_max:,.2f}元以内，"
                                  f"或在判决理由中充分论证超出部分的合法性和必要性，"
                                  f"并确认是否存在变更诉求申请书。")
                else:
                    analysis = f"诉请边界突破：{cv.violation_type}"
                    suggestion = "修正该处判决，确保诉审一致。"
                    strategy_eval = ""

                self._write_card(
                    lines, idx, "H5", cv.violation_type,
                    "high", analysis,
                    original_text=f"{cv.judgment_item}: {cv.judgment_amount:,.2f}元",
                    suggestion=suggestion,
                    strategy_eval=strategy_eval,
                )
        else:
            lines.append("✅ 判决主文未超出诉请范围，诉审一致原则得到遵守。")
            lines.append("")

    def _build_h6_section(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十二、H-6：非文本证据穿透失败检测")
        lines.append("")
        lines.append("检测文书中引用的非文本证据（录音、录像、鉴定意见等）是否标注了来源形式。"
                      "非文本证据的穿透失败会导致事实认定缺乏可验证性。"
                      "本报告基于前置版本检测经验，不做\u201c可能为幻觉\u201d的预设判断，"
                      "而是标注其验证状态供人工确认。")
        lines.append("")

        h6_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-6":
                h6_flags = dim.rule_flags
                break

        if h6_flags:
            idx = 0
            for flag in h6_flags:
                idx += 1
                analysis = (f"引用了非文本证据（{flag.sub_type}）但未标注来源形式。"
                           f"非文本证据需要明确其来源形式（如\u201c据录音整理文字\u201d"
                           f"\u201c据当事人陈述\u201d\u201c据鉴定意见认定\u201d等），"
                           f"否则无法验证其真实性和完整性。")
                self._write_card(
                    lines, idx, "H6", flag.sub_type,
                    flag.severity, analysis,
                    location=flag.location,
                    original_text=flag.evidence[:100] if flag.evidence else "",
                    suggestion="补充标注非文本证据的来源形式，"
                               "如\u201c据录音整理文字\u201d\u201c据当事人陈述\u201d"
                               "\u201c据鉴定意见认定\u201d等。"
                               "若无法确认来源，标注\u201c待确认\u201d或\u201c待核实\u201d。",
                )
        else:
            lines.append("✅ 非文本证据引用均已标注来源形式，或未引用非文本证据。")
            lines.append("")

    def _build_nontext_evidence_table(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十三、非文本证据穿透验证表")
        lines.append("")
        lines.append("以下列出文书中引用的非文本证据及其验证状态，"
                      "供人工确认其真实性。本表不做\u201c可能为幻觉\u201d的预设判断，"
                      "仅标注验证状态。")
        lines.append("")

        h6_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-6":
                h6_flags = dim.rule_flags
                break

        nontext_evidence = []
        for flag in h6_flags:
            nontext_evidence.append({
                "description": flag.evidence[:80] if flag.evidence else flag.sub_type,
                "evidence_type": flag.sub_type,
                "status": "未标注来源" if flag.severity in ("medium", "high") else "已标注",
            })

        if nontext_evidence:
            lines.append("| 序号 | 文书陈述 | 证据类型 | 验证状态 |")
            lines.append("|:---:|:---|:---|:---|")
            for idx, ne in enumerate(nontext_evidence, 1):
                lines.append(f"| {idx} | {ne['description']} | {ne['evidence_type']} | {ne['status']} |")
            lines.append("")
        else:
            lines.append("✅ 未引用非文本证据，或所有非文本证据均已标注来源形式。")
            lines.append("")

    def _build_summary_stats(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十四、汇总统计")
        lines.append("")

        confirmed = 0
        suspected = 0
        strategic = 0

        for dim in result.dimensions:
            confirmed += dim.critical_count + dim.high_count
            suspected += dim.medium_count

        non_exception_rhetoric = [r for r in result.rhetoric_items if not r.is_exception]
        strategic += len(non_exception_rhetoric)

        lines.append("### 各维度幻觉统计")
        lines.append("")
        lines.append("| 幻觉类型 | 确认数 | 疑似 | 策略性争议 |")
        lines.append("|:---|:---:|:---:|:---:|")

        for dim in result.dimensions:
            dim_confirmed = dim.critical_count + dim.high_count
            dim_suspected = dim.medium_count
            dim_strategic = 0
            if dim.h_code == "H-4":
                dim_strategic = len(non_exception_rhetoric)
            lines.append(f"| {dim.h_code}（{dim.dimension_title}） | {dim_confirmed} | {dim_suspected} | {dim_strategic} |")

        lines.append(f"| **合计** | **{confirmed}** | **{suspected}** | **{strategic}** |")
        lines.append("")

        lines.append("### 最需优先修正的三项")
        lines.append("")

        top3 = self._get_top3_fixes(result)
        if top3:
            lines.append("| 优先级 | 问题 | 理由 |")
            lines.append("|:---:|:---|:---|")
            for priority, problem, reason in top3:
                lines.append(f"| {priority} | {problem} | {reason} |")
            lines.append("")
        else:
            lines.append("✅ 未发现需要优先修正的致命或严重级问题。")
            lines.append("")

        lines.append("### 继续有效的优势")
        lines.append("")

        advantages = []
        if not result.structure_issues:
            advantages.append(("文书结构完整", "四个必需段落标题均已包含"))
        if not result.citation_frauds:
            advantages.append(("引注无欺诈", "所有事实引注均在证据索引清单中找到对应条目"))
        if not result.time_bar_issues:
            advantages.append(("仲裁时效合规", "未检测到超过仲裁时效的诉请"))
        if not result.syllogism_breaks:
            advantages.append(("三段论完整", "所有裁判结论均同时包含法律依据和证据锚点"))
        if not result.fact_source_issues:
            advantages.append(("事实来源绑定", "所有事实陈述均已标注证据来源"))
        if not result.claim_violations:
            advantages.append(("诉审一致", "判决主文未超出诉请范围"))

        if advantages:
            lines.append("| 优势 | 说明 |")
            lines.append("|:---|:---|")
            for adv, desc in advantages:
                lines.append(f"| {adv} | {desc} |")
            lines.append("")
        else:
            lines.append("⚠️ 未检测到明显的文书优势项，建议全面修正。")
            lines.append("")

    def _get_top3_fixes(self, result: HallucinationDetectionResult) -> list[tuple]:
        fixes = []

        h1_version_flags = []
        h1_fabricated_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-1":
                h1_version_flags = [f for f in dim.rule_flags if f.rule_id == "h1_internal_version_ref"]
                h1_fabricated_flags = [f for f in dim.rule_flags if f.rule_id == "h1_fabricated_judicial_doc_number"]

        if h1_version_flags:
            fixes.append((1, "删除正文中所有\u201cV38\u201d\u201c本版本\u201d等内部版本引用",
                          "颠覆文书可信度。将法官不可能知道的内容写入判决书是最严重的幻觉。"))

        if h1_fabricated_flags:
            fixes.append((1, "联网验证司法解释文号的真实性",
                          "虚构司法解释文号属于严重法条幻觉，将导致裁判依据不成立。"))

        if result.citation_frauds:
            fixes.append((1, "修正或删除不在证据索引清单中的引注",
                          "引注欺诈属于无源编造，违反封闭宇宙规则。"))

        if result.claim_violations:
            max_violation = max(result.claim_violations, key=lambda cv: cv.excess_amount, default=None)
            if max_violation and max_violation.claim_max > 0:
                ratio = max_violation.judgment_amount / max_violation.claim_max
                fixes.append((2, f"增加{max_violation.judgment_item}从起诉状算法到判决算法的过渡论证",
                              f"给付金额超起诉状{ratio:.2f}倍，需充分说明新旧算法的法理差异和采纳新算法的理由。"))
            elif max_violation:
                fixes.append((2, f"删除判决项目\u201c{max_violation.judgment_item}\u201d或确认变更诉求",
                              "该给付项目在起诉状中无对应诉请，属于项目越权裁判。"))

        non_exception_rhetoric = [r for r in result.rhetoric_items if not r.is_exception]
        overdue_words = [r for r in non_exception_rhetoric if r.keyword in ("逾期", "迟至", "拖延")]
        if overdue_words:
            fixes.append((3, "将\u201c逾期\u201d\u201c迟至\u201d替换为\u201c需法庭确认\u201d标记",
                          "延续多个版本的同一问题，频繁再现说明系统性修正不足。"))
        elif non_exception_rhetoric:
            fixes.append((3, "将主观评价性词汇替换为中性客观表述",
                          "司法文书应保持客观中立，避免使用主观感情色彩词汇。"))

        fixes.sort(key=lambda x: x[0])
        return fixes[:3]

    def _build_version_comparison(
        self,
        lines: list,
        result: HallucinationDetectionResult,
        previous_versions: list[dict],
    ):
        lines.append("## 十五、与既往版本改进对照")
        lines.append("")

        if not previous_versions:
            lines.append("*未提供既往版本数据，无法进行版本对照。"
                          "如需版本对照，请在调用时传入 previous_versions 参数。*")
            lines.append("")
            lines.append("版本对照格式示例：")
            lines.append("```python")
            lines.append("previous_versions = [")
            lines.append("    {'version': 'V38', 'critical_count': 3, 'high_count': 5,")
            lines.append("     'issues': '内部版本引用+诉请边界突破', 'improvements': '删除版本引用',")
            lines.append("     'avoided_issues': [")
            lines.append("         {'type': '肝功能异常', 'original': '已出现肝功能异常', 'status': '已避免'}")
            lines.append("     ]}")
            lines.append("]")
            lines.append("```")
            lines.append("")
            return

        lines.append("### 与既往版本幻觉总量对比")
        lines.append("")
        lines.append("| 版本 | 确认幻觉数 | 核心问题类型 | 改进点 |")
        lines.append("|:---|:---:|:---|:---|")
        for ver in previous_versions:
            lines.append(
                f"| {ver.get('version', '?')} | {ver.get('critical_count', '?')} | "
                f"{ver.get('issues', '?')} | {ver.get('improvements', '?')} |"
            )

        current_issues = self._get_core_issues(result)
        current_improvements = self._get_current_improvements(result)
        lines.append(
            f"| **当前版本** | **{result.total_flags}** | {current_issues} | {current_improvements} |"
        )
        lines.append("")

        lines.append("### 当前版本避免的既往幻觉")
        lines.append("")
        for ver in previous_versions:
            ver_issues = ver.get("avoided_issues", [])
            if ver_issues:
                lines.append(f"**{ver.get('version', '?')}中已避免的幻觉：**")
                lines.append("")
                lines.append("| 既往幻觉 | 既往表述 | 当前版本状态 |")
                lines.append("|:---|:---|:---|")
                for ai in ver_issues:
                    lines.append(f"| {ai.get('type', '?')} | {ai.get('original', '?')} | {ai.get('status', '?')} |")
                lines.append("")

    def _get_core_issues(self, result: HallucinationDetectionResult) -> str:
        issues = []
        if result.citation_frauds or result.fact_source_issues:
            issues.append("引注问题")
        if result.claim_violations:
            issues.append("诉请边界突破")
        if result.syllogism_breaks:
            issues.append("三段论断裂")
        non_exc = [r for r in result.rhetoric_items if not r.is_exception]
        if non_exc:
            issues.append("主观修辞残留")
        if result.time_bar_issues:
            issues.append("仲裁时效超期")
        if result.methodology_replacements:
            issues.append("方法论替换")
        return "+".join(issues) if issues else "无核心问题"

    def _get_current_improvements(self, result: HallucinationDetectionResult) -> str:
        improvements = []
        if not result.structure_issues:
            improvements.append("文书结构完整")
        if not result.citation_frauds:
            improvements.append("引注无欺诈")
        if not result.time_bar_issues:
            improvements.append("仲裁时效合规")
        if not result.syllogism_breaks:
            improvements.append("三段论完整")
        if not result.fact_source_issues:
            improvements.append("事实来源绑定")
        return "；".join(improvements) if improvements else "待改进"

    def _build_suggestions(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十六、综合修正建议")
        lines.append("")

        suggestions = []

        if result.structure_issues:
            suggestions.append(("文书结构", "致命",
                                '补充缺失的必需段落标题，确保文书包含'
                                '\u201c诉讼请求与主张\u201d\u201c本院查明事实\u201d'
                                '\u201c本院认为\u201d\u201c判决如下\u201d四个标准部分'))

        h1_version_flags = []
        h1_fabricated_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-1":
                h1_version_flags = [f for f in dim.rule_flags if f.rule_id == "h1_internal_version_ref"]
                h1_fabricated_flags = [f for f in dim.rule_flags if f.rule_id == "h1_fabricated_judicial_doc_number"]

        if h1_version_flags:
            suggestions.append(("内部版本引用", "致命",
                                '删除判决书正文中对AI生成文档内部版本号的引用'
                                '（如\u201cV38版本\u201d\u201c本版本\u201d\u201c以V38为框架\u201d等），'
                                '版本说明仅保留于文书末尾的AI生成声明区域'))

        if h1_fabricated_flags:
            suggestions.append(("法条虚构", "严重",
                                '对检出的司法解释文号联网验证其真实存在性，若不存在则删除引用'))

        if result.citation_frauds:
            suggestions.append(("引注欺诈", "严重",
                                '修正或删除不在证据索引清单中的引注，'
                                '对缺乏证据的事实标注\u201c未见相关书证支持\u201d'))

        if result.fact_source_issues:
            suggestions.append(("事实来源未绑定", "严重",
                                '为未绑定证据的事实陈述补充引注，格式：（见《证据文件名.md》），'
                                '或标注\u201c当事人主张...，但截至本操作时未见相关书证支持\u201d'))

        replaced = [lc for lc in result.law_citation_issues if lc.is_replaced]
        if replaced:
            suggestions.append(("已废止法律", "严重",
                                "将已废止法律替换为现行有效的法律"))

        format_issues = [lc for lc in result.law_citation_issues if not lc.is_replaced and lc.format_issues]
        if format_issues:
            suggestions.append(("法条引用格式", "中等",
                                '首次引用法律全称时标注简称，如\u201c以下简称《劳动合同法》\u201d；'
                                '司法解释须含全称+文号'))

        if result.syllogism_breaks:
            suggestions.append(("三段论断裂", "严重",
                                "为缺失大前提的结论补充法律依据，为缺失小前提的结论补充证据引注"))

        if result.time_bar_issues:
            suggestions.append(("仲裁时效", "严重",
                                "论证时效中断事由或对方放弃时效抗辩，否则应驳回超时效诉请"))

        non_exception_rhetoric = [r for r in result.rhetoric_items if not r.is_exception]
        if non_exception_rhetoric:
            suggestions.append(("主观修辞", "中等",
                                "将主观评价性词汇替换为中性客观表述，保留法律术语必要用法"))

        if result.claim_violations:
            suggestions.append(("诉请边界", "严重",
                                "将超出诉请范围的判决项目删除或调整金额至诉请上限以内，"
                                "或在判决理由中充分论证超出部分的合法性"))

        if result.methodology_replacements:
            suggestions.append(("方法论替换", "严重",
                                "明确说明为何不适用起诉状援引的法律依据而采用新的法律依据，"
                                "论证新旧法律依据的区别及采用新依据的理由"))

        h6_flags = []
        for dim in result.dimensions:
            if dim.h_code == "H-6":
                h6_flags = dim.rule_flags
                break
        if h6_flags:
            suggestions.append(("非文本证据", "轻微",
                                "为非文本证据引用补充来源形式标注"))

        if suggestions:
            lines.append("| 序号 | 问题类别 | 优先级 | 修正建议 |")
            lines.append("|:---:|:---|:---|:---|")
            for idx, (cat, priority, suggestion) in enumerate(suggestions, 1):
                lines.append(f"| {idx} | {cat} | {priority} | {suggestion} |")
            lines.append("")
        else:
            lines.append("✅ 未发现需要修正的问题，文书质量良好。")
            lines.append("")

    def _build_priority_fixes(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十七、紧急修复优先级")
        lines.append("")
        lines.append("发现幻觉时的修复优先级排序（参照操作手册第十二章）：")
        lines.append("")

        priorities = []

        h1_fabricated = []
        h1_version = []
        for dim in result.dimensions:
            if dim.h_code == "H-1":
                h1_fabricated = [f for f in dim.rule_flags if f.rule_id == "h1_fabricated_judicial_doc_number"]
                h1_version = [f for f in dim.rule_flags if f.rule_id == "h1_internal_version_ref"]

        if h1_fabricated:
            priorities.append(("P0-紧急", "法条虚构（H-1/H-2）", "立即删除虚构引用，替换为真实法律依据", "即时"))
        if h1_version:
            priorities.append(("P0-紧急", "内部版本号引用（H-1）", "立即删除所有VXX版本号引用", "即时"))
        if result.citation_frauds:
            priorities.append(("P0-紧急", "证据引注不存在（H-1）", "立即删除或修正为正确引注", "即时"))
        if result.claim_violations:
            priorities.append(("P0-紧急", "诉请边界突破（H-5）", "立即调整判决金额至诉请上限内", "即时"))
        if result.structure_issues:
            priorities.append(("P0-紧急", "文书结构缺失", "补充缺失的必需段落标题", "即时"))
        if result.time_bar_issues:
            priorities.append(("P0-紧急", "仲裁时效超期（H-2）", "论证时效中断或驳回超时效诉请", "即时"))

        if result.methodology_replacements:
            priorities.append(("P1-高", "法律方法论替换（H-2/H-5）", "增加变更论证或恢复原方法论", "2小时内"))

        non_exception_rhetoric = [r for r in result.rhetoric_items if not r.is_exception]
        if non_exception_rhetoric:
            priorities.append(("P2-中", "主观修辞残留（H-4）", "替换为中性表述", "2小时内"))

        format_issues = [lc for lc in result.law_citation_issues if not lc.is_replaced and lc.format_issues]
        if format_issues:
            priorities.append(("P3-低", "格式不统一", "统一全文引注格式", "4小时内"))

        if priorities:
            lines.append("| 优先级 | 幻觉类型 | 修复方式 | 时限 |")
            lines.append("|:---|:---|:---|:---|")
            for p, h_type, fix, deadline in priorities:
                lines.append(f"| {p} | {h_type} | {fix} | {deadline} |")
            lines.append("")
        else:
            lines.append("✅ 无需紧急修复，文书质量良好。")
            lines.append("")

    def _build_checklist(self, lines: list, result: HallucinationDetectionResult):
        lines.append("## 十八、文书生成后自检清单")
        lines.append("")
        lines.append("以下清单供人工审核和AI Agent进一步改进时参考（参照操作手册第七章）：")
        lines.append("")

        lines.append("### A. 法律引用自检")
        lines.append("- [ ] 每条引用的法条是否已通过权威渠道验证存在？")
        lines.append("- [ ] 司法解释文号是否与发布记录一致？")
        lines.append("- [ ] 法条内容是否逐字核对原文？")
        lines.append("- [ ] 法条在裁判时点是否已生效？")
        lines.append("- [ ] 类案案号是否可验证？不可验证的是否已标注？")
        lines.append("- [ ] 法条引用是否完整（含但书/除外条款）？")
        lines.append("- [ ] 法律方法论是否与起诉状一致？若变更是否已论证？")
        lines.append("- [ ] 是否存在内部版本号引用（V38/V39等）？")
        lines.append("- [ ] 附录是否列出所有引用法条及验证状态？")
        lines.append("- [ ] 法条引用是否存在张冠李戴（A法条款用于B法事实）？")
        lines.append("- [ ] 劳动法特别规定是否被民法典一般条款不当替代？")
        lines.append("")

        lines.append("### B. 证据引注自检")
        lines.append("- [ ] 每项证据引注是否在证据索引清单中有对应条目？")
        lines.append("- [ ] 证据名称是否与清单完全一致？")
        lines.append("- [ ] 是否存在引用非清单证据的情况？")
        lines.append("- [ ] 证据内容描述是否与证据文件一致？")
        lines.append("- [ ] 全文同一证据的引注格式是否一致？")
        lines.append('- [ ] 缺乏证据的事实是否已标注\u201c未见相关书证支持\u201d？')
        lines.append("- [ ] 非文本证据是否标注了来源形式？")
        lines.append("")

        lines.append("### C. 诉请边界自检")
        lines.append("- [ ] 判决主文每个项目是否在起诉状/上诉状中有对应诉请？")
        lines.append("- [ ] 判决主文每项金额是否 \u2264 诉请中该项目的最高金额？")
        lines.append("- [ ] 是否存在法律方法论替换？若存在是否已论证？")
        lines.append("- [ ] 是否运行审计脚本并获得 [AUDIT_PASSED]？")
        lines.append("")

        lines.append("### D. 主观修辞自检")
        lines.append('- [ ] 是否存在\u201c恶意\u201d\u201c卑劣\u201d等主观评价词汇？')
        lines.append('- [ ] 保留的\u201c恶意\u201d是否属于法律术语必要用法？')
        lines.append("- [ ] 是否存在意图推断性表述？")
        lines.append('- [ ] 是否存在程序事实主观推断（\u201c逾期\u201d\u201c迟至\u201d）？')
        lines.append("- [ ] 事实描述是否保持客观中立？")
        lines.append("")

        lines.append("### E. 逻辑一致性自检")
        lines.append("- [ ] 事实认定前后是否一致？")
        lines.append("- [ ] 证据\u2192事实\u2192法律\u2192结论链条是否完整（三段论）？")
        lines.append("- [ ] 争议焦点是否逐一回应？")
        lines.append("- [ ] 时间线是否逻辑自洽？")
        lines.append("- [ ] 仲裁时效是否合规？")
        lines.append("- [ ] 上诉期/起诉期等程序时效是否合规？")
        lines.append("- [ ] 程序性日期是否真实可验证（非杜撰）？")
        lines.append("- [ ] 利息计算基数是否与判决主文金额一致？")
        lines.append("- [ ] 加班工资/二倍工资等关键计算公式是否可还原？")
        lines.append("- [ ] 判决主文各项金额加总是否与合计金额一致？")
        lines.append("- [ ] 是否存在逻辑循环（推定性金额用于计算惩罚性赔偿）？")
        lines.append("")

    def export_json(
        self,
        result: HallucinationDetectionResult,
        indent: int = 2,
    ) -> str:
        """将检测结果导出为JSON格式。"""
        data = {
            "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "hallucination_score": result.hallucination_score,
            "risk_grade": result.risk_grade,
            "risk_description": result.risk_description,
            "total_flags": result.total_flags,
            "dimensions": [],
            "structure_issues": [
                {"heading": si.heading, "present": si.present, "severity": si.severity}
                for si in result.structure_issues
            ],
            "citation_frauds": [
                {"citation": cf.citation, "closest_match": cf.closest_match, "severity": cf.severity}
                for cf in result.citation_frauds
            ],
            "claim_comparisons": [
                {
                    "item_name": cc.item_name,
                    "claim_amount": cc.claim_amount,
                    "judgment_amount": cc.judgment_amount,
                    "deviation_ratio": cc.deviation_ratio,
                    "nature": cc.nature,
                    "is_consistent": cc.is_consistent,
                }
                for cc in result.claim_comparisons
            ],
            "calculation_issues": [
                {"issue_type": ci.issue_type, "description": ci.description, "severity": ci.severity}
                for ci in result.calculation_issues
            ],
        }

        for dim in result.dimensions:
            dim_data = {
                "dimension": dim.dimension,
                "title": dim.title,
                "total_flags": dim.total_flags,
                "critical_count": dim.critical_count,
                "high_count": dim.high_count,
                "medium_count": dim.medium_count,
                "low_count": dim.low_count,
                "rule_flags": [
                    {
                        "rule_id": rf.rule_id,
                        "severity": rf.severity,
                        "message": rf.message,
                        "evidence": rf.evidence,
                        "line_number": rf.line_number,
                    }
                    for rf in dim.rule_flags
                ],
            }
            data["dimensions"].append(dim_data)

        return json.dumps(data, ensure_ascii=False, indent=indent)

    def export_csv(
        self,
        result: HallucinationDetectionResult,
    ) -> str:
        """将检测标记导出为CSV格式。"""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "维度", "维度标题", "规则ID", "子类型", "严重度",
            "消息", "证据", "行号",
        ])

        for dim in result.dimensions:
            for rf in dim.rule_flags:
                writer.writerow([
                    dim.dimension,
                    dim.title,
                    rf.rule_id,
                    rf.sub_type,
                    rf.severity,
                    rf.message,
                    rf.evidence[:100] if rf.evidence else "",
                    rf.line_number,
                ])

        return output.getvalue()
