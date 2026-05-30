"""工作流编排模块 — 多子代理并行检测协调器。

借鉴claude-code的coordinator模式，将幻觉检测任务分解为多个子代理并行执行，
每个子代理负责一个或多个维度的检测，减少单次LLM调用的上下文长度，
提高检测效率和准确性。

核心设计：
  1. 任务分解：将六维检测拆分为独立的子任务
  2. 并行执行：多个子任务同时运行，互不阻塞
  3. 令牌约束：按LLM最大上下文令牌数约束每个子任务的输入
  4. 结果聚合：汇总所有子任务结果，生成统一报告
  5. 工作流状态追踪：记录每个子任务的状态和进度
  6. 多材料模式：多份材料每份一个subagent，单份材料按维度分配subagent
  7. 里程碑追踪：50%/100%进度时记录token消耗和耗时
  8. 资源汇总：报告完成时输出token消耗、时间消耗等统计信息

本模块不调用任何LLM API，仅负责任务编排和结果聚合。
实际的LLM调用由AI Agent完成。
"""

import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger("legal-hallucination.workflow")


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class SubTask:
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    description: str = ""
    dimension_codes: list[str] = field(default_factory=list)
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    prompt_template: str = ""
    input_sections: list[str] = field(default_factory=list)
    token_budget: int = 0
    token_used: int = 0
    result: dict[str, Any] | None = None
    error: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    depends_on: list[str] = field(default_factory=list)
    document_name: str = ""

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0


@dataclass
class MilestoneRecord:
    progress_pct: float = 0.0
    token_used: int = 0
    elapsed_ms: float = 0.0
    completed_tasks: int = 0
    total_tasks: int = 0
    timestamp: float = 0.0


@dataclass
class WorkflowRun:
    run_id: str = field(default_factory=lambda: f"wf-{uuid.uuid4().hex[:8]}")
    document_name: str = ""
    total_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    status: TaskStatus = TaskStatus.PENDING
    tasks: list[SubTask] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    total_tokens_budget: int = 0
    total_tokens_used: int = 0
    midpoint_milestone: MilestoneRecord | None = None
    final_milestone: MilestoneRecord | None = None
    mode: str = "dimension"

    @property
    def progress_pct(self) -> float:
        if not self.total_tasks:
            return 0.0
        return round((self.completed_tasks + self.failed_tasks) / self.total_tasks * 100, 1)

    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0

    def check_milestones(self) -> list[str]:
        logs = []
        now = time.time()
        elapsed = (now - self.start_time) * 1000 if self.start_time else 0
        pct = self.progress_pct

        if pct >= 50 and self.midpoint_milestone is None:
            self.midpoint_milestone = MilestoneRecord(
                progress_pct=pct,
                token_used=self.total_tokens_used,
                elapsed_ms=elapsed,
                completed_tasks=self.completed_tasks,
                total_tasks=self.total_tasks,
                timestamp=now,
            )
            logs.append(
                f"[{self.document_name}] ⏱️ 50%里程碑 | Token: {self.total_tokens_used} | "
                f"耗时: {elapsed/1000:.2f}s | 阶段: {self.completed_tasks}/{self.total_tasks}"
            )

        if pct >= 100 and self.final_milestone is None:
            self.final_milestone = MilestoneRecord(
                progress_pct=pct,
                token_used=self.total_tokens_used,
                elapsed_ms=elapsed,
                completed_tasks=self.completed_tasks,
                total_tasks=self.total_tasks,
                timestamp=now,
            )
            logs.append(
                f"[{self.document_name}] ✅ 100%里程碑 | Token: {self.total_tokens_used} | "
                f"耗时: {elapsed/1000:.2f}s | 阶段: {self.completed_tasks}/{self.total_tasks}"
            )

        return logs


