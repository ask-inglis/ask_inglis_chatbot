# AskInglis backend

The server behind the AskInglis study assistant for UB Human Anatomy Laboratory students.
It answers questions from Dr. Inglis's lecture notes, keeps a review queue for student
feedback, and takes finished notes from the review tool. A person decides what is wrong;
the AI never edits notes or corrects itself.

## How it works
1. **Notes in.** The review tool sends a finalized note to `POST /admin/ingest-note`. The backend
   splits it into overlapping chunks, stores them in a search index (ChromaDB), and backs the text
   up as `data/notes/<name>.txt` in a **private** GitHub repo.
2. **Questions answered.** `POST /chat` finds the best chunks, asks the AI model for a short bulleted
   answer, and returns it with the lecture names. Most answers end with a **Body type note** on how the
   approach or anatomy can differ between bodies (slender, obese, elderly, variants). Anything the notes
   don't cover is labeled "General knowledge:". Answers about names or definitions skip the note.
3. **Feedback reviewed.** A thumbs-down (or a typed "that's wrong") is saved to the private repo and
   grouped in a review queue. A person resolves each item.

The search index lives in `chroma_store/` and is rebuilt from the private repo every time the server
starts, so it can be deleted safely.

## Practice questions (Phase 2)
Turned on with `QUIZ_IN_CHAT=true`. Keep it `false` on the public site until Phase 2 is approved.
- A student asks for a quiz ("quiz me on the brachium", "a few more"). Questions are written from the
  notes, checked against NBME-style item-writing rules, and checked that the correct answer appears in
  the notes. Every quiz has multiple-choice options.
- Answers stay hidden. They appear when the student says "show answers", or asks for them in the
  request ("5 questions with answers"). Replying "1B 2D 3A" gives a score with explanations.
- Grading only works on the most recent reply. The answer key is kept in memory, so it expires when
  the server restarts (the student is told to ask for a new quiz).
- Admin route `POST /admin/questions/generate` builds a batch and saves it under `questions/generated/`
  in the private repo.

## Files
| File | Purpose |
|---|---|
| `main.py` | The API: chat, quiz handling, feedback, admin routes, model calls |
| `ingestion.py` | Splits notes, stores them, backs them up, rebuilds the index at startup |
| `feedback.py` | Saves thumbs-up/down, builds the grouped review queue, resolves items |
| `review.py` | Spots "that's wrong" messages, remembers which lectures an answer used and the current quiz |
| `protect.py` | Allowed websites (CORS), rate limits, daily cap |
| `cache.py` | Remembers answers to repeated first questions for 6 hours |
| `questions.py` | Phase 2: question writing, checks, quiz requests, grading |
| `images.py`, `guides.py` | Phase 3 and NBME guide code, not enabled on the public site |
| `phase1_backend.py` | End-to-end check of Phase 1 |

