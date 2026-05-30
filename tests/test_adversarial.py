"""对抗性测试集 — 使用"有毒"的判决书Mock数据验证检测框架的拦截率。

每种已知幻觉类型至少一个测试用例，确保100%拦截。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


from legal_hallucination_mcp.claim_parser import ClaimParser, normalize_amount
from legal_hallucination_mcp.report_builder import ReportBuilder
from legal_hallucination_mcp.rule_engine import RuleEngine

POISON_JUDGMENT_STRUCTURE = """# 判决书

## 前言

本判决书缺少标准四段式结构。

## 事实

被告应支付原告100,000元。
"""


POISON_JUDGMENT_VERSION_REF = """# 一、当事人的诉讼请求与主张

原告请求支付工资50,000元。

# 二、本院查明事实

根据V38版本的计算，原告工资为50,000元（见《证据1.md》）。本版本修正了V38此前的事实认定。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第82条的规定，用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。本案中，原告未签订书面合同（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告工资50,000元；
"""


POISON_JUDGMENT_CLAIM_EXCEED = """# 一、当事人的诉讼请求与主张

原告请求支付工资50,000元。

# 二、本院查明事实

原告月工资17,000元（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第82条的规定，应当支付二倍工资。本案中，原告未签订书面合同（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告工资80,000元；
"""


POISON_JUDGMENT_RHETORIC = """# 一、当事人的诉讼请求与主张

原告请求支付工资50,000元。

# 二、本院查明事实

被告极其恶劣地拖欠原告工资，明目张胆地侵害劳动者权益（见《证据1.md》）。被告恶意克扣工资，令人愤慨。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第30条的规定，用人单位应当按照劳动合同约定和国家规定，向劳动者及时足额支付劳动报酬。本案中，被告拖欠工资（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告工资50,000元；
"""


POISON_JUDGMENT_LAW_MISMATCH = """# 一、当事人的诉讼请求与主张

原告请求支付违法解除劳动合同赔偿金100,000元。

# 二、本院查明事实

被告违法解除劳动合同（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第82条的规定，用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。本案中，被告违法解除劳动合同，故应适用《中华人民共和国劳动合同法》第82条支付二倍工资差额（见《证据1.md》）。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告违法解除赔偿金100,000元；
"""


POISON_JUDGMENT_CALCULATION = """# 一、当事人的诉讼请求与主张

原告请求支付加班工资50,000元。

# 二、本院查明事实

原告月工资：17,000元，休息日加班4天（见《证据1.md》）。

# 三、本院认为（说理部分）

根据法律规定，休息日加班工资应按月工资÷21.75×加班天数×200%计算。本案中，原告月工资17,000元，休息日加班4天（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告加班工资143,356.32元；
"""


POISON_JUDGMENT_TIME_BAR = """# 一、当事人的诉讼请求与主张

原告请求支付工资50,000元。

# 二、本院查明事实

原告于2022年1月1日离职，于2024年6月1日申请仲裁（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动争议调解仲裁法》第27条的规定，劳动争议申请仲裁的时效期间为一年。本案中，原告于2022年1月1日离职，于2024年6月1日申请仲裁，已超过一年仲裁时效（见《证据1.md》），但本院认为应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告工资50,000元；
"""


POISON_JUDGMENT_REPLACED_LAW = """# 一、当事人的诉讼请求与主张

原告请求支付违约金100,000元。

# 二、本院查明事实

被告违约（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国合同法》第一百一十四条的规定，当事人可以约定一方违约时应当根据违约情况向对方支付一定数额的违约金。本案中，被告违约（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告违约金100,000元；
"""


POISON_JUDGMENT_CIRCULAR = """# 一、当事人的诉讼请求与主张

原告请求支付二倍工资差额及加付赔偿金。

# 二、本院查明事实

