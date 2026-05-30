# legal-hallucination-mcp 开发计划 v2.0

> 更新日期：2026-05-29
> 基于代码审计报告，整理全部优化点并按优先级排列

## 一、已完成项（P0-P1，本次修复）

| 编号 | 优化点 | 优先级 | 状态 | 涉及文件 |
|------|--------|--------|------|----------|
| OPT-01 | H-2程序日期杜撰检测误报——增加证据索引时间线交叉验证 | P0 | ✅ 已完成 | evidence_index.py, rule_engine.py |
| OPT-02 | REPLACED_LAWS双副本数据不一致——统一到config.py，law_knowledge_base.py改为导入 | P0 | ✅ 已完成 | config.py, law_knowledge_base.py, law_citation_checker.py |
| OPT-03 | CrossReferenceEngine未暴露为MCP工具——新增run_cross_verification | P1 | ✅ 已完成 | server.py |
| OPT-16 | 缺少批量检测接口——新增batch_detect和compare_documents | P1 | ✅ 已完成 | server.py |

### OPT-01 修复详情
- **根因**：`_check_procedural_time_limits`仅从判决书提取日期，未与证据索引中的真实时间线交叉验证
- **修复**：
  1. `EvidenceIndex.get_procedural_timeline()`：从证据文件提取8类程序性日期（受理/立案/开庭/宣判/送达/上诉/仲裁裁决/起诉）
  2. `RuleEngine._is_procedural_date_in_evidence()`：H-2规则命中时，若日期存在于证据时间线则过滤误报
  3. `_check_procedural_time_limits()`：增加文书日期与证据日期不一致检测

### OPT-02 修复详情
- **根因**：config.py（9条简单键值对）与law_knowledge_base.py（5条嵌套字典）独立维护
- **修复**：
  1. config.py的REPLACED_LAWS升级为嵌套字典（含replaced_by/effective_date/repeal_date），保留9条完整数据
  2. 新增REPLACED_LAWS_SIMPLE向后兼容简单格式
  3. law_knowledge_base.py删除独立定义，从config.py导入并自动添加书名号

### OPT-03 修复详情
- 新增`run_cross_verification` MCP工具，返回完整CrossReferenceReport（含law_verifications的confidence/verification_status、case_verifications的court/judgment_date/key_holding、web_verifications）

### OPT-16 修复详情
- 新增`batch_detect` MCP工具：接受JSON数组输入，批量检测多份文书
- 新增`compare_documents` MCP工具：比对两份文书检测结果，生成差异对比报告

---

## 二、待办清单（按优先级排序）

### P1 级（高优先级，影响检测准确性）

| 编号 | 优化点 | 说明 | 涉及文件 |
|------|--------|------|----------|
| OPT-04 | 证据引注匹配算法优化 | 短引注（≤4字符）精确匹配率低，需引入匹配置信度评分和模糊匹配 | rule_engine.py |
| OPT-05 | 诉请项目匹配优化 | 判决项目与诉请项目缺少同义词映射，需引入编辑距离/Jaccard相似度 | rule_engine.py, claim_parser.py |
| OPT-06 | 变更诉求解析完善 | `_parse_amended_claims`方法未实现，导致变更诉求场景下诉审一致检测失效 | claim_parser.py |
| OPT-07 | 向量索引集成到主流程 | vector_index.py已实现但未集成到rule_engine和cross_reference_engine | rule_engine.py, cross_reference_engine.py |

### P2 级（中优先级，影响系统健壮性）

| 编号 | 优化点 | 说明 | 涉及文件 |
|------|--------|------|----------|
| OPT-08 | 日志级别规范化 | 部分关键路径使用print而非logger，需统一为logging | 全局 |
| OPT-09 | H-1法条杜撰检测增强 | 当前仅检查REPLACED_LAWS中的9部法律，需扩展到更多已废止法律 | config.py, law_knowledge_base.py |
| OPT-10 | H-3案例杜撰检测增强 | 案例号验证仅做格式检查，需增加案例号与法院管辖区的逻辑校验 | rule_engine.py |
| OPT-11 | H-4证据杜撰检测增强 | 证据引注与证据索引的匹配需支持模糊匹配和部分匹配 | rule_engine.py |
| OPT-12 | H-5逻辑谬误检测增强 | 当前仅检测二倍工资循环计算，需扩展到更多逻辑谬误模式 | rule_engine.py |
| OPT-13 | report_builder.py的LLM名验证 | `generate_report_filename`对LLM名的正则验证过严（如拒绝GLM-5.1中的点号） | report_builder.py |
| OPT-14 | 在线验证降级策略 | 当在线验证不可用时，需有明确的降级提示和置信度调整 | cross_reference_engine.py |
| OPT-15 | 证据索引缓存机制 | 重复加载同一manifest时应有缓存，避免重复IO | evidence_index.py |

### P3 级（低优先级，改善用户体验）

| 编号 | 优化点 | 说明 | 涉及文件 |
|------|--------|------|----------|
| OPT-17 | 检测结果缓存 | 对同一文档的重复检测应有结果缓存（基于文档hash） | rule_engine.py |
| OPT-18 | 检测进度回调 | 长文档检测时提供进度回调，避免超时 | server.py |
| OPT-19 | 自定义规则配置 | 允许用户通过配置文件自定义规则开关和阈值 | config.py, rule_engine.py |
| OPT-20 | 多语言支持 | 当前仅支持中文文书，需扩展到英文法律文书 | 全局 |
| OPT-21 | 检测结果导出格式 | 增加CSV/Excel导出选项 | report_builder.py |
| OPT-22 | API限流与并发控制 | batch_detect等批量接口需增加限流和并发控制 | server.py |
| OPT-23 | 单元测试覆盖率提升 | 当前测试覆盖率为~60%，目标提升至80%+ | tests/ |

---

## 三、里程碑规划

### M1：检测准确性提升（预计2周）
- OPT-04 证据引注匹配算法优化
- OPT-05 诉请项目匹配优化
- OPT-06 变更诉求解析完善
- OPT-07 向量索引集成

### M2：系统健壮性提升（预计2周）
- OPT-08 日志级别规范化
- OPT-09~OPT-12 H-1~H-5检测维度增强
- OPT-13 report_builder修复
- OPT-14 在线验证降级策略
- OPT-15 证据索引缓存

### M3：用户体验与扩展性（预计2周）
- OPT-17~OPT-23 缓存、进度、配置、多语言、导出、限流、测试

---

## 四、技术债务记录

| 编号 | 描述 | 影响 |
|------|------|------|
| TD-01 | law_citation_checker.py中REPLACED_LAWS的遍历使用`isinstance(info, dict)`兼容新旧格式 | 新格式稳定后可移除兼容代码 |
| TD-02 | evidence_index.py的`get_procedural_timeline`依赖正则提取，对非标准文书格式可能遗漏 | 后续可引入NLP分句+NER |
| TD-03 | server.py中batch_detect和compare_documents重复调用`_rule_engine.run_full_scan` | 可抽取为公共方法 |
| TD-04 | `_check_procedural_time_limits`中TimeBarIssue的resignation_date/arbitration_date字段语义不精确 | 需重构TimeBarIssue模型 |
