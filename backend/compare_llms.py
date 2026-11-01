"""Check Cerebras (the custom provider) and compare it with Gemini on real questions from your notes.

Run from the backend folder, after the server has been started once (so chroma_store is filled):
    python3 compare_llms.py --models        # list the models your Cerebras key can use
    python3 compare_llms.py                 # run the comparison, write compare_llms.md

Needs in .env: CUSTOM_LLM_BASE_URL, CUSTOM_LLM_API_KEY, CUSTOM_LLM_MODEL_NAME, GEMINI_API_KEY.
Keys are never printed. LLM_PROVIDER can stay "gemini" while you test.
"""
import argparse
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

QUESTIONS = [
    "How do I find the radial nerve in the arm?",
    "What is the blood supply of the middle ear ossicles?",
    "Which muscles of the hand does the ulnar nerve supply?",
    "How does the approach to the brachial artery change in an obese donor?",
    "What are the boundaries of the anterior triangle of the neck?",
    "Where does the spinal cord end in an adult?",
]


def list_models():
    from openai import OpenAI

    client = OpenAI(api_key=os.environ["CUSTOM_LLM_API_KEY"], base_url=os.environ["CUSTOM_LLM_BASE_URL"])
    for m in client.models.list().data:
        print(m.id)


def build_prompt(app, question: str) -> str:
    """A simplified version of the chat prompt: same notes, same format rules."""
    res = app.collection.query(query_texts=[question], n_results=4, include=["documents", "metadatas"])
    docs = res["documents"][0] if res["documents"] else []
    metas = res["metadatas"][0] if res["metadatas"] else []
    context = "\n\n".join(f"[{m['source']}] {d}" for d, m in zip(docs, metas))
    return f"""You are a study assistant for UB Human Anatomy Laboratory students who are skimming at the dissection table.
Use the notes below as your primary source. Each note excerpt starts with the lecture it came from, in [brackets].
Don't contradict the notes. If the notes don't cover something, add a bullet that starts with "General knowledge:".

FORMAT (very important):
- Start with one bold line that answers the question directly.
- Then give 3 to 6 short bullet points, each under 20 words. Use "-" for bullets.
- Put structure names in **bold**.
- Keep the main answer under 150 words.
- Unless the question only asks for a name or a definition, end with a section titled exactly "**Body type note**" with 1 to 2 bullets.

Notes:
{context}

Question: {question}

Answer:"""


def timed(fn, prompt):
    start = time.time()
    try:
        out = fn(prompt)
        return out, time.time() - start, None
    except Exception as e:
        return "", time.time() - start, repr(e)[:200]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", action="store_true")
    ap.add_argument("--out", default="compare_llms.md")
    args = ap.parse_args()
    if args.models:
        return list_models()

    import main as app  # builds the clients from .env; does not start the server

    if app.custom_client is None or not app.CUSTOM_LLM_MODEL_NAME:
        raise SystemExit("Set CUSTOM_LLM_BASE_URL, CUSTOM_LLM_API_KEY and CUSTOM_LLM_MODEL_NAME in .env first")
    if app.gemini_client is None:
        raise SystemExit("Set GEMINI_API_KEY in .env to compare against Gemini")

    lines = ["# Cerebras vs Gemini\n", f"Custom model: `{app.CUSTOM_LLM_MODEL_NAME}`; Gemini: `{app.GEMINI_MODEL_NAME}`\n"]
    totals = {"custom": 0.0, "gemini": 0.0}
    for q in QUESTIONS:
        prompt = build_prompt(app, q)
        c_out, c_t, c_err = timed(app.call_custom, prompt)
        g_out, g_t, g_err = timed(app.call_gemini, prompt)
        totals["custom"] += c_t
        totals["gemini"] += g_t
        has_note = lambda t: bool(re.search(r"body type note", t, re.I))
        lines += [
            f"\n## {q}\n",
            f"**Cerebras** ({c_t:.1f}s, body-type note: {has_note(c_out)})\n",
            c_out or f"ERROR: {c_err}",
            f"\n**Gemini** ({g_t:.1f}s, body-type note: {has_note(g_out)})\n",
            g_out or f"ERROR: {g_err}",
            "\n---",
        ]
        print(f"{q[:50]:50s} cerebras {c_t:4.1f}s {'ERR' if c_err else 'ok '} | gemini {g_t:4.1f}s {'ERR' if g_err else 'ok '}")
        time.sleep(1)
    lines.append(f"\nTotal time: Cerebras {totals['custom']:.1f}s, Gemini {totals['gemini']:.1f}s")
    open(args.out, "w", encoding="utf-8").write("\n".join(lines))
    print(f"Wrote {args.out}. Token usage per call is in the server-style log lines above.")


if __name__ == "__main__":
    main()