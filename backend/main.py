from dotenv import load_dotenv
load_dotenv()
import os
import secrets
from fastapi import FastAPI, HTTPException, Header, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai

from ingestion import (
    ingest_text,
    collection,
    rebuild_from_backups,
    read_note_from_github,
    remove_existing_chunks,
    delete_from_github,
)
from feedback import save_feedback, list_feedback

app = FastAPI(title="AskInglis Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.5-flash-lite")
client = genai.Client(api_key=GEMINI_API_KEY)

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")


def generate(prompt: str) -> str:
    """The only place the app calls Gemini, so a future model or SDK change is one edit."""
    response = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
    return (response.text or "").strip()


def require_admin(x_admin_key: str = Header(default="")):
    """Blocks /admin routes unless the caller sends the right X-Admin-Key header."""
    if not ADMIN_API_KEY or not secrets.compare_digest(x_admin_key, ADMIN_API_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ---------- Models ----------

class Turn(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[Turn] = []


class ChatResponse(BaseModel):
    reply: str
    sources: list[str]
    lectures: list[str] = []


class FeedbackRequest(BaseModel):
    question: str
    answer: str
    rating: str  # "up" or "down"
    comment: str = ""
    lectures: list[str] = []


class IngestNoteRequest(BaseModel):
    filename: str
    text: str


class IngestNoteResponse(BaseModel):
    status: str
    filename: str
    chunks: int | None = None
    reason: str | None = None


# ---------- Startup and health ----------

@app.on_event("startup")
def startup_event():
    results = rebuild_from_backups()
    print(f"Startup rebuild: {results}")


@app.get("/")
def health_check():
    return {"status": "ok", "message": "AskInglis backend running"}


# ---------- Chat ----------

def format_history(history: list[Turn]) -> str:
    return "\n".join(f"{t.role}: {t.content[:1000]}" for t in history[-6:])


def standalone_question(message: str, history: list[Turn]) -> str:
    """Turn a follow-up like 'how do I do that?' into a full question,
    so the note search has something to match. Falls back to the original."""
    if not history:
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


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    query = standalone_question(request.message, request.history)

    results = collection.query(query_texts=[query], n_results=4)
    docs = results["documents"][0] if results["documents"] else []
    metas = results["metadatas"][0] if results["metadatas"] else []

    sources = [f"[{m['source']}] {d}" for d, m in zip(docs, metas)]
    lectures = list(dict.fromkeys(m["source"] for m in metas))
    context = "\n\n".join(sources)

    history_block = format_history(request.history)
    if history_block:
        history_block = f"\nConversation so far:\n{history_block}\n"

    prompt = f"""You are a helpful anatomy study assistant for UB Human Anatomy Laboratory students.
Answer clearly and concisely, using the notes below as your primary source.
Each note excerpt starts with the lecture it came from, in [brackets].
If the notes don't fully cover the question, use general anatomy knowledge, say that you are doing so, and don't contradict the notes.

Anatomy varies between bodies. If the student mentions a body type or specimen characteristic
(for example obese, thin, muscular, elderly, child, small or large frame, sex differences, or a variant),
or asks how something differs for different body types, adapt your answer to it.
This applies to every anatomy topic, not only dissection:
- For dissection questions, explain how the approach, landmarks, depth and what they will find change.
- For other questions, explain how the structure's size, position, relations or appearance change.
Use body type details the student gave earlier in the conversation.
Start with what the course notes say, then explain the adaptation, and clearly mark anything that
comes from general knowledge rather than the notes. If you are not confident about a specific
difference, say so instead of guessing. If a difference could indicate an abnormality, say that
and tell the student to check with their instructor.
If the student asks about differences between body types without naming one, briefly cover the common ones.

Notes:
{context}
{history_block}
Question: {request.message}

Answer:"""

    try:
        reply = generate(prompt)
    except Exception as e:
        print(f"Gemini call failed: {e}")
        raise HTTPException(status_code=502, detail="The AI service is unavailable right now.")

    if not reply:
        reply = "I couldn't generate an answer for that. Please try rephrasing your question."
    return ChatResponse(reply=reply, sources=sources, lectures=lectures)


# ---------- Student feedback ----------

@app.post("/feedback")
def submit_feedback(fb: FeedbackRequest, background: BackgroundTasks):
    if fb.rating not in ("up", "down"):
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

    record = {
        "question": fb.question[:2000],
        "answer": fb.answer[:6000],
        "rating": fb.rating,
        "comment": fb.comment[:1000],
        "lectures": fb.lectures[:10],
    }
    background.add_task(save_feedback, record)  # saved after the response is sent
    return {"status": "received"}


@app.get("/admin/feedback", dependencies=[Depends(require_admin)])
def get_feedback(rating: str | None = None, limit: int = 50):
    return list_feedback(rating=rating, limit=min(limit, 200))


# ---------- Admin routes (used by the notes review interface) ----------

@app.post(
    "/admin/ingest-note",
    response_model=IngestNoteResponse,
    dependencies=[Depends(require_admin)],
)
def ingest_note(note: IngestNoteRequest):
    result = ingest_text(note.filename, note.text)
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
    return {"status": "deleted", "filename": safe_name}