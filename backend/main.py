from dotenv import load_dotenv
load_dotenv()
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai

from ingestion import ingest_text, collection, rebuild_from_backups

app = FastAPI(title="AskInglis Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.8-flash")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str
    sources: list[str]


class IngestNoteRequest(BaseModel):
    filename: str
    text: str


class IngestNoteResponse(BaseModel):
    status: str
    filename: str
    chunks: int | None = None
    reason: str | None = None


@app.on_event("startup")
def startup_event():
    results = rebuild_from_backups()
    print(f"Startup rebuild: {results}")


@app.get("/")
def health_check():
    return {"status": "ok", "message": "AskInglis backend running"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    results = collection.query(query_texts=[request.message], n_results=3)
    retrieved_chunks = results["documents"][0] if results["documents"] else []
    context = "\n\n".join(retrieved_chunks)

    prompt = f"""You are a helpful anatomy study assistant for UB Human Anatomy Laboratory students.
Answer the student's question clearly and concisely, using the notes below as your primary source.
If the notes don't fully cover it, use general anatomy knowledge, but don't contradict the notes.

Notes:
{context}

Question: {request.message}

Answer:"""

    response = model.generate_content(prompt)
    return ChatResponse(reply=response.text, sources=retrieved_chunks)


@app.post("/admin/ingest-note", response_model=IngestNoteResponse)
def ingest_note(note: IngestNoteRequest):
    result = ingest_text(note.filename, note.text)
    return IngestNoteResponse(**result)