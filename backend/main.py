from dotenv import load_dotenv
load_dotenv()
import os
import re
import time
import threading
import secrets
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from google import genai
from google.genai import types
from openai import OpenAI

from ingestion import (
    ingest_text,
    collection,
    rebuild_from_backups,
    read_note_from_github,
    remove_existing_chunks,
    delete_from_github,
)
from feedback import (
    save_feedback,
    list_feedback,
    resolve_feedback,
    build_queue,
    resolve_group,
    resolve_lecture,
    DECISIONS,
)
from images import (
    rebuild_images,
    add_images,
    remove_image,
    search_images,
    set_approval,
    list_images,
    read_image,
)
from review import (
    looks_like_dispute,
    last_exchange,
    dispute_record,
    remember_answer,
    recall_lectures,
    remember_quiz,
    find_quiz,
    DISPUTE_REPLY,
)
from guides import rebuild_guides
from questions import (
    generate_questions,
    format_quiz_for,
    quiz_intent,
    format_answer_key,
    grade_answers,
    parse_picks,
    save_batch,
)
from retrieval import search_notes
from protect import allowed_origins, rate_limit, daily_cap
from cache import cache_get, cache_put, cache_clear
from typos import build_vocabulary, correct_query
import chatlog
from quizgate import quiz_gate, BUSY_REPLY
from insights import (
    load_insights,
    mark_reviewed,
    reviewed_lectures,
    record_answer,
    record_quiz_grade,
    flush_usage,
    build_report,
)

app = FastAPI(title="AskInglis Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],  # lets browser code read how long to wait
)

# ---------- LLM setup ----------

LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower()  # gemini | bullsai | custom
LLM_FALLBACK = os.environ.get("LLM_FALLBACK", "true").lower() == "true"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.5-flash")
# If the main model is overloaded, these are tried in order
GEMINI_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()
]
# Optional: a stronger model used only for writing practice questions (chat keeps the fast main model)
GEMINI_QUIZ_MODEL = os.environ.get("GEMINI_QUIZ_MODEL", "").strip()
GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "45")) * 1000

BULLSAI_API_KEY = os.environ.get("BULLSAI_API_KEY")
BULLSAI_BASE_URL = os.environ.get("BULLSAI_BASE_URL", "https://gateway.bullsai.buffalo.edu/v1")
BULLSAI_MODEL_NAME = os.environ.get("BULLSAI_MODEL_NAME", "openai/gpt-oss-20b")
BULLSAI_TIMEOUT = float(os.environ.get("BULLSAI_TIMEOUT", "60"))
# A practice-question batch is long (every option has a reason), so the cap must be generous. It is only a ceiling.
BULLSAI_MAX_TOKENS = int(os.environ.get("BULLSAI_MAX_TOKENS", "8000"))

CUSTOM_LLM_API_KEY = os.environ.get("CUSTOM_LLM_API_KEY")
CUSTOM_LLM_BASE_URL = os.environ.get("CUSTOM_LLM_BASE_URL")
CUSTOM_LLM_MODEL_NAME = os.environ.get("CUSTOM_LLM_MODEL_NAME")
CUSTOM_LLM_TIMEOUT = float(os.environ.get("CUSTOM_LLM_TIMEOUT", "60"))
CUSTOM_LLM_MAX_TOKENS = int(os.environ.get("CUSTOM_LLM_MAX_TOKENS", "8000"))

# Images, disputes and practice questions
MAX_IMAGE_DISTANCE = float(os.environ.get("MAX_IMAGE_DISTANCE", "1.0"))
IMAGES_IN_REPLY = os.environ.get("IMAGES_IN_REPLY", "true").lower() == "true"
DISPUTE_IN_CHAT = os.environ.get("DISPUTE_IN_CHAT", "true").lower() == "true"
QUIZ_IN_CHAT = os.environ.get("QUIZ_IN_CHAT", "false").lower() == "true"

# Only rewrite follow-ups shorter than this many words; longer ones usually stand alone
REWRITE_BELOW_WORDS = 12

# Keep a retrieved chunk only if it is within this multiple of the best match's distance.
# 1.3 is an untuned starting value: check the "Retrieval distances" log line and adjust.
RETRIEVAL_DISTANCE_RATIO = float(os.environ.get("RETRIEVAL_DISTANCE_RATIO", "1.3"))

# Words that mean the student is talking about a specific body type or specimen
BODY_TYPE_PATTERN = re.compile(
    r"\b(obese|obesity|overweight|slender|muscular|elderly|cachectic|pediatric|infant|child|children|"
    r"body type|body habitus|different bod(?:y|ies)|"
    r"(?:thin|small|large|tall|short)\s+(?:specimen|frame|build|body|person|donor|cadaver))\b",
    re.IGNORECASE,
)

# Limits for public use (0 turns a limit off)
limit_chat = rate_limit("chat", int(os.environ.get("CHAT_PER_MIN", "40")), 60)
limit_feedback = rate_limit("feedback", int(os.environ.get("FEEDBACK_PER_MIN", "40")), 60)
limit_images = rate_limit("images", int(os.environ.get("IMAGES_PER_MIN", "80")), 60)
limit_title = rate_limit("title", int(os.environ.get("TITLE_PER_MIN", "30")), 60)
limit_translate = rate_limit("translate", int(os.environ.get("TRANSLATE_PER_MIN", "20")), 60)
limit_gloss = rate_limit("gloss", int(os.environ.get("GLOSS_PER_MIN", "40")), 60)
daily_chat_cap = daily_cap(int(os.environ.get("DAILY_CHAT_LIMIT", "0")))


