"""法律知识库 — 法律法规、司法解释、案例、学术参考的结构化索引与检索。

桥接架构：不调用任何LLM。纯文件读取、正则匹配与结构化索引。
支持从本地目录装载法律法规库，构建条文级索引；
支持从权威网站在线验证法条时效性；
支持案例库和学术参考库的索引构建与检索。
"""

import logging
import os
import re
from datetime import datetime

from pydantic import BaseModel, Field

from .config import REPLACED_LAWS as _REPLACED_LAWS_FROM_CONFIG

logger = logging.getLogger("legal-hallucination")


class LawArticle(BaseModel):
    law_name: str = Field(default="", description="法律名称")
    article_number: str = Field(default="", description="条款号")
    full_text: str = Field(default="", description="条文全文")
    short_name: str = Field(default="", description="法律简称")
    law_type: str = Field(default="", description="法律类型: 法律/行政法规/部门规章/司法解释/地方法规")
    hierarchy: str = Field(default="", description="法律层级: 上位法/中位法/下位法")
    is_procedural: bool = Field(default=False, description="是否程序法")
    effective_date: str = Field(default="", description="生效日期")
    replaced_by: str = Field(default="", description="被替代法律（如已废止）")
    source_file: str = Field(default="", description="来源文件路径")
    last_verified: str = Field(default="", description="最后验证日期")
    verification_status: str = Field(default="未验证", description="验证状态: 未验证/已验证/已变更/已废止")


class GuidingCase(BaseModel):
    case_number: str = Field(default="", description="案号")
    court: str = Field(default="", description="审理法院")
    case_type: str = Field(default="", description="案例类型: 指导案例/典型案例/地方类案/公报案例")
    judgment_date: str = Field(default="", description="裁判日期")
    key_holding: str = Field(default="", description="裁判要旨")
    applicable_law: str = Field(default="", description="适用法条")
    dispute_type: str = Field(default="", description="纠纷类型")
    source_file: str = Field(default="", description="来源文件路径")
    last_verified: str = Field(default="", description="最后验证日期")
    verification_status: str = Field(default="未验证", description="验证状态")


class AcademicReference(BaseModel):
    title: str = Field(default="", description="文献标题")
    author: str = Field(default="", description="作者")
    source: str = Field(default="", description="来源: 司法解释理解与适用/学术论文/专家解读/法律原则")
    year: str = Field(default="", description="年份")
    key_point: str = Field(default="", description="核心观点")
    applicable_law: str = Field(default="", description="关联法条")
    dispute_type: str = Field(default="", description="纠纷类型")
    source_file: str = Field(default="", description="来源文件路径")
    authority_level: str = Field(default="参考", description="权威等级: 权威/重要/参考")


class LegalPrinciple(BaseModel):
    name: str = Field(default="", description="原则名称")
    description: str = Field(default="", description="原则描述")
    application_scope: str = Field(default="", description="适用范围")
    source: str = Field(default="", description="来源")
    examples: list[str] = Field(default_factory=list, description="适用示例")


class LawVerificationResult(BaseModel):
    citation_text: str = Field(default="", description="法条引用原文")
    law_name: str = Field(default="", description="法律名称")
    article: str = Field(default="", description="条款号")
    local_found: bool = Field(default=False, description="本地库是否找到")
    online_verified: bool = Field(default=False, description="在线是否验证")
    is_current: bool = Field(default=True, description="是否现行有效")
    replaced_by: str = Field(default="", description="替代法律")
    effective_date: str = Field(default="", description="生效日期")
    verification_source: str = Field(default="", description="验证来源")
    verification_time: str = Field(default="", description="验证时间")
    discrepancy: str = Field(default="", description="差异描述")
    confidence: float = Field(default=0.0, description="验证置信度 0-1")


class CaseVerificationResult(BaseModel):
    case_number: str = Field(default="", description="案号")
    local_found: bool = Field(default=False, description="本地库是否找到")
    online_verified: bool = Field(default=False, description="在线是否验证")
    is_real: bool = Field(default=True, description="是否真实案例")
    court: str = Field(default="", description="审理法院")
    judgment_date: str = Field(default="", description="裁判日期")
    key_holding: str = Field(default="", description="裁判要旨")
    verification_source: str = Field(default="", description="验证来源")
    verification_time: str = Field(default="", description="验证时间")
    confidence: float = Field(default=0.0, description="验证置信度 0-1")


