"""Order Fulfilment Workflow -- FastAPI example demonstrating workchain.

This package provides a complete example of a multi-step order fulfilment
workflow with:

- Parallel step execution (reserve inventory + charge payment)
- EventStep suspension and webhook-driven resumption
- PollingStep for carrier tracking
- FastAPI integration with Change Stream watcher support

Run the standalone demo::

    python -m examples.order_fulfilment

Run the FastAPI server::

    uvicorn examples.order_fulfilment.app:app --reload
"""
