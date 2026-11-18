"""Keeps the app inside the AI key's request limits, and says plainly when it is near them.

Settings (environment variables, 0 turns a limit off):
  LLM_RPM_LIMIT           requests per minute you want to allow. Set it a little below the key's real limit.
  LLM_RPD_LIMIT           requests per day you want to allow. Set it a little below the key's real limit.
  LLM_QUIZ_SHED_PERCENT   share of a limit at which new quizzes are refused (default 70)
  LLM_QUIZ_CALLS_NEEDED   requests a new quiz may need; refused if less than this is left this minute (default 4)
  LLM_DAY_TZ              time zone where the day resets (default America/Los_Angeles, which is Gemini's reset)

Counts live in memory (the app runs one worker), so a restart resets them.
"""
import os
import threading
import time
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo


class LLMBusy(Exception):
    """The configured request limit was reached. Callers treat it like any other AI failure."""


class LLMBudget:
    def __init__(self, rpm: int, rpd: int, shed_percent: float, quiz_calls: int, day_tz: str):
        self.rpm = max(0, rpm)
        self.rpd = max(0, rpd)
        self.shed = min(max(shed_percent, 1.0), 100.0) / 100.0
        self.quiz_calls = max(1, quiz_calls)
        try:
            self.tz = ZoneInfo(day_tz)
        except Exception:
            self.tz = ZoneInfo("America/Los_Angeles")
        self._lock = threading.Lock()
        self._times = deque()          # start times of requests in the last 60 seconds
        self._day = self._today()
        self._day_count = 0
        self.peak_minute = 0
        self.total = 0
        self.refused_requests = 0      # requests turned away at the hard limit
        self.refused_quizzes = 0       # quizzes turned away early
        self.first_refusal_at = None

    def _today(self) -> str:
        return datetime.now(self.tz).strftime("%Y-%m-%d")

    def _roll(self, now: float):
        while self._times and now - self._times[0] >= 60:
            self._times.popleft()
        today = self._today()
        if today != self._day:
            self._day, self._day_count = today, 0

    def spend(self):
        """Count one request. Raises LLMBusy instead if a limit has been reached."""
        now = time.time()
        with self._lock:
            self._roll(now)
            if (self.rpm and len(self._times) >= self.rpm) or (self.rpd and self._day_count >= self.rpd):
                self.refused_requests += 1
                self.first_refusal_at = self.first_refusal_at or datetime.now(self.tz).isoformat(timespec="seconds")
                raise LLMBusy("AI request limit reached")
            self._times.append(now)
            self._day_count += 1
            self.total += 1
            self.peak_minute = max(self.peak_minute, len(self._times))

    def quiz_allowed(self) -> tuple[bool, str]:
        """(True, '') when a new quiz may start. Otherwise (False, reason) with reason 'minute' or 'day'."""
        now = time.time()
        with self._lock:
            self._roll(now)
            used_min, used_day = len(self._times), self._day_count
            if self.rpm and (used_min >= self.rpm * self.shed or self.rpm - used_min < self.quiz_calls):
                self.refused_quizzes += 1
                return False, "minute"
            if self.rpd and (used_day >= self.rpd * self.shed or self.rpd - used_day < self.quiz_calls):
                self.refused_quizzes += 1
                return False, "day"
            return True, ""

    def status(self) -> dict:
        now = time.time()
        with self._lock:
            self._roll(now)
            used_min, used_day = len(self._times), self._day_count
            return {
                "limits": {"per_minute": self.rpm or None, "per_day": self.rpd or None,
                           "quiz_refused_at_percent": round(self.shed * 100), "day_resets_in_tz": str(self.tz)},
                "used": {"last_minute": used_min, "today": used_day},
                "left": {"this_minute": (self.rpm - used_min) if self.rpm else None,
                         "today": (self.rpd - used_day) if self.rpd else None},
                "since_start": {"requests": self.total, "peak_in_one_minute": self.peak_minute,
                                "requests_turned_away": self.refused_requests,
                                "quizzes_turned_away": self.refused_quizzes,
                                "first_turned_away_at": self.first_refusal_at},
            }


llm_budget = LLMBudget(
    int(os.environ.get("LLM_RPM_LIMIT", "0")),
    int(os.environ.get("LLM_RPD_LIMIT", "0")),
    float(os.environ.get("LLM_QUIZ_SHED_PERCENT", "70")),
    int(os.environ.get("LLM_QUIZ_CALLS_NEEDED", "4")),
    os.environ.get("LLM_DAY_TZ", "America/Los_Angeles"),
)

BUSY_MINUTE_REPLY = (
    "- The AI helper is at its usage limit for this minute, so I couldn't start your quiz.\n"
    "- Please try again in about a minute."
)
BUSY_DAY_REPLY = (
    "- The AI helper has reached its usage limit for today, so quizzes are paused.\n"
    "- Questions still work and show passages from the course notes. Quizzes come back tomorrow."
)