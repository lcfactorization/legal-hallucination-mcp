# 修改日志

## 2026-10-26 V44 更新（v0.4.0）

### 新增 H-7 / H-8 检测维度（P1）

八维分类体系在原六维（H-1至H-6）基础上扩展两个"结构一致性"维度：

| 维度 | 名称 | 权重 | 检测内容 |
|:---|:---|:---:|:---|
| H-7 | 裁判结果—援引依据一致性 | 0.075 | 逐判项检查说理支撑：判项关键词（金额/义务动词/确认对象）在说理段无呼应、说理段亦无相关《法规名》第X条引用 → critical |
| H-8 | 诉请—判决回应一致性 | 0.075 | 逐项诉请与主文处置对齐：诉请"未回应"（含无理由不予理涉）→ high；"变更/增加诉请"表述未获主文与说理处理 → critical |

**实现要点**：
1. `config.py`：`HALLUCINATION_DIMENSIONS`/`DIMENSION_TITLES`/`DIMENSION_ORDER`/`DIMENSION_WEIGHTS` 扩至八维
2. `rule_engine.py`：新增 `check_judgment_basis_consistency()` 与 `check_claim_response_consistency()` 检查方法（启发式文本对齐：金额呼应、义务对象名词呼应、字符 Jaccard 句级对齐、法条引用+义务动词四路判据），接入 `run_full_scan()` 与 `_group_flags_by_dimension()`，输出进入 `_calculate_score()` 罚分路径
3. `section_extractor.py`（新模块）：原 `server._extract_sections` 的共享实现，供 MCP 工具层与规则引擎复用，避免循环导入；H-7/H-8 输入优先取其产出的 reasoning/judgment_main 段，空缺时回退四段式标题切分
4. `server.py`：`run_rule_engine_full` 输出新增 `h7_h8_flags` 字段，供调用方将两维标志喂入 `calculate_hallucination_score` 的语义罚分路径（severity 系数沿用 critical 5 / high 3 / medium 1.5 / low 0.5 口径）
5. `report_builder.py`：报告新增"十三、H-7"与"十四、H-8"章节，后续章节序号顺延；`skill_runner.py` 幻觉分类体系同步补全
6. `RULE_ENGINE_PATTERNS` 为扁平规则组（按 h_code 归维，非按维度键控），无需新增两维规则组

**权重再平衡**（和=1.00）：

| 维度 | V43前 | V44 |
|:---|:---:|:---:|
| H-1 无源编造事实 | 0.25 | 0.22 |
| H-2 法律适用错误 | 0.20 | 0.18 |
| H-3 证据链断裂 | 0.20 | 0.18 |
| H-4 主观臆断/修辞过度 | 0.10 | 0.08 |
| H-5 诉求边界突破 | 0.20 | 0.15 |
| H-6 非文本证据穿透失败 | 0.05 | 0.04 |
| H-7 裁判结果—援引依据一致性 | — | 0.075 |
| H-8 诉请—判决回应一致性 | — | 0.075 |

`rule_engine._calculate_score` 的维度权重映射按同比例（×5）同步更新。

### 法规库 .docx 加载支持

`law_knowledge_base.load_from_directory()` 支持 `.docx` 法规文件：
- 惰性导入 `python-docx`，按段落拼接提取纯文本后走既有 `_parse_law_articles`/`_parse_cases` 解析
- python-docx 未安装时跳过 `.docx` 并汇总 warning 提示安装方式，**不加硬依赖**
- `pyproject.toml` 新增可选依赖组：`[project.optional-dependencies] docx = ["python-docx>=1.1"]`

### 日志卫生（P0）

全项目 `logging.info` 中含 `[LOCK]` 的锁诊断日志共 20 处全部降级为 `logging.debug`（仅动 [LOCK] 类，其余日志不动），消除 INFO 级别下的锁诊断噪音。

### 空目录清理

项目根目录 `skills/`（仅含空子目录）与 `anchors/`（空目录）删除。`SkillLoader` 对缺失目录本就回退内置提示词（`load_system_skill` 返回空串、`list_dimensions` 返回空列表），行为不变。

### 测试与版本

