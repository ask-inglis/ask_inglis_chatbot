"""Gentle spelling help for anatomy terms.

The vocabulary is every word in Dr. Inglis's notes. A word in a student's question that is NOT in the
notes, but is one or two letters away from a word that appears there several times, is probably a typo
("brachilais" for "brachialis"). We fix it before searching the notes and tell the student what we
searched for. Words the notes already contain are never touched, and plain endings
(describe/described, nerve/nerves) are left alone, so ordinary English is not "corrected".
"""
import difflib
import re
import threading
from collections import Counter

WORD = re.compile(r"[A-Za-z]+")
MIN_WORD = 7          # shorter words are too easy to confuse with each other
MIN_COUNT = 2         # the notes must use the target word at least this often
CUTOFF = 0.82         # how similar the two spellings must be (1.0 = identical)

_lock = threading.Lock()
_counts: Counter = Counter()
_by_letter: dict[str, list[str]] = {}


def build_vocabulary(documents: list[str]) -> int:
    """Run on startup and after notes change. Returns the number of distinct words kept."""
    counts = Counter(w.lower() for doc in documents for w in WORD.findall(doc or "") if len(w) >= 4)
    by_letter: dict[str, list[str]] = {}
    for word, n in counts.items():
        if n >= MIN_COUNT and len(word) >= MIN_WORD:
            by_letter.setdefault(word[0], []).append(word)
    with _lock:
        _counts.clear()
        _counts.update(counts)
        _by_letter.clear()
        _by_letter.update(by_letter)
    return len(counts)


def _inflection(a: str, b: str) -> bool:
    """describe/described, nerve/nerves, flexion/flexions: a plain ending, not a typo."""
    short, long_ = sorted((a, b), key=len)
    return long_.startswith(short) and len(long_) - len(short) <= 3


def _best_match(word: str) -> str | None:
    candidates = [c for c in _by_letter.get(word[0], []) if abs(len(c) - len(word)) <= 2]
    matches = difflib.get_close_matches(word, candidates, n=1, cutoff=CUTOFF)
    if not matches or _inflection(word, matches[0]):
        return None
    return matches[0]


def _like(original: str, fixed: str) -> str:
    if original.isupper() and len(original) > 1:
        return fixed.upper()
    return fixed.capitalize() if original[:1].isupper() else fixed


def correct_query(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Returns (text with obvious typos fixed, [(typed, fixed), ...]). Unchanged if nothing is clearly wrong."""
    with _lock:
        if not _by_letter:
            return text, []
        changes: list[tuple[str, str]] = []

        def fix(m: re.Match) -> str:
            word = m.group(0)
            low = word.lower()
            if len(low) < MIN_WORD or _counts.get(low, 0) > 0:
                return word
            match = _best_match(low)
            if not match:
                return word
            repaired = _like(word, match)
            changes.append((word, repaired))
            return repaired

        fixed = WORD.sub(fix, text or "")
    return (fixed, changes) if changes else (text, [])