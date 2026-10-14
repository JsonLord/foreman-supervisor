from __future__ import annotations

import asyncio
from collections.abc import Sequence

from foreman.evidence import evidence_for
from foreman.foreman.base import ForemanModelError
from foreman.models import ForemanResult
from foreman.observation import FactoryObservation
from foreman.providers import AssessmentRequest, DecisionProvider
from foreman.request_budget import AssessmentRequestBudget, RequestBudgetError
from foreman.responsibilities import Check


class ProviderForemanModel:
    """Translate existing checks and bounded observations to semantic requests."""

    def __init__(
        self,
        provider: DecisionProvider,
        *,
        budget: AssessmentRequestBudget | None = None,
    ) -> None:
        self.provider = provider
        self.budget = budget or AssessmentRequestBudget()

    async def assess(
        self,
        observation: FactoryObservation,
        checks: Sequence[Check],
    ) -> ForemanResult:
        groups: dict[tuple[str, ...], list[Check]] = {}
        for check in checks:
            groups.setdefault(evidence_for(check), []).append(check)
        plans = []
        try:
            for evidence, group in groups.items():
                questions = {check.key: check.instructions for check in group}
                fitted = self.budget.fit(observation.state_for(evidence), questions)
                plans.append((AssessmentRequest(fitted, questions), group))
        except RequestBudgetError as error:
            raise ForemanModelError(str(error)) from error

        async def assess_group(request: AssessmentRequest, group: Sequence[Check]):
            result = await self.provider.assess(request)
            return {check.key: result.probabilities[check.key] for check in group}

        results = await asyncio.gather(*(assess_group(request, group) for request, group in plans))
        merged = {}
        for values, (_, group) in zip(results, plans, strict=True):
            for check in group:
                merged.setdefault(check.responsibility_id, {})[check.check_id] = values[check.key]
        return ForemanResult(checks=merged)

    async def close(self) -> None:
        await self.provider.close()
