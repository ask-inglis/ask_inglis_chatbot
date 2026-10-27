import json
import os
import random
import re
import uuid
from datetime import datetime, timezone

from ingestion import collection, get_github_repo
from guides import rule_excerpts

MAX_PER_REQUEST = 10
OPTIONS_PER_QUESTION = int(os.environ.get("QUESTION_OPTIONS", "5"))
QUESTIONS_PATH = "questions/generated"  # saved batches, no review step
LETTERS = "ABCDE"
MIN_GROUNDED = 0.6  # share of the correct answer's key words that must appear in the notes

NEGATIVE = re.compile(r"\b(not|except|least|incorrect|false)\b", re.IGNORECASE)
ABSOLUTE = re.compile(r"\b(always|never)\b", re.IGNORECASE)
ALL_NONE = re.compile(r"\b(all|none|both)\s+of\s+(the\s+)?(above|these|the following)\b", re.IGNORECASE)
LETTER_COMBO = re.compile(r"^\s*[A-E]\s*(and|&|,|or)\s*[A-E]\s*$", re.IGNORECASE)

QUIZ_PATTERN = re.compile(
    r"\b(quiz me|test me|practice (questions?|quiz|test)|multiple[- ]choice|mcqs?|exam questions?|"
    r"(write|make|generate|give me|create)\b.{0,25}\bquestions?)\b",
    re.IGNORECASE,
)
COUNT_PATTERN = re.compile(r"\b(\d{1,2})\s+(?:\w+\s+){0,2}(?:questions?|mcqs?|quiz)", re.IGNORECASE)


def parse_quiz_request(message: str) -> int | None:
    """If the message asks for practice questions, return how many (default 5), else None."""
    if len(message) > 300 or not QUIZ_PATTERN.search(message):
        return None
    m = COUNT_PATTERN.search(message)
    return max(1, min(int(m.group(1)), MAX_PER_REQUEST)) if m else 5