def make_openai_client(api_key, base_url, timeout):
    if not api_key or not base_url:
        return None
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)


gemini_client = (
    genai.Client(api_key=GEMINI_API_KEY, http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    if GEMINI_API_KEY
    else None
)
bullsai_client = make_openai_client(BULLSAI_API_KEY, BULLSAI_BASE_URL, BULLSAI_TIMEOUT)
custom_client = make_openai_client(CUSTOM_LLM_API_KEY, CUSTOM_LLM_BASE_URL, CUSTOM_LLM_TIMEOUT)

PROVIDER_READY = {
    "gemini": gemini_client is not None,
    "bullsai": bullsai_client is not None,
    "custom": custom_client is not None and bool(CUSTOM_LLM_MODEL_NAME),
}
if LLM_PROVIDER not in PROVIDER_READY:
    raise RuntimeError(f"Unknown LLM_PROVIDER '{LLM_PROVIDER}' (use gemini, bullsai or custom)")
if not PROVIDER_READY[LLM_PROVIDER]:
    raise RuntimeError(f"LLM_PROVIDER is '{LLM_PROVIDER}' but its settings are missing in .env")

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


def call_openai_style(client, model_name, prompt, max_tokens, label, extra=None):
    start = time.time()
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
        **({"extra_body": extra} if extra else {}),
    )
    print(f"{label} {model_name}: {time.time() - start:.1f}s, usage={getattr(response, 'usage', None)}")
    return (response.choices[0].message.content or "").strip()


def call_gemini(prompt: str, first_model: str = "") -> str:
    """Try the main model, then each fallback model, so one overloaded model doesn't stop the chat.
    first_model: a model to try before the usual list (used for practice questions)."""
    last_error = None
    order = list(dict.fromkeys(([first_model] if first_model else []) + [GEMINI_MODEL_NAME] + GEMINI_FALLBACK_MODELS))
    for model_name in order:
        start = time.time()
        try:
            response = gemini_client.models.generate_content(model=model_name, contents=prompt)
            print(f"Gemini {model_name}: {time.time() - start:.1f}s")
            return (response.text or "").strip()
        except Exception as e:
            print(f"Gemini {model_name} failed after {time.time() - start:.1f}s: {repr(e)[:120]}")
            last_error = e
    raise last_error


def call_bullsai(prompt: str) -> str:
    return call_openai_style(bullsai_client, BULLSAI_MODEL_NAME, prompt, BULLSAI_MAX_TOKENS, "BullsAI")


def call_custom(prompt: str) -> str:
    return call_openai_style(custom_client, CUSTOM_LLM_MODEL_NAME, prompt, CUSTOM_LLM_MAX_TOKENS, "Custom LLM")


PROVIDERS = {"gemini": call_gemini, "bullsai": call_bullsai, "custom": call_custom}

print(
    f"LLM provider: {LLM_PROVIDER}, main model: "
    f"{GEMINI_MODEL_NAME if LLM_PROVIDER == 'gemini' else LLM_PROVIDER}, "
    f"Gemini fallback models: {GEMINI_FALLBACK_MODELS or 'none'}"
)


def generate(prompt: str) -> str:
    """The only place the app calls an LLM. Which one is set by LLM_PROVIDER."""
    try:
        return PROVIDERS[LLM_PROVIDER](prompt)
    except Exception as e:
        print(f"{LLM_PROVIDER} call failed: {e}")
        if LLM_FALLBACK and LLM_PROVIDER != "gemini" and gemini_client is not None:
            print("Falling back to Gemini")
            return call_gemini(prompt)
        raise


QUIZ_REASONING_EFFORT = os.environ.get("QUIZ_REASONING_EFFORT", "").strip().lower()  # low, medium or high; blank = model default


def generate_quiz(prompt: str) -> str:
    """Same as generate(), but uses GEMINI_QUIZ_MODEL first when Gemini is the provider and one is set.
    With BullsAI, QUIZ_REASONING_EFFORT=low asks the model to think less, which makes quiz writing much faster."""
    if LLM_PROVIDER == "bullsai" and QUIZ_REASONING_EFFORT and bullsai_client is not None:
        try:
            return call_openai_style(bullsai_client, BULLSAI_MODEL_NAME, prompt, BULLSAI_MAX_TOKENS,
                                     f"BullsAI quiz ({QUIZ_REASONING_EFFORT})", {"reasoning_effort": QUIZ_REASONING_EFFORT})
        except Exception as e:
            print(f"Quiz call with reasoning_effort failed, using the normal call: {e}")
    if LLM_PROVIDER == "gemini" and GEMINI_QUIZ_MODEL and gemini_client is not None:
        return call_gemini(prompt, first_model=GEMINI_QUIZ_MODEL)
    return generate(prompt)


def require_admin(x_admin_key: str = Header(default="")):
    """Blocks /admin routes unless the caller sends the right X-Admin-Key header."""
    if not ADMIN_API_KEY or not secrets.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- Startup state ----------

state = {"ready": False}


