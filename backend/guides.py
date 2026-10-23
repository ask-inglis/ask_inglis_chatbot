from dotenv import load_dotenv
load_dotenv()

import base64
import hashlib
import io
import os
import sys

from ingestion import chroma_client, embedding_fn, get_github_repo, chunk_text, read_docx_bytes

NBME_TEXT_PATH = "guides/nbme-rules.txt"  # extracted text, saved in the private repo
RULE_QUERIES = [
    "writing the question stem and lead-in",
    "writing answer options and distractors",
    "item flaws: cues, negative wording, absolute terms, all of the above",
]
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
MIN_IMAGE_BYTES = 5000  # skips icons and logos


def _col():
    return chroma_client.get_or_create_collection(name="nbme_rules", embedding_function=embedding_fn)


def _index_text(text: str) -> int:
    try:
        chroma_client.delete_collection("nbme_rules")
    except Exception:
        pass
    col = _col()
    chunks = chunk_text(text, 200)
    for i in range(0, len(chunks), 100):
        batch = chunks[i:i + 100]
        col.add(documents=batch, ids=[f"nbme_{i + j}" for j in range(len(batch))])
    return len(chunks)


def rebuild_guides() -> int:
    """Run on startup: reload the NBME guide text from the private repo."""
    repo = get_github_repo()
    if repo is None:
        return 0
    try:
        text = repo.get_contents(NBME_TEXT_PATH).decoded_content.decode("utf-8")
    except Exception:
        return 0
    return _index_text(text)


def rule_excerpts(max_chars: int = 3500) -> str:
    """The most relevant item-writing rules from the NBME guide, for the question prompt."""
    col = _col()
    if col.count() == 0:
        return ""
    seen, parts, total = set(), [], 0
    for q in RULE_QUERIES:
        res = col.query(query_texts=[q], n_results=min(2, col.count()))
        for doc in res["documents"][0]:
            if doc in seen:
                continue
            if total + len(doc) > max_chars:
                return "\n\n".join(parts)
            seen.add(doc)
            parts.append(doc)
            total += len(doc)
    return "\n\n".join(parts)


# ---------- One-time setup commands ----------

def _fetch(repo, path: str) -> bytes:
    blob = repo.get_git_blob(repo.get_contents(path).sha)
    return base64.b64decode(blob.content)


def _put(repo, path: str, content, message: str):
    try:
        existing = repo.get_contents(path)
        repo.update_file(path, message, content, existing.sha)
    except Exception:
        repo.create_file(path, message, content)


def _text_of(data: bytes, name: str) -> str:
    if name.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    if name.lower().endswith(".docx"):
        return read_docx_bytes(data)
    return data.decode("utf-8", errors="ignore")


def load_nbme(repo_path: str) -> int:
    repo = get_github_repo()
    text = _text_of(_fetch(repo, repo_path), repo_path)
    if len(text.strip()) < 500:
        raise RuntimeError("Very little text was extracted. Is the file a scanned PDF?")
    _put(repo, NBME_TEXT_PATH, text, "Add extracted NBME guide text")
    return _index_text(text)


def _docx_images(data: bytes) -> list[dict]:
    from docx import Document
    doc = Document(io.BytesIO(data))
    found, pending, last_text = [], [], ""
    for p in doc.paragraphs:
        for rid in p._element.xpath(".//a:blip/@r:embed"):
            part = doc.part.related_parts[rid]
            img = {"data": part.blob, "ext": part.partname.ext, "before": last_text, "after": ""}
            found.append(img)
            pending.append(img)
        text = p.text.strip()
        if text:
            for img in pending:
                img["after"] = text
            pending, last_text = [], text
    for img in found:
        after, before = img["after"], img["before"]
        img["caption"] = after if after.lower().startswith(("figure", "fig", "image")) else (before or after)
    return found


def _pdf_images(data: bytes) -> list[dict]:
    from pypdf import PdfReader
    found = []
    for n, page in enumerate(PdfReader(io.BytesIO(data)).pages, 1):
        text = " ".join((page.extract_text() or "").split())[:150]
        for img in page.images:
            found.append(
                {"data": img.data, "ext": os.path.splitext(img.name)[1] or ".png", "caption": f"Page {n}: {text}"}
            )
    return found


def import_images(repo_path: str) -> int:
    """Pull pictures out of the dissection guide and add them to the catalog as UNAPPROVED."""
    from images import add_images
    repo = get_github_repo()
    data = _fetch(repo, repo_path)
    raw = _pdf_images(data) if repo_path.lower().endswith(".pdf") else _docx_images(data)

    entries, seen = [], set()
    for img in raw:
        if len(img["data"]) < MIN_IMAGE_BYTES:
            continue
        digest = hashlib.sha1(img["data"]).hexdigest()[:10]
        if digest in seen:
            continue
        seen.add(digest)
        image_id = f"guide-{digest}"
        file_path = f"images/guide/{image_id}{img['ext']}"
        _put(repo, file_path, img["data"], f"Add guide image {image_id}")
        entries.append({
            "id": image_id,
            "caption": (img["caption"] or "Dissection guide figure")[:200],
            "structures": [],
            "url": f"{PUBLIC_BASE_URL}/guide-image/{image_id}",
            "page_url": "",
            "source": "UB dissection guide",
            "license": "UB course material",
            "attribution": "Dr. Inglis, UB Human Anatomy",
            "file_path": file_path,
            "approved": False,
        })
    if entries:
        add_images(entries)
    return len(entries)


if __name__ == "__main__":
    usage = 'Usage: python3 guides.py nbme "<path in private repo>"  |  python3 guides.py images "<path in private repo>"'
    if len(sys.argv) != 3 or sys.argv[1] not in ("nbme", "images"):
        print(usage)
        sys.exit(1)
    if sys.argv[1] == "nbme":
        print(f"NBME guide indexed: {load_nbme(sys.argv[2])} chunks")
    else:
        print(f"Imported {import_images(sys.argv[2])} images (all unapproved)")
    sys.stdout.flush()
    os._exit(0)  # skips the native-library shutdown crash