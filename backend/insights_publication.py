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

    # Always validate the requested period first.
    withheld = build_private_insights(weeks)

    # Never let caller-supplied aggregates authorize release.
    if internal_aggregates is not None:
        return withheld

    # The reviewed snapshot contract supports only four weeks.
    if weeks != 4:
        return withheld

    snapshot = load_approved_snapshot()
    if snapshot is None:
        return withheld

    return format_reviewed_snapshot(snapshot)


def build_course_activity_release_draft():
    """Prepare a restricted, non-public course activity summary.

    Uses only server-side stored activity and one fixed period.
    Does not publish lecture names or numerical counts.
    """

    from insights import prepare_internal_validated_insights
    from insights_disclosure_policy import activity_band

    report = prepare_internal_validated_insights(weeks=4)

    trends = report["weekly_trends"]

    # Course-wide interactions across four completed weeks.
    total = sum(week["answers"] for week in trends)

    # Suppress low counts and expose only a coarse label.
    level = activity_band(total)

    return {
        "status": "release_draft_only",
        "data_type": "interaction_activity_not_students",
        "period": "four_completed_weeks",
        "course_activity_level": level,
        "individual_data_exposed": False,
        "privacy_approved": False,
    }


def validate_reviewed_snapshot(snapshot):
    """Validate the shape of a manually reviewed release snapshot.

    Schema validation alone does not constitute privacy approval.
    """
    from datetime import date, timedelta

    if not isinstance(snapshot, dict):
        raise ValueError("Invalid snapshot")

    expected_fields = {
        "schema_version",
        "period_start",
        "period_end",
        "course_activity_level",
    }

    if set(snapshot) != expected_fields:
        raise ValueError("Unexpected snapshot fields")

    if type(snapshot["schema_version"]) is not int:
        raise ValueError("Invalid schema version")

    if snapshot["schema_version"] != 1:
        raise ValueError("Unsupported schema version")

    start = date.fromisoformat(snapshot["period_start"])
    end = date.fromisoformat(snapshot["period_end"])

    if start.weekday() != 0:
        raise ValueError("Period must begin on Monday")

    if end - start != timedelta(days=27):
        raise ValueError("Expected exactly four completed weeks")

    current_monday = date.today() - timedelta(
        days=date.today().weekday()
    )

    if end >= current_monday:
        raise ValueError("Incomplete reporting period")

    allowed_levels = {
        None,
        "low",
        "moderate",
        "high",
        "very_high",
    }

    if snapshot["course_activity_level"] not in allowed_levels:
        raise ValueError("Invalid activity level")

    return {
        "schema_version": 1,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "course_activity_level": snapshot["course_activity_level"],
    }


def format_reviewed_snapshot(snapshot):
    """Format a reviewed snapshot for the Professor Insights API.

    The caller must obtain this from a trusted approval process.
    Validation here checks structure, not approval provenance.
    """
    validated = validate_reviewed_snapshot(snapshot)

    level = validated["course_activity_level"]

    return {
        "status": "reviewed_snapshot_candidate",
        "weeks_requested": 4,
        "privacy": {
            "individual_data_exposed": False,
            "contributor_verification": "not_applicable",
            "reporting_basis": "interaction_activity_not_students",
            "message": (
                "Course-wide interaction activity only. "
                "This does not represent unique students."
            ),
        },
        "course_activity": {
            "period_start": validated["period_start"],
            "period_end": validated["period_end"],
            "activity_level": level,
        },
        "frequently_asked_topics": {
            "status": "not_available",
            "items": [],
        },
        "quiz_performance": {
            "status": "not_available",
            "items": [],
        },
        "dissection_guides": {
            "status": "not_available",
            "items": [],
        },
        "learning_gaps": {
            "status": "not_available",
            "items": [],
        },
        "weekly_trends": {
            "status": "not_available",
            "items": [],
        },
        "recommendations": [],
    }


def load_approved_snapshot():
    """Load a deployment-controlled, checksum-pinned snapshot.

    Missing configuration or mismatched content fails closed.
    Approval must happen outside this function.
    """
    import hashlib
    import json
    import os
    from pathlib import Path

    path_value = os.environ.get(
        "INSIGHTS_APPROVED_SNAPSHOT_PATH", ""
    )
    expected_hash = os.environ.get(
        "INSIGHTS_APPROVED_SNAPSHOT_SHA256", ""
    ).lower()

    if not path_value or len(expected_hash) != 64:
        return None

    if any(c not in "0123456789abcdef" for c in expected_hash):
        return None

    try:
        path = Path(path_value)
        if not path.is_absolute() or not path.is_file():
            return None

        content = path.read_bytes()

        if len(content) > 4096:
            return None

        actual_hash = hashlib.sha256(content).hexdigest()

        if actual_hash != expected_hash:
            return None

        snapshot = json.loads(content)
        return validate_reviewed_snapshot(snapshot)

    except (OSError, ValueError, TypeError, UnicodeError):
        return None
