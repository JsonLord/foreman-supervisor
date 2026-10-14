"""The bounded, batched semantic boundary shared by assessment and routing.

This is an internal contract, not a claim about Laya's wire format.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal, Protocol

from foreman.errors import ForemanModelError
from foreman.jev_usage import JevUsageRecorder
from foreman.request_budget import BudgetedRequest


@dataclass(frozen=True)
class AssessmentRequest:
    evidence: BudgetedRequest
    questions: Mapping[str, str]
    purpose: Literal["assessment", "routing"] = "assessment"


@dataclass(frozen=True)
class AssessmentResult:
    probabilities: Mapping[str, float]


class DecisionProvider(Protocol):
    async def assess(self, request: AssessmentRequest) -> AssessmentResult: ...

    async def close(self) -> None: ...


def validate_probabilities(
    values: Mapping[str, Any],
    questions: Mapping[str, str],
    *,
    provider: str,
    clamp: bool = False,
) -> dict[str, float]:
    missing = set(questions) - set(values)
    unknown = set(values) - set(questions)
    if missing:
        raise ForemanModelError(f"{provider} response omitted: {', '.join(sorted(missing))}")
    if unknown:
        raise ForemanModelError(f"{provider} response has unexpected result keys")
    result = {}
    for key, raw in values.items():
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForemanModelError(f"{provider} answer {key!r} is not numeric")
        value = float(raw)
        if not isfinite(value):
            raise ForemanModelError(f"{provider} answer {key!r} is not finite")
        if not clamp and not 0.0 <= value <= 1.0:
            raise ForemanModelError(f"{provider} answer {key!r} is outside [0, 1]")
        result[key] = min(1.0, max(0.0, value)) if clamp else value
    return result


class JevDecisionProvider:
    """Keep SDK Noul questions, retries, budgets and usage recording unchanged."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        timeout_seconds: float = 10.0,
        model: str = "jev-latest",
        usage_recorder: JevUsageRecorder | None = None,
    ) -> None:
        self._client = client
        self._owns_client = client is None
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.usage_recorder = usage_recorder or JevUsageRecorder()

    def _make_client(self) -> Any:
        try:
            from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
        except ImportError as error:
            raise ForemanModelError(
                "typesafe-sdk is not installed; install the project dependencies"
            ) from error
        return AsyncTypeSafeClient(
            model=self.model,
            timeout=self.timeout_seconds,
            retry=RetryPolicy(
                max_retries=2,
                http_statuses={429, 500, 502, 503, 504},
                respect_retry_after=True,
                timeout=self.timeout_seconds,
            ),
        )

    async def assess(self, request: AssessmentRequest) -> AssessmentResult:
        if "evidence_budget" in request.evidence.state:
            logging.getLogger(__name__).info(
                "Compacted Jev evidence: %s",
                request.evidence.measurements,
            )
        try:
            from typesafe_sdk import Noul
        except ImportError as error:
            if self._client is None:
                raise ForemanModelError("typesafe-sdk is not installed") from error

            class Noul:  # type: ignore[no-redef]
                def __init__(self, *, instructions: str) -> None:
                    self.instructions = instructions

        if self._client is None:
            self._client = self._make_client()
        try:
            response = await asyncio.wait_for(
                self._client.system_one(
                    state=request.evidence.state,
                    questions={
                        key: Noul(instructions=instructions)
                        for key, instructions in request.questions.items()
                    },
                    model=self.model,
                    timeout=self.timeout_seconds,
                ),
                timeout=self.timeout_seconds + 0.5,
            )
            self.usage_recorder.record(
                response,
                request.evidence,
                purpose=request.purpose,
                model=self.model,
            )
            values = {}
            nouls = getattr(response, "nouls", None)
            if nouls is not None:
                for key in request.questions:
                    answer = nouls.get(key)
                    if answer is not None:
                        values[key] = getattr(answer, "noul", None)
            elif isinstance(response, Mapping):
                answers = response.get("answers", response)
                if isinstance(answers, Mapping):
                    for key in request.questions:
                        answer = answers.get(key)
                        if isinstance(answer, Mapping):
                            values[key] = answer.get("noul")
                        elif answer is not None:
                            values[key] = answer
            return AssessmentResult(
                validate_probabilities(
                    values,
                    request.questions,
                    provider="Jev",
                    clamp=True,
                )
            )
        except TimeoutError as error:
            raise ForemanModelError(f"Jev {request.purpose} timed out") from error
        except ForemanModelError:
            raise
        except Exception as error:
            raise ForemanModelError(
                f"Jev {request.purpose} failed: {type(error).__name__}: {error}"
            ) from error

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "aclose", None)
            if close is not None:
                await close()
        self._client = None
