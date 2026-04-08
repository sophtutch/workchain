"""Server configuration via environment variables.

All settings are loaded from environment variables at module level.
Prefix-free variable names are used for simplicity::

    MONGO_URI=mongodb://localhost:27017
    MONGO_DATABASE=workchain
    ENGINE_INSTANCE_ID=server-001
"""

from __future__ import annotations

import uuid

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Workchain server settings, loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "workchain"

    # Engine
    engine_instance_id: str = ""
    engine_claim_interval: float = 5.0
    engine_heartbeat_interval: float = 10.0
    engine_sweep_interval: float = 60.0
    engine_step_stuck_seconds: float = 300.0
    engine_max_concurrent: int = 5

    # Plugins — comma-separated dotted module paths
    workchain_plugins: str = ""

    # Server
    server_title: str = "Workchain Server"

    def get_instance_id(self) -> str:
        """Return the configured instance ID, or generate one."""
        return self.engine_instance_id or f"wcs-{uuid.uuid4().hex[:8]}"
