import { useState } from "react";
import "./App.css";

const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:8000";

function App() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const handleAsk = async () => {
    if (!question.trim()) return;

    setLoading(true);
    setError("");
    setAnswer("");
    setSources([]);

    try {
      const res = await fetch(`${BACKEND_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question }),
      });

      if (!res.ok) throw new Error("Server error");

      const data = await res.json();
      setAnswer(data.reply);
      setSources(data.sources || []);
    } catch (err) {
      setError("Couldn't reach the server. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  return (
    <div className="container">
      <h1>Anatomy AI Study Assistant</h1>

      <textarea
        placeholder="Ask an anatomy question..."
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={handleKeyDown}
      />

      <button onClick={handleAsk} disabled={loading}>
        {loading ? "Thinking..." : "Ask"}
      </button>

      {error && (
        <div className="answer">
          <p style={{ color: "red" }}>{error}</p>
        </div>
      )}

      {answer && !error && (
        <div className="answer">
          <h2>Answer</h2>
          <p>{answer}</p>

          {sources.length > 0 && (
            <>
              <h3>Source{sources.length > 1 ? "s" : ""}</h3>
              {sources.map((src, i) => (
                <p key={i} style={{ fontSize: "14px", color: "#666" }}>
                  {src}
                </p>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default App;