from legal_hallucination_mcp.workflow_orchestrator import WorkflowOrchestrator

wo = WorkflowOrchestrator()
wo.set_orchestration_mode("passive")
try:
    wo.create_workflow("test", {"full_text": "test"})
    print("ERROR: should have been blocked")
except RuntimeError as e:
    print("Blocked as expected:", e)

wo.set_orchestration_mode("internal")
try:
    run = wo.create_workflow("test", {"full_text": "test content"})
    print("Created workflow OK:", run.run_id)
except RuntimeError as e:
    print("ERROR: should not have been blocked:", e)
