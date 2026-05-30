"""法律文书幻觉检测数据模型 v0.1.0。"""

from datetime import datetime

from pydantic import BaseModel, Field


class RuleFlag(BaseModel):
    rule_id: str = Field(default="", description="规则标识")
    h_code: str = Field(default="", description="幻觉维度编号 H-1~H-6")
    sub_type: str = Field(default="", description="子类型")
    severity: str = Field(default="medium", description="严重度: critical/high/medium/low/info")
    message: str = Field(default="", description="检测消息")
    evidence: str = Field(default="", description="命中原文")
    location: str = Field(default="", description="位置描述")
    line_number: int = Field(default=0, description="行号")


class CitationFraudItem(BaseModel):
    citation: str = Field(default="", description="引注文本")
    normalized: str = Field(default="", description="标准化后的引注")
    matched: bool = Field(default=False, description="是否在证据清单中找到匹配")
    closest_match: str = Field(default="", description="最接近的有效文件名")


class ClaimBoundaryItem(BaseModel):
    judgment_item: str = Field(default="", description="判决主文项目名")
    judgment_amount: float = Field(default=0.0, description="判决支持金额")
    matched_claim: str = Field(default="", description="匹配的诉请项目名")
    claim_max: float = Field(default=0.0, description="诉请上限金额")
    violation_type: str = Field(default="", description="越权类型: 项目越权/金额冒顶")
    excess_amount: float = Field(default=0.0, description="超出金额")


class SyllogismBreakItem(BaseModel):
    line_number: int = Field(default=0)
    line_text: str = Field(default="", description="原文摘录")
    missing_part: str = Field(default="", description="缺失部分: 大前提/小前提")
    h_code: str = Field(default="H-3")


class RhetoricItem(BaseModel):
    keyword: str = Field(default="", description="命中的主观修辞关键词")
    line_number: int = Field(default=0)
    line_text: str = Field(default="", description="原文摘录")
    severity: str = Field(default="medium")
    is_exception: bool = Field(default=False, description="是否属于法条保留例外")
    suggestion: str = Field(default="", description="替换建议")


class LawCitationItem(BaseModel):
    citation_text: str = Field(default="", description="法条引用原文")
    law_name: str = Field(default="", description="法律名称")
    article: str = Field(default="", description="条款号")
    is_replaced: bool = Field(default=False, description="是否引用已废止法律")
    replaced_by: str = Field(default="", description="替代法律")
    format_issues: list[str] = Field(default_factory=list, description="格式问题列表")
    local_match_found: bool = Field(default=False, description="本地法条库是否匹配")


class StructureCheckItem(BaseModel):
    heading: str = Field(default="", description="缺失的标题")
    severity: str = Field(default="critical")
    h_code: str = Field(default="H-3")
    message: str = Field(default="")


class FactSourceIssue(BaseModel):
    line_number: int = Field(default=0, description="行号")
    line_text: str = Field(default="", description="原文摘录")
    issue_type: str = Field(default="", description="问题类型: 无证据引注/证据不在清单/事实缺乏来源")
    h_code: str = Field(default="H-1", description="幻觉维度编号")
    suggestion: str = Field(default="", description="修正建议")


class HallucinationDimensionResult(BaseModel):
    dimension: str = Field(default="", description="维度标识")
    dimension_title: str = Field(default="", description="维度中文名")
    h_code: str = Field(default="", description="H-1~H-6")
    rule_flags: list[RuleFlag] = Field(default_factory=list)
    semantic_flags: list[dict] = Field(default_factory=list)
    total_flags: int = Field(default=0)
    critical_count: int = Field(default=0)
    high_count: int = Field(default=0)
    medium_count: int = Field(default=0)
    low_count: int = Field(default=0)


class TimeBarIssue(BaseModel):
    resignation_date: str = Field(default="", description="离职/解除日期")
    arbitration_date: str = Field(default="", description="申请仲裁日期")
    deadline_date: str = Field(default="", description="时效届满日期")
    is_time_barred: bool = Field(default=False, description="是否超过仲裁时效")
    gap_days: int = Field(default=0, description="超出时效天数")
    h_code: str = Field(default="H-2", description="幻觉维度编号")
    suggestion: str = Field(default="", description="修正建议")