- 新增 `tests/test_h7_h8.py`：维度数 6→8 断言、权重和=1.00、H-7/H-8 合成样例出 flag、severity 罚分系数（经 server 工具端到端）、报告章节、.docx 加载（含 monkeypatch 模拟 ImportError 的优雅跳过）
- 既有测试样例均为合成数据，新增内容不含真实人名、真实公司名与完整真实案号
- `pyproject.toml` version → 0.4.0

---

## 2026-05-29 V42.3 更新

### 类案杜撰误报修复（P0级）

**问题**：`h1_fabricated_case_number` 规则将所有未标注"已验证/模拟"的案号一律标记为"类案案号杜撰"，导致严重误报：
- （2025）苏0602民初4514号 = 本案6271号的一审阶段，被误报为杜撰
- （2025）苏0602民初11402号 = 同当事人关联案件，被误报为杜撰
- （2020）苏03民终3088号 = 已在evidence_manifest.md的"类案类"分区中，仍被误报为杜撰

**根因**：规则引擎的 `requires_absent` 逻辑仅检查行内是否有"模拟/已验证"标注，完全不交叉引用evidence_manifest中的类案案号，也不识别关联案件。

**修复**：
1. EvidenceIndex新增 `get_manifest_case_numbers()` 方法：从manifest和已加载的证据文件中提取所有案号
2. EvidenceIndex新增 `get_related_case_numbers()` 方法：识别同法院/同年度/同编号的关联案件
3. EvidenceIndex新增 `_are_related_cases()` 静态方法：判断两个案号是否属于关联案件（同法院+同年度、同法院+同编号、同法院+不同审级）
4. RuleEngine新增 `_is_case_number_whitelisted()` 方法：归一化比较案号是否在白名单中
5. `_run_pattern_rules()` 在 `requires_absent` 分支中增加白名单过滤

**效果**：

| 版本 | 修复前Flag数 | 修复后Flag数 | 类案杜撰误报消除 |
|:---|:---|:---|:---|
| V40 | 37 | 19 | 18个误报消除 |
| V40P1 | 35 | 17 | 18个误报消除 |
| V41 | 25 | 20 | 5个误报消除（3个"模拟类案已标注"保留） |
| V42 | 34 | 16 | 18个误报消除 |

---

## 2026-05-29 V42.2 更新

### 多版本判决书幻觉比对扫描

对V40/V40P1/V41/V42四个版本的模拟二审判决书进行了并行幻觉检测和比对分析。

| 版本 | 总Flag数 | 严重 | 高 | 中 | 低 | Token估算 |
|:---|:---|:---|:---|:---|:---|:---|
| V40 | 37 | 0 | 31 | 5 | 1 | 19201 |
| V40P1 | 35 | 0 | 30 | 4 | 1 | 19702 |
| V41 | 25 | 0 | 16 | 5 | 4 | 16336 |
| V42 | 34 | 0 | 28 | 5 | 1 | 19488 |

核心发现：
- V41是幻觉最少的版本（25个Flag），主要因为标注了"模拟类案参考"使类案杜撰降级
- 类案案号杜撰是最大问题源（V40/V40P1/V42各18个，V41仅5个）
- 程序日期杜撰在所有版本中一致（8-10个）
- V42在项目越权方面改善（0个，其他版本2-3个）
- 引注欺诈在V41存在但V42已修复

### 新增模块

| 模块 | 文件 | 说明 |
|:---|:---|:---|
| 证据清单更新器 | `evidence_manifest_updater.py` | 证据索引清单的版本化更新、去重、一致性检查 |
| 证据清单CLI | `update_evidence_manifest.py` | 交互式管理evidence_manifest.md的命令行工具 |
| 多版本扫描器 | `multi_version_scan.py` | 并行扫描多个版本判决书，生成比对报告 |

### 规则引擎Bug修复

| 问题 | 修复 |
|:---|:---|
| 类案杜撰检测完全失效 | `h1_fabricated_case_number`规则的`exceptions`过于宽松，移除并改用`requires_absent` |
| 同行多案号只检测一个 | `_run_pattern_rules`的`requires_absent`/`exceptions`分支改用`re.finditer`逐匹配输出 |
| 模拟类案未分级 | 新增`h1_simulated_case_annotated`规则，已标注模拟的类案降级为low |

### 证据索引清单更新

| 变更 | 说明 |
|:---|:---|
| 从18条扩展到51条 | 新增证据材料清单5条、类案14条、法律1条、法理书面意见11条、证据30 PDF 2条 |
| 新增分区 | 证据材料清单类、类案类、法律类、法理与书面意见类 |
| 证据30补充 | 添加证据30-1(股东会议决议对账单等.pdf)和证据30-2(对账单部分.pdf) |