def _warm_up():
    """Rebuild the search indexes in the background so the server can answer right away."""
    start = time.time()
    try:
        print(f"Startup rebuild: {rebuild_from_backups()}")
        print(f"Image catalog: {rebuild_images()} images")
        print(f"NBME guide: {rebuild_guides()} chunks")
        print(f"Insights: {load_insights()}")
        print(f"Spelling vocabulary: {rebuild_vocabulary()} words")
    except Exception as e:
        print(f"Warm-up failed: {e}")
    finally:
        state["ready"] = True
        print(f"Warm-up finished in {time.time() - start:.0f}s")


# "Compare the brachialis and the biceps", "difference between X and Y", "X vs Y"
COMPARE_PATTERN = re.compile(r"\b(compare|comparison|differences?\s+(?:between|of)|versus|vs\.?)\b", re.I)


def rebuild_vocabulary() -> int:
    """Words from the notes, used to catch typos in a student's question."""
    try:
        return build_vocabulary(collection.get(include=["documents"])["documents"])
    except Exception as e:
        print(f"Spelling vocabulary failed: {e}")
        return 0


def require_ready():
    if not state["ready"]:
        raise HTTPException(
            status_code=503,
            detail="The study assistant is starting up. Please try again in a minute.",
            headers={"Retry-After": "30"},
        )


# ---------- Models ----------

class Turn(BaseModel):
    role: str  # "user" or "assistant"
    content: str = Field(max_length=10000)


class ChatRequest(BaseModel):
    message: str = Field(max_length=2000)
    history: list[Turn] = Field(default_factory=list, max_length=12)
    mode: str = Field(default="", max_length=16)  # "dissect" = layered guide, "locate" = Where am I?
    session: str = Field(default="", max_length=40)  # random conversation id made by the browser, for grouping only
    seen: list[str] = Field(default_factory=list, max_length=80)  # stems of quiz questions this browser already showed the student
    shown: str = Field(default="", max_length=600)   # the short question the student actually saw, if the page sent a longer prompt


class TitleRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)  # the student's first question
    reply: str = Field(default="", max_length=4000)      # optional: the first answer, for context


class TitleResponse(BaseModel):
    title: str


# Languages the frontend offers for translating an answer (display name sent by the page)
TRANSLATE_LANGUAGES = {
    "Korean", "Spanish", "Simplified Chinese", "Japanese", "French",
    "German", "Hindi", "Arabic", "Portuguese", "Vietnamese",
}


class TranslateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)  # an answer already shown to the student
    target_language: str = Field(min_length=2, max_length=40)


class TranslateResponse(BaseModel):
    translated_text: str


class GlossRequest(BaseModel):
    term: str = Field(min_length=2, max_length=80)  # a short bold term the student tapped
    target_language: str = Field(min_length=2, max_length=40)


class GlossResponse(BaseModel):
    translation: str
    meaning: str


class ChatResponse(BaseModel):
    reply: str
    sources: list[str]
    lectures: list[str] = []
    images: list[dict] = []
    degraded: bool = False  # true when the AI was unavailable and note excerpts were returned
    understood_as: str | None = None  # the question after obvious spelling fixes, when anything was fixed


class FeedbackRequest(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=12000)
    rating: str  # "up" or "down"
    comment: str = Field(default="", max_length=2000)
    lectures: list[str] = Field(default_factory=list, max_length=10)


class DisputeRequest(BaseModel):
    question: str = Field(max_length=4000)
    answer: str = Field(max_length=12000)
    claim: str = Field(default="", max_length=2000)


class ResolveRequest(BaseModel):
    decision: str  # note_fixed | answer_was_correct | dismissed
    comment: str = Field(default="", max_length=1000)


class ResolveGroupRequest(BaseModel):
    fingerprint: str = Field(max_length=40)
    decision: str
    comment: str = Field(default="", max_length=1000)


class ResolveLectureRequest(BaseModel):
    lecture: str = Field(max_length=300)
    decision: str
    comment: str = Field(default="", max_length=1000)


class QuestionRequest(BaseModel):
    topic: str = Field(max_length=500)
    count: int = Field(default=5, ge=1, le=10)
    save: bool = True


class IngestNoteRequest(BaseModel):
    filename: str
    text: str


class IngestNoteResponse(BaseModel):
    status: str
    filename: str
    chunks: int | None = None
    reason: str | None = None


class ImageEntry(BaseModel):
    id: str
    caption: str
    structures: list[str] = []
    url: str
    page_url: str = ""
    source: str
    license: str
    attribution: str = ""


class ApproveRequest(BaseModel):
    caption: str | None = Field(default=None, max_length=300)
    structures: list[str] | None = None


def check_decision(decision: str):
    if decision not in DECISIONS:
        raise HTTPException(status_code=400, detail=f"decision must be one of {DECISIONS}")


# ---------- Startup and health ----------

@app.on_event("startup")
def startup_event():
    threading.Thread(target=_warm_up, daemon=True).start()


@app.get("/")
def health_check():
    return {"status": "ok", "ready": state["ready"], "message": "AskInglis backend running"}


# ---------- Chat ----------

def format_history(history: list[Turn]) -> str:
    return "\n".join(f"{t.role}: {t.content[:1000]}" for t in history[-6:])


def standalone_question(message: str, history: list[Turn]) -> str:
    """Turn a follow-up like 'how do I do that?' into a full question,
    so the note search has something to match. Skipped for long messages
    (they usually stand alone) to save a model call. Falls back to the original."""
    if not history or len(message.split()) >= REWRITE_BELOW_WORDS:
        return message

    prompt = f"""Rewrite the student's follow-up as a standalone anatomy question,
using the conversation for context. Keep any body type or specimen details the student
mentioned (for example obese, thin, muscular, elderly, child, small or large frame, a variant).
If it is already a standalone question, return it unchanged.
Return only the question.

Conversation:
{format_history(history)}

Follow-up: {message}

Standalone question:"""
    try:
        return generate(prompt) or message
    except Exception as e:
        print(f"Follow-up rewrite failed: {e}")
        return message