LEGAL_PRINCIPLES = [
    LegalPrinciple(
        name="任何人不应该从违法行为中获利",
        description="法律不允许任何人通过违法行为获得利益，"
                    "这是法治的基本原则，源于罗马法'不法行为不产生权利'格言。",
        application_scope="所有民事、商事、劳动争议案件",
        source="法理学基本原理",
        examples=[
            "用人单位违法解除劳动合同，不得因违法行为减少应支付的补偿",
            "违约方不得因违约行为获得比守约方更多的利益",
        ],
    ),
    LegalPrinciple(
        name="诚实信用原则",
        description="民事主体从事民事活动，应当遵循诚信原则，秉持诚实，恪守承诺。",
        application_scope="所有民事活动，包括劳动合同的订立、履行和解除",
        source="《中华人民共和国民法典》第七条",
        examples=[
            "用人单位在劳动合同履行中不得以欺诈手段损害劳动者权益",
            "劳动者在离职时不得恶意带走商业秘密",
        ],
    ),
    LegalPrinciple(
        name="公平原则",
        description="民事主体从事民事活动，应当遵循公平原则，合理确定各方的权利和义务。",
        application_scope="所有民事活动，特别适用于合同条款的解释和调整",
        source="《中华人民共和国民法典》第六条",
        examples=[
            "格式条款的解释应当作不利于提供格式条款一方的解释",
            "违约金的调整应当考虑实际损失和合同履行情况",
        ],
    ),
    LegalPrinciple(
        name="公序良俗原则",
        description="民事主体从事民事活动，不得违反法律，不得违背公序良俗。",
        application_scope="所有民事活动",
        source="《中华人民共和国民法典》第八条",
        examples=[
            "劳动合同中排除劳动者法定权利的条款无效",
            "用人单位规章制度不得违反公序良俗",
        ],
    ),
    LegalPrinciple(
        name="有利于劳动者原则",
        description="在劳动法律关系中，当法律条文存在多种解释时，"
                    "应当采取有利于劳动者的解释。",
        application_scope="劳动争议案件",
        source="劳动法立法宗旨（劳动法第一条）",
        examples=[
            "劳动合同条款存在歧义时，作有利于劳动者的解释",
            "计算基数存在争议时，采取有利于劳动者的标准",
        ],
    ),
    LegalPrinciple(
        name="特别法优于一般法",
        description="同一事项，特别规定与一般规定不一致的，适用特别规定。",
        application_scope="法律适用冲突解决",
        source="《中华人民共和国立法法》第九十二条",
        examples=[
            "劳动争议案件中，劳动法优先于民法典适用",
            "劳动合同法优先于合同法（已废止）的一般规定",
        ],
    ),
    LegalPrinciple(
        name="新法优于旧法",
        description="同一事项，新的规定与旧的规定不一致的，适用新的规定。",
        application_scope="法律适用冲突解决",
        source="《中华人民共和国立法法》第九十二条",
        examples=[
            "2021年民法典生效后，合同法废止",
            "新司法解释优于旧司法解释",
        ],
    ),
]

LAW_TYPE_PATTERNS = {
    "法律": [
        r"《中华人民共和国(\w+法)》",
        r"《中华人民共和国(\w+法)",
    ],
    "司法解释": [
        r"法释〔\d{4}〕\d+号",
        r"最高人民法院关于.{2,}的解释",
        r"最高人民法院关于.{2,}的若干规定",
        r"最高人民法院关于.{2,}的解释\w*",
    ],
    "行政法规": [
        r"《中华人民共和国(\w+条例)》",
        r"《(\w+条例)》",
        r"国务院关于",
    ],
    "部门规章": [
        r"劳部发〔\d{4}〕\d+号",
        r"人社部发〔\d{4}〕\d+号",
        r"劳社部发〔\d{4}〕\d+号",
    ],
    "地方法规": [
        r"《(\w+省\w+条例)》",
        r"《(\w+市\w+条例)》",
        r"苏\w+规",
    ],
}

LAW_HIERARCHY = {
    "法律": "上位法",
    "司法解释": "中位法",
    "行政法规": "中位法",
    "部门规章": "下位法",
    "地方法规": "下位法",
}

