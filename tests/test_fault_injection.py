"""Tests for the fault-injection harness (fail-loud verification).

See docs/fault-injection-DESIGN.md. The harness is a Decorator over the
WorkSource protocol that deterministically raises on configured write
seams so the controller's I2 fail-loud paths can be dogfooded as
repeatable regression.
"""

import os
import pytest
from unittest.mock import AsyncMock

from src.controller.worksources.fault_injection import (
    FaultInjected,
    FaultInjectingWorkSource,
    parse_fault_spec,
    maybe_wrap_faults,
)


class TestParseFaultSpec:
    def test_single(self):
        assert parse_fault_spec("submit_review:1") == {"submit_review": 1}

    def test_multi(self):
        assert parse_fault_spec("submit_review:1,complete:2") == {
            "submit_review": 1, "complete": 2}

    def test_empty_and_none(self):
        assert parse_fault_spec("") == {}
        assert parse_fault_spec(None) == {}

    def test_malformed_ignored(self):
        # garbage / missing count / non-int are skipped, valid ones kept
        assert parse_fault_spec("submit_review:1,junk,fail:x,complete:3") == {
            "submit_review": 1, "complete": 3}

    def test_default_count_one(self):
        # a bare method name defaults to a single raise
        assert parse_fault_spec("submit_review") == {"submit_review": 1}


class TestFaultInjectingWorkSource:
    def setup_method(self):
        self.inner = AsyncMock()

    @pytest.mark.asyncio
    async def test_raises_configured_count_then_passes_through(self):
        ws = FaultInjectingWorkSource(self.inner, {"submit_review": 2})
        for _ in range(2):
            with pytest.raises(FaultInjected):
                await ws.submit_review("t", "approved", "r", "j")
        # third call passes through to inner
        await ws.submit_review("t", "approved", "r", "j")
        self.inner.submit_review.assert_awaited_once_with("t", "approved", "r", "j")

    @pytest.mark.asyncio
    async def test_unconfigured_seam_delegates(self):
        ws = FaultInjectingWorkSource(self.inner, {"submit_review": 1})
        await ws.complete("t", object())
        self.inner.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_seam_method_delegates_via_getattr(self):
        # methods that aren't fault-injectable still work (passthrough)
        ws = FaultInjectingWorkSource(self.inner, {"submit_review": 1})
        await ws.poll("agent-1", ["judge"])
        self.inner.poll.assert_awaited_once_with("agent-1", ["judge"])

    @pytest.mark.asyncio
    async def test_fail_seam_can_be_injected(self):
        # the fail() escalation itself is a seam (verifies R3b fail-also-fails)
        ws = FaultInjectingWorkSource(self.inner, {"fail": 1})
        with pytest.raises(FaultInjected):
            await ws.fail("t", "reason")


class TestMaybeWrapFaults:
    def teardown_method(self):
        os.environ.pop("VF_FAULT_INJECT", None)

    def test_unset_returns_same_object(self):
        os.environ.pop("VF_FAULT_INJECT", None)
        inner = AsyncMock()
        assert maybe_wrap_faults(inner) is inner

    def test_empty_returns_same_object(self):
        os.environ["VF_FAULT_INJECT"] = ""
        inner = AsyncMock()
        assert maybe_wrap_faults(inner) is inner

    def test_set_returns_decorator(self):
        os.environ["VF_FAULT_INJECT"] = "submit_review:1"
        inner = AsyncMock()
        wrapped = maybe_wrap_faults(inner)
        assert isinstance(wrapped, FaultInjectingWorkSource)
