import pytest

from foreman.laya import LayaSettings
from foreman.laya_transport import HttpLayaTransport
from foreman.providers import AssessmentRequest
from foreman.request_budget import BudgetedRequest


@pytest.mark.asyncio
async def test_live_health():
    import httpx

    async with httpx.AsyncClient() as client:
        r = await client.get("https://leon4gr45-needle-router.hf.space/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_live_one_noul():
    transport = HttpLayaTransport()
    settings = LayaSettings(base_url="https://leon4gr45-needle-router.hf.space")
    evidence = BudgetedRequest(
        state={"job": "test"},
        measurements={"total_bytes": 10, "pair_bytes": 10},
        original_measurements={"total_bytes": 10, "pair_bytes": 10},
    )
    request = AssessmentRequest(
        evidence=evidence,
        questions={"meaningful_progress": "Is the worker making progress?"},
        purpose="assessment",
    )
    result = await transport.assess(request, settings=settings)
    assert len(result.probabilities) == 1
    assert result.probabilities[0][0] == "meaningful_progress"
    assert isinstance(result.probabilities[0][1], float)
    await transport.close()


@pytest.mark.asyncio
async def test_live_batch_noul():
    transport = HttpLayaTransport()
    settings = LayaSettings(base_url="https://leon4gr45-needle-router.hf.space")
    evidence = BudgetedRequest(
        state={"job": "test"},
        measurements={"total_bytes": 10, "pair_bytes": 10},
        original_measurements={"total_bytes": 10, "pair_bytes": 10},
    )
    request = AssessmentRequest(
        evidence=evidence,
        questions={
            "meaningful_progress": "Is the worker making progress?",
            "worker_stuck": "Is the worker stuck?",
            "implementation_complete": "Is it complete?",
            "needs_human": "Does it need a human?",
        },
        purpose="assessment",
    )
    result = await transport.assess(request, settings=settings)
    assert len(result.probabilities) == 4
    keys = set(k for k, v in result.probabilities)
    assert keys == {"meaningful_progress", "worker_stuck", "implementation_complete", "needs_human"}
    await transport.close()


@pytest.mark.asyncio
async def test_live_contradictory_checks():
    transport = HttpLayaTransport()
    settings = LayaSettings(base_url="https://leon4gr45-needle-router.hf.space")
    evidence = BudgetedRequest(
        state={"job": "test", "worker_history": ["Worker successfully implemented tests"]},
        measurements={"total_bytes": 10, "pair_bytes": 10},
        original_measurements={"total_bytes": 10, "pair_bytes": 10},
    )
    request = AssessmentRequest(
        evidence=evidence,
        questions={
            "meaningful_progress": "Is the worker making meaningful progress?",
            "worker_stuck": "Is the worker stuck?",
        },
        purpose="assessment",
    )
    result = await transport.assess(request, settings=settings)
    assert len(result.probabilities) == 2

    meaningful = next(v for k, v in result.probabilities if k == "meaningful_progress")
    stuck = next(v for k, v in result.probabilities if k == "worker_stuck")

    # We expect meaningful progress to be higher than worker stuck
    assert meaningful > stuck
    await transport.close()


@pytest.mark.asyncio
async def test_live_routing():
    transport = HttpLayaTransport()
    settings = LayaSettings(base_url="https://leon4gr45-needle-router.hf.space")
    evidence = BudgetedRequest(
        state={"job": "test"},
        measurements={"total_bytes": 10, "pair_bytes": 10},
        original_measurements={"total_bytes": 10, "pair_bytes": 10},
    )
    request = AssessmentRequest(
        evidence=evidence,
        questions={
            "responsibility__docs": "Are docs good?",
            "responsibility__security": "Is it secure?",
        },
        purpose="routing",
    )
    result = await transport.assess(request, settings=settings)
    assert len(result.probabilities) == 2
    keys = set(k for k, v in result.probabilities)
    assert keys == {"responsibility__docs", "responsibility__security"}
    await transport.close()
