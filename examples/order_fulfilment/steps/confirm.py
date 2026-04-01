"""SendConfirmationStep — sends order confirmation email."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from workchain import step


class ConfirmationConfig(BaseModel):
    from_email: str = "orders@example.com"


@step(name="send_confirmation")
async def send_confirmation(config: dict, context: dict) -> dict:
    """Send an order confirmation email with tracking details."""
    cfg = ConfirmationConfig(**config)
    order = context.get("order", {})
    order_id = order.get("order_id", "unknown")
    email = order.get("customer_email", "customer@example.com")

    charge_id = context.get("charge_id", "N/A")
    shipment_id = context.get("shipment_id", "N/A")

    print(f"  [confirm] Sending confirmation for order {order_id}")
    print(f"  [confirm]   To: {email}, From: {cfg.from_email}")
    print(f"  [confirm]   Charge: {charge_id}, Shipment: {shipment_id}")

    return {
        "email_sent": True,
        "recipient": email,
        "order_id": order_id,
        "sent_at": datetime.now(UTC).isoformat(),
    }
