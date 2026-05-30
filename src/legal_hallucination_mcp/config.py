# ruff: noqa: E501
"""法律文书幻觉检测 MCP 服务器配置管理 v0.1.0。

定义全部规则引擎模式、维度元数据、权重和应用配置。
"""

import importlib
import logging
import os
from enum import StrEnum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

logger = logging.getLogger("legal-hallucination")

SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
ANCHORS_DIR = Path(__file__).resolve().parent.parent.parent / "anchors"

HALLUCINATION_DIMENSIONS = [
    "h1_sourceless_fabrication",
    "h2_law_misapplication",
    "h3_evidence_chain_break",
    "h4_subjective_rhetoric",
    "h5_claim_boundary_breach",
    "h6_nontext_evidence_fail",
]

DIMENSION_TITLES = {
    "h1_sourceless_fabrication": "H-1 无源编造事实",
    "h2_law_misapplication": "H-2 法律适用错误",
    "h3_evidence_chain_break": "H-3 证据链断裂",
    "h4_subjective_rhetoric": "H-4 主观臆断/修辞过度",
    "h5_claim_boundary_breach": "H-5 诉求边界突破",
    "h6_nontext_evidence_fail": "H-6 非文本证据穿透失败",
}

DIMENSION_ORDER = {
    "h1_sourceless_fabrication": 1,
    "h2_law_misapplication": 2,
    "h3_evidence_chain_break": 3,
    "h4_subjective_rhetoric": 4,
    "h5_claim_boundary_breach": 5,
    "h6_nontext_evidence_fail": 6,
}

DIMENSION_WEIGHTS = {
    "h1_sourceless_fabrication": 0.25,
    "h2_law_misapplication": 0.20,
    "h3_evidence_chain_break": 0.20,
    "h4_subjective_rhetoric": 0.10,
    "h5_claim_boundary_breach": 0.20,
    "h6_nontext_evidence_fail": 0.05,
}

HALLUCINATION_SEVERITY = {
    "critical": {"level": 4, "label": "致命"},
    "high": {"level": 3, "label": "严重"},
    "medium": {"level": 2, "label": "中等"},
    "low": {"level": 1, "label": "轻微"},
    "info": {"level": 0, "label": "提示"},
}

RISK_GRADES = {
    "A": (0, 5, "极低风险：文书几乎无幻觉痕迹"),
    "B": (5, 15, "低风险：存在少量轻微幻觉，不影响裁判结论"),
    "C": (15, 30, "中风险：存在多处幻觉，可能影响裁判公正性"),
    "D": (30, 50, "高风险：幻觉密集，裁判结论可信度存疑"),
    "F": (50, 101, "极高风险：幻觉泛滥，文书基本不可信"),
}

DOCUMENT_SECTIONS = [
    "header",
    "plaintiff_claim",
    "defendant_defense",
    "court_finding",
    "evidence_analysis",
    "reasoning",
    "judgment_basis",
    "judgment_main",
    "footer",
]

REQUIRED_STRUCTURE_HEADINGS = [
    "# 一、当事人的诉讼请求与主张",
    "# 二、本院查明事实",
    "# 三、本院认为",
    "# 四、判决如下",
]


