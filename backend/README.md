# AskInglis backend

The server behind the AskInglis study assistant for UB Human Anatomy Laboratory students.
It answers questions from Dr. Inglis's lecture notes, keeps a review queue for student
feedback, and takes finished notes from the review tool. A person decides what is wrong;
the AI never edits notes or corrects itself.

## How it works
1. **Notes in.** The review tool sends a finalized note to `POST /admin/ingest-note`. The backend
   splits it into overlapping chunks, stores them in a search index (ChromaDB), and backs the text
   up as `data/notes/<name>.txt` in a **private** GitHub repo.
2. **Questions answered.** `POST /chat` finds the best chunks, asks Gemini for a short bulleted answer,
   and returns it with the lecture names.
3. **Feedback reviewed.** A thumbs-down (or a typed "that's wrong") is saved to the private repo and
   grouped in a review queue. A person resolves each item.

The search index lives in `chroma_store/` and is rebuilt from the private repo every time the server
starts, so it can be deleted safely.

## Files
| File | Purpose |
|---|---|
| `main.py` | The API: chat, feedback, admin routes, model calls |
| `ingestion.py` | Splits notes, stores them, backs them up, rebuilds the index at startup |
| `feedback.py` | Saves thumbs-up/down, builds the grouped review queue, resolves items |
| `review.py` | Spots "that's wrong" messages, remembers which lectures an answer used |
| `protect.py` | Allowed websites (CORS), rate limits, daily cap |
| `cache.py` | Remembers answers to repeated first questions for 6 hours |
| `images.py`, `guides.py`, `questions.py` | Phase 2 and 3 code, **not enabled yet** |
| `phase1_backend.py` | End-to-end check of Phase 1 |

## Run it locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values below
uvicorn main:app
```
Wait for `Warm-up finished`. `GET /` shows `"ready": true` when it can answer. Until then, `/chat`
returns `503`. To run the check from a second terminal:
```bash
set -a; source .env; set +a
python3 phase1_backend.py
```

## Settings (`.env`)
| Setting | What it is |
|---|---|
| `GEMINI_API_KEY` | Key for Google's Gemini API |
| `GEMINI_MODEL_NAME`, `GEMINI_FALLBACK_MODELS` | Main model, and models tried in order if it is overloaded |
| `GEMINI_TIMEOUT_SECONDS` | How long to wait for one model call |
| `LLM_PROVIDER` | `gemini` (public site), `bullsai` or `custom` |
| `GITHUB_TOKEN`, `GITHUB_NOTES_REPO` | Token and `owner/repo` of the private notes repo |
| `ADMIN_API_KEY` | Secret that the `X-Admin-Key` header must match on `/admin` routes |
| `ALLOWED_ORIGINS` | Websites allowed to call the API, comma-separated |
| `CHAT_PER_MIN`, `FEEDBACK_PER_MIN`, `IMAGES_PER_MIN`, `DAILY_CHAT_LIMIT` | Rate limits (0 turns one off) |
| `CACHE_TTL_SECONDS` | How long repeated answers are remembered |
| `RETRIEVAL_DISTANCE_RATIO` | How much weaker than the best match a chunk may be (default 1.3) |
| `QUIZ_IN_CHAT` | Phase 2 practice questions in chat. Keep `false` for now |

## API
**Public**
- `GET /`: status and readiness
- `POST /chat`: `{message, history: [{role, content}]}` returns `{reply, sources, lectures, images, degraded}`
- `POST /feedback`: `{question, answer, rating: "up"|"down", comment, lectures}`

**Admin** (header `X-Admin-Key`, call from a server, never from browser code)
- `POST /admin/ingest-note`: `{filename, text}`, where `filename` is the note's name without an extension
- `GET /admin/notes`, `GET /admin/notes/{filename}`, `DELETE /admin/notes/{filename}`
- `GET /admin/feedback/queue`: thumbs-downs grouped by question
- `POST /admin/feedback/{id}/resolve`: `{decision, comment}`
- `POST /admin/feedback/resolve-group`: `{fingerprint, decision, comment}`
- Decisions: `note_fixed`, `answer_was_correct`, `dismissed`

## Common jobs
- **Add or update a note:** finalize it in the review tool, or `POST /admin/ingest-note` with the same
  filename. The old chunks are replaced and the answer cache is cleared.
- **Read student feedback:** `GET /admin/feedback/queue`, or browse `feedback/up/` and `feedback/down/`
  in the private repo.
- **Change the AI model:** edit `GEMINI_MODEL_NAME` and restart. Google retires models often.
- **Rebuild the index:** delete `chroma_store/` and restart.

## Credentials and who owns them
| Credential | Where it lives | Notes |
|---|---|---|
| Gemini key | `.env` or the host's environment settings | Tied to one person's Google account. Move it to Dr. Inglis's or UB's account before handover |
| GitHub token | Same | Fine-grained token with Contents read and write on the notes repo only. It expires, so note the date and renew it |
| Admin key | Same, and the review tool's server | Rotate by changing it in both places |
| BullsAI key | Not used by the public site | Personal to the person who requested it |

Never put any of these in the frontend or in a public repository.

## Known limits
- Free-tier hosting sleeps when idle, so the first visit after a quiet period waits for the warm-up.
- Gemini's free tier can be overloaded or rate-limited. The server tries the fallback models, then shows
  the most relevant note passages instead of an answer.
- BullsAI (UB's AI service) is reachable only from UB's network, so the public site cannot use it.
- Donor pictures in lecture files are HIPAA-protected. Only text is stored. Never add pictures.
- Phase 2 (practice questions) and Phase 3 (images) are written but switched off.

## Troubleshooting
- `401` on an admin route: the `X-Admin-Key` doesn't match `ADMIN_API_KEY`.
- `503` from `/chat`: still starting up. Wait 30 seconds.
- The reply says "The AI helper is busy": Gemini failed. Check the server log for the status code.
- `422`: the request body is malformed or too long.