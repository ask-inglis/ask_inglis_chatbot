"""Question and answer log for the instructor.

Every answered /chat request is appended to a daily JSON-lines file: when, which conversation (a random id made
by the student's browser, not tied to a person), the mode, the question the student saw, the answer, and the
lectures it came from. No IP address, no account, no name. Emails and long digit strings are masked before saving.

Files older than CHATLOG_RETENTION_DAYS are deleted. Failures are printed and never raised: the study
assistant must keep answering even when the disk is full.

Where it lives: CHATLOG_DIR (default ./chatlog) is the working copy. A background thread also copies each day's
file into the private notes repo under chatlog/ every CHATLOG_SYNC_SECONDS (default 600), so the log survives a
container restart. The copy merges with what is already in the repo, so a restart that wiped the local file never
overwrites older entries. Old files are deleted from both places (Git history keeps old versions of a deleted
file; the repo is private).
"""
import csv
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

ENABLED = os.environ.get("CHATLOG_ENABLED", "true").lower() == "true"
LOG_DIR = os.environ.get("CHATLOG_DIR", "chatlog")
RETENTION_DAYS = int(os.environ.get("CHATLOG_RETENTION_DAYS", "90"))
MAX_TEXT = 8000
REPO_FOLDER = os.environ.get("CHATLOG_REPO_FOLDER", "chatlog").strip("/") or "chatlog"  # folder inside the notes repo
SYNC_SECONDS = int(os.environ.get("CHATLOG_SYNC_SECONDS", "600"))
REPO_SYNC = os.environ.get("CHATLOG_REPO_SYNC", "true").lower() == "true"  # false: keep the log local only (for tests)

_lock = threading.Lock()
_last_purge = 0.0
_dirty_days: set[str] = set()
_syncer_started = False
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_DIGITS = re.compile(r"\b\d[\d\s().-]{7,}\d\b")


def _mask(text: str) -> str:
    text = _EMAIL.sub("[email]", str(text or ""))
    text = _DIGITS.sub("[number]", text)
    return text[:MAX_TEXT]


def _path(day: str) -> str:
    return os.path.join(LOG_DIR, f"{day}.jsonl")


def log_exchange(session: str, mode: str, question: str, answer: str, lectures: list[str], ms: int):
    if not ENABLED:
        return
    now = datetime.now(timezone.utc)
    record = {
        "ts": now.isoformat(timespec="seconds"),
        "session": re.sub(r"[^\w-]", "", str(session or ""))[:40],
        "mode": (mode or "ask")[:16],
        "question": _mask(question),
        "answer": _mask(answer),
        "lectures": [str(x)[:120] for x in (lectures or [])][:12],
        "ms": int(ms),
    }
    try:
        with _lock:
            os.makedirs(LOG_DIR, exist_ok=True)
            day = now.strftime("%Y-%m-%d")
            with open(_path(day), "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            _dirty_days.add(day)
        _start_syncer()
        _maybe_purge()
    except Exception as e:  # never break answering
        print(f"Chat log write failed: {e}")


def _repo():
    try:
        from ingestion import get_github_repo  # imported here so this file loads without the whole backend

        return get_github_repo()
    except Exception as e:
        print(f"Chat log: GitHub unavailable: {e}")
        return None


def _local_lines(day: str) -> list[str]:
    try:
        with open(_path(day), encoding="utf-8") as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]
    except FileNotFoundError:
        return []


def _remote_file(repo, day: str):
    try:
        item = repo.get_contents(f"{REPO_FOLDER}/{day}.jsonl")
        text = item.decoded_content.decode("utf-8")
        return item.sha, [ln for ln in text.split("\n") if ln.strip()]
    except Exception:
        return None, []


def _merged(remote: list[str], local: list[str]) -> list[str]:
    seen = set(remote)
    return remote + [ln for ln in local if ln not in seen]