class MethodologyReplacement(BaseModel):
    claim_law_basis: str = Field(default="", description="起诉状援引的法律依据")
    judgment_law_basis: str = Field(default="", description="判决书实际使用的法律依据")
    claim_item: str = Field(default="", description="诉请项目")
    replacement_type: str = Field(default="", description="替换类型: 法律依据替换/计算方法替换/参照系替换")
    impact_analysis: str = Field(default="", description="替换影响分析")
    h_code: str = Field(default="H-5", description="幻觉维度编号")
    severity: str = Field(default="high", description="严重度")


class InterestBaseItem(BaseModel):
    base_amount: float = Field(default=0.0, description="利息基数金额")
    base_text: str = Field(default="", description="基数原文")
    rate_text: str = Field(default="", description="利率原文")
    period_text: str = Field(default="", description="计息期间原文")
    line_number: int = Field(default=0, description="行号")


class CalculationIssue(BaseModel):
    item: str = Field(default="", description="计算项名称")
    amount: float = Field(default=0.0, description="判决书中的金额")
    expected: float = Field(default=0.0, description="按公式还原的预期金额")
    formula: str = Field(default="", description="还原的计算公式")
    issue_type: str = Field(default="", description="问题类型: 计算错误/参数缺失/逻辑循环/无法还原")
    message: str = Field(default="", description="详细说明")
    severity: str = Field(default="medium", description="严重度")
    h_code: str = Field(default="H-5", description="幻觉维度编号")
    line_number: int = Field(default=0, description="行号")
    parameters: dict = Field(default_factory=dict, description="提取的计算参数")


class ClaimComparisonItem(BaseModel):
    item_name: str = Field(default="", description="给付项目名称")
    claim_amount: float = Field(default=0.0, description="起诉状请求金额")
    judgment_amount: float = Field(default=0.0, description="判决金额")
    claim_description: str = Field(default="", description="起诉状中的计算说明")
    judgment_description: str = Field(default="", description="判决书中的计算说明")
    deviation_ratio: float = Field(default=0.0, description="偏差倍数")
    nature: str = Field(default="", description="性质: 一致/轻微超出/明显超出/显著超出/项目越权")
    strategy_eval: str = Field(default="", description="策略评估")
    is_consistent: bool = Field(default=True, description="是否一致")


class HallucinationDetectionResult(BaseModel):
    document_path: str = Field(default="")
    manifest_path: str = Field(default="")
    detection_time: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    dimensions: list[HallucinationDimensionResult] = Field(default_factory=list)
    structure_issues: list[StructureCheckItem] = Field(default_factory=list)
    citation_frauds: list[CitationFraudItem] = Field(default_factory=list)
    claim_violations: list[ClaimBoundaryItem] = Field(default_factory=list)
    syllogism_breaks: list[SyllogismBreakItem] = Field(default_factory=list)
    rhetoric_items: list[RhetoricItem] = Field(default_factory=list)
    law_citation_issues: list[LawCitationItem] = Field(default_factory=list)
    fact_source_issues: list[FactSourceIssue] = Field(default_factory=list)
    time_bar_issues: list[TimeBarIssue] = Field(default_factory=list)
    methodology_replacements: list[MethodologyReplacement] = Field(default_factory=list)
    interest_base_items: list[InterestBaseItem] = Field(default_factory=list)
    calculation_issues: list[CalculationIssue] = Field(default_factory=list)
    claim_comparisons: list[ClaimComparisonItem] = Field(default_factory=list)
    cross_ref_issues: list[dict] = Field(default_factory=list, description="交叉验证问题")
    law_verifications: list[dict] = Field(default_factory=list, description="法条在线验证结果")
    case_verifications: list[dict] = Field(default_factory=list, description="案例在线验证结果")
    total_flags: int = Field(default=0)
    hallucination_score: float = Field(default=0.0, description="幻觉风险评分 0-100")
    risk_grade: str = Field(default="", description="风险等级 A/B/C/D/F")
    risk_description: str = Field(default="")
    report_markdown: str = Field(default="")
    agent_name: str = Field(default="TraeCN", description="AI Agent名称")
    llm_name: str = Field(default="", description="LLM名称和版本号")
    report_version: str = Field(default="v2.0", description="报告版本号")


class SectionExtractionResult(BaseModel):
    plaintiff_claim: str = Field(default="")
    defendant_defense: str = Field(default="")
    court_finding: str = Field(default="")
    evidence_analysis: str = Field(default="")
    reasoning: str = Field(default="")
    judgment_basis: str = Field(default="")
    judgment_main: str = Field(default="")
    case_info: dict = Field(default_factory=dict)
    trial_stage: str = Field(default="")
    extraction_confidence: float = Field(default=0.0)
