import json
import os
import random
import re
import uuid
from datetime import datetime, timezone

from ingestion import get_github_repo
from retrieval import search_notes
from guides import rule_excerpts

MAX_PER_REQUEST = 10
BATCH_SIZE = 3  # questions per model call; each now carries a reason for every option
NOTES_MENTION = re.compile(r"\b(?:the|these|course|lecture)\s+notes\b|according to the|as stated in|as described in", re.IGNORECASE)
LETTER_MENTION = re.compile(r"\b(?:option|choice|answer)\s+[A-E]\b|\(\s*[A-E]\s*\)", re.IGNORECASE)
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
    if max(lens.values()) > 30:  # the length rules are for phrase options, not short names like "Stapes"
        others = sorted(l for k, l in lens.items() if k != answer)
        median = others[len(others) // 2]
        if lens[answer] == max(lens.values()) and lens[answer] > 1.15 * max(median, 1):
            problems.append("the correct answer is noticeably the longest option")
        if max(lens.values()) > 3 * max(min(lens.values()), 1):
            problems.append("options differ a lot in length")
    if not item.get("explanation"):
        problems.append("missing explanation")
    option_notes = item.get("option_notes") or {}
    if set(option_notes) != set(options) or any(len(str(v).split()) < 4 for v in option_notes.values()):
        problems.append("missing the reason for every option")
    if not item.get("study_tip"):
        problems.append("missing study tip")
    written = " ".join([item.get("explanation", ""), item.get("study_tip", "")] + [str(v) for v in option_notes.values()])
    if LETTER_MENTION.search(written):
        problems.append("the explanations mention option letters, which change when options are shuffled")
    if NOTES_MENTION.search(written):
        problems.append("the explanations refer to 'the notes' (students see the lecture name separately)")
    note_texts = [re.sub(r"\W+", " ", str(v).lower()).strip() for v in option_notes.values()]
    if len(set(note_texts)) < len(note_texts):
        problems.append("two option notes are identical")
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
    notes_raw = raw.get("option_notes") if isinstance(raw.get("option_notes"), dict) else {}
    option_notes = {
        str(k).strip().upper()[:1]: re.sub(r"^[A-E][\.\):]\s*", "", str(v).strip()) for k, v in notes_raw.items()
    }
    return {
        "stem": str(raw.get("stem", "")).strip(),
        "options": clean,
        "answer": str(raw.get("answer", "")).strip().upper()[:1],
        "explanation": str(raw.get("explanation", "")).strip(),
        "option_notes": option_notes,
        "study_tip": str(raw.get("study_tip", "")).strip(),
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
    """Randomize which letter is correct, since models tend to favor certain letters.
    The reason written for each option moves with its option."""
    letters = sorted(item["options"])
    old_notes = item.get("option_notes") or {}
    pairs = [(item["options"][l], old_notes.get(l, "")) for l in letters]
    correct_text = item["options"][item["answer"]]
    random.shuffle(pairs)
    item["options"] = {l: p[0] for l, p in zip(letters, pairs)}
    item["option_notes"] = {l: p[1] for l, p in zip(letters, pairs)}
    item["answer"] = next(l for l, t in item["options"].items() if t == correct_text)
    return item


def _build_prompt(topic: str, n: int, notes: str, avoid: list[str], rules: str) -> str:
    letters = LETTERS[:OPTIONS_PER_QUESTION]
    example_options = ", ".join(f'"{l}": "..."' for l in letters)
    example_notes = ", ".join(f'"{l}": "..."' for l in letters)
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
- The correct answer must not be the longest option. Keep the correct answer short, and write every wrong option with about the same length and level of detail as the correct one. Do not repeat words from the question inside only the correct option.
- Do not use absolute words such as always or never.
- Do not include any fact that is not in the notes. The correct answer must be stated in the notes.
- Use a short dissection-lab or clinical scenario when the notes support it.
- "option_notes" must cover EVERY option, including the correct one, with one or two sentences each. For the correct option, say why it is right. For each wrong option, say why it is wrong AND the mix-up that makes a student pick it (for example, confusing two nerves, two levels or two attachments). Use only facts from the notes.
- "study_tip" is one sentence on how a student can avoid this mistake next time (for example, which two structures to compare, or what pattern to practice), based on the notes.
- Wrong options must be plausible: structures from the same region or group that a student could genuinely confuse with the answer (neighbouring nerves, muscles of the same group, structures at the same level). Never use structures from unrelated regions just to fill the list.
- Each option note must state the specific fact. Never write only "this is the correct answer" or "this is incorrect". Never write "the notes", "according to the notes" or "as stated". Every option note must be different.
- The question must be anatomically accurate and have only one defensible answer. If another option could also be argued as correct from the notes, choose a different question.
- Never mention option letters (A, B, C...) in the explanation, notes or tip. Refer to options by name, because the order is shuffled afterwards.
{rules_block}
Return ONLY a JSON array, with no other text. Each item looks like:
{{"stem": "...", "options": {{{example_options}}}, "answer": "C", "explanation": "one or two sentences on why the correct answer is right", "option_notes": {{{example_notes}}}, "study_tip": "one sentence", "lecture": "exact lecture name from the notes label"}}
{avoid_block}
Course notes:
{notes}
"""


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _quote_in_notes(quote: str, notes: str) -> bool:
    """The judge must copy a sentence from the notes. Checked by code, not trusted."""
    q = _norm(quote)
    if len(q.split()) < 5:
        return False
    n = _norm(notes)
    if q in n:
        return True
    words = q.split()
    return sum(1 for w in words if w in set(n.split())) / len(words) >= 0.9 and q[:25] in n


def _judge(items: list[dict], notes: str, generate) -> list[tuple[list[str], str]]:
    """One extra model call per batch for what the mechanical rules cannot see. For each question
    returns (problems, supporting quote). The judge must copy the sentence from the notes that states
    the answer; the code then checks that the sentence really is in the notes, so a made-up quote fails.
    The same model that wrote the questions is judging them, so this is a screen, not proof.
    If the call or its output fails, the items pass with no quote (the other checks still ran)."""
    if not items:
        return []
    listing = "\n\n".join(
        f"#{i}\nQuestion: {q['stem']}\n"
        + "\n".join(f"{l}. {q['options'][l]}" for l in sorted(q["options"]))
        + f"\nAnswer: {q['answer']}\nExplanation: {q['explanation']}"
        + "".join(f"\nNote on {l}: {t}" for l, t in sorted((q.get('option_notes') or {}).items()))
        for i, q in enumerate(items)
    )
    prompt = f"""You are a strict anatomy examiner checking exam questions against course notes.
For each question decide:
- same_type: true only if every option is the same kind of thing (all nerves, all muscles, and so on)
  and no option is an obviously impossible choice for the question.
- stem_accurate: true only if the question is anatomically correct (for example, it does not give a muscle the wrong action)
  and only ONE option can be defended as correct using the notes.
- answer_quote: copy ONE sentence or phrase, word for word, from the notes that states the correct answer.
  Use an empty string if the notes do not state it. Do not use outside knowledge.
- contradicted: the letters of any note (including the correct option's) that says something the notes contradict
  or do not support. Use [] if every note agrees with the notes.
Return ONLY a JSON array with one object per question, in order:
{{"id": 0, "same_type": true, "stem_accurate": true, "answer_quote": "...", "contradicted": [], "issue": "short reason if anything is false"}}

Course notes:
{notes}

Questions:
{listing}
"""
    try:
        verdicts = _parse_items(generate(prompt))
    except Exception as e:
        print(f"Judge call failed, skipping the extra check: {e}")
        return [([], "") for _ in items]
    by_id = {}
    for v in verdicts:
        try:
            by_id[int(v.get("id"))] = v
        except (TypeError, ValueError):
            continue
    out = []
    for i in range(len(items)):
        v, problems, quote = by_id.get(i), [], ""
        if v:
            if v.get("same_type") is False:
                problems.append("options are not all the same kind of thing")
            if v.get("stem_accurate") is False:
                problems.append("question is inaccurate or has more than one defensible answer")
            quote = str(v.get("answer_quote") or "").strip()
            if not _quote_in_notes(quote, notes):
                problems.append("the notes do not state the answer (no matching supporting sentence)")
                quote = ""
            bad = v.get("contradicted")
            if isinstance(bad, list) and bad:
                problems.append("option notes not supported by the notes: " + ", ".join(str(x) for x in bad))
        out.append((problems, quote))
    return out


def _evidence(item: dict, docs: list[str], metas: list[dict]) -> str:
    """The sentences in the retrieved notes that best match the correct answer, so a reviewer can
    compare the answer with the notes without knowing the subject. Empty if nothing matches."""
    answer_words = set(re.findall(r"[a-z]{4,}", item["options"][item["answer"]].lower()))
    stem_words = set(re.findall(r"[a-z]{5,}", item["stem"].lower()))
    scored = []
    for doc, meta in zip(docs, metas):
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", doc):
            words = set(re.findall(r"[a-z]{4,}", sentence.lower()))
            hits = len(answer_words & words)
            if not hits:
                continue
            scored.append((2 * hits + len(stem_words & words), sentence.strip(), meta["source"]))
    scored.sort(key=lambda t: -t[0])
    picked, seen = [], set()
    for _, sentence, source in scored:
        if sentence and sentence not in seen:
            seen.add(sentence)
            picked.append(f"[{source}] {sentence[:300]}")
        if len(picked) == 2:
            break
    return " | ".join(picked)


def generate_questions(topic: str, count: int, generate, avoid: list[str] | None = None) -> dict:
    """Write, check and shuffle questions. `generate` is the app's LLM function.
    A question is only returned if it passes the NBME-style checks, is grounded in the notes and
    passes the judge check. `avoid` lists question stems that must not be repeated."""
    count = max(1, min(count, MAX_PER_REQUEST))
    avoid = list(avoid or [])
    # Lecture notes by default. Dissection-guide chunks are used only if the topic is about dissecting.
    docs, metas, _ = search_notes(topic, n=8)
    if not docs:
        return {"items": [], "rejected": [], "lectures": []}

    notes = "\n\n".join(f"[{m['source']}]\n{d}" for d, m in zip(docs, metas))
    lectures = list(dict.fromkeys(m["source"] for m in metas))
    rules = rule_excerpts()
    print(f"NBME excerpts used: {len(rules)} characters")

    accepted, rejected = [], []
    seen = {a.lower() for a in avoid}
    for _ in range(max(2, -(-count // BATCH_SIZE) + 1)):  # small batches; extra rounds fill any gaps
        need = count - len(accepted)
        if need <= 0:
            break
        ask = min(need + 1, BATCH_SIZE + 1)
        prompt = _build_prompt(topic, ask, notes, avoid + [q["stem"] for q in accepted], rules)
        candidates = []
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
            candidates.append(item)
            if len(candidates) >= ask:  # one spare, in case the judge rejects one
                break
        for item, (problems, quote) in zip(candidates, _judge(candidates, notes, generate)):
            answer_text = item["options"][item["answer"]].strip().lower()
            if any(a["options"][a["answer"]].strip().lower() == answer_text for a in accepted):
                problems = problems + ["same correct answer as another question in this set"]
            if problems:
                rejected.append({"stem": item["stem"][:200], "problems": problems})
            else:
                src = next((m["source"] for d, m in zip(docs, metas) if quote and _norm(quote)[:40] in _norm(d)), "")
                item["evidence"] = f"[{src}] {quote}" if quote and src else (quote or _evidence(item, docs, metas))
                accepted.append(_shuffle_options(item))
    return {"items": accepted[:count], "rejected": rejected, "lectures": lectures}


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
    lines.append("---\n")
    lines.append(format_answer_key(items))
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


def _rationale(q: dict, chosen: str | None = None) -> str:
    """Paragraphs for one question: why the answer is right, why each other option is wrong and the
    mix-up behind it, and a study tip. Older saved questions without notes fall back to the explanation."""
    options, notes = q.get("options") or {}, q.get("option_notes") or {}
    source = f" *({q['lecture']})*" if q.get("lecture") else ""
    right = options.get(q["answer"])  # a quiz remembered without its options still gets its explanation
    paras = [f"The answer is **{right or q['answer']}**. {q.get('explanation', '')}{source}"]
    if notes:
        if chosen and chosen != q["answer"] and chosen in notes and chosen in options:
            paras.append(f"You chose **{options[chosen]}**. {notes[chosen]}")
        rest = [l for l in sorted(options) if l != q["answer"] and l != chosen and l in notes]
        if rest:
            lead = "The other options: " if chosen and chosen != q["answer"] else "Why the other options are wrong: "
            paras.append(lead + " ".join(f"**{options[l]}**: {notes[l]}" for l in rest))
        if q.get("study_tip"):
            paras.append(f"**Study tip:** {q['study_tip']}")
    return "\n\n".join(paras)


def format_answer_key(items: list[dict]) -> str:
    blocks = ["**Answers**"]
    for i, q in enumerate(items, 1):
        blocks.append(f"**Question {i}: {q['answer']}**\n\n{_rationale(q)}")
    return "\n\n".join(blocks)


def grade_answers(items: list[dict], picks: dict[int, str]) -> str:
    right, blocks = 0, []
    for i, q in enumerate(items, 1):
        chosen = picks.get(i)
        if chosen == q["answer"]:
            right += 1
            status = f"Question {i}: Correct ({q['answer']})"
        elif chosen:
            status = f"Question {i}: Not quite. You chose {chosen}; the answer is {q['answer']}"
        else:
            status = f"Question {i}: Not answered. The answer is {q['answer']}"
        blocks.append(f"**{status}**\n\n{_rationale(q, chosen)}")
    return f"**Score: {right} of {len(items)}**\n\n" + "\n\n".join(blocks)


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