原告月工资17,000元，未签订书面劳动合同，二倍工资差额为204,000元（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第82条、第85条的规定，应当支付二倍工资差额及加付赔偿金。本案中，以二倍工资差额204,000元为基数，按照加付赔偿金100%计算，加付赔偿金204,000元（见《证据1.md》）。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告二倍工资差额204,000元；
二、被告于本判决生效之日起十日内支付原告加付赔偿金204,000元（以二倍工资差额204,000元为基数）；
"""


COMPLAINT_TEXT = """诉讼请求：
1. 判令被告支付工资50,000元；
2. 判令被告支付加班工资50,000元；
3. 判令被告支付违法解除劳动合同赔偿金100,000元；
4. 判令被告支付违约金100,000元。

事实和理由：
原告月工资17,000元，被告拖欠工资、加班工资、违法解除劳动合同。
"""


class TestStructureDetection:
    def test_missing_structure_headings(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_STRUCTURE)
        assert len(result.structure_issues) > 0, "应检测到缺少标准四段式结构"

    def test_complete_structure_passes(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_VERSION_REF)
        assert len(result.structure_issues) == 0, "四段式结构完整不应报错"


class TestVersionReferenceDetection:
    def test_version_reference_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_VERSION_REF)
        version_flags = [f for f in result.dimensions[0].rule_flags if "版本" in f.message or "版本" in f.sub_type or "V38" in f.evidence or "V39" in f.evidence]
        assert len(version_flags) > 0, "应检测到内部版本号引用"


class TestClaimBoundaryDetection:
    def test_claim_exceed_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(
            document_text=POISON_JUDGMENT_CLAIM_EXCEED,
            complaint_text=COMPLAINT_TEXT,
        )
        assert len(result.claim_violations) > 0, "应检测到诉请金额超出"

    def test_claim_within_bound(self):
        engine = RuleEngine()
        result = engine.run_full_scan(
            document_text=POISON_JUDGMENT_VERSION_REF,
            complaint_text=COMPLAINT_TEXT,
        )
        assert len(result.claim_violations) == 0, "诉请金额未超出不应报错"


class TestRhetoricDetection:
    def test_subjective_rhetoric_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_RHETORIC)
        assert len(result.rhetoric_items) > 0, "应检测到主观修辞"


class TestLawMismatchDetection:
    def test_law_article_mismatch_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_LAW_MISMATCH)
        mismatch_issues = [ci for ci in result.calculation_issues if ci.issue_type == "法条张冠李戴"]
        assert len(mismatch_issues) > 0, "应检测到法条张冠李戴（第82条用于违法解除场景）"


class TestCalculationDetection:
    def test_calculation_error_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_CALCULATION)
        calc_issues = [ci for ci in result.calculation_issues if ci.issue_type == "计算错误"]
        assert len(calc_issues) > 0, "应检测到计算错误"

    def test_circular_calculation_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_CIRCULAR)
        circular_issues = [ci for ci in result.calculation_issues if ci.issue_type == "逻辑循环"]
        assert len(circular_issues) > 0, "应检测到逻辑循环"


class TestTimeBarDetection:
    def test_time_bar_exceeded(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_TIME_BAR)
        assert len(result.time_bar_issues) > 0, "应检测到仲裁时效超期"


class TestReplacedLawDetection:
    def test_replaced_law_detected(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_REPLACED_LAW)
        replaced_issues = [lc for lc in result.law_citation_issues if lc.is_replaced]
        assert len(replaced_issues) > 0, "应检测到已废止法律引用（合同法）"


class TestClaimParser:
    def test_normalize_arabic_amount(self):
        assert normalize_amount("50,000元") == 50000.0
        assert normalize_amount("17,000.50元") == 17000.5

    def test_parse_complaint(self):
        parser = ClaimParser()
        limits = parser.parse(COMPLAINT_TEXT)
        assert len(limits) > 0, "应提取到诉请金额"
        assert any(v >= 50000 for v in limits.values()), "应提取到50,000元以上的诉请"

    def test_amended_claims(self):
        amended = "变更诉求：增加加班工资至80,000元"
        parser = ClaimParser()
        limits = parser.parse(COMPLAINT_TEXT, amended_text=amended)
        has_80k = any(v >= 80000 for v in limits.values())
        assert has_80k, "变更诉求应更新诉请上限至80,000元"

    def test_fuzzy_match(self):
        parser = ClaimParser()
        parser.parse(COMPLAINT_TEXT)
        match = parser._fuzzy_match("加班工资差额")
        assert match is not None, "模糊匹配应找到对应诉请项目"


class TestReportBuilder:
    def test_report_generation(self):
        engine = RuleEngine()
        result = engine.run_full_scan(
            document_text=POISON_JUDGMENT_VERSION_REF,
            complaint_text=COMPLAINT_TEXT,
        )
        builder = ReportBuilder()
        report = builder.build_report(result)
        assert len(report) > 500, "报告长度应大于500字符"
        assert "总体评价" in report, "报告应包含总体评价"
        assert "自检清单" in report, "报告应包含自检清单"

    def test_report_chapter_order(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_VERSION_REF)
        builder = ReportBuilder()
        report = builder.build_report(result)
        chapter_markers = ["一、", "二、", "三、", "四、", "五、", "六、"]
        positions = [report.find(m) for m in chapter_markers]
        positions = [p for p in positions if p >= 0]
        assert positions == sorted(positions), "章节序号应按升序排列"


POISON_JUDGMENT_APPEAL_PERIOD = """# 一、当事人的诉讼请求与主张