def sync_now() -> bool:
    """Copy every day with new entries into the notes repo. Safe to call at any time."""
    with _lock:
        days = sorted(_dirty_days)
        _dirty_days.clear()
    if not days or not REPO_SYNC:
        return True
    repo = _repo()
    ok = True
    for day in days:
        try:
            if repo is None:
                raise RuntimeError("GitHub not configured")
            sha, remote = _remote_file(repo, day)
            lines = _merged(remote, _local_lines(day))
            if sha is not None and len(lines) == len(remote):
                continue  # nothing new for the repo
            content = "\n".join(lines) + "\n"
            if sha is None:
                repo.create_file(f"{REPO_FOLDER}/{day}.jsonl", f"Chat log {day}", content)
            else:
                repo.update_file(f"{REPO_FOLDER}/{day}.jsonl", f"Chat log {day}", content, sha)
        except Exception as e:
            ok = False
            print(f"Chat log sync for {day} failed: {e}")
            with _lock:
                _dirty_days.add(day)  # try again next round
    return ok


def _sync_loop():
    while True:
        time.sleep(SYNC_SECONDS)
        try:
            sync_now()
            sync_xlsx()
        except Exception as e:
            print(f"Chat log sync crashed: {e}")


def _start_syncer():
    global _syncer_started
    if _syncer_started:
        return
    _syncer_started = True
    threading.Thread(target=_sync_loop, daemon=True).start()
    import atexit
    atexit.register(sync_now)


def _maybe_purge():
    global _last_purge
    if time.time() - _last_purge < 6 * 3600:
        return
    _last_purge = time.time()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
    try:
        for name in os.listdir(LOG_DIR):
            if name.endswith(".jsonl") and name[:-6] < cutoff:
                os.remove(os.path.join(LOG_DIR, name))
    except Exception as e:
        print(f"Chat log purge failed: {e}")
    if REPO_SYNC:
        threading.Thread(target=_purge_repo, args=(cutoff,), daemon=True).start()


def _purge_repo(cutoff: str):
    repo = _repo()
    if repo is None:
        return
    try:
        for item in repo.get_contents(REPO_FOLDER):
            name = item.name
            if name.endswith(".jsonl") and name[:-6] < cutoff:
                repo.delete_file(item.path, f"Delete chat log older than {RETENTION_DAYS} days", item.sha)
    except Exception as e:
        print(f"Chat log repo purge skipped: {e}")


def read_log(days: int = 7, query: str = "", mode: str = "", limit: int = 200) -> list[dict]:
    """Newest first. query matches the question or answer, mode matches exactly."""
    days = max(1, min(int(days), RETENTION_DAYS))
    limit = max(1, min(int(limit), 20000))
    query = (query or "").strip().lower()
    out: list[dict] = []
    repo = _repo() if (ENABLED and REPO_SYNC) else None
    today = datetime.now(timezone.utc).date()
    day_list = [(today - timedelta(days=back)).strftime("%Y-%m-%d") for back in range(days)]
    remote_by_day: dict[str, list[str]] = {}
    if repo is not None:
        # Fetch the days side by side: one request per day is slow when done one after another.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            for d, res in zip(day_list, pool.map(lambda d: _remote_file(repo, d)[1], day_list)):
                remote_by_day[d] = res
    for day in day_list:
        lines = _local_lines(day)
        if repo is not None:
            lines = _merged(remote_by_day.get(day, []), lines)
        for line in reversed(lines):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if mode and rec.get("mode") != mode:
                continue
            if query and query not in (rec.get("question", "") + " " + rec.get("answer", "")).lower():
                continue
            out.append(rec)
            if len(out) >= limit:
                return out
    return out


def to_csv(records: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["time_utc", "conversation", "mode", "question", "answer", "lectures", "ms"])
    for r in records:
        w.writerow([_safe_cell(x) for x in (r.get("ts"), r.get("session"), r.get("mode"), r.get("question"),
                    r.get("answer"), "; ".join(r.get("lectures", [])), r.get("ms"))])
    return buf.getvalue()


# ---------- Excel export ----------

XLSX_EVERY = int(os.getenv("CHATLOG_XLSX_SECONDS", "3600"))
_last_xlsx = 0.0


