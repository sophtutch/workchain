"""Request and response models for the order fulfilment API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ============================================================================
# Request bodies
# ============================================================================


class OrderItem(BaseModel):
    sku: str
    quantity: int = Field(ge=1)
    price: float = Field(ge=0)


class CreateOrderRequest(BaseModel):
    """Request body for POST /orders."""

    customer_email: str
    shipping_region: str = "US"
    items: list[OrderItem]


class PaymentWebhookPayload(BaseModel):
    """Payload from payment gateway webhook."""

    correlation_id: str
    success: bool
    charge_id: str | None = None
    provider_ref: str | None = None
    error: str | None = None


# ============================================================================
# Response bodies
# ============================================================================


class StepView(BaseModel):
    step_id: str
    step_type: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    correlation_id: str | None = None
    next_poll_at: str | None = None


class OrderStatusResponse(BaseModel):
    run_id: str
    workflow: str
    version: str
    status: str
    steps: list[StepView]
