import json
import uuid
from datetime import datetime, timezone

from ingestion import get_github_repo

FEEDBACK_PATH = "feedback"


def save_feedback(record: dict) -> bool:
    """Save one rating as its own JSON file in the private repo. Never raises."""
    repo = get_github_repo()
    if repo is None:
        print("WARNING: GitHub not configured, feedback was NOT saved")
        return False

    now = datetime.now(timezone.utc)
    record = {**record, "created_at": now.isoformat()}
    path = f"{FEEDBACK_PATH}/{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
    try:
        repo.create_file(
            path=path,
            message="Add student feedback",
            content=json.dumps(record, indent=2),
        )
        return True
    except Exception as e:
        print(f"Feedback save failed: {e}")
        return False


def list_feedback(rating: str | None = None, limit: int = 50) -> list[dict]:
    """Read the newest `limit` ratings, optionally keeping only 'up' or 'down'."""
    repo = get_github_repo()
    if repo is None:
        return []
    try:
        files = repo.get_contents(FEEDBACK_PATH)
    except Exception:
        return []  # nothing saved yet

    files = sorted(
        (f for f in files if f.name.endswith(".json")),
        key=lambda f: f.name,
        reverse=True,
    )[:limit]

    items = []
    for f in files:
        try:
            rec = json.loads(f.decoded_content.decode("utf-8"))
        except Exception:
            continue
        if rating is None or rec.get("rating") == rating:
            items.append(rec)
    return items