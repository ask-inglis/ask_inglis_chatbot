import { useState } from "react";
import "./App.css";

function App() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");

  const handleAsk = () => {
    setAnswer("AI answer will appear here.");
  };

  return (
    <div className="container">
      <h1>Anatomy AI Study Assistant</h1>

      <textarea
        placeholder="Ask an anatomy question..."
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
      />

      <button onClick={handleAsk}>Ask</button>

      {answer && (
        <div className="answer">
          <h2>Answer</h2>
          <p>{answer}</p>

          <h3>Source</h3>
          <p>Lecture note source will appear here.</p>
        </div>
      )}
    </div>
  );
}

export default App;