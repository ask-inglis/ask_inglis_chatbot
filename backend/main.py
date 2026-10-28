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
from protect import allowed_origins, rate_limit, daily_cap
from cache import cache_get, cache_put, cache_clear

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
GEMINI_TIMEOUT_MS = int(os.environ.get("GEMINI_TIMEOUT_SECONDS", "45")) * 1000

BULLSAI_API_KEY = os.environ.get("BULLSAI_API_KEY")
BULLSAI_BASE_URL = os.environ.get("BULLSAI_BASE_URL", "https://gateway.bullsai.buffalo.edu/v1")
BULLSAI_MODEL_NAME = os.environ.get("BULLSAI_MODEL_NAME", "openai/gpt-oss-20b")
BULLSAI_TIMEOUT = float(os.environ.get("BULLSAI_TIMEOUT", "30"))
BULLSAI_MAX_TOKENS = int(os.environ.get("BULLSAI_MAX_TOKENS", "1500"))

CUSTOM_LLM_API_KEY = os.environ.get("CUSTOM_LLM_API_KEY")
CUSTOM_LLM_BASE_URL = os.environ.get("CUSTOM_LLM_BASE_URL")
CUSTOM_LLM_MODEL_NAME = os.environ.get("CUSTOM_LLM_MODEL_NAME")
CUSTOM_LLM_TIMEOUT = float(os.environ.get("CUSTOM_LLM_TIMEOUT", "30"))
CUSTOM_LLM_MAX_TOKENS = int(os.environ.get("CUSTOM_LLM_MAX_TOKENS", "1500"))

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


def call_openai_style(client, model_name, prompt, max_tokens, label):
    start = time.time()
    response = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens,
    )
    print(f"{label} {model_name}: {time.time() - start:.1f}s, usage={getattr(response, 'usage', None)}")
    return (response.choices[0].message.content or "").strip()


def call_gemini(prompt: str) -> str:
    """Try the main model, then each fallback model, so one overloaded model doesn't stop the chat."""
    last_error = None
    for model_name in [GEMINI_MODEL_NAME] + GEMINI_FALLBACK_MODELS:
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
    except Exception as e:
        print(f"Warm-up failed: {e}")
    finally:
        state["ready"] = True
        print(f"Warm-up finished in {time.time() - start:.0f}s")


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


class ChatResponse(BaseModel):
    reply: str
    sources: list[str]
    lectures: list[str] = []
    images: list[dict] = []
    degraded: bool = False  # true when the AI was unavailable and note excerpts were returned


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


