import base64
import json
import os
from functools import lru_cache

from ingestion import chroma_client, embedding_fn, get_github_repo

CATALOG_PATH = "images/catalog.json"  # inside the private notes repo
REQUIRED = ("id", "caption", "url", "source", "license")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}

image_collection = chroma_client.get_or_create_collection(
    name="anatomy_images",
    embedding_function=embedding_fn,
)

_catalog: list[dict] = []


def _read_catalog() -> list[dict]:
    repo = get_github_repo()
    if repo is None:
        return []
    try:
        f = repo.get_contents(CATALOG_PATH)
        return json.loads(f.decoded_content.decode("utf-8"))
    except Exception:
        return []


def _write_catalog(entries: list[dict]):
    global _catalog
    _catalog = entries
    repo = get_github_repo()
    if repo is None:
        print("WARNING: GitHub not configured, image catalog was NOT saved")
        return
    content = json.dumps(entries, indent=2)
    try:
        try:
            existing = repo.get_contents(CATALOG_PATH)
        except Exception:
            repo.create_file(CATALOG_PATH, "Add image catalog", content)
            return
        repo.update_file(CATALOG_PATH, "Update image catalog", content, existing.sha)
    except Exception as e:
        print(f"Image catalog save failed: {e}")


def _index(entries: list[dict]):
    if not entries:
        return
    image_collection.upsert(
        ids=[e["id"] for e in entries],
        documents=[f"{e['caption']}. {' '.join(e.get('structures', []))}" for e in entries],
        metadatas=[
            {
                "caption": e["caption"],
                "url": e["url"],
                "page_url": e.get("page_url", ""),
                "source": e["source"],
                "license": e["license"],
                "attribution": e.get("attribution", ""),
                "approved": bool(e.get("approved", True)),
            }
            for e in entries
        ],
    )


def rebuild_images() -> int:
    """Run on startup: reload the catalog from the private repo into ChromaDB."""
    global _catalog
    _catalog = _read_catalog()
    _index(_catalog)
    return len(_catalog)


def add_images(new_entries: list[dict]) -> int:
    for e in new_entries:
        missing = [k for k in REQUIRED if not e.get(k)]
        if missing:
            raise ValueError(f"Image '{e.get('id')}' is missing: {missing}")
        if not (e.get("file_path") or e["url"].startswith("https://")):
            raise ValueError(f"Image '{e['id']}' must use an https URL")
        e.setdefault("approved", True)
    merged = {e["id"]: e for e in _catalog}
    for e in new_entries:
        merged[e["id"]] = e
    entries = list(merged.values())
    _write_catalog(entries)
    _index(new_entries)
    return len(entries)


def set_approval(image_id: str, approved: bool, caption=None, structures=None):
    """A human approves (or un-approves) an image, and can fix its caption first."""
    entries = list(_catalog)
    for e in entries:
        if e["id"] == image_id:
            e["approved"] = approved
            if caption:
                e["caption"] = caption
            if structures is not None:
                e["structures"] = structures
            _write_catalog(entries)
            _index([e])
            return e
    return None


def list_images(approved=None) -> list[dict]:
    items = _catalog if approved is None else [e for e in _catalog if bool(e.get("approved", True)) == approved]
    return [
        {k: e.get(k) for k in ("id", "caption", "structures", "source", "license", "approved", "url")}
        for e in items
    ]


def remove_image(image_id: str):
    entry = next((e for e in _catalog if e["id"] == image_id), None)
    _write_catalog([e for e in _catalog if e["id"] != image_id])
    try:
        image_collection.delete(ids=[image_id])
    except Exception:
        pass
    repo = get_github_repo()
    if entry and entry.get("file_path") and repo is not None:
        try:
            f = repo.get_contents(entry["file_path"])
            repo.delete_file(entry["file_path"], f"Delete image {image_id}", f.sha)
        except Exception as e:
            print(f"Image file delete failed: {e}")


@lru_cache(maxsize=128)
def _fetch_bytes(path: str) -> bytes | None:
    repo = get_github_repo()
    if repo is None:
        return None
    try:
        blob = repo.get_git_blob(repo.get_contents(path).sha)  # works for files over 1 MB
        return base64.b64decode(blob.content)
    except Exception as e:
        print(f"Image fetch failed for {path}: {e}")
        return None


def read_image(image_id: str, require_approved: bool = True):
    """(bytes, mime type) for an image stored in the private repo, or None."""
    entry = next((e for e in _catalog if e["id"] == image_id), None)
    if not entry or not entry.get("file_path"):
        return None
    if require_approved and not entry.get("approved", True):
        return None
    data = _fetch_bytes(entry["file_path"])
    if data is None:
        return None
    return data, MIME.get(os.path.splitext(entry["file_path"])[1].lower(), "application/octet-stream")


def search_images(query: str, limit: int = 3, max_distance: float = 1.0) -> list[dict]:
    """Only approved images are ever returned."""
    count = image_collection.count()
    if count == 0:
        return []
    r = image_collection.query(
        query_texts=[query], n_results=min(limit, count), where={"approved": True}
    )
    results = []
    for meta, dist in zip(r["metadatas"][0], r["distances"][0]):
        if dist <= max_distance:
            results.append({**meta, "distance": round(dist, 3)})
    return results