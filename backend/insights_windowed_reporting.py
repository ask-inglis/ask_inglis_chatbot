"""Internal analytics for completed reporting weeks.

Not approved for publication. Interaction counts are not
verified distinct-student counts.
"""

from insights_aggregates import prepare_weekly_aggregates
from insights_reporting_windows import completed_week_windows


def prepare_windowed_report(usage, weeks=4, today=None):
    """Use completed weeks only, excluding the current partial week."""

    windows = completed_week_windows(today=today, count=weeks)

    source = usage.get("weeks", {}) if isinstance(usage, dict) else {}
    if not isinstance(source, dict):
        source = {}

    selected = {
        window["week"]: source.get(window["week"], {})
        for window in windows
    }

    # The aggregation processor includes its reference week.
    # Use the Sunday ending the final completed reporting week.
    from datetime import date

    reference_date = date.fromisoformat(windows[-1]["end"])

    result = prepare_weekly_aggregates(
        {"weeks": selected},
        weeks=weeks,
        today=reference_date,
    )

    return {
        "status": "internal_only",
        "privacy_approved": False,
        "data_type": "interaction_counts_only",
        "weeks_requested": weeks,
        "reporting_windows": windows,
        "frequently_asked_topics": result["frequently_asked_topics"],
        "quiz_performance": result["quiz_performance"],
        "weekly_trends": result["weekly_trends"],
    }
