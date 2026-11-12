"""A limit on how many practice quizzes are written at the same moment.

Writing one quiz means several model calls. If 40 students ask for a quiz in the same few seconds, the AI
gateway is flooded and times out (504). This lets only a few quizzes run at once, makes the next few wait their
turn, and tells anyone beyond that to try again in a minute. The calls reach the gateway as a steady stream.

Settings (environment variables):
  QUIZ_MAX_CONCURRENT  quizzes being written at the same time (default 6)
  QUIZ_MAX_WAITING     students allowed to wait in line (default 14). Each waiting student holds one server
                       thread, so keep this well below the server's thread limit (40 by default).
  QUIZ_WAIT_SECONDS    longest a student waits in line before being asked to try again (default 150)
"""
import os
import threading


class QuizGate:
    def __init__(self, max_concurrent: int, max_waiting: int, wait_seconds: float):
        self.slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self.max_waiting = max(0, max_waiting)
        self.wait_seconds = wait_seconds
        self._waiting = 0
        self._lock = threading.Lock()

    def enter(self) -> bool:
        """True when it is this quiz's turn (call leave() afterwards). False when the line is full or the wait ran out."""
        if self.slots.acquire(blocking=False):
            return True
        with self._lock:
            if self._waiting >= self.max_waiting:
                return False
            self._waiting += 1
        try:
            return self.slots.acquire(timeout=self.wait_seconds)
        finally:
            with self._lock:
                self._waiting -= 1

    def leave(self):
        self.slots.release()

    @property
    def waiting(self) -> int:
        return self._waiting


quiz_gate = QuizGate(
    int(os.environ.get("QUIZ_MAX_CONCURRENT", "6")),
    int(os.environ.get("QUIZ_MAX_WAITING", "14")),
    float(os.environ.get("QUIZ_WAIT_SECONDS", "150")),
)

BUSY_REPLY = (
    "- Many students are generating quizzes right now, so I couldn't start yours.\n"
    "- Please try again in about a minute."
)