def _chat_impl(request: ChatRequest, background: BackgroundTasks):
    t0 = time.time()

    # "That's wrong": no self-correction. Queue it for a human and tell the student.
    if DISPUTE_IN_CHAT and looks_like_dispute(request.message):
        exchange = last_exchange(request.history)
        if exchange:
            question, answer = exchange
            background.add_task(
                save_feedback,
                dispute_record(question, answer, request.message, recall_lectures(answer)),
            )
            return ChatResponse(reply=DISPUTE_REPLY, sources=[], lectures=[])
    
    # ---- Practice quizzes (Phase 2): every quiz-related message is handled here ----
    if QUIZ_IN_CHAT:
        prev = [t for t in request.history if t.role == "assistant"][-1:]  # only the last reply counts
        items, seen_quiz = find_quiz(prev) if prev else ([], False)
        intent = quiz_intent(request.message, prev[0].content if prev else "", bool(items) or seen_quiz)

        if intent["kind"] == "new":
            waiting_now = quiz_gate.waiting
            if not quiz_gate.enter():
                print(f"Quiz busy: {waiting_now} waiting, asked to try again")
                return ChatResponse(reply=BUSY_REPLY, sources=[], lectures=[], degraded=True)
            try:
                batch = generate_questions(
                    intent["topic"], intent["count"], generate_quiz,
                    # don't repeat the last quiz, or any question this browser has already shown the student
                    avoid=list(dict.fromkeys([i["stem"] for i in items] + [str(x)[:300] for x in request.seen])) or None,
                )
            except Exception as e:
                print(f"Quiz generation failed: {e}")
                raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
            finally:
                quiz_gate.leave()
            print(
                f"Quiz: {len(batch['items'])} accepted, {len(batch['rejected'])} rejected "
                f"in {time.time() - t0:.1f}s"
            )
            new_items = batch["items"]
            # list only the lectures the questions came from, not every lecture the search touched
            used = list(dict.fromkeys(i["lecture"] for i in new_items if i["lecture"])) or batch["lectures"]
            quiz_reply = format_quiz_for(new_items, intent["with_answers"])
            if new_items:
                record_answer(used, "quiz")
            if new_items:
                remember_quiz(quiz_reply, new_items)
                remember_answer(quiz_reply, used)
            return ChatResponse(reply=quiz_reply, sources=[], lectures=used)

        if items:
            reply = None
            if intent["kind"] == "answers":
                reply = format_answer_key(items)
            else:
                picks = parse_picks(request.message, len(items))
                if picks:
                    reply = grade_answers(items, picks)
                    record_quiz_grade(items, picks)
            if reply:
                quiz_lectures = list(dict.fromkeys(i["lecture"] for i in items if i["lecture"]))
                remember_answer(reply, quiz_lectures)
                # Note passages are shown only now, after grading or "show answers": next to an unanswered question they could give it away.
                passages = [i["evidence"][:700] for i in items if i.get("evidence")]
                return ChatResponse(reply=reply, sources=passages, lectures=quiz_lectures)
        elif seen_quiz and (intent["kind"] == "answers" or parse_picks(request.message, 10)):
            return ChatResponse(
                reply=(
                    "- That quiz has expired, because the server restarted.\n"
                    '- Ask for a new one, for example "quiz me on the brachium".'
                ),
                sources=[],
                lectures=[],
            )

    elif quiz_intent(request.message)["kind"] == "new":
        # Practice questions are not public yet: don't answer a quiz request with unchecked free text
        return ChatResponse(
            reply=(
                "- Practice questions aren't available yet.\n"
                '- Ask me about a lecture topic instead, for example "What does the biceps brachii do?"'
            ),
            sources=[],
            lectures=[],
        )

    # First questions (no history) can be answered from the cache
    # Same topic, same lab guide: dissection guides are cached by topic so every student (and every retry) sees
    # the same steps. "Where am I?" answers depend on the student's own words, so they are never cached.
    cacheable = not request.history and request.mode != "locate"
    cache_key = f"[{request.mode}] {request.message}" if request.mode else request.message
    if cacheable:
        hit = cache_get(cache_key)
        if hit:
            print(f"Cache hit after {time.time() - t0:.2f}s")
            record_answer(hit.get("lectures", []), "chat")
            return ChatResponse(**hit)

    typed, fixes = correct_query(request.message)  # "brachilais" -> "brachialis", only when clearly a typo
    if fixes:
        print(f"Spelling fixed: {fixes}")
    query = standalone_question(typed, request.history)

    docs, metas, dists = search_notes(query, n=4, ratio=RETRIEVAL_DISTANCE_RATIO)
    if dists:
        print(f"Retrieval distances: {[round(d, 2) for d in dists]}, kinds: {[m.get('kind') for m in metas]}")
    print(f"Retrieval done {time.time() - t0:.1f}s after request start")

    sources = [f"[{m['source']}] {d}" for d, m in zip(docs, metas)]
    lectures = list(dict.fromkeys(m["source"] for m in metas))
    context = "\n\n".join(sources)

    images = search_images(query, max_distance=MAX_IMAGE_DISTANCE)

    history_block = format_history(request.history)
    if history_block:
        history_block = f"\nConversation so far:\n{history_block}\n"

    # Only the student's own words decide whether a body type was mentioned
    student_text = " ".join([t.content for t in request.history if t.role == "user"] + [request.message])
    body_words = list(dict.fromkeys(m.group(1).lower() for m in BODY_TYPE_PATTERN.finditer(student_text)))
    if body_words:
        named = ", ".join(body_words)
        body_rule = (
            f'The student named this body type or specimen: {named}. You MUST end your answer with a section titled '
            '"**Body type note**" containing 2 to 3 bullets on how the approach, landmarks, depth, or the '
            f"structure's size and position change for {named} specimens, for the structures in this answer. "
            "Write only about the body type the student named, and do not discuss any other body type. "
            'Never write that no body type was given, because it was. Do not hedge about the body type itself; if one '
            'detail is uncertain, start that bullet with "General knowledge:".'
        )
    else:
        body_rule = (
            'Unless the question only asks for a name or a definition, or is a general overview of a whole system '
            "or topic (for example 'what does the digestive system do'), end your answer with a section titled exactly "
            '"**Body type note**" with 1 to 2 bullets. Say how the approach, landmarks, depth, or the structure\'s '
            "size and position typically vary between bodies (for example slender, obese, elderly, small or large "
            "frame, or common anatomical variants) and what that changes at the dissection table. "
            "Only add the note when it is about a specific structure or region the question names, and leave it out "
            "rather than write something generic. "
            'If the notes do not cover it, start that bullet with "General knowledge:". '
            "If a difference could mean an abnormality, say so and tell the student to check with their instructor."
        )

    body_rule += (
        " Each body type bullet must name one concrete change at the table (for example a landmark that is harder to "
        "palpate, a structure that lies deeper, or a thinner tendon), never vague wording. "
        'Use "General knowledge:" only for facts that are not in the notes.'
    )

    if request.mode == "locate":
        # "Where am I?": the student describes what they see, in words. Never a photo.
        format_rules = (
            "- Start with one bold line: Most likely: <structure or region>, then (confident), (possible) or (not sure).\n"
            "- **Confirm it:** 2 to 3 bullets, each a landmark or feature to check that tells it apart from look-alikes.\n"
            "- **Look next:** 1 to 2 bullets on what lies next to it or deeper, and what to expose next.\n"
            "- **Careful:** 1 bullet on what not to cut or damage here.\n"
            "- **If it does not match:** 1 bullet naming what else it could be.\n"
            "- If the description is too vague to place, do not guess. Say so and ask for 2 specific details "
            "(for example direction of the fibres, nearby bone, or depth).\n"
            "- A text description is never proof. End with exactly: Confirm with your instructor or atlas before cutting.\n"
            "- Put structure names in **bold**. Start anything that is not in the notes with \"General knowledge:\". "
            "Do not add a Body type note."
        )
    elif request.mode == "dissect":
        # Layered dissection guide: every step says what to expect, what to protect, and what to do if lost
        format_rules = (
            "- Start with one bold line naming the region and what the student will expose.\n"
            "- Then 4 to 7 numbered steps (1. 2. 3.). Each step is one action at the table, under 25 words.\n"
            "- Under EVERY step add exactly three indented lines, each starting with a dash and one of these labels:\n"
            "   - You should see: what is visible once the step is done, under 20 words.\n"
            "   - Do not cut: the structure to protect at this step, or write: Nothing critical at this step.\n"
            "   - Can't find it? one practical tip, under 20 words.\n"
            "- Put structure names in **bold**. Use the dissection guides and lecture notes. Start anything that is "
            "not in them with \"General knowledge:\".\n"
            f"- After the steps: {body_rule}"
        )
    elif COMPARE_PATTERN.search(typed):
        # A side-by-side table: students compare structures all the time (origin, nerve, action...)
        format_rules = (
            "- Start with one bold line that states the main difference.\n"
            "- Then ONE markdown table. The first column is called Feature, then one column per structure (at most 3).\n"
            "- Use 4 to 6 rows chosen from: Location, Origin, Insertion, Action, Nerve supply, Blood supply, "
            "What to look for at the table. Use only rows the notes support.\n"
            '- Keep every cell under 12 words. If the notes do not cover a cell, write "Not in the notes", or start '
            'it with "General knowledge:" for a fact that is not in the notes.\n'
            "- Put structure names in **bold**. After the table, add one bullet on how students mix them up.\n"
            "- Do not add a Body type note unless the student asked about a body type."
        )
        if BODY_TYPE_PATTERN.search(student_text):
            format_rules += f"\n- {body_rule}"
    else:
        format_rules = (
            "- Start with one bold line that answers the question directly.\n"
            '- Then give 3 to 6 short bullet points, each under 20 words. Use "-" for bullets.\n'
            "- Put structure names in **bold**.\n"
            "- Never write a paragraph longer than two sentences. Keep the main answer under 150 words unless the "
            "student asks for more detail; the Body type note is extra.\n"
            f"- {body_rule}"
        )

    prompt = f"""You are a study assistant for UB Human Anatomy Laboratory students who are skimming at the dissection table.
Use the notes below as your primary source. Each note excerpt starts with the lecture it came from, in [brackets].
Don't contradict the notes. If the notes don't cover something, add a bullet that starts with "General knowledge:" and say so.
Excerpts from files named "Class NN" are dissection guides. Use them for what to do at the table, and the lecture notes for the anatomy itself.

FORMAT (very important):
{format_rules}
- If you are not confident about a specific difference, say so. If a difference could mean an abnormality, say that and tell the
  student to check with their instructor.

Notes:
{context}
{history_block}
Question: {typed}

Answer:"""

    try:
        reply = generate(prompt)
    except Exception as e:
        print(f"LLM call failed: {e}")
        if not docs:
            raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
        # AI unavailable or out of quota: give the best note passages instead of an error
        passages = "\n\n".join(f"**{m['source']}**\n{d}" for d, m in zip(docs[:2], metas[:2]))
        degraded_reply = (
            "The AI helper is busy right now. Here are the most relevant passages from the course notes:\n\n"
            + passages
        )
        remember_answer(degraded_reply, lectures)  # so a thumbs-down on this reply still names its lectures
        return ChatResponse(
            reply=degraded_reply,
            sources=sources,
            lectures=lectures,
            images=[],
            degraded=True,
        )

    print(f"Chat total: {time.time() - t0:.1f}s")
    answered = bool(reply)
    if not answered:
        reply = "- I couldn't generate an answer for that.\n- Please try rephrasing your question."
    else:
        remember_answer(reply, lectures)  # so a later "that's wrong" can name these lectures
        record_answer(lectures, "dissection" if metas and metas[0].get("kind") == "guide" else "chat")

    if answered and images and IMAGES_IN_REPLY:
        top = images[0]
        alt = top["caption"].replace("[", "(").replace("]", ")")
        credit = top["attribution"] or top["source"]
        reply += f"\n\n![{alt}]({top['url']})\n\n*{credit} ({top['license']})*"

    data = {
        "reply": reply, "sources": sources, "lectures": lectures, "images": images,
        "understood_as": typed if fixes else None,
    }
    if cacheable and answered:
        cache_put(cache_key, data)
    return ChatResponse(**data)