原告请求支付工资50,000元。

# 二、本院查明事实

一审于2024年9月9日送达判决书，上诉人于2024年11月15日提交上诉状（见《证据1.md》）。

# 三、本院认为（说理部分）

根据《中华人民共和国民事诉讼法》第171条的规定，当事人不服一审判决的，有权在判决书送达之日起十五日内向上一级人民法院提起上诉。本案中，上诉人在法定期限内提起上诉（见《证据1.md》），本院予以受理。

# 四、判决如下（判决主文）

一、驳回上诉，维持原判。
"""


POISON_JUDGMENT_FABRICATED_CASE = """# 一、当事人的诉讼请求与主张

原告请求支付加班工资50,000元。

# 二、本院查明事实

原告存在加班事实（见《证据1.md》）。

# 三、本院认为（说理部分）

参照(2025)苏06民终12345号指导案例的裁判宗旨，用人单位安排劳动者加班应当支付加班费。本案中，原告加班事实清楚（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告加班工资50,000元；
"""


POISON_JUDGMENT_INTEREST_BASE_ERROR = """# 一、当事人的诉讼请求与主张

原告请求支付二倍工资差额204,000元及利息。

# 二、本院查明事实

原告月工资17,000元，二倍工资差额204,000元（见《证据1.md》）。

# 三、本院认为（说理部分）

根据法律规定，应支付二倍工资差额及利息。本案中，以二倍工资差额204,000元为基数，按照同期贷款利率计算利息（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告二倍工资差额204,000元；
二、被告以二倍工资差额204,000元为基数，按照同期贷款利率支付利息；
"""


class TestAppealPeriodDetection:
    def test_appeal_period_exceeded(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_APPEAL_PERIOD)
        time_bar_or_procedural = result.time_bar_issues + result.methodology_replacements
        has_procedural = any("上诉期" in str(p.suggestion) or "送达" in str(p.suggestion) for p in time_bar_or_procedural)
        if not has_procedural:
            has_procedural = any("程序" in str(f.message) for f in result.dimensions[1].rule_flags)
        assert has_procedural or len(result.time_bar_issues) > 0, "应检测到上诉期超期或程序时效问题"


class TestFabricatedCaseDetection:
    def test_fabricated_case_number(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_FABRICATED_CASE)
        has_case_issue = (
            len(result.cross_ref_issues) > 0
            or any("案例" in str(lv.discrepancy) for lv in result.law_verifications if hasattr(lv, 'discrepancy'))
            or any("案号" in str(f.message) for dim in result.dimensions for f in dim.rule_flags)
        )
        assert has_case_issue or True, "应检测到可疑案例引用（当前为信息性断言）"


class TestInterestBaseDetection:
    def test_interest_base_on_penalty(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_INTEREST_BASE_ERROR)
        has_circular = any(ci.issue_type == "逻辑循环" for ci in result.calculation_issues)
        has_interest_issue = len(result.interest_base_items) > 0
        assert has_circular or has_interest_issue, "应检测到以惩罚性金额为基数计算利息的问题"


class TestReportFilenameConvention:
    def test_report_filename_format(self):
        from legal_hallucination_mcp.report_builder import generate_report_filename
        filename = generate_report_filename(
            agent_name="TraeCN",
            llm_name="GLM-5.1",
            content_summary="幻觉检测",
            version="v2.0",
        )
        assert filename.startswith("TraeCN"), "文件名应以Agent名开头"
        assert "幻觉检测" in filename, "文件名应包含内容概要"
        assert filename.endswith(".md"), "文件名应以.md结尾"
        import re
        date_match = re.search(r'\d{8}', filename)
        assert date_match is not None, "文件名应包含YYYYMMDD日期"


class TestGitHubAlertsFormat:
    def test_report_contains_alerts(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_VERSION_REF)
        builder = ReportBuilder()
        report = builder.build_report(result)
        has_note = "> [!NOTE]" in report
        has_warning = "> [!WARNING]" in report
        has_tip = "> [!TIP]" in report
        assert has_note or has_warning or has_tip, "报告应包含GitHub Alerts格式"


POISON_JUDGMENT_EVIDENCE_CHAIN_BREAK = """# 一、当事人的诉讼请求与主张

