import hashlib
import re
from collections import OrderedDict

DISPUTE_PATTERN = re.compile(
    r"\b(that(['’]s| is)|this is|it(['’]s| is)|you(['’]re| are)|the answer is)\b[^.?!]{0,30}"
    r"\b(wrong|incorrect|not (right|true|correct)|a mistake|mistaken)\b"
    r"|\bare you sure\b",
    re.IGNORECASE,
)

DISPUTE_REPLY = (
    "- Thanks for flagging this.\n"
    "- I can't confirm it myself, so I've sent it to Dr. Inglis to review.\n"
    "- Until then, please check your lecture notes."
)

# Remembers which lectures each answer used, so a later "that's wrong" can name them.
# Kept in memory only, so it resets when the server restarts.
MAX_REMEMBERED = 500
_answers: OrderedDict[str, list[str]] = OrderedDict()


def looks_like_dispute(message: str) -> bool:
    return len(message.split()) <= 40 and bool(DISPUTE_PATTERN.search(message))


def strip_image_markdown(text: str) -> str:
    """Remove the image and credit line that /chat appends to a reply."""
    return re.sub(r"\n*!\[[^\]]*\]\([^)]*\)\s*(\*[^*]*\*)?\s*$", "", text).strip()


def last_exchange(history):
    """(question, answer) for the most recent user -> assistant pair, or None."""
    for i in range(len(history) - 1, 0, -1):
        if history[i].role == "assistant" and history[i - 1].role == "user":
            return history[i - 1].content, strip_image_markdown(history[i].content)
    return None


def _key(answer: str) -> str:
    cleaned = " ".join(strip_image_markdown(answer).split())
    return hashlib.sha1(cleaned.encode()).hexdigest()


def remember_answer(answer: str, lectures: list[str]):
    if not lectures:
        return
    key = _key(answer)
    _answers[key] = list(lectures)
    _answers.move_to_end(key)
    while len(_answers) > MAX_REMEMBERED:
        _answers.popitem(last=False)


def recall_lectures(answer: str) -> list[str]:
    return _answers.get(_key(answer), [])


def dispute_record(question: str, answer: str, claim: str, lectures=None) -> dict:
    return {
        "question": question[:2000],
        "answer": answer[:6000],
        "rating": "down",
        "reason": "incorrect",
        "comment": claim[:1000],
        "lectures": lectures or [],
        "source": "dispute",
    }