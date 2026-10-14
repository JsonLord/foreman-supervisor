from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from pydantic import SecretStr
from test_foreman import Client, observation
from test_routing import RoutedResponsibility
from typer.testing import CliRunner

from foreman.cli import app
from foreman.config import FactoryConfig
from foreman.foreman import ForemanModelError, JevForemanModel
from foreman.foreman.provider import ProviderForemanModel
from foreman.jev_usage import JevUsageRecorder
from foreman.laya import (
    BLOCKED_PENDING_LAYA_CONTRACT,
    LayaDecisionProvider,
    LayaSettings,
    LayaTransportError,
    LayaTransportResult,
)
from foreman.models import FactoryStatus, InterventionType
from foreman.provider_selection import semantic_components, validate_provider_configuration
from foreman.providers import AssessmentRequest, JevDecisionProvider
from foreman.request_budget import AssessmentRequestBudget, JevRequestBudget, json_bytes
from foreman.responsibilities import Check, ResponsibilityRegistry, ResponsibilityRoute
from foreman.routing import (
    JevResponsibilityRouter,
    ProviderResponsibilityRouter,
)
from foreman.runtime import FactoryRuntime
from foreman.workers import FakeWorker
from foreman.workers.base import worker_environment

CHECKS = (
    Check("example", "complete", "Is the implementation complete?"),
    Check("example", "verified", "Have the required tests passed?"),
)


class FixtureTransport:
    """Internal-contract double; this does not model any Laya HTTP wire schema."""

    requires_api_key = False

    def __init__(self, *, error=None, response=None, delay=0):
        self.error = error
        self.response = response
        self.delay = delay
        self.calls = []
        self.close_count = 0

    async def assess(self, request, *, settings):
        self.calls.append((request, settings))
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        if self.response is not None:
            return self.response
        pairs = [(key, 0.8) for key in request.questions]
        return LayaTransportResult(pairs, json_bytes(pairs))

    async def close(self):
        self.close_count += 1


def provider(transport, **kwargs):
    return LayaDecisionProvider(
        settings=LayaSettings("https://laya.example", SecretStr("supervisor-secret"), 0.03),
        transport=transport, **kwargs,
    )


def request():
    questions = {check.key: check.instructions for check in CHECKS}
    fitted = AssessmentRequestBudget().fit(observation().state_for(("git.diff",)), questions)
    return AssessmentRequest(fitted, questions)


def test_provider_defaults_and_explicit_configuration(monkeypatch):
    monkeypatch.delenv("FOREMAN_DECISION_PROVIDER", raising=False)
    assert FactoryConfig.from_environment().decision_provider == "jev"
    monkeypatch.setenv("FOREMAN_DECISION_PROVIDER", "laya")
    monkeypatch.setenv("FOREMAN_LAYA_BASE_URL", "https://laya.example")
    monkeypatch.setenv("FOREMAN_LAYA_TIMEOUT", "3.5")
    config = FactoryConfig.from_environment()
    assert config.decision_provider == "laya"
    assert config.laya_base_url == "https://laya.example"
    assert config.laya_timeout_seconds == 3.5
    assert AssessmentRequestBudget is JevRequestBudget


