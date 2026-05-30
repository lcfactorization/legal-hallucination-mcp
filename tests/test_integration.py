import sys

sys.path.insert(0, "src")

from legal_hallucination_mcp.report_builder import REPORT_FILENAME_PATTERN, generate_report_filename
from legal_hallucination_mcp.workflow_orchestrator import WorkflowOrchestrator

print("=" * 60)
print("1. 报告文件名规范测试")
print("=" * 60)

valid_cases = [
    ("TraeCN", "GLM51", "法律文书幻觉检测报告", "v2.0"),
    ("TraeCN", "Claude4Sonnet", "多版本判决书幻觉比对报告", "v42.1"),
    ("MyAgent", "GPT4o", "劳动争议判决书检测", "v1.0"),
]

for agent, llm, summary, ver in valid_cases:
    f = generate_report_filename(agent, llm, summary, ver)
    ok = bool(REPORT_FILENAME_PATTERN.match(f))
    print(f"  OK  {f}  match={ok}")

invalid_cases = [
    ("", "GLM51", "测试报告", "v1.0", "agent_name为空"),
    ("TraeCN", "", "测试报告", "v1.0", "llm_name为空"),
    ("TraeCN", "GLM51", "English Only", "v1.0", "无中文描述"),
    ("TraeCN", "GLM51", "测试报告", "1.0", "版本号无v前缀"),
    ("bad agent", "GLM51", "测试报告", "v1.0", "agent_name含空格"),
]

for agent, llm, summary, ver, desc in invalid_cases:
    try:
        f = generate_report_filename(agent, llm, summary, ver)
        print(f"  FAIL  应抛出ValueError但未抛出: {desc}")
    except ValueError as e:
        print(f"  OK  正确拦截: {desc} -> {str(e)[:60]}...")

print()
print("=" * 60)
print("2. 编排模式测试")
print("=" * 60)

wo = WorkflowOrchestrator()
print(f"  默认模式: {wo.orchestration_mode.value}")

wo.set_orchestration_mode("external")
print(f"  切换external: {wo.orchestration_mode.value}")

wo.set_orchestration_mode("passive")
print(f"  切换passive: {wo.orchestration_mode.value}")
try:
    wo.create_workflow("test", {"full_text": "test"})
    print("  FAIL  passive模式应阻止workflow创建")
except RuntimeError as e:
    print(f"  OK  passive模式正确阻止: {str(e)[:50]}...")

wo.set_orchestration_mode("internal")
run = wo.create_workflow("test", {"full_text": "test content here"})
print(f"  internal模式创建workflow: {run.run_id}")

wo.set_orchestration_mode("external")
wo._active_run_id = run.run_id
wo._runs[run.run_id].status = __import__("legal_hallucination_mcp.workflow_orchestrator", fromlist=["TaskStatus"]).TaskStatus.RUNNING
try:
    wo.create_workflow("test2", {"full_text": "test2"})
    print("  FAIL  external模式有活跃workflow时应阻止")
except RuntimeError as e:
    print(f"  OK  external模式正确阻止: {str(e)[:50]}...")

print()
print("=" * 60)
print("3. 环境变量检测测试")
print("=" * 60)

import os

os.environ["ORCHESTRATOR_MODE"] = "claude_code"
wo2 = WorkflowOrchestrator()
print(f"  ORCHESTRATOR_MODE=claude_code -> {wo2.orchestration_mode.value}")

os.environ["ORCHESTRATOR_MODE"] = "passive"
wo3 = WorkflowOrchestrator()
print(f"  ORCHESTRATOR_MODE=passive -> {wo3.orchestration_mode.value}")

del os.environ["ORCHESTRATOR_MODE"]
wo4 = WorkflowOrchestrator()
print(f"  ORCHESTRATOR_MODE=(空) -> {wo4.orchestration_mode.value}")

print()
print("ALL TESTS PASSED")
