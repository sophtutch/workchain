"""ShipOrderStep -- PollingStep that polls for tracking number."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from examples.order_fulfilment.logging_config import step_log
from workchain import Context, PollingStep, StepResult


class ShipOrderConfig(BaseModel):
    """Configuration for ShipOrderStep."""

    carrier: str = "fedex"
    max_checks: int = 4


class ShipOrderStep(PollingStep[ShipOrderConfig]):
    """
    Submits a shipment request and polls until the carrier assigns
    a tracking number.

    Simulates a shipping API where the carrier takes several poll
    cycles to process and assign tracking. Each check() call
    represents querying the carrier's API for status.
    """

    Config = ShipOrderConfig
    poll_interval_seconds = 2
    timeout_seconds = 60

    def __init__(self, config: ShipOrderConfig | None = None) -> None:
        super().__init__(config=config)
        self._check_count = 0

    def execute(self, context: Context) -> StepResult:
        carrier = self.config.carrier if self.config else "fedex"

        shipment_id = f"SHIP-{uuid.uuid4().hex[:8].upper()}"
        context.set("shipment_id", shipment_id)
        context.set("carrier", carrier)

        step_log("ship", f"Shipment submitted to {carrier} -- shipment_id: {shipment_id}")
        step_log("ship", f"Polling for tracking number (every {self.poll_interval_seconds}s)...")

        return super().execute(context)

    def check(self, context: Context) -> bool:
        self._check_count += 1
        max_checks = self.config.max_checks if self.config else 4
        shipment_id = context.get("shipment_id", "?")
        carrier = context.get("carrier", "?")

        done = self._check_count >= max_checks

        if done:
            tracking = f"TRK-{uuid.uuid4().hex[:10].upper()}"
            context.set("tracking_number", tracking)
            step_log("ship", f"OK -- {carrier} assigned tracking: {tracking} (after {self._check_count} checks)")
        else:
            step_log(
                "ship",
                f"  >> Poll {self._check_count}/{max_checks} -- " f"{shipment_id} status: processing...",
            )

        return done

    def on_complete(self, context: Context) -> dict[str, Any]:
        return {
            "shipment_id": context.get("shipment_id"),
            "carrier": context.get("carrier"),
            "tracking_number": context.get("tracking_number"),
            "checks_required": self._check_count,
            "shipped_at": datetime.now(UTC).isoformat(),
        }
