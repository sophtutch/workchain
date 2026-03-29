"""ValidateOrderStep -- synchronous order validation."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from examples.order_fulfilment.logging_config import step_log
from workchain import Context, Step, StepResult


class ValidateOrderConfig(BaseModel):
    """Configuration for ValidateOrderStep."""

    allowed_regions: list[str] = ["US", "EU", "UK", "AU"]


class ValidateOrderStep(Step[ValidateOrderConfig]):
    """
    Validates incoming order data.

    Checks that items exist, quantities are positive, and the shipping
    region is in the allowed list. Writes the validated order into context
    for downstream steps.
    """

    Config = ValidateOrderConfig

    def execute(self, context: Context) -> StepResult:
        order = context.get("order")
        if not order:
            return StepResult.fail(error="No order data in context")

        step_log("validate", f"Validating order {order.get('order_id', '?')}...")

        items = order.get("items", [])
        if not items:
            return StepResult.fail(error="Order has no items")

        for item in items:
            if item.get("quantity", 0) <= 0:
                return StepResult.fail(error=f"Invalid quantity for SKU {item.get('sku')}")

        region = order.get("shipping_region", "")
        allowed = self.config.allowed_regions if self.config else ["US", "EU", "UK", "AU"]
        if region not in allowed:
            return StepResult.fail(error=f"Shipping region '{region}' not supported")

        total = sum(item["price"] * item["quantity"] for item in items)

        step_log("validate", f"Order valid -- {len(items)} item(s), total ${total:.2f}, region {region}")

        return StepResult.complete(
            output={
                "order_id": order["order_id"],
                "item_count": len(items),
                "total": total,
                "validated_at": datetime.now(UTC).isoformat(),
            }
        )