# ---------- Images ----------

@app.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_ready), Depends(limit_chat), Depends(daily_chat_cap)],
)
def chat(request: ChatRequest, background: BackgroundTasks):
    t0 = time.time()
    response = _chat_impl(request, background)
    background.add_task(
        chatlog.log_exchange,
        request.session, request.mode, request.shown or request.message,
        response.reply, response.lectures, int((time.time() - t0) * 1000),
    )
    return response


def _clean_title(text: str, fallback: str) -> str:
    title = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    title = re.sub(r"^\W*title\s*:\s*", "", title.replace("*", ""), flags=re.IGNORECASE)
    title = title.strip().strip("\"'`#- ").rstrip(".!?:")
    words = title.split()
    if not words:
        words = fallback.split()
    title = " ".join(words[:7])
    return title[:60] or "New conversation"


@app.post("/title", response_model=TitleResponse, dependencies=[Depends(limit_title)])
def conversation_title(req: TitleRequest):
    """Short title for a conversation, from the first question. Never fails: if the model is
    unavailable, the title is the first few words of the question."""
    fallback = req.message.strip()
    prompt = f"""Write a short title (2 to 5 words) naming the specific topic of this student's first message.
Use the actual structure, region or task (for example "Biceps Brachii Function", "Brachium Quiz", "Anterior Neck Triangle").
Never use generic titles such as "Human Anatomy Study", "Anatomy Chat" or "Study Session".
Use title case. No quotes, no ending punctuation, no emoji. Return only the title.

Student's first question: {req.message[:500]}
{('Answer given: ' + req.reply[:500]) if req.reply else ''}

Title:"""
    try:
        return TitleResponse(title=_clean_title(generate(prompt), fallback))
    except Exception as e:
        print(f"Title generation failed: {e}")
        return TitleResponse(title=_clean_title("", fallback))


