import { useEffect, useState } from "react";
import "./ChatWithInglis.css";

// Student-side preview only. This deliberately makes NO network requests.
// A secure server-side messaging API and professor portal integration must be
// completed before any message can actually be delivered to an instructor.
const DRAFTS_KEY = "askInglisProfessorMessageDraftsV1";
const MAX_SUBJECT = 100;
const MAX_MESSAGE = 2000;

function readDrafts() {
  try {
    const value = JSON.parse(localStorage.getItem(DRAFTS_KEY) || "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((thread) =>
      thread && typeof thread.id === "string" &&
      typeof thread.subject === "string" &&
      Array.isArray(thread.messages)
    );
  } catch (error) {
    console.warn("Could not load professor message drafts", error);
    return [];
  }
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "Saved locally";
  return date.toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

export default function ChatWithInglis() {
  const [threads, setThreads] = useState(readDrafts);
  const [selectedId, setSelectedId] = useState(null);
  const [subject, setSubject] = useState("");
  const [message, setMessage] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const selectedThread = threads.find((item) => item.id === selectedId) || null;
  const sortedThreads = [...threads].sort(
    (a, b) => (b.updatedAt || 0) - (a.updatedAt || 0)
  );

  useEffect(() => {
    try {
      localStorage.setItem(DRAFTS_KEY, JSON.stringify(threads));
    } catch (err) {
      console.error("Could not save professor message drafts:", err);
      setError("Your browser could not save this draft. Check available storage.");
    }
  }, [threads]);

  const newTopic = () => {
    setSelectedId(null);
    setSubject("");
    setMessage("");
    setNotice("");
    setError("");
  };

  const openTopic = (id) => {
    setSelectedId(id);
    setMessage("");
    setNotice("");
    setError("");
  };

  const saveDraft = () => {
    const body = message.trim();
    const topic = selectedThread?.subject || subject.trim();
    if (!topic) {
      setError("Please enter a subject for this conversation.");
      return;
    }
    if (!body) {
      setError("Please write a message before saving.");
      return;
    }

    const now = Date.now();
    const draft = {
      id: crypto.randomUUID(),
      author: "student",
      body,
      createdAt: now,
      status: "local-draft",
    };

    if (selectedThread) {
      setThreads((old) => old.map((item) => item.id === selectedThread.id
        ? {
            ...item,
            messages: [...item.messages, draft],
            updatedAt: now,
          }
        : item
      ));
    } else {
      const id = crypto.randomUUID();
      const thread = {
        id,
        subject: topic,
        createdAt: now,
        updatedAt: now,
        messages: [draft],
      };
      setThreads((old) => [thread, ...old]);
      setSelectedId(id);
      setSubject("");
    }
    setMessage("");
    setError("");
    setNotice("Draft saved in this browser. It has NOT been sent to the professor.");
  };

  const deleteDraft = (messageId) => {
    if (!selectedThread) return;
    if (!window.confirm("Delete this local draft message?")) return;
    setThreads((old) => old.map((item) => item.id === selectedThread.id
      ? {
          ...item,
          messages: item.messages.filter((msg) => msg.id !== messageId),
          updatedAt: Date.now(),
        }
      : item
    ));
    setNotice("Local draft deleted.");
    setError("");
  };

  const deleteThread = () => {
    if (!selectedThread) return;
    if (!window.confirm("Delete this entire local conversation draft?")) return;
    setThreads((old) => old.filter((item) => item.id !== selectedThread.id));
    newTopic();
  };

  return (
    <div className="inglis-workspace">
      <div className="inglis-intro">
        <div className="inglis-kicker">DIRECT PROFESSOR MESSAGING</div>
        <h1>Chat with Inglis</h1>
        <p>
          Prepare a question for your professor, without a student login.
          Professor replies will appear here after the messaging service is connected.
        </p>
      </div>

      <div className="inglis-preview-warning" role="status">
        <span aria-hidden="true">ⓘ</span>
        <div>
          <strong>Frontend preview — not connected</strong>
          <span>
            Messages saved here are local drafts only. They are not delivered to a professor.
            Please do not include personal or sensitive information.
          </span>
        </div>
      </div>

      <div className="inglis-layout">
        <nav className="inglis-topics" aria-label="Professor message drafts">
          <div className="inglis-topics-head">
            <strong>My questions</strong>
            <button type="button" onClick={newTopic}>+ New</button>
          </div>
          {sortedThreads.length === 0 ? (
            <p className="inglis-no-topics">No saved drafts yet. Start by writing a question.</p>
          ) : (
            <div className="inglis-topic-list">
              {sortedThreads.map((thread) => (
                <button
                  type="button"
                  key={thread.id}
                  className={`inglis-topic ${selectedId === thread.id ? "selected" : ""}`}
                  aria-current={selectedId === thread.id ? "page" : undefined}
                  onClick={() => openTopic(thread.id)}
                >
                  <strong>{thread.subject}</strong>
                  <span>{thread.messages.length} draft{thread.messages.length !== 1 ? "s" : ""} · {formatDate(thread.updatedAt)}</span>
                </button>
              ))}
            </div>
          )}
        </nav>

        <section className="inglis-thread" aria-label="Professor message editor">
          <div className="inglis-thread-head">
            <div>
              <div className="inglis-thread-eyebrow">ANONYMOUS QUESTION</div>
              <h2>{selectedThread?.subject || "New question"}</h2>
              <p>{selectedThread ? "Draft conversation" : "Write a question for Professor Inglis"}</p>
            </div>
            {selectedThread && (
              <button className="inglis-delete-thread" type="button" onClick={deleteThread}>
                Delete drafts
              </button>
            )}
          </div>

          <div className="inglis-thread-body">
            {selectedThread?.messages.length ? (
              <div className="inglis-drafts">
                {selectedThread.messages.map((item) => (
                  <article className="inglis-draft-bubble" key={item.id}>
                    <div className="inglis-draft-top">
                      <strong>Anonymous Student</strong>
                      <span>{formatDate(item.createdAt)}</span>
                    </div>
                    <p>{item.body}</p>
                    <div className="inglis-draft-bottom">
                      <span>Saved locally · Not sent</span>
                      <button type="button" onClick={() => deleteDraft(item.id)}>Delete draft</button>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="inglis-empty-state">
                <span className="inglis-empty-icon" aria-hidden="true">✉</span>
                <strong>Ask your professor a question</strong>
                <p>
                  Your message can be about lecture notes, anatomy concepts, or something
                  you would like clarified in class.
                </p>
              </div>
            )}
          </div>

          <div className="inglis-compose">
            {!selectedThread && (
              <label htmlFor="inglis-subject">
                Subject
                <input
                  id="inglis-subject"
                  value={subject}
                  maxLength={MAX_SUBJECT}
                  onChange={(event) => { setSubject(event.target.value); setError(""); }}
                  placeholder="e.g., Question about brachial plexus"
                />
              </label>
            )}
            <label htmlFor="inglis-message">
              Message
              <textarea
                id="inglis-message"
                value={message}
                maxLength={MAX_MESSAGE}
                rows={4}
                onChange={(event) => { setMessage(event.target.value); setError(""); setNotice(""); }}
                placeholder="What would you like to ask Professor Inglis?"
              />
            </label>
            <div className="inglis-compose-footer">
              <span>{message.length}/{MAX_MESSAGE} characters</span>
              <div className="inglis-compose-buttons">
                <button type="button" className="inglis-save" onClick={saveDraft} disabled={!message.trim()}>
                  Save draft
                </button>
                <button
                  type="button"
                  className="inglis-send-disabled"
                  disabled
                  title="Available after the professor messaging API is connected"
                >
                  Send to Professor (coming soon)
                </button>
              </div>
            </div>
            {error && <p className="inglis-feedback inglis-error" role="alert">{error}</p>}
            {notice && <p className="inglis-feedback inglis-success" role="status">{notice}</p>}
            <p className="inglis-privacy-note">
              This preview saves drafts in your browser. Anyone using the same browser profile may see them.
              No message is sent to the professor until the server-side feature is implemented.
            </p>
          </div>
        </section>
      </div>
    </div>
  );
}
