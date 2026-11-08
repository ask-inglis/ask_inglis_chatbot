"""Two small records kept in the private notes repo.

1. Reviewed lectures: which lectures the instructor has looked at, and when. A lecture is marked
   when the review tool sends a finalized note, or when a person resolves a thumbs-down with
   "note_fixed" or "answer_was_correct". Students see this as a badge on the lecture's source chip.
2. Usage counts: how many answers used each lecture, and how often quiz questions from each lecture
   were missed, per week. Only lecture names and numbers are stored. Never a student's words,
   never an address, never an identifier.

Both live under insights/ in the private repo. Writes are rare (reviews) or throttled (usage), so the
repo does not fill up with commits. Failures are logged and never raised: the study assistant must keep
answering even when GitHub is slow.
"""
import json
import threading
import time
from datetime import datetime, timezone

REVIEWED_PATH = "insights/reviewed.json"
USAGE_PATH = "insights/usage.json"
REVIEW_DECISIONS = ("note_fixed", "answer_was_correct")
FLUSH_EVERY_SECONDS = 6 * 3600  # at most four usage commits a day
MAX_WEEKS_KEPT = 26

_lock = threading.Lock()
_reviewed: dict[str, str] = {}
_usage: dict = {"weeks": {}}
_dirty = False
_last_flush = time.time()


def _repo():
    from ingestion import get_github_repo  # imported here so this file loads without the whole backend

    return get_github_repo()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _week() -> str:
    iso = datetime.now(timezone.utc).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _read_json(path: str, default):
    repo = _repo()
    if repo is None:
        return default
    try:
        return json.loads(repo.get_contents(path).decoded_content.decode("utf-8"))
    except Exception:
        return default


def _write_json(path: str, data, message: str):
    repo = _repo()
    if repo is None:
        print(f"WARNING: GitHub not configured, {path} was NOT saved")
        return False
    content = json.dumps(data, indent=2, sort_keys=True)
    try:
        try:
            existing = repo.get_contents(path)
        except Exception:
            repo.create_file(path, message, content)
            return True
        repo.update_file(path, message, content, existing.sha)
        return True
    except Exception as e:
        print(f"Saving {path} failed: {e}")
        return False


def load_insights() -> str:
    """Run on startup."""
    global _reviewed, _usage
    reviewed = _read_json(REVIEWED_PATH, {})
    usage = _read_json(USAGE_PATH, {"weeks": {}})
    with _lock:
        _reviewed = reviewed if isinstance(reviewed, dict) else {}
        _usage = usage if isinstance(usage, dict) and isinstance(usage.get("weeks"), dict) else {"weeks": {}}
    return f"{len(_reviewed)} reviewed lectures, {len(_usage['weeks'])} weeks of usage"


# ---------- Reviewed lectures ----------

def mark_reviewed(lectures: list[str]) -> int:
    """Stamp each lecture with today's date. Returns how many changed."""
    names = [n for n in dict.fromkeys(l.strip() for l in lectures or []) if n and len(n) <= 300]
    if not names:
        return 0
    today = _today()
    with _lock:
        changed = [n for n in names if _reviewed.get(n) != today]
        for n in changed:
            _reviewed[n] = today
        snapshot = dict(_reviewed)
    if changed:
        _write_json(REVIEWED_PATH, snapshot, "Update reviewed lectures")
    return len(changed)


def reviewed_lectures() -> dict[str, str]:
    with _lock:
        return dict(_reviewed)


# ---------- Usage counts ----------

def _week_bucket(week: str) -> dict:
    return _usage["weeks"].setdefault(week, {"answers": 0, "modes": {}, "lectures": {}, "quiz": {}})


def record_answer(lectures: list[str], kind: str = "chat"):
    """One answered question. kind is chat, dissection or quiz."""
    global _dirty
    with _lock:
        bucket = _week_bucket(_week())
        bucket["answers"] += 1
        bucket["modes"][kind] = bucket["modes"].get(kind, 0) + 1
        for name in dict.fromkeys(lectures or []):
            bucket["lectures"][name] = bucket["lectures"].get(name, 0) + 1
        _dirty = True
    _maybe_flush()


def record_quiz_grade(items: list[dict], picks: dict[int, str]):
    """For each question the student answered: was it right? Counted per lecture."""
    global _dirty
    with _lock:
        bucket = _week_bucket(_week())
        for number, q in enumerate(items, 1):
            lecture = (q.get("lecture") or "").strip()
            chosen = picks.get(number)
            if not lecture or not chosen:
                continue
            row = bucket["quiz"].setdefault(lecture, [0, 0])  # [answered, missed]
            row[0] += 1
            if chosen != q.get("answer"):
                row[1] += 1
        _dirty = True
    _maybe_flush()


def _maybe_flush():
    if time.time() - _last_flush >= FLUSH_EVERY_SECONDS:
        threading.Thread(target=flush_usage, daemon=True).start()


def flush_usage() -> bool:
    global _dirty, _last_flush
    with _lock:
        if not _dirty:
            _last_flush = time.time()
            return True
        weeks = sorted(_usage["weeks"])
        for old in weeks[:-MAX_WEEKS_KEPT]:
            del _usage["weeks"][old]
        snapshot = json.loads(json.dumps(_usage))
        _dirty = False
        _last_flush = time.time()
    ok = _write_json(USAGE_PATH, snapshot, "Update usage counts")
    if not ok:
        with _lock:
            _dirty = True
    return ok


def build_report(weeks: int = 4) -> dict:
    """What students ask about and miss, for the instructor. Counts only."""
    from feedback import list_feedback  # imported here: feedback pulls in the GitHub libraries

    weeks = max(1, min(int(weeks), MAX_WEEKS_KEPT))
    with _lock:
        keys = sorted(_usage["weeks"])[-weeks:]
        buckets = {k: json.loads(json.dumps(_usage["weeks"][k])) for k in keys}

    answers, modes, lecture_use, quiz = 0, {}, {}, {}
    for b in buckets.values():
        answers += b.get("answers", 0)
        for k, v in b.get("modes", {}).items():
            modes[k] = modes.get(k, 0) + v
        for k, v in b.get("lectures", {}).items():
            lecture_use[k] = lecture_use.get(k, 0) + v
        for k, (a, m) in b.get("quiz", {}).items():
            row = quiz.setdefault(k, [0, 0])
            row[0] += a
            row[1] += m

    downs = {}
    for item in list_feedback(rating="down", limit=200):
        for lecture in item.get("lectures", []):
            row = downs.setdefault(lecture, {"reports": 0, "pending": 0})
            row["reports"] += 1
            if item.get("status") == "pending_review":
                row["pending"] += 1

    most_missed = sorted(
        (
            {"lecture": k, "answered": a, "missed": m, "miss_rate": round(m / a, 2)}
            for k, (a, m) in quiz.items()
            if a >= 3
        ),
        key=lambda r: (-r["miss_rate"], -r["answered"]),
    )
    return {
        "weeks": keys,
        "answers": answers,
        "by_mode": modes,
        "most_asked_lectures": sorted(
            ({"lecture": k, "answers": v} for k, v in lecture_use.items()), key=lambda r: -r["answers"]
        )[:15],
        "most_missed_in_quizzes": most_missed[:15],
        "reports_by_lecture": sorted(
            ({"lecture": k, **v} for k, v in downs.items()), key=lambda r: (-r["pending"], -r["reports"])
        )[:15],
        "reviewed_lectures": reviewed_lectures(),
    }