def _safe_cell(value) -> str:
    """Stop a student's text from running as a spreadsheet formula, and respect Excel's cell limit."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text[:32000]


def to_xlsx(records: list[dict]) -> bytes:
    """Two sheets: Chats (one row per question, newest first) and Summary (counts per day and per mode)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Chats"
    head = ["Date (UTC)", "Time (UTC)", "Conversation", "Mode", "Question", "Answer", "Lecture notes used", "Seconds"]
    ws.append(head)
    for r in records:
        ts = str(r.get("ts") or "")
        day, _, clock = ts.replace("T", " ").partition(" ")
        ms = r.get("ms")
        ws.append([
            _safe_cell(day), _safe_cell(clock[:8]), _safe_cell(r.get("session")), _safe_cell(r.get("mode")),
            _safe_cell(r.get("question")), _safe_cell(r.get("answer")),
            _safe_cell("; ".join(r.get("lectures", []) or [])),
            round(ms / 1000, 1) if isinstance(ms, (int, float)) else None,
        ])
    fill = PatternFill("solid", fgColor="1F3864")
    for c in ws[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = fill
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name="Arial", size=10)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    for col, width in zip("ABCDEFGH", (12, 10, 16, 14, 50, 80, 30, 9)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    sm = wb.create_sheet("Summary")
    by_day: dict[str, dict[str, int]] = {}
    modes: set[str] = set()
    for r in records:
        day = str(r.get("ts") or "")[:10]
        mode = str(r.get("mode") or "ask")
        modes.add(mode)
        by_day.setdefault(day, {}).setdefault(mode, 0)
        by_day[day][mode] += 1
    cols = sorted(modes)
    sm.append(["Date (UTC)", "Total"] + cols)
    for day in sorted(by_day, reverse=True):
        sm.append([day, sum(by_day[day].values())] + [by_day[day].get(m, 0) for m in cols])
    for c in sm[1]:
        c.font = Font(name="Arial", bold=True, color="FFFFFF")
        c.fill = fill
    sm.column_dimensions["A"].width = 14
    sm.freeze_panes = "A2"
    sm.append([])
    sm.append(["Counts are of the rows in the Chats sheet only (the period named in the file or download)."])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _put_file(repo, path: str, data: bytes, message: str):
    try:
        sha = repo.get_contents(path).sha
    except Exception:
        sha = None
    if sha is None:
        repo.create_file(path, message, data)
    else:
        repo.update_file(path, message, data, sha)


def _file_exists(repo, path: str) -> bool:
    try:
        repo.get_contents(path)
        return True
    except Exception:
        return False


def sync_xlsx(force: bool = False) -> bool:
    """One Excel file per week (Monday to Sunday, UTC) in the notes repo: chatlog/excel/week-YYYY-MM-DD.xlsx.
    The current week is rebuilt at most once an hour. The previous week is rebuilt on Mondays (to catch the
    last entries) and whenever its file is missing. Older weeks are never touched again."""
    global _last_xlsx
    if not (ENABLED and REPO_SYNC):
        return True
    if not force and time.time() - _last_xlsx < XLSX_EVERY:
        return True
    repo = _repo()
    if repo is None:
        return False
    try:
        today = datetime.now(timezone.utc).date()
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        recent = read_log((today - last_monday).days + 1, "", "", 20000)
        for monday in (this_monday, last_monday):
            path = f"{REPO_FOLDER}/excel/week-{monday.isoformat()}.xlsx"
            if monday == last_monday and today.weekday() != 0 and _file_exists(repo, path):
                continue
            start, end = monday.isoformat(), (monday + timedelta(days=6)).isoformat()
            rows = [r for r in recent if start <= str(r.get("ts") or "")[:10] <= end]
            if not rows:
                continue
            _put_file(repo, path, to_xlsx(rows), f"Chat log Excel, week of {start}")
        _last_xlsx = time.time()
        return True
    except Exception as e:
        print(f"Chat log Excel copy failed: {e}")
        return False