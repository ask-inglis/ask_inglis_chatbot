# AskInglis backend

The server behind the AskInglis study assistant for UB Human Anatomy Laboratory students.
It answers questions from Dr. Inglis's lecture notes and dissection guides, writes practice
questions, keeps a review queue for student feedback, and takes finished notes from the review tool.
A person decides what is wrong; the AI never edits notes or corrects itself.

## How it works
1. **Notes in.** The review tool sends a finalized note to `POST /admin/ingest-note`. The backend splits it
   into overlapping chunks, stores them in a search index (ChromaDB), and backs the text up as
   `data/notes/<name>.txt` in a **private** GitHub repo. Dissection guides (`.pptx`) in the same repo are
   read for text only; pictures are skipped.
2. **Questions answered.** `POST /chat` finds the best chunks and asks the model for a short bulleted
   answer. Lecture notes are preferred. Dissection-guide chunks come first only for procedure questions
   (cut, reflect, expose, dissect). The reply includes the lecture names.
3. **Practice questions (Phase 2, switched off by default).** A student types a request such as
   "give me 5 quiz questions with answers on the brachium". The backend writes NBME-style questions from the
   notes, checks them, and hides the answers until the student asks. Each answer comes with a reason for every
   option and a study tip.
4. **Feedback reviewed.** A thumbs-down (or a typed "that's wrong") is saved to the private repo and grouped in a
   review queue. A person resolves each item. Resolutions are committed by the GitHub App bot.

The search index lives in `chroma_store/` and is rebuilt from the private repo every time the server starts, so
it can be deleted safely.

## Files
| File | Purpose |
|---|---|
| `main.py` | The API: chat, title, feedback, admin routes, model calls |
| `ingestion.py` | Splits notes, stores them, backs them up, reads `.docx`/`.pptx`, rebuilds the index at startup |
| `retrieval.py` | Searches the index; chooses lecture or dissection-guide chunks |
| `github_auth.py` | Signs in to GitHub as the GitHub App (falls back to a token only if one is set) |
| `feedback.py` | Saves thumbs-up/down, builds the grouped review queue, resolves items as the bot |
| `review.py` | Spots "that's wrong" messages, remembers which lectures an answer used and the last quiz |
| `questions.py` | Phase 2: writes, checks and grades practice questions |
| `guides.py` | Reads the NBME item-writing guide from the private repo |
| `protect.py` | Allowed websites (CORS), rate limits, daily cap |
| `cache.py` | Remembers answers to repeated first questions for 6 hours |
| `chatlog.py` | Keeps the instructor's log of questions and answers (no names or IP addresses) and copies it to the private notes repo |
| `quizgate.py` | Limits how many practice quizzes are written at the same moment, and makes the rest wait |
| `images.py` | Approved-image catalog. Phase 3 images are **not enabled yet** |
| `phase1_backend.py` | End-to-end check of Phase 1 |