@app.post("/translate", response_model=TranslateResponse, dependencies=[Depends(limit_translate)])
def translate_text(req: TranslateRequest):
    """Translate an answer the student is already looking at. The English answer stays on screen as the
    reference, so this is display-only. Anatomical terms stay in English so they match the exam."""
    language = req.target_language.strip()
    if language not in TRANSLATE_LANGUAGES:
        raise HTTPException(status_code=422, detail="Unsupported language.")
    cache_key = f"[translate:{language}] {req.text}"
    cached = cache_get(cache_key)
    if cached and cached.get("translated_text"):
        return TranslateResponse(translated_text=cached["translated_text"])
    prompt = f"""Translate the anatomy study answer below into {language}.

Rules:
- Keep the exact structure: headings, bullet points, numbered lists and bold text stay as they are.
- Keep every anatomical term in English (or its Latin name), for example "biceps brachii" or "musculocutaneous nerve",
  because the student's exams are in English. Right after a term you may add its {language} name in brackets,
  the first time it appears only.
- Do not translate lecture or file names, numbers, or the markers "General knowledge:" and "Body type note"
  (translate the words after them).
- Do not add, remove, correct or explain anything. If a sentence looks wrong, translate it as it is.
- Return only the translation, with no introduction and no notes.

Answer to translate:
{req.text}"""
    try:
        translated = (generate(prompt) or "").strip()
    except Exception as e:
        print(f"Translation failed: {e}")
        raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
    if not translated:
        raise HTTPException(status_code=502, detail="The AI service returned an empty translation.")
    cache_put(cache_key, {"translated_text": translated})
    return TranslateResponse(translated_text=translated)


