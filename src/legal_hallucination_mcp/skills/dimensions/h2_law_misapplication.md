---
name: h2_law_misapplication
title: "H-2：法律适用错误"
type: dimension
layer: content
order: 20
weight: 0.20
full_score: 100
output_format: json
---

## 检测目标

检测裁判文书中的法律引用是否存在以下问题：
1. 引用已废止的法律条文
2. 法条引用格式不规范
3. 法律名称与条文内容不匹配

## 检测标准

### 1. 已废止法律检测（规则可检）

对照 `REPLACED_LAWS` 配置表，检查文书中引用的法律是否已被新法取代。

**典型场景**：
- 引用《劳动合同法》旧版条文
- 引用已废止的司法解释
- 引用已失效的行政法规

### 2. 引用格式检测（规则可检）

检查法条引用是否符合规范格式：
- 《法律名》第X条 — 标准格式
- 《法律名》（法释〔年份〕编号号） — 司法解释格式
- 首次引用全称未标注简称 → 格式问题

### 3. 法律适用匹配检测（语义需判）

检查引用的法律条文是否与案件事实和法律争议相匹配：
- 劳动争议案件是否引用了正确的劳动法律
- 是否存在引用与案件无关的法律条文
- 条文的具体款项是否与裁判理由一致

## 输出格式

```json
{
  "hallucination_items": [
    {
      "item_name": "已废止法律/格式不规范/适用不匹配",
      "description": "具体描述",
      "severity": "critical|high|medium|low",
      "evidence": "原文引用",
      "legal_basis": "正确应引用的法律",
      "suggestion": "修复建议"
    }
  ],
  "score": 90,
  "reasoning": "评分理由"
}
```
