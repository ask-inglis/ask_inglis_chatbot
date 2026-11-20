"""Internal weekly analytics preparation for Ask Inglis.

This module processes existing interaction-level counters.

IMPORTANT:
- Interaction counts do not establish distinct student counts.
- Results from this module are NOT approved for direct frontend exposure.
- Only the separate privacy layer may decide what gets published.
"""

from collections import defaultdict
from datetime import date, timedelta
import re

MAX_WEEKS = 26
WEEK_PATTERN = re.compile(r"^\d{4}-W\d{2}$")


def _valid_week(value):
    if not isinstance(value, str) or not WEEK_PATTERN.fullmatch(value):
        return False

    try:
        year, week = map(int, value.split("-W"))
        date.fromisocalendar(year, week, 1)
        return True
    except ValueError:
        return False


def _count(value):
    """Accept only nonnegative integer counters."""
    if type(value) is not int or value < 0:
        return 0
    return value


def prepare_weekly_aggregates(usage, weeks=4, today=None):
    """Prepare internal analytics from existing weekly usage counters.

    Uses actual calendar weeks rather than simply the last N stored keys.
    Missing weeks are represented by zero activity.

    Returned data is NOT privacy-approved for professor access.
    """
    if type(weeks) is not int or not 1 <= weeks <= MAX_WEEKS:
        raise ValueError("weeks must be between 1 and 26")

    if today is None:
        today = date.today()

    if not isinstance(today, date):
        raise TypeError("today must be a date")

    if not isinstance(usage, dict):
        raise TypeError("usage must be a dictionary")

    source = usage.get("weeks", {})
    if not isinstance(source, dict):
        source = {}

    current_monday = today - timedelta(days=today.weekday())

    totals = defaultdict(int)
    quiz_totals = defaultdict(lambda: [0, 0])
    trends = []

    for offset in range(weeks - 1, -1, -1):
        monday = current_monday - timedelta(weeks=offset)
        year, week, _ = monday.isocalendar()
        week_key = f"{year}-W{week:02d}"

        bucket = source.get(week_key, {})
        if not isinstance(bucket, dict):
            bucket = {}

        answers = _count(bucket.get("answers"))
        modes = bucket.get("modes", {})
        lectures = bucket.get("lectures", {})
        quiz = bucket.get("quiz", {})

        if not isinstance(modes, dict):
            modes = {}
        if not isinstance(lectures, dict):
            lectures = {}
        if not isinstance(quiz, dict):
            quiz = {}

        trends.append({
            "week": week_key,
            "answers": answers,
            "chat": _count(modes.get("chat")),
            "quiz": _count(modes.get("quiz")),
            "dissection": _count(modes.get("dissection")),
        })

        for lecture, count in lectures.items():
            if isinstance(lecture, str) and lecture:
                totals[lecture] += _count(count)

        for lecture, counts in quiz.items():
            if (
                isinstance(lecture, str)
                and lecture
                and isinstance(counts, (list, tuple))
                and len(counts) == 2
            ):
                answered = _count(counts[0])
                missed = _count(counts[1])

                if missed <= answered:
                    quiz_totals[lecture][0] += answered
                    quiz_totals[lecture][1] += missed

    topics = [
        {"lecture": lecture, "interactions": count}
        for lecture, count in totals.items()
        if count > 0
    ]
    topics.sort(key=lambda row: (-row["interactions"], row["lecture"]))

    performance = []
    for lecture, (answered, missed) in quiz_totals.items():
        if answered > 0:
            performance.append({
                "lecture": lecture,
                "answered": answered,
                "missed": missed,
                "miss_rate": round(missed / answered, 4),
            })

    performance.sort(
        key=lambda row: (-row["miss_rate"], -row["answered"], row["lecture"])
    )

    return {
        "privacy_approved": False,
        "data_type": "interaction_counts_only",
        "weeks_requested": weeks,
        "weekly_trends": trends,
        "frequently_asked_topics": topics,
        "quiz_performance": performance,
    }
