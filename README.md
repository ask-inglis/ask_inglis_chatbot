# AskInglis student chatbot

AskInglis is a study assistant for students of the UB Human Anatomy Laboratory. Students ask questions and get short answers taken from Dr. Inglis's lecture notes and dissection guides. It can also write practice questions, take feedback, and translate answers.

This repository holds the student chatbot: the website (`frontend/`) and the server behind it (`backend/`). It is one of four AskInglis repositories:

| Repository | What it is |
|---|---|
| `ask_inglis_chatbot` (this one) | Student chatbot: website and backend |
| `ask_inglis_professor_v2` | Professor portal: review interface for notes and student feedback |
| `ask_inglis_proxy` | nginx container that routes web addresses to the two apps |
| `ask_inglis_notes` (private) | Lecture notes, feedback, chat logs and generated questions |

## Live addresses

| Address | What it is |
|---|---|
| https://inglis.gansonstreet.com/ask_inglis_chatbot/ | The student site |
| https://app.159-203-143-149.sslip.io/ask_inglis_chatbot/ | Older address for the same site, still works |
| https://api.159-203-143-149.sslip.io/ | The backend. `GET /` shows `"ready": true` when it can answer |

## What is in this repo

| Path | What it is |
|---|---|
| `backend/` | FastAPI server (Python). See `backend/README.md` |
| `frontend/` | React and Vite website. See `frontend/README.md` |
| `docker-compose.prod.yml` | How the two containers run on the droplet |
| `.env.example` | Example backend settings. Copy to `backend/.env` |
| `.github/workflows/` | The automatic deploy |
| `docs/` | Older copy of the site built for GitHub Pages. The droplet does not use it |

## How it fits together

```
Student browser
   -> droplet nginx (HTTPS, ports 80 and 443)
   -> ask_inglis_proxy container (127.0.0.1:8088)
   -> chatbot-frontend (site)  or  chatbot-backend (API)   on the Docker network "web"
```

- The two containers here publish no ports. They join the Docker network `web` with the names `chatbot-frontend` and `chatbot-backend`.
- The backend reads the lecture notes from the private notes repo, answers with Google Gemini, and writes feedback and chat logs back to the notes repo.
- The routing rules live in the `ask_inglis_proxy` repo, not here.

## Run it on your own computer

You need Python 3 and Node.js. Open two terminals.

**Backend**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example .env          # then fill in the values
uvicorn main:app
```
Wait for `Warm-up finished`. The backend is at http://localhost:8000.

**Frontend**
```bash
cd frontend
npm install
npm run dev
```
Open http://localhost:5173/ask_inglis_chatbot/ (the address needs that ending). The frontend uses http://localhost:8000 for the backend unless `VITE_BACKEND_URL` says otherwise.

To run the chatbot and the professor portal together, give them different ports. See the portal README.

## Deploying

Merging or pushing to `main` is a deploy. GitHub Actions copies the repo to the droplet and runs:

```
docker compose --env-file .env.prod -f docker-compose.prod.yml up -d --build
```

Two files live on the droplet only and are never copied by the deploy or stored in git:

- `backend/.env`: the real settings and keys
- `.env.prod`: the API address used when the frontend is built (`API_HOST`)

To change a setting, edit `backend/.env` on the droplet, then restart the backend (see below). Changing the API address needs a rebuild of the frontend.

Test changes on your own computer before merging to `main`.

## Restarting and logs (on the droplet)

```
docker ps --format '{{.Names}}' | grep -i backend     # find the chatbot backend (the portal has a backend too)
docker restart NAME_HERE
docker logs -f --tail 20 NAME_HERE                    # wait for "Warm-up finished", then Ctrl+C
```

On start, the backend reads the notes and rebuilds its search index, so `/chat` shows an error (503) for a short time. This is normal. Do not touch containers that belong to other projects on the droplet.

## Settings and secrets

All settings are listed in `backend/README.md`. Never put keys or passwords in git, in the frontend, or in chat.

| Secret | Where it lives |
|---|---|
| Gemini key | `backend/.env` on the droplet |
| GitHub App (`ask-inglis-bot`) ID and private key | `backend/.env` on the droplet |
| Admin key (`ADMIN_API_KEY`) | `backend/.env` here and in the portal's `.env` (`STUDENT_CHATBOT_ADMIN_KEY`). The two must match |

## Rules

- Never add a `ports:` section to a service here. Only the nginx in front is exposed.
- Never commit `.env` files or keys.
- Student questions are saved in the private notes repo. See the privacy notes in `backend/README.md`.

## Handover checklist

- Move the Gemini key to an account owned by new owner or the course. A paid key avoids the free limits.
- Make sure the `ask-inglis` GitHub organization has at least two owners.
- Rotate the admin key and the deploy key, and remove any starting passwords.
- Give the new owner access to all four repositories and to the droplet.

## Troubleshooting

| Problem | What to check |
|---|---|
| Site shows an error page (502) | Is the backend or frontend container running? `docker ps` on the droplet |
| Chat says the AI helper is busy | Gemini limit or an overloaded model. Check the backend log for the status code |
| A new setting has no effect | The backend only reads `.env` at start. Restart it |
| Browser says the site is blocked (CORS) | The site's address must be in `ALLOWED_ORIGINS` in `backend/.env` |
| Deploy run is red in GitHub Actions | Open the run and read the failing step |