class DetectionConfig(BaseModel):
    """检测流程可配置参数，支持从环境变量或JSON文件加载。"""

    score_multiplier_critical: float = Field(default=4.0, description="致命级别评分乘数")
    score_multiplier_high: float = Field(default=3.0, description="严重级别评分乘数")
    score_multiplier_medium: float = Field(default=2.0, description="中等级别评分乘数")
    score_multiplier_low: float = Field(default=1.0, description="轻微级别评分乘数")
    score_multiplier_info: float = Field(default=0.5, description="提示级别评分乘数")
    claim_deviation_threshold: float = Field(default=1.2, description="诉请偏差阈值（倍数）")
    claim_significant_threshold: float = Field(default=2.0, description="诉请显著超出阈值")
    similarity_threshold: float = Field(default=0.3, description="文本相似度判定阈值")
    short_cite_max_length: int = Field(default=4, description="短引注最大字符数")
    evidence_cache_enabled: bool = Field(default=True, description="证据索引缓存开关")
    circuit_max_failures: int = Field(default=3, description="熔断器最大失败次数")
    circuit_reset_seconds: int = Field(default=300, description="熔断器重置间隔（秒）")
    max_flags_per_rule: int = Field(default=10, description="每条规则最大标记数")
    online_verify_enabled: bool = Field(default=True, description="在线验证开关")

    @classmethod
    def from_env(cls) -> "DetectionConfig":
        """从环境变量加载配置，前缀 LH_。"""
        import os as _os
        kwargs = {}
        env_map = {
            "LH_SCORE_CRITICAL": ("score_multiplier_critical", float),
            "LH_SCORE_HIGH": ("score_multiplier_high", float),
            "LH_SCORE_MEDIUM": ("score_multiplier_medium", float),
            "LH_SCORE_LOW": ("score_multiplier_low", float),
            "LH_SCORE_INFO": ("score_multiplier_info", float),
            "LH_CLAIM_DEVIATION": ("claim_deviation_threshold", float),
            "LH_CLAIM_SIGNIFICANT": ("claim_significant_threshold", float),
            "LH_SIMILARITY_THRESHOLD": ("similarity_threshold", float),
            "LH_SHORT_CITE_MAX": ("short_cite_max_length", int),
            "LH_CACHE_ENABLED": ("evidence_cache_enabled", lambda x: x.lower() == "true"),
            "LH_CIRCUIT_MAX": ("circuit_max_failures", int),
            "LH_CIRCUIT_RESET": ("circuit_reset_seconds", int),
            "LH_MAX_FLAGS": ("max_flags_per_rule", int),
            "LH_ONLINE_VERIFY": ("online_verify_enabled", lambda x: x.lower() == "true"),
        }
        for env_key, (field_name, converter) in env_map.items():
            val = _os.environ.get(env_key)
            if val is not None:
                try:
                    kwargs[field_name] = converter(val)
                except (ValueError, TypeError):
                    logger.warning("Invalid env %s=%s, using default", env_key, val)
        return cls(**kwargs)


DEFAULT_CONFIG = DetectionConfig()

