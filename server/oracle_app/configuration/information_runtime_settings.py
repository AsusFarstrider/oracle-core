from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .domain_models import (
    InformationConfiguration,
    NewsSource,
    OpenClawHttpProvider,
    OpenClawMockProvider,
    OpenClawSshCliProvider,
    RssNewsProvider,
    StaticFactsProvider,
    WikipediaFactsProvider,
)
from .effective import EffectiveConfig


FactsProviderConfiguration = StaticFactsProvider | WikipediaFactsProvider
SuggestionsProviderConfiguration = (
    OpenClawHttpProvider | OpenClawSshCliProvider | OpenClawMockProvider
)


@dataclass(frozen=True)
class FactsRuntimeSettings:
    enabled: bool
    provider_id: str | None
    provider: FactsProviderConfiguration | None
    summarizer_enabled: bool
    acknowledgement_enabled: bool
    timeout_seconds: int
    cache_enabled: bool
    cache_ttl_seconds: int


@dataclass(frozen=True)
class NewsSourceRuntimeSettings:
    source: NewsSource
    provider: RssNewsProvider


@dataclass(frozen=True)
class NewsRuntimeSettings:
    enabled: bool
    provider_id: str | None
    provider: RssNewsProvider | None
    sources: Mapping[str, NewsSourceRuntimeSettings]
    resolution_terms: Mapping[str, str]
    max_headlines: int
    fresh_seconds: int
    stale_if_error_seconds: int

    def resolve_source_id(self, value: str | None) -> str | None:
        normalized = _normalized_term(value or "")
        return self.resolution_terms.get(normalized) if normalized else None


@dataclass(frozen=True)
class SuggestionsRuntimeSettings:
    enabled: bool
    provider_id: str | None
    provider: SuggestionsProviderConfiguration | None
    max_suggestions: int
    resolved_base_url: str | None = field(default=None, repr=False)
    resolved_password: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class InformationRuntimeSettings:
    """Frozen execution settings for the optional information domain role."""

    activation_generation_id: str
    config_generation_id: str
    secret_generation_id: str
    selection_operation_id: str
    selection_revision: int
    config_revision: str
    facts: FactsRuntimeSettings
    news: NewsRuntimeSettings
    suggestions: SuggestionsRuntimeSettings

    @classmethod
    def from_effective_config(cls, effective: EffectiveConfig) -> InformationRuntimeSettings:
        role = effective.role("domains/information.yaml")
        if not isinstance(role, InformationConfiguration):
            raise TypeError("Effective information role does not use the executable information schema.")

        facts_provider = None
        facts_provider_id = None
        if role.facts.enabled:
            facts_provider_id = role.facts.provider
            if facts_provider_id is None:
                raise ValueError("Enabled canonical facts has no selected provider.")
            facts_provider = role.facts.providers[facts_provider_id]

        news_provider = None
        news_provider_id = None
        news_sources: dict[str, NewsSourceRuntimeSettings] = {}
        news_terms: dict[str, str] = {}
        if role.news.enabled:
            news_provider_id = role.news.provider
            if news_provider_id is None:
                raise ValueError("Enabled canonical news has no selected provider.")
            news_provider = role.news.providers[news_provider_id]
            for source in role.news.sources:
                source_provider = role.news.providers[source.provider]
                news_sources[source.id] = NewsSourceRuntimeSettings(source, source_provider)
                for term in (source.id, source.display_name, *source.aliases):
                    news_terms[_normalized_term(term)] = source.id

        suggestions_provider = None
        suggestions_provider_id = None
        resolved_base_url = None
        resolved_password = None
        if role.suggestions.enabled:
            suggestions_provider_id = role.suggestions.provider
            if suggestions_provider_id is None:
                raise ValueError("Enabled canonical suggestions has no selected provider.")
            suggestions_provider = role.suggestions.providers[suggestions_provider_id]
            if isinstance(suggestions_provider, OpenClawHttpProvider):
                resolved_base_url = suggestions_provider.base_url
                if suggestions_provider.base_url_secret is not None:
                    resolved_base_url = effective.secrets.resolve(
                        suggestions_provider.base_url_secret
                    )
                if resolved_base_url is None:
                    raise ValueError("Enabled canonical HTTP suggestions lacks its endpoint value.")
            elif isinstance(suggestions_provider, OpenClawSshCliProvider):
                if suggestions_provider.password_secret is not None:
                    resolved_password = effective.secrets.resolve(
                        suggestions_provider.password_secret
                    )
                    if resolved_password is None:
                        raise ValueError("Enabled canonical SSH suggestions lacks its password value.")

        return cls(
            activation_generation_id=effective.activation_generation_id,
            config_generation_id=effective.config_generation_id,
            secret_generation_id=effective.secret_generation_id,
            selection_operation_id=effective.selection_operation_id,
            selection_revision=effective.selection_revision,
            config_revision=effective.config_revision,
            facts=FactsRuntimeSettings(
                enabled=role.facts.enabled,
                provider_id=facts_provider_id,
                provider=facts_provider,
                summarizer_enabled=role.facts.summarizer_enabled,
                acknowledgement_enabled=role.facts.acknowledgement_enabled,
                timeout_seconds=role.facts.timeout_seconds,
                cache_enabled=role.facts.cache_enabled,
                cache_ttl_seconds=role.facts.cache_ttl_seconds,
            ),
            news=NewsRuntimeSettings(
                enabled=role.news.enabled,
                provider_id=news_provider_id,
                provider=news_provider,
                sources=MappingProxyType(news_sources),
                resolution_terms=MappingProxyType(news_terms),
                max_headlines=role.news.max_headlines,
                fresh_seconds=role.news.fresh_seconds,
                stale_if_error_seconds=role.news.stale_if_error_seconds,
            ),
            suggestions=SuggestionsRuntimeSettings(
                enabled=role.suggestions.enabled,
                provider_id=suggestions_provider_id,
                provider=suggestions_provider,
                max_suggestions=role.suggestions.max_suggestions,
                resolved_base_url=resolved_base_url,
                resolved_password=resolved_password,
            ),
        )


def _normalized_term(value: str) -> str:
    return " ".join(value.casefold().split())