### 幻觉修改建议指南

为10种幻觉子类型提供详细可执行的人工修改建议：
- 类案案号杜撰、引注欺诈、程序日期杜撰、引用格式问题
- 项目越权、参照系替换、非文本证据未标注
- 三段论缺失（法律依据/证据引用）、模拟类案已标注

每条建议包含：诊断、人工修改步骤（含具体命令）、LLM提示词修正方向

### Workflow进度显示

多版本扫描脚本支持实时进度显示：
- 进度百分比（基于12阶段检测流程）
- 耗时统计（秒）
- Token估算
- 并行执行（ThreadPoolExecutor，最多4个worker）

### 测试覆盖

| 测试类 | 用例数 | 说明 |
|:---|:---|:---|
| TestEvidenceChainBreak | 3 | 证据链断裂：法律依据缺失、证据缺失、事实无绑定 |
| TestCaseFabrication | 2 | 类案杜撰：单案检测、多案检测 |
| TestEvidenceManifestUpdater | 4 | 清单更新：一致性检查、去重、新版本生成、报告提取 |
| 总计 | 31 | 全部通过 |

---

## 2026-05-29 V42.1 更新

### V41判决书幻觉检测

对V41_模拟二审判决书_苏06民终6271号劳动争议_20260527.md进行了完整的六维幻觉检测，生成检测报告。

| 检测项 | 结果 |
|:---|:---|
| 幻觉评分 | 71.35 |
| 风险等级 | F（极高风险） |
| 总标记数 | 25 |
| H-1 无源编造事实 | 9项（类案杜撰7+引注欺诈1+交叉验证未找到1） |
| H-2 法律适用错误 | 12项（程序日期8+引用格式4） |
| H-3 证据链断裂 | 0项 |
| H-4 主观修辞过度 | 0项 |
| H-5 诉求边界突破 | 3项（项目越权2+方法论替换1） |
| H-6 非文本证据穿透失败 | 1项 |

核心发现：
- 证据30名称不匹配（"审计报告"vs"股东会议决议及进账单对账单"）
- 诉请边界突破：奖金违约损害赔偿金189,750元和加付赔偿金2,488,798.49元无对应诉请
- 4个类案案号无法验证（已标注"模拟类案参考"）
- 同工同酬方法论替换导致判决金额远超诉请预期
- 苏高法审委〔2011〕14号已废止但仍被引用

### 文档更新

| 文件 | 变更 |
|:---|:---|
| 手册v3.0 | 新增9.4工作流编排模式章节，复制到MCP docs目录 |
| 检测报告 | 生成TraeCN_GLM-5.1_法律文书幻觉检测报告_v1.0_20260529.md |
| run_detection_v41.py | 新增V41判决书检测脚本 |

### Bug修复

| 问题 | 修复 |
|:---|:---|
| 检测脚本使用错误的方法名 | `load_manifest`→`load`，`run_all`→`run_full_scan` |
| 检测脚本使用错误的属性名 | `dim.name`→`dim.dimension`，`dim.title`→`dim.dimension_title`等 |

---

## 2026-05-29 V42 更新

### 新增模块

| 模块 | 文件 | 说明 |
|:---|:---|:---|
| 法律知识库 | `law_knowledge_base.py` | 法条、案例、学术参考的结构化索引与检索 |
| 交叉验证引擎 | `cross_reference_engine.py` | 事实陈述与证据材料、法条原文的多源比对 |
| 网络验证模块 | `web_verifier.py` | 对接权威网站进行法条时效性和案例真实性在线验证 |
| 向量索引模块 | `vector_index.py` | 法律文本矢量化和向量索引，支持降级为关键词索引 |
| 工作流编排模块 | `workflow_orchestrator.py` | 多子代理并行检测协调器，借鉴claude-code的coordinator模式 |

### 规则引擎增强

| 功能 | 说明 |
|:---|:---|
| 程序时效检测 | 新增 `check_time_bar()` 方法，检测劳动争议1年仲裁时效 |
| 上诉期检测 | 新增上诉期15日计算和验证 |
| 法条张冠李戴检测 | 检测法条引用与案件事实场景是否匹配 |
| 计算核验 | 对加班工资、二倍工资差额等关键计算项进行公式还原和数值校验 |
| 利息基数提取 | 通过正则表达式从"以XX元为基数"等表述中提取本金金额 |
| 中文大写金额解析 | 使用cn2an库将中文大写金额转换为阿拉伯数字 |

