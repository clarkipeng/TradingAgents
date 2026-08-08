"""Stable compatibility contracts for global-event collection.

Collection identities describe the evidence stream, not the process that
produced it.  Builds, database drivers, alerts, and deployment code therefore
cannot invalidate otherwise compatible evidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from types import MappingProxyType
from typing import Any

from tradingagents.dataflows import media_sources

# Version tokens in the manifests are compatibility migrations: bump the
# relevant token when its named behavior changes, never for an implementation
# or operational refactor.
COLLECTOR_POLICY = "global-only-editorial-and-trend-reaction-v2"
COLLECTOR_COMPATIBILITY_PRECEDENCE = (
    "current-then-newest-prior-deployment-to-oldest-v2"
)

_DISCOVERY_STOPWORDS = tuple(sorted({
    "a", "about", "according", "after", "against", "all", "amid", "an", "and",
    "are", "as", "at", "be", "before", "but", "by", "can", "confirms", "could",
    "for", "from", "has", "have", "how", "in", "into", "is", "it", "its", "may",
    "more", "new", "not", "of", "on", "or", "over", "report", "reports", "says",
    "than", "that", "the", "their", "this", "to", "up", "was", "what", "when",
    "where", "which", "who", "why", "will", "with", "would",
}))
_DISCOVERY_GENERIC_CAPITALIZED_TERMS = tuple(sorted({
    "Analysis", "Breaking", "Exclusive", "Explainer", "Here", "How", "Live", "My",
    "New", "Opinion", "The", "This", "Update", "What", "When", "Why", "Worldwide",
}))

# All literals that can change ranked topic discovery live here. Nested values
# are immutable, while ``discovery_policy_manifest`` returns a plain JSON-ready
# projection for the collection identity.
DISCOVERY_POLICY = MappingProxyType({
    "version": "ranked-strategic-technology-topic-discovery-v7-explicit-domain",
    "inputs": MappingProxyType({
        "ranked_feed_limit": 20,
        "categories": ("world", "business", "technology", "general"),
        "exclude_low_information": True,
        "exclude_company_authored": True,
        "low_information_pattern": (
            r"\b(best|deal|discount|guide|hands[- ]on|how to|review|rumor|"
            r"versus|vs\.?|wishlist)\b"
        ),
        "low_information_flags": int(re.IGNORECASE | re.UNICODE),
    }),
    "normalization": MappingProxyType({
        "publisher_suffix_pattern": r"\s+-\s+[^-]{2,80}$",
        "word_pattern": r"[a-z0-9]+",
        "case": "lower",
        "stopwords": _DISCOVERY_STOPWORDS,
        "semantic_min_chars": 3,
        "semantic_short_allowlist": ("ai", "uk", "us"),
        "semantic_prefix_aliases": (("launch", "launch"),),
        "semantic_exact_aliases": (("worldwide", "world"),),
        "plural_suffix": "s",
        "plural_min_chars": 5,
    }),
    "story_grouping": MappingProxyType({
        "primary_jaccard_min": 0.58,
        "secondary_overlap_min": 3,
        "secondary_jaccard_min": 0.38,
        "resolution": "first-matching-input-group",
    }),
    "trend_matching": MappingProxyType({
        "headline_scope": "all-grouped-lineage-titles-v1",
        "leading_chars_to_strip": "#",
        "meaningful_min_chars": 4,
        "single_term_required_overlap": 1,
        "multiple_term_required_overlap": 2,
    }),
    "query": MappingProxyType({
        "token_pattern": (
            r"[A-Za-z][A-Za-z0-9&.'’+-]*|\d[A-Za-z0-9.-]*"
        ),
        "version_token_pattern": (
            r"(?:[A-Za-z]\d[A-Za-z0-9.-]*|\d[A-Za-z0-9.-]*)"
        ),
        "generic_capitalized_terms": _DISCOVERY_GENERIC_CAPITALIZED_TERMS,
        "capitalization": "initial-or-internal-uppercase-v1",
        "distinctive_token": "digit-or-internal-uppercase-or-single-uppercase-v1",
        "long_run_min_words": 4,
        "long_run_word_cap": 2,
        "phrase_word_cap": 3,
        "qualified_phrase_min_words": 1,
        "anchor_order": (
            "word-count-desc",
            "position-asc",
            "character-count-desc",
            "phrase-asc",
        ),
        "anchor_cap": 2,
        "deferred_short_uppercase_max_chars": 2,
        "signal_min_chars": 4,
        "query_part_cap": 3,
        "fallback_signal_cap": 3,
        "phrase_quote": '"',
        "max_query_chars": 400,
        "preferred_signal_order": (
            "domain-then-event-then-context-matches-in-headline-v2"
        ),
        "preferred_signal_anchor_deduplication": (
            "casefolded-token-set-containment-v2"
        ),
        "event_signal_pattern": (
            r"\b(?:exports?|tariffs?|ceasefires?|sanctions?|shortages?|"
            r"breaches?|cyberattacks?|outages?|launch(?:es|ed|ing)?|"
            r"releas(?:e|es|ed|ing)|debut(?:s|ed)?|unveil(?:s|ed|ing)?)\b"
        ),
        "query_identity": "unicode-casefold-whitespace-collapse-v1",
    }),
    "ranking": MappingProxyType({
        "default_category": "general",
        "default_region": "unknown",
        "missing_created_utc": 0,
        "missing_rank": 10_000,
        "lineage_fields": (
            "external_id", "title", "body", "created_utc", "publisher",
            "metadata", "category", "region", "rank",
        ),
        "canonical_role_order": (
            "strategic_technology", "major_global_impact", "any",
        ),
        "canonical_headline_order": (
            "strategic-context-desc", "rank-asc", "created-utc-desc",
            "topic-key-asc", "external-id-asc",
        ),
    }),
    "prioritization": MappingProxyType({
        "version": "strategic-technology-two-plus-global-v3-explicit-domain",
        "classification_text": "each-grouped-lineage-title",
        "strategic_domain_patterns": (
            (
                "artificial_intelligence",
                r"\b(?:artificial intelligence|machine learning|large language "
                r"models?|foundation models?|frontier models?|multimodal models?|"
                r"generative ai|ai models?|ai systems?|ai agents?|agentic ai|"
                r"language models?|reasoning models?|model training|model inference|"
                r"neural networks?|llms?)\b",
            ),
            (
                "compute_infrastructure",
                r"\b(?:compute clusters?|ai accelerators?|graphics processing units?|"
                r"gpus?|data cent(?:er|re)s?|cloud infrastructure)\b",
            ),
            (
                "semiconductors",
                r"\b(?:ai chips?|semiconductors?|chipmakers?|chipmaking|chip fabrication|"
                r"microchips?|microprocessors?|integrated circuits?|memory chips?|"
                r"foundr(?:y|ies)|fabs?|wafers?|silicon dies?|lithograph(?:y|ic)|euv|"
                r"high bandwidth memory|hbm|dram|nand|advanced packaging)\b",
            ),
            (
                "cybersecurity",
                r"\b(?:cybersecurity|cyber attacks?|cyberattacks?|ransomware|malware|"
                r"data breaches?|network security)\b",
            ),
            (
                "telecommunications",
                r"\b(?:telecommunications?|telecoms?|5g|6g|fiber optic|"
                r"undersea cables?|submarine cables?)\b",
            ),
            (
                "robotics",
                r"\b(?:robotics?|humanoid robots?|industrial robots?|autonomous "
                r"systems?)\b",
            ),
            (
                "quantum",
                r"\b(?:quantum comput(?:ers?|ing)|quantum networks?|quantum chips?)\b",
            ),
            (
                "space_infrastructure",
                r"\b(?:communications? satellites?|weather satellites?|satellite "
                r"networks?|satellite launches?|spacecraft|space rockets?|"
                r"orbital infrastructure|launch vehicles?)\b",
            ),
            (
                "critical_power",
                r"\b(?:power grids?|electric grids?|electricity transmission|"
                r"data cent(?:er|re) power)\b",
            ),
        ),
        "ambiguous_semiconductor_pattern": r"\bchips?\b",
        "ambiguous_semiconductor_context_pattern": (
            r"\b(?:ai|artificial intelligence|computers?|servers?|memory|processors?|"
            r"semiconductors?|silicon|wafers?|fabs?|foundr(?:y|ies)|electronics?|"
            r"rare earths?|export controls?|supply chains?)\b"
        ),
        "model_identifier_pattern": (
            r"\b[A-Z]{3,}(?:-[A-Z0-9]+|\d[A-Z0-9.-]*)\b"
        ),
        "material_event_pattern": (
            r"\b(?:launch(?:es|ed|ing)?|releas(?:e|es|ed|ing)|debut(?:s|ed)?|"
            r"unveil(?:s|ed|ing)?|announc(?:e|es|ed|ing)|breakthroughs?|research|"
            r"train(?:s|ed|ing)?|inference|invest(?:s|ed|ing|ment|ments)|funding|"
            r"develop(?:s|ed|ing|ment|ments)?|deploy(?:s|ed|ing|ment|ments)?|"
            r"expand(?:s|ed|ing|ion)?|surge(?:s|d|ing)?|accelerat(?:e|es|ed|ing|ion)|"
            r"ris(?:e|es|ing)|rose|fall(?:s|ing)?|fell|declin(?:e|es|ed|ing)|"
            r"rollouts?|build(?:s|ing)?|construction|capacity|factor(?:y|ies)|plants?|"
            r"produc(?:e|es|ed|ing|tion)|manufactur(?:e|es|ed|ing)|shortages?|"
            r"supply chains?|shipments?|export controls?|sanctions?|regulat(?:e|es|ed|ion|ions)|"
            r"legislation|subsid(?:y|ies)|industrial policy|bans?|"
            r"restrict(?:s|ed|ing|ion|ions)?|"
            r"breaches?|cyberattacks?|attacks?|outages?|"
            r"disrupt(?:s|ed|ing|ion|ions)?|adoption|demand)\b"
        ),
        "strategic_context_pattern": (
            r"\b(?:china|chinese|taiwan|taiwanese|south korea|korea|korean|japan|"
            r"japanese|india|indian|united states|u\.s\.|europe|european union|eu|"
            r"netherlands|dutch|export controls?|export curbs?|sanctions?|"
            r"supply chains?|trade restrictions?|"
            r"industrial policy|national security|power grids?|electricity)\b"
        ),
        "major_global_impact_pattern": (
            r"\b(?:elections?|government|administration|congress|parliament|courts?|"
            r"policy|presidents?|presidential|prime ministers?|heads? of state|"
            r"political leaders?|"
            r"executive orders?|summits?|negotiations?|tariffs?|trade|sanctions?|"
            r"war|conflict|ceasefires?|diplomacy|treat(?:y|ies)|military|defen[cs]e|"
            r"nuclear|missiles?|central banks?|interest rates?|inflation|recession|"
            r"unemployment|jobs?|econom(?:y|ies|ic)|gdp|debt|currenc(?:y|ies)|"
            r"energy|oil|gas|climate|wildfires?|earthquakes?|floods?|"
            r"hurricanes?|public health|pandemics?|protests?)\b"
        ),
        "consumer_gadget_pattern": (
            r"\b(?:smartphones?|phones?|handsets?|tablets?|wearables?|smartwatches?|"
            r"earbuds?|headsets?|televisions?|tvs?|cameras?|consoles?|gaming|"
            r"laptops?|foldables?|cars?|vehicles?|automobiles?|suvs?|trucks?|"
            r"products?|devices?|appliances?)\b"
        ),
        "consumer_strategic_override_pattern": (
            r"\b(?:semiconductors?|chips?|chipmakers?|chip fabrication|microchips?|"
            r"microprocessors?|integrated circuits?|memory chips?|foundr(?:y|ies)|"
            r"fabs?|wafers?|lithograph(?:y|ic)|euv|hbm|dram|nand|advanced packaging|export "
            r"controls?|sanctions?|supply chains?|shortages?|large language models?|"
            r"foundation models?|frontier models?|reasoning models?|model training|"
            r"model inference|data cent(?:er|re)s?|ai accelerators?)\b"
        ),
        "pattern_flags": int(re.IGNORECASE | re.UNICODE),
    }),
    "allocation": MappingProxyType({
        "target_roles": (
            "strategic_technology",
            "strategic_technology",
            "major_global",
        ),
        "fallback_role": "general_fallback",
        "major_global_categories": ("world", "general"),
        "general_category_requires_major_global_impact": True,
        "major_global_excludes_strategic_technology": True,
        "exclude_consumer_only_from_fallback": True,
        "fallback_categories": ("world", "business"),
        "fallback_allows_strategic_technology": True,
        "fallback_allows_major_global_general": True,
        "require_distinct_queries": True,
        "slot_topic_prefix": "trend_slot_",
        "strategic_candidate_order": (
            "independent-publisher-count-desc",
            "strategic-context-desc",
            "cross-region-count-desc",
            "trend-match-desc",
            "best-rank-asc",
            "new-subdomain-count-desc",
            "created-utc-desc",
            "topic-key-asc",
            "query-asc",
        ),
        "major_global_candidate_order": (
            "independent-publisher-count-desc",
            "cross-region-count-desc",
            "trend-match-desc",
            "best-rank-asc",
            "created-utc-desc",
            "topic-key-asc",
            "query-asc",
        ),
        "fallback_candidate_order": (
            "independent-publisher-count-desc",
            "cross-region-count-desc",
            "trend-match-desc",
            "best-rank-asc",
            "created-utc-desc",
            "topic-key-asc",
            "query-asc",
        ),
        "search_request_grouping": "exact-query-with-sorted-label-union-v1",
        "search_request_order": "query-asc",
    }),
    "audit_record": MappingProxyType({
        "version": "content-addressed-full-input-and-decision-v2",
        "headline_fields": (
            "external_id", "title", "created_utc", "publisher",
            "category", "region", "rank",
        ),
        "headline_metadata_fields": ("publisher_domain",),
        "trend_fields": ("name", "tweet_count"),
        "selected_topic_fields": (
            "topic", "category", "query", "external_id", "title",
            "selection_role", "strategic_technology", "strategic_subdomains",
            "strategic_context", "major_global_impact", "consumer_only",
        ),
    }),
})

_PROTOCOL_ID = re.compile(r"protocol_[0-9a-f]{24}")
_COLLECTOR_ID = re.compile(r"collector_[0-9a-f]{24}")
_IDENTITY_HISTORY_KEYS = {
    "protocol_id",
    "collector_semantics_id",
    "x_daily_static_slots",
    "x_daily_max_dynamic_slots",
    "reason",
}


def x_daily_static_slots(evidence: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Return the ordered non-search slots in one daily X collection cycle."""
    return tuple(
        ("xtrend", f"woeid:{int(woeid)}")
        for woeid in evidence["x_trend_woeids"]
    ) + (("trendnews", "ranked-global-discovery"),)