DETECTION_WORKFLOW_STAGES = [
    {
        "stage": "structure_check",
        "name": "文书结构检测",
        "dimensions": [],
        "priority": TaskPriority.CRITICAL,
        "description": "检测判决书是否包含四个必需段落标题",
        "input_sections": ["full_text"],
        "token_budget_pct": 0.05,
    },
    {
        "stage": "evidence_binding",
        "name": "证据引注与事实来源检测",
        "dimensions": ["h1"],
        "priority": TaskPriority.CRITICAL,
        "description": "检测证据引注是否在证据索引清单中，事实陈述是否绑定证据来源",
        "input_sections": ["court_finding", "evidence_analysis"],
        "token_budget_pct": 0.20,
    },
    {
        "stage": "law_citation",
        "name": "法条引用与法律适用检测",
        "dimensions": ["h2"],
        "priority": TaskPriority.HIGH,
        "description": "检测法条引用是否存在、是否已废止、是否张冠李戴、法律方法论是否一致",
        "input_sections": ["reasoning"],
        "token_budget_pct": 0.20,
    },
    {
        "stage": "syllogism_check",
        "name": "三段论完整性检测",
        "dimensions": ["h3"],
        "priority": TaskPriority.HIGH,
        "description": "检测说理部分是否同时包含法律依据（大前提）和证据锚点（小前提）",
        "input_sections": ["reasoning"],
        "token_budget_pct": 0.15,
    },
    {
        "stage": "rhetoric_check",
        "name": "主观修辞检测",
        "dimensions": ["h4"],
        "priority": TaskPriority.MEDIUM,
        "description": "检测道德评价、意图推断、情感化修辞等主观臆断",
        "input_sections": ["court_finding", "reasoning"],
        "token_budget_pct": 0.10,
    },
    {
        "stage": "claim_boundary",
        "name": "诉求边界检测",
        "dimensions": ["h5"],
        "priority": TaskPriority.CRITICAL,
        "description": "检测判决金额是否超出诉请上限，项目是否越权，计算是否正确",
        "input_sections": ["plaintiff_claim", "judgment_main"],
        "token_budget_pct": 0.15,
    },
    {
        "stage": "cross_verify",
        "name": "交叉验证与原始文件核对",
        "dimensions": [],
        "priority": TaskPriority.HIGH,
        "description": "将事实陈述与证据材料、法条原文进行多源比对",
        "input_sections": ["court_finding", "evidence_analysis"],
        "token_budget_pct": 0.10,
    },
    {
        "stage": "procedural_check",
        "name": "程序时效检测",
        "dimensions": [],
        "priority": TaskPriority.HIGH,
        "description": "检测上诉期、仲裁时效、再审申请期限等法定程序期限",
        "input_sections": ["full_text"],
        "token_budget_pct": 0.05,
    },
]

DEFAULT_MAX_CONTEXT_TOKENS = 128000
RESERVED_SYSTEM_TOKENS = 8000
RESERVED_OUTPUT_TOKENS = 4000
AVAILABLE_INPUT_TOKENS = DEFAULT_MAX_CONTEXT_TOKENS - RESERVED_SYSTEM_TOKENS - RESERVED_OUTPUT_TOKENS


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', text))
    non_cjk = len(text) - cjk
    return int(cjk / 1.5 + non_cjk / 4.0)


def _truncate_to_token_budget(text: str, budget: int) -> str:
    if not text:
        return text
    estimated = _estimate_tokens(text)
    if estimated <= budget:
        return text
    ratio = budget / estimated
    target_chars = int(len(text) * ratio * 0.9)
    truncated = text[:target_chars]
    last_period = max(
        truncated.rfind('。'),
        truncated.rfind('；'),
        truncated.rfind('\n'),
    )
    if last_period > target_chars * 0.5:
        truncated = truncated[:last_period + 1]
    truncated += "\n\n[注：因令牌预算限制，此段内容已截断]"
    return truncated