## Run it locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then replace every placeholder with a real value
uvicorn main:app
```
Wait for `Warm-up finished`. `GET /` shows `"ready": true` when it can answer. Until then, `/chat`
returns `503`. To run the check from a second terminal:
```bash
set -a; source .env; set +a
python3 phase1_backend.py
```

## Settings (`.env`)
See `.env.example` for every setting with comments. The main ones:

| Setting | What it is |
|---|---|
| `LLM_PROVIDER` | `gemini`, `custom` (any OpenAI-compatible service, set up for Cerebras) or `bullsai` (UB network only) |
| `LLM_FALLBACK` | If a non-Gemini provider fails, try Gemini |
| `GEMINI_API_KEY`, `GEMINI_MODEL_NAME`, `GEMINI_FALLBACK_MODELS`, `GEMINI_TIMEOUT_SECONDS` | Gemini settings; fallback models are tried in order if the main one is busy |
| `CUSTOM_LLM_BASE_URL`, `CUSTOM_LLM_MODEL_NAME`, `CUSTOM_LLM_API_KEY`, `CUSTOM_LLM_TIMEOUT`, `CUSTOM_LLM_MAX_TOKENS` | The custom provider |
| `BULLSAI_*` | UB's AI service. Reachable only from UB's network |
| `GITHUB_TOKEN`, `GITHUB_NOTES_REPO` | Token and `owner/repo` of the private notes repo |
| `ADMIN_API_KEY` | Secret that the `X-Admin-Key` header must match on `/admin` routes |
| `ALLOWED_ORIGINS` | Websites allowed to call the API, comma-separated (scheme and host only) |
| `TRUSTED_PROXY_HOPS` | Proxies in front of the server (Render uses 1) |
| `CHAT_PER_MIN`, `DISPUTE_PER_MIN`, `FEEDBACK_PER_MIN`, `IMAGES_PER_MIN`, `DAILY_CHAT_LIMIT` | Rate limits (0 turns one off) |
| `CACHE_TTL_SECONDS`, `CACHE_MAX_ENTRIES` | How long and how many repeated answers are remembered |
| `RETRIEVAL_DISTANCE_RATIO` | How much weaker than the best match a chunk may be (default 1.3) |
| `EXTERNAL_SOURCES`, `WIKI_USER_AGENT`, `DISPUTE_IN_CHAT` | Answer checking when a student disputes an answer |
| `PUBLIC_BASE_URL` | The backend's public address (used for image links) |
| `QUIZ_IN_CHAT`, `QUESTION_OPTIONS` | Phase 2 practice questions and options per question |

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
- `POST /admin/questions/generate`: `{topic, count, save}`
- Decisions: `note_fixed`, `answer_was_correct`, `dismissed`

## Common jobs
- **Add or update a note:** finalize it in the review tool, or `POST /admin/ingest-note` with the same
  filename. The old chunks are replaced and the answer cache is cleared.
- **Read student feedback:** `GET /admin/feedback/queue`, or browse `feedback/up/` and `feedback/down/`
  in the private repo.
- **Change the AI model:** edit `GEMINI_MODEL_NAME` (or `CUSTOM_LLM_MODEL_NAME`) and restart. Providers
  retire models often.
- **Switch provider:** set `LLM_PROVIDER` to `gemini` or `custom` and fill in that provider's settings.
- **Rebuild the index:** delete `chroma_store/` and restart.

## Deploy (Render)
1. Create a Web Service from the GitHub repo.
2. Build command: `pip install -r requirements.txt`. Start command:
   `uvicorn main:app --host 0.0.0.0 --port $PORT`. Health check path: `/`.
3. Under Environment, add every variable from `.env.example` with real values. Never commit `.env`.
   Set `TRUSTED_PROXY_HOPS=1`, `ALLOWED_ORIGINS` (the frontend's address), `PUBLIC_BASE_URL` (the
   service's address) and keep `QUIZ_IN_CHAT=false` until Phase 2 is approved.
4. After the first deploy, wait for `"ready": true` at `GET /`, then run the Phase 1 check against the
   live address.
5. Check memory use in the host's metrics during startup, since the index rebuild is the heaviest step.

## Handing over to a new owner
The service is deployed from GitHub, so a new owner can redeploy it under their own account:
1. They create a hosting account and connect the repo (steps in "Deploy" above).
2. They create their own AI model key and a new GitHub token with Contents read and write on the notes
   repo only, and generate a new admin key.
3. They set those values in the host's environment settings.
4. The frontend's backend address changes once, and `ALLOWED_ORIGINS` and `PUBLIC_BASE_URL` are updated.
5. The previous owner turns off the old service and revokes their old keys.

## Credentials and who owns them
| Credential | Where it lives | Notes |
|---|---|---|
| Gemini key | `.env` or the host's environment settings | Tied to one person's Google account. Move it to Dr. Inglis's or UB's account before handover |
| Custom provider key | Same | Tied to the account that created it. Same handover rule |
| GitHub token | Same | Fine-grained token with Contents read and write on the notes repo only. It expires, so note the date and renew it |
| Admin key | Same, and the review tool's server | Rotate by changing it in both places |
| BullsAI key | Not used by the public site | Personal to the person who requested it |

Never put any of these in the frontend or in a public repository.

## Known limits
- Free-tier hosting sleeps when idle, so the first visit after a quiet period waits for the warm-up.
- Free AI tiers have daily and per-minute limits that the providers can change. When the main model
  fails, the server tries the fallback models, then shows the most relevant note passages instead of an
  answer. Free tiers may use questions for the provider's own purposes, so students should not enter
  personal information.
- BullsAI (UB's AI service) is reachable only from UB's network, so the public site cannot use it.
- Donor pictures in lecture files are HIPAA-protected. Only text is stored. Never add pictures.
- The quiz answer key lives in memory and is lost on restart.
- Student chats are not stored on the server. The chat screen keeps them in the student's browser.

## Troubleshooting
- `401` on an admin route: the `X-Admin-Key` doesn't match `ADMIN_API_KEY`.
- `503` from `/chat`: still starting up. Wait 30 seconds.
- The reply says "The AI helper is busy": the AI call failed. Check the server log for the status code.
- `422`: the request body is malformed or too long.
- Quiz request returns a normal answer instead of questions: `QUIZ_IN_CHAT` is `false`.