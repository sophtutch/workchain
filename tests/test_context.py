"""Tests for workchain.context.Context."""

from __future__ import annotations

import pytest

from workchain import Context


class TestContextBasicAccess:
    def test_get_set(self):
        ctx = Context()
        ctx.set("key", "value")
        assert ctx.get("key") == "value"

    def test_get_default(self):
        ctx = Context()
        assert ctx.get("missing") is None
        assert ctx.get("missing", 42) == 42

    def test_getitem_setitem(self):
        ctx = Context()
        ctx["x"] = 10
        assert ctx["x"] == 10

    def test_getitem_missing_raises(self):
        ctx = Context()
        with pytest.raises(KeyError):
            ctx["nope"]

    def test_contains(self):
        ctx = Context(data={"a": 1})
        assert "a" in ctx
        assert "b" not in ctx


class TestContextSerialization:
    def test_rejects_non_serializable(self):
        ctx = Context()
        with pytest.raises(ValueError, match="not JSON-serializable"):
            ctx.set("bad", object())

    def test_rejects_non_serializable_via_setitem(self):
        ctx = Context()
        with pytest.raises(ValueError, match="not JSON-serializable"):
            ctx["bad"] = {1, 2, 3}  # sets aren't JSON-serializable

    def test_accepts_json_types(self):
        ctx = Context()
        ctx.set("str", "hello")
        ctx.set("int", 42)
        ctx.set("float", 3.14)
        ctx.set("bool", True)
        ctx.set("none", None)
        ctx.set("list", [1, 2, 3])
        ctx.set("dict", {"nested": "ok"})

    def test_to_dict(self):
        ctx = Context(data={"a": 1, "b": 2})
        d = ctx.to_dict()
        assert d == {"a": 1, "b": 2}
        # Mutation of returned dict doesn't affect context
        d["c"] = 3
        assert "c" not in ctx

    def test_from_dict(self):
        ctx = Context.from_dict({"x": 10})
        assert ctx.get("x") == 10


class TestContextStepOutputs:
    def test_set_and_get_step_output(self):
        ctx = Context()
        ctx.set_step_output("step_a", {"result": 42})
        assert ctx.step_output("step_a") == {"result": 42}

    def test_step_output_missing_raises(self):
        ctx = Context()
        with pytest.raises(KeyError, match="No output found for step"):
            ctx.step_output("nonexistent")

    def test_multiple_step_outputs(self):
        ctx = Context()
        ctx.set_step_output("a", {"x": 1})
        ctx.set_step_output("b", {"y": 2})
        assert ctx.step_output("a") == {"x": 1}
        assert ctx.step_output("b") == {"y": 2}

    def test_step_output_rejects_non_serializable(self):
        ctx = Context()
        with pytest.raises(ValueError, match="not JSON-serializable"):
            ctx.set_step_output("step_a", {"bad": object()})

    def test_step_outputs_survive_roundtrip(self):
        ctx = Context()
        ctx.set_step_output("s1", {"val": "hello"})
        restored = Context.from_dict(ctx.to_dict())
        assert restored.step_output("s1") == {"val": "hello"}


class TestContextRepr:
    def test_repr(self):
        ctx = Context(data={"a": 1, "b": 2})
        r = repr(ctx)
        assert "Context" in r
        assert "a" in r
        assert "b" in r