def test_selected_credentials_only(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("FOREMAN_LAYA_API_KEY", raising=False)
    with pytest.raises(ForemanModelError, match="Jev: Missing TYPESAFE_API_KEY"):
        validate_provider_configuration(FactoryConfig())
    validate_provider_configuration(FactoryConfig(
        decision_provider="laya", laya_base_url="https://laya.example",
    ))
    monkeypatch.setenv("TYPESAFE_API_KEY", "existing")
    validate_provider_configuration(FactoryConfig())


def test_live_transport_is_blocked_and_not_invented():
    with pytest.raises(ForemanModelError, match=BLOCKED_PENDING_LAYA_CONTRACT):
        semantic_components(
            FactoryConfig(decision_provider="laya", laya_base_url="https://laya.example"),
            budget=AssessmentRequestBudget(), usage_recorder=JevUsageRecorder(),
        )


def test_transport_declares_its_authentication_requirement():
    transport = FixtureTransport()
    transport.requires_api_key = True
    with pytest.raises(ForemanModelError, match="FOREMAN_LAYA_API_KEY"):
        LayaDecisionProvider(settings=LayaSettings("https://laya.example"), transport=transport)
    assert LayaDecisionProvider(
        settings=LayaSettings("https://laya.example", SecretStr("configured")),
        transport=transport,
    )


def test_selection_preserves_default_jev_components():
    model, router = semantic_components(
        FactoryConfig(), budget=AssessmentRequestBudget(), usage_recorder=JevUsageRecorder(),
    )
    assert isinstance(model, JevForemanModel)
    assert isinstance(model.provider, JevDecisionProvider)
    assert isinstance(router, JevResponsibilityRouter)


@pytest.mark.asyncio
async def test_jev_internal_boundary_keeps_clamping_and_batched_nouls():
    client = Client(result={"answers": {
        CHECKS[0].key: {"noul": 1.01}, CHECKS[1].key: {"noul": -0.01},
    }})
    adapter = JevDecisionProvider(client=client)
    result = await adapter.assess(request())
    assert result.probabilities == {CHECKS[0].key: 1, CHECKS[1].key: 0}
    assert len(client.calls) == 1
    assert {key: q.instructions for key, q in client.calls[0]["questions"].items()} == (
        request().questions
    )
    assert client.calls[0]["state"] == request().evidence.state
    assert client.calls[0]["timeout"] == 10
    await adapter.close()


@pytest.mark.asyncio
async def test_laya_batches_checks_and_preserves_instructions_and_state():
    transport = FixtureTransport()
    model, router = semantic_components(
        FactoryConfig(decision_provider="laya", laya_base_url="https://laya.example"),
        budget=AssessmentRequestBudget(), usage_recorder=JevUsageRecorder(),
        laya_transport=transport,
    )
    assert isinstance(model, ProviderForemanModel)
    assert isinstance(router, ProviderResponsibilityRouter)
    result = await model.assess(observation(), CHECKS)
    assert result.probability("example", "complete") == 0.8
    assert result.probability("example", "verified") == 0.8
    assert len(transport.calls) == 1
    sent, _ = transport.calls[0]
    assert sent.questions == {check.key: check.instructions for check in CHECKS}
    assert sent.evidence.state["original_job"] == "job"
    assert sent.evidence.state["agents_md_instructions"] == observation().agents_md_instructions
    assert sent.purpose == "assessment"
    assert "api_key" not in json.dumps(sent.evidence.state)


@pytest.mark.asyncio
async def test_laya_evidence_groups_and_budget_remain_bounded():
    transport = FixtureTransport()
    checks = (
        Check("example", "diff", "Judge diff", evidence=("git.diff",)),
        Check("example", "worker", "Judge worker", evidence=("worker",)),
    )
    model = ProviderForemanModel(provider(transport))
    value = observation().model_copy(update={"git_diff": "large evidence " * 30_000})
    await model.assess(value, checks)
    assert len(transport.calls) == 2
    for sent, _ in transport.calls:
        assert sent.evidence.measurements["total_bytes"] <= 60_000
        assert sent.evidence.measurements["pair_bytes"] <= 30_000
    states = [sent.evidence.state for sent, _ in transport.calls]
    assert any("git_diff" in state and "worker_history" not in state for state in states)
    assert any("worker_history" in state and "git_diff" not in state for state in states)


@pytest.mark.asyncio
async def test_laya_routing_preserves_globals_multiple_matches_and_thresholds():
    registry = ResponsibilityRegistry([
        RoutedResponsibility("global", "", always=True),
        RoutedResponsibility("alpha", "Alpha?", threshold=0.7),
        RoutedResponsibility("beta", "Beta?", threshold=0.8),
        RoutedResponsibility("gamma", "Gamma?", threshold=0.9),
    ])
    transport = FixtureTransport()
    router = ProviderResponsibilityRouter(provider(transport))
    decision = await router.route("work", registry)
    assert decision.active_responsibility_ids == ["global", "alpha", "beta"]
    assert len(transport.calls) == 1
    sent, _ = transport.calls[0]
    assert sent.evidence.state == {"work": "work"}
    assert sent.purpose == "routing"
    assert sent.questions == {
        "responsibility__alpha": "Alpha?", "responsibility__beta": "Beta?",
        "responsibility__gamma": "Gamma?",
    }
    group = await router.route_groups("work", {
        "global": ResponsibilityRoute(always=True),
        "conditional": ResponsibilityRoute(always=False, instructions="Match?", threshold=0.8),
    })
    assert group.active_responsibility_ids == ["global", "conditional"]
    assert transport.calls[1][0].questions == {"group__conditional": "Match?"}


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [float("nan"), float("inf"), -0.01, 1.01, True, "0.5", None])
async def test_invalid_probabilities_are_rejected(raw):
    pairs = [(CHECKS[0].key, raw), (CHECKS[1].key, 0.5)]
    with pytest.raises(ForemanModelError, match="Laya"):
        await provider(FixtureTransport(response=LayaTransportResult(pairs, 10))).assess(request())


@pytest.mark.asyncio
@pytest.mark.parametrize("pairs,match", [
    ([], "omitted"),
    ([(CHECKS[0].key, 0.5)], "omitted"),
    ([(CHECKS[0].key, 0.5), (CHECKS[0].key, 0.5)], "duplicate"),
    ([(CHECKS[0].key, 0.5), (CHECKS[1].key, 0.5), ("unknown", 0.5)], "unexpected"),
])
async def test_missing_partial_duplicate_and_unknown_results(pairs, match):
    with pytest.raises(ForemanModelError, match=match):
        await provider(FixtureTransport(response=LayaTransportResult(pairs, 10))).assess(request())


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,match", [
    (LayaTransportError(401), "authentication"),
    (LayaTransportError(403), "authentication"),
    (LayaTransportError(429), "429"),
    (LayaTransportError(500), "500"),
    (LayaTransportError(503), "503"),
    (ConnectionError("supervisor-secret"), "transport"),
    (json.JSONDecodeError("supervisor-secret", "malformed", 0), "validation"),
    (RuntimeError("Authorization: supervisor-secret"), "transport"),
    (ForemanModelError("supervisor-secret"), "transport"),
])
async def test_failures_are_translated_without_secret_disclosure(failure, match, caplog):
    with pytest.raises(ForemanModelError, match=match) as raised:
        await provider(FixtureTransport(error=failure)).assess(request())
    assert "supervisor-secret" not in str(raised.value)
    assert "supervisor-secret" not in caplog.text


