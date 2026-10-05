import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import chromadb
from chromadb.utils import embedding_functions
import google.generativeai as genai

app = FastAPI(title="AskInglis")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

MOCK_MODE = not GEMINI_API_KEY  # auto-enables if no key is set

if not MOCK_MODE:
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

chroma_client = chromadb.PersistentClient(path="./chroma_store")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="anatomy_notes",
    embedding_function=embedding_fn,
)


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# ---------- Handler ----------

def handle_qa(message: str) -> str:
    results = collection.query(query_texts=[message], n_results=3)
    retrieved_chunks = results["documents"][0] if results["documents"] else []
    context = "\n\n".join(retrieved_chunks)

    prompt = f"""You are a helpful anatomy study assistant.
Answer the student's question clearly and concisely, using the notes below as your primary source.
If the notes don't fully cover it, use general anatomy knowledge, but don't contradict the notes.

Notes:
{context}

Question: {message}

Answer:"""

    response = model.generate_content(prompt)
    return response.text


# ---------- Routes ----------

@app.get("/")
def health_check():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    reply = handle_qa(request.message)
    return ChatResponse(reply=reply)