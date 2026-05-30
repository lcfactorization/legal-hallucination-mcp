"""网络验证模块 — 对接权威网站进行法条时效性、案例真实性在线验证。

桥接架构：提供验证提示词模板和结果解析器，由Agent使用自身LLM完成语义判断。
同时提供基于 urllib 的轻量级 HTTP 验证接口，用于从权威网站获取法条原文进行比对。

验证优先级：本地文件 → law_kb → 权威网站HTTP → WebSearch工具 → 标记幻觉
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from pydantic import BaseModel, Field

from .law_knowledge_base import LawVerificationResult

logger = logging.getLogger("legal-hallucination")


AUTHORITATIVE_SOURCES = {
    "法律法规": {
        "国家法律法规数据库": {
            "url": "https://flk.npc.gov.cn/",
            "description": "全国人大常委会办公厅主办，最权威的法律法规查询平台",
            "search_url": "https://flk.npc.gov.cn/api/detail",
        },
        "中国政府网法律法规": {
            "url": "https://www.gov.cn/zhengce/",
            "description": "国务院主办，行政法规和部门规章查询",
        },
        "最高人民法院司法解释": {
            "url": "https://www.court.gov.cn/shenpan/",
            "description": "最高人民法院官网，司法解释和指导案例查询",
        },
    },
    "案例": {
        "中国裁判文书网": {
            "url": "https://wenshu.court.gov.cn/",
            "description": "最高人民法院主办，裁判文书公开查询平台",
        },
        "最高人民法院指导案例": {
            "url": "https://www.court.gov.cn/shenpan/xiangqing.html",
            "description": "最高人民法院发布的指导性案例",
        },
        "人民法院案例库": {
            "url": "https://rmfyalk.cn/",
            "description": "最高人民法院案例库，参考案例和入库案例查询",
        },
    },
    "学术参考": {
        "中国知网": {
            "url": "https://www.cnki.net/",
            "description": "学术论文检索",
        },
        "北大法宝": {
            "url": "https://www.pkulaw.com/",
            "description": "法律信息检索，含法律法规、案例、期刊",
        },
    },
}

LAW_VERIFICATION_PROMPT_TEMPLATE = """你是一位法律条文验证专家。请验证以下法条引用是否正确、现行有效。

## 待验证法条引用
{citation_text}

## 验证要求
1. 该法律是否现行有效？是否已被废止或修订？
2. 该条款号是否存在于该法律中？
3. 条文内容是否与原文一致？
4. 如有变更，请说明变更情况。

## 验证来源建议
- 国家法律法规数据库：https://flk.npc.gov.cn/
- 最高人民法院：https://www.court.gov.cn/
- 北大法宝：https://www.pkulaw.com/

## 本地法条库信息
{local_info}

请按以下格式输出验证结果：
- 验证状态：已验证/已变更/已废止/无法验证
- 是否现行有效：是/否
- 差异描述：（如有）
- 置信度：0-1
- 验证来源：
"""

CASE_VERIFICATION_PROMPT_TEMPLATE = """你是一位案例验证专家。请验证以下案例信息是否真实。

## 待验证案例
案号：{case_number}
法院：{court}
裁判日期：{judgment_date}
裁判要旨：{key_holding}

## 验证要求
1. 该案号是否真实存在？
2. 审理法院是否与案号编码一致？
3. 裁判要旨是否与实际判决一致？
4. 如为杜撰案例，请指出矛盾之处。

## 验证来源建议
- 中国裁判文书网：https://wenshu.court.gov.cn/
- 人民法院案例库：https://rmfyalk.cn/

请按以下格式输出验证结果：
- 验证状态：已验证/疑似杜撰/无法验证
- 是否真实案例：是/否/无法确定
- 差异描述：（如有）
- 置信度：0-1
- 验证来源：
"""

FACT_VERIFICATION_PROMPT_TEMPLATE = """你是一位法律事实验证专家。请验证以下法律事实陈述是否有证据支撑。

## 待验证事实陈述
{fact_statement}

## 可用证据材料
{evidence_materials}