@app.post(
    "/chat",
    response_model=ChatResponse,
    dependencies=[Depends(require_ready), Depends(limit_chat), Depends(daily_chat_cap)],
)
def chat(request: ChatRequest, background: BackgroundTasks):
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
            try:
                    batch = generate_questions(
                    intent["topic"], intent["count"], generate,
                    avoid=[i["stem"] for i in items] if items else None,  # don't repeat the last quiz
                    )
            except Exception as e:
                print(f"Quiz generation failed: {e}")
                raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")
            print(
                f"Quiz: {len(batch['items'])} accepted, {len(batch['rejected'])} rejected "
                f"in {time.time() - t0:.1f}s"
            )
            new_items = batch["items"]
            quiz_reply = format_quiz_for(new_items, intent["with_answers"])
            if new_items:
                remember_quiz(quiz_reply, new_items)
                remember_answer(quiz_reply, batch["lectures"])
            return ChatResponse(reply=quiz_reply, sources=[], lectures=batch["lectures"])

        if items:
            reply = None
            if intent["kind"] == "answers":
                reply = format_answer_key(items)
            else:
                picks = parse_picks(request.message, len(items))
                if picks:
                    reply = grade_answers(items, picks)
            if reply:
                quiz_lectures = list(dict.fromkeys(i["lecture"] for i in items if i["lecture"]))
                remember_answer(reply, quiz_lectures)
                return ChatResponse(reply=reply, sources=[], lectures=quiz_lectures)
        elif seen_quiz and (intent["kind"] == "answers" or parse_picks(request.message, 10)):
            return ChatResponse(
                reply=(
                    "- That quiz has expired, because the server restarted.\n"
                    '- Ask for a new one, for example "quiz me on the brachium".'
                ),
                sources=[],
                lectures=[],
            )

    # First questions (no history) can be answered from the cache
    cacheable = not request.history
    if cacheable:
        hit = cache_get(request.message)
        if hit:
            print(f"Cache hit after {time.time() - t0:.2f}s")
            return ChatResponse(**hit)

    query = standalone_question(request.message, request.history)

    results = collection.query(
        query_texts=[query], n_results=4, include=["documents", "metadatas", "distances"]
    )
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []
    dists = results["distances"][0] if results["distances"] else []
    if dists:
        print(f"Retrieval distances: {[round(d, 2) for d in dists]}")
        # drop chunks that are much weaker matches than the best one
        limit = max(dists[0], 0.1) * RETRIEVAL_DISTANCE_RATIO
        keep = [i for i, d in enumerate(dists) if d <= limit]
        docs, metas = [docs[i] for i in keep], [metas[i] for i in keep]
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
    if BODY_TYPE_PATTERN.search(student_text):
        body_rule = (
            'The student mentioned a body type or specimen. You MUST end your answer with a section titled exactly '
            '"**Body type note**" containing 2 to 3 bullets on how the approach, landmarks, depth, or the '
            "structure's size and position change for that body type. Use body type details from earlier in the conversation."
        )
    else:
        body_rule = (
            'Unless the question only asks for a name or a definition, end your answer with a section titled exactly '
            '"**Body type note**" with 1 to 2 bullets. Say how the approach, landmarks, depth, or the structure\'s '
            "size and position typically vary between bodies (for example slender, obese, elderly, small or large "
            "frame, or common anatomical variants) and what that changes at the dissection table. "
            'If the notes do not cover it, start that bullet with "General knowledge:". '
            "If a difference could mean an abnormality, say so and tell the student to check with their instructor."
        )

    prompt = f"""You are a study assistant for UB Human Anatomy Laboratory students who are skimming at the dissection table.
Use the notes below as your primary source. Each note excerpt starts with the lecture it came from, in [brackets].
Don't contradict the notes. If the notes don't cover something, add a bullet that starts with "General knowledge:" and say so.

FORMAT (very important):
- Start with one bold line that answers the question directly.
- Then give 3 to 6 short bullet points, each under 20 words. Use "-" for bullets.
- Put structure names in **bold**.
- Never write a paragraph longer than two sentences. Keep the main answer under 150 words unless the student asks for more detail; the Body type note is extra.
- {body_rule}
- If you are not confident about a specific difference, say so. If a difference could mean an abnormality, say that and tell the
  student to check with their instructor.

Notes:
{context}
{history_block}
Question: {request.message}

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

    if answered and images and IMAGES_IN_REPLY:
        top = images[0]
        alt = top["caption"].replace("[", "(").replace("]", ")")
        credit = top["attribution"] or top["source"]
        reply += f"\n\n![{alt}]({top['url']})\n\n*{credit} ({top['license']})*"

    data = {"reply": reply, "sources": sources, "lectures": lectures, "images": images}
    if cacheable and answered:
        cache_put(request.message, data)
    return ChatResponse(**data)


# ---------- Images ----------

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


# ---------- Practice questions (Phase 2) ----------

@app.post(
    "/admin/questions/generate",
    dependencies=[Depends(require_admin), Depends(require_ready)],
)
def admin_generate_questions(req: QuestionRequest):
    """Build a batch of questions using the NBME guide. With save=true the batch is stored in the
    private repo under questions/generated/."""
    try:
        batch = generate_questions(req.topic, req.count, generate)
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
    return {"status": "deleted", "filename": safe_name}