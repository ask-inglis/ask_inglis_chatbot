"""Contributor verification policy for Professor Insights.

A count must originate from an approved, independent source.
Browser sessions, conversations, questions and quiz attempts
are never accepted as verified student contributors.
"""

MIN_CONTRIBUTORS = 10

TRUSTED_SOURCE = "approved_course_aggregate"


def verify_contributors(count, source, independently_verified=False):
    """Check whether a trusted aggregate meets the privacy threshold.

    This is a policy check, not an identity-verification service.
    Callers must establish source trust independently; untrusted
    request parameters must never be passed as verified evidence.
    """
    if (
        source != TRUSTED_SOURCE
        or independently_verified is not True
        or type(count) is not int
        or count < MIN_CONTRIBUTORS
    ):
        return {
            "verified": False,
            "minimum_contributors": MIN_CONTRIBUTORS,
        }

    return {
        "verified": True,
        "minimum_contributors": MIN_CONTRIBUTORS,
    }
