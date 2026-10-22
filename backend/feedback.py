import hashlib
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone

from ingestion import get_github_repo

FEEDBACK_PATH = "feedback"
RATING_FOLDERS = {"up": "up", "down": "down"}
DECISIONS = ("note_fixed", "answer_was_correct", "dismissed")
INCORRECT_WORDS = re.compile(
    r"\b(wrong|incorrect|inaccurate|mistake|error|not (right|true|correct|accurate))\b", re.IGNORECASE
)
ID_PATTERN = re.compile(r"^[0-9]{8}T[0-9]{6}_[0-9a-f]{8}$")

# GitHub rejects two commits made at the same moment, so writes take turns and retry.
_write_lock = threading.Lock()


def _with_retry(action, what: str, attempts: int = 3):
    for n in range(attempts):
        try:
            return action()
        except Exception as e:
            if n == attempts - 1:
                print(f"{what} failed after {attempts} tries: {e}")
                return None
            time.sleep(1.5 * (n + 1))


def make_fingerprint(question: str) -> str:
    """Same question, same fingerprint, so repeated reports can be grouped."""
    norm = " ".join(re.sub(r"[^a-z0-9 ]", "", question.lower()).split())[:120]
    return hashlib.sha1(norm.encode()).hexdigest()[:10]


def save_feedback(record: dict) -> bool:
    """Save one rating as feedback/up/<id>.json or feedback/down/<id>.json. Never raises.
    Every thumbs-down is queued for a human (status: pending_review)."""
    repo = get_github_repo()
    if repo is None:
        print("WARNING: GitHub not configured, feedback was NOT saved")
        return False

    folder = RATING_FOLDERS.get(record.get("rating"))
    if folder is None:
        print(f"Feedback not saved: unknown rating {record.get('rating')!r}")
        return False

    now = datetime.now(timezone.utc)
    feedback_id = f"{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    record = {**record, "id": feedback_id, "created_at": now.isoformat()}
    if folder == "down":
        record.setdefault(
            "reason", "incorrect" if INCORRECT_WORDS.search(record.get("comment", "")) else "other"
        )
        record["status"] = "pending_review"
        record["fingerprint"] = make_fingerprint(record.get("question", ""))

    def write():
        repo.create_file(
            path=f"{FEEDBACK_PATH}/{folder}/{feedback_id}.json",
            message=f"Add {folder} feedback",
            content=json.dumps(record, indent=2),
        )
        return True

    with _write_lock:
        return bool(_with_retry(write, f"Feedback save {feedback_id}"))


def _read_folder(repo, folder: str, scan: int) -> list[dict]:
    try:
        entries = repo.get_contents(f"{FEEDBACK_PATH}/{folder}")
    except Exception:
        return []
    files = sorted(
        (f for f in entries if f.type == "file" and f.name.endswith(".json")),
        key=lambda f: f.name,
        reverse=True,
    )[:scan]
    items = []
    for f in files:
        try:
            rec = json.loads(repo.get_contents(f.path).decoded_content.decode("utf-8"))
        except Exception:
            continue
        rec.setdefault("id", f.name[:-5])
        rec.setdefault("status", "pending_review" if folder == "down" else "n/a")
        rec.setdefault("fingerprint", make_fingerprint(rec.get("question", "")))
        items.append(rec)
    return items


def list_feedback(rating=None, status=None, reason=None, limit: int = 50) -> list[dict]:
    """Newest first. Filter by rating (up/down), status (pending_review/resolved) and reason."""
    repo = get_github_repo()
    if repo is None:
        return []
    folders = [RATING_FOLDERS[rating]] if rating in RATING_FOLDERS else ["up", "down"]
    items = []
    for folder in folders:
        items += _read_folder(repo, folder, 100)  # newest 100 per folder
    if status:
        items = [i for i in items if i.get("status") == status]
    if reason:
        items = [i for i in items if i.get("reason") == reason]
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return items[:limit]


def build_queue(status: str = "pending_review", limit: int = 50) -> list[dict]:
    """Thumbs-downs grouped by question, so a reviewer makes one decision per problem.
    Order: 'incorrect' first, then most reports, then newest."""
    groups = {}
    for it in list_feedback(rating="down", status=status, limit=200):
        reason = it.get("reason", "other")
        created = it.get("created_at", "")
        g = groups.setdefault(
            (it["fingerprint"], reason),
            {
                "fingerprint": it["fingerprint"],
                "reason": reason,
                "question": it.get("question", ""),
                "answer": it.get("answer", ""),
                "lectures": [],
                "comments": [],
                "ids": [],
                "count": 0,
                "first_seen": created,
                "last_seen": created,
            },
        )
        g["count"] += 1
        g["ids"].append(it["id"])
        if created > g["last_seen"]:
            g["last_seen"], g["answer"] = created, it.get("answer", "")
        if created < g["first_seen"]:
            g["first_seen"] = created
        for lecture in it.get("lectures", []):
            if lecture not in g["lectures"]:
                g["lectures"].append(lecture)
        comment = it.get("comment", "").strip()
        if comment and comment not in g["comments"] and len(g["comments"]) < 5:
            g["comments"].append(comment)

    ordered = sorted(groups.values(), key=lambda g: g["last_seen"], reverse=True)
    ordered.sort(key=lambda g: (g["reason"] != "incorrect", -g["count"]))
    return ordered[:limit]


def resolve_feedback(feedback_id: str, decision: str, comment: str = "") -> dict | None:
    """A human closes a thumbs-down: note_fixed, answer_was_correct or dismissed."""
    repo = get_github_repo()
    if repo is None or not ID_PATTERN.match(feedback_id) or decision not in DECISIONS:
        return None
    path = f"{FEEDBACK_PATH}/down/{feedback_id}.json"

    def update():
        f = repo.get_contents(path)  # fresh version on every try
        rec = json.loads(f.decoded_content.decode("utf-8"))
        rec.update(
            status="resolved",
            decision=decision,
            reviewer_comment=comment[:1000],
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )
        repo.update_file(path, f"Resolve feedback {feedback_id}", json.dumps(rec, indent=2), f.sha)
        return rec

    with _write_lock:
        return _with_retry(update, f"Resolve {feedback_id}")


def _resolve_many(ids: list[str], decision: str, comment: str) -> int:
    return sum(1 for i in ids if resolve_feedback(i, decision, comment))


def resolve_group(fingerprint: str, decision: str, comment: str = "") -> int:
    """Close every pending thumbs-down about the same question. Returns how many were closed."""
    items = list_feedback(rating="down", status="pending_review", limit=200)
    return _resolve_many([i["id"] for i in items if i["fingerprint"] == fingerprint], decision, comment)


def resolve_lecture(lecture: str, decision: str, comment: str = "") -> int:
    """Close every pending thumbs-down whose answer used this lecture. Returns how many were closed."""
    items = list_feedback(rating="down", status="pending_review", limit=200)
    ids = [i["id"] for i in items if lecture in i.get("lectures", [])]
    return _resolve_many(ids, decision, comment)