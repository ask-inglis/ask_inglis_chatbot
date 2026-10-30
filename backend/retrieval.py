"""Finding the right notes for a question.

Lecture notes explain the anatomy; dissection guides ("Class NN - ...") explain what to do at the table.
A concept question searches the lectures. A how-to-dissect question searches the guides first.
"""
import re

from ingestion import collection

PROCEDURE = re.compile(
    r"\b(dissect\w*|incision|incise|cut|cutting|reflect\w*|retract\w*|step[- ]by[- ]step|steps?|procedure|"
    r"technique|scalpel|forceps|probe|bone (?:saw|cutter)|saw|expos(?:e|ing)|"
    r"how (?:do|should|can|would) (?:i|we) (?:cut|open|remove|expose|find|reach|approach|start|begin|clean))\b",
    re.IGNORECASE,
)


def wants_guide(question: str) -> bool:
    """True when the student is asking how to do something at the dissection table."""
    return bool(PROCEDURE.search(question or ""))


def _query(query: str, n: int, kind: str | None):
    """Returns a list of (document, metadata, distance), best first."""
    if n <= 0:
        return []
    args = {"query_texts": [query], "n_results": n, "include": ["documents", "metadatas", "distances"]}
    if kind:
        args["where"] = {"kind": kind}
    r = collection.query(**args)
    docs = r["documents"][0] if r["documents"] else []
    metas = r["metadatas"][0] if r["metadatas"] else []
    dists = r["distances"][0] if r.get("distances") else [0.0] * len(docs)
    return list(zip(docs, metas, dists))


def search_notes(query: str, n: int = 4, prefer: str | None = None, ratio: float | None = None):
    """Returns (docs, metas, distances).

    prefer="lecture": lecture notes only, plus the best guide chunk if it matches better than any lecture.
    prefer="guide":   guide chunks first, plus the best lecture chunk as background.
    prefer=None:      picks one of the two from the wording of the question.
    ratio: if set, drops chunks whose distance is more than this multiple of the best one."""
    prefer = prefer or ("guide" if wants_guide(query) else "lecture")
    if prefer == "guide":
        items = _query(query, max(n - 1, 1), "guide") + _query(query, 1, "lecture")
    else:
        lectures, guide = _query(query, n, "lecture"), _query(query, 1, "guide")
        if guide and (not lectures or guide[0][2] < lectures[0][2]):
            items = sorted(lectures + guide, key=lambda t: t[2])[:n]
        else:
            items = lectures
    if not items:  # chunks saved before 'kind' existed, or nothing of that kind: search everything
        items = _query(query, n, None)
    if ratio and items:
        limit = max(min(t[2] for t in items), 0.1) * ratio
        items = [t for t in items if t[2] <= limit]
    return [t[0] for t in items], [t[1] for t in items], [t[2] for t in items]