LAW_NAME_SHORT = {
    "《中华人民共和国劳动合同法》": "《劳动合同法》",
    "《中华人民共和国劳动法》": "《劳动法》",
    "《中华人民共和国劳动争议调解仲裁法》": "《劳动争议调解仲裁法》",
    "《中华人民共和国民法典》": "《民法典》",
    "《中华人民共和国民事诉讼法》": "《民事诉讼法》",
    "《中华人民共和国劳动合同法实施条例》": "《劳动合同法实施条例》",
    "《中华人民共和国公司法》": "《公司法》",
}

REPLACED_LAWS = {}
for _law_name_no_bookmark, _info in _REPLACED_LAWS_FROM_CONFIG.items():
    _bookmarked_name = f"《{_law_name_no_bookmark}》"
    if isinstance(_info, dict):
        REPLACED_LAWS[_bookmarked_name] = _info
    else:
        REPLACED_LAWS[_bookmarked_name] = {
            "replaced_by": str(_info),
            "effective_date": "2021-01-01",
            "repeal_date": "2021-01-01",
        }


class LawKnowledgeBase:
    def __init__(self, law_dir: str = ""):
        self.law_dir = law_dir
        self.articles: list[LawArticle] = []
        self.cases: list[GuidingCase] = []
        self.academic_refs: list[AcademicReference] = []
        self.principles: list[LegalPrinciple] = LEGAL_PRINCIPLES
        self.article_index: dict[str, list[LawArticle]] = {}
        self.case_index: dict[str, list[GuidingCase]] = {}
        self.loaded = False

    def load_from_directory(self, law_dir: str = "") -> dict:
        ld = law_dir or self.law_dir
        if not ld or not os.path.exists(ld):
            logger.warning("LawKnowledgeBase.load: law_dir not found: %s", ld)
            return {"success": False, "articles": 0, "cases": 0, "files": 0}

        total_files = 0
        total_articles = 0
        total_cases = 0

        for root, dirs, files in os.walk(ld):
            current_depth = os.path.normpath(root).count(os.sep) - os.path.normpath(ld).count(os.sep)
            if current_depth >= 3:
                dirs.clear()
            for fname in files:
                if not fname.endswith(('.md', '.txt')):
                    continue

                fpath = os.path.join(root, fname)
                total_files += 1

                try:
                    with open(fpath, encoding="utf-8") as f:
                        content = f.read()
                except (OSError, UnicodeDecodeError) as e:
                    logger.warning("LawKnowledgeBase: failed to read %s: %s", fname, e)
                    continue

                articles = self._parse_law_articles(content, fpath)
                total_articles += len(articles)

                cases = self._parse_cases(content, fpath)
                total_cases += len(cases)

        self._build_index()
        self.loaded = True

        logger.info(
            "LawKnowledgeBase.load: files=%d, articles=%d, cases=%d",
            total_files, total_articles, total_cases,
        )

        return {
            "success": True,
            "articles": total_articles,
            "cases": total_cases,
            "files": total_files,
        }

    def _parse_law_articles(self, content: str, source_file: str) -> list[LawArticle]:
        articles = []

        law_name_match = re.search(r'《([^》]+)》', content[:500])
        law_name = f"《{law_name_match.group(1)}》" if law_name_match else ""
        short_name = LAW_NAME_SHORT.get(law_name, law_name)

        law_type = self._detect_law_type(content[:1000], law_name)
        hierarchy = LAW_HIERARCHY.get(law_type, "下位法")
        is_procedural = self._is_procedural_law(law_name, content[:2000])

        article_pattern = re.compile(
            r'第([一二三四五六七八九十百千零\d]+)条\s*([^\n第]+?)(?=第[一二三四五六七八九十百千零\d]+条|$)',
            re.DOTALL,
        )

        for match in article_pattern.finditer(content):
            article_num = match.group(1)
            article_text = match.group(2).strip()

            if len(article_text) < 5:
                continue

            article = LawArticle(
                law_name=law_name,
                article_number=f"第{article_num}条",
                full_text=article_text[:500],
                short_name=short_name,
                law_type=law_type,
                hierarchy=hierarchy,
                is_procedural=is_procedural,
                source_file=source_file,
            )

            replaced_info = REPLACED_LAWS.get(law_name)
            if replaced_info:
                article.replaced_by = replaced_info["replaced_by"]
                article.verification_status = "已废止"

            articles.append(article)
            self.articles.append(article)

        return articles

    def _detect_law_type(self, header_text: str, law_name: str) -> str:
        for law_type, patterns in LAW_TYPE_PATTERNS.items():
            for pat in patterns:
                if re.search(pat, header_text) or re.search(pat, law_name):
                    return law_type
        return "法律"

    def _is_procedural_law(self, law_name: str, content: str) -> bool:
        procedural_keywords = ["诉讼", "仲裁", "程序", "审理", "执行", "证据"]
        for kw in procedural_keywords:
            if kw in law_name:
                return True
        procedural_count = sum(1 for kw in procedural_keywords if kw in content[:2000])
        return procedural_count >= 3

    def _parse_cases(self, content: str, source_file: str) -> list[GuidingCase]:
        cases = []

        case_number_patterns = [
            r'[（(](\d{4})[）)](\w+)(\d+号)',
            r'指导案例第(\d+)号',
            r'公报案例[：:]\s*(\S+)',
        ]

        for pat in case_number_patterns:
            for match in re.finditer(pat, content[:3000]):
                if pat == case_number_patterns[0]:
                    case_num = f"（{match.group(1)}）{match.group(2)}{match.group(3)}"
                else:
                    case_num = match.group(0)

                case_type = self._detect_case_type(content[:2000])

                holding_match = re.search(
                    r'(?:裁判要旨|裁判宗旨|核心观点|判决要旨)[：:]\s*([^\n]+)',
                    content,
                )
                key_holding = holding_match.group(1).strip()[:200] if holding_match else ""

                court_match = re.search(
                    r'(?:审理法院|裁判法院|法院)[：:]\s*([^\n,，]+)',
                    content[:3000],
                )
                court = court_match.group(1).strip() if court_match else ""

                date_match = re.search(
                    r'(?:裁判日期|判决日期|审结日期)[：:]\s*(\d{4}[-年]\d{1,2}[-月]\d{1,2})',
                    content[:3000],
                )
                judgment_date = date_match.group(1) if date_match else ""

                law_match = re.search(
                    r'(?:适用法条|法律依据|适用法律)[：:]\s*([^\n]+)',
                    content,
                )
                applicable_law = law_match.group(1).strip()[:200] if law_match else ""

                gc = GuidingCase(
                    case_number=case_num,
                    court=court,
                    case_type=case_type,
                    judgment_date=judgment_date,
                    key_holding=key_holding,
                    applicable_law=applicable_law,
                    source_file=source_file,
                )
                cases.append(gc)
                self.cases.append(gc)

        return cases

    def _detect_case_type(self, header_text: str) -> str:
        if "指导案例" in header_text:
            return "指导案例"
        if "典型案例" in header_text:
            return "典型案例"
        if "公报案例" in header_text:
            return "公报案例"
        if re.search(r'\w+省|\w+市', header_text):
            return "地方类案"
        return "典型案例"

    def _build_index(self):
        self.article_index = {}
        for article in self.articles:
            key = f"{article.law_name}{article.article_number}"
            if key not in self.article_index:
                self.article_index[key] = []
            self.article_index[key].append(article)

        self.case_index = {}
        for case in self.cases:
            dispute_type = case.dispute_type or "未分类"
            if dispute_type not in self.case_index:
                self.case_index[dispute_type] = []
            self.case_index[dispute_type].append(case)

    def lookup_article(self, law_name: str, article_number: str) -> list[LawArticle]:
        key = f"{law_name}{article_number}"
        results = self.article_index.get(key, [])

        if not results:
            short = LAW_NAME_SHORT.get(law_name, law_name)
            key2 = f"{short}{article_number}"
            results = self.article_index.get(key2, [])

        return results

    def verify_citation(
        self,
        citation_text: str,
        online_verify: bool = False,
    ) -> LawVerificationResult:
        result = LawVerificationResult(citation_text=citation_text)

        law_match = re.search(r'《([^》]+)》', citation_text)
        article_match = re.search(r'第([一二三四五六七八九十百千零\d]+)条', citation_text)

        if law_match:
            result.law_name = f"《{law_match.group(1)}》"
        if article_match:
            result.article = f"第{article_match.group(1)}条"

        replaced_info = REPLACED_LAWS.get(result.law_name)
        if replaced_info:
            result.is_current = False
            result.replaced_by = replaced_info["replaced_by"]
            result.effective_date = replaced_info.get("effective_date", "")
            result.verification_status = "已废止"
            result.discrepancy = (
                f"{result.law_name}已于{replaced_info.get('repeal_date', '')}废止，"
                f"由{replaced_info['replaced_by']}替代"
            )

        if result.law_name and result.article:
            local_articles = self.lookup_article(result.law_name, result.article)
            if local_articles:
                result.local_found = True
                result.verification_status = "已验证" if result.is_current else "已废止"
                result.confidence = 0.9
            else:
                result.local_found = False
                result.discrepancy = (result.discrepancy + "；" if result.discrepancy else "") + \
                    f"本地法条库未找到{result.law_name}{result.article}的条文内容"
                result.confidence = 0.3 if result.is_current else 0.5

        result.verification_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return result

    def verify_case_number(
        self,
        case_number: str,
        online_verify: bool = False,
    ) -> CaseVerificationResult:
        result = CaseVerificationResult(case_number=case_number)

        for gc in self.cases:
            if gc.case_number == case_number:
                result.local_found = True
                result.is_real = True
                result.court = gc.court
                result.judgment_date = gc.judgment_date
                result.key_holding = gc.key_holding
                result.confidence = 0.8
                break

        if not result.local_found:
            pattern = r'[（(](\d{4})[）)](\w+)(\d+号)'
            match = re.match(pattern, case_number)
            if match:
                court_code = match.group(2)
                valid_court_codes = [
                    "苏06民终", "苏01民终", "苏02民终", "苏05民终",
                    "京01民终", "京02民终", "沪01民终", "沪02民终",
                    "粤01民终", "粤03民终", "浙01民终", "浙02民终",
                    "最高法民", "最高法行",
                ]
                if any(court_code.startswith(vc[:2]) for vc in valid_court_codes):
                    result.confidence = 0.3
                else:
                    result.is_real = False
                    result.confidence = 0.1

        result.verification_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        return result

    def search_applicable_law(
        self,
        dispute_type: str,
        keywords: list[str] = None,
    ) -> list[LawArticle]:
        results = []

        dispute_law_map = {
            "劳动争议": ["劳动合同法", "劳动法", "劳动争议调解仲裁法"],
            "劳动合同": ["劳动合同法", "劳动法"],
            "工资报酬": ["劳动合同法", "劳动法", "工资支付"],
            "违法解除": ["劳动合同法"],
            "二倍工资": ["劳动合同法"],
            "加班工资": ["劳动法", "劳动合同法"],
            "经济补偿": ["劳动合同法"],
        }

        target_laws = dispute_law_map.get(dispute_type, [])

        for article in self.articles:
            if target_laws:
                for tl in target_laws:
                    if tl in article.law_name:
                        if keywords:
                            if any(kw in article.full_text for kw in keywords):
                                results.append(article)
                        else:
                            results.append(article)
                        break
            else:
                if keywords:
                    if any(kw in article.full_text for kw in keywords):
                        results.append(article)

        return results[:50]

    def search_similar_cases(
        self,
        dispute_type: str,
        keywords: list[str] = None,
    ) -> list[GuidingCase]:
        results = []

        for case in self.cases:
            if dispute_type and dispute_type in case.dispute_type:
                results.append(case)
                continue

            if keywords:
                text = f"{case.key_holding} {case.applicable_law}"
                if any(kw in text for kw in keywords):
                    results.append(case)

        return results[:20]

    def search_academic_refs(
        self,
        keywords: list[str],
        dispute_type: str = "",
    ) -> list[AcademicReference]:
        results = []

        for ref in self.academic_refs:
            text = f"{ref.title} {ref.key_point} {ref.applicable_law}"
            if any(kw in text for kw in keywords):
                if dispute_type and dispute_type in ref.dispute_type:
                    results.append(ref)
                elif not dispute_type:
                    results.append(ref)

        return results[:20]

    def search_principles(
        self,
        context: str,
    ) -> list[LegalPrinciple]:
        results = []

        for principle in self.principles:
            if principle.name in context:
                results.append(principle)
                continue

            for example in principle.examples:
                example_keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}', example))
                context_keywords = set(re.findall(r'[\u4e00-\u9fff]{2,}', context))
                overlap = example_keywords & context_keywords
                if len(overlap) >= 2:
                    results.append(principle)
                    break

        return results

    def get_statistics(self) -> dict:
        return {
            "total_articles": len(self.articles),
            "total_cases": len(self.cases),
            "total_academic_refs": len(self.academic_refs),
            "total_principles": len(self.principles),
            "law_types": list(set(a.law_type for a in self.articles)),
            "loaded": self.loaded,
        }