@app.post("/gloss", response_model=GlossResponse, dependencies=[Depends(limit_gloss)])
def gloss_term(req: GlossRequest):
    """A small glossary entry for one term: its name in the student's language plus a plain-English meaning.
    The lab steps and quizzes stay in English; this only helps a learner understand the word."""
    language = req.target_language.strip()
    term = " ".join(req.term.split())
    if language not in TRANSLATE_LANGUAGES:
        raise HTTPException(status_code=422, detail="Unsupported language.")
    cache_key = f"[gloss:{language}] {term.lower()}"
    cached = cache_get(cache_key)
    if cached and cached.get("translation"):
        return GlossResponse(translation=cached["translation"], meaning=cached.get("meaning", ""))
    prompt = f"""A first-year anatomy student is learning the English term "{term}".

Reply in exactly two lines and nothing else:
TRANSLATION: the term in {language} (the usual word used for it in {language}; if there is no common word, give a short description in {language})
MEANING: one short sentence in simple English saying what it is or does (maximum 20 words)

If "{term}" is not an anatomy or dissection term, reply with only: NOT A TERM"""
    try:
        raw = (generate(prompt) or "").strip()
    except Exception as e:
        print(f"Gloss failed: {e}")
        raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
    translation = meaning = ""
    for line in raw.splitlines():
        head, _, rest = line.partition(":")
        key = head.strip().strip("*").upper()
        if key == "TRANSLATION":
            translation = rest.strip().strip("*").strip()
        elif key == "MEANING":
            meaning = rest.strip().strip("*").strip()
    if not translation:
        raise HTTPException(status_code=404, detail="No glossary entry for that term.")
    cache_put(cache_key, {"translation": translation, "meaning": meaning})
    return GlossResponse(translation=translation, meaning=meaning)


@app.get("/images", dependencies=[Depends(limit_images)])
def images_for(query: str, limit: int = 3):
    """Quick lookup the frontend can call in parallel with /chat. Approved images only."""
    return search_images(query[:500], limit=min(limit, 5), max_distance=MAX_IMAGE_DISTANCE)


@app.get("/guide-image/{image_id}", dependencies=[Depends(limit_images)])
def guide_image(image_id: str):
    """Serves an approved picture from the dissection guide, stored in the private repo."""
    found = read_image(os.path.basename(image_id), require_approved=True)
    if found is None:
        raise HTTPException(status_code=404, detail="Image not found")
    data, mime = found
    return Response(content=data, media_type=mime, headers={"Cache-Control": "public, max-age=86400"})


@app.get("/admin/guide-image/{image_id}", dependencies=[Depends(require_admin)])
def admin_guide_image(image_id: str):
    """Lets a reviewer look at any image, approved or not."""
    found = read_image(os.path.basename(image_id), require_approved=False)
    if found is None:
        raise HTTPException(status_code=404, detail="Image not found")
    data, mime = found
    return Response(content=data, media_type=mime)


@app.get("/admin/images", dependencies=[Depends(require_admin)])
def admin_list_images(approved: bool | None = None):
    return list_images(approved)


@app.post("/admin/images", dependencies=[Depends(require_admin)])
def upsert_images(entries: list[ImageEntry]):
    try:
        total = add_images([e.model_dump() for e in entries])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    cache_clear()
    return {"status": "saved", "total_images": total}


@app.post("/admin/images/{image_id}/approve", dependencies=[Depends(require_admin)])
def approve_image(image_id: str, req: ApproveRequest):
    entry = set_approval(image_id, True, req.caption, req.structures)
    if entry is None:
        raise HTTPException(status_code=404, detail="Image not found")
    cache_clear()
    return {"status": "approved", "id": image_id}


@app.post("/admin/images/{image_id}/unapprove", dependencies=[Depends(require_admin)])
def unapprove_image(image_id: str):
    entry = set_approval(image_id, False)
    if entry is None:
        raise HTTPException(status_code=404, detail="Image not found")
    cache_clear()
    return {"status": "unapproved", "id": image_id}


@app.delete("/admin/images/{image_id}", dependencies=[Depends(require_admin)])
def delete_image(image_id: str):
    remove_image(image_id)
    cache_clear()
    return {"status": "deleted", "id": image_id}


# ---------- Student feedback (human in the loop) ----------

@app.post("/feedback", dependencies=[Depends(limit_feedback)])
def submit_feedback(fb: FeedbackRequest, background: BackgroundTasks):
    """Thumbs up or down. Every thumbs-down is queued for a human to review."""
    if fb.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

    record = {
        "question": fb.question[:2000],
        "answer": fb.answer[:6000],
        "rating": fb.rating,
        "comment": fb.comment[:1000],
        "lectures": (fb.lectures or recall_lectures(fb.answer))[:10],
    }
    background.add_task(save_feedback, record)  # saved after the response is sent
    return {"status": "received"}


@app.post("/feedback/dispute", dependencies=[Depends(limit_feedback)])
def dispute_answer(req: DisputeRequest, background: BackgroundTasks):
    """A student says an answer is incorrect. Queue it for a human. No AI correction."""
    claim = req.claim.strip() or "The student says this answer is incorrect."
    background.add_task(
        save_feedback,
        dispute_record(req.question, req.answer, claim, recall_lectures(req.answer)),
    )
    return {"reply": DISPUTE_REPLY, "status": "pending_review"}