RULE_ENGINE_PATTERNS = {
    "h1_internal_version_ref": {
        "pattern": r"[Vv]\d{2,3}[Pp]?\d?版本|本版本|此前版本|V\d{2,3}此前|以V\d{2,3}为框架",
        "section": "body",
        "severity": "critical",
        "h_code": "H-1",
        "sub_type": "内部版本引用",
        "message": "判决书正文中出现AI生成文档内部版本号引用，暴露LLM生成物本质",
    },
    "h1_fabricated_judicial_doc_number": {
        "pattern": r"法释〔\d{4}〕\d+号",
        "section": "body",
        "severity": "high",
        "h_code": "H-1",
        "sub_type": "法条虚构",
        "message": "司法解释文号需联网验证存在性，历史案例：V40P1虚构'法释〔2025〕12号'",
        "known_valid": [
            "法释〔2020〕26号",
            "法释〔2001〕14号",
            "法释〔2010〕12号",
            "法释〔2013〕4号",
            "法释〔2015〕5号",
            "法释〔2017〕5号",
            "法释〔2018〕3号",
            "法释〔2019〕11号",
            "法释〔2021〕1号",
            "法释〔2022〕11号",
            "法释〔2025〕12号",
        ],
    },
    "h3_reasoning_no_law_basis": {
        "pattern": r"(?:应予支持|不予支持|应当支付|确认|驳回)",
        "section": "reasoning",
        "severity": "high",
        "h_code": "H-3",
        "sub_type": "三段论缺失大前提",
        "message": "说理含支持/驳回结论但缺少法律依据《》",
        "requires_absent": r"《[^》]+》",
    },
    "h3_reasoning_no_evidence": {
        "pattern": r"(?:应予支持|不予支持|应当支付|确认|驳回)",
        "section": "reasoning",
        "severity": "high",
        "h_code": "H-3",
        "sub_type": "三段论缺失小前提",
        "message": "说理含支持/驳回结论但缺少证据锚点",
        "requires_absent": r"见《|见证据|证据\d",
    },
    "h4_moral_judgment": {
        "pattern": r"(?:恶意|卑劣|无耻|明目张胆|极其恶劣|触目惊心|令人愤慨|肆无忌惮|丧心病狂|蓄意|居心叵测)",
        "section": "body",
        "severity": "medium",
        "h_code": "H-4",
        "sub_type": "道德评价",
        "message": "使用主观感情色彩词汇，违反司法中立性",
        "exceptions": r"恶意串通|恶意取得|善意取得|恶意隐匿证据|恶意诉讼|恶意减资|恶意涂黑|恶意遮挡|恶意欠薪|恶意妨碍|恶意规避|恶意遮掩|恶意遮蔽|恶意隐匿|恶意透支|举证妨碍|恶意程度|逃避.*恶意|恶意.*逃",
    },
    "h4_intent_inference": {
        "pattern": r"主观(?:意图|恶意|目的|上有)|故意隐瞒|蓄意(?:规避|逃避|拖延)",
        "section": "body",
        "severity": "medium",
        "h_code": "H-4",
        "sub_type": "意图推断",
        "message": "无证据推断当事人主观状态，违反证据裁判原则",
        "exceptions": r"录音.*表述|录音.*证明|恶意程度|《[^》]+》.*主观|第[^条]+条.*主观|证据.*证明.*主观",
    },
    "h4_emotional_rhetoric": {
        "pattern": r"令人(?:愤慨|震惊|不齿|发指)|触目惊心|令人痛心|性质恶劣|情节恶劣|手段恶劣",
        "section": "body",
        "severity": "medium",
        "h_code": "H-4",
        "sub_type": "情感化修辞",
        "message": "使用情感化修辞替代客观描述",
    },
    "h6_nontext_evidence_unmarked": {
        "pattern": r"(?:录音|录像|视频|照片|实物|鉴定意见|勘验笔录)",
        "section": "body",
        "severity": "low",
        "h_code": "H-6",
        "sub_type": "非文本证据未标注",
        "message": "引用非文本证据但未标注来源形式",
        "requires_absent": r"据.*整理|据.*陈述|据.*笔录|待确认|待核实|在.*录音|录音.*陈述|录音.*表述|录音.*证明|鉴定意见.*认定|勘验.*查明",
    },
    "h1_fabricated_case_number": {
        "pattern": r"[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?第?\d+号",
        "section": "body",
        "severity": "high",
        "h_code": "H-1",
        "sub_type": "类案案号杜撰",
        "message": "类案案号需联网验证存在性，大模型常杜撰案号或张冠李戴",
        "requires_absent": r"模拟|虚构|假设|仅供|参考|待验证|已验证|经核实|法发|法释|人社部发|国办发|国务院令",
    },
    "h1_simulated_case_annotated": {
        "pattern": r"模拟[^，。；\n]*?类案[^\n]*?[（\(]?\d{4}[）)]?\s*[^\s，。；]+?民[^\s，。；]*?\d+号",
        "section": "body",
        "severity": "low",
        "h_code": "H-1",
        "sub_type": "模拟类案已标注",
        "message": "类案已标注模拟属性，但案号格式仍可能误导读者，建议改为'模拟类案A-01'等标识",
    },
    "h1_fabricated_fact_detail": {
        "pattern": r"(?:据查|经查|查明|经审理认定|经审理查明)\s*[^，。；\n]*?(?:身份证号|户籍|籍贯|高考|学历背景|家庭背景|个人隐私)",
        "section": "body",
        "severity": "high",
        "h_code": "H-1",
        "sub_type": "事实细节杜撰",
        "message": "从身份证号推断籍贯/高考难度、编造个人背景等属于典型H-1无源编造",
    },
    "h2_law_article_mismatch": {
        "pattern": r"《[^》]+》第[一二三四五六七八九十百千零\d]+条",
        "section": "reasoning",
        "severity": "medium",
        "h_code": "H-2",
        "sub_type": "法条张冠李戴",
        "message": "法条引用需验证条款内容与引用目的的匹配性，大模型常将A法的条款张冠李戴到B法的事实上",
    },
    "h2_procedural_date_fabrication": {
        "pattern": r"(?:一审|二审|再审|发回|受理|送达|立案|开庭|宣判|裁定)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        "section": "body",
        "severity": "high",
        "h_code": "H-2",
        "sub_type": "程序日期杜撰",
        "message": "程序性日期需与法定时效和已知时间线交叉验证，大模型常杜撰裁定日期或无视时效倒推日期",
    },
    "h5_calculation_amount_mismatch": {
        "pattern": r"(?:共计|合计|总计|应支付|应赔偿)\s*(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元",
        "section": "judgment_main",
        "severity": "high",
        "h_code": "H-5",
        "sub_type": "计算金额存疑",
        "message": "判决主文中的汇总金额需与各项明细加总核对，大模型常出现计算错误或数字不一致",
    },
    "h1_vague_legal_basis": {
        "pattern": r"(?:根据|依照|按照|依据)(?:国家|我国|法律|法规|规定|有关|相关|上述)(?:的)?(?:规定|法律|法规|条款|精神)",
        "section": "reasoning",
        "severity": "high",
        "h_code": "H-1",
        "sub_type": "模糊法源引用",
        "message": "使用'根据国家相关规定'等模糊表述替代具体法条引用，属于典型H-1无源编造",
        "exceptions": r"《[^》]+》|第[一二三四五六七八九十百千零\d]+条",
    },
    "h2_date_sequence_anomaly": {
        "pattern": r"(?:受理|立案)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日[^\n]*?(?:宣判|判决|裁定)[^\n]*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日",
        "section": "body",
        "severity": "medium",
        "h_code": "H-2",
        "sub_type": "日期时序异常",
        "message": "受理日期晚于宣判日期等时序倒置，属于H-2程序日期杜撰",
    },
    "h3_conclusion_without_reasoning": {
        "pattern": r"(?:综上|因此|故|据此|据此，?)[，,]?\s*(?:本院|法院|法庭)?(?:认为|认定|确认|判定|判决|裁定)",
        "section": "reasoning",
        "severity": "high",
        "h_code": "H-3",
        "sub_type": "结论无推理过程",
        "message": "直接以'综上，本院认为'得出结论但缺少中间推理步骤，属于H-3三段论断裂",
        "requires_absent": r"因为|由于|鉴于|基于|理由|依据",
    },
    "h5_interest_calculation_anomaly": {
        "pattern": r"(?:利息|逾期利息|迟延履行)[^\n]*?(?:计算|支付|给付)[^\n]*?(\d+(?:[,，]\d{3})*(?:\.\d+)?)\s*元",
        "section": "judgment_main",
        "severity": "medium",
        "h_code": "H-5",
        "sub_type": "利息计算存疑",
        "message": "利息金额需与本金、利率、期限交叉验证，大模型常编造利息金额",
    },
}