class OrchestrationMode(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    PASSIVE = "passive"


class WorkflowOrchestrator:

    def __init__(self, max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS):
        self.max_context_tokens = max_context_tokens
        self.available_input_tokens = max_context_tokens - RESERVED_SYSTEM_TOKENS - RESERVED_OUTPUT_TOKENS
        self._runs: dict[str, WorkflowRun] = {}
        self._orchestration_mode: OrchestrationMode = OrchestrationMode.INTERNAL
        self._active_run_id: str | None = None
        self._detect_external_orchestrator()

    def _detect_external_orchestrator(self) -> None:
        env_mode = os.environ.get("ORCHESTRATOR_MODE", "").lower()
        if env_mode in ("external", "claude_code", "claude-code"):
            self._orchestration_mode = OrchestrationMode.EXTERNAL
            logger.info(
                "检测到外部编排器 (ORCHESTRATOR_MODE=%s)，MCP workflow自动降级为被动模式",
                env_mode,
            )
        elif env_mode in ("passive", "disabled"):
            self._orchestration_mode = OrchestrationMode.PASSIVE
            logger.info("MCP workflow已设为被动模式 (ORCHESTRATOR_MODE=%s)", env_mode)

    @property
    def orchestration_mode(self) -> OrchestrationMode:
        return self._orchestration_mode

    def set_orchestration_mode(self, mode: str) -> dict[str, str]:
        mode_lower = mode.lower()
        if mode_lower in ("internal", "internal"):
            self._orchestration_mode = OrchestrationMode.INTERNAL
            self._active_run_id = None
        elif mode_lower in ("external", "claude_code", "claude-code"):
            self._orchestration_mode = OrchestrationMode.EXTERNAL
        elif mode_lower in ("passive", "disabled"):
            self._orchestration_mode = OrchestrationMode.PASSIVE
        else:
            return {"error": f"无效编排模式: {mode}，可选: internal/external/passive"}

        logger.info("编排模式已切换为: %s", self._orchestration_mode.value)
        return {"success": True, "mode": self._orchestration_mode.value}

    def _check_workflow_allowed(self) -> str | None:
        if self._orchestration_mode == OrchestrationMode.PASSIVE:
            return "当前为被动模式，MCP workflow已禁用。请使用外部编排器（如Claude Code）调度检测任务。"
        if self._orchestration_mode == OrchestrationMode.EXTERNAL:
            if self._active_run_id and self._active_run_id in self._runs:
                run = self._runs[self._active_run_id]
                if run.status in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    return (
                        f"外部编排器模式下，已有活跃workflow ({self._active_run_id})，"
                        f"请等待完成或切换为internal模式后再创建新workflow。"
                    )
            self._active_run_id = None
        return None

    def create_workflow(
        self,
        document_name: str,
        document_sections: dict[str, str],
        evidence_manifest_path: str = "",
        vault_root: str = "",
    ) -> WorkflowRun:
        block_reason = self._check_workflow_allowed()
        if block_reason:
            raise RuntimeError(block_reason)

        run = WorkflowRun(
            document_name=document_name,
            start_time=time.time(),
            mode="dimension",
        )

        for stage_def in DETECTION_WORKFLOW_STAGES:
            sections_text = self._assemble_sections(stage_def["input_sections"], document_sections)
            token_budget = int(self.available_input_tokens * stage_def["token_budget_pct"])
            sections_text = _truncate_to_token_budget(sections_text, token_budget)

            task = SubTask(
                name=stage_def["name"],
                description=stage_def["description"],
                dimension_codes=stage_def["dimensions"],
                priority=stage_def["priority"],
                input_sections=stage_def["input_sections"],
                token_budget=token_budget,
                token_used=_estimate_tokens(sections_text),
                prompt_template=self._build_prompt_template(
                    stage_def, sections_text, evidence_manifest_path, vault_root,
                ),
                document_name=document_name,
            )
            run.tasks.append(task)

        run.total_tasks = len(run.tasks)
        run.total_tokens_budget = sum(t.token_budget for t in run.tasks)
        run.status = TaskStatus.PENDING
        self._runs[run.run_id] = run
        return run

    def create_multi_document_workflow(
        self,
        documents: list[dict[str, Any]],
        evidence_manifest_path: str = "",
        vault_root: str = "",
    ) -> list[WorkflowRun]:
        block_reason = self._check_workflow_allowed()
        if block_reason:
            raise RuntimeError(block_reason)

        runs = []
        for doc_info in documents:
            doc_name = doc_info.get("name", "unknown")
            doc_sections = doc_info.get("sections", {})
            run = WorkflowRun(
                document_name=doc_name,
                start_time=time.time(),
                mode="document",
            )

            for stage_def in DETECTION_WORKFLOW_STAGES:
                sections_text = self._assemble_sections(stage_def["input_sections"], doc_sections)
                token_budget = int(self.available_input_tokens * stage_def["token_budget_pct"])
                sections_text = _truncate_to_token_budget(sections_text, token_budget)

                task = SubTask(
                    name=f"[{doc_name}] {stage_def['name']}",
                    description=stage_def["description"],
                    dimension_codes=stage_def["dimensions"],
                    priority=stage_def["priority"],
                    input_sections=stage_def["input_sections"],
                    token_budget=token_budget,
                    token_used=_estimate_tokens(sections_text),
                    prompt_template=self._build_prompt_template(
                        stage_def, sections_text, evidence_manifest_path, vault_root,
                    ),
                    document_name=doc_name,
                )
                run.tasks.append(task)

            run.total_tasks = len(run.tasks)
            run.total_tokens_budget = sum(t.token_budget for t in run.tasks)
            run.status = TaskStatus.PENDING
            self._runs[run.run_id] = run
            runs.append(run)

        logger.info(
            "create_multi_document_workflow: %d documents → %d runs, mode=document",
            len(documents), len(runs),
        )
        return runs

    def create_dimension_workflow(
        self,
        document_name: str,
        document_sections: dict[str, str],
        evidence_manifest_path: str = "",
        vault_root: str = "",
    ) -> WorkflowRun:
        block_reason = self._check_workflow_allowed()
        if block_reason:
            raise RuntimeError(block_reason)

        run = WorkflowRun(
            document_name=document_name,
            start_time=time.time(),
            mode="dimension",
        )

        dimension_stages = [s for s in DETECTION_WORKFLOW_STAGES if s["dimensions"]]
        cross_stages = [s for s in DETECTION_WORKFLOW_STAGES if not s["dimensions"]]

        for stage_def in dimension_stages:
            sections_text = self._assemble_sections(stage_def["input_sections"], document_sections)
            token_budget = int(self.available_input_tokens * stage_def["token_budget_pct"])
            sections_text = _truncate_to_token_budget(sections_text, token_budget)

            task = SubTask(
                name=stage_def["name"],
                description=stage_def["description"],
                dimension_codes=stage_def["dimensions"],
                priority=stage_def["priority"],
                input_sections=stage_def["input_sections"],
                token_budget=token_budget,
                token_used=_estimate_tokens(sections_text),
                prompt_template=self._build_prompt_template(
                    stage_def, sections_text, evidence_manifest_path, vault_root,
                ),
                document_name=document_name,
            )
            run.tasks.append(task)

        for stage_def in cross_stages:
            sections_text = self._assemble_sections(stage_def["input_sections"], document_sections)
            token_budget = int(self.available_input_tokens * stage_def["token_budget_pct"])
            sections_text = _truncate_to_token_budget(sections_text, token_budget)

            task = SubTask(
                name=stage_def["name"],
                description=stage_def["description"],
                dimension_codes=stage_def["dimensions"],
                priority=stage_def["priority"],
                input_sections=stage_def["input_sections"],
                token_budget=token_budget,
                token_used=_estimate_tokens(sections_text),
                prompt_template=self._build_prompt_template(
                    stage_def, sections_text, evidence_manifest_path, vault_root,
                ),
                document_name=document_name,
            )
            run.tasks.append(task)

        run.total_tasks = len(run.tasks)
        run.total_tokens_budget = sum(t.token_budget for t in run.tasks)
        run.status = TaskStatus.PENDING
        self._runs[run.run_id] = run
        logger.info(
            "create_dimension_workflow: %s → %d tasks (dim=%d, cross=%d), mode=dimension",
            document_name, run.total_tasks, len(dimension_stages), len(cross_stages),
        )
        return run

    def get_workflow(self, run_id: str) -> WorkflowRun | None:
        return self._runs.get(run_id)

    def list_workflows(self) -> list[dict]:
        return [
            {
                "run_id": r.run_id,
                "document_name": r.document_name,
                "status": r.status.value,
                "progress_pct": r.progress_pct,
                "total_tasks": r.total_tasks,
                "completed_tasks": r.completed_tasks,
                "failed_tasks": r.failed_tasks,
            }
            for r in self._runs.values()
        ]

    def update_task_status(
        self,
        run_id: str,
        task_id: str,
        status: TaskStatus,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> SubTask | None:
        run = self._runs.get(run_id)
        if not run:
            return None

        for task in run.tasks:
            if task.task_id == task_id:
                task.status = status
                if status == TaskStatus.RUNNING and not task.start_time:
                    task.start_time = time.time()
                if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    task.end_time = time.time()
                if result is not None:
                    task.result = result
                if error:
                    task.error = error
                break

        run.completed_tasks = sum(1 for t in run.tasks if t.status == TaskStatus.COMPLETED)
        run.failed_tasks = sum(1 for t in run.tasks if t.status == TaskStatus.FAILED)
        run.total_tokens_used = sum(
            t.token_used for t in run.tasks
            if t.status in (TaskStatus.COMPLETED, TaskStatus.RUNNING)
        )

        milestone_logs = run.check_milestones()
        for log_msg in milestone_logs:
            logger.info(log_msg)

        all_done = all(t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) for t in run.tasks)
        if all_done:
            run.status = TaskStatus.COMPLETED if run.failed_tasks == 0 else TaskStatus.FAILED
            run.end_time = time.time()

        return None

    def get_parallel_tasks(self, run_id: str) -> list[dict]:
        run = self._runs.get(run_id)
        if not run:
            return []

        ready = []
        for task in run.tasks:
            if task.status != TaskStatus.PENDING:
                continue
            deps_met = all(
                any(t.task_id == dep and t.status == TaskStatus.COMPLETED for t in run.tasks)
                for dep in task.depends_on
            )
            if deps_met:
                ready.append({
                    "task_id": task.task_id,
                    "name": task.name,
                    "description": task.description,
                    "dimension_codes": task.dimension_codes,
                    "priority": task.priority.value,
                    "prompt_template": task.prompt_template,
                    "token_budget": task.token_budget,
                    "token_used": task.token_used,
                })

        return ready

    def get_workflow_status(self, run_id: str) -> dict:
        run = self._runs.get(run_id)
        if not run:
            return {"error": f"工作流 {run_id} 不存在"}

        task_statuses = []
        for task in run.tasks:
            task_statuses.append({
                "task_id": task.task_id,
                "name": task.name,
                "status": task.status.value,
                "priority": task.priority.value,
                "token_budget": task.token_budget,
                "token_used": task.token_used,
                "duration_ms": task.duration_ms,
                "error": task.error,
                "document_name": task.document_name,
            })

        status = {
            "run_id": run.run_id,
            "document_name": run.document_name,
            "status": run.status.value,
            "progress_pct": run.progress_pct,
            "total_tasks": run.total_tasks,
            "completed_tasks": run.completed_tasks,
            "failed_tasks": run.failed_tasks,
            "total_tokens_budget": run.total_tokens_budget,
            "total_tokens_used": run.total_tokens_used,
            "duration_ms": run.duration_ms,
            "mode": run.mode,
            "tasks": task_statuses,
        }

        if run.midpoint_milestone:
            status["midpoint_milestone"] = {
                "progress_pct": run.midpoint_milestone.progress_pct,
                "token_used": run.midpoint_milestone.token_used,
                "elapsed_ms": run.midpoint_milestone.elapsed_ms,
                "completed_tasks": run.midpoint_milestone.completed_tasks,
                "total_tasks": run.midpoint_milestone.total_tasks,
            }
        if run.final_milestone:
            status["final_milestone"] = {
                "progress_pct": run.final_milestone.progress_pct,
                "token_used": run.final_milestone.token_used,
                "elapsed_ms": run.final_milestone.elapsed_ms,
                "completed_tasks": run.final_milestone.completed_tasks,
                "total_tasks": run.final_milestone.total_tasks,
            }

        return status

    def aggregate_results(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if not run:
            return {"error": f"工作流 {run_id} 不存在"}

        aggregated = {
            "run_id": run.run_id,
            "document_name": run.document_name,
            "status": run.status.value,
            "total_tasks": run.total_tasks,
            "completed_tasks": run.completed_tasks,
            "failed_tasks": run.failed_tasks,
            "duration_ms": run.duration_ms,
            "total_tokens_used": run.total_tokens_used,
            "mode": run.mode,
            "dimensions": {},
            "all_flags": [],
            "summary": {},
            "resource_summary": {},
        }

        if run.midpoint_milestone:
            aggregated["midpoint_milestone"] = {
                "progress_pct": run.midpoint_milestone.progress_pct,
                "token_used": run.midpoint_milestone.token_used,
                "elapsed_sec": round(run.midpoint_milestone.elapsed_ms / 1000, 2),
                "completed_tasks": run.midpoint_milestone.completed_tasks,
                "total_tasks": run.midpoint_milestone.total_tasks,
            }
        if run.final_milestone:
            aggregated["final_milestone"] = {
                "progress_pct": run.final_milestone.progress_pct,
                "token_used": run.final_milestone.token_used,
                "elapsed_sec": round(run.final_milestone.elapsed_ms / 1000, 2),
                "completed_tasks": run.final_milestone.completed_tasks,
                "total_tasks": run.final_milestone.total_tasks,
            }

        for task in run.tasks:
            if not task.result:
                continue

            task_flags = task.result.get("flags", [])
            aggregated["all_flags"].extend(task_flags)

            for dim_code in task.dimension_codes:
                if dim_code not in aggregated["dimensions"]:
                    aggregated["dimensions"][dim_code] = {
                        "flags": [],
                        "score": 0.0,
                    }
                aggregated["dimensions"][dim_code]["flags"].extend(task_flags)
                dim_score = task.result.get("dimension_scores", {}).get(dim_code, 0)
                aggregated["dimensions"][dim_code]["score"] = max(
                    aggregated["dimensions"][dim_code]["score"], dim_score
                )

            stage_key = task.name
            aggregated["summary"][stage_key] = {
                "status": task.status.value,
                "flags_count": len(task_flags),
                "token_used": task.token_used,
                "duration_ms": task.duration_ms,
            }

        total_flags = len(aggregated["all_flags"])
        critical_flags = sum(
            1 for f in aggregated["all_flags"]
            if isinstance(f, dict) and f.get("severity") == "critical"
        )
        high_flags = sum(
            1 for f in aggregated["all_flags"]
            if isinstance(f, dict) and f.get("severity") == "high"
        )

        if critical_flags > 0:
            risk_grade = "F"
        elif high_flags > 3:
            risk_grade = "D"
        elif high_flags > 0:
            risk_grade = "C"
        elif total_flags > 5:
            risk_grade = "B"
        else:
            risk_grade = "A"

        aggregated["risk_grade"] = risk_grade
        aggregated["total_flags"] = total_flags
        aggregated["critical_flags"] = critical_flags
        aggregated["high_flags"] = high_flags

        total_duration_ms = sum(t.duration_ms for t in run.tasks if t.status == TaskStatus.COMPLETED)
        total_token_used = sum(t.token_used for t in run.tasks if t.status == TaskStatus.COMPLETED)
        aggregated["resource_summary"] = {
            "total_token_used": total_token_used,
            "total_duration_sec": round(total_duration_ms / 1000, 2),
            "avg_token_per_task": round(total_token_used / max(run.completed_tasks, 1), 1),
            "avg_duration_sec_per_task": round(total_duration_ms / max(run.completed_tasks, 1) / 1000, 2),
            "midpoint_token": run.midpoint_milestone.token_used if run.midpoint_milestone else 0,
            "midpoint_elapsed_sec": round(run.midpoint_milestone.elapsed_ms / 1000, 2) if run.midpoint_milestone else 0,
            "final_token": run.final_milestone.token_used if run.final_milestone else 0,
            "final_elapsed_sec": round(run.final_milestone.elapsed_ms / 1000, 2) if run.final_milestone else 0,
        }

        logger.info(
            "aggregate_results: %s | Token: %d | 耗时: %.2fs | 50%%Token: %d | 50%%耗时: %.2fs",
            run.document_name,
            total_token_used,
            total_duration_ms / 1000,
            run.midpoint_milestone.token_used if run.midpoint_milestone else 0,
            run.midpoint_milestone.elapsed_ms / 1000 if run.midpoint_milestone else 0,
        )

        return aggregated

    def aggregate_multi_workflow_results(self) -> dict[str, Any]:
        all_run_ids = list(self._runs.keys())
        if not all_run_ids:
            return {"error": "无可用工作流"}

        per_doc = []
        total_token = 0
        total_flags = 0
        total_duration_sec = 0.0

        for run_id in all_run_ids:
            agg = self.aggregate_results(run_id)
            if "error" in agg:
                continue
            res = agg.get("resource_summary", {})
            total_token += res.get("total_token_used", 0)
            total_flags += agg.get("total_flags", 0)
            total_duration_sec += res.get("total_duration_sec", 0)

            per_doc.append({
                "document_name": agg["document_name"],
                "status": agg["status"],
                "total_flags": agg.get("total_flags", 0),
                "risk_grade": agg.get("risk_grade", "N/A"),
                "midpoint_token": res.get("midpoint_token", 0),
                "midpoint_elapsed_sec": res.get("midpoint_elapsed_sec", 0),
                "final_token": res.get("final_token", 0),
                "final_elapsed_sec": res.get("final_elapsed_sec", 0),
                "total_token_used": res.get("total_token_used", 0),
                "total_duration_sec": res.get("total_duration_sec", 0),
            })

        summary = {
            "total_documents": len(per_doc),
            "total_flags": total_flags,
            "total_token_used": total_token,
            "total_duration_sec": round(total_duration_sec, 2),
            "avg_token_per_doc": round(total_token / max(len(per_doc), 1), 1),
            "avg_duration_sec_per_doc": round(total_duration_sec / max(len(per_doc), 1), 2),
            "per_document": per_doc,
        }

        logger.info(
            "aggregate_multi_workflow_results: %d docs | Total Token: %d | Total 耗时: %.2fs | Total Flags: %d",
            len(per_doc), total_token, total_duration_sec, total_flags,
        )
        return summary

    def generate_status_table(self) -> str:
        lines = []
        lines.append(
            "| 文档 | 模式 | 状态 | 进度 | Token估算 | "
            "50%里程碑Token | 50%里程碑耗时(s) | "
            "100%里程碑Token | 100%里程碑耗时(s) | 总耗时(s) | Flag数 |"
        )
        lines.append(
            "|------|------|------|------|----------|"
            "---------------|-----------------|"
            "----------------|------------------|---------|--------|"
        )

        for run in self._runs.values():
            mode_label = "材料" if run.mode == "document" else "维度"
            _status_icons = {
                "pending": "⏳", "running": "🔄",
                "completed": "✅", "failed": "❌",
            }
            status_icon = _status_icons.get(run.status.value, "❓")

            mid_token = run.midpoint_milestone.token_used if run.midpoint_milestone else "—"
            mid_elapsed = f"{run.midpoint_milestone.elapsed_ms/1000:.2f}" if run.midpoint_milestone else "—"
            fin_token = run.final_milestone.token_used if run.final_milestone else "—"
            fin_elapsed = f"{run.final_milestone.elapsed_ms/1000:.2f}" if run.final_milestone else "—"
            total_elapsed = f"{run.duration_ms/1000:.2f}" if run.duration_ms > 0 else "—"

            flag_count = sum(
                len(t.result.get("flags", [])) if t.result else 0
                for t in run.tasks
            )

            lines.append(
                f"| {run.document_name} | {mode_label} | {status_icon} {run.status.value} | "
                f"{run.progress_pct:.0f}% | {run.total_tokens_used} | {mid_token} | {mid_elapsed} | "
                f"{fin_token} | {fin_elapsed} | {total_elapsed} | {flag_count} |"
            )

        return "\n".join(lines)

    def _assemble_sections(self, section_keys: list[str], sections: dict[str, str]) -> str:
        parts = []
        for key in section_keys:
            if key == "full_text":
                parts.append(sections.get("full_text", ""))
            elif key in sections:
                content = sections[key]
                if isinstance(content, str) and len(content) > 10:
                    parts.append(f"## {key}\n\n{content}")
                elif isinstance(content, dict) and content:
                    parts.append(f"## {key}\n\n{json.dumps(content, ensure_ascii=False, indent=2)}")
        return "\n\n".join(parts)

    def _build_prompt_template(
        self,
        stage_def: dict,
        sections_text: str,
        evidence_manifest_path: str,
        vault_root: str,
    ) -> str:
        stage = stage_def["stage"]
        name = stage_def["name"]
        description = stage_def["description"]
        dimensions = stage_def["dimensions"]

        dim_instructions = ""
        if dimensions:
            dim_parts = []
            for d in dimensions:
                dim_parts.append(f"- {d.upper()}: {description}")
            dim_instructions = "\n".join(dim_parts)
        else:
            dim_instructions = f"- 检测目标: {description}"

        manifest_hint = ""
        if evidence_manifest_path:
            manifest_hint = f"\n证据索引清单路径: {evidence_manifest_path}\n请核对所有引注是否在该清单中。"
        if vault_root:
            manifest_hint += f"\n工作区根目录: {vault_root}"

        template = f"""# {name}

## 检测任务

{dim_instructions}
{manifest_hint}

## 待检测文书内容

{sections_text}

## 检测要求

1. 逐句扫描上述文书内容，识别所有可能的幻觉
2. 对每个幻觉，记录：原文、位置、严重程度（critical/high/medium/low）、幻觉类型、修正建议
3. 严格按照封闭宇宙规则：任何事实陈述必须有证据支撑，任何法条引用必须可验证
4. 返回JSON格式的检测结果

## 输出格式

```json
{{
  "flags": [
    {{
      "original_text": "原文",
      "location": "位置描述",
      "severity": "critical/high/medium/low",
      "hallucination_type": "{stage}",
      "suggestion": "修正建议"
    }}
  ],
  "dimension_scores": {{
    "{dimensions[0] if dimensions else stage}": 0.0
  }},
  "summary": "本阶段检测概述"
}}
```
"""
        return template
