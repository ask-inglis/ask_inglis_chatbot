"""Print the weekly report for the instructor: what students ask about, miss, and report.

Run the backend first, then from a second terminal:
    set -a; source .env; set +a
    python3 weekly_report.py                 # last 4 weeks, as readable text
    python3 weekly_report.py --weeks 8
    python3 weekly_report.py --csv > report.csv

Needs ADMIN_API_KEY in the environment (never printed). BASE_URL defaults to http://localhost:8000.
The report holds lecture names and counts only. No student wording is stored anywhere.
"""
import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")


def fetch(weeks: int) -> dict:
    key = os.environ.get("ADMIN_API_KEY", "")
    if not key:
        sys.exit("ADMIN_API_KEY is not set. Run: set -a; source .env; set +a")
    req = urllib.request.Request(f"{BASE_URL}/admin/report?weeks={weeks}", headers={"X-Admin-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"The backend answered {e.code}. 401 means the admin key does not match.")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach {BASE_URL}: {e.reason}")


def text(report: dict):
    print(f"Weeks covered: {', '.join(report['weeks']) or 'none yet'}")
    print(f"Questions answered: {report['answers']}  {report['by_mode']}\n")
    print("Most asked about")
    for r in report["most_asked_lectures"] or [{"lecture": "(nothing yet)", "answers": 0}]:
        print(f"  {r['answers']:>4}  {r['lecture']}")
    print("\nMost missed in practice quizzes (at least 3 answers)")
    for r in report["most_missed_in_quizzes"] or [{"lecture": "(nothing yet)", "missed": 0, "answered": 0, "miss_rate": 0}]:
        print(f"  {int(r['miss_rate'] * 100):>3}%  {r['lecture']}  ({r['missed']} of {r['answered']})")
    print("\nStudent reports (thumbs-down), by lecture")
    for r in report["reports_by_lecture"] or [{"lecture": "(none)", "reports": 0, "pending": 0}]:
        print(f"  {r['reports']:>4} reports, {r['pending']} waiting  {r['lecture']}")
    print("\nReviewed by the instructor")
    for name, day in sorted(report["reviewed_lectures"].items()):
        print(f"  {day}  {name}")


def as_csv(report: dict):
    w = csv.writer(sys.stdout)
    w.writerow(["section", "lecture", "count_or_rate", "detail"])
    for r in report["most_asked_lectures"]:
        w.writerow(["asked", r["lecture"], r["answers"], ""])
    for r in report["most_missed_in_quizzes"]:
        w.writerow(["missed", r["lecture"], r["miss_rate"], f"{r['missed']} of {r['answered']}"])
    for r in report["reports_by_lecture"]:
        w.writerow(["reports", r["lecture"], r["reports"], f"{r['pending']} waiting"])
    for name, day in sorted(report["reviewed_lectures"].items()):
        w.writerow(["reviewed", name, day, ""])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--csv", action="store_true")
    args = ap.parse_args()
    data = fetch(args.weeks)
    as_csv(data) if args.csv else text(data)