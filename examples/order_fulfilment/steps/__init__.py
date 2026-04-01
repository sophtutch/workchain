"""Step handlers for the order fulfilment workflow.

Importing this module registers all step handlers via decorators.
"""

from examples.order_fulfilment.steps.charge import charge_payment, check_payment
from examples.order_fulfilment.steps.confirm import send_confirmation
from examples.order_fulfilment.steps.reserve import reserve_inventory
from examples.order_fulfilment.steps.ship import check_shipment, ship_order
from examples.order_fulfilment.steps.validate import validate_order

__all__ = [
    "charge_payment",
    "check_payment",
    "check_shipment",
    "reserve_inventory",
    "send_confirmation",
    "ship_order",
    "validate_order",
]
