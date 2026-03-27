"""SendConfirmationStep -- sends order confirmation email."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from examples.order_fulfilment.logging_config import step_log
from workchain import Context, Step, StepResult


class SendConfirmationConfig(BaseModel):
    """Configuration for SendConfirmationStep."""

    from_email: str = "orders@example.com"


class SendConfirmationStep(Step[SendConfirmationConfig]):
    """
    Sends an order confirmation email with tracking details.

    Reads upstream step outputs from context to assemble the email.
    Uses ``on_dependency_failure=SKIP`` in the workflow so this step
    runs even if shipping had issues -- the customer still gets notified.
    """

    Config = SendConfirmationConfig

    def execute(self, context: Context) -> StepResult:
        order = context.get("order", {})
        order_id = order.get("order_id", "unknown")
        email = order.get("customer_email", "customer@example.com")
        from_email = self.config.from_email if self.config else "orders@example.com"

        tracking = context.get("tracking_number", "pending")
        payment = context.get("payment_result", {})
        charge_id = payment.get("charge_id", "N/A")

        step_log("confirm", f"Sending confirmation for order {order_id}")
        step_log("confirm", f"  To: {email}")
        step_log("confirm", f"  From: {from_email}")
        step_log("confirm", f"  Tracking: {tracking}")
        step_log("confirm", f"  Charge: {charge_id}")

        return StepResult.complete(
            output={
                "email_sent": True,
                "recipient": email,
                "order_id": order_id,
                "tracking_number": tracking,
                "sent_at": datetime.now(UTC).isoformat(),
            }
        )
