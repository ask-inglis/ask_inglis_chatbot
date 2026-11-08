import { useEffect, useRef, useState } from "react";
import "./App.css";
import "./GuidedStudy.css";

const API_URL = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "askInglisConversations";
const RETENTION_DAYS = 30;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;

const REGIONS = [
  "Head & Neck",
  "Thorax",
  "Abdomen",
  "Pelvis",
  "Upper Limb",
  "Lower Limb",
  "Back",
];

const TOPICS = [
  "Muscles & Functions",
  "Nerves & Blood Supply",
  "Dissection Landmarks",
  "Anatomical Relationships",
];

const SPECIMENS = [
  "Typical",
  "Thin",
  "Muscular",
  "Obese",
  "Elderly",
];

function newChat() {
  return {
    id: crypto.randomUUID(),
    title: "New conversation",
    messages: [],
    mode: null,
    studyContext: null,
    lastUsedAt: Date.now(),
  };
}

function readChats() {
  try {
    const saved = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "[]"
    );

    const now = Date.now();

    if (!Array.isArray(saved)) return [];

    return saved
      .filter(
        (chat) => chat && Array.isArray(chat.messages)
      )
      .map((chat) => {
        const timestamp = Number(chat.lastUsedAt);
        const oldId = String(chat.id ?? "");

        const legacyTimestamp = /^\d{13}$/.test(oldId)
          ? Number(oldId)
          : now;

        const lastUsedAt =
          Number.isFinite(timestamp) &&
          timestamp > 1577836800000 &&
          timestamp <= now
            ? timestamp
            : legacyTimestamp;

        return { ...chat, lastUsedAt };
      })
      .filter(
        (chat) => now - chat.lastUsedAt < RETENTION_MS
      );
  } catch (error) {
    console.error(
      "Could not read chat history:",
      error
    );

    return [];
  }
}

function formatInline(value) {
  return String(value)
    .split(
      /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*)/g
    )
    .map((part, index) => {
      if (
        part.startsWith("**") &&
        part.endsWith("**")
      ) {
        return (
          <strong key={index}>
            {part.slice(2, -2)}
          </strong>
        );
      }

      if (
        part.startsWith("`") &&
        part.endsWith("`")
      ) {
        return (
          <code key={index}>
            {part.slice(1, -1)}
          </code>
        );
      }

      if (
        part.startsWith("*") &&
        part.endsWith("*")
      ) {
        return (
          <em key={index}>
            {part.slice(1, -1)}
          </em>
        );
      }

      return <span key={index}>{part}</span>;
    });
}

function FormattedAnswer({ content }) {
  const lines = String(content || "")
    .replace(/\r\n/g, "\n")
    .split("\n");

  const blocks = [];

  let paragraph = [];
  let list = [];
  let listType = null;
  let code = [];
  let inCode = false;

  const flushParagraph = () => {
    if (paragraph.length) {
      blocks.push({
        type: "p",
        content: paragraph.join("\n"),
      });
    }

    paragraph = [];
  };

  const flushList = () => {
    if (list.length) {
      blocks.push({
        type: listType,
        items: list,
      });
    }

    list = [];
    listType = null;
  };

  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      flushParagraph();
      flushList();

      if (inCode) {
        blocks.push({
          type: "code",
          content: code.join("\n"),
        });

        code = [];
      }

      inCode = !inCode;
      continue;
    }

    if (inCode) {
      code.push(line);
      continue;
    }

    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }

    const heading = line.match(
      /^\s*#{1,4}\s+(.+)$/
    );

    const bullet = line.match(
      /^\s*[-*•]\s+(.+)$/
    );

    const ordered = line.match(
      /^\s*\d+[.)]\s+(.+)$/
    );

    if (heading) {
      flushParagraph();
      flushList();

      blocks.push({
        type: "heading",
        content: heading[1],
      });
    } else if (bullet || ordered) {
      flushParagraph();

      const kind = bullet ? "ul" : "ol";

      if (listType && listType !== kind) {
        flushList();
      }

      listType = kind;
      list.push((bullet || ordered)[1]);
    } else {
      flushList();
      paragraph.push(line);
    }
  }

  flushParagraph();
  flushList();

  if (code.length) {
    blocks.push({
      type: "code",
      content: code.join("\n"),
    });
  }

  return (
    <div className="formatted-answer">
      {blocks.map((block, index) => {
        if (block.type === "heading") {
          return (
            <h3 key={index}>
              {formatInline(block.content)}
            </h3>
          );
        }

        if (block.type === "code") {
          return (
            <pre key={index}>
              <code>{block.content}</code>
            </pre>
          );
        }

        if (
          block.type === "ul" ||
          block.type === "ol"
        ) {
          const ListTag = block.type;

          return (
            <ListTag key={index}>
              {block.items.map((item, i) => (
                <li key={i}>
                  {formatInline(item)}
                </li>
              ))}
            </ListTag>
          );
        }

        return (
          <p key={index}>
            {block.content
              .split("\n")
              .map((line, i) => (
                <span key={i}>
                  {i > 0 && <br />}
                  {formatInline(line)}
                </span>
              ))}
          </p>
        );
      })}
    </div>
  );
}

