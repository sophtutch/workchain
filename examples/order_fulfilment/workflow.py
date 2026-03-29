"""Workflow definition and step registry for order fulfilment.

DAG structure::

    validate_order
          │
     ┌────┴────┐
     │         │
   reserve   charge_payment  (EventStep — suspends for webhook)
   inventory
     │         │
     └────┬────┘
          │
      ship_order              (PollingStep — polls for tracking)
          │
    send_confirmation
"""

from __future__ import annotations

from examples.order_fulfilment.steps import (
    ChargePaymentConfig,
    ChargePaymentStep,
    ReserveInventoryConfig,
    ReserveInventoryStep,
    SendConfirmationConfig,
    SendConfirmationStep,
    ShipOrderConfig,
    ShipOrderStep,
    ValidateOrderConfig,
    ValidateOrderStep,
)
from workchain import DependencyFailurePolicy, Workflow
from workchain.steps import Step

# ============================================================================
# Step registry — maps class names to classes for runner deserialization
# ============================================================================

STEP_REGISTRY: dict[str, type[Step]] = {
    "ValidateOrderStep": ValidateOrderStep,
    "ReserveInventoryStep": ReserveInventoryStep,
    "ChargePaymentStep": ChargePaymentStep,
    "ShipOrderStep": ShipOrderStep,
    "SendConfirmationStep": SendConfirmationStep,
}


# ============================================================================
# Workflow builder
# ============================================================================


def build_workflow() -> Workflow:
    """
    Construct the order fulfilment workflow DAG.

    The workflow exercises:
    - Parallel branches (reserve + charge run concurrently)
    - EventStep suspension (charge waits for payment webhook)
    - PollingStep (ship polls for tracking number)
    - Failure propagation with SKIP policy (confirmation sends even if shipping fails)
    """
    return (
        Workflow(name="order_fulfilment", version="1.0.0")
        .add(
            step_id="validate",
            step=ValidateOrderStep(config=ValidateOrderConfig()),
        )
        .add(
            step_id="reserve",
            step=ReserveInventoryStep(config=ReserveInventoryConfig()),
            depends_on=["validate"],
        )
        .add(
            step_id="charge",
            step=ChargePaymentStep(config=ChargePaymentConfig()),
            depends_on=["validate"],
        )
        .add(
            step_id="ship",
            step=ShipOrderStep(config=ShipOrderConfig(max_checks=4)),
            depends_on=["reserve", "charge"],
        )
        .add(
            step_id="confirm",
            step=SendConfirmationStep(config=SendConfirmationConfig()),
            depends_on=["ship"],
            on_dependency_failure=DependencyFailurePolicy.SKIP,
        )
    )