### 诉请解析器优化

| 功能 | 说明 |
|:---|:---|
| 模糊匹配改进 | 使用2-4字滑动窗口提取关键词，提高模糊匹配准确性 |
| 项目名清理 | 增加对"判令""被告""支付"等前缀的清理 |
| 去重优化 | 改进 `_deduplicate_limits()` 方法，避免相似诉请项重复计算 |

### 报告生成器增强

| 功能 | 说明 |
|:---|:---|
| 报告文件名规范 | `{Agent名}_{LLM名版本号}_{内容概要}_{版本号}_{YYYYMMDD}.md` |
| GitHub Alerts格式 | 使用 `> [!NOTE]`、`> [!WARNING]`、`> [!TIP]`、`> [!IMPORTANT]`、`> [!CAUTION]` |
| 交叉验证章节 | 新增第10章"交叉验证与原始文件核对" |
| 法律知识库验证章节 | 新增第11章"法律知识库验证" |

### 对抗性测试扩展

| 测试类 | 说明 |
|:---|:---|
| TestAppealPeriodDetection | 上诉期超期检测 |
| TestFabricatedCaseDetection | 类案杜撰检测 |
| TestInterestBaseDetection | 利息基数错误检测 |
| TestReportFilenameConvention | 报告文件名规范验证 |
| TestGitHubAlertsFormat | GitHub Alerts格式验证 |

### 工作流编排模块

| 功能 | 说明 |
|:---|:---|
| 多子代理并行检测 | 将六维检测拆分为8个独立子任务，支持并行执行 |
| 令牌预算约束 | 按LLM最大上下文令牌数约束每个子任务的输入，避免超长上下文 |
| 任务状态追踪 | 记录每个子任务的状态（pending/running/completed/failed） |
| 结果聚合 | 汇总所有子任务结果，计算风险等级，生成综合评估 |
| 工作流生命周期 | 创建→获取并行任务→执行→更新状态→汇总结果 |

新增MCP工具：
- `create_detection_workflow`：创建多子代理并行检测工作流
- `get_workflow_tasks`：获取可并行执行的子任务
- `update_workflow_task`：更新子任务状态
- `get_workflow_status`：获取工作流状态
- `aggregate_workflow_results`：汇总工作流检测结果
- `list_workflows`：列出所有工作流

### 幻觉检测手册更新

| 文件 | 说明 |
|:---|:---|
| `法律引用与幻觉规避操作手册_20260527.md` | 原版保留，未修改 |
| `法律引用与幻觉规避操作手册_v3.0_20260529.md` | 新版，追加版本号和日期 |

手册V3.0新增内容：
- H-2新增子类型：法条张冠李戴、类案杜撰、程序时效杜撰、仲裁时效张冠李戴、法律法规变更未追踪
- H-5新增子类型：计算错误、逻辑循环、数字引用不一致、利息基数错误
- 第三章：法律知识库与交叉验证体系（全新）
- 步骤9：法律知识库验证（新增）
- 步骤6：原始文件交叉验证（新增）
- 第十章：报告文件名规范与格式要求（全新）
- 第十一章：一般民事和劳动争议案件通用规则（全新）
- 自检清单新增F类：法律知识库自检

### README更新

| 文件 | 更新内容 |
|:---|:---|
| `README.md` | 新增模块说明、新MCP工具、报告章节更新 |
| `README_EN.md` | 同步英文版更新 |

### Bug修复

| 问题 | 修复 |
|:---|:---|
| TestVersionReferenceDetection失败 | 使用 `f.message` 和 `f.sub_type` 替代 `f.description` |
| TestClaimBoundaryDetection失败 | 增加对"原告""被告"等前缀的清理 |
| TestTimeBarDetection失败 | 优化日期提取正则，支持"日期+关键词"和"关键词+日期"两种模式 |
| TestClaimParser.test_fuzzy_match失败 | 改进模糊匹配方法，使用2-4字滑动窗口 |
| TestReportFilenameConvention失败 | 将 `topic` 参数改为 `content_summary` |