def validate_item(item: dict, n_options: int = OPTIONS_PER_QUESTION) -> list[str]:
    """Mechanical item-writing checks. Returns a list of problems (empty means it passed)."""
    problems = []
    stem = item.get("stem", "")
    options = item.get("options", {})
    answer = item.get("answer", "")

    if len(stem.split()) < 6:
        problems.append("question is too short")
    lead_in = re.split(r"(?<=[.?!])\s+", stem.strip())[-1]
    if NEGATIVE.search(lead_in):
        problems.append("negative wording in the question (NOT, EXCEPT, LEAST)")
    if ABSOLUTE.search(stem) or any(ABSOLUTE.search(t) for t in options.values()):
        problems.append("absolute word (always, never)")

    if sorted(options) != list(LETTERS[:n_options]):
        problems.append(f"needs exactly {n_options} options labeled {LETTERS[:n_options]}")
        return problems

    texts = [t.strip() for t in options.values()]
    if any(not t for t in texts):
        problems.append("an option is empty")
    if len({t.lower() for t in texts}) != len(texts):
        problems.append("duplicate options")
    if any(ALL_NONE.search(t) or LETTER_COMBO.match(t) for t in texts):
        problems.append("'all/none of the above' or combined options")

    if answer not in options:
        problems.append("answer is not one of the options")
        return problems

    lens = {k: len(v) for k, v in options.items()}
    others = sorted(l for k, l in lens.items() if k != answer)
    median = others[len(others) // 2]
    if lens[answer] == max(lens.values()) and lens[answer] > 1.15 * max(median, 1):
        problems.append("the correct answer is noticeably the longest option")
    if max(lens.values()) > 3 * max(min(lens.values()), 1):
        problems.append("options differ a lot in length")
    if not item.get("explanation"):
        problems.append("missing explanation")
    return problems


def is_grounded(item: dict, notes: str) -> bool:
    """The correct answer's key words must appear in the notes the question was written from.
    This catches answers that aren't in the notes at all. It can't catch a wrong term that
    happens to appear somewhere in them."""
    words = re.findall(r"[a-z]{4,}", item["options"][item["answer"]].lower())
    if not words:
        return True
    text = notes.lower()
    return sum(1 for w in words if w in text) / len(words) >= MIN_GROUNDED


def _normalize(raw: dict) -> dict | None:
    options = raw.get("options")
    if not isinstance(options, dict):
        return None
    clean = {}
    for key, text in options.items():
        letter = str(key).strip().upper()[:1]
        clean[letter] = re.sub(r"^[A-E][\.\)]\s*", "", str(text).strip())
    return {
        "stem": str(raw.get("stem", "")).strip(),
        "options": clean,
        "answer": str(raw.get("answer", "")).strip().upper()[:1],
        "explanation": str(raw.get("explanation", "")).strip(),
        "lecture": str(raw.get("lecture", "")).strip(),
    }


def _parse_items(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []


def _shuffle_options(item: dict) -> dict:
    """Randomize which letter is correct, since models tend to favor certain letters."""
    letters = sorted(item["options"])
    correct_text = item["options"][item["answer"]]
    texts = [item["options"][l] for l in letters]
    random.shuffle(texts)
    item["options"] = dict(zip(letters, texts))
    item["answer"] = next(l for l, t in item["options"].items() if t == correct_text)
    return item


def _build_prompt(topic: str, n: int, notes: str, avoid: list[str], rules: str) -> str:
    letters = LETTERS[:OPTIONS_PER_QUESTION]
    example_options = ", ".join(f'"{l}": "..."' for l in letters)
    avoid_block = ""
    if avoid:
        avoid_block = "\nDo not repeat these questions:\n" + "\n".join(f"- {s[:120]}" for s in avoid) + "\n"
    rules_block = ""
    if rules:
        rules_block = (
            "\nExcerpts from the official NBME item-writing guide. If they differ from the short rules above, "
            "follow the guide:\n" + rules + "\n"
        )
    return f"""Write {n} single-best-answer multiple-choice questions for UB anatomy students about: {topic}
Use ONLY the course notes below. If the notes do not contain enough information about the topic, return an empty array [].

Item-writing rules (NBME style):
- Each question tests one important idea from the notes and has exactly {OPTIONS_PER_QUESTION} options, with ONE clearly best answer.
- Write the question so it can be answered without seeing the options (a clear lead-in such as "Which nerve ...?").
- Write the lead-in as a positive question. Never use NOT, EXCEPT, LEAST or INCORRECT.
- Never use "all of the above", "none of the above", or options like "A and B".
- Make all options the same type of thing (all nerves, all muscles, and so on), plausible, and similar in length and detail.
- The correct answer must not be the longest option. Do not repeat words from the question inside only the correct option.
- Do not use absolute words such as always or never.
- Do not include any fact that is not in the notes. The correct answer must be stated in the notes.
- Use a short dissection-lab or clinical scenario when the notes support it.
{rules_block}
Return ONLY a JSON array, with no other text. Each item looks like:
{{"stem": "...", "options": {{{example_options}}}, "answer": "C", "explanation": "one or two sentences", "lecture": "exact lecture name from the notes label"}}
{avoid_block}
Course notes:
{notes}
"""


def generate_questions(topic: str, count: int, generate) -> dict:
    """Write, check and shuffle questions. `generate` is the app's LLM function.
    A question is only returned if it passes the NBME-style checks and is grounded in the notes."""
    count = max(1, min(count, MAX_PER_REQUEST))
    found = collection.query(query_texts=[topic], n_results=8)
    docs = found["documents"][0] if found["documents"] else []
    metas = found["metadatas"][0] if found["metadatas"] else []
    if not docs:
        return {"items": [], "rejected": [], "lectures": []}

    notes = "\n\n".join(f"[{m['source']}]\n{d}" for d, m in zip(docs, metas))
    lectures = list(dict.fromkeys(m["source"] for m in metas))
    rules = rule_excerpts()
    print(f"NBME excerpts used: {len(rules)} characters")

    accepted, rejected, seen = [], [], set()
    for _ in range(2):  # a second attempt fills any gaps left by rejected questions
        need = count - len(accepted)
        if need <= 0:
            break
        prompt = _build_prompt(
            topic, min(need + 1, MAX_PER_REQUEST + 2), notes, [q["stem"] for q in accepted], rules
        )
        for raw in _parse_items(generate(prompt)):
            item = _normalize(raw)
            if item is None:
                rejected.append({"stem": str(raw.get("stem", ""))[:200], "problems": ["malformed item"]})
                continue
            problems = validate_item(item)
            if not problems and not is_grounded(item, notes):
                problems.append("the correct answer is not supported by the notes")
            if item["stem"].lower() in seen:
                problems.append("duplicate question")
            if problems:
                rejected.append({"stem": item["stem"][:200], "problems": problems})
                continue
            seen.add(item["stem"].lower())
            if item["lecture"] not in lectures:
                item["lecture"] = ""  # don't claim a source the model made up
            accepted.append(_shuffle_options(item))
            if len(accepted) >= count:
                break
    return {"items": accepted, "rejected": rejected, "lectures": lectures}


def format_markdown(items: list[dict]) -> str:
    if not items:
        return (
            "- I couldn't write reliable practice questions on that from the course notes.\n"
            "- Try naming a specific lecture topic, for example the brachium or the larynx."
        )
    lines = []
    for i, q in enumerate(items, 1):
        lines.append(f"**Question {i}.** {q['stem']}\n")
        for letter in sorted(q["options"]):
            lines.append(f"{letter}. {q['options'][letter]}  ")
        lines.append("")
    lines.append("---\n**Answers**\n")
    for i, q in enumerate(items, 1):
        source = f" *({q['lecture']})*" if q["lecture"] else ""
        lines.append(f"- **{i}. {q['answer']}**: {q['explanation']}{source}")
    return "\n".join(lines)

SHOW_ANSWERS = re.compile(
    r"\b(show|reveal|give|see|tell|what\s+(?:are|is))\b[^.?!]{0,25}\b(?:answers?|answer\s+key|solutions?)\b"
    r"|^\W*(?:the\s+)?(?:answers?|answer\s+key|solutions?)(?:\s+please)?\W*$",
    re.IGNORECASE,
)
PICK = re.compile(r"\b(\d{1,2})\s*[.):=\-]?\s*([A-Ea-e])\b")


def wants_answers(message: str) -> bool:
    return len(message.split()) <= 12 and bool(SHOW_ANSWERS.search(message))


def parse_picks(message: str, n: int) -> dict[int, str] | None:
    """Read a student's answers: '1B 2D 3A', '1. B, 2. D', 'B D A E C' or 'BDAEC'.
    Returns {question number: letter}, or None if the message isn't a list of answers."""
    if len(message.split()) > 40:
        return None
    pairs = PICK.findall(message)
    if pairs:
        leftover = PICK.sub(" ", message)
        if len(re.findall(r"[A-Za-z]{2,}", leftover)) > 4:
            return None
        picks = {int(num): letter.upper() for num, letter in pairs if 1 <= int(num) <= n}
        return picks or None
    letters = re.findall(r"[A-Za-z]+", message)
    if len(letters) == n and all(len(t) == 1 and t.upper() in LETTERS for t in letters):
        return {i: t.upper() for i, t in enumerate(letters, 1)}
    compact = re.fullmatch(r"\W*([A-E]{2,})\W*", message)  # uppercase only, so words like "bad" don't count
    if compact and len(compact.group(1)) == n:
        return {i: c for i, c in enumerate(compact.group(1), 1)}
    return None


def format_quiz(items: list[dict]) -> str:
    """The questions only, with no answers."""
    if not items:
        return format_markdown([])
    lines = []
    for i, q in enumerate(items, 1):
        lines.append(f"**Question {i}.** {q['stem']}\n")
        for letter in sorted(q["options"]):
            lines.append(f"{letter}. {q['options'][letter]}  ")
        lines.append("")
    lines.append('Reply with your answers (for example "1B 2D 3A"), or say "show answers".')
    return "\n".join(lines)


def format_answer_key(items: list[dict]) -> str:
    lines = ["**Answers**\n"]
    for i, q in enumerate(items, 1):
        source = f" *({q['lecture']})*" if q["lecture"] else ""
        lines.append(f"- **{i}. {q['answer']}**: {q['explanation']}{source}")
    return "\n".join(lines)


def grade_answers(items: list[dict], picks: dict[int, str]) -> str:
    right, lines = 0, []
    for i, q in enumerate(items, 1):
        chosen = picks.get(i)
        source = f" *({q['lecture']})*" if q["lecture"] else ""
        if chosen == q["answer"]:
            right += 1
            lines.append(f"- **{i}. Correct** ({q['answer']}).")
        elif chosen:
            lines.append(
                f"- **{i}. Not quite.** You chose {chosen}; the answer is **{q['answer']}**. "
                f"{q['explanation']}{source}"
            )
        else:
            lines.append(f"- **{i}. Not answered.** The answer is **{q['answer']}**. {q['explanation']}{source}")
    return f"**Score: {right} of {len(items)}**\n\n" + "\n".join(lines)

def save_batch(topic: str, items: list[dict]) -> str | None:
    """Save a generated batch to questions/generated/ in the private repo, to build up a question bank."""
    repo = get_github_repo()
    if repo is None:
        print("WARNING: GitHub not configured, question batch was NOT saved")
        return None
    now = datetime.now(timezone.utc)
    path = f"{QUESTIONS_PATH}/{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}.json"
    payload = {"topic": topic, "created_at": now.isoformat(), "items": items}
    try:
        repo.create_file(path, "Add generated question batch", json.dumps(payload, indent=2))
        return path
    except Exception as e:
        print(f"Question batch save failed: {e}")
        return None# ---------- One place that understands every quiz-related message ----------
import difflib

_VOCAB = ["answers", "answer", "questions", "question", "options", "option", "choices",
          "quiz", "quizzes", "solutions", "practice", "another", "reveal", "mcqs"]
_FILLER = set("""quiz quizzes question questions more another few some set sets with without answers answer
options option choices me give make generate create write practice test on about the a an of and please
multiple choice mcq mcqs exam but no new next again show reveal i want need can you could would like to
for from my us one two three four five six seven eight nine ten some key keys""".split())

_NEW = re.compile(
    r"\b(quiz me|test me|practice (questions?|quiz|test)|multiple[- ]choice|mcqs?|exam questions?|"
    r"(write|make|generate|give me|create)\b.{0,25}\b(questions?|quiz)|"
    r"(?:more|another|few|some|\d{1,2})\s+(?:\w+\s+){0,2}(?:questions?|quiz(?:zes)?|sets?)\b.{0,40}"
    r"\b(?:with|without)\s+(?:the\s+)?(?:answers?|options?|choices))\b",
    re.IGNORECASE,
)
_FOLLOW = re.compile(r"\b(more|another|next|again|new|few|set|different)\b", re.IGNORECASE)
_FORMAT_ONLY = re.compile(r"\b(with|without)\s+(the\s+)?(options?|choices|answers?)\b", re.IGNORECASE)
_WITH_ANS = re.compile(
    r"\b(?:with|and|plus|including)\s+(?:the\s+|their\s+|correct\s+)*(?:answers?|answer\s+key|solutions?)\b"
    r"|\b(?:show|give)\s+(?:me\s+)?(?:the\s+)?answers?\b|\banswers?\s+included\b",
    re.IGNORECASE,
)
_NO_ANS = re.compile(r"\b(without|no|hide|hidden)\s+(the\s+)?(answers?|answer\s+key|solutions?)\b", re.IGNORECASE)
_QUIZ_LIKE = re.compile(r"\bQuestion\s*1[.:)]|\bquiz\b|\bpractice (questions?|set)\b", re.IGNORECASE)


def fix_typos(text: str) -> str:
    """'answwers' -> 'answers', 'quesions' -> 'questions'. Only touches near-misses of quiz words."""
    out = []
    for part in re.split(r"(\W+)", text):
        low = part.lower()
        if len(low) >= 5 and low.isalpha() and low not in _VOCAB:
            hit = difflib.get_close_matches(low, _VOCAB, n=1, cutoff=0.84)
            part = hit[0] if hit else part
        out.append(part)
    return "".join(out)


def _topic(message: str, prev_text: str) -> str:
    left = [w for w in re.findall(r"[A-Za-z]+", message) if w.lower() not in _FILLER]
    if sum(len(w) for w in left) >= 4:
        return message  # the message itself names the subject, e.g. "quiz me on the brachium"
    cleaned = re.sub(r"\*+|Question\s*\d+[.:)]|Answers?:", " ", prev_text).strip()
    return cleaned[:500] if cleaned else message  # same lectures as the quiz just shown


def quiz_intent(message: str, prev_assistant: str = "", active: bool = False) -> dict:
    """kind: 'new' (write a quiz), 'answers' (reveal the key), or None (not about a quiz).
    active: the last reply was a saved quiz. prev_assistant: text of the last reply."""
    msg = fix_typos(message or "")
    short = len(msg.split()) <= 12
    prev_quiz_like = active or bool(_QUIZ_LIKE.search(prev_assistant or ""))

    new = bool(_NEW.search(msg))
    if not new and prev_quiz_like and short and (_FOLLOW.search(msg) or _FORMAT_ONLY.search(msg)):
        new = True  # "a few more", "another set", "with options" right after a quiz
    if new and len(msg) <= 300:
        m = COUNT_PATTERN.search(msg)
        count = max(1, min(int(m.group(1)), MAX_PER_REQUEST)) if m else 5
        with_answers = bool(_WITH_ANS.search(msg)) and not _NO_ANS.search(msg)
        return {"kind": "new", "count": count, "with_answers": with_answers,
                "topic": _topic(msg, prev_assistant)}
    if wants_answers(msg):
        return {"kind": "answers", "count": 0, "with_answers": True, "topic": ""}
    return {"kind": None, "count": 0, "with_answers": False, "topic": ""}


def format_quiz_for(items: list[dict], with_answers: bool = False) -> str:
    """Questions with options; the answer block is added only if the student asked for it."""
    if items and with_answers:
        return format_markdown(items)
    return format_quiz(items)