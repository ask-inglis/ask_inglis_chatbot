"""Publication boundary for Ask Inglis Professor Insights.

Internal interaction aggregates are not verified student aggregates.
This module must fail closed until an approved data source exists.
"""

from insights_privacy import build_private_insights


def publish_insights(internal_aggregates=None, weeks=4):
    """Return the privacy-safe public response.

    Never return internal interaction statistics directly.
    An approved contributor-verification pipeline is required
    before any numerical analytics can be published.
    """

    # Validate the requested reporting period.
    result = build_private_insights(weeks)

    # Deliberately ignore unverified internal_aggregates.
    # This prevents accidental disclosure even if a caller
    # passes high interaction counts or lecture names.
    return result
