"""Custom step definitions for the document approval workflow example."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from workchain import Context, EventStep, PollingStep, Step, StepResult

# ============================================================================
# Configuration Models
# ============================================================================


class FetchDocumentConfig(BaseModel):
    """Configuration for FetchDocumentStep."""

    document_id: str
    source_url: str = "https://api.example.com/documents"


class ProcessJobConfig(BaseModel):
    """Configuration for ProcessJobStep."""

    job_type: str = "document_processing"
    max_checks: int = 3


class SendNotificationConfig(BaseModel):
    """Configuration for SendNotificationStep."""

    recipient_email: str


# ============================================================================
# Step 1: FetchDocumentStep — Standard synchronous step
# ============================================================================


class FetchDocumentStep(Step[FetchDocumentConfig]):
    """
    Fetches a document from a remote source.

    This is a standard Step that executes synchronously and returns immediately.
    It demonstrates:
    - Reading config passed at workflow build time
    - Returning structured output stored in context
    - Output accessible to downstream steps via context.step_output("fetch")

    In a real scenario, this would call an API. For demo purposes, we simulate
    a fetch with mock data.
    """

    Config = FetchDocumentConfig

    def execute(self, context: Context) -> StepResult:
        document_id = self.config.document_id
        source_url = self.config.source_url

        # Simulate fetching from remote API
        document_data = {
            "id": document_id,
            "title": f"Document {document_id}",
            "content": "Lorem ipsum dolor sit amet...",
            "author": "Alice Smith",
            "created_at": datetime.now(UTC).isoformat(),
            "pages": 5,
        }

        print(f"  [fetch] Fetched document '{document_id}' from {source_url}")
        return StepResult.complete(output=document_data)


# ============================================================================
# Step 2: ApprovalStep — EventStep (suspend/resume with external signal)
# ============================================================================


class ApprovalStep(EventStep):
    """
    Waits for human approval of the document.

    This is an EventStep that suspends the workflow until an external signal
    arrives. It demonstrates:
    - Returning StepResult.suspend(correlation_id) to pause workflow
    - Storing a unique correlation_id in the persisted WorkflowRun
    - Implementing on_resume() callback to process the approval decision

    An external actor (human via web UI, automated service, etc.) calls
    runner.resume(correlation_id="...", payload={"approved": true/false})
    """

    def execute(self, context: Context) -> StepResult:
        document = context.step_output("fetch")
        correlation_id = f"approval-{document['id']}-{datetime.now(UTC).timestamp():.0f}"

        print(f"  [approve] Suspended, awaiting approval (correlation_id: {correlation_id})")
        return StepResult.suspend(correlation_id=correlation_id)

    def on_resume(self, payload: dict[str, Any], context: Context) -> dict[str, Any]:
        approved = payload.get("approved", False)
        approver = payload.get("approver", "Unknown")
        notes = payload.get("notes", "")

        decision = {
            "approved": approved,
            "approver": approver,
            "notes": notes,
            "approved_at": datetime.now(UTC).isoformat(),
        }
        context.set("approval_decision", decision)

        status = "APPROVED" if approved else "REJECTED"
        print(f"  [approve] {status} by {approver}")
        if notes:
            print(f"            Notes: {notes}")

        return decision


# ============================================================================
# Step 3: ProcessJobStep — PollingStep (async job + periodic checks)
# ============================================================================


class ProcessJobStep(PollingStep[ProcessJobConfig]):
    """
    Simulates starting an async processing job and polling until completion.

    This is a PollingStep that demonstrates:
    - execute() kicks off the job and returns StepResult.poll(next_poll_at)
    - check() is called periodically to see if the job is done
    - on_complete() returns the final output when check() returns True
    - Timeout handling (if configured)

    The runner manages the polling schedule automatically.
    """

    Config = ProcessJobConfig
    poll_interval_seconds = 1
    timeout_seconds = 30

    def __init__(self, config: ProcessJobConfig | None = None) -> None:
        super().__init__(config=config)
        self._check_count = 0

    def execute(self, context: Context) -> StepResult:
        job_id = f"job-{datetime.now(UTC).timestamp():.0f}"
        context.set("job_id", job_id)
        print(f"  [process] Started async job (job_id: {job_id}), polling for completion...")
        return super().execute(context)

    def check(self, context: Context) -> bool:
        self._check_count += 1
        max_checks = self.config.max_checks if self.config else 3
        done = self._check_count >= max_checks
        job_id = context.get("job_id", "unknown")
        if done:
            print(f"  [process] Job {job_id} completed after {self._check_count} checks")
        else:
            print(f"  [process] Job {job_id} still running (check {self._check_count}/{max_checks})")
        return done

    def on_complete(self, context: Context) -> dict[str, Any]:
        return {
            "job_id": context.get("job_id", "unknown"),
            "status": "completed",
            "result": "Document processed successfully",
            "checks_required": self._check_count,
            "completed_at": datetime.now(UTC).isoformat(),
        }


# ============================================================================
# Step 4: SendNotificationStep — Standard synchronous step
# ============================================================================


class SendNotificationStep(Step[SendNotificationConfig]):
    """
    Sends a notification email with results.

    Demonstrates reading results from upstream steps via context.
    """

    Config = SendNotificationConfig

    def execute(self, context: Context) -> StepResult:
        recipient = self.config.recipient_email

        approval = context.get("approval_decision", {})
        job_id = context.get("job_id", "unknown")

        approved = approval.get("approved", False)
        if not approved:
            print("  [notify] Skipping notification: document was rejected")
            return StepResult.complete(
                output={
                    "sent": False,
                    "reason": "Document rejected in approval step",
                }
            )

        subject = "Document Processing Complete"
        print(f"  [notify] Sent email to {recipient}")
        print(f"           Subject: {subject}")
        print(f"           Approver: {approval.get('approver', 'N/A')}, Job: {job_id}")

        return StepResult.complete(
            output={
                "sent": True,
                "recipient": recipient,
                "subject": subject,
                "sent_at": datetime.now(UTC).isoformat(),
            }
        )
