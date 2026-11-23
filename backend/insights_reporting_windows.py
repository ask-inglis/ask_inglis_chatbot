"""Fixed reporting windows for privacy-aware analytics.

These windows reduce overlapping-filter risks, but do not
independently establish anonymity or authorize publication.
"""

from datetime import date, timedelta


def completed_week_windows(today=None, count=4):
    """Return non-overlapping completed ISO calendar weeks."""

    if today is None:
        today = date.today()

    if not isinstance(today, date):
        raise TypeError("today must be a date")

    if type(count) is not int or not 1 <= count <= 26:
        raise ValueError("count must be between 1 and 26")

    current_monday = today - timedelta(days=today.weekday())

    windows = []

    for offset in range(count, 0, -1):
        start = current_monday - timedelta(weeks=offset)
        end = start + timedelta(days=6)

        year, week, _ = start.isocalendar()

        windows.append({
            "week": f"{year}-W{week:02d}",
            "start": start.isoformat(),
            "end": end.isoformat(),
        })

    return windows