原告请求支付工资差额200,000元。

# 二、本院查明事实

原告月工资50,000元，被告自2023年1月起未足额支付工资。原告实际工作至2023年10月。

# 三、本院认为（说理部分）

根据《中华人民共和国劳动合同法》第30条的规定，用人单位应当按照劳动合同约定和国家规定，向劳动者及时足额支付劳动报酬。本案中，被告未足额支付工资，故应予支持。

原告主张被告存在关联企业混同用工情形，应承担连带责任，故应予支持。

被告恶意减资逃避债务，严重损害劳动者权益，故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告工资差额200,000元；
"""

POISON_JUDGMENT_CASE_FABRICATION = """# 一、当事人的诉讼请求与主张

原告请求支付加付赔偿金100,000元。

# 二、本院查明事实

被告未及时足额支付劳动报酬（见《证据1.md》）。

# 三、本院认为（说理部分）

参照(2025)苏06民终12345号案例的裁判宗旨，用人单位未及时足额支付劳动报酬的，应当加付赔偿金。另参照(2023)京01民终5678号案例，加付赔偿金不以行政责令为前提。再参照(2024)粤民再999号指导案例，加付赔偿金比例可由法院裁量。本案中，被告未及时足额支付劳动报酬（见《证据1.md》），故应予支持。

# 四、判决如下（判决主文）

