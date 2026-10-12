import { useEffect, useRef, useState } from "react";
import "./App.css";

const BACKEND_URL =
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

function App() {
  const [conversations, setConversations] = useState(() => {
    const saved = localStorage.getItem("askInglisConversations");

    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        if (Array.isArray(parsed) && parsed.length > 0) {
          return parsed;
        }
      } catch (err) {
        console.error("Failed to load saved conversations:", err);
      }
    }

    return [
      {
        id: Date.now(),
        title: "New conversation",
        messages: [],
      },
    ];
  });

  const [activeId, setActiveId] = useState(() => {
    const saved = localStorage.getItem("askInglisConversations");

    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        return parsed[0]?.id ?? null;
      } catch {
        return null;
      }
    }

    return null;
  });

  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const [feedbackMessage, setFeedbackMessage] = useState(null);
  const [feedbackComment, setFeedbackComment] = useState("");

  const messagesEndRef = useRef(null);

  const activeConversation =
    conversations.find((conversation) => conversation.id === activeId) ||
    conversations[0];

  const messages = activeConversation?.messages || [];

  useEffect(() => {
    localStorage.setItem(
      "askInglisConversations",
      JSON.stringify(conversations)
    );
  }, [conversations]);

  useEffect(() => {
    if (!activeId && conversations.length > 0) {
      setActiveId(conversations[0].id);
    }
  }, [activeId, conversations]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  const updateConversationMessages = (conversationId, newMessages) => {
    setConversations((previous) =>
      previous.map((conversation) =>
        conversation.id === conversationId
          ? {
              ...conversation,
              messages: newMessages,
              title:
                conversation.messages.length === 0 &&
                newMessages.length > 0
                  ? newMessages[0].content.slice(0, 35)
                  : conversation.title,
            }
          : conversation
      )
    );
  };

  const handleNewConversation = () => {
    const id = Date.now();

    const newConversation = {
      id,
      title: "New conversation",
      messages: [],
    };

    setConversations((previous) => [
      newConversation,
      ...previous,
    ]);

    setActiveId(id);
    setQuestion("");
    setError("");
  };

  const handleDeleteConversation = (conversationId) => {
    setConversations((previous) => {
      const remaining = previous.filter(
        (conversation) => conversation.id !== conversationId
      );

      if (remaining.length === 0) {
        const id = Date.now();

        const newConversation = {
          id,
          title: "New conversation",
          messages: [],
        };

        setActiveId(id);
        return [newConversation];
      }

      if (conversationId === activeId) {
        setActiveId(remaining[0].id);
      }

      return remaining;
    });

    setQuestion("");
    setError("");
  };

  const handleAsk = async () => {
    if (!question.trim() || loading || !activeConversation) return;

    const conversationId = activeConversation.id;
    const currentMessages = activeConversation.messages || [];
    const userQuestion = question.trim();

    const history = currentMessages
      .slice(-6)
      .map((message) => ({
        role: message.role,
        content: message.content,
      }));

    const userMessage = {
      id: Date.now(),
      role: "user",
      content: userQuestion,
    };

    const messagesWithQuestion = [...currentMessages, userMessage];

    updateConversationMessages(
      conversationId,
      messagesWithQuestion
    );

    setQuestion("");
    setLoading(true);
    setError("");

    try {
      const response = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: userQuestion,
          history,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();

        console.error(
          "Backend error:",
          response.status,
          errorText
        );

        throw new Error("Chat request failed");
      }

      const data = await response.json();

      const assistantMessage = {
        id: Date.now() + 1,
        role: "assistant",
        content: data.reply,
        question: userQuestion,
        lectures: data.lectures || [],
        feedback: null,
      };

      updateConversationMessages(
        conversationId,
        [...messagesWithQuestion, assistantMessage]
      );
    } catch (err) {
      console.error(err);

      setError(
        "Unable to reach the server. Please try again."
      );
    } finally {
      setLoading(false);
    }
  };

  const handleFeedback = async (
    message,
    rating,
    comment = ""
  ) => {
    // Prevent duplicate feedback for the same answer.
    if (message.feedback) {
      return;
    }

    try {
      const response = await fetch(
        `${BACKEND_URL}/feedback`,
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
        throw new Error("Feedback request failed");
      }

      setConversations((previous) =>
        previous.map((conversation) => ({
          ...conversation,
          messages: conversation.messages.map((msg) =>
            msg.id === message.id
              ? {
                  ...msg,
                  feedback: rating,
                }
              : msg
          ),
        }))
      );

      setFeedbackMessage(null);
      setFeedbackComment("");
    } catch (err) {
      console.error("Feedback error:", err);

      alert(
        "Unable to submit feedback. Please try again."
      );
    }
  };

  const handleThumbDown = (message) => {
    // If feedback was already submitted, do nothing.
    if (message.feedback) {
      return;
    }

    setFeedbackMessage(message);
    setFeedbackComment("");
  };

  const submitNegativeFeedback = () => {
    if (!feedbackMessage || feedbackMessage.feedback) return;

    handleFeedback(
      feedbackMessage,
      "down",
      feedbackComment
    );
  };

  const handleKeyDown = (event) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();
      handleAsk();
    }
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="sidebar-header">
          <h2>Ask Inglis</h2>

          <button
            className="new-chat-button"
            onClick={handleNewConversation}
          >
            + New conversation
          </button>
        </div>

        <div className="conversation-list">
          {conversations.map((conversation) => (
            <div
              className="conversation-row"
              key={conversation.id}
            >
              <button
                className={
                  conversation.id === activeConversation?.id
                    ? "conversation-item active"
                    : "conversation-item"
                }
                onClick={() =>
                  setActiveId(conversation.id)
                }
              >
                {conversation.title}
              </button>

              <button
                className="delete-conversation-button"
                title="Delete conversation"
                aria-label="Delete conversation"
                onClick={(event) => {
                  event.stopPropagation();
                  handleDeleteConversation(conversation.id);
                }}
              >
                ×
              </button>
            </div>
          ))}
        </div>
      </aside>

      <main className="chat-container">
        <header className="chat-header">
          <h1>Anatomy AI Study Assistant</h1>
          <p>
            Ask questions based on your anatomy lecture
            materials.
          </p>
        </header>

        <section className="messages">
          {messages.length === 0 && (
            <div className="welcome">
              <h2>
                How can I help you study anatomy?
              </h2>

              <p>
                Ask a question about your lecture
                material.
              </p>
            </div>
          )}

          {messages.map((message) => (
            <div
              key={message.id}
              className={`message-row ${message.role}`}
            >
              <div
                className={`message-bubble ${message.role}`}
              >
                <div className="message-role">
                  {message.role === "user"
                    ? "You"
                    : "Ask Inglis"}
                </div>

                <div className="message-content">
                  {message.content}
                </div>

                {message.role === "assistant" &&
                  message.lectures?.length > 0 && (
                    <div className="sources">
                      <strong>
                        Source
                        {message.lectures.length > 1
                          ? "s"
                          : ""}
                      </strong>

                      {message.lectures.map(
                        (lecture, index) => (
                          <div
                            className="source-item"
                            key={index}
                          >
                            {lecture}
                          </div>
                        )
                      )}
                    </div>
                  )}

                {message.role === "assistant" && (
                  <div className="feedback-buttons">
                    <button
                      className={
                        message.feedback === "up"
                          ? "feedback selected"
                          : "feedback"
                      }
                      title={
                        message.feedback
                          ? "Feedback already submitted"
                          : "Helpful"
                      }
                      disabled={Boolean(message.feedback)}
                      onClick={() =>
                        handleFeedback(
                          message,
                          "up",
                          ""
                        )
                      }
                    >
                      👍
                    </button>

                    <button
                      className={
                        message.feedback === "down"
                          ? "feedback selected"
                          : "feedback"
                      }
                      title={
                        message.feedback
                          ? "Feedback already submitted"
                          : "Not helpful"
                      }
                      disabled={Boolean(message.feedback)}
                      onClick={() =>
                        handleThumbDown(message)
                      }
                    >
                      👎
                    </button>

                    {message.feedback && (
                      <span className="feedback-sent">
                        Feedback submitted
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="message-row assistant">
              <div className="message-bubble assistant">
                <div className="message-role">
                  Ask Inglis
                </div>

                <div className="thinking">
                  Thinking...
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="error-message">
              {error}
            </div>
          )}

          <div ref={messagesEndRef} />
        </section>

        <div className="input-area">
          <div className="input-wrapper">
            <textarea
              placeholder="Ask an anatomy question..."
              value={question}
              onChange={(event) =>
                setQuestion(event.target.value)
              }
              onKeyDown={handleKeyDown}
              disabled={loading}
            />

            <button
              className="send-button"
              onClick={handleAsk}
              disabled={
                loading || !question.trim()
              }
            >
              {loading ? "..." : "Send"}
            </button>
          </div>

          <div className="input-hint">
            Enter to send · Shift + Enter for a new line
          </div>
        </div>
      </main>

      {feedbackMessage && (
        <div className="modal-overlay">
          <div className="feedback-modal">
            <h2>What went wrong?</h2>

            <p>
              Your feedback will help improve future
              answers.
            </p>

            <textarea
              placeholder="Describe the issue, such as incorrect information, missing details, or an incorrect source."
              value={feedbackComment}
              onChange={(event) =>
                setFeedbackComment(event.target.value)
              }
            />

            <div className="modal-buttons">
              <button
                className="cancel-button"
                onClick={() => {
                  setFeedbackMessage(null);
                  setFeedbackComment("");
                }}
              >
                Cancel
              </button>

              <button
                className="submit-feedback-button"
                onClick={submitNegativeFeedback}
              >
                Submit feedback
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
