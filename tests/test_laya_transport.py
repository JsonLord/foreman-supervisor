import pytest

from foreman.laya import LayaSettings, LayaTransportError
from foreman.laya_transport import HttpLayaTransport
from foreman.providers import AssessmentRequest
from foreman.request_budget import BudgetedRequest


@pytest.mark.asyncio
async def test_oversized_streaming_response():
    class MockResponse:
        status_code = 200

        async def aiter_bytes(self):
            # yield 10 chunks of 10000 bytes
            for _ in range(10):
                yield b"a" * 10000

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockClient:
        def stream(self, method, url, **kwargs):
            return MockResponse()

        async def aclose(self):
            pass

    transport = HttpLayaTransport(client=MockClient())
    settings = LayaSettings(base_url="https://example.com")
    evidence = BudgetedRequest(
        state={},
        measurements={"total_bytes": 0, "pair_bytes": 0},
        original_measurements={"total_bytes": 0, "pair_bytes": 0},
    )
    request = AssessmentRequest(evidence=evidence, questions={"test": "test"})

    # We set max_response_bytes to 60000, so it should stop reading at 70000 bytes.
    # It returns LayaTransportResult with len(probabilities) = 0 and response_bytes > max_bytes
    # which will be rejected by LayaDecisionProvider.

    result = await transport.assess(request, settings=settings)
    assert len(result.probabilities) == 0
    assert result.response_bytes == 70000


@pytest.mark.asyncio
async def test_duplicate_key_handling():
    class MockResponse:
        status_code = 200

        async def aiter_bytes(self):
            # JSON with duplicate answer keys
            json_str = '{"results": {"key1": {"noul": 0.5}, "key1": {"noul": 0.9}}}'
            yield json_str.encode("utf-8")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockClient:
        def stream(self, method, url, **kwargs):
            return MockResponse()

        async def aclose(self):
            pass

    transport = HttpLayaTransport(client=MockClient())
    settings = LayaSettings(base_url="https://example.com")
    evidence = BudgetedRequest(
        state={},
        measurements={"total_bytes": 0, "pair_bytes": 0},
        original_measurements={"total_bytes": 0, "pair_bytes": 0},
    )
    request = AssessmentRequest(evidence=evidence, questions={"key1": "test"})

    with pytest.raises(LayaTransportError):
        await transport.assess(request, settings=settings)


@pytest.mark.asyncio
async def test_error_mapping():
    class MockResponse:
        def __init__(self, status):
            self.status_code = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    class MockClient:
        def stream(self, method, url, **kwargs):
            return MockResponse(401)

        async def aclose(self):
            pass

    transport = HttpLayaTransport(client=MockClient())
    settings = LayaSettings(base_url="https://example.com")
    evidence = BudgetedRequest(
        state={},
        measurements={"total_bytes": 0, "pair_bytes": 0},
        original_measurements={"total_bytes": 0, "pair_bytes": 0},
    )
    request = AssessmentRequest(evidence=evidence, questions={"test": "test"})

    with pytest.raises(LayaTransportError) as exc:
        await transport.assess(request, settings=settings)
    assert exc.value.status_code == 401
