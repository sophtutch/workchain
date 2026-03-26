"""Shared runtime context passed between workflow steps."""

from __future__ import annotations

import json
from typing import Any


class Context:
    """
    Shared, JSON-serializable key/value store passed between workflow steps.

    Step outputs are stored under their step_id key and accessible via
    step_output(step_id). Arbitrary global state can be read/written directly.

    All values must be JSON-serializable — this is enforced on write so that
    round-tripping through MongoDB is always safe.
    """

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = data or {}

    # ------------------------------------------------------------------
    # Core access
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._assert_serializable(key, value)
        self._data[key] = value

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)

    def __contains__(self, key: str) -> bool:
        return key in self._data

    # ------------------------------------------------------------------
    # Step output helpers
    # ------------------------------------------------------------------

    _STEP_OUTPUTS_KEY = "__step_outputs__"

    def set_step_output(self, step_id: str, output: dict[str, Any]) -> None:
        """Called by the runner to store a step's output after completion."""
        self._assert_serializable(step_id, output)
        if self._STEP_OUTPUTS_KEY not in self._data:
            self._data[self._STEP_OUTPUTS_KEY] = {}
        self._data[self._STEP_OUTPUTS_KEY][step_id] = output

    def step_output(self, step_id: str) -> dict[str, Any]:
        """Retrieve the output dict produced by a completed step."""
        outputs = self._data.get(self._STEP_OUTPUTS_KEY, {})
        if step_id not in outputs:
            raise KeyError(f"No output found for step '{step_id}'. Has it completed?")
        return outputs[step_id]

    # ------------------------------------------------------------------
    # Serialization (for MongoDB persistence)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Context:
        return cls(data=data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_serializable(key: str, value: Any) -> None:
        try:
            json.dumps(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Context value for key '{key}' is not JSON-serializable: {exc}") from exc

    def __repr__(self) -> str:
        return f"Context(keys={list(self._data.keys())})"
