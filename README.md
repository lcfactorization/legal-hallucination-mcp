# 法律文书幻觉检测 MCP 服务器

> 基于桥接架构的法律文书大模型幻觉自动检测系统，无需调用 LLM API，借助 AI Agent 自身大模型完成语义判断。

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![CI](https://img.shields.io/badge/CI-passing-brightgreen)](./.github/workflows/ci.yml)
[![MCP](https://img.shields.io/badge/protocol-MCP-orange)](https://modelcontextprotocol.io/)
[![Version](https://img.shields.io/badge/version-0.1.0-blue)](./CHANGELOG.md)

[English](./README_EN.md) | [修改日志](./CHANGELOG.md) | [开发计划](./docs/dev_plan_v2.md)

---

## 目录

- [项目简介](#项目简介)
- [快速开始](#快速开始)
- [安装指南](#安装指南)
- [六维幻觉检测体系](#六维幻觉检测体系)
- [架构说明](#架构说明)
- [配置 MCP 客户端](#配置-mcp-客户端)
- [使用示例](#使用示例)
- [MCP 工具参考](#mcp-工具参考)
- [检测报告结构](#检测报告结构)
- [风险评分体系](#风险评分体系)
- [证据索引机制](#证据索引机制)
- [审计底座](#审计底座verify_agentpy)
- [项目结构](#项目结构)
- [贡献指南](#贡献指南)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 项目简介

本工具是一个基于 MCP（Model Context Protocol）协议的法律文书幻觉检测服务器，专门用于检测大模型生成的司法裁判文书中可能存在的幻觉问题。

**核心设计理念**：服务器自身不调用任何 LLM API，仅提供规则引擎和提示词模板，由 AI Agent（如 Trae、Claude Desktop 等）使用自身大模型完成语义层面的判断。

**封闭宇宙规则**：大模型生成的每一句法律事实，必须完全源自 `evidence_manifest.md`（证据索引清单）中所列出并存在于工作区中的真实文件。绝对禁止任何常识性推导、主观脑补或艺术加工。

**强制文书结构**：生成的判决书草稿必须严格包含且仅包含以下四个标准 Markdown 一级标题：

```
# 一、当事人的诉讼请求与主张
# 二、本院查明事实
# 三、本院认为（说理部分）
# 四、判决如下（判决主文）
```

---

## 快速开始

60 秒体验核心功能：

```bash
# 1. 克隆仓库
git clone https://github.com/your-username/legal-hallucination-mcp.git
cd legal-hallucination-mcp

# 2. 安装
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .

# 3. 复制环境变量模板
cp .env.example .env

# 4. 用示例文件运行检测
python scripts/verify_agent.py \
  --vault-root ./vault_mirror \
  --manifest ./vault_mirror/.trae/evidence_manifest.md \
  --draft ./vault_mirror/output/sample_judgment.md
```

看到 `[AUDIT_PASSED]` 表示文书通过检测，`[AUDIT_FAILED]` 则会列出具体问题。

---

## 安装指南

### 环境要求

| 组件 | 最低版本 | 说明 |
|:---|:---|:---|
| Python | 3.11+ | 推荐 3.11 或 3.12 |
| pip | 23.0+ | Python 包管理器 |
| 操作系统 | Windows / macOS / Linux | 跨平台支持 |

可选依赖：

| 组件 | 用途 |
|:---|:---|
| `uv` | 更快的 Python 包管理器（替代 pip） |
| `make` | 便捷构建命令（非必需） |

### 步骤一：获取源码

```bash
git clone https://github.com/your-username/legal-hallucination-mcp.git
cd legal-hallucination-mcp
```

### 步骤二：创建虚拟环境（推荐）

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows (PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# Windows (CMD)
python -m venv .venv
.venv\Scripts\activate.bat
```

### 步骤三：安装依赖

**用户安装（仅使用，不开发）：**

```bash
pip install -e .
```

**开发者安装（含测试和代码检查工具）：**

```bash
pip install -e ".[dev]"
```

**使用 uv（更快）：**

```bash
# 用户安装
uv pip install -e .

# 开发者安装
uv pip install -e ".[dev]"
```

### 步骤四：配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入实际路径：

```ini
# 证据索引清单路径（绝对路径）
EVIDENCE_MANIFEST_PATH=C:\Users\xxx\Obsidian Vault\.trae\evidence_manifest.md

# 工作区根目录
VAULT_ROOT=C:\Users\xxx\Obsidian Vault

# 本地法律法规库目录
LOCAL_LAW_DIR=C:\Users\xxx\Obsidian Vault\案件\法律法规

# 日志级别（DEBUG/INFO/WARNING/ERROR）
LOG_LEVEL=INFO
```

| 变量名 | 必填 | 说明 | 示例 |
|:---|:---|:---|:---|
| `EVIDENCE_MANIFEST_PATH` | 是 | 证据索引清单绝对路径 | `C:\path\to\evidence_manifest.md` |
| `VAULT_ROOT` | 是 | 工作区根目录 | `C:\Users\xxx\Obsidian Vault` |
| `LOCAL_LAW_DIR` | 否 | 本地法律法规库目录 | `C:\path\to\法律法规` |
| `SKILLS_DIR` | 否 | 技能模板目录（默认用内置） | `C:\path\to\skills` |
| `ANCHORS_DIR` | 否 | 锚定示例目录（默认用内置） | `C:\path\to\anchors` |
| `LOG_LEVEL` | 否 | 日志级别，默认 `INFO` | `DEBUG` |

### 步骤五：验证安装

```bash
# 运行测试确认安装正确
python -c "from legal_hallucination_mcp import __version__; print(__version__)"
# 输出: 0.1.0

# 运行测试套件（开发者安装）
pytest tests/ -v --tb=short
```

### 常见安装问题

**问题：`pip install -e .` 报错 "No module named hatchling"**

```bash
pip install --upgrade pip build hatchling
pip install -e .
```

**问题：cn2an 安装失败**

```bash
# 确保 Python >= 3.11
python --version
pip install cn2an>=0.5.0
```

**问题：`legal-hallucination-mcp` 命令找不到**

确认已使用 `-e`（editable）模式安装，或将 `src/` 目录添加到 `PYTHONPATH`：

```bash
export PYTHONPATH="$PYTHONPATH:$(pwd)/src"
```

---

## 六维幻觉检测体系

| 维度 | 代码 | 检测内容 | 风险等级 |
|:---|:---|:---|:---|
| 无源编造事实 | H-1 | 引注欺诈、内部版本引用、法条虚构、事实来源未绑定证据、类案杜撰 | 最高 |
| 法律适用错误 | H-2 | 已废止法律、引用格式不规范、法律方法论替换、程序日期杜撰 | 高 |
| 证据链断裂 | H-3 | 三段论缺失大前提/小前提、文书结构缺失 | 高 |
| 主观臆断/修辞过度 | H-4 | 道德评价、意图推断、情感化修辞 | 中 |
| 诉求边界突破 | H-5 | 判决金额超出诉请上限、项目越权、计算错误、利息基数错误 | 高 |
| 非文本证据穿透失败 | H-6 | 录音/录像/鉴定意见等未标注来源形式 | 低 |

每个维度对应的技能模板位于 `src/legal_hallucination_mcp/skills/dimensions/` 目录：

```
h1_sourceless_fabrication.md   # 无源编造事实
h2_law_misapplication.md       # 法律适用错误
h3_evidence_chain_break.md     # 证据链断裂
h4_subjective_rhetoric.md      # 主观修辞
h5_claim_boundary_breach.md    # 诉求边界突破
h6_nontext_evidence_fail.md    # 非文本证据穿透失败
```

---

## 架构说明

```
┌─────────────┐     MCP协议      ┌──────────────────────┐
│  AI Agent   │ ◄──────────────► │  MCP Server          │
│ (Trae/Claude)│                  │                      │
│             │                   │  ┌────────────────┐  │
│  自身LLM    │◄──提示词模板──────│  │ 规则引擎       │  │
│  语义判断   │───LLM响应───────►│  │ (正则+数值比对) │  │
│             │                   │  └────────────────┘  │
│             │                   │  ┌────────────────┐  │
│             │                   │  │ 证据索引       │  │
│             │                   │  └────────────────┘  │
│             │                   │  ┌────────────────┐  │
│             │                   │  │ 诉请解析器     │  │
│             │                   │  └────────────────┘  │
│             │                   │  ┌────────────────┐  │
│             │                   │  │ 法条引用校验   │  │
│             │                   │  └────────────────┘  │
│             │                   │  ┌────────────────┐  │
│             │                   │  │ 报告生成器     │  │
│             │                   │  └────────────────┘  │
│             │                   │  ┌────────────────┐  │
│             │                   │  │ 审计底座       │  │
│             │                   │  │ (verify_agent) │  │
│             │                   │  └────────────────┘  │
└─────────────┘                   └──────────────────────┘
```

**规则引擎**：基于正则表达式、模式匹配和数值比对的确定性检测，无需 LLM，可快速返回结果。

**审计底座**：`scripts/verify_agent.py` 实现诉请边界审计和三段论完整性审计，输出 `[AUDIT_PASSED]` 或 `[AUDIT_FAILED]`。

**提示词模板**：对于规则引擎无法覆盖的语义判断（如事实编造、法条内容核对），服务器返回提示词模板，由 Agent 使用自身 LLM 完成判断。

---

## 配置 MCP 客户端

### Trae / Claude Desktop

在 MCP 客户端配置文件中添加：

```json
{
  "mcpServers": {
    "legal-hallucination": {
      "command": "python",
      "args": ["-m", "legal_hallucination_mcp.server"],
      "cwd": "C:\\path\\to\\legal-hallucination-mcp",
      "env": {
        "PYTHONPATH": "C:\\path\\to\\legal-hallucination-mcp\\src",
        "EVIDENCE_MANIFEST_PATH": "C:\\path\\to\\evidence_manifest.md",
        "VAULT_ROOT": "C:\\path\\to\\vault"
      }
    }
  }
}
```

### WorkBuddy / CodeBuddy

在 `~/.workbuddy/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "legal-hallucination": {
      "command": "python",
      "args": ["-m", "legal_hallucination_mcp.server"],
      "cwd": "C:\\Users\\stere\\Documents\\Obsidian Vault\\legal-hallucination-mcp",
      "env": {
        "PYTHONPATH": "C:\\Users\\stere\\Documents\\Obsidian Vault\\legal-hallucination-mcp\\src",
        "EVIDENCE_MANIFEST_PATH": "C:\\Users\\stere\\Documents\\Obsidian Vault\\.trae\\evidence_manifest.md",
        "VAULT_ROOT": "C:\\Users\\stere\\Documents\\Obsidian Vault"
      }
    }
  }
}
```

配置完成后，MCP 客户端需要手动启用该服务器（通常在连接器管理页面点击"信任"或"启用"按钮）。

---

## 使用示例

### 示例一：判决书草稿全量幻觉检测

**场景**：你有一份大模型生成的判决书草稿 `judgment_draft.md`，需要全面检测是否存在幻觉。

**步骤**：

1. 确认 `evidence_manifest.md` 已包含所有证据文件
2. 通过 MCP 客户端调用 `run_rule_engine_full` 工具：

```
调用工具: run_rule_engine_full
参数:
  document_text: <判决书全文>
  manifest_path: "C:\Users\stere\Documents\Obsidian Vault\.trae\evidence_manifest.md"
  vault_root: "C:\Users\stere\Documents\Obsidian Vault"
  document_name: "V42_模拟二审判决书_苏06民终6271号"
  content_summary: "苏06民终6271号劳动争议二审模拟判决书"
  agent_name: "TraeCN"
  llm_version: "DSV4P"
  save_report: true
```

**返回结果示例**：

```json
{
  "audit_status": "AUDIT_FAILED",
  "score": 43.25,
  "risk_grade": "D",
  "total_flags": 16,
  "dimension_summary": {
    "H-1 无源编造事实": {"flags": 3, "severity": "high"},
    "H-2 法律适用错误": {"flags": 10, "severity": "high"},
    "H-3 证据链断裂": {"flags": 0, "severity": "none"},
    "H-4 主观修辞过度": {"flags": 0, "severity": "none"},
    "H-5 诉求边界突破": {"flags": 2, "severity": "medium"},
    "H-6 非文本证据穿透失败": {"flags": 1, "severity": "low"}
  }
}
```

生成的报告保存在 `output/` 目录，文件名格式：
`TraeCN_DSV4P_苏06民终6271号劳动争议二审模拟判决书_V42_20260530.md`

### 示例二：命令行审计判决书草稿

**场景**：不通过 MCP，直接在命令行快速审计一份判决书草稿。

```bash
python scripts/verify_agent.py \
  --vault-root "C:\Users\stere\Documents\Obsidian Vault" \
  --manifest "C:\Users\stere\Documents\Obsidian Vault\.trae\evidence_manifest.md" \
  --draft "C:\Users\stere\Documents\Obsidian Vault\output\V42_模拟二审判决书.md"
```

**输出示例（AUDIT_PASSED）**：
```
[LegalHarness] 加载证据索引...
[LegalHarness] 已装载 51 条有效证据
[LegalHarness] 开始审计...

[PASS] 文书结构检测 — 四个必需段落标题完整
[PASS] 引注欺诈检测 — 所有证据引注均存在于证据索引
[PASS] 诉请边界审计 — 判决金额未超出诉请上限
[PASS] 三段论完整性审计 — 说理部分包含法律依据和证据锚点

[AUDIT_PASSED] 所有检测通过，耗时 1.23 秒
```

**输出示例（AUDIT_FAILED）**：
```
[LegalHarness] 开始审计...

[PASS] 文书结构检测 — 四个必需段落标题完整
[FAIL] 引注欺诈检测 — 发现 3 个不在证据索引中的引注:
  - "证据30" → 实际文件为 "证据30-1_股东会议决议对账单等.pdf"
  - "证据35" → 证据索引中不存在
  - "证据42.pdf" → 不应包含文件扩展名
[FAIL] 诉请边界审计 — 判决金额超出诉请上限:
  - 奖金违约损害赔偿金: 判决 189,750 元 > 诉请 0 元（项目越权）
  - 加付赔偿金: 判决 2,488,798.49 元 > 诉请上限 644,290 元

[AUDIT_FAILED] 发现 2 类错误，共 4 项问题
```

### 示例三：多版本并行扫描

**场景**：你有 V40/V40P1/V41/V42 四个版本的判决书草稿，想并行检测并生成比对报告。

```bash
python scripts/multi_version_scan.py
```

脚本会自动扫描 `output/` 目录下的所有版本，使用 `ThreadPoolExecutor`（最多 4 个 worker）并行执行六维规则引擎检测。

**输出示例**：

```
[Scanner] 发现 4 个版本: V40, V40P1, V41, V42
[Scanner] 开始并行扫描 (4 workers)...

[V40]  45% | 规则引擎检测中... | 耗时 3.2s | 预估 Token: 19,201
[V40P1] 45% | 规则引擎检测中... | 耗时 3.4s | 预估 Token: 19,702
[V41]  45% | 规则引擎检测中... | 耗时 2.8s | 预估 Token: 16,336
[V42]  45% | 规则引擎检测中... | 耗时 3.3s | 预估 Token: 19,488

[扫描完成]

| 版本 | 总Flag | 严重 | 高 | 中 | 低 | Token估算 |
|:---|:---|:---|:---|:---|:---|:---|
| V40 | 19 | 0 | 13 | 5 | 1 | 19,201 |
| V40P1 | 17 | 0 | 12 | 4 | 1 | 19,702 |
| V41 | 20 | 0 | 14 | 5 | 1 | 16,336 |
| V42 | 16 | 0 | 10 | 5 | 1 | 19,488 |

对比报告已保存到 output/multi_version_scan_report_20260530.md
```

### 示例四：初始化新案件工作区

**场景**：接手一个新案件，需要初始化工作区目录和证据索引清单。

```bash
python -c "
from legal_hallucination_mcp.server import init_case_workspace
result = init_case_workspace(
    vault_root='C:\\\\Users\\\\stere\\\\Documents\\\\Obsidian Vault',
    case_name='苏06民终9999号',
    evidence_files=[
        '证据材料/证据1_劳动合同.pdf',
        '证据材料/证据2_工资明细.xlsx',
        '证据材料/证据3_微信聊天记录.pdf',
    ],
    complaint_file='诉状类/起诉状_张三_劳动争议.md'
)
print(result)
"
```

**输出示例**：
```
[WorkspaceInit] 工作区初始化完成
- 案件目录: C:\Users\stere\Documents\Obsidian Vault\苏06民终9999号
- 证据索引: C:\Users\stere\Documents\Obsidian Vault\苏06民终9999号\evidence_manifest.md
- 已注册证据: 3 份
- 已链接诉状: 1 份
```

### 示例五：证据清单管理

**场景**：在办案过程中需要维护证据索引清单。

```bash
cd legal-hallucination-mcp

# 检查清单与文件系统的一致性
python scripts/update_evidence_manifest.py --check

# 添加新证据
python scripts/update_evidence_manifest.py --add "C:\path\to\证据31_鉴定意见.pdf"

# 从检测报告中提取缺失证据
python scripts/update_evidence_manifest.py --from-report "output\TraeCN_DSV4P_检测报告_V42_20260530.md"

# 更新已有证据条目的描述
python scripts/update_evidence_manifest.py --update "证据30" --desc "股东会议决议及进账单对账单"
```

### 示例六：获取封闭宇宙规则提示词

**场景**：你需要让 AI Agent 在生成判决书时严格遵循封闭宇宙规则。

通过 MCP 调用 `get_closed_universe_prompt`：

```
调用工具: get_closed_universe_prompt
参数:
  manifest_path: "C:\Users\stere\Documents\Obsidian Vault\.trae\evidence_manifest.md"
  vault_root: "C:\Users\stere\Documents\Obsidian Vault"
```

该工具返回一段可直接注入 Agent 系统提示的文本，包含当前案件的完整证据清单和强制证据绑定规则。

### 示例七：法条在线验证

**场景**：怀疑判决书中引用的法条可能已废止，需要在线核实。

通过 MCP 调用 `verify_law_citation_online`：

```
调用工具: verify_law_citation_online
参数:
  citation_text: "《江苏省高级人民法院关于审理劳动人事争议案件的指导意见（二）》（苏高法审委〔2011〕14号）第十条"
```

**返回结果示例**：
```json
{
  "citation": "苏高法审委〔2011〕14号",
  "status": "已废止",
  "replaced_by": "《江苏省高级人民法院劳动争议案件审理指南》",
  "effective_date": "2020-01-01",
  "confidence": 0.95,
  "source": "江苏省高级人民法院官网"
}
```

### 示例八：批量检测与版本比对

**场景**：同时检测多份文书或比对两个版本的差异。

```
# 批量检测
调用工具: batch_detect
参数:
  documents: [
    {"name": "V41", "text": "<V41判决书全文>"},
    {"name": "V42", "text": "<V42判决书全文>"}
  ]
  manifest_path: "..."
  vault_root: "..."
```

```
# 两版比对
调用工具: compare_documents
参数:
  doc_a_name: "V41"
  doc_a_text: "<V41判决书全文>"
  doc_b_name: "V42"
  doc_b_text: "<V42判决书全文>"
  manifest_path: "..."
  vault_root: "..."
```

---

## MCP 工具参考

### 基础查询（3 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `list_dimensions` | 列出所有检测维度 | 无 | 维度元数据 |
| `extract_document_sections` | 提取文书各段落 | 判决书全文 | 段落字典 |
| `get_detection_config` | 获取当前检测配置 | 无 | 配置详情 |

### 证据管理（2 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `load_evidence_manifest` | 装载证据索引 | 清单路径、工作区根目录 | 有效证据集合 |
| `init_case_workspace` | 初始化新案件工作区 | 工作区根目录、案件名称、证据列表 | 初始化结果 |

### 单项检测（6 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `check_structure` | 文书结构检测 | 判决书全文 | 缺失标题列表 |
| `check_citation_fraud` | 引注欺诈检测 | 判决书全文 | 欺诈引注列表 |
| `check_claim_boundary` | 诉请边界检测 | 诉状文本、判决书全文 | 越权项目列表 |
| `check_syllogism` | 三段论完整性检测 | 判决书全文 | 断裂点列表 |
| `check_subjective_rhetoric` | 主观修辞检测 | 判决书全文 | 修辞项列表 |
| `check_law_citations` | 法条引用检测 | 判决书全文、法条库路径 | 引用问题列表 |

### 全量检测（3 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `run_rule_engine_full` | 完整规则引擎检测 | 判决书全文、清单路径、工作区根目录 | 完整检测结果+报告 |
| `batch_detect` | 批量检测多份文书 | 文书列表、清单路径、工作区根目录 | 批量检测结果 |
| `compare_documents` | 两版比对 | 两份文书、清单路径、工作区根目录 | 差异对比报告 |

### 语义检测（3 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `render_dimension_prompt` | 渲染维度提示词 | 维度代码、文书段落 | 提示词文本 |
| `parse_hallucination_result` | 解析LLM响应 | 维度代码、LLM响应文本 | 结构化标志列表 |
| `calculate_hallucination_score` | 计算幻觉评分 | 检测结果 | 评分+风险等级 |

### 审计验证（4 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `verify_judgment_draft` | 判决书草稿审计 | 工作区根目录、清单路径、草稿路径 | 审计结果 |
| `verify_law_citation_online` | 在线法条验证 | 法条引用文本 | 验证结果 |
| `verify_case_number_online` | 在线案号验证 | 案号文本 | 验证结果 |
| `parse_verification_response` | 解析验证结果 | 验证响应文本 | 结构化验证结论 |

### 交叉验证（2 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `cross_verify_document` | 交叉验证文档 | 判决书全文、清单路径、法律库路径 | 验证报告 |
| `run_cross_verification` | 完整交叉验证 | 判决书全文、清单路径、法律库路径 | 完整 CrossReferenceReport |

### 知识库与向量索引（7 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `load_law_knowledge_base` | 装载法律知识库 | 法律法规库目录 | 知识库对象 |
| `search_applicable_law` | 搜索适用法律 | 关键字 | 匹配结果 |
| `get_law_kb_statistics` | 知识库统计 | 无 | 统计信息 |
| `get_authoritative_sources` | 权威来源列表 | 无 | 来源 URL 列表 |
| `build_vector_index` | 构建向量索引 | 法律法规库目录、证据清单路径 | 索引构建结果 |
| `search_vector_index` | 搜索向量索引 | 查询文本、文档类型、数量 | 搜索结果列表 |
| `build_llm_context` | 构建LLM上下文 | 查询文本、文档类型、令牌上限 | 上下文文本 |
| `get_vector_index_stats` | 获取向量索引统计 | 无 | 索引统计信息 |

### 工作流编排（8 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `create_detection_workflow` | 创建多子代理并行检测工作流 | 判决书全文、文档名称、清单路径、工作区根目录、最大令牌数 | 工作流ID+并行任务列表 |
| `get_workflow_tasks` | 获取可并行执行的子任务 | 工作流ID | 子任务列表 |
| `update_workflow_task` | 更新子任务状态 | 工作流ID、任务ID、状态、结果JSON | 更新确认 |
| `get_workflow_status` | 获取工作流状态 | 工作流ID | 详细状态 |
| `aggregate_workflow_results` | 汇总工作流检测结果 | 工作流ID | 综合评估 |
| `list_workflows` | 列出所有工作流 | 无 | 工作流列表 |
| `set_orchestration_mode` | 设置编排模式 | 模式名称 | 确认 |
| `get_orchestration_mode` | 获取编排模式 | 无 | 当前模式 |

### 输出与提示词（4 个）

| 工具名 | 功能 | 输入 | 输出 |
|:---|:---|:---|:---|
| `generate_report_filename_tool` | 生成报告文件名 | Agent名、LLM版本、概要、版本号 | 文件名 |
| `save_hallucination_report` | 保存报告到文件 | 报告文本、输出路径 | 保存确认 |
| `export_detection_result` | 导出JSON/CSV | 检测结果、格式 | 导出文件路径 |
| `estimate_tokens` | 估算令牌数 | 技能名、文本 | 令牌数估算 |
| `get_closed_universe_prompt` | 获取封闭宇宙规则提示词 | 清单路径、工作区根目录 | 提示词文本 |
| `get_judgment_draft_template` | 获取判决书草稿模板 | 案件类型、清单路径、工作区根目录 | 模板文本 |

---

## 检测报告结构

生成的检测报告包含以下章节：

1. **综合风险概览** — 评分、风险等级、各维度汇总
2. **文书结构检测** — 四个必需段落标题是否完整
3. **引注欺诈检测（H-1）** — 证据引注是否在证据索引清单中
4. **事实来源绑定检测（H-1）** — 事实陈述是否绑定证据来源
5. **法条引用检测（H-2）** — 已废止法律、格式不规范、张冠李戴
6. **三段论完整性检测（H-3）** — 说理部分大前提+小前提
7. **主观修辞检测（H-4）** — 道德评价、意图推断、情感化修辞
8. **诉求边界检测（H-5）** — 判决金额是否超出诉请上限
9. **非文本证据穿透检测（H-6）** — 录音/录像等是否标注来源
10. **交叉验证与原始文件核对** — 事实陈述与证据材料的多源比对
11. **法律知识库验证** — 法条引用与知识库原文比对
12. **各维度详细标志** — 每个标志的原文、位置、严重程度
13. **综合修正建议** — 按优先级排列的修正建议
14. **紧急修复优先级** — P0至P3分级修复指引
15. **文书生成后自检清单** — A-F六类自检项

报告文件名规范：`{AI Agent名}_{LLM名版本号}_{内容概要}_{版本号}_{YYYYMMDD}.md`

报告内部使用 GitHub Alerts 格式（`> [!NOTE]`、`> [!WARNING]`、`> [!TIP]`、`> [!IMPORTANT]`、`> [!CAUTION]`）分类高亮显示不同类型的批注、点评和建议。

---

## 风险评分体系

| 等级 | 分数区间 | 含义 |
|:---:|:---|:---|
| A | 0-5 | 极低风险：文书几乎无幻觉痕迹 |
| B | 5-15 | 低风险：存在少量轻微幻觉，不影响裁判结论 |
| C | 15-30 | 中风险：存在多处幻觉，可能影响裁判公正性 |
| D | 30-50 | 高风险：幻觉密集，裁判结论可信度存疑 |
| F | 50-100 | 极高风险：幻觉泛滥，文书基本不可信 |

评分采用递减惩罚机制：同一规则首次命中按全权重计分，后续命中按30%权重计分，避免同一模式的大量重复匹配导致评分虚高。

---

## 证据索引机制

系统从 `evidence_manifest.md` 动态装载有效证据文件名集合，用于引注欺诈检测和事实来源绑定检测。证据清单格式示例：

```markdown
# 证据索引清单

## 诉状类
- `路径/起诉状_当事人_案由.md`

## 证据类
- `路径/证据1_微信聊天记录.md`
- `路径/证据2_劳动合同.md`

## 法律依据类
- `路径/法律法规/法律名称.md`

## 类案类
- `路径/类案/（2020）苏03民终3088号.md`
```

---

## 审计底座（verify_agent.py）

`scripts/verify_agent.py` 实现了 `LegalHarness` 类，包含以下核心方法：

| 方法 | 功能 | 检测内容 |
|:---|:---|:---|
| `load_and_compile()` | 装载证据索引+诉状 | 构建有效证据文件名集合、诉请金额上限 |
| `verify_structure()` | 文书结构检测 | 四个必需段落标题是否完整 |
| `verify_citation_fraud()` | 引注欺诈检测 | 证据引注是否在证据索引清单中 |
| `verify_strict_scope_containment()` | 诉请边界审计 | 判决金额 vs 诉请上限 |
| `verify_syllogism_complete_chain()` | 三段论完整性审计 | 引注防伪+说理大前提小前提检测 |
| `verify_fact_source_binding()` | 事实来源绑定检测 | 查明事实部分每句是否标注证据来源 |
| `run_all_checks()` | 运行全部检查 | 汇总错误，输出 PASSED/FAILED |

**迭代修复闭环**：脚本返回 `[AUDIT_FAILED]` 时，必须跟用户确认是否需要根据报告修正。若需要，则必须重新修改文书并再次运行脚本校验，直到脚本返回 `[AUDIT_PASSED]`。

---

## 项目结构

```
legal-hallucination-mcp/
├── src/legal_hallucination_mcp/
│   ├── __init__.py              # 包初始化
│   ├── server.py                # MCP 服务器主入口（29 个工具）
│   ├── rule_engine.py           # 规则引擎核心（11 个子检测）
│   ├── config.py                # 配置与规则定义（30+ 正则规则）
│   ├── models.py                # 数据模型（Pydantic）
│   ├── evidence_index.py        # 证据索引装载
│   ├── evidence_manifest_updater.py # 证据清单版本化更新
│   ├── claim_parser.py          # 诉请金额解析
│   ├── law_citation_checker.py  # 法条引用校验
│   ├── law_knowledge_base.py    # 法律知识库
│   ├── cross_reference_engine.py # 交叉验证引擎
│   ├── web_verifier.py          # 网络验证模块
│   ├── vector_index.py          # 向量索引模块
│   ├── workflow_orchestrator.py  # 工作流编排模块
│   ├── report_builder.py        # 检测报告生成
│   ├── skill_runner.py          # 技能模板加载
│   ├── response_parser.py       # LLM响应解析
│   └── skills/dimensions/       # 各维度提示词模板
│       ├── h1_sourceless_fabrication.md
│       ├── h2_law_misapplication.md
│       ├── h3_evidence_chain_break.md
│       ├── h4_subjective_rhetoric.md
│       ├── h5_claim_boundary_breach.md
│       └── h6_nontext_evidence_fail.md
├── scripts/
│   ├── verify_agent.py          # 审计底座（独立版）
│   ├── run_detection.py         # 命令行检测脚本
│   ├── multi_version_scan.py    # 多版本并行扫描
│   └── update_evidence_manifest.py # 证据清单 CLI 管理
├── tests/
│   ├── test_adversarial.py      # 对抗性测试
│   ├── test_integration.py      # 集成测试
│   ├── test_orchestration.py    # 编排测试
│   └── test_regex_fix.py        # 正则修复测试
├── docs/
│   ├── dev_plan_v2.md           # 开发计划
│   └── 法律引用与幻觉规避操作手册_v3.0_20260529.md
├── .github/workflows/ci.yml    # GitHub Actions CI
├── pyproject.toml
├── .env.example
├── CHANGELOG.md
├── README.md
└── README_EN.md
```

---

## 贡献指南

欢迎贡献！无论你是修复 bug、添加功能、改进文档，还是报告问题，都请遵循以下流程。

### 行为准则

本项目遵循 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则。请保持友善、专业和建设性。

### 如何贡献

#### 报告问题

1. 在 GitHub Issues 中搜索是否已有相同问题
2. 若没有，创建新 Issue，包含：
   - **标题**：简明描述问题
   - **环境信息**：Python 版本、操作系统、MCP 客户端
   - **复现步骤**：最小化复现用例
   - **期望行为 vs 实际行为**
   - **相关日志或输出**

#### 提交代码

1. **Fork 仓库** 并克隆到本地
2. **创建分支**：使用语义化分支名

```bash
git checkout -b feature/your-feature-name    # 新功能
git checkout -b fix/issue-description        # 修复 bug
git checkout -b docs/what-you-updated        # 文档更新
```

3. **搭建开发环境**：

```bash
git clone https://github.com/your-username/legal-hallucination-mcp.git
cd legal-hallucination-mcp
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

4. **遵循编码规范**：

- Python 3.11+ 语法
- 行宽：120 字符（见 `pyproject.toml` 中 `tool.ruff.line-length`）
- 代码风格：遵循 Ruff 规则（E、F、I、N、W、UP）
- 类型提示：在公共 API 中使用类型注解
- 文档字符串：关键函数和方法必须有中文注释

5. **运行检查**（提交前必须通过）：

```bash
# 代码风格检查
ruff check src/ tests/

# 自动修复
ruff check --fix src/ tests/

# 运行测试
pytest tests/ -v --tb=short

# 运行特定测试
pytest tests/test_adversarial.py -v
```

6. **编写测试**：

- 新功能必须包含测试用例
- bug 修复应添加回归测试
- 测试文件位于 `tests/` 目录
- 运行全部测试确保没有回归

```bash
# 测试覆盖率检查
pip install pytest-cov
pytest tests/ --cov=src/legal_hallucination_mcp --cov-report=term-missing
```

7. **提交代码**：

```bash
# 提交信息格式
git commit -m "feat: 添加证据清单自动更新功能"
git commit -m "fix: 修复类案杜撰误报（V42.3）"
git commit -m "docs: 完善 README 安装指南"
git commit -m "test: 增加上诉期超期检测测试用例"
```

提交信息规范：

| 前缀 | 用途 |
|:---|:---|
| `feat:` | 新功能 |
| `fix:` | Bug 修复 |
| `docs:` | 文档更新 |
| `test:` | 测试相关 |
| `refactor:` | 代码重构 |
| `chore:` | 构建/工具链变更 |
| `perf:` | 性能优化 |

8. **创建 Pull Request**：

- 推送到你的 fork：`git push origin feature/your-feature-name`
- 在 GitHub 上创建 PR
- PR 标题使用中文或英文均可，描述需包含：
  - **做了什么**（简述变更）
  - **为什么做**（动机）
  - **测试情况**（是否通过 CI）
  - **相关 Issue**（如有关联）

### 开发工作流

```
[Issue] → [分支] → [开发] → [测试] → [PR] → [Code Review] → [合并]
```

**PR 审核清单**：

- [ ] 代码通过 ruff 检查
- [ ] 所有测试通过
- [ ] 新功能有测试覆盖
- [ ] 文档已更新（README、CHANGELOG）
- [ ] 提交历史清晰（可 squash 合并）
- [ ] 无破坏性变更（或已标记）

### 参与文档编写

文档改进同样重要：

- `README.md` — 项目主文档
- `README_EN.md` — 英文文档（需与中文同步）
- `CHANGELOG.md` — 版本更新日志
- `docs/` — 开发计划、操作手册等深度文档
- `skills/dimensions/` — 各维度检测技能模板

### CI 流程

每次 push 和 PR 会自动触发 GitHub Actions：

```
Lint (ruff) → Test (pytest, Python 3.11 & 3.12)
```

确保提交前本地已通过以上两项检查。

---

## 常见问题

### 一般问题

**Q：这个服务器需要调用 LLM API 吗？需要 API Key 吗？**

A：不需要。桥接架构的核心设计就是不调用任何 LLM API。规则引擎基于正则和数值比对直接返回结果；语义判断部分由 AI Agent 自身的 LLM 完成，服务器仅提供提示词模板。

**Q：支持哪些类型的法律文书？**

A：目前专注支持中国劳动争议案件的民事判决书。后续计划扩展到其他民商事案件（见 [开发计划](./docs/dev_plan_v2.md) 中的 OPT-20 多语言支持）。

**Q：检测速度如何？**

A：纯规则引擎检测非常快（通常 < 5 秒），因为它基于正则匹配和数值比对，无需网络请求。涉及向量检索或在线验证时会稍慢。

### 配置问题

**Q：MCP 客户端无法连接服务器？**

A：检查以下几点：
1. Python 路径是否正确（`which python`）
2. `PYTHONPATH` 是否包含 `src/` 目录
3. 是否使用 `-e` 模式安装（`pip install -e .`）
4. MCP 客户端是否已信任该服务器

**Q：环境变量不生效？**

A：确保变量名使用 `LH_` 前缀（如 `LH_EVIDENCE_MANIFEST_PATH`），详见 `config.py` 中的 `DetectionConfig` 类。

### 检测问题

**Q：为什么我的判决书通过了审计但仍然有幻觉评分？**

A：审计底座（verify_agent）只检查结构、引注、诉请边界和三段论完整性。更细粒度的检测（事实编造、法律适用、主观修辞等）需要运行 `run_rule_engine_full` 或使用语义检测模板。

**Q：证据引注检测误报很多？**

A：证据引注需要与 `evidence_manifest.md` 中的文件名精确匹配（不含扩展名）。确保清单中的文件名与判决书中引用的名称一致。短引注（≤4字符）可能匹配率较低，这是已知问题（OPT-04）。

**Q：如何让检测评分不虚高？**

A：系统已采用递减惩罚机制。如果仍有虚高，检查是否同一模式被大量重复标记——这是提示需要从源头修复判决书而非反复检测。

---

## 许可证

MIT License

Copyright (c) 2025 Legal Hallucination MCP Team
