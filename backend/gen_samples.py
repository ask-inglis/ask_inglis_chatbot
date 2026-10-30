"""Generate a sample set of Phase 2 questions across lectures and write a review sheet.

Run the backend first (uvicorn main:app, wait for "ready"), then, from a second terminal:
    set -a; source .env; set +a
    python3 gen_samples.py                      # ~6 topics x 10 questions, nothing saved to the repo
    python3 gen_samples.py --per-topic 5        # fewer per topic
    python3 gen_samples.py --save               # also save each batch in the private repo

Needs ADMIN_API_KEY in the environment (never printed). BASE_URL defaults to http://localhost:8000.
Writes samples_review.csv: one row per accepted question, with blank columns for you to fill in.
"""
import argparse
import collections
import csv
import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
TOPICS = [
    "the middle ear: ossicles, muscles, and the walls of the middle ear cavity",
    "the brachium: arteries, veins and nerves of the arm",
    "the hand: intrinsic muscles and their nerve supply",
    "the anterior triangle of the neck: muscles and nerves",
    "the perineum: urogenital triangle and anal triangle",
    "the spinal cord and meninges",
]


def post(path: str, body: dict, key: str) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-Admin-Key": key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-topic", type=int, default=10)
    ap.add_argument("--save", action="store_true", help="also save each batch to the private repo")
    ap.add_argument("--out", default="samples_review.csv")
    args = ap.parse_args()

    key = os.environ.get("ADMIN_API_KEY")
    if not key:
        raise SystemExit("ADMIN_API_KEY is not set in the environment")

    reasons = collections.Counter()
    rows, asked, got = [], 0, 0
    for topic in TOPICS:
        asked += args.per_topic
        try:
            res = post("/admin/questions/generate",
                       {"topic": topic, "count": args.per_topic, "save": args.save}, key)
        except urllib.error.HTTPError as e:
            print(f"[{topic[:30]}] HTTP {e.code}: {e.read()[:150]!r}")
            continue
        except Exception as e:
            print(f"[{topic[:30]}] failed: {e!r}")
            continue
        items, rejected = res.get("items", []), res.get("rejected", [])
        got += len(items)
        for r in rejected:
            for p in r.get("problems", []):
                reasons[p] += 1
        print(f"[{topic[:40]}] accepted {len(items)}, rejected {len(rejected)}")
        for it in items:
            opts = it["options"]
            rows.append({
                "topic": topic, "stem": it["stem"],
                **{l: opts.get(l, "") for l in "ABCDE"},
                "answer": it["answer"], "explanation": it["explanation"], "lecture": it["lecture"],
                "evidence_from_notes": it.get("evidence", ""),
                "option_notes": json.dumps(it.get("option_notes", {}), ensure_ascii=False),
                "study_tip": it.get("study_tip", ""),
                "checked_against_notes": "", "keep": "", "comment": "",
            })
        time.sleep(2)  # stay well under free-tier per-minute limits

    cols = ["topic", "stem", "A", "B", "C", "D", "E", "answer", "explanation", "lecture", "evidence_from_notes", "option_notes", "study_tip",
            "checked_against_notes", "keep", "comment"]
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"\nAccepted {got} of {asked} asked. Wrote {args.out}")
    if reasons:
        print("Most common rejection reasons:")
        for reason, n in reasons.most_common():
            print(f"  {n:3d}  {reason}")


if __name__ == "__main__":
    main()