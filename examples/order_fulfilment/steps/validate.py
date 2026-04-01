"""ValidateOrderStep — synchronous order validation."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from workchain import RetryPolicy, step


class ValidateOrderConfig(BaseModel):
    allowed_regions: list[str] = ["US", "EU", "UK", "AU"]


class OrderItem(BaseModel):
    sku: str
    quantity: int
    price: float


class OrderData(BaseModel):
    order_id: str = "?"
    customer_email: str = "customer@example.com"
    shipping_region: str = ""
    items: list[OrderItem] = []
    created_at: str | None = None


class OrderContext(BaseModel):
    """Shape of context at validation time — only the order is present."""
    order: OrderData = OrderData()


@step(name="validate_order", retry=RetryPolicy(max_attempts=2))
async def validate_order(config: dict, context: dict) -> dict:
    """
    Validate incoming order data.

    Checks that items exist, quantities are positive, and the shipping
    region is in the allowed list.
    """
    cfg = ValidateOrderConfig(**config)
    ctx = OrderContext(**context)
    order = ctx.order

    if not order.items:
        raise ValueError("Order has no items")

    for item in order.items:
        if item.quantity <= 0:
            raise ValueError(f"Invalid quantity for SKU {item.sku}")

    if order.shipping_region not in cfg.allowed_regions:
        raise ValueError(f"Shipping region '{order.shipping_region}' not supported")

    total = sum(item.price * item.quantity for item in order.items)

    print(f"  [validate] Order {order.order_id} valid -- {len(order.items)} item(s), ${total:.2f}, region {order.shipping_region}")
    return {
        "order_id": order.order_id,
        "item_count": len(order.items),
        "total": total,
        "validated_at": datetime.now(UTC).isoformat(),
    }
