import { useEffect, useRef, useState } from "react";
import "./App.css";

const API_URL = (
  import.meta.env.VITE_BACKEND_URL ||
  "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "askInglisConversations";
const RETENTION_DAYS = 7;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;

const makeConversation = () => ({
  id: crypto.randomUUID(),
  title: "New conversation",
  messages: [],
  lastUsedAt: Date.now(),
});

// Load conversations and remove expired ones.
function readConversations() {
  try {
    const saved = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "[]"
    );

    const now = Date.now();

    const valid = Array.isArray(saved)
      ? saved.filter(
          (item) => item && Array.isArray(item.messages)
        )
      : [];

    // Preserve old conversations without timestamps.
    const migrated = valid.map((item) => ({
      ...item,
      lastUsedAt: Number(item.lastUsedAt) || now,
    }));

    return migrated.filter(
      (item) => now - item.lastUsedAt < RETENTION_MS
    );
  } catch (err) {
    console.error("Could not read conversation history:", err);
    return [];
  }
}

// Convert Markdown-style inline formatting to React elements.
function formatInline(text) {
  const parts = String(text).split(
    /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g
  );

  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return (
        <strong key={index}>
          {part.slice(2, -2)}
        </strong>
      );
    }

    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index}>
          {part.slice(1, -1)}
        </code>
      );
    }

    if (part.startsWith("*") && part.endsWith("*")) {
      return (
        <em key={index}>
          {part.slice(1, -1)}
        </em>
      );
    }

    return <span key={index}>{part}</span>;
  });
}

