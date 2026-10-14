"""Opt-in Laya semantic provider; live transport awaits an authoritative contract.

The transport types below describe Foreman's internal boundary only. They do
not define an HTTP endpoint, request payload, authentication header or response
schema for the inaccessible Laya service.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from pydantic import SecretStr

from foreman.foreman.base import ForemanModelError
from foreman.providers import AssessmentRequest, AssessmentResult, validate_probabilities
from foreman.request_budget import AssessmentRequestBudget, RequestBudgetError, json_bytes

BLOCKED_PENDING_LAYA_CONTRACT = "BLOCKED_PENDING_LAYA_CONTRACT"


@dataclass(frozen=True)
class LayaSettings:
    base_url: str
    api_key: SecretStr | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0

    def __post_init__(self) -> None:
        url = urlsplit(self.base_url)
        if (
            url.scheme not in {"http", "https"} or not url.hostname
            or url.username is not None or url.password is not None
            or url.query or url.fragment
        ):
            raise ValueError(
                "Laya base URL must be HTTP(S), without credentials, query or fragment"
            )
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("Laya timeout must be finite and positive")


@dataclass(frozen=True)
class LayaTransportResult:
    # A sequence retains duplicate keys so validation cannot silently overwrite them.
    probabilities: Sequence[tuple[str, Any]]
    response_bytes: int


class LayaTransportError(RuntimeError):
    """An adapter failure, optionally carrying an HTTP status, never a response body."""

    def __init__(self, status_code: int | None = None) -> None:
        super().__init__("Laya transport failed")
        self.status_code = status_code


class LayaTransport(Protocol):
    requires_api_key: bool

    async def assess(
        self, request: AssessmentRequest, *, settings: LayaSettings,
    ) -> LayaTransportResult: ...

    async def close(self) -> None: ...


class LayaDecisionProvider:
    def __init__(
        self, *, settings: LayaSettings, transport: LayaTransport | None = None,
        budget: AssessmentRequestBudget | None = None, max_response_bytes: int = 60_000,
    ) -> None:
        if transport is None:
            raise ForemanModelError(
                f"Laya: {BLOCKED_PENDING_LAYA_CONTRACT}; authoritative endpoint, "
                "schema, batching and authentication must be verified before live HTTP is enabled"
            )
        if transport.requires_api_key and (
            settings.api_key is None or not settings.api_key.get_secret_value()
        ):
            raise ForemanModelError("Laya transport requires FOREMAN_LAYA_API_KEY")
        if max_response_bytes <= 0:
            raise ValueError("Laya response limit must be positive")
        self._closed = False
        self.settings = settings
        self.transport = transport
        self.budget = budget or AssessmentRequestBudget()
        self.max_response_bytes = max_response_bytes

    async def assess(self, request: AssessmentRequest) -> AssessmentResult:
        try:
            # Reject over-budget callers, even outside the normal observation adapter.
            self.budget.fit(request.evidence.state, request.questions, compact=False)
            try:
                response = await asyncio.wait_for(
                    self.transport.assess(request, settings=self.settings),
                    timeout=self.settings.timeout_seconds,
                )
            except ForemanModelError:
                # Even a provider-shaped exception from an injected adapter is untrusted.
                raise ForemanModelError("Laya transport failed") from None
            if not isinstance(response, LayaTransportResult):
                raise ForemanModelError("Laya returned an incompatible schema")
            if (
                isinstance(response.response_bytes, bool)
                or not isinstance(response.response_bytes, int)
                or not 0 <= response.response_bytes <= self.max_response_bytes
            ):
                raise ForemanModelError("Laya response exceeds the response budget")
            values = {}
            for key, value in response.probabilities:
                if not isinstance(key, str):
                    raise ForemanModelError("Laya returned an incompatible result key")
                if key in values:
                    raise ForemanModelError("Laya returned duplicate result keys")
                values[key] = value
            values = validate_probabilities(values, request.questions, provider="Laya")
            if json_bytes(values) > self.max_response_bytes:
                raise ForemanModelError("Laya response exceeds the response budget")
            return AssessmentResult(values)
        except TimeoutError:
            raise ForemanModelError("Laya assessment timed out") from None
        except ForemanModelError:
            raise
        except RequestBudgetError:
            raise ForemanModelError("Laya request exceeds the assessment budget") from None
        except LayaTransportError as error:
            status = error.status_code
            if status in {401, 403}:
                message = "Laya authentication failed"
            elif status == 429:
                message = "Laya rate limited (429)"
            elif isinstance(status, int) and 500 <= status <= 599:
                message = f"Laya service failed ({status})"
            else:
                message = "Laya transport failed"
            raise ForemanModelError(message) from None
        except Exception:
            # Adapter exceptions can contain URLs, headers, bodies and secrets.
            raise ForemanModelError("Laya transport or response validation failed") from None

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self.transport.close()
        except Exception:
            raise ForemanModelError("Laya transport close failed") from None
