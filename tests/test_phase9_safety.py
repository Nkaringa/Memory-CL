from __future__ import annotations

import pytest

from core.config import Settings
from core.safety import (
    BootStage,
    FeatureFlagRegistry,
    HealthGate,
    SafeModeController,
)


# ---- SafeModeController ---------------------------------------------------
def test_safe_mode_starts_disabled_by_default() -> None:
    sm = SafeModeController()
    assert sm.status.enabled is False


def test_safe_mode_enable_records_reason_and_trigger() -> None:
    sm = SafeModeController()
    sm.enable(reason="boot stage failed", triggered_by="boot_failure")
    assert sm.status.enabled is True
    assert sm.status.triggered_by == "boot_failure"
    assert "boot" in sm.status.reason


def test_safe_mode_disable_clears_state() -> None:
    sm = SafeModeController(enabled=True, reason="x", triggered_by="config")
    sm.disable()
    assert sm.status.enabled is False
    assert sm.status.reason == ""


# ---- FeatureFlagRegistry --------------------------------------------------
def test_feature_flags_exposes_all_phase_specific_toggles() -> None:
    reg = FeatureFlagRegistry.from_settings(Settings())
    names = [f.name for f in reg.all()]
    assert "enable_graph_ranking" in names
    assert "enable_incremental_indexing" in names
    assert "enable_context_compression" in names
    assert "ui_enabled" in names
    assert "strict_bootstrap" in names


def test_feature_flags_environment_propagates() -> None:
    # Phase-10 strict validator demands non-sentinel secrets + json logs
    # + strict bootstrap when environment="production". Provide them so
    # the construction proves the propagation, not a config rejection.
    from pydantic import SecretStr

    reg = FeatureFlagRegistry.from_settings(
        Settings(
            environment="production",
            log_format="json",
            strict_bootstrap=True,
            otel_enabled=True,
            mcp_api_key=SecretStr("phase10-test-mcp-key"),
            neo4j_password=SecretStr("phase10-test-neo4j-pw"),
        ),
    )
    assert all(f.environment == "production" for f in reg.all())


def test_feature_flags_rejects_duplicate_names() -> None:
    from core.safety.feature_flags import FeatureFlag
    with pytest.raises(ValueError):
        FeatureFlagRegistry([
            FeatureFlag(name="x", description="", enabled=True, environment="dev"),
            FeatureFlag(name="x", description="", enabled=False, environment="dev"),
        ])


# ---- HealthGate -----------------------------------------------------------
@pytest.mark.asyncio
async def test_health_gate_runs_stages_in_order() -> None:
    order: list[str] = []

    async def make_probe(name: str, ok: bool):
        async def _probe():
            order.append(name)
            return ok
        return _probe

    stages = [
        BootStage(name="b", order=2, probe=await make_probe("b", True)),
        BootStage(name="a", order=1, probe=await make_probe("a", True)),
        BootStage(name="c", order=3, probe=await make_probe("c", True)),
    ]
    outcome = await HealthGate(stages).run()
    assert order == ["a", "b", "c"]
    assert outcome.overall_ok
    assert not outcome.safe_mode_recommended


@pytest.mark.asyncio
async def test_health_gate_marks_required_stage_failure_as_failed() -> None:
    async def fail():
        return False

    async def passes():
        return True

    outcome = await HealthGate([
        BootStage(name="storage", order=1, probe=fail, required=True),
        BootStage(name="warmup", order=2, probe=passes),
    ]).run()
    assert outcome.overall_ok is False
    assert outcome.safe_mode_recommended is True
    assert "storage" in outcome.failed_stages


@pytest.mark.asyncio
async def test_health_gate_optional_failure_only_degrades() -> None:
    async def soft_fail():
        return False

    async def passes():
        return True

    outcome = await HealthGate([
        BootStage(name="ui", order=1, probe=soft_fail, required=False),
        BootStage(name="api", order=2, probe=passes),
    ]).run()
    # One degradation alone shouldn't trigger safe-mode.
    assert outcome.overall_ok is True
    assert outcome.safe_mode_recommended is False
    assert "ui" in outcome.degraded_stages


@pytest.mark.asyncio
async def test_health_gate_captures_probe_exceptions() -> None:
    async def boom():
        raise RuntimeError("backend exploded")

    outcome = await HealthGate([
        BootStage(name="storage", order=1, probe=boom, required=True),
    ]).run()
    assert "storage" in outcome.failed_stages
    assert outcome.results[0].error.startswith("RuntimeError")


def test_health_gate_rejects_duplicate_stage_names() -> None:
    async def p():
        return True

    with pytest.raises(ValueError):
        HealthGate([
            BootStage(name="x", order=1, probe=p),
            BootStage(name="x", order=2, probe=p),
        ])
