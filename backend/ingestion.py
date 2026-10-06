import os
import hashlib
import chromadb
from chromadb.utils import embedding_functions
from docx import Document
from github import Github

NOTES_FOLDER = "./data/notes"
CHUNK_SIZE = 300

chroma_client = chromadb.PersistentClient(path="./chroma_store")
embedding_fn = embedding_functions.DefaultEmbeddingFunction()
collection = chroma_client.get_or_create_collection(
    name="anatomy_notes",
    embedding_function=embedding_fn,
)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPO = os.environ.get("GITHUB_REPO")
GITHUB_NOTES_PATH = "data/notes"

_github_client = None


def get_github_repo():
    global _github_client
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None
    if _github_client is None:
        _github_client = Github(GITHUB_TOKEN)
    return _github_client.get_repo(GITHUB_REPO)


def push_to_github(filename: str, text: str):
    repo = get_github_repo()
    if repo is None:
        return

    github_path = f"{GITHUB_NOTES_PATH}/{filename}.txt"
    try:
        existing_file = repo.get_contents(github_path)
        repo.update_file(
            path=github_path,
            message=f"Update note: {filename}",
            content=text,
            sha=existing_file.sha,
        )
    except Exception:
        repo.create_file(
            path=github_path,
            message=f"Add note: {filename}",
            content=text,
        )


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
    for i, chunk in enumerate(chunks):
        collection.add(
            documents=[chunk],
            metadatas=[{"source": filename, "content_hash": current_hash, "chunk_index": i}],
            ids=[f"{filename}_{i}"],
        )

    if save_backup:
        os.makedirs(NOTES_FOLDER, exist_ok=True)
        local_path = os.path.join(NOTES_FOLDER, f"{filename}.txt")
        with open(local_path, "w") as f:
            f.write(text)
        push_to_github(filename, text)

    return {"status": "ingested", "filename": filename, "chunks": len(chunks)}


def rebuild_from_backups():
    if not os.path.exists(NOTES_FOLDER):
        return []

    results = []
    for filename in os.listdir(NOTES_FOLDER):
        if not filename.endswith(".txt"):
            continue
        filepath = os.path.join(NOTES_FOLDER, filename)
        with open(filepath, "r") as f:
            text = f.read()
        original_name = filename.replace(".txt", "")
        result = ingest_text(original_name, text, force=True, save_backup=False)
        results.append(result)

    return results


def read_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def ingest_local_docx_folder(force: bool = False) -> list[dict]:
    results = []
    if not os.path.exists(NOTES_FOLDER):
        os.makedirs(NOTES_FOLDER)
        return results

    for filename in os.listdir(NOTES_FOLDER):
        if not filename.endswith(".docx"):
            continue
        filepath = os.path.join(NOTES_FOLDER, filename)
        text = read_docx(filepath)
        result = ingest_text(filename, text, force=force)
        results.append(result)

    return results


if __name__ == "__main__":
    results = ingest_local_docx_folder()
    for r in results:
        print(r)