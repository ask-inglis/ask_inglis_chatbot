from dotenv import load_dotenv
load_dotenv()

import os
import io
import re
import sys
import base64
import hashlib
import chromadb
from chromadb.utils import embedding_functions
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from github_auth import get_notes_repo

CHUNK_SIZE = 300
CHUNK_OVERLAP = 60  # each chunk repeats the last 60 words of the previous one

chroma_client = chromadb.PersistentClient(path="./chroma_store")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="anatomy_notes",
    embedding_function=embedding_fn,
)

GITHUB_NOTES_REPO = os.environ.get("GITHUB_NOTES_REPO")
GITHUB_NOTES_PATH = "data/notes"  # path inside the private notes repo, not on disk

# Dissection guides are the files named "Class NN - ..." (also "ANA407 Class 33 ..."). Everything else is a lecture.
# If your guide files are named differently, set GUIDE_NAME_PATTERN in .env to a regular expression.
GUIDE_NAME = re.compile(os.environ.get("GUIDE_NAME_PATTERN", r"\bClass\s+\d+"), re.IGNORECASE)


def source_kind(filename: str) -> str:
    """'guide' for a dissection guide, 'lecture' for lecture notes. Stored on every chunk."""
    return "guide" if GUIDE_NAME.search(filename) else "lecture"


# ---------- Private notes repo ----------

def get_github_repo():
    return get_notes_repo()


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

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into chunks of about `chunk_size` words. Neighbouring chunks share `overlap`
    words, so a sentence at a boundary is never cut off from its context."""
    words = text.split()
    if not words:
        return []
    step = max(chunk_size - overlap, 1)
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start:start + chunk_size]))
        if start + chunk_size >= len(words):
            break
    return chunks


def text_hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def remove_existing_chunks(source_filename: str):
    existing = collection.get(where={"source": source_filename})
    if existing and existing["ids"]:
        collection.delete(ids=existing["ids"])


def ingest_text(filename: str, text: str, force: bool = False, save_backup: bool = True) -> dict:
    """Chunk and store a note in ChromaDB, and back it up to the private repo."""
    current_hash = text_hash(text)
    kind = source_kind(filename)

    existing = collection.get(where={"source": filename}, limit=1)
    already_ingested = (
        existing
        and existing["metadatas"]
        and existing["metadatas"][0].get("content_hash") == current_hash
        and existing["metadatas"][0].get("kind") == kind  # chunks saved before 'kind' existed are re-ingested
    )

    if already_ingested and not force:
        return {"status": "skipped", "filename": filename, "reason": "no changes detected"}

    remove_existing_chunks(filename)

    chunks = chunk_text(text)
    if chunks:
        collection.add(
            documents=chunks,
            metadatas=[
                {"source": filename, "content_hash": current_hash, "chunk_index": i, "kind": kind}
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
    print(f"GitHub repo: {GITHUB_NOTES_REPO!r}, repo opened: {repo is not None}")
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


def _shape_text(shape) -> list[str]:
    """Text from one slide shape: text boxes, tables and grouped shapes. Pictures are skipped,
    so only words ever enter the index, never images."""
    out = []
    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for inner in shape.shapes:
            out.extend(_shape_text(inner))
    elif shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
        pass
    elif getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                out.append(" | ".join(cells))
    elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            t = "".join(run.text for run in para.runs).strip()
            if t:
                out.append(t)
    return out


def read_pptx_bytes(data: bytes) -> str:
    """Extract text from a .pptx slide by slide. Each slide starts with a 'Slide N: title' line so
    that retrieved chunks say which part of the guide they came from. Speaker notes are included."""
    prs = Presentation(io.BytesIO(data))
    parts = []
    for n, slide in enumerate(prs.slides, 1):
        title = ""
        if slide.shapes.title is not None and slide.shapes.title.has_text_frame:
            title = slide.shapes.title.text_frame.text.strip()
        lines = []
        for shape in slide.shapes:
            for t in _shape_text(shape):
                if t != title and t not in lines:
                    lines.append(t)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip() if slide.notes_slide.notes_text_frame else ""
            if notes:
                lines.append("Notes: " + notes)
        if lines or title:
            parts.append(f"Slide {n}: {title}".rstrip(": ").rstrip() + "\n" + "\n".join(lines))
    return "\n\n".join(parts)


def seed_from_repo_docx(force: bool = False) -> list[dict]:
    """Read .docx files from the private repo, extract text, ingest it, and save
    a .txt copy back to the same repo. Nothing is written to local disk.
    .pptx files (the dissection guides) are read as text only."""
    repo = get_github_repo()
    if repo is None:
        print("GitHub not configured")
        return []

    try:
        files = repo.get_contents(GITHUB_NOTES_PATH)
    except Exception as e:
        print(f"Could not list '{GITHUB_NOTES_PATH}' in {GITHUB_NOTES_REPO}: {e}")
        try:
            print("Top level of the repo:", [c.path for c in repo.get_contents("")])
        except Exception as e2:
            print(f"Could not even list the repo root: {e2} (check token access)")
        return []

    results = []
    failed = []
    for f in files:
        if f.name.endswith(".doc"):
            print(f"SKIPPED (old .doc format, convert to .docx): {f.name}")
            continue
        is_pptx = f.name.endswith(".pptx")
        if not (f.name.endswith(".docx") or is_pptx):
            continue
        try:
            blob = repo.get_git_blob(f.sha)  # works for files over 1 MB
            raw = base64.b64decode(blob.content)
            text = read_pptx_bytes(raw) if is_pptx else read_docx_bytes(raw)
            result = ingest_text(f.name.rsplit(".", 1)[0], text, force=force)
            print(result)
            results.append(result)
        except Exception as e:
            print(f"FAILED: {f.name} -> {e}")
            failed.append(f.name)

    if failed:
        print("\nThese files could not be read. Re-save each one as .docx (notes) or .pptx (guides):")
        for name in failed:
            print(f"  - {name}")
    return results


if __name__ == "__main__":
    seed_from_repo_docx()
    print("Done.")
    sys.stdout.flush()
    os._exit(0)  # skips the native-library shutdown crash