STRUCTURE_CHECK_RULES = {
    "missing_section_1": {
        "heading": "# 一、当事人的诉讼请求与主张",
        "alt_patterns": [
            r"[#]+\s*一[、.．]\s*(?:当事人的)?(?:诉讼请求|诉辩|诉请|诉辩主张)",
            r"[#]+\s*(?:一审)?(?:诉讼请求|诉辩主张)(?:及认定)?",
            r"[#]+\s*一[、.．]\s*.*(?:诉辩|诉讼请求|主张)",
        ],
        "severity": "critical",
        "h_code": "H-3",
        "message": "缺少第一部分：当事人的诉讼请求与主张",
    },
    "missing_section_2": {
        "heading": "# 二、本院查明事实",
        "alt_patterns": [
            r"[#]+\s*二[、.．]\s*(?:本院)?(?:查明|认定)(?:的)?事实",
            r"[#]+\s*(?:二审查明|事实查明|经审理查明|举证.*质证.*事实查明)",
            r"[#]+\s*二[、.．]\s*.*(?:查明|事实|举证|质证)",
            r"[#]+\s*[二三][、.．]\s*.*(?:举证|质证|事实查明|审理查明)",
        ],
        "severity": "critical",
        "h_code": "H-3",
        "message": "缺少第二部分：本院查明事实",
    },
    "missing_section_3": {
        "heading": "# 三、本院认为",
        "alt_patterns": [
            r"[#]+\s*三[、.．]\s*(?:本院认为|争议焦点|裁判理由)",
            r"[#]+\s*(?:本院认为|争议焦点分析|争议焦点)",
        ],
        "severity": "critical",
        "h_code": "H-3",
        "message": "缺少第三部分：本院认为（说理部分）",
    },
    "missing_section_4": {
        "heading": "# 四、判决如下",
        "alt_patterns": [
            r"[#]+\s*四[、.．]\s*(?:判决|裁定|裁判)(?:如下|结果|主文)",
            r"[#]+\s*(?:裁判结果|判决主文|裁定如下|裁判给付金额)",
            r"[#]+\s*[四4][、.．]\s*.*(?:裁判|给付|判决)",
            r"[#]+\s*[四五六][、.．]\s*.*(?:裁判|给付|判决|金额总汇)",
        ],
        "severity": "critical",
        "h_code": "H-3",
        "message": "缺少第四部分：判决如下（判决主文）",
    },
}