## 验证要求
1. 该事实陈述是否有直接证据支撑？
2. 如无直接证据，是否有间接证据链支撑？
3. 是否存在常识性推导或主观脑补的成分？
4. 按照封闭宇宙规则，无证据支撑的事实应如何标注？

## 封闭宇宙规则
大模型生成的每一句法律事实，必须完全源自证据索引清单中所列出并存在于工作区中的真实文件。
绝对禁止任何常识性推导、主观脑补或艺术加工。
若某一事实缺乏直接证据或存在争议，必须如实表述为：
"上诉人主张...，但截至本操作时未见相关书证支持。"

请按以下格式输出验证结果：
- 验证状态：有证据支撑/缺乏证据/部分支撑
- 支撑证据：（如有）
- 缺失证据：（如有）
- 建议修正：（如有）
"""


class WebVerificationResult(BaseModel):
    target: str = Field(default="", description="验证目标")
    target_type: str = Field(default="", description="验证类型: 法条/案例/事实")
    verification_status: str = Field(default="未验证", description="验证状态")
    is_verified: bool = Field(default=False, description="是否验证通过")
    is_current: bool = Field(default=True, description="是否现行有效")
    discrepancy: str = Field(default="", description="差异描述")
    verification_source: str = Field(default="", description="验证来源")
    verification_time: str = Field(default="", description="验证时间")
    confidence: float = Field(default=0.0, description="置信度 0-1")
    suggestion: str = Field(default="", description="修正建议")
    raw_result: str = Field(default="", description="原始验证结果")


class WebVerifier:
    def __init__(self):
        self.verification_cache: dict[str, WebVerificationResult] = {}
        self._failure_counts: dict[str, int] = {}
        self._circuit_open_until: dict[str, datetime] = {}
        self._max_failures = 3
        self._circuit_reset_seconds = 300

    def get_law_verification_prompt(
        self,
        citation_text: str,
        local_info: str = "",
    ) -> str:
        return LAW_VERIFICATION_PROMPT_TEMPLATE.format(
            citation_text=citation_text,
            local_info=local_info or "未加载本地法条库",
        )

    def get_case_verification_prompt(
        self,
        case_number: str,
        court: str = "",
        judgment_date: str = "",
        key_holding: str = "",
    ) -> str:
        return CASE_VERIFICATION_PROMPT_TEMPLATE.format(
            case_number=case_number,
            court=court,
            judgment_date=judgment_date,
            key_holding=key_holding,
        )

    def get_fact_verification_prompt(
        self,
        fact_statement: str,
        evidence_materials: str = "",
    ) -> str:
        return FACT_VERIFICATION_PROMPT_TEMPLATE.format(
            fact_statement=fact_statement,
            evidence_materials=evidence_materials or "未提供证据材料",
        )

    def parse_verification_response(
        self,
        target: str,
        target_type: str,
        response: str,
    ) -> WebVerificationResult:
        result = WebVerificationResult(
            target=target,
            target_type=target_type,
            verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            raw_result=response[:1000],
        )

        status_match = re.search(r'验证状态[：:]\s*(.+)', response)
        if status_match:
            result.verification_status = status_match.group(1).strip()

        current_match = re.search(r'是否现行有效[：:]\s*(是|否)', response)
        if current_match:
            result.is_current = current_match.group(1) == "是"

        real_match = re.search(r'是否真实案例[：:]\s*(是|否|无法确定)', response)
        if real_match:
            result.is_verified = real_match.group(1) == "是"

        verified_match = re.search(r'验证状态[：:]\s*已验证', response)
        if verified_match:
            result.is_verified = True

        discrepancy_match = re.search(r'差异描述[：:]\s*(.+)', response)
        if discrepancy_match:
            result.discrepancy = discrepancy_match.group(1).strip()

        confidence_match = re.search(r'置信度[：:]\s*([\d.]+)', response)
        if confidence_match:
            try:
                result.confidence = float(confidence_match.group(1))
            except ValueError:
                result.confidence = 0.0

        source_match = re.search(r'验证来源[：:]\s*(.+)', response)
        if source_match:
            result.verification_source = source_match.group(1).strip()

        suggestion_match = re.search(r'建议修正[：:]\s*(.+)', response)
        if suggestion_match:
            result.suggestion = suggestion_match.group(1).strip()

        cache_key = f"{target_type}:{target}"
        self.verification_cache[cache_key] = result

        return result

    def get_authoritative_sources(self, source_type: str = "") -> dict:
        if source_type and source_type in AUTHORITATIVE_SOURCES:
            return {source_type: AUTHORITATIVE_SOURCES[source_type]}
        return AUTHORITATIVE_SOURCES

    def get_cached_result(self, target: str, target_type: str = "") -> WebVerificationResult | None:
        cache_key = f"{target_type}:{target}" if target_type else target
        return self.verification_cache.get(cache_key)

    def batch_verify_citations(
        self,
        citations: list[str],
        local_results: list[LawVerificationResult] = None,
    ) -> list[WebVerificationResult]:
        results = []

        for citation in citations:
            cached = self.get_cached_result(citation, "法条")
            if cached:
                results.append(cached)
                continue

            local_info = ""
            if local_results:
                for lr in local_results:
                    if lr.citation_text == citation:
                        local_info = (
                            f"本地库查找：{'已找到' if lr.local_found else '未找到'}；"
                            f"是否现行有效：{'是' if lr.is_current else '否'}；"
                            f"差异：{lr.discrepancy or '无'}"
                        )
                        break

            result = WebVerificationResult(
                target=citation,
                target_type="法条",
                verification_status="待在线验证",
                verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                suggestion=f"请使用以下提示词进行在线验证：\n"
                           f"{self.get_law_verification_prompt(citation, local_info)[:200]}...",
            )
            results.append(result)

        return results

    def _is_circuit_open(self, source_type: str) -> bool:
        """检查熔断器是否开启（该类型源暂时不可用）。"""
        if source_type not in self._circuit_open_until:
            return False
        if datetime.now() < self._circuit_open_until[source_type]:
            return True
        del self._circuit_open_until[source_type]
        self._failure_counts.pop(source_type, None)
        return False

    def _record_failure(self, source_type: str):
        """记录验证失败，达到阈值时开启熔断器。"""
        count = self._failure_counts.get(source_type, 0) + 1
        self._failure_counts[source_type] = count
        if count >= self._max_failures:
            from datetime import timedelta
            self._circuit_open_until[source_type] = datetime.now() + timedelta(
                seconds=self._circuit_reset_seconds
            )
            logger.warning(
                "在线验证熔断器开启: source_type=%s, 将在%d秒后重试",
                source_type, self._circuit_reset_seconds,
            )

    def _record_success(self, source_type: str):
        """记录验证成功，重置失败计数。"""
        self._failure_counts.pop(source_type, None)

    def verify_with_fallback(
        self,
        target: str,
        target_type: str,
        local_result=None,
    ) -> WebVerificationResult:
        """带降级策略的在线验证：优先在线 → 熔断时降级为本地 → 最终降级为待验证。"""
        cached = self.get_cached_result(target, target_type)
        if cached:
            return cached

        if self._is_circuit_open(target_type):
            logger.info("verify_with_fallback: 熔断器开启，降级为本地验证: %s", target)
            status = "本地验证"
            is_current = False
            discrepancy = ""
            if local_result:
                is_current = getattr(local_result, 'is_current', False)
                discrepancy = getattr(local_result, 'discrepancy', '')
                status = "本地验证（在线源不可用）"

            result = WebVerificationResult(
                target=target,
                target_type=target_type,
                verification_status=status,
                is_current=is_current,
                discrepancy=discrepancy,
                verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                suggestion="在线验证源暂时不可用，已降级为本地验证。建议稍后重试在线验证。",
            )
            cache_key = f"{target_type}:{target}"
            self.verification_cache[cache_key] = result
            return result

        prompt_method = (
            self.get_law_verification_prompt if target_type == "法条"
            else self.get_case_verification_prompt if target_type == "案例"
            else self.get_fact_verification_prompt
        )
        local_info = ""
        if local_result:
            local_info = f"本地库查找：{'已找到' if getattr(local_result, 'local_found', False) else '未找到'}"

        result = WebVerificationResult(
            target=target,
            target_type=target_type,
            verification_status="待在线验证",
            verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            suggestion=f"请使用以下提示词进行在线验证：\n{prompt_method(target, local_info)[:200]}...",
        )
        cache_key = f"{target_type}:{target}"
        self.verification_cache[cache_key] = result
        return result

    def batch_verify_cases(
        self,
        case_numbers: list[str],
    ) -> list[WebVerificationResult]:
        results = []

        for case_num in case_numbers:
            cached = self.get_cached_result(case_num, "案例")
            if cached:
                results.append(cached)
                continue

            result = WebVerificationResult(
                target=case_num,
                target_type="案例",
                verification_status="待在线验证",
                verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                suggestion=f"请使用以下提示词进行在线验证：\n"
                           f"{self.get_case_verification_prompt(case_num)[:200]}...",
            )
            results.append(result)

        return results

    def _http_get(
        self,
        url: str,
        timeout: int = 15,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, str]:
        """轻量级 HTTP GET 请求，返回 (status_code, response_body)。

        使用标准库 urllib，避免外部依赖。
        """
        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/json,application/xhtml+xml,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if headers:
            default_headers.update(headers)

        req = urllib.request.Request(url, headers=default_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return resp.status, body
        except urllib.error.HTTPError as e:
            logger.warning("_http_get HTTP %s for %s: %s", e.code, url, e.reason)
            return e.code, ""
        except urllib.error.URLError as e:
            logger.warning("_http_get URLError for %s: %s", url, e.reason)
            return 0, ""
        except Exception as e:
            logger.warning("_http_get exception for %s: %s", url, e)
            return 0, ""

    def verify_case_number_http(
        self,
        case_number: str,
    ) -> WebVerificationResult | None:
        """通过权威网站验证案号真实性。

        优先尝试：
        1. 中国裁判文书网 (wenshu.court.gov.cn) - 搜索案号
        2. 人民法院案例库 (rmfyalk.cn) - API 查询
        """
        if self._is_circuit_open("案例"):
            logger.info("verify_case_number_http: circuit open, skipping HTTP")
            return None

        encoded = urllib.parse.quote(case_number)

        targets = [
            (
                "中国裁判文书网",
                f"https://wenshu.court.gov.cn/website/wenshu/181217BMTKHNT2W0/index.html?searchType=0&searchValue={encoded}",
            ),
            (
                "人民法院案例库",
                f"https://rmfyalk.cn/search/case?keyword={encoded}",
            ),
        ]

        for source_name, url in targets:
            logger.info("verify_case_number_http: querying %s for '%s'", source_name, case_number)
            status, body = self._http_get(url, timeout=12)
            if status == 200 and body:
                has_match = case_number in body or encoded in body
                if has_match:
                    logger.info(
                        "verify_case_number_http: '%s' FOUND on %s (status=%d, body_len=%d)",
                        case_number, source_name, status, len(body),
                    )
                    self._record_success("案例")
                    result = WebVerificationResult(
                        target=case_number,
                        target_type="案例",
                        verification_status="已验证",
                        is_verified=True,
                        verification_source=source_name,
                        verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        confidence=0.8,
                    )
                    cache_key = f"案例:{case_number}"
                    self.verification_cache[cache_key] = result
                    return result
                else:
                    logger.info(
                        "verify_case_number_http: '%s' NOT found on %s (status=%d)",
                        case_number, source_name, status,
                    )

        self._record_failure("案例")
        logger.info("verify_case_number_http: '%s' not found on any authoritative source", case_number)
        return None

    def verify_law_article_http(
        self,
        law_name: str,
        article_num: str,
    ) -> WebVerificationResult | None:
        """通过权威网站验证法条引用真实性。

        优先尝试：
        1. 国家法律法规数据库 (flk.npc.gov.cn)
        2. 中国政府网 (gov.cn) 法律法规
        3. 最高人民法院 (court.gov.cn) 司法解释
        """
        if self._is_circuit_open("法律法规"):
            logger.info("verify_law_article_http: circuit open, skipping HTTP")
            return None

        target = f"《{law_name}》第{article_num}条"
        encoded = urllib.parse.quote(target)

        targets = [
            (
                "国家法律法规数据库",
                f"https://flk.npc.gov.cn/api/detail?searchValue={encoded}",
            ),
            (
                "中国政府网",
                f"https://sousuo.www.gov.cn/sousuo/search.shtml?code=17da70961a7&dataTypeId=107&sign=4b4b4b4b-4b4b-4b4b-4b4b-4b4b4b4b4b4b&searchWord={encoded}",
            ),
            (
                "最高人民法院",
                f"https://www.court.gov.cn/search.html?keyword={encoded}",
            ),
        ]

        for source_name, url in targets:
            logger.info("verify_law_article_http: querying %s for '%s'", source_name, target)
            status, body = self._http_get(url, timeout=12)
            if status == 200 and body and len(body) > 100:
                has_match = (law_name in body) or (target in body) or (article_num in body)
                if has_match:
                    logger.info(
                        "verify_law_article_http: '%s' FOUND on %s (status=%d, body_len=%d)",
                        target, source_name, status, len(body),
                    )
                    self._record_success("法律法规")
                    result = WebVerificationResult(
                        target=target,
                        target_type="法条",
                        verification_status="已验证",
                        is_verified=True,
                        is_current=True,
                        verification_source=source_name,
                        verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        confidence=0.75,
                    )
                    cache_key = f"法条:{target}"
                    self.verification_cache[cache_key] = result
                    return result
                else:
                    logger.info(
                        "verify_law_article_http: '%s' NOT found on %s (status=%d)",
                        target, source_name, status,
                    )

        self._record_failure("法律法规")
        logger.info("verify_law_article_http: '%s' not found on any authoritative source", target)
        return None

    def verify_online(
        self,
        target: str,
        target_type: str,
        local_verified: bool = False,
    ) -> WebVerificationResult:
        """统一在线验证入口：本地未验证 → 尝试权威网站 → 返回结果。

        参数:
            target: 验证目标（案号/法条/日期）
            target_type: "案例" / "法条" / "司法文书" / "事实"
            local_verified: 本地是否已找到（True则不重复在线验证）

        返回:
            WebVerificationResult，is_verified 表示在线是否找到
        """
        cache_key = f"{target_type}:{target}"
        cached = self.verification_cache.get(cache_key)
        if cached:
            logger.info("verify_online: cache hit for '%s'", target)
            return cached

        if local_verified:
            logger.info("verify_online: '%s' already verified locally, skipping", target)
            result = WebVerificationResult(
                target=target,
                target_type=target_type,
                verification_status="本地已验证",
                is_verified=True,
                verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                verification_source="本地文件",
                confidence=0.95,
            )
            self.verification_cache[cache_key] = result
            return result

        if self._is_circuit_open(target_type):
            logger.info("verify_online: circuit open for type='%s'", target_type)
            return WebVerificationResult(
                target=target,
                target_type=target_type,
                verification_status="无法验证（在线源不可用）",
                is_verified=False,
                verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                suggestion="在线验证源暂时不可用",
            )

        if target_type == "案例":
            http_result = self.verify_case_number_http(target)
            if http_result:
                return http_result

        elif target_type == "法条":
            law_match = re.search(r'《([^》]+)》\s*第\s*([一二三四五六七八九十百千零\d]+)\s*条', target)
            if law_match:
                http_result = self.verify_law_article_http(
                    law_match.group(1), law_match.group(2),
                )
                if http_result:
                    return http_result

        result = WebVerificationResult(
            target=target,
            target_type=target_type,
            verification_status="在线未找到",
            is_verified=False,
            verification_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            verification_source="权威网站查询",
            suggestion="建议人工核查或通过 WebSearch 工具二次确认",
            confidence=0.3,
        )
        self.verification_cache[cache_key] = result
        return result

    def get_verification_summary(self) -> dict[str, int]:
        """获取验证缓存统计摘要。"""
        total = len(self.verification_cache)
        verified = sum(1 for v in self.verification_cache.values() if v.is_verified)
        pending = total - verified
        return {
            "total_cached": total,
            "verified": verified,
            "unverified": pending,
        }
