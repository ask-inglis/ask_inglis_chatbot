from dotenv import load_dotenv
load_dotenv()

import os
import io
import sys
import base64
import hashlib
import chromadb
from chromadb.utils import embedding_functions
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from github import Github, Auth

CHUNK_SIZE = 300

chroma_client = chromadb.PersistentClient(path="./chroma_store")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="anatomy_notes",
    embedding_function=embedding_fn,
)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_NOTES_REPO = os.environ.get("GITHUB_NOTES_REPO")
GITHUB_NOTES_PATH = "data/notes"  # path inside the private notes repo, not on disk

_github_client = None


# ---------- Private notes repo ----------

def get_github_repo():
    global _github_client
    if not GITHUB_TOKEN or not GITHUB_NOTES_REPO:
        return None
    try:
        if _github_client is None:
            _github_client = Github(auth=Auth.Token(GITHUB_TOKEN))
        return _github_client.get_repo(GITHUB_NOTES_REPO)
    except Exception as e:
        print(f"Could not open GitHub repo '{GITHUB_NOTES_REPO}': {e}")
        return None


def push_to_github(filename: str, text: str):
    """Save a note as .txt in the private repo. Failures are logged, never raised."""
    repo = get_github_repo()
    if repo is None:
        print(f"WARNING: GitHub not configured, '{filename}' was NOT backed up")
        return

    path = f"{GITHUB_NOTES_PATH}/{filename}.txt"
    try:
        try:
            existing = repo.get_contents(path)
        except Exception:
            repo.create_file(path=path, message=f"Add note: {filename}", content=text)
            return
        repo.update_file(
            path=path,
            message=f"Update note: {filename}",
            content=text,
            sha=existing.sha,
        )
    except Exception as e:
        print(f"GitHub backup failed for {filename}: {e}")


def delete_from_github(filename: str):
    repo = get_github_repo()
    if repo is None:
        return
    path = f"{GITHUB_NOTES_PATH}/{filename}.txt"
    try:
        f = repo.get_contents(path)
        repo.delete_file(path, f"Delete note: {filename}", f.sha)
    except Exception as e:
        print(f"GitHub delete failed for {filename}: {e}")


def read_note_from_github(filename: str) -> str | None:
    repo = get_github_repo()
    if repo is None:
        return None
    try:
        f = repo.get_contents(f"{GITHUB_NOTES_PATH}/{filename}.txt")
        return f.decoded_content.decode("utf-8")
    except Exception:
        return None


# ---------- Chunking and ingestion ----------

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    words = text.split()
    return [" ".join(words[i:i + chunk_size]) for i in range(0, len(words), chunk_size)]


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def remove_existing_chunks(source_filename: str):
    existing = collection.get(where={"source": source_filename})
    if existing and existing["ids"]:
        collection.delete(ids=existing["ids"])


def ingest_text(filename: str, text: str, force: bool = False, save_backup: bool = True) -> dict:
    """Chunk and store a note in ChromaDB, and back it up to the private repo."""
    current_hash = text_hash(text)

    existing = collection.get(where={"source": filename}, limit=1)
    already_ingested = (
        existing
        and existing["metadatas"]
        and existing["metadatas"][0].get("content_hash") == current_hash
    )

    if already_ingested and not force:
        return {"status": "skipped", "filename": filename, "reason": "no changes detected"}

    remove_existing_chunks(filename)

    chunks = chunk_text(text)
    if chunks:
        collection.add(
            documents=chunks,
            metadatas=[
                {"source": filename, "content_hash": current_hash, "chunk_index": i}
                for i in range(len(chunks))
            ],
            ids=[f"{filename}_{i}" for i in range(len(chunks))],
        )

    if save_backup:
        push_to_github(filename, text)

    return {"status": "ingested", "filename": filename, "chunks": len(chunks)}


# ---------- Startup rebuild (reads only from the private repo) ----------

def rebuild_from_backups():
    """Run on startup. Rebuilds ChromaDB from the .txt notes in the private repo."""
    repo = get_github_repo()
    print(f"GitHub repo: {GITHUB_NOTES_REPO!r}, token set: {bool(GITHUB_TOKEN)}, repo opened: {repo is not None}")
    results = []

    if repo is None:
        print("WARNING: notes repo not available, skipping rebuild")
        return results

    try:
        files = repo.get_contents(GITHUB_NOTES_PATH)
    except Exception as e:
        print(f"Could not read notes from GitHub: {e}")
        return results

    for f in files:
        if not f.name.endswith(".txt"):
            continue
        text = f.decoded_content.decode("utf-8")
        results.append(ingest_text(f.name[:-4], text, save_backup=False))
    return results


# ---------- One-time seeding from the Word files in the private repo ----------

def read_docx_bytes(data: bytes) -> str:
    """Extract text from a .docx, keeping paragraphs and table rows in order."""
    doc = Document(io.BytesIO(data))
    parts = []
    for child in doc.element.body.iterchildren():
        if child.tag.endswith("}p"):
            t = Paragraph(child, doc).text.strip()
            if t:
                parts.append(t)
        elif child.tag.endswith("}tbl"):
            for row in Table(child, doc).rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    return "\n".join(parts)


def seed_from_repo_docx(force: bool = False) -> list[dict]:
    """Read .docx files from the private repo, extract text, ingest it, and save
    a .txt copy back to the same repo. Nothing is written to local disk."""
    repo = get_github_repo()
    if repo is None:
        print("GitHub not configured")
        return []

    results = []
    for f in repo.get_contents(GITHUB_NOTES_PATH):
        if f.name.endswith(".doc"):
            print(f"SKIPPED (old .doc format, convert to .docx): {f.name}")
            continue
        if not f.name.endswith(".docx"):
            continue
        blob = repo.get_git_blob(f.sha)  # works for files over 1 MB
        text = read_docx_bytes(base64.b64decode(blob.content))
        result = ingest_text(f.name[:-5], text, force=force)
        print(result)
        results.append(result)
    return results


if __name__ == "__main__":
    seed_from_repo_docx()
    print("Done.")
    sys.stdout.flush()
    os._exit(0)  # skips the native-library shutdown crash