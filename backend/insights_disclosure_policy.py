"""Internal activity bands for candidate analytics.

Interaction counts do not establish distinct student counts.
These bands are not authorization to publish data.
"""

MIN_REPORTABLE_INTERACTIONS = 10


def activity_band(count):
    """Classify an internal interaction count."""
    if type(count) is not int or count < 0:
        raise ValueError("Count must be a nonnegative integer")

    if count < MIN_REPORTABLE_INTERACTIONS:
        return None
    if count < 25:
        return "low"
    if count < 50:
        return "moderate"
    if count < 100:
        return "high"

    return "very_high"


def prepare_banded_topics(candidate):
    """Prepare internal topic bands, never exact counts."""
    if not isinstance(candidate, dict):
        raise TypeError("Candidate must be a dictionary")

    if candidate.get("privacy_approved") is not False:
        raise ValueError("Expected an unpublished candidate")

    topics = candidate.get("frequently_asked_topics", [])
    if not isinstance(topics, list):
        raise ValueError("Invalid topic data")

    result = []

    for topic in topics:
        if not isinstance(topic, dict):
            raise ValueError("Invalid topic")

        name = topic.get("lecture")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Invalid topic label")

        band = activity_band(topic.get("interactions"))

        if band is not None:
            result.append({
                "lecture": name.strip(),
                "activity_level": band,
            })

    return {
        "status": "internal_only",
        "privacy_approved": False,
        "topics": result,
    }
