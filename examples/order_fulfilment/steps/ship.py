"""ShipOrderStep — async step that polls for tracking number."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from workchain import PollPolicy, async_step

# Simulated shipping state
_ship_polls: dict[str, int] = {}


class ShipOrderConfig(BaseModel):
    carrier: str = "fedex"
    max_checks: int = 3


class ShipmentResult(BaseModel):
    shipment_id: str
    carrier: str


async def check_shipment(config: dict, context: dict, result: dict) -> dict:
    """
    Poll the carrier for tracking number assignment.

    Simulates tracking number appearing after max_checks polls.
    """
    cfg = ShipOrderConfig(**config)
    res = ShipmentResult(**result)
    _ship_polls[res.shipment_id] = _ship_polls.get(res.shipment_id, 0) + 1
    count = _ship_polls[res.shipment_id]

    if count >= cfg.max_checks:
        tracking = f"TRK-{uuid.uuid4().hex[:10].upper()}"
        print(f"  [ship] Tracking assigned: {tracking} (poll {count})")
        return {"complete": True, "progress": 1.0, "message": f"Tracking: {tracking}"}

    print(f"  [ship] Shipment {res.shipment_id} processing (poll {count}/{cfg.max_checks})")
    return {"complete": False, "progress": count / cfg.max_checks}


@async_step(
    name="ship_order",
    completeness_check=check_shipment,
    poll=PollPolicy(interval=2.0, backoff_multiplier=1.5, max_interval=10.0, timeout=120.0),
)
async def ship_order(config: dict, context: dict) -> dict:
    """Submit a shipment request to the carrier."""
    cfg = ShipOrderConfig(**config)
    shipment_id = f"SHIP-{uuid.uuid4().hex[:8].upper()}"

    print(f"  [ship] Shipment submitted to {cfg.carrier}: {shipment_id}")
    return {"shipment_id": shipment_id, "carrier": cfg.carrier}
