# Ask Inglis – Student Chatbot Frontend

## Overview

Ask Inglis is an AI-powered anatomy study assistant developed for the University at Buffalo Human Anatomy Laboratory.

The frontend provides a web-based chat interface where students can ask anatomy-related questions, receive AI-generated answers, view relevant lecture sources, and provide feedback on the responses.

The interface is built with React and Vite and communicates with a Python FastAPI backend.

## Features

### 1. Interactive Chat Interface
- ChatGPT-style interface for asking anatomy questions.
- Displays student questions and AI-generated responses.
- Supports follow-up questions by sending the six most recent messages as conversation context.
- Displays loading and error messages during API requests.

### 2. Conversation History
- Stores conversations locally in the user's browser using `localStorage`.
- Allows students to create new conversations and switch between previous conversations.
- Provides an option to delete individual conversations.
- Automatically removes conversations that have not been used for seven days.

### 3. Formatted AI Responses
- Converts basic Markdown-style responses into readable text.
- Supports bold text, italic text, headings, bullet points, numbered lists, and code blocks.
- Improves readability for structured anatomy explanations.

### 4. Lecture Source Display
- Displays the names of lecture materials associated with AI responses.
- Helps students identify the course materials relevant to their questions.

### 5. Student Feedback
- Students can rate individual AI responses using thumbs-up or thumbs-down buttons.
- Thumbs-down feedback supports an optional written comment.
- Feedback is submitted to the backend through the `/feedback` endpoint.
- Once feedback has been successfully submitted for an answer, additional feedback on that answer is disabled.

### 6. User Interface Design
- React-based responsive layout.
- Sidebar for conversation navigation.
- Centered chat messages and input area.
- Anatomy-themed background image and medical-inspired styling.

## Technology Stack

- **React:** Frontend component development and state management.
- **Vite:** Local development and production builds.
- **JavaScript and CSS:** Application behavior and styling.
- **Fetch API:** Communication with the backend.
- **localStorage:** Browser-based conversation history.
- **GitHub Pages:** Static frontend hosting.

## Running the Frontend Locally

### Prerequisites

Node.js and npm must be installed.

### Installation

From the repository root, navigate to the frontend directory:

```bash
cd frontend
npm install
```

Start the development server:

```bash
npm run dev
```

The frontend will normally be accessible at:

`http://localhost:5173`

### Backend Connection

The frontend uses the `VITE_BACKEND_URL` environment variable to determine the backend address.

For local development, the default backend address is:

`http://localhost:8000`

An optional `frontend/.env.local` file can be used:

```env
VITE_BACKEND_URL=http://localhost:8000
```

The backend must be running separately for chat responses and feedback submission to work.

## Backend API Integration

The student frontend primarily communicates with two backend endpoints.

**POST /chat**

Sends a student question along with recent conversation history. The frontend displays the returned AI response and lecture source names.

**POST /feedback**

Sends student feedback, including the original question, AI response, rating, optional comment, and associated lecture sources.

Backend API implementation, lecture ingestion, and instructor review workflows are maintained separately from the student frontend.

## Conversation Storage and Retention

Conversation history is stored in each user's browser using `localStorage`. The current frontend does not require students to log in.

Each conversation includes a last-used timestamp. Conversations that have been inactive for seven days are removed from browser storage when the application is opened or reactivated.

This retention policy applies only to locally stored conversations. Feedback submitted to the backend is handled separately and is not deleted by the browser's conversation retention feature.

Because there is no account-based synchronization, conversation histories are not shared across browsers or devices.

## Build and Deployment

The frontend uses Vite to create a production build.

```bash
cd frontend
npm run build
```

The generated output is stored in `frontend/dist/`.

GitHub Pages serves the frontend from the repository's `docs/` directory. After building, copy the generated files:

```bash
cd ..
rm -rf docs/*
cp -r frontend/dist/* docs/
```

Commit and push the updated frontend and deployment files:

```bash
git add frontend docs
git commit -m "Update frontend"
git push origin main
```

For public deployment, `VITE_BACKEND_URL` must be configured with the accessible backend URL before building. Backend API keys must never be stored in frontend environment variables or committed to the repository.

## Current Scope

The student frontend focuses on anatomy Q&A, conversation management, readable AI responses, lecture source display, and student feedback collection.

Instructor-facing review tools, lecture note management, and backend AI processing are outside the scope of this frontend application.