"""ReserveInventoryStep — synchronous stock reservation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from workchain import step


class ReserveInventoryConfig(BaseModel):
    warehouse: str = "warehouse-east-1"


@step(name="reserve_inventory")
async def reserve_inventory(config: dict, context: dict) -> dict:
    """Reserve inventory for each line item in the order."""
    cfg = ReserveInventoryConfig(**config)
    order = context.get("order", {})
    items = order.get("items", [])

    print(f"  [reserve] Reserving {len(items)} item(s) at {cfg.warehouse}...")

    reservations = {}
    for item in items:
        res_id = f"RES-{uuid.uuid4().hex[:8].upper()}"
        reservations[item["sku"]] = {
            "reservation_id": res_id,
            "quantity": item["quantity"],
            "warehouse": cfg.warehouse,
        }
        print(f"  [reserve]   {item['sku']} x {item['quantity']} -> {res_id}")

    print(f"  [reserve] All {len(items)} item(s) reserved")
    return {
        "reservations": reservations,
        "reserved_at": datetime.now(UTC).isoformat(),
    }
