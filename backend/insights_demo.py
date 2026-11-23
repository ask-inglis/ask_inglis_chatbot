"""Synthetic analytics data for local development only.

Never use this as real student activity or publish it
through the production Professor Insights API.
"""

from datetime import date, timedelta

from insights_aggregates import prepare_weekly_aggregates


def build_demo_insights(weeks=4, today=None):
    """Prepare artificial anatomy usage statistics for UI testing."""
    if type(weeks) is not int or not 1 <= weeks <= 26:
        raise ValueError("weeks must be between 1 and 26")

    if today is None:
        today = date.today()

    current_monday = today - timedelta(days=today.weekday())

    usage = {"weeks": {}}

    for offset in range(weeks - 1, -1, -1):
        monday = current_monday - timedelta(weeks=offset)
        year, week, _ = monday.isocalendar()
        week_key = f"{year}-W{week:02d}"

        index = weeks - offset

        usage["weeks"][week_key] = {
            "answers": 40 + index * 8,
            "modes": {
                "chat": 25 + index * 5,
                "quiz": 10 + index * 2,
                "dissection": 5 + index,
            },
            "lectures": {
                "Upper Limb Anatomy": 15 + index * 3,
                "Cardiovascular System": 12 + index * 2,
                "Nervous System": 10 + index,
                "Muscular System": 8 + index,
            },
            "quiz": {
                "Upper Limb Anatomy": [20, 8],
                "Cardiovascular System": [15, 4],
                "Nervous System": [12, 5],
                "Muscular System": [10, 2],
            },
        }

    result = prepare_weekly_aggregates(
        usage,
        weeks=weeks,
        today=today,
    )

    result["demo"] = True
    result["source"] = "synthetic_test_data"

    return result
