"""Entry point for running the demo via ``python -m examples.order_fulfilment``."""

from __future__ import annotations

import asyncio

from examples.order_fulfilment.demo import run_demo

if __name__ == "__main__":
    asyncio.run(run_demo())
