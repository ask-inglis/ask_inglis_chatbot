"""Phase 1 backend check. Start the server first, then:
    set -a; source .env; set +a
    python3 phase1_check.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
KEY = os.environ.get("ADMIN_API_KEY", "")
ALLOWED_ORIGIN = os.environ.get("TEST_ALLOWED_ORIGIN", "http://localhost:5173")
STAMP = str(int(time.time()))
results = []


def call(method, path, body=None, admin=False, headers=None, timeout=120):
    h = {"Content-Type": "application/json"}
    if admin:
        h["X-Admin-Key"] = KEY
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw, status, hdrs = r.read().decode(), r.status, r.headers
    except urllib.error.HTTPError as e:
        raw, status, hdrs = e.read().decode(), e.code, e.headers
    try:
        parsed = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        parsed = raw
    return status, parsed, {k.lower(): v for k, v in hdrs.items()}


def chat(message, history=None):
    body = {"message": message}
    if history:
        body["history"] = history
    start = time.time()
    status, data, _ = call("POST", "/chat", body)
    return status, (data if isinstance(data, dict) else {}), time.time() - start


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok or not detail else f"   [{detail}]"))


def poll(fn, timeout=40, every=3):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(every)
    return None


def find_group(prefix, count=None):
    status, groups, _ = call("GET", "/admin/feedback/queue?limit=200", admin=True)
    if status != 200 or not isinstance(groups, list):
        return None
    for g in groups:
        if g["question"].startswith(prefix) and (count is None or g["count"] == count):
            return g
    return None


print(f"Checking {BASE}\n")

# ---- 1. Startup and protection ----
s, d, _ = call("GET", "/")
check("Server is up and ready", s == 200 and isinstance(d, dict) and d.get("ready") is True, str(d))
s, _, _ = call("GET", "/admin/notes")
check("Admin route refuses a missing key", s == 401, f"status {s}")
s, _, _ = call("GET", "/admin/notes", headers={"X-Admin-Key": "wrong"})
check("Admin route refuses a wrong key", s == 401, f"status {s}")
s, notes, _ = call("GET", "/admin/notes", admin=True)
check("Admin route accepts the key and lists notes", s == 200 and isinstance(notes, list) and len(notes) > 0, f"status {s}")
print(f"      notes in the index: {len(notes) if isinstance(notes, list) else 0}")

# ---- 2. Chat answers ----
q1 = "What does the biceps brachii do?"
s, d1, t1 = chat(q1)
reply = d1.get("reply", "")
bullets = [l for l in reply.splitlines() if l.strip().startswith("-")]
check("Chat returns an answer", s == 200 and bool(reply), f"status {s}")
check("Answer names its lecture(s)", bool(d1.get("lectures")))
check("Answer is bulleted (3+ bullet lines)", len(bullets) >= 3, f"{len(bullets)} bullet lines")
check("Answer has a bold lead line", "**" in reply)
s, d2, t2 = chat(q1)
check("Repeated question comes from the cache", s == 200 and d2.get("reply") == reply and t2 < 1.0,
      f"first {t1:.1f}s, second {t2:.2f}s")

history = [{"role": "user", "content": q1}, {"role": "assistant", "content": reply}]
s, d, _ = chat("What nerve supplies it?", history)
check("Follow-up uses memory (names the musculocutaneous nerve)", s == 200 and "musculocutaneous" in d.get("reply", "").lower(),
      d.get("reply", "")[:120])
s, d, _ = chat("How do I find the brachial artery in an obese specimen?")
check("Body-type question gets a body type note", s == 200 and "body type" in d.get("reply", "").lower(),
      d.get("reply", "")[:120])
s, d, _ = chat("Give me 3 questions on the brachium")
check("Quiz mode is off (Phase 2 not shown)", s == 200 and "Question 1" not in d.get("reply", ""),
      "set QUIZ_IN_CHAT=false in .env and restart")

# ---- 3. Typed 'that's wrong' goes to a human ----
qd = f"Test {STAMP}: which nerve supplies the biceps brachii?"
s, dd, _ = chat(qd)
s, dv, _ = chat("That is wrong", [{"role": "user", "content": qd},
                                  {"role": "assistant", "content": dd.get("reply", "")}])
check("Typed 'that is wrong' is queued, not corrected", s == 200 and "Dr. Inglis" in dv.get("reply", ""), str(dv)[:120])
gd = poll(lambda: find_group(f"Test {STAMP}: which nerve"))
check("Disputed answer shows up in the review queue", bool(gd))
check("It is marked reason 'incorrect'", bool(gd) and gd["reason"] == "incorrect")
check("It names the lectures the answer used", bool(gd) and bool(gd["lectures"]), "empty lectures")

# ---- 4. Thumbs up and down, grouped queue ----
qg = f"Test {STAMP}: what does the brachialis muscle do?"
s, d, _ = call("POST", "/feedback", {"question": f"Test {STAMP} up", "answer": "a", "rating": "up", "comment": "", "lectures": []})
check("Thumbs-up is accepted", s == 200 and isinstance(d, dict) and d.get("status") == "received", str(d))
for comment in ("this is wrong", "incorrect answer"):
    s, d, _ = call("POST", "/feedback", {"question": qg, "answer": "x", "rating": "down", "comment": comment, "lectures": []})
    check(f"Thumbs-down accepted ({comment})", s == 200, str(d))
g = poll(lambda: find_group(f"Test {STAMP}: what does the brachialis", 2))
check("Two identical thumbs-downs become ONE group with count 2", bool(g), "group not found or count is not 2")
check("Group is marked 'incorrect' and lists both comments", bool(g) and g["reason"] == "incorrect" and len(g["comments"]) == 2)
check("A thumbs-up is not in the review queue", find_group(f"Test {STAMP} up") is None)

if g:
    s, d, _ = call("POST", f"/admin/feedback/{g['ids'][0]}/resolve", {"decision": "note_fixed", "comment": "test"}, admin=True)
    check("Resolve one item", s == 200 and isinstance(d, dict) and d.get("status") == "resolved", str(d)[:120])
    g2 = poll(lambda: find_group(f"Test {STAMP}: what does the brachialis", 1))
    check("Group count drops to 1", bool(g2))
    s, d, _ = call("POST", "/admin/feedback/resolve-group", {"fingerprint": g["fingerprint"], "decision": "answer_was_correct"}, admin=True)
    check("Resolve the rest of the group", s == 200 and isinstance(d, dict) and d.get("resolved") == 1, str(d))
    check("Group leaves the queue", find_group(f"Test {STAMP}: what does the brachialis") is None)
    s, _, _ = call("POST", f"/admin/feedback/{g['ids'][0]}/resolve", {"decision": "banana"}, admin=True)
    check("A bad decision value is refused", s == 400, f"status {s}")

if gd:
    # Closes only this test item. (resolve-lecture is not run: it would also close real pending items.)
    s, d, _ = call("POST", "/admin/feedback/resolve-group", {"fingerprint": gd["fingerprint"], "decision": "dismissed"}, admin=True)
    check("Close the typed-dispute item", s == 200 and isinstance(d, dict) and d.get("resolved", 0) >= 1, str(d))

# ---- 5. Notes pipeline (what the review tool calls) ----
zname = f"Zorbit Test {STAMP}"
ztext = "The zorbit muscle is a fictional muscle of the forearm that flexes the elbow and rotates the wrist."
try:
    s, d, _ = call("POST", "/admin/ingest-note", {"filename": zname, "text": ztext}, admin=True)
    check("Ingest a new note", s == 200 and isinstance(d, dict) and d.get("status") == "ingested", str(d))
    s, d, _ = chat("What does the zorbit muscle do?")
    check("Chat answers from the new note", s == 200 and "zorbit" in d.get("reply", "").lower(), d.get("reply", "")[:120])
    s, d, _ = call("GET", f"/admin/notes/{zname.replace(' ', '%20')}", admin=True)
    check("Read the note back", s == 200 and isinstance(d, dict) and "zorbit" in d.get("text", ""), f"status {s}")
    s, d, _ = call("POST", "/admin/ingest-note", {"filename": zname, "text": ztext}, admin=True)
    check("Same text again is skipped", s == 200 and isinstance(d, dict) and d.get("status") == "skipped", str(d))
    s, d, _ = call("POST", "/admin/ingest-note", {"filename": zname, "text": ztext + " It also does not exist."}, admin=True)
    check("Changed text replaces the note", s == 200 and isinstance(d, dict) and d.get("status") == "ingested", str(d))
finally:
    s, d, _ = call("DELETE", f"/admin/notes/{zname.replace(' ', '%20')}", admin=True)
    check("Delete the test note", s == 200, f"status {s}")
s, listing, _ = call("GET", "/admin/notes", admin=True)
check("Test note is gone from the list", isinstance(listing, list) and all(n["filename"] != zname for n in listing))
s, d, _ = chat("What does the zorbit muscle do?")
check("Chat no longer finds the deleted note", s == 200 and "fictional muscle of the forearm" not in d.get("reply", "").lower())

# ---- 6. Input limits and CORS ----
s, _, _ = call("POST", "/chat", {"message": "a" * 2500})
check("Oversized message is refused (422)", s == 422, f"status {s}")
s, _, _ = call("POST", "/chat", {})
check("Missing message is refused (422)", s == 422, f"status {s}")
s, _, _ = call("POST", "/feedback", {"question": "q", "answer": "a", "rating": "maybe"})
check("Bad rating is refused (400)", s == 400, f"status {s}")
_, _, h = call("OPTIONS", "/chat", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
check("CORS refuses an unknown website", "access-control-allow-origin" not in h, "set ALLOWED_ORIGINS in .env")
_, _, h = call("OPTIONS", "/chat", headers={"Origin": ALLOWED_ORIGIN, "Access-Control-Request-Method": "POST"})
check("CORS allows your own site", h.get("access-control-allow-origin") in (ALLOWED_ORIGIN, "*"), str(h.get("access-control-allow-origin")))

passed = sum(results)
print(f"\n{passed} of {len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)