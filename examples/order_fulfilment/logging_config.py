"""Logging configuration and step-level log helpers.

Provides colourful, structured output for visual tracking of
workflow progress in the terminal.
"""

from __future__ import annotations

import logging

# ============================================================================
# Global logging setup
# ============================================================================

LOG_FORMAT = "\033[90m%(asctime)s\033[0m %(levelname)-5s %(name)-30s %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Apply the standard log format for the order fulfilment example."""
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%H:%M:%S")

    # Quieten noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("motor").setLevel(logging.WARNING)


# ============================================================================
# Per-step coloured log helper
# ============================================================================

_STEP_STYLES = {
    "validate": ("*", "\033[36m"),  # cyan
    "reserve": (">", "\033[33m"),  # yellow
    "charge": ("$", "\033[35m"),  # magenta
    "ship": ("~", "\033[34m"),  # blue
    "confirm": ("@", "\033[32m"),  # green
}
_RESET = "\033[0m"

_step_logger = logging.getLogger("workchain.example.order")


def step_log(step_key: str, message: str) -> None:
    """Emit a visually distinct log line for a step."""
    icon, colour = _STEP_STYLES.get(step_key, (".", ""))
    _step_logger.info("%s%s  [%s] %s%s", colour, icon, step_key, message, _RESET)
