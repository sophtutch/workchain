"""Build the order fulfillment workflow definition.

DAG structure:

    validate_order (root)
        ├── check_inventory ──┐
        └── calculate_shipping ──┤
                                 ├── process_payment (async)
                                 │        │
                                 └── reserve_inventory ← (check_inventory + process_payment)
                                              │
                                         pick_and_pack
                                              │
                                      arrange_shipping (async)
                                              │
                                     send_confirmation
"""

from __future__ import annotations

# Import steps module to trigger handler registration via decorators.
from examples.order_fulfillment import steps  # noqa: F401
from examples.order_fulfillment.steps import (
    ArrangeShippingConfig,
    CalculateShippingConfig,
    ValidateOrderConfig,
)
from workchain import PollPolicy, RetryPolicy, Step, Workflow


def build_workflow(
    order_id: str,
    customer_email: str,
    line_items: list[dict[str, int | str]],
    *,
    destination_zip: str = "10001",
    shipping_method: str = "standard",
    carrier: str = "ups",
) -> Workflow:
    """Construct an 8-step order fulfillment workflow.

    After validate_order, the pipeline fans out:
        - check_inventory and calculate_shipping run in parallel
        - process_payment depends on both (needs total from shipping calc)
        - reserve_inventory waits for inventory check + payment confirmation
        - pick_and_pack → arrange_shipping → send_confirmation run sequentially

    Args:
        order_id: Unique order identifier.
        customer_email: Customer's email address.
        line_items: List of {"sku": "...", "quantity": N} dicts.
        destination_zip: Delivery ZIP code.
        shipping_method: One of standard, express, overnight.
        carrier: Carrier code (ups, fedex, usps, dhl).

    Returns:
        A fully-configured Workflow ready to be inserted into the store.
    """
    return Workflow(
        name=f"order-fulfillment-{order_id}",
        steps=[
            # 1. Validate order (root)
            Step(
                name="validate_order",
                handler="examples.order_fulfillment.steps.validate_order",
                config=ValidateOrderConfig(
                    order_id=order_id,
                    customer_email=customer_email,
                    line_items=line_items,
                ),
                depends_on=[],
            ),
            # 2. Check inventory (parallel branch A)
            Step(
                name="check_inventory",
                handler="examples.order_fulfillment.steps.check_inventory",
                config={},
                depends_on=["validate_order"],
            ),
            # 3. Calculate shipping (parallel branch B)
            Step(
                name="calculate_shipping",
                handler="examples.order_fulfillment.steps.calculate_shipping",
                config=CalculateShippingConfig(
                    destination_zip=destination_zip,
                    shipping_method=shipping_method,
                ),
                depends_on=["validate_order"],
            ),
            # 4. Process payment (async — joins both parallel branches)
            Step(
                name="process_payment",
                handler="examples.order_fulfillment.steps.process_payment",
                config={},
                depends_on=["check_inventory", "calculate_shipping"],
                is_async=True,
                completeness_check=(
                    "examples.order_fulfillment.steps.check_payment"
                ),
                poll_policy=PollPolicy(
                    interval=2.0,
                    timeout=60.0,
                    max_polls=5,
                ),
            ),
            # 5. Reserve inventory (after payment confirmed)
            Step(
                name="reserve_inventory",
                handler="examples.order_fulfillment.steps.reserve_inventory",
                config={},
                depends_on=["check_inventory", "process_payment"],
                retry_policy=RetryPolicy(
                    max_attempts=3,
                    wait_seconds=0.5,
                    wait_multiplier=2.0,
                ),
            ),
            # 6. Pick and pack
            Step(
                name="pick_and_pack",
                handler="examples.order_fulfillment.steps.pick_and_pack",
                config={},
                depends_on=["reserve_inventory"],
            ),
            # 7. Arrange shipping (async — polls carrier)
            Step(
                name="arrange_shipping",
                handler="examples.order_fulfillment.steps.arrange_shipping",
                config=ArrangeShippingConfig(
                    carrier=carrier,
                ),
                depends_on=["pick_and_pack"],
                is_async=True,
                completeness_check=(
                    "examples.order_fulfillment.steps.check_shipment"
                ),
                poll_policy=PollPolicy(
                    interval=3.0,
                    timeout=120.0,
                    max_polls=8,
                ),
            ),
            # 8. Send confirmation email
            Step(
                name="send_confirmation",
                handler="examples.order_fulfillment.steps.send_confirmation",
                config={},
                depends_on=["validate_order", "arrange_shipping"],
            ),
        ],
    )
