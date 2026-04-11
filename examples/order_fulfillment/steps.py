"""Step handlers for the order fulfillment workflow.

Every handler declares typed StepConfig and StepResult subclasses so that
all steps are launchable from the workflow designer UI.

Steps:
  1.  validate_order        — Validate order details and line items
  2.  check_inventory       — Verify stock availability for all items
  3.  calculate_shipping    — Compute shipping cost and delivery estimate
  4.  process_payment       — Async: charge payment method via gateway, poll until settled
  5.  reserve_inventory     — Decrement stock counts for ordered items
  6.  pick_and_pack         — Warehouse picks and packs the order
  7.  arrange_shipping      — Async: book carrier and get tracking number, poll until dispatched
  8.  send_confirmation     — Email order confirmation with tracking info
"""

from __future__ import annotations

import logging
import uuid
from typing import cast

from workchain import (
    CheckResult,
    PollPolicy,
    RetryPolicy,
    StepConfig,
    StepResult,
    async_step,
    completeness_check,
    step,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Poll simulation state
# ---------------------------------------------------------------------------

_payment_polls: dict[str, int] = {}
_shipping_polls: dict[str, int] = {}

# ---------------------------------------------------------------------------
# Configs
# ---------------------------------------------------------------------------


class ValidateOrderConfig(StepConfig):
    order_id: str
    customer_email: str
    line_items: list[dict[str, int | str]]
    """Each item: {"sku": "ABC-123", "quantity": 2}."""


class CheckInventoryConfig(StepConfig):
    warehouse_id: str = "wh-primary"


class CalculateShippingConfig(StepConfig):
    destination_zip: str
    shipping_method: str = "standard"
    """One of: standard, express, overnight."""


class ProcessPaymentConfig(StepConfig):
    payment_method: str = "credit_card"
    currency: str = "USD"


class ReserveInventoryConfig(StepConfig):
    warehouse_id: str = "wh-primary"


class PickAndPackConfig(StepConfig):
    warehouse_id: str = "wh-primary"
    priority: str = "normal"
    """One of: normal, high, rush."""


class ArrangeShippingConfig(StepConfig):
    carrier: str = "ups"
    """Carrier code: ups, fedex, usps, dhl."""
    service_level: str = "ground"


class SendConfirmationConfig(StepConfig):
    include_tracking: bool = True
    template: str = "order_shipped"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class ValidateOrderResult(StepResult):
    order_id: str = ""
    customer_email: str = ""
    item_count: int = 0
    valid: bool = False


class CheckInventoryResult(StepResult):
    all_in_stock: bool = False
    items_checked: int = 0
    warehouse_id: str = ""


class CalculateShippingResult(StepResult):
    shipping_cost_cents: int = 0
    estimated_days: int = 0
    shipping_method: str = ""


class ProcessPaymentResult(StepResult):
    transaction_id: str = ""
    amount_cents: int = 0
    status: str = ""


class ReserveInventoryResult(StepResult):
    reservation_id: str = ""
    items_reserved: int = 0


class PickAndPackResult(StepResult):
    package_id: str = ""
    weight_grams: int = 0
    dimensions: str = ""


class ArrangeShippingResult(StepResult):
    shipment_id: str = ""
    tracking_number: str = ""
    carrier: str = ""


class SendConfirmationResult(StepResult):
    email_id: str = ""
    sent_to: str = ""


# ---------------------------------------------------------------------------
# Step 1: validate_order (root step)
# ---------------------------------------------------------------------------


@step(category="Order Fulfilment", description="Validate order details and line items")
async def validate_order(
    config: ValidateOrderConfig,
    _results: dict[str, StepResult],
) -> ValidateOrderResult:
    """Validate order details: check line items are non-empty and email is present."""
    if not config.line_items:
        raise ValueError("Order must contain at least one line item")
    if not config.customer_email:
        raise ValueError("Customer email is required")

    logger.info(
        "[order] Validated order %s: %d items for %s",
        config.order_id,
        len(config.line_items),
        config.customer_email,
    )
    return ValidateOrderResult(
        order_id=config.order_id,
        customer_email=config.customer_email,
        item_count=len(config.line_items),
        valid=True,
    )


# ---------------------------------------------------------------------------
# Step 2: check_inventory (parallel with calculate_shipping)
# ---------------------------------------------------------------------------


@step(category="Order Fulfilment", description="Verify stock availability at warehouse")
async def check_inventory(
    config: CheckInventoryConfig,
    results: dict[str, StepResult],
) -> CheckInventoryResult:
    """Verify that all ordered items are in stock at the given warehouse."""
    order = cast(ValidateOrderResult, results["validate_order"])
    logger.info(
        "[inventory] Checking %d items at %s",
        order.item_count,
        config.warehouse_id,
    )
    return CheckInventoryResult(
        all_in_stock=True,
        items_checked=order.item_count,
        warehouse_id=config.warehouse_id,
    )


# ---------------------------------------------------------------------------
# Step 3: calculate_shipping (parallel with check_inventory)
# ---------------------------------------------------------------------------


@step(category="Order Fulfilment", description="Compute shipping cost and delivery estimate")
async def calculate_shipping(
    config: CalculateShippingConfig,
    results: dict[str, StepResult],
) -> CalculateShippingResult:
    """Compute shipping cost and delivery estimate based on destination and method."""
    order = cast(ValidateOrderResult, results["validate_order"])

    cost_per_item = {"standard": 499, "express": 999, "overnight": 1999}
    cost = cost_per_item.get(config.shipping_method, 499) * order.item_count
    days = {"standard": 5, "express": 2, "overnight": 1}

    logger.info(
        "[shipping] %s to %s: $%.2f, %d days",
        config.shipping_method,
        config.destination_zip,
        cost / 100,
        days.get(config.shipping_method, 5),
    )
    return CalculateShippingResult(
        shipping_cost_cents=cost,
        estimated_days=days.get(config.shipping_method, 5),
        shipping_method=config.shipping_method,
    )


# ---------------------------------------------------------------------------
# Step 4: process_payment (async — polls external payment gateway)
# ---------------------------------------------------------------------------


@completeness_check()
async def check_payment(
    _config: ProcessPaymentConfig,
    _results: dict[str, StepResult],
    result: ProcessPaymentResult,
) -> CheckResult:
    """Poll the payment gateway until the transaction settles."""
    txn_id = result.transaction_id
    count = _payment_polls.get(txn_id, 0) + 1
    _payment_polls[txn_id] = count

    total_polls = 2
    progress = min(count / total_polls, 1.0)
    complete = count >= total_polls

    logger.info(
        "[payment] Poll %d/%d for txn=%s (%.0f%%)",
        count,
        total_polls,
        txn_id,
        progress * 100,
    )
    return CheckResult(
        complete=complete,
        progress=progress,
        message=f"Payment poll {count}/{total_polls}",
    )


@async_step(
    completeness_check=check_payment,
    poll=PollPolicy(interval=2.0, timeout=60.0, max_polls=5),
    category="Order Fulfilment",
    description="Charge payment method via gateway and poll until settled",
)
async def process_payment(
    config: ProcessPaymentConfig,
    results: dict[str, StepResult],
) -> ProcessPaymentResult:
    """Submit a payment charge to the gateway. Settles asynchronously."""
    inventory = cast(CheckInventoryResult, results["check_inventory"])
    shipping = cast(CalculateShippingResult, results["calculate_shipping"])

    # Simulate an order total (items + shipping).
    item_total = inventory.items_checked * 2999  # $29.99 per item
    amount = item_total + shipping.shipping_cost_cents
    txn_id = f"txn-{uuid.uuid4().hex[:12]}"

    logger.info(
        "[payment] Submitted %s charge of $%.2f via %s",
        config.currency,
        amount / 100,
        config.payment_method,
    )
    return ProcessPaymentResult(
        transaction_id=txn_id,
        amount_cents=amount,
        status="pending",
    )


# ---------------------------------------------------------------------------
# Step 5: reserve_inventory (after inventory check + payment)
# ---------------------------------------------------------------------------


@step(
    retry=RetryPolicy(max_attempts=3, wait_seconds=0.5, wait_multiplier=2.0),
    category="Order Fulfilment",
    description="Atomically reserve stock for the order",
)
async def reserve_inventory(
    config: ReserveInventoryConfig,
    results: dict[str, StepResult],
) -> ReserveInventoryResult:
    """Atomically reserve stock for the order. Retries on contention."""
    inventory = cast(CheckInventoryResult, results["check_inventory"])
    reservation_id = f"res-{uuid.uuid4().hex[:10]}"
    logger.info(
        "[inventory] Reserved %d items at %s (reservation=%s)",
        inventory.items_checked,
        config.warehouse_id,
        reservation_id,
    )
    return ReserveInventoryResult(
        reservation_id=reservation_id,
        items_reserved=inventory.items_checked,
    )


# ---------------------------------------------------------------------------
# Step 6: pick_and_pack
# ---------------------------------------------------------------------------


@step(category="Order Fulfilment", description="Warehouse picks and packs items for shipment")
async def pick_and_pack(
    config: PickAndPackConfig,
    results: dict[str, StepResult],
) -> PickAndPackResult:
    """Warehouse picks ordered items and packs them for shipment."""
    reservation = cast(ReserveInventoryResult, results["reserve_inventory"])
    package_id = f"pkg-{uuid.uuid4().hex[:10]}"
    weight = reservation.items_reserved * 350  # ~350g per item
    logger.info(
        "[warehouse] Packed %s (%d items, %dg, priority=%s) at %s",
        package_id,
        reservation.items_reserved,
        weight,
        config.priority,
        config.warehouse_id,
    )
    return PickAndPackResult(
        package_id=package_id,
        weight_grams=weight,
        dimensions=f"{20 + reservation.items_reserved * 5}x20x15cm",
    )


# ---------------------------------------------------------------------------
# Step 7: arrange_shipping (async — polls carrier API)
# ---------------------------------------------------------------------------


@completeness_check()
async def check_shipment(
    _config: ArrangeShippingConfig,
    _results: dict[str, StepResult],
    result: ArrangeShippingResult,
) -> CheckResult:
    """Poll the carrier API until the shipment is dispatched."""
    shipment_id = result.shipment_id
    count = _shipping_polls.get(shipment_id, 0) + 1
    _shipping_polls[shipment_id] = count

    total_polls = 3
    progress = min(count / total_polls, 1.0)
    complete = count >= total_polls

    logger.info(
        "[shipping] Dispatch poll %d/%d for shipment=%s (%.0f%%)",
        count,
        total_polls,
        shipment_id,
        progress * 100,
    )
    return CheckResult(
        complete=complete,
        progress=progress,
        message=f"Dispatch poll {count}/{total_polls}",
    )


@async_step(
    completeness_check=check_shipment,
    poll=PollPolicy(interval=3.0, timeout=120.0, max_polls=8),
    category="Order Fulfilment",
    description="Book carrier pickup and get tracking number",
)
async def arrange_shipping(
    config: ArrangeShippingConfig,
    results: dict[str, StepResult],
) -> ArrangeShippingResult:
    """Book a carrier pickup and get a tracking number."""
    package = cast(PickAndPackResult, results["pick_and_pack"])
    shipment_id = f"ship-{uuid.uuid4().hex[:10]}"
    tracking = f"1Z{uuid.uuid4().hex[:16].upper()}"
    logger.info(
        "[shipping] Booked %s %s for %s (tracking=%s)",
        config.carrier,
        config.service_level,
        package.package_id,
        tracking,
    )
    return ArrangeShippingResult(
        shipment_id=shipment_id,
        tracking_number=tracking,
        carrier=config.carrier,
    )


# ---------------------------------------------------------------------------
# Step 8: send_confirmation
# ---------------------------------------------------------------------------


@step(category="Notification", description="Send order confirmation email with tracking info")
async def send_confirmation(
    config: SendConfirmationConfig,
    results: dict[str, StepResult],
) -> SendConfirmationResult:
    """Send order confirmation email with optional tracking info."""
    order = cast(ValidateOrderResult, results["validate_order"])
    shipping = cast(ArrangeShippingResult, results["arrange_shipping"])

    tracking_msg = ""
    if config.include_tracking:
        tracking_msg = f" (tracking: {shipping.tracking_number})"

    email_id = f"email-{uuid.uuid4().hex[:10]}"
    logger.info(
        "[email] Sent '%s' to %s%s",
        config.template,
        order.customer_email,
        tracking_msg,
    )
    return SendConfirmationResult(
        email_id=email_id,
        sent_to=order.customer_email,
    )
