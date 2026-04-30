from __future__ import annotations

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from app.temporal.activities import run_session_graph_activity
    from app.temporal.models import SessionWorkflowInput
    from app.temporal.workflow_limits import (
        ACTIVITY_HEARTBEAT_TIMEOUT,
        ACTIVITY_MAXIMUM_ATTEMPTS,
        ACTIVITY_START_TO_CLOSE_TIMEOUT,
    )


@workflow.defn
class SessionRunWorkflow:
    @workflow.run
    async def run(self, req: SessionWorkflowInput) -> dict[str, str]:
        await workflow.execute_activity(
            run_session_graph_activity,
            req,
            start_to_close_timeout=ACTIVITY_START_TO_CLOSE_TIMEOUT,
            heartbeat_timeout=ACTIVITY_HEARTBEAT_TIMEOUT,
            retry_policy=RetryPolicy(
                maximum_attempts=ACTIVITY_MAXIMUM_ATTEMPTS,
            ),
        )
        return {"status": "completed", "run_id": req.run_id}

