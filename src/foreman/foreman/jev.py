from __future__ import annotations

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import Any

from foreman.foreman.base import ForemanModelError
from foreman.foreman.provider import ProviderForemanModel
from foreman.jev_usage import JevUsageRecorder
from foreman.models import ForemanResult
from foreman.providers import JevDecisionProvider
from foreman.request_budget import JevRequestBudget
from foreman.responsibilities import Check


def normalize_check_results(
    values: Mapping[str, Any],
    checks: Sequence[Check],
) -> ForemanResult:
    """Validate Jev probabilities and group them by owning responsibility."""

    expected = {check.key for check in checks}
    missing = expected - set(values)
    if missing:
        raise ForemanModelError(f"Jev response omitted: {', '.join(sorted(missing))}")

    grouped: dict[str, dict[str, float]] = {}
    for check in checks:
        raw = values[check.key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ForemanModelError(f"Jev answer {check.key!r} is not numeric")
        value = float(raw)
        if not isfinite(value):
            raise ForemanModelError(f"Jev answer {check.key!r} is not finite")
        grouped.setdefault(check.responsibility_id, {})[check.check_id] = min(1.0, max(0.0, value))
    return ForemanResult(checks=grouped)


def parse_jev_response(response: Any, checks: Sequence[Check]) -> ForemanResult:
    """Translate official SDK response types (or their test doubles) into our model."""

    values: dict[str, Any] = {}
    nouls = getattr(response, "nouls", None)
    if nouls is not None:
        for check in checks:
            answer = nouls.get(check.key)
            if answer is not None:
                values[check.key] = getattr(answer, "noul", None)
    elif isinstance(response, Mapping):
        answers = response.get("answers", response)
        if isinstance(answers, Mapping):
            for check in checks:
                answer = answers.get(check.key)
                if isinstance(answer, Mapping):
                    values[check.key] = answer.get("noul")
                elif answer is not None:
                    values[check.key] = answer
    return normalize_check_results(values, checks)


class JevForemanModel(ProviderForemanModel):
    """Backward-compatible Jev model using the generic semantic boundary."""

    def __init__(
        self,
        *,
        client: Any | None = None,
        timeout_seconds: float = 10.0,
        model: str = "jev-latest",
        budget: JevRequestBudget | None = None,
        usage_recorder: JevUsageRecorder | None = None,
    ) -> None:
        super().__init__(
            JevDecisionProvider(
                client=client,
                timeout_seconds=timeout_seconds,
                model=model,
                usage_recorder=usage_recorder,
            ),
            budget=budget,
        )
        self.timeout_seconds = timeout_seconds
        self.model = model
        self.usage_recorder = self.provider.usage_recorder
