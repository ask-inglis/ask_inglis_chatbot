"""Privacy protection for Professor Insights.

Do not expose student-level information or aggregate statistics
unless the required contributor threshold is independently verified.
"""

MIN_CONTRIBUTORS = 10

SECTIONS = (
    "frequently_asked_topics",
    "quiz_performance",
    "dissection_guides",
    "learning_gaps",
    "weekly_trends",
)


def build_private_insights(weeks=4):
    """Fail closed until privacy-safe contributor counting exists.

    Existing interaction counts do not establish the number
    of distinct students. Therefore all activity statistics
    remain suppressed until that requirement is verified.
    """
    if type(weeks) is not int or not 1 <= weeks <= 26:
        raise ValueError("weeks must be between 1 and 26")

    result = {
        "status": "insufficient_anonymous_data",
        "weeks_requested": weeks,
        "privacy": {
            "minimum_contributors": MIN_CONTRIBUTORS,
            "contributor_verification": "unavailable",
            "individual_data_exposed": False,
            "message": (
                "Student activity statistics are withheld "
                "until privacy requirements can be verified."
            ),
        },
        "recommendations": [],
    }

    for section in SECTIONS:
        result[section] = {
            "status": "insufficient_anonymous_data",
            "items": [],
        }

    return result
