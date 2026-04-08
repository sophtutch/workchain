"""Plugin discovery for step handler registration.

Handlers are registered by importing modules that use ``@step``,
``@async_step``, and ``@completeness_check`` decorators.  Two
discovery mechanisms are supported:

1. **Entry points** — installed packages declare handlers under
   the ``workchain.plugins`` group in their ``pyproject.toml``.
   Failures are logged as warnings but do not prevent startup.
2. **Environment variable** — ``WORKCHAIN_PLUGINS`` is a
   comma-separated list of dotted module paths to import.
   Failures are **fatal** — the server will not start with
   missing explicitly-configured plugins.
"""

from __future__ import annotations

import importlib
import logging
import sys
from importlib.metadata import entry_points

logger = logging.getLogger(__name__)


def discover_plugins(plugin_paths: str = "") -> list[str]:
    """Import plugin modules to register step handlers.

    Args:
        plugin_paths: Comma-separated dotted module paths (from
            the ``WORKCHAIN_PLUGINS`` environment variable).
            Failures for these are fatal.

    Returns:
        List of successfully loaded module names.

    Raises:
        SystemExit: If any explicitly configured plugin (from
            *plugin_paths*) fails to import.
    """
    loaded: list[str] = []

    # 1. Entry points — best-effort (installed packages may be optional)
    for ep in entry_points(group="workchain.plugins"):
        try:
            ep.load()
            loaded.append(ep.name)
            logger.info("Loaded plugin (entry point): %s", ep.name)
        except Exception:
            logger.exception("Failed to load plugin entry point: %s", ep.name)

    # 2. Environment variable paths — fatal on failure
    failed: list[str] = []
    for raw in plugin_paths.split(","):
        path = raw.strip()
        if not path:
            continue
        try:
            importlib.import_module(path)
            loaded.append(path)
            logger.info("Loaded plugin (env): %s", path)
        except Exception:
            logger.exception("Failed to load plugin module: %s", path)
            failed.append(path)

    if failed:
        logger.critical(
            "Server cannot start — %d configured plugin(s) failed to load: %s",
            len(failed),
            ", ".join(failed),
        )
        sys.exit(1)

    return loaded
