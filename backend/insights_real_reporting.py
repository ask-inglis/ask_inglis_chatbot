"""Prepare candidate anonymous interaction reports.

These are interaction-level statistics, not unique-student counts.
Output remains INTERNAL until the publication policy is completed.
"""

from insights_aggregates import prepare_weekly_aggregates

ALLOWED_PERIODS = (4, 8, 12, 26)

# Preliminary reporting thresholds, not proof of anonymity.
MIN_TOPIC_INTERACTIONS = 10
MIN_QUIZ_ANSWERS = 10


def prepare_candidate_report(usage, weeks=4, today=None):
    """Build a candidate aggregate report from existing counters."""

    if type(weeks) is not int or weeks not in ALLOWED_PERIODS:
        raise ValueError("Unsupported reporting period")

    internal = prepare_weekly_aggregates(
        usage,
        weeks=weeks,
        today=today,
    )

    topics = [
        {
            "lecture": row["lecture"],
            "interactions": row["interactions"],
        }
        for row in internal["frequently_asked_topics"]
        if row["interactions"] >= MIN_TOPIC_INTERACTIONS
    ]

    quizzes = [
        {
            "lecture": row["lecture"],
            "answered": row["answered"],
            "missed": row["missed"],
            "miss_rate": row["miss_rate"],
        }
        for row in internal["quiz_performance"]
        if row["answered"] >= MIN_QUIZ_ANSWERS
    ]

    return {
        "status": "candidate_only",
        "privacy_approved": False,
        "data_type": "anonymous_interaction_aggregates",
        "weeks_requested": weeks,
        "frequently_asked_topics": topics,
        "quiz_performance": quizzes,
        "weekly_trends": internal["weekly_trends"],
        "recommendations": [],
    }
