"""Candidate Professor Insights response formatting.

This formats internal analytics for future approved reporting.
It does not authorize release of real interaction data.
"""

def format_candidate_insights(report):
    if not isinstance(report, dict):
        raise TypeError("report must be a dictionary")

    if report.get("privacy_approved") is not False:
        raise ValueError("Expected unpublished internal report")

    return {
        "status": "candidate_only",
        "privacy_approved": False,
        "weeks_requested": report.get("weeks_requested"),
        "frequently_asked_topics": {
            "status": "candidate_only",
            "items": report.get("frequently_asked_topics", []),
        },
        "quiz_performance": {
            "status": "candidate_only",
            "items": report.get("quiz_performance", []),
        },
        "weekly_trends": {
            "status": "candidate_only",
            "items": report.get("weekly_trends", []),
        },
        "dissection_guides": {
            "status": "not_available",
            "items": [],
        },
        "learning_gaps": {
            "status": "not_available",
            "items": [],
        },
        "recommendations": [],
    }
