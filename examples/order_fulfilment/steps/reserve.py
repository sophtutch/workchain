"""ReserveInventoryStep -- synchronous stock reservation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from examples.order_fulfilment.logging_config import step_log
from workchain import Context, Step, StepResult


class ReserveInventoryConfig(BaseModel):
    """Configuration for ReserveInventoryStep."""

    warehouse: str = "warehouse-east-1"


class ReserveInventoryStep(Step[ReserveInventoryConfig]):
    """
    Reserves inventory for each line item in the order.

    Simulates a warehouse API call with a short delay. Produces
    reservation IDs for each SKU.
    """

    Config = ReserveInventoryConfig

    def execute(self, context: Context) -> StepResult:
        order = context.get("order", {})
        items = order.get("items", [])
        warehouse = self.config.warehouse if self.config else "warehouse-east-1"

        step_log("reserve", f"Reserving {len(items)} item(s) at {warehouse}...")

        # Simulate warehouse API latency
        import time

        time.sleep(0.8)

        reservations = {}
        for item in items:
            res_id = f"RES-{uuid.uuid4().hex[:8].upper()}"
            reservations[item["sku"]] = {
                "reservation_id": res_id,
                "quantity": item["quantity"],
                "warehouse": warehouse,
            }
            step_log("reserve", f"  OK {item['sku']} x {item['quantity']} -> {res_id}")

        step_log("reserve", f"All {len(items)} item(s) reserved")

        return StepResult.complete(
            output={
                "reservations": reservations,
                "reserved_at": datetime.now(UTC).isoformat(),
            }
        )
