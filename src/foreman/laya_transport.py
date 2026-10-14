import json
from typing import Any

import httpx

from foreman.laya import (
    LayaSettings,
    LayaTransport,
    LayaTransportError,
    LayaTransportResult,
)
from foreman.providers import AssessmentRequest


class HttpLayaTransport(LayaTransport):
    requires_api_key = False  # Set to false because needle-router didn't require one for inference

    def __init__(
        self, client: httpx.AsyncClient | None = None, max_response_bytes: int = 60_000
    ) -> None:
        self._client = client or httpx.AsyncClient(follow_redirects=False)
        self._owns_client = client is None
        self.max_response_bytes = max_response_bytes

    async def assess(
        self,
        request: AssessmentRequest,
        *,
        settings: LayaSettings,
    ) -> LayaTransportResult:
        base_url = settings.base_url.rstrip("/")
        url = f"{base_url}/v1/system-one"

        headers = {"Content-Type": "application/json"}
        if settings.api_key and settings.api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {settings.api_key.get_secret_value()}"

        payload = {
            "state": request.evidence.state,
            "questions": {
                key: {"type": "noul", "instructions": instructions}
                for key, instructions in request.questions.items()
            },
        }

        try:
            # We enforce max_response_bytes in the LayaDecisionProvider (via ForemanModelError)
            # But we also enforce it during streaming here to prevent OOM

            response_body_bytes = bytearray()

            async with self._client.stream(
                "POST", url, headers=headers, json=payload, timeout=settings.timeout_seconds
            ) as response:
                if response.status_code in {401, 403}:
                    raise LayaTransportError(response.status_code)
                elif response.status_code == 429:
                    raise LayaTransportError(429)
                elif 500 <= response.status_code <= 599:
                    raise LayaTransportError(response.status_code)
                elif response.status_code != 200:
                    raise LayaTransportError(response.status_code)

                async for chunk in response.aiter_bytes():
                    response_body_bytes.extend(chunk)
                    if len(response_body_bytes) > self.max_response_bytes:
                        # Oversized
                        return LayaTransportResult([], len(response_body_bytes))

        except httpx.TimeoutException:
            raise TimeoutError("Laya transport timed out") from None
        except httpx.RequestError:
            raise LayaTransportError() from None

        try:
            parsed = json.loads(response_body_bytes.decode("utf-8"), object_pairs_hook=list)
        except json.JSONDecodeError:
            raise LayaTransportError() from None

        # object_pairs_hook=list turns `{ "a": 1 }` into `[("a", 1)]`
        # We need to extract the results
        # Structure:
        # {
        #   "results": {
        #     "question_key": {
        #       "noul": 0.5
        #     }
        #   }
        # }

        # We need to explicitly check for duplicate keys at every level
        def get_unique_value(pairs: list, target_key: str) -> Any:
            found = False
            val = None
            for k, v in pairs:
                if k == target_key:
                    if found:
                        raise LayaTransportError()  # Duplicate key at this level
                    found = True
                    val = v
            if not found:
                raise LayaTransportError()
            return val

        # Check if the top level is a list of pairs
        if not isinstance(parsed, list):
            raise LayaTransportError()

        # Top-level duplicate check (rejecting any duplicated top level keys)
        top_keys = set()
        for k, _v in parsed:
            if k in top_keys:
                raise LayaTransportError()
            top_keys.add(k)

        results_list = get_unique_value(parsed, "results")
        if not isinstance(results_list, list):
            raise LayaTransportError()

        probabilities: list[tuple[str, Any]] = []

        # We also want to reject if there are duplicate result keys?
        # The user said: "rather than relying on the provider to catch only duplicated result keys."
        # This implies we should reject them *here* raising LayaTransportError.
        result_keys = set()
        for q_key, q_val in results_list:
            if q_key in result_keys:
                raise LayaTransportError()
            result_keys.add(q_key)

            if not isinstance(q_val, list):
                # Malformed inner object
                raise LayaTransportError()

            # Check for duplicates in inner noul object
            inner_keys = set()
            for k, _v in q_val:
                if k in inner_keys:
                    raise LayaTransportError()
                inner_keys.add(k)

            noul_prob = get_unique_value(q_val, "noul")
            probabilities.append((q_key, noul_prob))

        return LayaTransportResult(
            probabilities=probabilities, response_bytes=len(response_body_bytes)
        )

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