// Display AI answers with paragraphs, headings, bullets and bold text.
function FormattedAnswer({ content }) {
  const lines = String(content || "")
    .replace(/\r\n/g, "\n")
    .split("\n");

  const blocks = [];

  let buffer = [];
  let listType = null;
  let listItems = [];
  let codeLines = [];
  let codeMode = false;

  const flushText = () => {
    if (buffer.length) {
      blocks.push({
        type: "paragraph",
        text: buffer.join("\n"),
      });
    }

    buffer = [];
  };

  const flushList = () => {
    if (listItems.length) {
      blocks.push({
        type: listType,
        items: listItems,
      });
    }

    listType = null;
    listItems = [];
  };

  lines.forEach((line) => {
    if (/^\s*```/.test(line)) {
      flushText();
      flushList();

      if (codeMode) {
        blocks.push({
          type: "code",
          text: codeLines.join("\n"),
        });

        codeLines = [];
      }

      codeMode = !codeMode;
      return;
    }

    if (codeMode) {
      codeLines.push(line);
      return;
    }

    if (!line.trim()) {
      flushText();
      flushList();
      return;
    }

    const heading = line.match(/^\s*#{1,4}\s+(.+)$/);

    if (heading) {
      flushText();
      flushList();

      blocks.push({
        type: "heading",
        text: heading[1],
      });

      return;
    }

    const bullet = line.match(/^\s*[-*•]\s+(.+)$/);
    const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);

    if (bullet || ordered) {
      flushText();

      const kind = bullet ? "ul" : "ol";

      if (listType && listType !== kind) {
        flushList();
      }

      listType = kind;
      listItems.push((bullet || ordered)[1]);
      return;
    }

    flushList();
    buffer.push(line);
  });

  flushText();
  flushList();

  if (codeLines.length) {
    blocks.push({
      type: "code",
      text: codeLines.join("\n"),
    });
  }

  return (
    <div className="formatted-answer">
      {blocks.map((block, i) => {
        if (block.type === "heading") {
          return (
            <h3 key={i}>
              {formatInline(block.text)}
            </h3>
          );
        }

        if (block.type === "code") {
          return (
            <pre key={i}>
              <code>{block.text}</code>
            </pre>
          );
        }

        if (block.type === "ul") {
          return (
            <ul key={i}>
              {block.items.map((item, j) => (
                <li key={j}>{formatInline(item)}</li>
              ))}
            </ul>
          );
        }

        if (block.type === "ol") {
          return (
            <ol key={i}>
              {block.items.map((item, j) => (
                <li key={j}>{formatInline(item)}</li>
              ))}
            </ol>
          );
        }

        return (
          <p key={i}>
            {block.text.split("\n").map((line, j) => (
              <span key={j}>
                {j > 0 && <br />}
                {formatInline(line)}
              </span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

export default function App() {
  const [conversations, setConversations] = useState(() => {
    const previous = readConversations();

    return previous.length
      ? previous
      : [makeConversation()];
  });

  const [activeId, setActiveId] = useState(null);
  const [question, setQuestion] = useState("");
  const [loadingId, setLoadingId] = useState(null);
  const [error, setError] = useState("");

  const [feedbackTarget, setFeedbackTarget] = useState(null);
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackPendingId, setFeedbackPendingId] = useState(null);

  const messagesEndRef = useRef(null);

  const current =
    conversations.find((item) => item.id === activeId) ||
    conversations[0];

  const messages = current?.messages || [];

  // Save conversations in browser storage.
  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify(conversations)
      );
    } catch (err) {
      console.error("Cannot save history:", err);
    }
  }, [conversations]);

  // Ensure an active conversation exists.
  useEffect(() => {
    if (!conversations.some((item) => item.id === activeId)) {
      setActiveId(conversations[0]?.id || null);
    }
  }, [conversations, activeId]);

  // Remove conversations inactive for 7 days.
  useEffect(() => {
    const prune = () => {
      const now = Date.now();

      setConversations((old) => {
        const retained = old.filter(
          (item) => now - item.lastUsedAt < RETENTION_MS
        );

        if (retained.length === old.length) {
          return old;
        }

        return retained.length
          ? retained
          : [makeConversation()];
      });
    };

    const onVisibility = () => {
      if (!document.hidden) {
        prune();
      }
    };

    window.addEventListener("focus", prune);

    document.addEventListener(
      "visibilitychange",
      onVisibility
    );

    return () => {
      window.removeEventListener("focus", prune);

      document.removeEventListener(
        "visibilitychange",
        onVisibility
      );
    };
  }, []);

  // Scroll to the newest message.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages.length, loadingId, activeId]);

  const selectConversation = (id) => {
    setActiveId(id);
    setError("");
    setQuestion("");

    setConversations((old) =>
      old.map((item) =>
        item.id === id
          ? { ...item, lastUsedAt: Date.now() }
          : item
      )
    );
  };

  const newConversation = () => {
    const newChat = makeConversation();

    setConversations((old) => [newChat, ...old]);
    setActiveId(newChat.id);
    setQuestion("");
    setError("");
  };

  const deleteConversation = (id) => {
    if (
      !window.confirm(
        "Delete this conversation? This cannot be undone."
      )
    ) {
      return;
    }

    setConversations((old) => {
      const remaining = old.filter(
        (item) => item.id !== id
      );

      return remaining.length
        ? remaining
        : [makeConversation()];
    });

    setError("");
  };

  const updateMessages = (id, fn) => {
    setConversations((old) =>
      old.map((item) =>
        item.id !== id
          ? item
          : {
              ...item,
              messages: fn(item.messages),
              lastUsedAt: Date.now(),
            }
      )
    );
  };

  // Send a question to FastAPI.
  const ask = async () => {
    const text = question.trim();

    if (!text || !current || loadingId) {
      return;
    }

    const id = current.id;
    const previous = [...current.messages];

    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };

    // Send only the last 6 previous messages.
    const history = previous
      .slice(-6)
      .map(({ role, content }) => ({
        role,
        content,
      }));

    updateMessages(id, (old) => [
      ...old,
      userMessage,
    ]);

    setConversations((old) =>
      old.map((item) =>
        item.id === id && item.messages.length === 0
          ? {
              ...item,
              title: text.slice(0, 36),
            }
          : item
      )
    );

    setQuestion("");
    setError("");
    setLoadingId(id);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: text,
          history,
        }),
      });

      if (!response.ok) {
        throw new Error(
          `Chat request failed (${response.status})`
        );
      }

      const data = await response.json();

      updateMessages(id, (old) => [
        ...old,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: data.reply || "No answer returned.",
          question: text,
          lectures: data.lectures || [],
          feedback: null,
          images: data.images || [],
          degraded: Boolean(data.degraded),
        },
      ]);
    } catch (err) {
      console.error(err);

      setError(
        "Could not get an answer. Please check the connection and try again."
      );
    } finally {
      setLoadingId(null);
    }
  };

  // Submit student feedback.
  const submitFeedback = async (
    message,
    rating,
    comment = ""
  ) => {
    if (message.feedback || feedbackPendingId) {
      return;
    }

    setFeedbackPendingId(message.id);

    try {
      const response = await fetch(
        `${API_URL}/feedback`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            question: message.question || "",
            answer: message.content,
            rating,
            comment,
            lectures: message.lectures || [],
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          `Feedback failed (${response.status})`
        );
      }

      setConversations((old) =>
        old.map((conv) => ({
          ...conv,
          messages: conv.messages.map((msg) =>
            msg.id === message.id
              ? { ...msg, feedback: rating }
              : msg
          ),
        }))
      );

      setFeedbackTarget(null);
      setFeedbackComment("");
    } catch (err) {
      console.error(err);

      window.alert(
        "Feedback could not be submitted."
      );
    } finally {
      setFeedbackPendingId(null);
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-symbol">✦</span>
          <span>Ask Inglis</span>
        </div>

        <button
          className="new-chat"
          type="button"
          onClick={newConversation}
        >
          ＋ New conversation
        </button>

        <div className="sidebar-label">
          Recent conversations
        </div>

        <div className="conversation-list">
          {conversations.map((item) => (
            <div
              className={`conversation-row ${
                item.id === current?.id ? "active" : ""
              }`}
              key={item.id}
            >
              <button
                className="conversation-select"
                type="button"
                onClick={() =>
                  selectConversation(item.id)
                }
                title={item.title}
              >
                {item.title}
              </button>

              <button
                className="delete-chat"
                type="button"
                title="Delete conversation"
                aria-label={`Delete ${item.title}`}
                onClick={() =>
                  deleteConversation(item.id)
                }
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <p className="retention-note">
          Inactive conversations are removed from this
          browser after {RETENTION_DAYS} days.
        </p>
      </aside>

      <main
        className="chat-panel"
        style={{
          backgroundImage: `linear-gradient(
            90deg,
            rgba(250,252,253,.88),
            rgba(250,252,253,.92)
          ), url("${import.meta.env.BASE_URL}anatomy-background.webp")`,
        }}
      >
        <header className="topbar">
          <strong>Anatomy AI Study Assistant</strong>
          <span>Grounded in course notes</span>
        </header>

        <section className="messages" aria-live="polite">
          {messages.length === 0 && (
            <div className="welcome">
              <div className="welcome-emblem">
                ✦
              </div>

              <h1>What would you like to learn?</h1>

              <p>
                Ask a question about human anatomy or
                your lecture notes.
              </p>
            </div>
          )}

          {messages.map((message) => (
            <div
              className="message-row"
              key={message.id}
            >
              <article
                className={`message ${
                  message.role === "user"
                    ? "user-message"
                    : "assistant-message"
                }`}
              >
                <div className="message-author">
                  {message.role === "user"
                    ? "You"
                    : "Ask Inglis"}
                </div>

                {message.role === "assistant" ? (
                  <FormattedAnswer
                    content={message.content}
                  />
                ) : (
                  <div className="user-text">
                    {message.content}
                  </div>
                )}

                {message.role === "assistant" && (
                  <>
                    {message.degraded && (
                      <p className="notice">
                        The assistant is temporarily
                        using retrieved note excerpts.
                      </p>
                    )}

                    {message.lectures?.length > 0 && (
                      <div className="sources">
                        <b>Lecture sources</b>

                        <div>
                          {message.lectures.map(
                            (lecture, i) => (
                              <span
                                className="source-chip"
                                key={`${lecture}-${i}`}
                              >
                                {lecture}
                              </span>
                            )
                          )}
                        </div>
                      </div>
                    )}

                    <div className="feedback-actions">
                      <button
                        type="button"
                        title="Helpful"
                        aria-label="Helpful"
                        disabled={
                          Boolean(message.feedback) ||
                          Boolean(feedbackPendingId)
                        }
                        onClick={() =>
                          submitFeedback(message, "up")
                        }
                      >
                        👍
                      </button>

                      <button
                        type="button"
                        title="Not helpful"
                        aria-label="Not helpful"
                        disabled={
                          Boolean(message.feedback) ||
                          Boolean(feedbackPendingId)
                        }
                        onClick={() => {
                          setFeedbackTarget(message);
                          setFeedbackComment("");
                        }}
                      >
                        👎
                      </button>

                      {message.feedback && (
                        <span>Feedback submitted</span>
                      )}
                    </div>
                  </>
                )}
              </article>
            </div>
          ))}

          {loadingId === current?.id && (
            <div className="message-row">
              <article className="message assistant-message">
                <div className="message-author">
                  Ask Inglis
                </div>

                <span className="thinking">
                  Thinking…
                </span>
              </article>
            </div>
          )}

          {error && (
            <div className="chat-error" role="alert">
              {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </section>

        <div className="composer-area">
          <div className="composer">
            <textarea
              placeholder="Ask an anatomy question..."
              aria-label="Ask an anatomy question"
              value={question}
              maxLength={2000}
              onChange={(e) =>
                setQuestion(e.target.value)
              }
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  ask();
                }
              }}
              disabled={Boolean(loadingId)}
              rows={2}
            />

            <button
              type="button"
              className="send-button"
              disabled={
                !question.trim() ||
                Boolean(loadingId)
              }
              onClick={ask}
            >
              Send
            </button>
          </div>

          <small>
            Enter to send · Shift + Enter for a new line
          </small>
        </div>
      </main>

      {feedbackTarget && (
        <div className="modal-overlay">
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="feedback-title"
          >
            <h2 id="feedback-title">
              What went wrong?
            </h2>

            <p>
              Your feedback helps the instructor
              review answers.
            </p>

            <textarea
              rows={4}
              maxLength={1000}
              autoFocus
              placeholder="Incorrect information, missing details, wrong lecture source…"
              value={feedbackComment}
              onChange={(e) =>
                setFeedbackComment(e.target.value)
              }
            />

            <div className="modal-actions">
              <button
                type="button"
                disabled={Boolean(feedbackPendingId)}
                onClick={() =>
                  setFeedbackTarget(null)
                }
              >
                Cancel
              </button>

              <button
                className="primary"
                type="button"
                disabled={Boolean(feedbackPendingId)}
                onClick={() =>
                  submitFeedback(
                    feedbackTarget,
                    "down",
                    feedbackComment
                  )
                }
              >
                {feedbackPendingId
                  ? "Submitting…"
                  : "Submit feedback"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}