LAW_CITATION_FORMAT_RULES = {
    "law_name_full": {
        "description": "法律名称首次引用须用全称+简称标注",
        "pattern": r"《[^》]+》（以下简称《[^》]+》）",
        "check_type": "should_exist_on_first_mention",
    },
    "judicial_interpretation_format": {
        "description": "司法解释须含全称+文号",
        "pattern": r"《[^》]+》（法释〔\d{4}〕\d+号）",
        "check_type": "format_suggestion",
    },
    "admin_regulation_format": {
        "description": "行政规章须含全称+文号",
        "pattern": r"《[^》]+》（[^）]*〔\d{4}〕\d+号）",
        "check_type": "format_suggestion",
    },
}

SUBJECTIVE_RHETORIC_KEYWORDS = {
    "high_severity": [
        "恶意", "卑劣", "无耻", "明目张胆", "极其恶劣",
        "触目惊心", "令人愤慨", "肆无忌惮", "丧心病狂",
        "欺诈", "主观恶意极为严重",
    ],
    "medium_severity": [
        "蓄意", "居心叵测", "故意隐瞒", "蓄意规避",
        "主观意图", "主观恶意", "主观目的",
        "令人震惊", "令人不齿", "令人发指", "令人痛心",
        "性质恶劣", "情节恶劣", "手段恶劣",
    ],
    "low_severity": [
        "拖延", "怠于",
    ],
    "exceptions": [
        "恶意串通", "恶意取得", "善意取得",
        "恶意隐匿证据", "恶意诉讼", "恶意透支",
        "恶意减资", "恶意涂黑", "恶意遮挡",
        "恶意欠薪", "恶意规避", "恶意妨碍",
        "恶意遮蔽", "恶意隐匿",
        "逾期不支付", "逾期未支付", "逾期支付",
        "迟至.*仍", "迟延履行",
        "《.*?》.*?恶意", "第.*?条.*?恶意",
        "法释.*?恶意", "法.*?规定.*?逾期",
        "举证妨碍", "举证妨碍行为",
    ],
}

METHODOLOGY_REPLACEMENT_RULES = [
    {
        "claim_basis": "劳部发〔1995〕223号",
        "judgment_basis": "《中华人民共和国民法典》",
        "claim_item": "25%赔偿金",
        "replacement_type": "法律依据替换",
        "impact_analysis": "起诉状援引劳部发〔1995〕223号作为25%赔偿金的法律依据（劳动法特别赔偿），"
                          "判决书转而引用《民法典》违约责任条款（民法一般违约责任），"
                          "两者法理基础不同：前者为'加付'性质，后者为'填补'性质。"
                          "在劳动争议中直接适用《民法典》违约责任条款存在争议——"
                          "劳动法作为特别法，其赔偿体系是否穷尽了民法一般规则的适用空间。",
        "h_code": "H-5",
        "severity": "high",
        "claim_pattern": r"劳部发〔1995〕223号|违反.*劳动法.*赔偿办法",
        "judgment_pattern": r"《中华人民共和国民法典》第577条|《中华人民共和国民法典》第584条",
    },
    {
        "claim_basis": "原告自身历史收入",
        "judgment_basis": "同工同酬",
        "claim_item": "奖金/工资差额",
        "replacement_type": "参照系替换",
        "impact_analysis": "起诉状以原告自身历史收入为参照（保守算法），"
                          "判决书以第三人薪酬为参照（激进算法），"
                          "两种算法基于完全不同的参照系，"
                          "可能导致判决金额远超起诉状请求。",
        "h_code": "H-5",
        "severity": "medium",
        "claim_pattern": r"平均.*月收入|历史.*收入|原告.*收入",
        "judgment_pattern": r"同工同酬|参照.*薪酬|参照.*工资标准",
    },
]

