"""ChargePaymentStep — async step that polls for payment confirmation."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from workchain import PollPolicy, async_step

# Simulated payment state
_charge_polls: dict[str, int] = {}


class ChargePaymentConfig(BaseModel):
    payment_provider: str = "stripe"


class ChargeResult(BaseModel):
    charge_id: str
    provider: str


async def check_payment(config: dict, context: dict, result: dict) -> dict:
    """
    Poll the payment provider for charge status.

    Simulates payment confirmation after 2 polls. In production this would
    query Stripe/Adyen/etc. for the charge status.
    """
    res = ChargeResult(**result)
    _charge_polls[res.charge_id] = _charge_polls.get(res.charge_id, 0) + 1
    count = _charge_polls[res.charge_id]

    if count >= 2:
        print(f"  [charge] Payment {res.charge_id} CONFIRMED (poll {count})")
        return {"complete": True, "progress": 1.0, "message": "Payment confirmed"}

    print(f"  [charge] Payment {res.charge_id} pending (poll {count}/2)")
    return {"complete": False, "progress": count / 2.0, "message": "Processing"}


@async_step(
    name="charge_payment",
    completeness_check=check_payment,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=60.0),
)
async def charge_payment(config: dict, context: dict) -> dict:
    """Initiate a payment charge. The engine polls check_payment until confirmed."""
    cfg = ChargePaymentConfig(**config)
    order = context.get("order", {})
    order_id = order.get("order_id", "unknown")
    total = context.get("total", 0)

    charge_id = f"ch_{order_id}_{datetime.now(UTC).timestamp():.0f}"
    print(f"  [charge] Initiated {cfg.payment_provider} charge of ${total:.2f} for {order_id}")
    print(f"  [charge] Charge ID: {charge_id}")

    return {"charge_id": charge_id, "provider": cfg.payment_provider}
