"""Validated internal analytics pipeline.

This module prepares reports but cannot authorize publication.
"""

from insights_windowed_reporting import prepare_windowed_report
from insights_safe_schema import (
    MAX_ITEMS,
    validate_candidate_report,
)


def prepare_validated_report(usage, weeks=4, today=None):
    """Create and validate an internal interaction-level report."""

    internal = prepare_windowed_report(
        usage,
        weeks=weeks,
        today=today,
    )

    # Limit internal report size before schema validation.
    # This is not a privacy-release decision.
    internal["frequently_asked_topics"] = (
        internal["frequently_asked_topics"][:MAX_ITEMS]
    )
    internal["quiz_performance"] = (
        internal["quiz_performance"][:MAX_ITEMS]
    )

    validated = validate_candidate_report(internal)

    # Explicitly preserve the unpublished status.
    validated["privacy_approved"] = False
    validated["status"] = "candidate_only"

    return validated