PROCEDURAL_TIME_LIMITS = {
    "劳动争议仲裁时效": {
        "statute": "《劳动争议调解仲裁法》第二十七条",
        "limit_type": "year",
        "limit_value": 1,
        "start_event": "劳动关系终止/知道或应当知道权利被侵害",
        "description": "劳动争议申请仲裁的时效期间为一年，从当事人知道或应当知道其权利被侵害之日起计算。劳动关系存续期间因拖欠劳动报酬发生争议的，不受此限；但劳动关系终止的，应当自终止之日起一年内提出。",
    },
    "民事上诉期": {
        "statute": "《民事诉讼法》第一百七十一条",
        "limit_type": "day",
        "limit_value": 15,
        "start_event": "一审判决书送达之日",
        "description": "当事人不服地方人民法院第一审判决的，有权在判决书送达之日起十五日内向上一级人民法院提起上诉。",
    },
    "民事申请再审期限": {
        "statute": "《民事诉讼法》第二百一十六条",
        "limit_type": "day",
        "limit_value": 180,
        "start_event": "判决/裁定发生法律效力之日",
        "description": "当事人申请再审，应当在判决、裁定发生法律效力后六个月内提出。",
    },
    "仲裁裁决起诉期限": {
        "statute": "《劳动争议调解仲裁法》第四十八条/第五十条",
        "limit_type": "day",
        "limit_value": 15,
        "start_event": "收到仲裁裁决书之日",
        "description": "劳动者对仲裁裁决不服的，可以自收到仲裁裁决书之日起十五日内向人民法院提起诉讼。",
    },
    "申请执行期限": {
        "statute": "《民事诉讼法》第二百五十条",
        "limit_type": "day",
        "limit_value": 365,
        "start_event": "判决/裁定规定的履行期限最后一日",
        "description": "申请执行的期间为二年。申请执行时效的中止、中断，适用法律有关诉讼时效中止、中断的规定。",
    },
    "民事一审立案审查期": {
        "statute": "《民事诉讼法》第一百二十六条",
        "limit_type": "day",
        "limit_value": 7,
        "start_event": "收到起诉状之日",
        "description": "人民法院应当自收到起诉状之日起七日内立案，并通知当事人。",
    },
    "二审立案移送期": {
        "statute": "《民事诉讼法》第一百七十四条",
        "limit_type": "day",
        "limit_value": 30,
        "start_event": "一审法院收到上诉状之日",
        "description": "原审人民法院收到上诉状后，应当在五日内将上诉状副本送达对方当事人，并在三十日内连同全部案卷和证据报送第二审人民法院。",
    },
    "劳动争议终局裁决撤销申请期": {
        "statute": "《劳动争议调解仲裁法》第四十九条",
        "limit_type": "day",
        "limit_value": 30,
        "start_event": "收到仲裁裁决书之日",
        "description": "用人单位有证据证明终局裁决有法定情形之一的，可以自收到仲裁裁决书之日起三十日内向劳动争议仲裁委员会所在地的中级人民法院申请撤销裁决。",
    },
}

