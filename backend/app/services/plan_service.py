import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.db.models import AnalysisPlan, AnalysisTask, now_utc


PLAN_NOT_FOUND = "PLAN_NOT_FOUND"
PLAN_INVALID_STATE = "PLAN_INVALID_STATE"

TASK_STATUS_PLANNING = "planning"
TASK_STATUS_AWAITING_APPROVAL = "awaiting_approval"
TASK_STATUS_EXECUTING = "executing"
TASK_STATUS_ERROR = "error"

PLAN_STATUS_AWAITING_APPROVAL = "awaiting_approval"
PLAN_STATUS_APPROVED = "approved"
PLAN_STATUS_REJECTED = "rejected"


class PlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_task(self, *, chat_id: str | None, user_message: str) -> AnalysisTask:
        task = AnalysisTask(
            chat_id=chat_id,
            user_message=user_message,
            status=TASK_STATUS_PLANNING,
        )
        self.db.add(task)
        self.db.flush()
        return task

    def create_plan(
        self,
        *,
        task: AnalysisTask,
        version: int,
        status: str,
        plan_markdown: str,
        prompt_key: str,
        prompt_version_hash: str,
        model_profile_id: str | None,
        user_feedback: str | None,
        metadata: dict[str, Any],
    ) -> AnalysisPlan:
        plan = AnalysisPlan(
            task_id=task.id,
            version=version,
            status=status,
            plan_markdown=plan_markdown,
            plan_json=json.dumps(metadata, ensure_ascii=False),
            prompt_key=prompt_key,
            prompt_version_hash=prompt_version_hash,
            model_profile_id=model_profile_id,
            user_feedback=user_feedback,
        )
        self.db.add(plan)
        self.db.flush()
        return plan

    def mark_task_awaiting_approval(self, *, task: AnalysisTask, plan: AnalysisPlan) -> None:
        task.status = TASK_STATUS_AWAITING_APPROVAL
        task.current_plan_id = plan.id
        task.updated_at = now_utc()

    def approve_plan(self, plan_id: str) -> AnalysisPlan:
        plan = self.get_plan(plan_id)
        if plan.status != PLAN_STATUS_AWAITING_APPROVAL:
            raise AppError(
                PLAN_INVALID_STATE,
                "Only an awaiting-approval plan can be approved.",
                status_code=409,
                details={"plan_id": plan_id, "status": plan.status},
            )

        task = self.get_task(plan.task_id)
        plan.status = PLAN_STATUS_APPROVED
        plan.approved_at = now_utc()
        task.status = TASK_STATUS_EXECUTING
        task.current_plan_id = plan.id
        task.updated_at = now_utc()
        self.db.flush()
        return plan

    def reject_plan_for_revision(self, plan_id: str, user_feedback: str) -> tuple[AnalysisPlan, AnalysisTask]:
        plan = self.get_plan(plan_id)
        if plan.status != PLAN_STATUS_AWAITING_APPROVAL:
            raise AppError(
                PLAN_INVALID_STATE,
                "Only an awaiting-approval plan can be revised.",
                status_code=409,
                details={"plan_id": plan_id, "status": plan.status},
            )

        task = self.get_task(plan.task_id)
        plan.status = PLAN_STATUS_REJECTED
        plan.rejected_at = now_utc()
        plan.user_feedback = user_feedback
        task.status = TASK_STATUS_PLANNING
        task.updated_at = now_utc()
        self.db.flush()
        return plan, task

    def get_plan(self, plan_id: str) -> AnalysisPlan:
        plan = self.db.get(AnalysisPlan, plan_id)
        if plan is None:
            raise AppError(
                PLAN_NOT_FOUND,
                "Analysis plan was not found.",
                status_code=404,
                details={"plan_id": plan_id},
            )
        return plan

    def get_task(self, task_id: str) -> AnalysisTask:
        task = self.db.get(AnalysisTask, task_id)
        if task is None:
            raise AppError("TASK_NOT_FOUND", "Analysis task was not found.", status_code=404)
        return task

    def mark_task_error(self, task: AnalysisTask | None) -> None:
        if task is None:
            return
        task.status = TASK_STATUS_ERROR
        task.updated_at = now_utc()
        self.db.flush()