// Create a question using the selected study options.
function buildStudyQuestion(region, topic, specimen) {
  const questions = {
    "Muscles & Functions":
      `Explain the major muscles and their functions in the ${region}. Include important attachments and actions.`,

    "Nerves & Blood Supply":
      `Explain the major nerves and blood vessels in the ${region}, including the structures they supply and key anatomical relationships.`,

    "Dissection Landmarks":
      `Explain the important landmarks of the ${region} for a supervised anatomy lab. Include important neighboring structures.`,

    "Anatomical Relationships":
      `Explain the spatial relationships among the major anatomical structures in the ${region}.`,
  };

  const variation =
    specimen === "Typical"
      ? ""
      : ` Describe how the anatomy might appear in a ${specimen.toLowerCase()} specimen, distinguishing typical anatomy from possible variations.`;

  return (
    `${questions[topic]}${variation} ` +
    "Use course lecture notes as the primary reference. " +
    "Explain clearly with headings and bullet points, " +
    "and identify any details not covered by the notes."
  );
}

// Guided Study selection screen.
function GuidedStudySetup({
  onStart,
  onFreeChat,
  disabled,
}) {
  const [region, setRegion] = useState("");
  const [topic, setTopic] = useState(TOPICS[0]);
  const [specimen, setSpecimen] = useState(
    SPECIMENS[0]
  );

  return (
    <div className="study-setup">
      <div className="study-eyebrow">
        GUIDED ANATOMY STUDY
      </div>

      <h1>Build your study session</h1>

      <p className="study-intro">
        Choose what you want to explore. Ask Inglis
        will prepare a focused question.
      </p>

      <button
        type="button"
        className="study-change-mode"
        onClick={onFreeChat}
        disabled={disabled}
      >
        ← Switch to Free Chat
      </button>

      <div className="study-form">
        <div className="study-step">
          <div className="study-step-heading">
            <span>01</span>
            Choose an anatomical region
          </div>

          <div className="study-options study-regions">
            {REGIONS.map((item) => (
              <button
                key={item}
                type="button"
                className={`study-choice ${
                  region === item ? "selected" : ""
                }`}
                aria-pressed={region === item}
                onClick={() => setRegion(item)}
                disabled={disabled}
              >
                {item}
              </button>
            ))}
          </div>
        </div>

        <div className="study-step">
          <div className="study-step-heading">
            <span>02</span>
            Choose a learning topic
          </div>

          <div className="study-options study-topics">
            {TOPICS.map((item) => (
              <button
                key={item}
                type="button"
                className={`study-choice ${
                  topic === item ? "selected" : ""
                }`}
                aria-pressed={topic === item}
                onClick={() => setTopic(item)}
                disabled={disabled}
              >
                {item}
              </button>
            ))}
          </div>
        </div>

        <div className="study-step">
          <label
            className="study-step-heading"
            htmlFor="study-specimen"
          >
            <span>03</span>
            Specimen characteristics
            <em>(optional)</em>
          </label>

          <select
            id="study-specimen"
            className="study-select"
            value={specimen}
            onChange={(event) =>
              setSpecimen(event.target.value)
            }
            disabled={disabled}
          >
            {SPECIMENS.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </div>

        <div className="study-preview">
          <div className="study-preview-label">
            QUESTION PREVIEW
          </div>

          <p>
            {region
              ? buildStudyQuestion(
                  region,
                  topic,
                  specimen
                )
              : "Select an anatomical region to preview your study question."}
          </p>
        </div>

        <button
          className="study-start"
          type="button"
          disabled={!region || disabled}
          onClick={() =>
            onStart({ region, topic, specimen })
          }
        >
          Start Guided Study →
        </button>
      </div>
    </div>
  );
}

export default function App() {
  const [conversations, setConversations] = useState(
    () => {
      const saved = readChats();

      return saved.length ? saved : [newChat()];
    }
  );

  const [activeId, setActiveId] = useState(null);
  const [question, setQuestion] = useState("");
  const [loadingId, setLoadingId] = useState(null);
  const [error, setError] = useState(null);

  const [feedbackTarget, setFeedbackTarget] =
    useState(null);

  const [feedbackComment, setFeedbackComment] =
    useState("");

  const [feedbackPendingId, setFeedbackPendingId] =
    useState(null);

  const feedbackLocks = useRef(new Set());
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  const current =
    conversations.find(
      (chat) => chat.id === activeId
    ) || conversations[0];

  const messages = current?.messages || [];
  const isEmpty = messages.length === 0;

  const showModeChooser =
    isEmpty && current?.mode == null;

  const showStudySetup =
    isEmpty && current?.mode === "guided";

  const showFreeWelcome =
    isEmpty && current?.mode === "free";

  const showComposer =
    !showModeChooser && !showStudySetup;

  // Save conversation history.
  useEffect(() => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(conversations)
    );
  }, [conversations]);

  // Keep a valid active conversation.
  useEffect(() => {
    if (
      !conversations.some(
        (chat) => chat.id === activeId
      )
    ) {
      setActiveId(conversations[0]?.id || null);
    }
  }, [conversations, activeId]);

  // Delete conversations after 30 days without messages.
  useEffect(() => {
    const prune = () => {
      const now = Date.now();

      setConversations((previous) => {
        const valid = previous.filter(
          (chat) =>
            Number.isFinite(
              Number(chat.lastUsedAt)
            ) &&
            now - Number(chat.lastUsedAt) <
              RETENTION_MS
        );

        if (valid.length === previous.length) {
          return previous;
        }

        return valid.length
          ? valid
          : [newChat()];
      });
    };

    const onVisibility = () => {
      if (!document.hidden) {
        prune();
      }
    };

    const timer = window.setInterval(
      prune,
      60000
    );

    window.addEventListener("focus", prune);

    document.addEventListener(
      "visibilitychange",
      onVisibility
    );

    return () => {
      window.clearInterval(timer);

      window.removeEventListener(
        "focus",
        prune
      );

      document.removeEventListener(
        "visibilitychange",
        onVisibility
      );
    };
  }, []);

  // Scroll to latest message.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages.length, loadingId, activeId]);

  function selectChat(id) {
    setActiveId(id);
    setQuestion("");
    setError(null);
  }

  function createChat() {
    const chat = newChat();

    setConversations((previous) => [
      chat,
      ...previous,
    ]);

    setActiveId(chat.id);
    setQuestion("");
    setError(null);
  }

  function deleteChat(id) {
    if (
      !window.confirm(
        "Delete this conversation? This cannot be undone."
      )
    ) {
      return;
    }

    setConversations((previous) => {
      const remaining = previous.filter(
        (chat) => chat.id !== id
      );

      return remaining.length
        ? remaining
        : [newChat()];
    });

    setError(null);
  }

  function chooseMode(mode) {
    if (
      !current ||
      current.messages.length ||
      loadingId
    ) {
      return;
    }

    setConversations((previous) =>
      previous.map((chat) =>
        chat.id === current.id
          ? { ...chat, mode }
          : chat
      )
    );

    if (mode === "free") {
      window.requestAnimationFrame(() =>
        inputRef.current?.focus()
      );
    }
  }

  // Generate a title through the existing /title API.
  async function generateTitle(
    chatId,
    firstQuestion
  ) {
    const fallback = firstQuestion.slice(0, 36);

    try {
      const response = await fetch(
        `${API_URL}/title`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: firstQuestion,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          `Title API error: ${response.status}`
        );
      }

      const data = await response.json();

      const title =
        typeof data.title === "string"
          ? data.title.trim().slice(0, 60)
          : "";

      if (!title) return;

      setConversations((previous) =>
        previous.map((chat) =>
          chat.id === chatId &&
          chat.messages[0]?.content ===
            firstQuestion &&
          (chat.title === fallback ||
            chat.title === "New conversation")
            ? { ...chat, title }
            : chat
        )
      );
    } catch (error) {
      console.warn(
        "Could not generate AI title. Using first question instead.",
        error
      );
    }
  }

  // Send Free Chat or Guided Study questions.
  async function sendMessage(
    textOverride = null,
    studyContext = null
  ) {
    const text = (
      typeof textOverride === "string"
        ? textOverride
        : question
    ).trim();

    if (!text || !current || loadingId) {
      return;
    }

    const chatId = current.id;
    const previousMessages = current.messages;
    const isFirst = previousMessages.length === 0;

    const history = previousMessages
      .slice(-6)
      .map(({ role, content }) => ({
        role,
        content,
      }));

    const userMessage = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
    };

    setConversations((previous) =>
      previous.map((chat) => {
        if (chat.id !== chatId) {
          return chat;
        }

        return {
          ...chat,
          mode: studyContext
            ? "guided"
            : chat.mode || "free",
          studyContext:
            studyContext ||
            chat.studyContext ||
            null,
          title: isFirst
            ? text.slice(0, 36)
            : chat.title,
          messages: [
            ...chat.messages,
            userMessage,
          ],
          lastUsedAt: Date.now(),
        };
      })
    );

    setQuestion("");
    setError(null);
    setLoadingId(chatId);

    if (isFirst) {
      void generateTitle(chatId, text);
    }

    try {
      const response = await fetch(
        `${API_URL}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: text,
            history,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          `Chat API error: ${response.status}`
        );
      }

      const data = await response.json();

      const assistantMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        content:
          data.reply || "No answer returned.",
        question: text,
        lectures: data.lectures || [],
        images: data.images || [],
        degraded: Boolean(data.degraded),
        feedback: null,
      };

      setConversations((previous) =>
        previous.map((chat) =>
          chat.id === chatId
            ? {
                ...chat,
                messages: [
                  ...chat.messages,
                  assistantMessage,
                ],
                lastUsedAt: Date.now(),
              }
            : chat
        )
      );
    } catch (error) {
      console.error(error);

      setError({
        chatId,
        text:
          "Could not get an answer. " +
          "Please check the connection and try again.",
      });
    } finally {
      setLoadingId(null);
    }
  }

  function startGuidedStudy(options) {
    const prompt = buildStudyQuestion(
      options.region,
      options.topic,
      options.specimen
    );

    void sendMessage(prompt, options);
  }

  // Submit student feedback to the backend.
  async function submitFeedback(
    chatId,
    message,
    rating,
    comment = ""
  ) {
    const lockId = `${chatId}:${message.id}`;

    if (
      message.feedback ||
      feedbackLocks.current.has(lockId)
    ) {
      return;
    }

    feedbackLocks.current.add(lockId);
    setFeedbackPendingId(lockId);

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
          `Feedback API error: ${response.status}`
        );
      }

      setConversations((previous) =>
        previous.map((chat) =>
          chat.id === chatId
            ? {
                ...chat,
                messages: chat.messages.map(
                  (item) =>
                    item.id === message.id
                      ? {
                          ...item,
                          feedback: rating,
                        }
                      : item
                ),
              }
            : chat
        )
      );

      setFeedbackTarget(null);
      setFeedbackComment("");
    } catch (error) {
      console.error(error);

      window.alert(
        "Feedback could not be submitted. Please try again."
      );
    } finally {
      feedbackLocks.current.delete(lockId);
      setFeedbackPendingId(null);
    }
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-symbol">
            ✦
          </span>

          <span>Ask Inglis</span>
        </div>

        <button
          className="new-chat"
          type="button"
          onClick={createChat}
        >
          ＋ New conversation
        </button>

        <div className="sidebar-label">
          Recent conversations
        </div>

        <div className="conversation-list">
          {conversations.map((chat) => (
            <div
              className={`conversation-row ${
                chat.id === current?.id
                  ? "active"
                  : ""
              }`}
              key={chat.id}
            >
              <button
                className="conversation-select"
                type="button"
                title={chat.title}
                onClick={() =>
                  selectChat(chat.id)
                }
              >
                {chat.title}
              </button>

              <button
                className="delete-chat"
                type="button"
                title="Delete conversation"
                aria-label={`Delete ${chat.title}`}
                onClick={() =>
                  deleteChat(chat.id)
                }
              >
                ×
              </button>
            </div>
          ))}
        </div>

        <p className="retention-note">
          Conversations without new messages are
          removed from this browser after{" "}
          {RETENTION_DAYS} days.
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
          <strong>
            Anatomy AI Study Assistant
          </strong>

          <span>
            Grounded in course notes
          </span>
        </header>

        <section
          className="messages"
          aria-live="polite"
        >
          {showModeChooser && (
            <div className="study-landing">
              <div className="study-eyebrow">
                WELCOME TO ASK INGLIS
              </div>

              <h1>
                What would you like to study today?
              </h1>

              <p>
                Choose how to begin your anatomy
                learning session.
              </p>

              <div className="study-entry-grid">
                <button
                  type="button"
                  className="study-entry"
                  onClick={() =>
                    chooseMode("free")
                  }
                >
                  <span className="study-entry-icon">
                    ✎
                  </span>

                  <strong>Free Chat</strong>

                  <span>
                    Ask any anatomy question in
                    your own words.
                  </span>

                  <small>
                    Start chatting →
                  </small>
                </button>

                <button
                  type="button"
                  className="study-entry guided"
                  onClick={() =>
                    chooseMode("guided")
                  }
                >
                  <span className="study-entry-icon">
                    ✦
                  </span>

                  <strong>
                    Guided Study
                  </strong>

                  <span>
                    Choose a body region, study topic,
                    and specimen type.
                  </span>

                  <small>
                    Build a study session →
                  </small>
                </button>
              </div>
            </div>
          )}

          {showStudySetup && (
            <GuidedStudySetup
              key={current.id}
              onFreeChat={() =>
                chooseMode("free")
              }
              onStart={startGuidedStudy}
              disabled={Boolean(loadingId)}
            />
          )}

          {showFreeWelcome && (
            <div className="welcome">
              <div className="welcome-emblem">
                ✦
              </div>

              <h1>
                What would you like to learn?
              </h1>

              <p>
                Ask a question about human anatomy
                or your lecture notes.
              </p>

              <button
                type="button"
                className="study-change-mode"
                onClick={() =>
                  chooseMode("guided")
                }
              >
                Try Guided Study instead →
              </button>
            </div>
          )}

          {current?.studyContext &&
            messages.length > 0 && (
              <div className="study-context-bar">
                <span>
                  ✦ Guided Study
                </span>

                <span>
                  {current.studyContext.region}
                </span>

                <span>
                  {current.studyContext.topic}
                </span>

                {current.studyContext.specimen !==
                  "Typical" && (
                  <span>
                    {current.studyContext.specimen} specimen
                  </span>
                )}
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

                    {message.lectures?.length >
                      0 && (
                      <div className="sources">
                        <b>
                          Lecture sources
                        </b>

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
                          submitFeedback(
                            current.id,
                            message,
                            "up"
                          )
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
                          setFeedbackTarget({
                            chatId: current.id,
                            message,
                          });

                          setFeedbackComment("");
                        }}
                      >
                        👎
                      </button>

                      {message.feedback && (
                        <span>
                          Feedback submitted
                        </span>
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

          {error?.chatId === current?.id && (
            <div
              className="chat-error"
              role="alert"
            >
              {error.text}
            </div>
          )}

          <div ref={messagesEndRef} />
        </section>

        {showComposer && (
          <div className="composer-area">
            <div className="composer">
              <textarea
                ref={inputRef}
                placeholder="Ask an anatomy question..."
                aria-label="Ask an anatomy question"
                value={question}
                maxLength={2000}
                rows={2}
                disabled={Boolean(loadingId)}
                onChange={(event) =>
                  setQuestion(event.target.value)
                }
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent
                      .isComposing
                  ) {
                    event.preventDefault();
                    void sendMessage();
                  }
                }}
              />

              <button
                className="send-button"
                type="button"
                disabled={
                  !question.trim() ||
                  Boolean(loadingId)
                }
                onClick={() =>
                  void sendMessage()
                }
              >
                Send
              </button>
            </div>

            <small>
              Enter to send · Shift + Enter for
              a new line
            </small>
          </div>
        )}
      </main>

      {feedbackTarget && (
        <div
          className="modal-overlay"
          role="presentation"
        >
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
              onChange={(event) =>
                setFeedbackComment(
                  event.target.value
                )
              }
            />

            <div className="modal-actions">
              <button
                type="button"
                disabled={Boolean(
                  feedbackPendingId
                )}
                onClick={() =>
                  setFeedbackTarget(null)
                }
              >
                Cancel
              </button>

              <button
                className="primary"
                type="button"
                disabled={Boolean(
                  feedbackPendingId
                )}
                onClick={() =>
                  submitFeedback(
                    feedbackTarget.chatId,
                    feedbackTarget.message,
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