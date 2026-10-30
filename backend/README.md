# AskInglis backend

The server behind the AskInglis study assistant for UB Human Anatomy Laboratory students.
It answers questions from Dr. Inglis's lecture notes and dissection guides, writes practice questions,
keeps a review queue for student feedback, and takes finished notes from the review tool. A person
decides what is wrong; the AI never edits notes or corrects itself.

## How it works
1. **Notes in.** The review tool sends a finalized note to `POST /admin/ingest-note`. The backend
   splits it into overlapping chunks, stores them in a search index (ChromaDB), and backs the text
   up as `data/notes/<name>.txt` in a **private** GitHub repo. Notes can also be committed to that
   repo as `.docx` (lecture notes) or `.pptx` (dissection guides) and read in with `python3 ingestion.py`.
2. **Questions answered.** `POST /chat` finds the best chunks, asks the AI for a short bulleted answer,
   and returns it with the lecture names. Most answers end with a **Body type note** on how the anatomy
   or the approach changes between bodies. Anything not in the notes is labeled "General knowledge:".
3. **Lectures and guides.** Every chunk is labeled `lecture` or `guide`. Concept questions search the
   lecture notes. How-to-dissect questions (cut, incision, reflect, expose, steps) search the dissection
   guides first, with one lecture chunk as background.
4. **Practice questions (Phase 2).** Typing "quiz me on the brachium" gives NBME-style multiple-choice
   questions. Answers stay hidden until the student asks ("show answers", or "with answers" in the
   request). Replying "1a 2b 3c" grades the quiz. Answers and grading explain why the correct option is
   right, why each wrong option is wrong and the mix-up behind it, and give a study tip.
5. **Feedback reviewed.** A thumbs-down (or a typed "that's wrong") is saved to the private repo and
   grouped in a review queue. A person resolves each item.

The search index lives in `chroma_store/` and is rebuilt from the private repo every time the server
starts, so it can be deleted safely.