## Run it locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env     # then fill in the values below
uvicorn main:app
```
Wait for `Warm-up finished`. `GET /` shows `"ready": true` when it can answer. Until then, `/chat` returns `503`.
Check that the startup log says `GitHub access: using the GitHub App`. To run the Phase 1 check from a second terminal:
```bash
set -a; source .env; set +a
python3 phase1_backend.py
```
`uvicorn --reload` does not reload `.env`. Restart the server after changing it.

## Settings (`.env`)
| Setting | What it is |
|---|---|
| `LLM_PROVIDER` | `gemini` (public site) or `bullsai` (UB network only) |
| `LLM_FALLBACK` | If a non-Gemini provider fails, fall back to Gemini (default `true`) |
| `GEMINI_API_KEY` | Key for Google's Gemini API |
| `GEMINI_MODEL_NAME`, `GEMINI_FALLBACK_MODELS` | Main model, and models tried in order if it is overloaded or retired |
| `GEMINI_TIMEOUT_SECONDS` | How long to wait for one model call |
| `GEMINI_QUIZ_MODEL` | Optional. A stronger model tried first for practice questions only |
| `BULLSAI_API_KEY`, `BULLSAI_BASE_URL`, `BULLSAI_MODEL_NAME` | BullsAI settings. Reachable only from UB's network |
| `BULLSAI_TIMEOUT`, `BULLSAI_MAX_TOKENS` | Keep max tokens high (8000+): gpt-oss models spend tokens thinking, and a low cap gives empty quizzes |
| `GITHUB_NOTES_REPO` | `owner/repo` of the private notes repo |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (or `GITHUB_APP_PRIVATE_KEY_FILE`) | The GitHub App (`ask-inglis-bot`) that reads and writes the notes repo |
| `GITHUB_APP_INSTALLATION_ID` | Optional. Looked up automatically if blank |
| `GITHUB_BOT_NAME`, `GITHUB_BOT_EMAIL` | The bot's name and `ID+name@users.noreply.github.com` address, used on resolution commits |
| `ADMIN_API_KEY` | Secret that the `X-Admin-Key` header must match on `/admin` routes |
| `ALLOWED_ORIGINS` | Websites allowed to call the API, comma-separated |
| `TRUSTED_PROXY_HOPS` | Number of proxies in front of the server. On the droplet it is 1 (the droplet's nginx; the nginx container passes the address through unchanged) |
| `CHAT_PER_MIN`, `DISPUTE_PER_MIN`, `FEEDBACK_PER_MIN`, `IMAGES_PER_MIN`, `TITLE_PER_MIN` | Rate limits per address per minute (0 turns one off) |
| `DAILY_CHAT_LIMIT` | Questions per day across everyone (0 = off) |
| `CACHE_TTL_SECONDS`, `CACHE_MAX_ENTRIES` | How long and how many repeated answers are remembered |
| `RETRIEVAL_DISTANCE_RATIO` | How much weaker than the best match a chunk may be (default 1.3) |
| `IMAGES_IN_REPLY`, `MAX_IMAGE_DISTANCE` | Approved images beside answers, and how strict the matching is |
| `EXTERNAL_SOURCES`, `WIKI_USER_AGENT`, `DISPUTE_IN_CHAT` | Answer checking when a student says an answer is wrong |
| `PUBLIC_BASE_URL` | Public address of this backend (used to build image links) |
| `QUIZ_IN_CHAT` | Phase 2 practice questions in chat. Keep `false` on the public site until approved |
| `QUESTION_OPTIONS` | Options per practice question (default 5) |
| `QUIZ_PARALLEL_CALLS` | Model calls run at the same time when writing a quiz (default 3). Lower it if Gemini returns 429 |
| `GUIDE_NAME_PATTERN` | File-name pattern that marks a note as a dissection guide (default `\bClass\s+\d+`) |
| `QUIZ_MAX_CONCURRENT`, `QUIZ_MAX_WAITING`, `QUIZ_WAIT_SECONDS` | How many quizzes are written at once (default 6), how many students may wait in line (default 14), and how long a student waits before being asked to try again (default 150 seconds) |
| `QUIZ_MAX_ROUNDS`, `QUIZ_SPARES`, `QUIZ_SHORT_OK` | Rounds of writing and checking before giving up (default 3), extra questions written per round (default 2), and how many questions short a quiz may be delivered (default 0) |
| `QUIZ_STYLE_REPO_PATH`, `QUIZ_REASONING_EFFORT` | Optional path of the question-style note in the notes repo (for example `style/question_style.md`), and (BullsAI only) `low`, `medium` or `high` thinking effort |
| `TRANSLATE_PER_MIN`, `GLOSS_PER_MIN` | Rate limits for translating answers (default 20) and glossary entries (default 40) |
| `LLM_RPM_LIMIT`, `LLM_RPD_LIMIT` | Model calls per minute and per day shared by everyone, kept under Gemini's free-tier limits. We use 9 and 450 |
| `LLM_QUIZ_CALLS_NEEDED` | Model calls one practice quiz needs (we use 6) |
| `PROFESSOR_INTEGRATION_KEY` | Secret used for the professor portal's connection to this backend |
| `CHATLOG_ENABLED`, `CHATLOG_DIR` | Turn the question log on or off (default on), and where the working copy is kept (default `chatlog`) |
| `CHATLOG_RETENTION_DAYS` | Days before old log files are deleted, here and in the notes repo (default 90) |
| `CHATLOG_REPO_SYNC`, `CHATLOG_SYNC_SECONDS`, `CHATLOG_REPO_FOLDER`, `CHATLOG_XLSX_SECONDS` | Copying the log into the notes repo (default on, every 600 seconds, folder `chatlog`) and how often the weekly Excel summary is made (default 3600) |

## API
**Public**
- `GET /`: status and readiness
- `POST /chat`: `{message, history: [{role, content}]}` returns `{reply, sources, lectures, images, degraded}`
- `POST /title`: `{message, reply?}` returns `{title}`, a 3 to 6 word conversation title. It never fails: if the model is busy, the
  title is the first words of the question
- `POST /feedback`: `{question, answer, rating: "up"|"down", comment, lectures}`
- `POST /feedback/dispute`: a student says an answer is wrong; the backend checks it against the notes
- `POST /translate`: `{text, target_language}` returns the answer in another language. Display only; anatomical terms stay in English
- `POST /gloss`: `{term, target_language}` returns a short glossary entry for one term
- `GET /reviewed`: which lectures the instructor has reviewed, and when
- `GET /images`, `GET /guide-image/{id}`: approved images

**Admin** (header `X-Admin-Key`, call from a server, never from browser code)
- `POST /admin/ingest-note`: `{filename, text}`, where `filename` is the note's name without an extension
- `GET /admin/notes`, `GET /admin/notes/{filename}`, `DELETE /admin/notes/{filename}`
- `GET /admin/feedback`, `GET /admin/feedback/queue`: thumbs-downs, grouped by question in the queue
- `GET /admin/chatlog` (also `.csv` and `.xlsx`): recent questions and answers, newest first
- `GET /admin/report`: what students ask about and miss (counts only, no student text)
- `POST /admin/feedback/{id}/resolve`: `{decision, comment}`
- `POST /admin/feedback/resolve-group`: `{fingerprint, decision, comment}`
- `POST /admin/feedback/resolve-lecture`: close every pending item for one lecture
- `POST /admin/questions/generate`: write a batch of practice questions (`save=true` stores it in the private repo)
- `GET/POST /admin/images` and approve, unapprove and delete routes: manage the approved-image catalog
- Decisions: `note_fixed`, `answer_was_correct`, `dismissed`

## Common jobs
- **Add or update a note:** finalize it in the review tool, or `POST /admin/ingest-note` with the same filename. The old chunks are
  replaced and the answer cache is cleared. A file whose name matches `GUIDE_NAME_PATTERN` (for example "Class 05 - ...") is treated as
  a dissection guide.
- **Read student feedback:** `GET /admin/feedback/queue`, or browse `feedback/up/` and `feedback/down/` in the private repo.
- **Change the AI model:** edit `GEMINI_MODEL_NAME` and restart. Google retires models often.
- **Turn practice questions on:** set `QUIZ_IN_CHAT=true` and restart. Students ask in chat, for example
  "give me 5 quiz questions with answers on the brachium", then "show the answers" or answer the questions to be graded.
- **Rebuild the index:** delete `chroma_store/` and restart.

## Running on the droplet
The backend runs as a Docker container on the droplet and is deployed when `main` is pushed (see the repo's main README). Settings are in `backend/.env` on the droplet. After changing it, restart the container:
```bash
docker ps --format '{{.Names}}' | grep -i backend     # the portal has a backend too; pick the chatbot's
docker restart NAME_HERE
docker logs -f --tail 20 NAME_HERE                    # wait for "Warm-up finished"
```
The notes folder and the chat log are copied to the private notes repo, so they survive a restart or a rebuild.

## Credentials and who owns them
| Credential | Where it lives | Notes |
|---|---|---|
| Gemini key | `.env` or the host's environment settings | Tied to one person's Google account. Move it to Dr. Inglis's or UB's account before handover. A paid key avoids the free tier's 429/503 errors |
| GitHub App (`ask-inglis-bot`) | Owned by the `ask-inglis` GitHub organization. App ID and private key in `.env` or the host | Needs at least two organization owners. If the key leaks, delete it on the app's settings page and generate a new one |
| Admin key | Same, and the review tool's server | Rotate by changing it in both places |
| BullsAI key | Not used by the public site | Personal to the person who requested it |

Never put any of these in the frontend or in a public repository. The old personal GitHub token is no longer used.

## Known limits
- After a restart the backend needs a short warm-up while it rebuilds the search index. `/chat` returns 503 until it finishes.
- The question log holds real student questions (no names, no IP addresses; emails and long digit strings are masked). It is stored in the private notes repo and deleted after `CHATLOG_RETENTION_DAYS`. UB approval for storing student questions is still to be confirmed.
- Gemini's free tier can be overloaded (503) or rate-limited (429). The server tries the fallback models, then shows the most
  relevant note passages instead of an answer.
- A practice quiz takes 1 to 2 minutes on the free tier. A question the checker cannot read is dropped and replaced, so a quiz may
  need an extra round. A quiz may come back with fewer questions than asked if the notes cannot support more.
- The quiz checker is the same model family that writes the questions, so it screens but does not prove accuracy. A person must
  review a sample before students use Phase 2.
- Answer keys for the last quiz are kept in memory, so they are lost on restart.
- BullsAI (UB's AI service) is reachable only from UB's network, so the public site cannot use it.
- Donor pictures in lecture files are HIPAA-protected. Only text is stored. Never add pictures.
- Phase 3 (diagrams and images) is not enabled yet.

## Troubleshooting
- `401` on an admin route: the `X-Admin-Key` doesn't match `ADMIN_API_KEY`.
- `503` from `/chat`: still starting up. Wait 30 seconds.
- The reply says "The AI helper is busy": the model call failed. Check the server log for the status code.
- Startup says `using a personal token`, or `GitHub App sign-in failed`: the app key is missing or malformed. Check
  `GITHUB_APP_ID` and the private key (use the `.pem` file path if the one-line key won't parse).
- Resolving feedback does nothing and the log says `Resolve feedback configuration error`: `GITHUB_BOT_NAME` and
  `GITHUB_BOT_EMAIL` are missing, or do not match each other.
- A quiz says "I couldn't write reliable practice questions": the notes may not cover the topic, or Google returned errors.
  Look for `Quiz:` lines in the log, name a specific lecture topic, and try again.
- `422`: the request body is malformed or too long.