"""Restricted server-to-server access for Professor Dashboard.

The professor integration key must never authorize unrestricted
administrative reporting or conversation-log access.
"""

import os
import secrets

PROFESSOR_ALLOWED_PATHS = frozenset({
    "/admin/insights",
    "/admin/feedback",
    "/admin/feedback/queue",
    "/admin/feedback/resolve-group",
    "/admin/feedback/resolve-lecture",
})


def is_professor_integration_key(provided_key, admin_key=None, professor_key=None):
    """Validate a separate, non-admin Professor integration key."""
    if admin_key is None:
        admin_key = os.getenv("ADMIN_API_KEY", "")
    if professor_key is None:
        professor_key = os.getenv("PROFESSOR_INTEGRATION_KEY", "")

    if not all(isinstance(k, str) for k in (provided_key, admin_key, professor_key)):
        return False

    if not provided_key or not professor_key:
        return False

    # A shared key would defeat credential separation.
    if admin_key and secrets.compare_digest(professor_key, admin_key):
        return False

    return secrets.compare_digest(provided_key, professor_key)


def professor_path_allowed(path):
    """Check the restricted, explicitly approved endpoint set."""
    if not isinstance(path, str):
        return False

    if path in PROFESSOR_ALLOWED_PATHS:
        return True

    # Existing individual feedback-resolution route.
    prefix = "/admin/feedback/"
    suffix = "/resolve"

    if path.startswith(prefix) and path.endswith(suffix):
        feedback_id = path[len(prefix):-len(suffix)]

        if (
            feedback_id
            and "/" not in feedback_id
            and feedback_id not in {"queue", "resolve-group", "resolve-lecture"}
        ):
            return True

    return False
