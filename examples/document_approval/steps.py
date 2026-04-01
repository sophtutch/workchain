"""Step handlers for the document approval workflow.

Demonstrates sync steps (@step) and async steps (@async_step) with
completeness_check polling.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from workchain import PollPolicy, RetryPolicy, async_step, step

# ============================================================================
# Simulated external state (in production, these would be real API calls)
# ============================================================================

# Tracks approval poll counts to simulate eventual approval
_approval_polls: dict[str, int] = {}


# ============================================================================
# Config / context models
# ============================================================================


class FetchDocumentConfig(BaseModel):
    document_id: str = "DOC-001"
    source_url: str = "https://api.example.com/documents"


class NotificationConfig(BaseModel):
    recipient_email: str = "user@example.com"


class DocumentContext(BaseModel):
    """Shape of context after fetch_document merges its result."""
    document: dict[str, Any] = {}


class ApprovalResult(BaseModel):
    """Shape of the result dict returned by request_approval."""
    request_id: str
    document_id: str


# ============================================================================
# Step 1: Fetch document (sync)
# ============================================================================


@step(name="fetch_document")
async def fetch_document(config: dict, context: dict) -> dict:
    """Fetch a document from a remote source."""
    cfg = FetchDocumentConfig(**config)

    # Simulate fetching from remote API
    document = {
        "id": cfg.document_id,
        "title": f"Document {cfg.document_id}",
        "content": "Lorem ipsum dolor sit amet...",
        "author": "Alice Smith",
        "created_at": datetime.now(UTC).isoformat(),
        "pages": 5,
    }

    print(f"  [fetch] Fetched document '{cfg.document_id}' from {cfg.source_url}")
    return {"document": document}


# ============================================================================
# Step 2: Request approval (async — polls until approved)
# ============================================================================


async def check_approval_status(config: dict, context: dict, result: dict) -> dict:
    """
    Completeness check — polls an approval system.

    Simulates approval arriving after 3 polls. In production this would
    query an approval service or message queue.
    """
    res = ApprovalResult(**result)
    _approval_polls[res.request_id] = _approval_polls.get(res.request_id, 0) + 1
    count = _approval_polls[res.request_id]

    if count >= 3:
        print(f"  [approve] Approval GRANTED for {res.request_id} (poll {count})")
        return {
            "complete": True,
            "progress": 1.0,
            "message": "Approved",
        }

    print(f"  [approve] Awaiting approval for {res.request_id} (poll {count}/3)")
    return {
        "complete": False,
        "progress": count / 3.0,
        "message": f"Pending review ({count}/3)",
    }


@async_step(
    name="request_approval",
    completeness_check=check_approval_status,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=60.0),
)
async def request_approval(config: dict, context: dict) -> dict:
    """
    Submit an approval request and return immediately.

    The engine will poll check_approval_status until approval is granted.
    """
    ctx = DocumentContext(**context)
    doc_id = ctx.document.get("id", "unknown")
    request_id = f"approval-{doc_id}-{datetime.now(UTC).timestamp():.0f}"

    print(f"  [approve] Submitted approval request: {request_id}")
    return {"request_id": request_id, "document_id": doc_id}


# ============================================================================
# Step 3: Process document (sync)
# ============================================================================


@step(name="process_document", retry=RetryPolicy(max_attempts=3))
async def process_document(config: dict, context: dict) -> dict:
    """Process the approved document."""
    ctx = DocumentContext(**context)
    doc_id = ctx.document.get("id", "unknown")

    print(f"  [process] Processing document {doc_id}...")
    return {
        "processed": True,
        "result": "Document processed successfully",
        "processed_at": datetime.now(UTC).isoformat(),
    }


# ============================================================================
# Step 4: Send notification (sync)
# ============================================================================


@step(name="send_notification")
async def send_notification(config: dict, context: dict) -> dict:
    """Send a notification email with the processing results."""
    cfg = NotificationConfig(**config)
    ctx = DocumentContext(**context)
    doc_id = ctx.document.get("id", "unknown")

    print(f"  [notify] Sent notification to {cfg.recipient_email}")
    print(f"           Document: {doc_id}, Status: processed")

    return {
        "sent": True,
        "recipient": cfg.recipient_email,
        "sent_at": datetime.now(UTC).isoformat(),
    }
