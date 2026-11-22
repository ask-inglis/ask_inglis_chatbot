# AskInglis student chatbot: frontend

The website students use. It is a React app built with Vite. It talks to the AskInglis backend (`../backend`) and shows answers, lecture sources, practice questions and feedback buttons.

## What students can do

- **Ask and Quiz:** ask an anatomy question, or type something like "quiz me on the brachium" to get practice questions. Quizzes use the same `/chat` request as questions.
- **Guided mode:** ask follow-up questions step by step.
- **Diagrams mode:** look up approved images.
- **Lab bench side panel:** a checklist and a station timer for use during lab.
- **Lecture sources:** each answer shows the lectures it came from. A badge shows which lectures the instructor has reviewed.
- **Translation:** show an answer in another language (for example Spanish, Chinese, Korean, Arabic). The English answer stays on screen. Tapping a bold term shows a short glossary entry in the chosen language. Answers can be read aloud with the browser's own voice.
- **Feedback:** thumbs-up or thumbs-down on each answer, with an optional comment for thumbs-down.
- **Conversation history:** saved in the student's own browser, with a sidebar to switch or delete conversations.

## How it talks to the backend

| Request | Used for |
|---|---|
| `POST /chat` | Questions and quizzes. Sends the six most recent messages as context |
| `POST /title` | A short title for the conversation |
| `POST /translate`, `POST /gloss` | Translating an answer and explaining one term |
| `POST /feedback`, `POST /feedback/dispute` | Thumbs and "this answer is wrong" |
| `GET /images`, `GET /reviewed` | Approved images and reviewed-lecture badges |

The backend address comes from `VITE_BACKEND_URL`. It is fixed when the site is **built**, so changing it needs a new build. Never put keys or passwords in the frontend: everything in it is visible to students.

## Run it on your own computer

You need Node.js and npm. The backend must also be running (see `../backend/README.md`).

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173/ask_inglis_chatbot/. The address needs the `/ask_inglis_chatbot/` ending, because the site is built to live under that path (`base` in `vite.config.js`).

By default it uses the backend at `http://localhost:8000`. To use another one, create `frontend/.env.local`:

```env
VITE_BACKEND_URL=http://localhost:8000
```

## Build and deploy

You normally do not build by hand. The droplet builds the site in Docker when `main` is pushed (see the repo's main README).

- The `Dockerfile` builds the site with `npm run build`, using the `VITE_BACKEND_URL` that `docker-compose.prod.yml` passes in, and serves the result with nginx on port 8080 inside the container.
- The site is then reached through the `ask_inglis_proxy` nginx at https://inglis.gansonstreet.com/ask_inglis_chatbot/.

To check a production build on your own computer:

```bash
npm run build      # output goes to frontend/dist/
npm run preview
```

The older `docs/` folder at the repo root is a copy of the site built for GitHub Pages. The droplet does not use it.

## Conversation storage and privacy

- Conversations are saved in each student's browser with `localStorage`. Students do not log in, and histories are not shared between browsers or devices.
- Items not used for 30 days are removed when the site is opened.
- Feedback and questions sent to the backend are handled by the backend. The backend keeps a private log of questions without names or IP addresses (see `../backend/README.md`).

## Project files

| Path | What it is |
|---|---|
| `src/App.jsx` | Almost all the screens and logic |
| `src/App.css`, `src/index.css` | Styles |
| `src/main.jsx` | Starts the app |
| `vite.config.js` | Build settings, including the `/ask_inglis_chatbot/` base path |
| `public/` | Images and icons served as they are |
| `Dockerfile` | Builds and serves the site on the droplet |