def x_daily_cycle_shape(
    evidence: Mapping[str, Any],
) -> tuple[tuple[tuple[str, str], ...], int]:
    """Return the immutable slot shape that identifies one daily X cycle."""
    return (
        x_daily_static_slots(evidence),
        int(evidence["max_x_search_requests_per_utc_day"]),
    )


def validated_collector_identity_history(
    entries: object,
    *,
    current_identity: tuple[str, str],
    current_x_daily_cycle_shape: tuple[tuple[tuple[str, str], ...], int],
) -> tuple[Mapping[str, Any], ...]:
    """Validate an append-only identity ledger whose last row is current."""
    if not isinstance(entries, list) or not entries:
        raise ValueError("collector identity history must be a non-empty list")

    seen: set[tuple[str, str]] = set()
    validated: list[Mapping[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != _IDENTITY_HISTORY_KEYS:
            raise ValueError("collector identity history row has an invalid shape")
        protocol_id = entry["protocol_id"]
        collector_id = entry["collector_semantics_id"]
        static_slots = entry["x_daily_static_slots"]
        max_dynamic_slots = entry["x_daily_max_dynamic_slots"]
        reason = entry["reason"]
        if not isinstance(protocol_id, str) or _PROTOCOL_ID.fullmatch(protocol_id) is None:
            raise ValueError("collector history protocol ID is invalid")
        if not isinstance(collector_id, str) or _COLLECTOR_ID.fullmatch(collector_id) is None:
            raise ValueError("collector history semantics ID is invalid")
        if (
            not isinstance(static_slots, (list, tuple))
            or not static_slots
            or any(
                not isinstance(slot, (list, tuple))
                or len(slot) != 2
                or not all(isinstance(value, str) and value for value in slot)
                for slot in static_slots
            )
        ):
            raise ValueError("collector history X static slots are invalid")
        frozen_slots = tuple((slot[0], slot[1]) for slot in static_slots)
        if len(frozen_slots) != len(set(frozen_slots)):
            raise ValueError("collector history X static slots must be unique")
        if (
            isinstance(max_dynamic_slots, bool)
            or not isinstance(max_dynamic_slots, int)
            or max_dynamic_slots < 0
        ):
            raise ValueError("collector history X dynamic-slot cap is invalid")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("collector history row requires a reason")

        pair = (protocol_id, collector_id)
        if pair in seen:
            raise ValueError("collector identity history must be unique")
        seen.add(pair)
        validated.append(MappingProxyType({
            "protocol_id": protocol_id,
            "collector_semantics_id": collector_id,
            "x_daily_static_slots": frozen_slots,
            "x_daily_max_dynamic_slots": max_dynamic_slots,
            "reason": reason,
        }))

    current = validated[-1]
    current_shape = (
        current["x_daily_static_slots"],
        current["x_daily_max_dynamic_slots"],
    )
    if (
        (current["protocol_id"], current["collector_semantics_id"])
        != current_identity
        or current_shape != current_x_daily_cycle_shape
    ):
        raise ValueError(
            "collector identity history must append the derived current identity and shape"
        )
    return tuple(validated)


def collector_identity_history_manifest(
    entries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Bind ordered identity pairs and frozen X shapes, excluding reason prose."""
    return [
        {
            "protocol_id": entry["protocol_id"],
            "collector_semantics_id": entry["collector_semantics_id"],
            "x_daily_cycle": {
                "expected_static_slots": entry["x_daily_static_slots"],
                "max_dynamic_slots": entry["x_daily_max_dynamic_slots"],
            },
        }
        for entry in entries
    ]


def discovery_policy_manifest() -> dict[str, Any]:
    """Return a detached JSON-ready projection of the frozen discovery policy."""
    def plain(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: plain(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [plain(item) for item in value]
        return value

    return plain(DISCOVERY_POLICY)


def collection_protocol_manifest(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Describe what is requested and admitted to the evidence stream."""
    query_cycle = evidence["query_cycle"]
    x_policy = evidence["x_formal_policy"]
    retry = query_cycle["globalnews_exception_retry_policy"]
    circuit_breaker = query_cycle["globalnews_cycle_circuit_breaker"]
    editorial = evidence["independent_editorial_policy"]
    return deepcopy({
        "schema_version": 1,
        "policy": COLLECTOR_POLICY,
        "scope": {
            "sources": evidence["allowed_sources"],
            "required_source_groups": evidence["required_source_groups"],
            "ticker_watchlist": False,
            "broad_editorial_news": True,
            "trend_derived_x_reaction": True,
        },
        "news": {
            "request": {
                "endpoint": media_sources._GLOBAL_NEWS_RSS,
                "lookback_days": evidence["lookback_days"],
                "queries": evidence["broad_news_queries"],
                "results_per_query": evidence["max_global_news_results_per_query"],
            },
            "schedule": {
                "interval_seconds": query_cycle["collector_interval_seconds"],
                "cycle_start_grace_seconds": query_cycle[
                    "cycle_start_grace_seconds"
                ],
                "require_every_slot": query_cycle[
                    "require_every_slot_in_cutoff_cycle"
                ],
            },
            "failure_policy": {
                "retry": {
                    "version": "provider-transient-only-v1",
                    "max_attempts_per_slot": retry[
                        "max_attempts_per_query_cycle"
                    ],
                    "delays_seconds": retry["delays_seconds"],
                    "retry_observed_empty": False,
                    "append_receipt_per_attempt": True,
                },
                "circuit_breaker": {
                    "version": "hourly-query-cycle-v1",
                    "failed_slots_before_open": circuit_breaker[
                        "failed_query_slots_before_open"
                    ],
                    "skip_remaining_slots": True,
                },
            },
            "admission": {
                "independent_editorial": {
                    "version": editorial["version"],
                    "require_exact_publisher_domain_pair": editorial[
                        "require_exact_normalized_publisher_domain_pair"
                    ],
                    "sources": editorial["sources"],
                },
                "company_authored_allowed": False,
                "company_authorship_classifier": {
                    "corporate_markers": media_sources._CORPORATE_SOURCE_MARKERS,
                    "editorial_markers": media_sources._EDITORIAL_SOURCE_MARKERS,
                    "first_party_headline_pattern": (
                        media_sources._FIRST_PARTY_HEADLINE.pattern
                    ),
                    "first_party_headline_flags": int(
                        media_sources._FIRST_PARTY_HEADLINE.flags
                    ),
                },
            },
        },
        "discovery": {
            "ranked_feeds": media_sources._GOOGLE_TOP_NEWS_RSS,
            "policy": discovery_policy_manifest(),
        },
        "x": {
            "adapter_policy": media_sources.global_x_adapter_policy_manifest(),
            "request": {
                "trend_woeids": evidence["x_trend_woeids"],
                "results_per_search": evidence["max_x_results_per_query"],
            },
            "schedule": {
                "interval_seconds": evidence["x_cycle_interval_seconds"],
                "recovery_stale_seconds": evidence[
                    "x_cycle_recovery_stale_seconds"
                ],
                "start_earliest_utc_seconds": evidence[
                    "x_cycle_start_earliest_utc_seconds"
                ],
                "start_minimum_remaining_utc_seconds": evidence[
                    "x_cycle_start_minimum_remaining_utc_seconds"
                ],
                "max_trend_requests_per_day": evidence[
                    "max_x_trend_requests_per_utc_day"
                ],
                "max_search_requests_per_day": evidence[
                    "max_x_search_requests_per_utc_day"
                ],
                "static_slots": x_daily_static_slots(evidence),
                "same_day_identity_precedence": (
                    COLLECTOR_COMPATIBILITY_PRECEDENCE
                ),
            },
            "admission": {
                key: x_policy[key]
                for key in (
                    "version",
                    "required_evidence_role",
                    "required_topic_context",
                    "required_immutable_author_id",
                    "required_account_created_utc",
                    "required_automation_signals_complete",
                    "required_profile_screening_complete",
                    "known_verified_types",
                    "excluded_verified_types",
                    "organization_signal_flags",
                    "exclude_any_organization_signal",
                    "profile_screening_limitation",
                    "topic_labels",
                    "topic_assignment",
                    "max_automation_risk",
                    "required_author_metrics",
                    "required_engagement_metrics",
                    "engagement_weights",
                    "minimum_engagement_score",
                    "normalized_text_min_chars",
                    "normalized_text_max_chars",
                )
            } | {"text_normalization": "nfkc-public-reaction-tokens-v1"},
        },
    })


def collector_semantics_manifest(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Describe receipt, normalization, and stored wire-format compatibility."""
    query_cycle = evidence["query_cycle"]
    response = query_cycle["provider_response_validation"]
    lineage = evidence["fetch_receipt_evidence_lineage"]
    return deepcopy({
        "schema_version": 1,
        "normalization": {
            "provider_rows": "global-media-row-v3-x-receipt-context",
            "google_news_content_vintages": "google-news-content-vintage-v3",
            "x_expanded_authors": "x-expanded-author-metrics-v3-canonical-aliases",
            "x_topic_context": (
                "receipt-query-key-rematerialized-as-exact-cycle-title-v2"
            ),
            "maximum_response_bytes": media_sources._MAX_PROVIDER_RESPONSE_BYTES,
            "provider_responses": {
                "version": "strict-provider-response-v1",
                "transient_http_statuses": response[
                    "transient_http_statuses"
                ],
                "globalnews": "rss2-direct-channel-items-v1",
                "topnews": "complete-nonempty-ranked-feed-set-v1",
                "x_recent_search": (
                    "expanded-author-strict-v4-canonical-post-aliases-receipt-context"
                ),
                "x_trends": "nonempty-ranked-trends-v1",
            },
        },
        "receipts": {
            "fetch_lineage": {
                "version": lineage["version"],
                "formal_projection_providers": lineage[
                    "formal_projection_providers"
                ],
                "evidence_identity": "sha256-source-external-id-v1",
                "raw_content_identity": (
                    "sha256-normalized-provider-content-v2-x-query-independent"
                ),
                "observation_time": "server-terminal-before-cutoff-v1",
            },
            "allowed_observed_empty_providers": query_cycle[
                "allowed_observed_empty_providers"
            ],
        },
        "wire_formats": {
            "collection_cycle_identity": 1,
            "collection_cycle_manifest": 2,
            "fetch_receipt_metadata": 1,
            "media_store_row": 1,
        },
    })
