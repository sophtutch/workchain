"""Workflow definition for order fulfilment.

Steps execute sequentially:

    validate_order -> reserve_inventory -> charge_payment (async)
        -> ship_order (async) -> send_confirmation
"""

from __future__ import annotations

# Import steps to trigger decorator registration
import examples.order_fulfilment.steps as _steps  # noqa: F401
from workchain import PollPolicy, RetryPolicy, Step, StepConfig, Workflow


def build_workflow(order_data: dict) -> Workflow:
    """
    Construct the order fulfilment workflow.

    The workflow exercises:
    - Sync steps (validate, reserve, confirm)
    - Async steps with polling (charge, ship)
    - Retry policies
    - Context passing between steps
    """
    return Workflow(
        name="order_fulfilment",
        context={"order": order_data},
        steps=[
            Step(
                name="validate_order",
                handler="validate_order",
                retry_policy=RetryPolicy(max_attempts=2),
            ),
            Step(
                name="reserve_inventory",
                handler="reserve_inventory",
                config=StepConfig(data={"warehouse": "warehouse-east-1"}),
            ),
            Step(
                name="charge_payment",
                handler="charge_payment",
                is_async=True,
                completeness_check="examples.order_fulfilment.steps.charge.check_payment",
                config=StepConfig(data={"payment_provider": "stripe"}),
                poll_policy=PollPolicy(interval=2.0, backoff_multiplier=1.0, timeout=60.0),
            ),
            Step(
                name="ship_order",
                handler="ship_order",
                is_async=True,
                completeness_check="examples.order_fulfilment.steps.ship.check_shipment",
                config=StepConfig(data={"carrier": "fedex", "max_checks": 3}),
                poll_policy=PollPolicy(
                    interval=2.0, backoff_multiplier=1.5, max_interval=10.0, timeout=120.0,
                ),
            ),
            Step(
                name="send_confirmation",
                handler="send_confirmation",
                config=StepConfig(data={"from_email": "orders@example.com"}),
            ),
        ],
    )
