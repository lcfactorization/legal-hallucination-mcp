---
name: h1_sourceless_fabrication
title: "H-1：无源编造事实"
type: dimension
layer: content
order: 10
weight: 0.25
full_score: 100
output_format: json
---

## 检测目标

检测裁判文书正文中是否存在无证据源支撑的"事实"陈述——即判决书声称某事实成立，但该事实既无证据清单中的书证支持，也无当事人自认，纯粹为编造。

## 检测标准

### 1. 引注欺诈检测（规则可检）

对"本院查明事实"部分中每一句标注了来源的事实陈述，检查其引用的证据文件名是否存在于 `evidence_manifest.md` 清单中。

**判定规则**：
- 引注文件名完全匹配 → 通过
- 引注文件名部分匹配（模糊匹配） → 标记为"疑似"
- 引注文件名无匹配 → 标记为"引注欺诈"

### 2. 无源事实陈述检测（语义需判）

对"本院查明事实"部分中未标注来源的事实陈述，检查其是否：
- 属于当事人自认（可免证）
- 属于众所周知的事实
- 属于自然规律及定理
- 若不属于上述任何免证情形，则标记为"无源编造"

### 3. 内部版本引用检测（规则可检）

检测正文中是否出现 AI 生成文档的内部版本号引用（如 V41P1、V38 等），这是 LLM 生成物的典型痕迹。

## 输出格式

```json
{
  "hallucination_items": [
    {
      "item_name": "引注欺诈/无源编造/内部版本引用",
      "description": "具体描述",
      "severity": "critical|high|medium|low",
      "evidence": "原文引用",
      "legal_basis": "法律依据",
      "suggestion": "修复建议"
    }
  ],
  "score": 85,
  "reasoning": "评分理由"
}
```