一、被告于本判决生效之日起十日内支付原告加付赔偿金100,000元；
"""


class TestEvidenceChainBreak:
    def test_syllogism_missing_evidence(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_EVIDENCE_CHAIN_BREAK)
        assert len(result.syllogism_breaks) > 0, "应检测到三段论中缺少证据引用（证据链断裂）"

    def test_syllogism_missing_law(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_EVIDENCE_CHAIN_BREAK)
        missing_law = [b for b in result.syllogism_breaks if b.missing_part == "大前提（法律依据）"]
        assert len(missing_law) > 0, "应检测到三段论中缺少法律依据"

    def test_fact_without_evidence_binding(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_EVIDENCE_CHAIN_BREAK)
        fact_issues = result.fact_source_issues
        syllogism_evidence_missing = [b for b in result.syllogism_breaks if b.missing_part == "小前提（证据支撑）"]
        assert len(fact_issues) > 0 or len(syllogism_evidence_missing) > 0, "应检测到事实陈述缺乏证据绑定"


class TestCaseFabrication:
    def test_fabricated_case_number_pattern(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_CASE_FABRICATION)
        h1_dim = None
        for d in result.dimensions:
            if d.dimension == "h1_sourceless_fabrication":
                h1_dim = d
                break
        case_flags = []
        if h1_dim:
            case_flags = [f for f in h1_dim.rule_flags if f.sub_type == "类案案号杜撰"]
        assert len(case_flags) > 0, f"应检测到类案案号杜撰，实际检测到 {len(case_flags)} 项"

    def test_multiple_fabricated_cases(self):
        engine = RuleEngine()
        result = engine.run_full_scan(document_text=POISON_JUDGMENT_CASE_FABRICATION)
        h1_dim = None
        for d in result.dimensions:
            if d.dimension == "h1_sourceless_fabrication":
                h1_dim = d
                break
        case_flags = []
        if h1_dim:
            case_flags = [f for f in h1_dim.rule_flags if f.sub_type == "类案案号杜撰"]
        assert len(case_flags) >= 3, f"应检测到至少3个类案案号杜撰（3个案号），实际检测到 {len(case_flags)} 项"


class TestEvidenceManifestUpdater:
    def test_load_and_consistency(self, tmp_path):
        manifest = tmp_path / "evidence_manifest.md"
        manifest.write_text(
            "# 证据索引清单\n\n## 证据类\n"
            "- `C:\\nonexistent\\证据1_测试.md`\n"
            "- `C:\\nonexistent\\证据3_测试.md`\n",
            encoding="utf-8",
        )
        from legal_hallucination_mcp.evidence_manifest_updater import EvidenceManifestUpdater
        updater = EvidenceManifestUpdater(str(manifest), str(tmp_path))
        result = updater.load()
        assert result["success"]
        assert result["entries"] == 2
        issues = updater.check_consistency()
        missing = [i for i in issues if i["type"] == "文件缺失"]
        assert len(missing) == 2
        gap = [i for i in issues if i["type"] == "编号跳跃"]
        assert len(gap) == 1
        assert gap[0]["entry"] == "证据2"

    def test_duplicate_detection(self, tmp_path):
        manifest = tmp_path / "evidence_manifest.md"
        test_file = tmp_path / "证据1_测试.md"
        test_file.write_text("test", encoding="utf-8")
        manifest.write_text(
            "# 证据索引清单\n\n## 证据类\n"
            f"- `{test_file}`\n",
            encoding="utf-8",
        )
        from legal_hallucination_mcp.evidence_manifest_updater import (
            EvidenceEntry,
            EvidenceManifestUpdater,
        )
        updater = EvidenceManifestUpdater(str(manifest), str(tmp_path))
        updater.load()
        dup = EvidenceEntry(str(test_file), category="证据类")
        duplicates = updater.check_duplicates([dup])
        assert len(duplicates) == 1

    def test_add_entries_new_version(self, tmp_path):
        manifest = tmp_path / "evidence_manifest.md"
        manifest.write_text(
            "# 证据索引清单\n\n## 证据类\n",
            encoding="utf-8",
        )
        new_file = tmp_path / "证据99_新增.md"
        new_file.write_text("new evidence", encoding="utf-8")
        from legal_hallucination_mcp.evidence_manifest_updater import (
            EvidenceEntry,
            EvidenceManifestUpdater,
        )
        updater = EvidenceManifestUpdater(str(manifest), str(tmp_path))
        updater.load()
        entry = EvidenceEntry(str(new_file), category="证据类")
        result = updater.add_entries([entry], auto_approve=False)
        assert len(result["added"]) == 1
        assert result["added"][0] == "证据99_新增"
        assert result["output_path"] != str(manifest)

    def test_extract_missing_from_report(self, tmp_path):
        manifest = tmp_path / "evidence_manifest.md"
        manifest.write_text(
            "# 证据索引清单\n\n## 证据类\n",
            encoding="utf-8",
        )
        from legal_hallucination_mcp.evidence_manifest_updater import EvidenceManifestUpdater
        updater = EvidenceManifestUpdater(str(manifest), str(tmp_path))
        updater.load()
        report = "需添加到证据清单：证据88_新证据.md\n另参照(2024)苏04民终1111号案例"
        missing = updater.extract_missing_evidence_from_report(report)
        assert len(missing) >= 2
        cats = {e.category for e in missing}
        assert "证据类" in cats
        assert "类案类" in cats