@pytest.mark.asyncio
async def test_timeout_cancels_transport_and_does_not_synthesize_results():
    with pytest.raises(ForemanModelError, match="timed out"):
        await provider(FixtureTransport(delay=1)).assess(request())


@pytest.mark.asyncio
async def test_incompatible_and_oversized_responses():
    with pytest.raises(ForemanModelError, match="incompatible schema"):
        await provider(FixtureTransport(response={"invented": "payload"})).assess(request())
    response = LayaTransportResult([(key, 0.5) for key in request().questions], 60_001)
    with pytest.raises(ForemanModelError, match="response budget"):
        await provider(FixtureTransport(response=response)).assess(request())
    with pytest.raises(ForemanModelError, match="response budget"):
        await provider(
            FixtureTransport(response=replace(response, response_bytes=0)), max_response_bytes=1,
        ).assess(request())


@pytest.mark.asyncio
async def test_routing_failure_fails_before_worker_launch(tmp_path):
    transport = FixtureTransport(error=LayaTransportError(503))
    semantic = provider(transport)
    launched = []
    registry = ResponsibilityRegistry([RoutedResponsibility("alpha", "Alpha?")])
    runtime = FactoryRuntime(
        repository=tmp_path, job="job", model=ProviderForemanModel(semantic),
        router=ProviderResponsibilityRouter(semantic), responsibilities=registry,
        config=FactoryConfig(), worker_factory=lambda record: launched.append(record),
        event_sink=lambda event: None,
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.FAILED
    assert any("503" in error for error in state.errors)
    assert launched == []


@pytest.mark.asyncio
async def test_laya_outage_uses_existing_consecutive_failure_escalation(tmp_path):
    semantic = provider(FixtureTransport(error=LayaTransportError(503)))
    runtime = FactoryRuntime(
        repository=tmp_path, job="job", model=ProviderForemanModel(semantic),
        config=FactoryConfig(
            assessment_min_interval_seconds=0, periodic_assessment_seconds=0.01,
            overall_timeout_seconds=3,
        ),
        worker_factory=lambda _: FakeWorker(wait_forever=True, output_lines=[]),
        event_sink=lambda event: None,
    )
    state = await runtime.run()
    assert state.status is FactoryStatus.ESCALATED
    assert state.consecutive_assessment_failures == 3
    assert state.latest_result is None
    assert state.intervention_history[-1].action is InterventionType.ESCALATE


def test_supervisor_key_filtered_without_stripping_other_provider_keys(monkeypatch):
    monkeypatch.setenv("FOREMAN_LAYA_API_KEY", "supervisor-secret")
    monkeypatch.setenv("FOREMAN_LAYA_CLIENT_SECRET", "another-supervisor-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jev-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "worker-key")
    monkeypatch.setenv("LAYA_API_KEY", "unrelated-key")
    inherited = worker_environment()
    assert "FOREMAN_LAYA_API_KEY" not in inherited
    assert "FOREMAN_LAYA_CLIENT_SECRET" not in inherited
    assert "TYPESAFE_API_KEY" not in inherited
    assert inherited["OPENAI_API_KEY"] == "worker-key"
    assert inherited["LAYA_API_KEY"] == "unrelated-key"


def test_settings_repr_redacts_secret():
    assert "supervisor-secret" not in repr(provider(FixtureTransport()).settings)


@pytest.mark.parametrize("url", [
    "file:///tmp/secret", "https://user:supervisor-secret@laya.example",
    "https://laya.example?key=supervisor-secret", "https://laya.example#secret",
])
def test_credential_bearing_and_invalid_urls_are_rejected(url):
    with pytest.raises(ValueError):
        LayaSettings(url)


def test_cli_laya_does_not_request_jev_key_and_blocks_before_worker(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setenv("FOREMAN_DECISION_PROVIDER", "laya")
    monkeypatch.setenv("FOREMAN_LAYA_BASE_URL", "https://laya.example")
    monkeypatch.setenv("FOREMAN_DATA_DIR", str(tmp_path / "data"))
    result = CliRunner().invoke(app, ["run", "--repo", str(tmp_path), "--job", "test"])
    assert result.exit_code == 2
    assert BLOCKED_PENDING_LAYA_CONTRACT in result.output
    assert "Missing TYPESAFE_API_KEY" not in result.output


@pytest.mark.asyncio
async def test_shared_laya_provider_closes_transport_once():
    transport = FixtureTransport()
    semantic = provider(transport)
    await ProviderForemanModel(semantic).close()
    await ProviderResponsibilityRouter(semantic).close()
    assert transport.close_count == 1