@app.get("/admin/feedback", dependencies=[Depends(require_admin)])
def get_feedback(
    rating: str | None = None,
    status: str | None = None,
    reason: str | None = None,
    limit: int = 50,
):
    """Every item, one by one. Try status=pending_review&reason=incorrect."""
    return list_feedback(rating=rating, status=status, reason=reason, limit=min(limit, 200))


@app.get("/admin/feedback/queue", dependencies=[Depends(require_admin)])
def feedback_queue(status: str = "pending_review", limit: int = 50):
    """The review queue: thumbs-downs grouped by question, most urgent first."""
    return build_queue(status=status, limit=min(limit, 200))


@app.post("/admin/feedback/resolve-group", dependencies=[Depends(require_admin)])
def resolve_feedback_group(req: ResolveGroupRequest):
    """A human closes every pending thumbs-down about the same question at once."""
    check_decision(req.decision)
    return {"resolved": resolve_group(req.fingerprint, req.decision, req.comment)}


@app.post("/admin/feedback/resolve-lecture", dependencies=[Depends(require_admin)])
def resolve_feedback_lecture(req: ResolveLectureRequest):
    """A human closes every pending thumbs-down for one lecture, for example after fixing that note."""
    check_decision(req.decision)
    return {"resolved": resolve_lecture(req.lecture, req.decision, req.comment)}


@app.post("/admin/feedback/{feedback_id}/resolve", dependencies=[Depends(require_admin)])
def resolve(feedback_id: str, req: ResolveRequest):
    """A human closes one thumbs-down."""
    check_decision(req.decision)
    record = resolve_feedback(feedback_id, req.decision, req.comment)
    if record is None:
        raise HTTPException(status_code=404, detail="Feedback not found")
    return record


# ---------- Instructor review badge and weekly report ----------

@app.get("/admin/chatlog", dependencies=[Depends(require_admin)])
def admin_chatlog(days: int = 7, q: str = "", mode: str = "", limit: int = 200):
    """Recent questions and answers, newest first. Filter with q (text) and mode."""
    return {"records": chatlog.read_log(days, q, mode, limit)}


@app.get("/admin/chatlog.csv", dependencies=[Depends(require_admin)])
def admin_chatlog_csv(days: int = 30, q: str = "", mode: str = ""):
    return Response(
        content=chatlog.to_csv(chatlog.read_log(days, q, mode, 2000)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="askinglis-chatlog.csv"'},
    )


@app.get("/admin/chatlog.xlsx", dependencies=[Depends(require_admin)])
def admin_chatlog_xlsx(days: int = 90, q: str = "", mode: str = ""):
    return Response(
        content=chatlog.to_xlsx(chatlog.read_log(days, q, mode, 20000)),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="askinglis-chatlog.xlsx"'},
    )


@app.get("/reviewed", dependencies=[Depends(limit_images)])
def reviewed():
    """Which lectures the instructor has reviewed, and when. Shown as a badge next to the lecture name."""
    return {"lectures": reviewed_lectures()}


@app.get("/admin/report", dependencies=[Depends(require_admin)])
def admin_report(weeks: int = 4):
    """What students ask about and miss. Counts only, no student text."""
    flush_usage()
    return build_report(weeks)


@app.on_event("shutdown")
def save_usage_on_shutdown():
    flush_usage()


# ---------- Practice questions (Phase 2) ----------

@app.post(
    "/admin/questions/generate",
    dependencies=[Depends(require_admin), Depends(require_ready)],
)
def admin_generate_questions(req: QuestionRequest):
    """Build a batch of questions using the NBME guide. With save=true the batch is stored in the
    private repo under questions/generated/."""
    try:
        batch = generate_questions(req.topic, req.count, generate_quiz)
    except Exception as e:
        print(f"Question generation failed: {e}")
        raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
    saved_to = save_batch(req.topic, batch["items"]) if req.save and batch["items"] else None
    return {**batch, "saved_to": saved_to}


# ---------- Admin routes (used by the notes review interface) ----------

@app.post(
    "/admin/ingest-note",
    response_model=IngestNoteResponse,
    dependencies=[Depends(require_admin)],
)
def ingest_note(note: IngestNoteRequest):
    result = ingest_text(note.filename, note.text)
    cache_clear()  # old answers may be based on the previous version of this note
    if result.get("status") == "ingested":
        mark_reviewed([note.filename])  # the review tool only sends finalized notes
        threading.Thread(target=rebuild_vocabulary, daemon=True).start()
    return IngestNoteResponse(**result)


@app.get("/admin/notes", dependencies=[Depends(require_admin)])
def list_notes():
    data = collection.get(include=["metadatas"])
    notes = {}
    for m in data["metadatas"]:
        entry = notes.setdefault(
            m["source"], {"filename": m["source"], "content_hash": m["content_hash"], "chunks": 0}
        )
        entry["chunks"] += 1
    return sorted(notes.values(), key=lambda n: n["filename"])


@app.get("/admin/notes/{filename}", dependencies=[Depends(require_admin)])
def get_note(filename: str):
    safe_name = os.path.basename(filename)
    text = read_note_from_github(safe_name)
    if text is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"filename": safe_name, "text": text}


@app.delete("/admin/notes/{filename}", dependencies=[Depends(require_admin)])
def delete_note(filename: str):
    safe_name = os.path.basename(filename)
    remove_existing_chunks(safe_name)
    delete_from_github(safe_name)
    cache_clear()
    threading.Thread(target=rebuild_vocabulary, daemon=True).start()
    return {"status": "deleted", "filename": safe_name}