LAW_ARTICLE_COMPATIBILITY = {
    "《中华人民共和国劳动合同法》": {
        "第82条": {"适用场景": "未签书面劳动合同的二倍工资", "常见张冠李戴": "违法解除赔偿金；违法解除劳动合同"},
        "第87条": {"适用场景": "违法解除劳动合同的赔偿金（2倍经济补偿）", "常见张冠李戴": "未签合同二倍工资；二倍工资差额"},
        "第85条": {"适用场景": "未及时足额支付劳动报酬的加付赔偿金（50%-100%）", "常见张冠李戴": "违约损害赔偿；违约金"},
        "第47条": {"适用场景": "经济补偿的计算（N）", "常见张冠李戴": "违法解除赔偿金；2N"},
        "第46条": {"适用场景": "应当支付经济补偿的情形", "常见张冠李戴": "遗漏支付情形；增加支付情形"},
        "第48条": {"适用场景": "违法解除的救济选择（继续履行或赔偿金）", "常见张冠李戴": "忽略继续履行"},
        "第14条": {"适用场景": "无固定期限劳动合同的订立条件", "常见张冠李戴": "二倍工资；第82条"},
        "第10条": {"适用场景": "建立劳动关系应订立书面合同", "常见张冠李戴": "二倍工资罚则；第82条"},
    },
    "《中华人民共和国民法典》": {
        "第577条": {"适用场景": "违约责任一般规定", "常见张冠李戴": "劳动争议；劳动法特别规定"},
        "第584条": {"适用场景": "违约损害赔偿范围（可预见规则）", "常见张冠李戴": "加付赔偿金；劳动法"},
        "第188条": {"适用场景": "民事诉讼时效3年", "常见张冠李戴": "劳动争议仲裁时效；1年时效"},
        "第119条": {"适用场景": "合同的约束力", "常见张冠李戴": "劳动合同法特别规定；劳动争议"},
    },
    "《中华人民共和国劳动法》": {
        "第50条": {"适用场景": "工资应当以货币形式按月支付", "常见张冠李戴": "加付赔偿金；第85条"},
        "第91条": {"适用场景": "用人单位侵害劳动者合法权益的赔偿责任", "常见张冠李戴": "加付赔偿金；第85条"},
    },
    "《中华人民共和国劳动争议调解仲裁法》": {
        "第27条": {"适用场景": "劳动争议仲裁时效1年", "常见张冠李戴": "3年诉讼时效；民法典"},
        "第47条": {"适用场景": "终局裁决的适用范围", "常见张冠李戴": "扩大终局裁决；缩小终局裁决"},
        "第48条": {"适用场景": "劳动者对终局裁决的起诉权（15日）", "常见张冠李戴": "撤销申请权；30日"},
        "第49条": {"适用场景": "用人单位申请撤销终局裁决（30日）", "常见张冠李戴": "起诉权；15日"},
    },
}

