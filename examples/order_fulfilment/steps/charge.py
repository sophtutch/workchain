"""ChargePaymentStep -- EventStep that suspends for payment webhook."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from examples.order_fulfilment.logging_config import step_log
from workchain import Context, EventStep, StepResult


class ChargePaymentConfig(BaseModel):
    """Configuration for ChargePaymentStep."""

    payment_provider: str = "stripe"
    currency: str = "USD"


class ChargePaymentStep(EventStep[ChargePaymentConfig]):
    """
    Initiates a payment charge and suspends until the payment gateway
    calls back via webhook.

    On execute():
        - Creates a payment intent with a unique correlation_id
        - Returns StepResult.suspend() so the workflow pauses

    On resume (webhook arrives):
        - Validates the payment result from the webhook payload
        - Writes charge details into context

    The correlation_id format is ``payment-<order_id>-<timestamp>``
    which the webhook endpoint uses to route the callback.
    """

    Config = ChargePaymentConfig

    def execute(self, context: Context) -> StepResult:
        order = context.get("order", {})
        order_id = order.get("order_id", "unknown")
        validated = context.step_output("validate")
        total = validated.get("total", 0)
        provider = self.config.payment_provider if self.config else "stripe"

        correlation_id = f"payment-{order_id}-{datetime.now(UTC).timestamp():.0f}"

        step_log("charge", f"Initiating {provider} charge of ${total:.2f} for order {order_id}")
        step_log("charge", "Payment intent created -- awaiting webhook callback")
        step_log("charge", f"Correlation ID: {correlation_id}")
        step_log("charge", "PAUSED -- Workflow SUSPENDED, waiting for payment gateway webhook")

        context.set("payment_correlation_id", correlation_id)

        return StepResult.suspend(correlation_id=correlation_id)

    def on_resume(self, payload: dict[str, Any], context: Context) -> None:
        """Process the payment webhook callback."""
        success = payload.get("success", False)
        charge_id = payload.get("charge_id", "unknown")
        provider_ref = payload.get("provider_ref", "")

        if not success:
            error = payload.get("error", "Payment declined")
            step_log("charge", f"FAILED -- Payment FAILED: {error}")
            context.set("payment_result", {"success": False, "error": error})
            return

        step_log("charge", f"OK -- Payment SUCCEEDED, charge_id: {charge_id}")
        if provider_ref:
            step_log("charge", f"  Provider ref: {provider_ref}")

        context.set(
            "payment_result",
            {
                "success": True,
                "charge_id": charge_id,
                "provider_ref": provider_ref,
                "charged_at": datetime.now(UTC).isoformat(),
            },
        )