## Files
| File | Purpose |
|---|---|
| `main.py` | The API: chat, quiz handling, feedback, admin routes, model calls |
| `ingestion.py` | Reads notes (`.docx`, `.pptx`, `.txt`), splits and stores them, backs them up, rebuilds the index at startup |
| `retrieval.py` | Picks lecture or guide chunks for a question |
| `github_auth.py` | Signs in to GitHub as the org's bot app (or with a token as a fallback) |
| `questions.py` | Writes, checks, grades and formats practice questions |
| `gen_samples.py` | Makes sample question sets for review (`samples_review.csv`) |
| `compare_llms.py` | Compares the custom model (Cerebras) with Gemini on sample questions |
| `feedback.py` | Saves thumbs-up/down, builds the grouped review queue, resolves items |
| `review.py` | Spots "that's wrong" messages, remembers which lectures an answer used and the last quiz |
| `protect.py` | Allowed websites (CORS), rate limits, daily cap |
| `cache.py` | Remembers answers to repeated first questions for 6 hours |
| `guides.py` | Reads the NBME item-writing guide that sets the practice-question rules |
| `images.py` | Phase 3 image code, **not enabled yet** |
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
| `LLM_PROVIDER` | `gemini`, `bullsai` or `custom` (Cerebras). `LLM_FALLBACK=true` falls back to Gemini if another provider fails |
| `GEMINI_API_KEY`, `GEMINI_MODEL_NAME`, `GEMINI_FALLBACK_MODELS`, `GEMINI_TIMEOUT_SECONDS` | Gemini key, main model, models tried in order if it is overloaded, and call timeout |
| `CUSTOM_LLM_BASE_URL`, `CUSTOM_LLM_MODEL_NAME`, `CUSTOM_LLM_API_KEY`, `CUSTOM_LLM_TIMEOUT`, `CUSTOM_LLM_MAX_TOKENS` | Any OpenAI-compatible service (set up for Cerebras) |
| `BULLSAI_*` | UB's AI service. Reachable only from UB's network, so not used by the public site |
| `GITHUB_NOTES_REPO` | `owner/repo` of the private notes repo, for example `ask-inglis/ask_inglis_notes` |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_FILE` | The org's GitHub App (`ask-inglis-bot`) and the path to its `.pem` key. On a host, `GITHUB_APP_PRIVATE_KEY` can hold the key with newlines written as `\n` |
| `GITHUB_TOKEN` | Old personal token. Only used if the app settings are missing or fail. Remove once the app works |
| `GUIDE_NAME_PATTERN` | Which file names are dissection guides. Default: names containing "Class" and a number |
| `ADMIN_API_KEY` | Secret that the `X-Admin-Key` header must match on `/admin` routes |
| `ALLOWED_ORIGINS` | Websites allowed to call the API, comma-separated |
| `CHAT_PER_MIN`, `FEEDBACK_PER_MIN`, `IMAGES_PER_MIN`, `DAILY_CHAT_LIMIT` | Rate limits (0 turns one off) |
| `CACHE_TTL_SECONDS` | How long repeated answers are remembered |
| `RETRIEVAL_DISTANCE_RATIO` | How much weaker than the best match a chunk may be (default 1.3) |
| `QUIZ_IN_CHAT` | Practice questions in chat. Keep `false` until Phase 2 is public |
| `QUESTION_OPTIONS` | Options per question (default 5) |

## API
**Public**
- `GET /`: status and readiness
- `POST /chat`: `{message, history: [{role, content}]}` returns `{reply, sources, lectures, images, degraded}`
- `POST /feedback`: `{question, answer, rating: "up"|"down", comment, lectures}`

**Admin** (header `X-Admin-Key`, call from a server, never from browser code)
- `POST /admin/ingest-note`: `{filename, text}`, where `filename` is the note's name without an extension
- `GET /admin/notes`, `GET /admin/notes/{filename}`, `DELETE /admin/notes/{filename}`
- `POST /admin/questions/generate`: `{topic, count, save}` builds a batch of practice questions
- `GET /admin/feedback/queue`: thumbs-downs grouped by question
- `POST /admin/feedback/{id}/resolve`: `{decision, comment}`
- `POST /admin/feedback/resolve-group`: `{fingerprint, decision, comment}`
- Decisions: `note_fixed`, `answer_was_correct`, `dismissed`

## Common jobs
- **Add or update a note:** finalize it in the review tool, or `POST /admin/ingest-note` with the same
  filename. The old chunks are replaced and the answer cache is cleared.
- **Add or update a dissection guide:** commit the `.pptx` to `data/notes/` in the private repo, named
  like `Class 07 - Anterior Arm and Forearm.pptx`, then run `python3 ingestion.py`. Only the text is
  read; pictures are skipped. Unchanged files are skipped on later runs.
- **Make sample practice questions:** `python3 gen_samples.py --per-topic 2`, then open
  `samples_review.csv`. Needs `ADMIN_API_KEY` and the server running.
- **Read student feedback:** `GET /admin/feedback/queue`, or browse `feedback/up/` and `feedback/down/`
  in the private repo.
- **Change the AI model:** edit `GEMINI_MODEL_NAME` (or the `CUSTOM_LLM_*` settings) and restart.
  Models are retired often.
- **Rebuild the index:** delete `chroma_store/` and restart.

## Credentials and who owns them
| Credential | Where it lives | Notes |
|---|---|---|
| GitHub App key | A `.pem` file kept outside the project, or the host's secret files | The app `ask-inglis-bot` belongs to the `ask-inglis` organization and only has Contents read and write on the notes repo. Keep at least two org owners. To rotate, generate a new key on the app's page and delete the old one. Commits show as `ask-inglis-bot[bot]` |
| Gemini key | `.env` or the host's environment settings | Tied to one person's Google account. Move it to Dr. Inglis's or UB's account before handover |
| Cerebras key | Same | Personal to whoever created the account |
| Admin key | Same, and the review tool's server | Rotate by changing it in both places |
| BullsAI key | Not used by the public site | Personal to the person who requested it |

Never put any of these in the frontend, in Trello, or in a public repository.

## Known limits
- Free-tier hosting sleeps when idle, so the first visit after a quiet period waits for the warm-up.
- Gemini's free tier can be overloaded or rate-limited. The server tries the fallback models, then shows
  the most relevant note passages instead of an answer.
- BullsAI is reachable only from UB's network, so the public site cannot use it.
- Donor pictures are HIPAA-protected. Only text is stored, and pictures in `.pptx` guides are skipped.
  Never add donor pictures to the repo or the index.
- Practice questions are checked automatically (item-writing rules, the answer must be backed by a
  sentence in the notes, and a second model call). The same AI wrote and checks them, so a person
  should still review samples before students rely on them.
- A quiz's answer key is kept in the server's memory. If the server restarts, an old quiz can no longer be
  graded and the student is asked to request a new one.
- Phase 2 is off by default (`QUIZ_IN_CHAT=false`) and Phase 3 (images) is written but switched off.

## Troubleshooting
- `401` on an admin route: the `X-Admin-Key` doesn't match `ADMIN_API_KEY`.
- `503` from `/chat`: still starting up. Wait 30 seconds.
- The reply says "The AI helper is busy": the AI call failed. Check the server log for the status code.
- `GitHub App sign-in failed (...)` in the log: the message says why. "private key is malformed" means
  the key file path or text is wrong; use `GITHUB_APP_PRIVATE_KEY_FILE` with the downloaded `.pem`.
  If `GITHUB_TOKEN` is still set the server falls back to it and says so.
- `GitHub not configured`: neither the app settings nor a token are set, so notes can't be saved or rebuilt.
- A dissection guide gives no answers: check that it is a `.pptx` (not `.ppt`) in `data/notes/` and that
  `python3 ingestion.py` printed `ingested` for it.
- A how-to question returns only lecture notes: its wording had none of the dissection words. Add the
  word to `PROCEDURE` in `retrieval.py`.
- `422`: the request body is malformed or too long.