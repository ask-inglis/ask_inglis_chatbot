import { useEffect, useRef, useState } from "react";
import "./App.css";

const API_URL = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "askInglisConversations";
const RETENTION_DAYS = 30;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;
const CHECK_INTERVAL_MS = 60 * 1000;

const makeConversation = () => ({
  id: crypto.randomUUID(),
  title: "New conversation",
  messages: [],
  lastUsedAt: Date.now(),
});

function lastActivityTime(conversation, now = Date.now()) {
  const saved = Number(conversation.lastUsedAt);
  if (Number.isFinite(saved) && saved > 1577836800000 && saved <= now) {
    return saved;
  }

  // Older versions used Date.now() as the conversation ID.
  // Recover the creation date for legacy conversations without timestamps.
  const legacyId = String(conversation.id ?? "");
  if (/^\d{13}$/.test(legacyId)) {
    const created = Number(legacyId);
    if (created > 1577836800000 && created <= now) return created;
  }

  // For UUID conversations without a date, the original age is unknown.
  // Give them a timestamp once instead of deleting unknown-age user data.
  return now;
}

function pruneConversations(items, now = Date.now()) {
  return items.filter((conversation) => {
    const timestamp = Number(conversation.lastUsedAt);
    return Number.isFinite(timestamp) &&
      timestamp > 0 &&
      timestamp <= now &&
      now - timestamp < RETENTION_MS;
  });
}

function readConversations() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
    if (!Array.isArray(saved)) return [];
    const now = Date.now();

    const migrated = saved
      .filter((item) => item && Array.isArray(item.messages))
      .map((item) => ({
        ...item,
        lastUsedAt: lastActivityTime(item, now),
      }));

    return pruneConversations(migrated, now);
  } catch (error) {
    console.error("Failed to load conversation history:", error);
    return [];
  }
}

