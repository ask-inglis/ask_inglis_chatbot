"""Strict internal schema for candidate learning analytics.

This is NOT a publication authorization mechanism.
Only known aggregate fields are retained.
"""

from insights_reporting_policy import ALLOWED_PERIODS

MAX_ITEMS = 15

def _count(value):
    if type(value) is not int or value < 0:
        raise ValueError("Invalid aggregate count")
    return value


def _lecture(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 100
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Invalid lecture label")
    return value.strip()


def validate_candidate_report(report):
    if not isinstance(report, dict):
        raise TypeError("Report must be a dictionary")

    if report.get("privacy_approved") is not False:
        raise ValueError("Expected unpublished candidate report")

    weeks = report.get("weeks_requested")
    if type(weeks) is not int or weeks not in ALLOWED_PERIODS:
        raise ValueError("Invalid reporting period")

    topics = report.get("frequently_asked_topics", [])
    quizzes = report.get("quiz_performance", [])
    trends = report.get("weekly_trends", [])

    if not all(isinstance(x, list) for x in (topics, quizzes, trends)):
        raise ValueError("Invalid report sections")

    if len(topics) > MAX_ITEMS or len(quizzes) > MAX_ITEMS:
        raise ValueError("Too many report items")

    if len(trends) != weeks:
        raise ValueError("Unexpected weekly trend count")

    safe_topics = []
    for row in topics:
        if not isinstance(row, dict):
            raise ValueError("Invalid topic")
        safe_topics.append({
            "lecture": _lecture(row.get("lecture")),
            "interactions": _count(row.get("interactions")),
        })

    safe_quizzes = []
    for row in quizzes:
        if not isinstance(row, dict):
            raise ValueError("Invalid quiz")
        answered = _count(row.get("answered"))
        missed = _count(row.get("missed"))
        if missed > answered:
            raise ValueError("Invalid quiz totals")

        safe_quizzes.append({
            "lecture": _lecture(row.get("lecture")),
            "answered": answered,
            "missed": missed,
            "miss_rate": round(missed / answered, 4) if answered else 0,
        })

    safe_trends = []
    seen = set()
    for row in trends:
        if not isinstance(row, dict):
            raise ValueError("Invalid trend")
        week = row.get("week")
        if not isinstance(week, str) or week in seen:
            raise ValueError("Invalid or duplicate week")
        seen.add(week)

        safe_trends.append({
            "week": week,
            "answers": _count(row.get("answers")),
            "chat": _count(row.get("chat")),
            "quiz": _count(row.get("quiz")),
            "dissection": _count(row.get("dissection")),
        })

    return {
        "status": "candidate_only",
        "privacy_approved": False,
        "weeks_requested": weeks,
        "frequently_asked_topics": safe_topics,
        "quiz_performance": safe_quizzes,
        "weekly_trends": safe_trends,
    }
