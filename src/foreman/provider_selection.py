from __future__ import annotations

import os
from collections.abc import Callable

from pydantic import SecretStr

from foreman.config import FactoryConfig
from foreman.foreman.base import ForemanModel, ForemanModelError
from foreman.foreman.jev import JevForemanModel
from foreman.foreman.provider import ProviderForemanModel
from foreman.jev_usage import JevUsageRecorder
from foreman.laya import LayaDecisionProvider, LayaSettings, LayaTransport
from foreman.request_budget import AssessmentRequestBudget
from foreman.routing import (
    JevResponsibilityRouter,
    ProviderResponsibilityRouter,
    ResponsibilityRouter,
)


def validate_provider_configuration(config: FactoryConfig) -> None:
    if config.decision_provider == "jev" and not os.getenv("TYPESAFE_API_KEY"):
        raise ForemanModelError(
            "Jev: Missing TYPESAFE_API_KEY. Copy .env.example to .env "
            "and add your TypeSafe API key."
        )
    if config.decision_provider == "laya" and not config.laya_base_url:
        raise ForemanModelError("Laya: Missing FOREMAN_LAYA_BASE_URL")


def semantic_components(
    config: FactoryConfig,
    *,
    budget: AssessmentRequestBudget,
    usage_recorder: JevUsageRecorder,
    laya_transport: LayaTransport | None = None,
    jev_model_factory: Callable[..., ForemanModel] = JevForemanModel,
    jev_router_factory: Callable[..., ResponsibilityRouter] = JevResponsibilityRouter,
) -> tuple[ForemanModel, ResponsibilityRouter]:
    if config.decision_provider == "jev":
        return (
            jev_model_factory(
                timeout_seconds=config.jev_timeout_seconds,
                budget=budget,
                usage_recorder=usage_recorder,
            ),
            jev_router_factory(
                timeout_seconds=config.jev_timeout_seconds,
                budget=budget,
                usage_recorder=usage_recorder,
            ),
        )
    if not config.laya_base_url:
        raise ForemanModelError("Laya: Missing FOREMAN_LAYA_BASE_URL")
    key = os.getenv("FOREMAN_LAYA_API_KEY")
    try:
        settings = LayaSettings(
            base_url=config.laya_base_url,
            api_key=SecretStr(key) if key else None,
            timeout_seconds=config.laya_timeout_seconds,
        )
    except ValueError:
        raise ForemanModelError("Laya: invalid base URL or timeout configuration") from None
    if laya_transport is None:
        from foreman.laya_transport import HttpLayaTransport

        laya_transport = HttpLayaTransport()
    provider = LayaDecisionProvider(settings=settings, transport=laya_transport, budget=budget)
    return ProviderForemanModel(provider, budget=budget), ProviderResponsibilityRouter(
        provider,
        budget=budget,
    )