function formatInline(text) {
  return String(text)
    .split(/(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g)
    .map((part, i) => {
      if (part.startsWith("**") && part.endsWith("**")) {
        return <strong key={i}>{part.slice(2, -2)}</strong>;
      }
      if (part.startsWith("`") && part.endsWith("`")) {
        return <code key={i}>{part.slice(1, -1)}</code>;
      }
      if (part.startsWith("*") && part.endsWith("*")) {
        return <em key={i}>{part.slice(1, -1)}</em>;
      }
      return <span key={i}>{part}</span>;
    });
}

function FormattedAnswer({ content }) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let listItems = [];
  let listKind = null;
  let codeLines = [];
  let inCode = false;

  const finishParagraph = () => {
    if (paragraph.length) blocks.push({ type: "p", text: paragraph.join("\n") });
    paragraph = [];
  };
  const finishList = () => {
    if (listItems.length) blocks.push({ type: listKind, items: listItems });
    listItems = [];
    listKind = null;
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      finishParagraph();
      finishList();
      if (inCode) {
        blocks.push({ type: "code", text: codeLines.join("\n") });
        codeLines = [];
      }
      inCode = !inCode;
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    if (!line.trim()) {
      finishParagraph();
      finishList();
      continue;
    }
    const heading = line.match(/^\s*#{1,4}\s+(.+)$/);
    if (heading) {
      finishParagraph();
      finishList();
      blocks.push({ type: "h", text: heading[1] });
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.+)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (bullet || numbered) {
      finishParagraph();
      const kind = bullet ? "ul" : "ol";
      if (listKind && listKind !== kind) finishList();
      listKind = kind;
      listItems.push((bullet || numbered)[1]);
      continue;
    }
    finishList();
    paragraph.push(line);
  }
  finishParagraph();
  finishList();
  if (codeLines.length) blocks.push({ type: "code", text: codeLines.join("\n") });

  return (
    <div className="formatted-answer">
      {blocks.map((block, i) => {
        if (block.type === "h") return <h3 key={i}>{formatInline(block.text)}</h3>;
        if (block.type === "code") {
          return <pre key={i}><code>{block.text}</code></pre>;
        }
        if (block.type === "ul" || block.type === "ol") {
          const Tag = block.type;
          return (
            <Tag key={i}>
              {block.items.map((item, j) => <li key={j}>{formatInline(item)}</li>)}
            </Tag>
          );
        }
        return (
          <p key={i}>
            {block.text.split("\n").map((line, j) => (
              <span key={j}>{j > 0 && <br />}{formatInline(line)}</span>
            ))}
          </p>
        );
      })}
    </div>
  );
}

export default function App() {
  const [conversations, setConversations] = useState(() => {
    const existing = readConversations();
    return existing.length ? existing : [makeConversation()];
  });
  const [activeId, setActiveId] = useState(null);
  const [question, setQuestion] = useState("");
  const [loadingId, setLoadingId] = useState(null);
  const [error, setError] = useState("");
  const [feedbackTarget, setFeedbackTarget] = useState(null);
  const [feedbackComment, setFeedbackComment] = useState("");
  const [feedbackPendingId, setFeedbackPendingId] = useState(null);
  const messagesEndRef = useRef(null);

  const current = conversations.find((item) => item.id === activeId)
    || conversations[0];
  const messages = current?.messages || [];

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
    } catch (error) {
      console.error("Failed to save conversation history:", error);
    }
  }, [conversations]);

  useEffect(() => {
    if (!conversations.some((item) => item.id === activeId)) {
      setActiveId(conversations[0]?.id || null);
    }
  }, [conversations, activeId]);

  // Check expiration on a timer, tab focus, and returning to the page.
  useEffect(() => {
    const prune = () => {
      const now = Date.now();
      setConversations((old) => {
        const valid = pruneConversations(old, now);
        if (valid.length === old.length) return old;
        return valid.length ? valid : [makeConversation()];
      });
    };
    const onVisibility = () => { if (!document.hidden) prune(); };
    prune();
    const timer = window.setInterval(prune, CHECK_INTERVAL_MS);
    window.addEventListener("focus", prune);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", prune);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, loadingId, activeId]);

  const selectConversation = (id) => {
    // Viewing an old chat does not reset the seven-day countdown.
    setActiveId(id);
    setQuestion("");
    setError("");
  };

  const newConversation = () => {
    const item = makeConversation();
    setConversations((old) => [item, ...old]);
    setActiveId(item.id);
    setQuestion("");
    setError("");
  };

  const deleteConversation = (id) => {
    if (!window.confirm("Delete this conversation? This cannot be undone.")) return;
    setConversations((old) => {
      const remaining = old.filter((item) => item.id !== id);
      return remaining.length ? remaining : [makeConversation()];
    });
    setError("");
  };

  const updateMessages = (id, update) => {
    setConversations((old) => old.map((item) => {
      if (item.id !== id) return item;
      const nextMessages = update(item.messages);
      const firstQuestion = nextMessages.find((msg) => msg.role === "user")?.content;
      return {
        ...item,
        messages: nextMessages,
        title: item.title === "New conversation" && firstQuestion
          ? firstQuestion.slice(0, 36) : item.title,
        lastUsedAt: Date.now(),
      };
    }));
  };

  const generateTitle = async (conversationId, firstQuestion) => {
    const fallbackTitle = firstQuestion.slice(0, 36);
    try {
      const response = await fetch(`${API_URL}/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: firstQuestion }),
      });
      if (!response.ok) throw new Error(`Title request failed (${response.status})`);
      const data = await response.json();
      const title = typeof data.title === "string"
        ? data.title.trim().slice(0, 60) : "";
      if (!title) return;
      setConversations((old) => old.map((item) =>
        item.id === conversationId &&
        item.messages[0]?.content === firstQuestion &&
        (item.title === fallbackTitle || item.title === "New conversation")
          ? { ...item, title } : item
      ));
    } catch (error) {
      console.warn("AI title unavailable; using the question instead:", error);
    }
  };

  const ask = async () => {
    const text = question.trim();
    if (!text || !current || loadingId) return;
    const conversationId = current.id;
    const previous = [...current.messages];
    const history = previous.slice(-6).map(({ role, content }) => ({ role, content }));
    updateMessages(conversationId, (old) => [
      ...old,
      { id: crypto.randomUUID(), role: "user", content: text },
    ]);
    if (previous.length === 0) void generateTitle(conversationId, text);
    setQuestion("");
    setError("");
    setLoadingId(conversationId);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history }),
      });
      if (!response.ok) throw new Error(`Chat request failed (${response.status})`);
      const data = await response.json();
      updateMessages(conversationId, (old) => [
        ...old,
        {
          id: crypto.randomUUID(), role: "assistant",
          content: data.reply || "No answer returned.", question: text,
          lectures: data.lectures || [], images: data.images || [],
          degraded: Boolean(data.degraded), feedback: null,
        },
      ]);
    } catch (err) {
      console.error(err);
      setError("Could not get an answer. Please check the connection and try again.");
    } finally {
      setLoadingId(null);
    }
  };

  const submitFeedback = async (message, rating, comment = "") => {
    if (message.feedback || feedbackPendingId) return;
    setFeedbackPendingId(message.id);
    try {
      const response = await fetch(`${API_URL}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: message.question || "", answer: message.content,
          rating, comment, lectures: message.lectures || [],
        }),
      });
      if (!response.ok) throw new Error(`Feedback failed (${response.status})`);
      setConversations((old) => old.map((conv) => ({
        ...conv,
        messages: conv.messages.map((msg) =>
          msg.id === message.id ? { ...msg, feedback: rating } : msg
        ),
      })));
      setFeedbackTarget(null);
      setFeedbackComment("");
    } catch (err) {
      console.error(err);
      window.alert("Feedback could not be submitted.");
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
        <button className="new-chat" type="button" onClick={newConversation}>
          ＋ New conversation
        </button>
        <div className="sidebar-label">Recent conversations</div>
        <div className="conversation-list">
          {conversations.map((item) => (
            <div className={`conversation-row ${item.id === current?.id ? "active" : ""}`} key={item.id}>
              <button className="conversation-select" type="button"
                onClick={() => selectConversation(item.id)} title={item.title}>
                {item.title}
              </button>
              <button className="delete-chat" type="button" title="Delete conversation"
                aria-label={`Delete ${item.title}`} onClick={() => deleteConversation(item.id)}>
                ×
              </button>
            </div>
          ))}
        </div>
        <p className="retention-note">
          Conversations without new messages are removed from this browser after {RETENTION_DAYS} days.
        </p>
      </aside>

      <main className="chat-panel" style={{
        backgroundImage: `linear-gradient(90deg, rgba(250,252,253,.88), rgba(250,252,253,.92)), url("${import.meta.env.BASE_URL}anatomy-background.webp")`,
      }}>
        <header className="topbar">
          <strong>Anatomy AI Study Assistant</strong>
          <span>Grounded in course notes</span>
        </header>
        <section className="messages" aria-live="polite">
          {messages.length === 0 && (
            <div className="welcome">
              <div className="welcome-emblem">✦</div>
              <h1>What would you like to learn?</h1>
              <p>Ask a question about human anatomy or your lecture notes.</p>
            </div>
          )}
          {messages.map((message) => (
            <div className="message-row" key={message.id}>
              <article className={`message ${message.role === "user" ? "user-message" : "assistant-message"}`}>
                <div className="message-author">
                  {message.role === "user" ? "You" : "Ask Inglis"}
                </div>
                {message.role === "assistant"
                  ? <FormattedAnswer content={message.content} />
                  : <div className="user-text">{message.content}</div>}
                {message.role === "assistant" && (
                  <>
                    {message.degraded && (
                      <p className="notice">The assistant is temporarily using retrieved note excerpts.</p>
                    )}
                    {message.lectures?.length > 0 && (
                      <div className="sources">
                        <b>Lecture sources</b>
                        <div>
                          {message.lectures.map((lecture, i) => (
                            <span className="source-chip" key={`${lecture}-${i}`}>{lecture}</span>
                          ))}
                        </div>
                      </div>
                    )}
                    <div className="feedback-actions">
                      <button type="button" title="Helpful" aria-label="Helpful"
                        disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                        onClick={() => submitFeedback(message, "up")}>👍</button>
                      <button type="button" title="Not helpful" aria-label="Not helpful"
                        disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                        onClick={() => { setFeedbackTarget(message); setFeedbackComment(""); }}>👎</button>
                      {message.feedback && <span>Feedback submitted</span>}
                    </div>
                  </>
                )}
              </article>
            </div>
          ))}
          {loadingId === current?.id && (
            <div className="message-row">
              <article className="message assistant-message">
                <div className="message-author">Ask Inglis</div>
                <span className="thinking">Thinking…</span>
              </article>
            </div>
          )}
          {error && <div className="chat-error" role="alert">{error}</div>}
          <div ref={messagesEndRef} />
        </section>
        <div className="composer-area">
          <div className="composer">
            <textarea placeholder="Ask an anatomy question..."
              aria-label="Ask an anatomy question" value={question} maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault(); ask();
                }
              }}
              disabled={Boolean(loadingId)} rows={2} />
            <button type="button" className="send-button"
              disabled={!question.trim() || Boolean(loadingId)} onClick={ask}>Send</button>
          </div>
          <small>Enter to send · Shift + Enter for a new line</small>
        </div>
      </main>

      {feedbackTarget && (
        <div className="modal-overlay" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="feedback-title">
            <h2 id="feedback-title">What went wrong?</h2>
            <p>Your feedback helps the instructor review answers.</p>
            <textarea rows={4} maxLength={1000} autoFocus
              placeholder="Incorrect information, missing details, wrong lecture source…"
              value={feedbackComment} onChange={(event) => setFeedbackComment(event.target.value)} />
            <div className="modal-actions">
              <button type="button" onClick={() => setFeedbackTarget(null)}
                disabled={Boolean(feedbackPendingId)}>Cancel</button>
              <button className="primary" type="button" disabled={Boolean(feedbackPendingId)}
                onClick={() => submitFeedback(feedbackTarget, "down", feedbackComment)}>
                {feedbackPendingId ? "Submitting…" : "Submit feedback"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