REPLACED_LAWS = {
    "中华人民共和国合同法": {
        "replaced_by": "《中华人民共和国民法典》第三编（合同编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国侵权责任法": {
        "replaced_by": "《中华人民共和国民法典》第七编（侵权责任编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国民法通则": {
        "replaced_by": "《中华人民共和国民法典》",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国民法总则": {
        "replaced_by": "《中华人民共和国民法典》第一编（总则编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国婚姻法": {
        "replaced_by": "《中华人民共和国民法典》第五编（婚姻家庭编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国继承法": {
        "replaced_by": "《中华人民共和国民法典》第六编（继承编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国收养法": {
        "replaced_by": "《中华人民共和国民法典》第五编（婚姻家庭编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国担保法": {
        "replaced_by": "《中华人民共和国民法典》第二编（物权编）及第三编（合同编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
    "中华人民共和国物权法": {
        "replaced_by": "《中华人民共和国民法典》第二编（物权编）",
        "effective_date": "2021-01-01",
        "repeal_date": "2021-01-01",
    },
}

REPLACED_LAWS_SIMPLE = {k: f"{v['replaced_by']}（{v['effective_date']}起施行）" for k, v in REPLACED_LAWS.items()}

_CHARS_PER_TOKEN_ZH = 1.5
_CHARS_PER_TOKEN_EN = 4.0


class ErrorCode(StrEnum):
    SKILL_NOT_FOUND = "SKILL_404"
    TOKEN_OVERFLOW = "TOKEN_500"
    PARSE_FAILED = "PARSE_400"
    INVALID_INPUT = "INPUT_400"
    INTERNAL_ERROR = "INTERNAL_500"
    MANIFEST_NOT_FOUND = "MANIFEST_404"
    DRAFT_NOT_FOUND = "DRAFT_404"
    DIMENSION_NOT_FOUND = "DIM_404"
    PIPELINE_NOT_FOUND = "PIPELINE_404"
    SESSION_NOT_FOUND = "SESSION_404"


class StructuredError(BaseModel):
    code: str = Field(description="错误码")
    message: str = Field(description="错误描述")
    details: dict = Field(default_factory=dict)
    retryable: bool = Field(default=False)


def make_error(code: ErrorCode, message: str, details: dict | None = None, retryable: bool = False) -> str:
    import json
    err = StructuredError(code=code.value, message=message, details=details or {}, retryable=retryable)
    return json.dumps({"success": False, "error": err.model_dump()}, ensure_ascii=False, indent=2)


def _detect_quality_mcp() -> bool:
    try:
        mod = importlib.import_module("judicial_quality_mcp")
        return hasattr(mod, "server") or importlib.util.find_spec("judicial_quality_mcp.server") is not None
    except (ImportError, ModuleNotFoundError):
        return False


def _detect_anomaly_mcp() -> bool:
    try:
        mod = importlib.import_module("judicial_lint_mcp")
        return hasattr(mod, "server") or importlib.util.find_spec("judicial_lint_mcp.server") is not None
    except (ImportError, ModuleNotFoundError):
        return False


QUALITY_MCP_AVAILABLE = _detect_quality_mcp()
ANOMALY_MCP_AVAILABLE = _detect_anomaly_mcp()


class AppConfig(BaseModel):
    skills_dir: str = Field(default=str(SKILLS_DIR))
    anchors_dir: str = Field(default=str(ANCHORS_DIR))
    manifest_path: str = Field(default="")
    draft_path: str = Field(default="")
    local_law_dir: str = Field(default="")
    vault_root: str = Field(default="")
    rule_engine_enabled: bool = Field(default=True)
    structure_check_enabled: bool = Field(default=True)
    citation_fraud_check_enabled: bool = Field(default=True)
    claim_boundary_check_enabled: bool = Field(default=True)
    rhetoric_check_enabled: bool = Field(default=True)
    law_citation_format_check_enabled: bool = Field(default=True)
    replaced_law_check_enabled: bool = Field(default=True)
    quality_mcp_available: bool = Field(default=False)
    anomaly_mcp_available: bool = Field(default=False)
    verbose: bool = Field(default=False)

    @classmethod
    def from_env(cls) -> "AppConfig":
        load_dotenv()
        vault = os.getenv("VAULT_ROOT", "")
        return cls(
            skills_dir=os.getenv("SKILLS_DIR", str(SKILLS_DIR)),
            anchors_dir=os.getenv("ANCHORS_DIR", str(ANCHORS_DIR)),
            manifest_path=os.getenv("EVIDENCE_MANIFEST_PATH", ""),
            draft_path=os.getenv("JUDGMENT_DRAFT_PATH", ""),
            local_law_dir=os.getenv("LOCAL_LAW_DIR", ""),
            vault_root=vault,
            rule_engine_enabled=os.getenv("RULE_ENGINE_ENABLED", "true").lower() == "true",
            structure_check_enabled=os.getenv("STRUCTURE_CHECK_ENABLED", "true").lower() == "true",
            citation_fraud_check_enabled=os.getenv("CITATION_FRAUD_CHECK_ENABLED", "true").lower() == "true",
            claim_boundary_check_enabled=os.getenv("CLAIM_BOUNDARY_CHECK_ENABLED", "true").lower() == "true",
            rhetoric_check_enabled=os.getenv("RHETORIC_CHECK_ENABLED", "true").lower() == "true",
            law_citation_format_check_enabled=os.getenv("LAW_CITATION_FORMAT_CHECK_ENABLED", "true").lower() == "true",
            replaced_law_check_enabled=os.getenv("REPLACED_LAW_CHECK_ENABLED", "true").lower() == "true",
            quality_mcp_available=QUALITY_MCP_AVAILABLE,
            anomaly_mcp_available=ANOMALY_MCP_AVAILABLE,
            verbose=os.getenv("VERBOSE", "false").lower() == "true",
        )
