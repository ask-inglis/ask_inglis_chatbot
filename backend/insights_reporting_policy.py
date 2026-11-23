"""Validation rules for future approved Professor Insights reports.

This module checks report structure and privacy requirements.
It does NOT establish source authenticity or authorize publication.
"""

from insights_contributors import MIN_CONTRIBUTORS

ALLOWED_PERIODS = (4, 8, 12, 26)
ALLOWED_SECTIONS = (
    "frequently_asked_topics",
    "quiz_performance",
    "weekly_trends",
)


def validate_reporting_request(weeks):
    if type(weeks) is not int or weeks not in ALLOWED_PERIODS:
        raise ValueError("Unsupported reporting period")

    return True


def validate_aggregate_group(group):
    """Validate metadata only; never return sensitive input fields."""

    if not isinstance(group, dict):
        return False

    count = group.get("verified_contributors")
    verified = group.get("independently_verified")
    source = group.get("source")

    if type(count) is not int or count < MIN_CONTRIBUTORS:
        return False

    if verified is not True:
        return False

    if source != "approved_course_aggregate":
        return False

    return True


def build_reporting_status(weeks=4):
    """Return a safe status without exposing activity statistics."""

    validate_reporting_request(weeks)

    return {
        "status": "awaiting_approved_aggregates",
        "weeks_requested": weeks,
        "minimum_contributors": MIN_CONTRIBUTORS,
        "statistics_available": False,
        "source_verification": "not_configured",
    }
