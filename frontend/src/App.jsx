import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import "./App.css";

const API_URL = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "askInglisConversations";
const RETENTION_DAYS = 30;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;
const CHECK_INTERVAL_MS = 60 * 1000;

// The three ways to use the assistant. Quiz and Dissection reuse the same /chat endpoint:
// the mode only decides how the student's words are phrased for the backend.
const MODES = [
  { id: "ask", label: "Ask & Quiz", icon: "💬", placeholder: "Ask a question, or type “quiz me on the brachium”" },
  { id: "dissect", label: "Dissection", icon: "🧰", placeholder: "Type a structure or region, e.g. the anterior triangle of the neck" },
  { id: "images", label: "Diagrams", icon: "🖼️", placeholder: "" },
];

const STARTERS = [
  { mode: "ask", icon: "💬", title: "Explain a structure", text: "What does the biceps brachii do?" },
  { mode: "ask", icon: "📝", title: "Quiz me", text: "Quiz me on the brachium" },
  { mode: "dissect", icon: "🧰", title: "Dissection steps", text: "the anterior triangle of the neck" },
  { mode: "images", icon: "🖼️", title: "Find a diagram", text: "" },
];

const CONTEXT_MESSAGES = 6;

const PICKS_ONLY = /^[\s\dA-Ea-e,.;:()-]+$/;

function buildPayload(mode, text) {
  const t = text.trim();
  if (mode === "quiz") {
    if (PICKS_ONLY.test(t) || /\b(answers?|quiz|questions?|another)\b/i.test(t)) return t;
    return `Give me 5 quiz questions on ${t}`;
  }
  if (mode === "dissect") {
    if (/\b(dissect|dissection|cut|reflect|expose|incision|steps?)\b/i.test(t)) return t;
    return `How do I expose, reflect and dissect ${t}? Give the steps at the dissection table.`;
  }
  return t;
}

function topicFrom(question) {
  return String(question || "")
    .replace(/[?!.]+$/, "")
    .replace(/^(please\s+)?(what|which|where|how|why|when|who)\s+(is|are|does|do|did|was|were|can|should)\s+/i, "")
    .replace(/^(explain|describe|tell me about|show me)\s+/i, "")
    .replace(/\s+(do|does|work|works|mean|attach|insert|originate|supply|supplies)$/i, "")
    .trim()
    .slice(0, 80);
}

// The quiz arrives as plain text ("**Question 1.** ... A. ... B. ..."). Turn it into cards so
// students can tap an option. Returns null for anything that is not an unanswered quiz.
function parseQuiz(content) {
  const text = String(content || "");
  if (!/Reply with your answers/i.test(text) || /\*\*Answers\*\*/.test(text)) return null;
  const parts = text.split(/\*\*Question (\d+)\.\*\*\s*/).slice(1);
  const items = [];
  for (let i = 0; i < parts.length; i += 2) {
    const stem = [];
    const options = [];
    for (const raw of parts[i + 1].split("\n")) {
      const line = raw.trim();
      const m = line.match(/^([A-E])\.\s+(.+)$/);
      if (m) options.push({ letter: m[1], text: m[2] });
      else if (!options.length && line) stem.push(line);
    }
    if (stem.length && options.length >= 2) {
      items.push({ num: Number(parts[i]), stem: stem.join(" "), options });
    }
  }
  return items.length ? items : null;
}

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

const SpeechRec = typeof window !== "undefined"
  ? (window.SpeechRecognition || window.webkitSpeechRecognition)
  : undefined;
const CAN_SPEAK = typeof window !== "undefined" && "speechSynthesis" in window;

function speakableText(markdown) {
  return String(markdown || "")
    .replace(/[*`#]/g, "")
    .replace(/^\s*[-•]\s+/gm, "")
    .replace(/\n+/g, ". ");
}

const GK = /^general knowledge:\s*/i;

// An approved, open-license picture from the catalog, always shown with its credit and license.
function Figure({ image }) {
  const src = /^https?:/i.test(image.url) ? image.url : `${API_URL}${image.url}`;
  const credit = image.attribution || image.source;
  return (
    <figure className="figure">
      <img src={src} alt={image.caption} loading="lazy" referrerPolicy="no-referrer" />
      <figcaption>
        <strong>{image.caption}</strong>
        <span>
          {image.page_url
            ? <a href={image.page_url} target="_blank" rel="noreferrer noopener">{credit}</a>
            : credit}
          {image.license ? ` · ${image.license}` : ""}
        </span>
      </figcaption>
    </figure>
  );
}

function renderItems(items) {
  return items.map((item, j) => {
    const gk = GK.test(item);
    return (
      <li key={j} className={gk ? "gk" : undefined}>
        {gk && <span className="gk-tag">General knowledge</span>}
        {formatInline(gk ? item.replace(GK, "") : item)}
      </li>
    );
  });
}

// ---------- Review what I missed (kept in this browser only) ----------

const MISSED_KEY = "askInglisMissed";

function readMissed() {
  try {
    const saved = JSON.parse(localStorage.getItem(MISSED_KEY) || "[]");
    const now = Date.now();  // same 30-day rule as conversations
    return Array.isArray(saved)
      ? saved.filter((m) => m && m.topic && Number.isFinite(m.ts) && now - m.ts < RETENTION_MS)
      : [];
  } catch {
    return [];
  }
}

function quizTopic(message) {
  const q = String(message?.question || "");
  const m = q.match(/quiz questions on (.+)$/i);
  return (m ? m[1] : q).replace(/[?!.]+$/, "").trim().slice(0, 60);
}

// The graded reply says which questions were wrong: "Question 2: Not quite. You chose C..."
function missedFromGrade(reply, quizItems) {
  const out = [];
  for (const m of String(reply).matchAll(/\*\*Question (\d+): (Correct|Not quite|Not answered)/g)) {
    if (m[2] !== "Not quite") continue;
    const q = quizItems.find((it) => it.num === Number(m[1]));
    if (q) out.push(q.stem.slice(0, 160));
  }
  return out;
}

// ---------- Lecture sources, with the passage behind each answer ----------

function formatDay(iso) {
  const d = new Date(`${iso}T12:00:00`);
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function SourcePanel({ lectures, sources, reviewed }) {
  const [open, setOpen] = useState(null);
  if (!lectures?.length) return null;
  const passagesFor = (lecture) => (sources || [])
    .filter((s) => s.startsWith(`[${lecture}]`))
    .map((s) => s.slice(lecture.length + 2).trim())
    .filter(Boolean)
    .slice(0, 3);
  const active = open && lectures.includes(open) ? open : null;
  return (
    <div className="sources">
      <b>Lecture sources</b>
      <div>
        {lectures.map((lecture, i) => {
          const day = reviewed?.[lecture];
          const canOpen = passagesFor(lecture).length > 0;
          const clickable = canOpen || Boolean(day);
          return (
            <button type="button" key={`${lecture}-${i}`} disabled={!clickable}
              className={`source-chip ${day ? "reviewed" : ""} ${active === lecture ? "open" : ""}`}
              aria-expanded={clickable ? active === lecture : undefined}
              title={day ? `Reviewed by the instructor on ${formatDay(day)}` : canOpen ? "Show the note passage" : undefined}
              onClick={() => setOpen(active === lecture ? null : lecture)}>
              {day && <span aria-hidden="true">✓ </span>}
              {lecture}
              {canOpen && <span aria-hidden="true"> ▾</span>}
            </button>
          );
        })}
      </div>
      {active && (
        <div className="source-passages">
          {passagesFor(active).map((p, i) => <blockquote key={i}>{p}</blockquote>)}
          {reviewed?.[active] && (
            <p className="reviewed-line">✓ Reviewed by the instructor on {formatDay(reviewed[active])}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ---------- Dissection steps, one at a time ----------

const BULLET = /^\s*([-*•]|\d+[.)])\s+/;

function splitSteps(content) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((l) => BULLET.test(l));
  if (start < 0) return null;
  let end = start;
  while (end < lines.length &&
    (BULLET.test(lines[end]) || (!lines[end].trim() && BULLET.test(lines[end + 1] || "")))) end++;
  const steps = lines.slice(start, end).filter((l) => BULLET.test(l)).map((l) => l.replace(BULLET, "").trim());
  if (steps.length < 3) return null;
  return {
    head: lines.slice(0, start).join("\n").trim(),
    steps,
    tail: lines.slice(end).join("\n").trim(),
  };
}

const CHECKS_KEY = "askInglisChecks";

function readChecks() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHECKS_KEY) || "{}");
    const now = Date.now();  // same 30-day rule as conversations
    return Object.fromEntries(Object.entries(saved).filter(
      ([, v]) => v && Number.isFinite(v.ts) && now - v.ts < RETENTION_MS
    ));
  } catch {
    return {};
  }
}

function saveChecks(id, done) {
  try {
    const all = readChecks();
    all[id] = { ts: Date.now(), done };
    localStorage.setItem(CHECKS_KEY, JSON.stringify(all));
  } catch (error) {
    console.warn("Could not save the checklist:", error);
  }
}

function formatClock(total) {
  const m = Math.floor(total / 60);
  const sec = String(total % 60).padStart(2, "0");
  return `${m}:${sec}`;
}

function StepViewer({ content, messageId }) {
  const parts = useMemo(() => splitSteps(content), [content]);
  const [index, setIndex] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [view, setView] = useState("steps");
  const [done, setDone] = useState(() => readChecks()[messageId]?.done || []);
  const [seconds, setSeconds] = useState(0);
  const [running, setRunning] = useState(false);
  const canSpeak = typeof window !== "undefined" && "speechSynthesis" in window;

  useEffect(() => () => { if (canSpeak) window.speechSynthesis.cancel(); }, [canSpeak]);

  useEffect(() => {
    if (!running) return undefined;
    const timer = window.setInterval(() => setSeconds((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!parts || showAll) {
    return (
      <>
        <FormattedAnswer content={content} />
        {parts && (
          <button type="button" className="step-link" onClick={() => setShowAll(false)}>
            Back to step by step
          </button>
        )}
      </>
    );
  }

  const { head, steps, tail } = parts;
  const toggleStep = (i) => {
    const next = done.includes(i) ? done.filter((n) => n !== i) : [...done, i];
    setDone(next);
    saveChecks(messageId, next);
  };
  const resetChecks = () => { setDone([]); saveChecks(messageId, []); };
  const stop = () => { if (canSpeak) window.speechSynthesis.cancel(); setSpeaking(false); };
  const go = (next) => { stop(); setIndex(Math.max(0, Math.min(steps.length - 1, next))); };
  const speak = () => {
    if (speaking) { stop(); return; }
    const text = `Step ${index + 1}. ${steps[index].replace(/[*`]/g, "")}`;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.cancel();
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  const switcher = (
    <div className="view-switch" role="tablist" aria-label="Dissection view">
      <button type="button" role="tab" aria-selected={view === "steps"}
        className={view === "steps" ? "active" : ""} onClick={() => setView("steps")}>Step by step</button>
      <button type="button" role="tab" aria-selected={view === "check"}
        className={view === "check" ? "active" : ""} onClick={() => setView("check")}>Checklist</button>
    </div>
  );

  if (view === "check") {
    const finished = steps.filter((_, i) => done.includes(i)).length;
    return (
      <div className="steps">
        {head && <FormattedAnswer content={head} />}
        {switcher}
        <div className="check-card">
          <div className="check-top">
            <strong>{finished} of {steps.length} done</strong>
            <div className="timer" aria-label="Station timer">
              <span className="clock">{formatClock(seconds)}</span>
              <button type="button" onClick={() => setRunning((r) => !r)}>{running ? "Pause" : seconds ? "Resume" : "Start"}</button>
              <button type="button" disabled={!seconds && !running}
                onClick={() => { setRunning(false); setSeconds(0); }}>Reset</button>
            </div>
          </div>
          <div className="step-bar"><span style={{ width: `${(finished / steps.length) * 100}%` }} /></div>
          <ul className="checklist">
            {steps.map((step, i) => (
              <li key={i} className={done.includes(i) ? "done" : ""}>
                <label>
                  <input type="checkbox" checked={done.includes(i)} onChange={() => toggleStep(i)} />
                  <span>{formatInline(step)}</span>
                </label>
              </li>
            ))}
          </ul>
          <button type="button" className="step-link" disabled={!done.length} onClick={resetChecks}>Clear all ticks</button>
        </div>
        {tail && <FormattedAnswer content={tail} />}
      </div>
    );
  }

  return (
    <div className="steps">
      {head && <FormattedAnswer content={head} />}
      {switcher}
      <div className="step-card" aria-live="polite">
        <div className="step-count">Step {index + 1} of {steps.length}</div>
        <div className="step-bar"><span style={{ width: `${((index + 1) / steps.length) * 100}%` }} /></div>
        <p className="step-text">{formatInline(steps[index])}</p>
      </div>
      <div className="step-actions">
        <button type="button" disabled={index === 0} onClick={() => go(index - 1)}>← Back</button>
        {canSpeak && (
          <button type="button" onClick={speak}>{speaking ? "■ Stop" : "🔊 Read aloud"}</button>
        )}
        <button type="button" className="primary" disabled={index === steps.length - 1}
          onClick={() => go(index + 1)}>Next →</button>
      </div>
      <button type="button" className="step-link" onClick={() => setShowAll(true)}>Show all steps</button>
      {tail && <FormattedAnswer content={tail} />}
    </div>
  );
}

// ---------- Diagrams: search the instructor-approved picture catalog ----------

function DiagramsTab({ onAsk }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const search = async (event) => {
    event.preventDefault();
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      const response = await fetch(`${API_URL}/images?query=${encodeURIComponent(q)}&limit=5`);
      if (!response.ok) throw new Error(`Image search failed (${response.status})`);
      const data = await response.json();
      setResults(Array.isArray(data) ? data : []);
    } catch (err) {
      console.error(err);
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="messages diagrams">
      <div className="diagrams-inner">
        <h1>Diagrams</h1>
        <p>Search pictures that the instructor's team has approved. Each one shows its credit and license.</p>
        <form className="diagram-search" onSubmit={search}>
          <input type="search" value={query} maxLength={200} aria-label="Search diagrams"
            placeholder="e.g. brachial plexus, middle ear ossicles"
            onChange={(event) => setQuery(event.target.value)} />
          <button type="submit" className="primary" disabled={!query.trim() || busy}>
            {busy ? "Searching…" : "Search"}
          </button>
        </form>
        {failed && <div className="chat-error" role="alert">Could not search diagrams. Please try again.</div>}
        {results && results.length === 0 && !failed && (
          <div className="diagram-empty">
            <strong>No approved diagrams match that yet.</strong>
            <span>Pictures are added by the instructor's team and checked before students see them. Try another name, or ask a question instead.</span>
          </div>
        )}
        {results && results.map((image, i) => (
          <div className="diagram-result" key={`${image.url}-${i}`}>
            <Figure image={image} />
            <button type="button" onClick={() => onAsk(image.caption)}>Ask about this</button>
          </div>
        ))}
        <p className="diagram-note">No photos of donors are ever shown or accepted here.</p>
      </div>
    </section>
  );
}

function QuizCards({ items, locked, onSubmit, onShowAnswers }) {
  const [picks, setPicks] = useState({});
  const answered = Object.keys(picks).length;
  const submit = () => onSubmit(
    items.filter((q) => picks[q.num]).map((q) => `${q.num}${picks[q.num]}`).join(" ")
  );
  return (
    <div className="quiz">
      {items.map((q) => (
        <fieldset className="quiz-q" key={q.num} disabled={locked}>
          <legend>Question {q.num}</legend>
          <p>{formatInline(q.stem)}</p>
          <div className="quiz-options">
            {q.options.map((o) => (
              <button type="button" key={o.letter} aria-pressed={picks[q.num] === o.letter}
                className={`quiz-option ${picks[q.num] === o.letter ? "selected" : ""}`}
                onClick={() => setPicks((old) => ({ ...old, [q.num]: o.letter }))}>
                <span className="quiz-letter">{o.letter}</span>
                <span>{formatInline(o.text)}</span>
              </button>
            ))}
          </div>
        </fieldset>
      ))}
      <div className="quiz-actions">
        <span>{answered} of {items.length} answered</span>
        <button type="button" disabled={locked} onClick={onShowAnswers}>Show answers</button>
        <button type="button" className="primary" disabled={locked || !answered} onClick={submit}>
          Check my answers
        </button>
      </div>
    </div>
  );
}

function FormattedAnswer({ content }) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let paragraph = [];
  let listItems = [];
  let listKind = null;
  let codeLines = [];
  let inCode = false;
  let tableLines = [];

  const finishTable = () => {
    if (!tableLines.length) return;
    const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    const rows = tableLines.map(cells).filter((r) => !r.every((c) => /^:?-{2,}:?$/.test(c)));
    if (rows.length >= 2) blocks.push({ type: "table", header: rows[0], rows: rows.slice(1) });
    else paragraph.push(...tableLines);
    tableLines = [];
  };

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
    if (/^\s*\|.*\|\s*$/.test(line)) {
      finishParagraph();
      finishList();
      tableLines.push(line);
      continue;
    }
    finishTable();
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
  finishTable();
  finishParagraph();
  finishList();
  if (codeLines.length) blocks.push({ type: "code", text: codeLines.join("\n") });

  // Fold "**Body type note**" and the bullets after it into one collapsible section
  const shown = [];
  for (let i = 0; i < blocks.length; i++) {
    const b = blocks[i];
    const next = blocks[i + 1];
    if (b.type === "p" && /^\*\*body type note\*\*:?$/i.test(b.text.trim()) &&
        next && (next.type === "ul" || next.type === "ol")) {
      shown.push({ type: "note", items: next.items });
      i++;
    } else {
      shown.push(b);
    }
  }

  return (
    <div className="formatted-answer">
      {shown.map((block, i) => {
        if (block.type === "note") {
          return (
            <details className="body-note" key={i} open>
              <summary>Body type note</summary>
              <ul>{renderItems(block.items)}</ul>
            </details>
          );
        }
        if (block.type === "table") {
          return (
            <div className="table-wrap" key={i}>
              <table>
                <thead><tr>{block.header.map((h, j) => <th key={j}>{formatInline(h)}</th>)}</tr></thead>
                <tbody>
                  {block.rows.map((row, r) => (
                    <tr key={r}>{row.map((cell, j) => <td key={j}>{formatInline(cell)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        if (block.type === "h") return <h3 key={i}>{formatInline(block.text)}</h3>;
        if (block.type === "code") {
          return <pre key={i}><code>{block.text}</code></pre>;
        }
        if (block.type === "ul" || block.type === "ol") {
          const Tag = block.type;
          return (
            <Tag key={i}>{renderItems(block.items)}</Tag>
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
  const [mode, setMode] = useState("ask");
  const [loadingText, setLoadingText] = useState("Thinking…");
  const [copiedId, setCopiedId] = useState(null);
  const [reviewed, setReviewed] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [undo, setUndo] = useState(null);
  const [printIds, setPrintIds] = useState(null);
  const undoTimer = useRef(null);
  const [listening, setListening] = useState(false);
  const [speakingId, setSpeakingId] = useState(null);
  const recRef = useRef(null);
  const inputRef = useRef(null);
  const [missed, setMissed] = useState(readMissed);
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

  // Which lectures the instructor has reviewed (shown as a check on the source chip)
  useEffect(() => {
    const clear = () => setPrintIds(null);
    window.addEventListener("afterprint", clear);
    return () => window.removeEventListener("afterprint", clear);
  }, []);

  useEffect(() => () => {
    recRef.current?.abort?.();
    if (CAN_SPEAK) window.speechSynthesis.cancel();
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_URL}/reviewed`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => { if (!cancelled && data?.lectures) setReviewed(data.lectures); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(MISSED_KEY, JSON.stringify(missed));
    } catch (error) {
      console.warn("Could not save missed topics:", error);
    }
  }, [missed]);

  const missedTopics = useMemo(() => {
    const counts = new Map();
    for (const m of missed) counts.set(m.topic, (counts.get(m.topic) || 0) + 1);
    return [...counts].map(([topic, count]) => ({ topic, count })).sort((a, b) => b.count - a.count);
  }, [missed]);

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
    setEditingId(null);
    setError("");
  };

  const newConversation = () => {
    const item = makeConversation();
    setConversations((old) => [item, ...old]);
    setActiveId(item.id);
    setQuestion("");
    setEditingId(null);
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

  const ask = async (overrideText, overrideMode) => {
    const text = (typeof overrideText === "string" ? overrideText : question).trim();
    const activeMode = overrideMode || mode;
    if (!text || !current || loadingId) return;
    const payload = buildPayload(activeMode, text);
    setLoadingText(
      /\bquiz questions\b|\banother set\b/i.test(payload) ? "Writing and checking questions. This takes about 20 to 40 seconds…"
        : PICKS_ONLY.test(payload) || /\banswers?\b/i.test(payload) ? "Checking your answers…"
        : activeMode === "dissect" ? "Finding the dissection steps…"
        : "Thinking…"
    );
    const conversationId = current.id;
    recRef.current?.stop?.();
    const cut = editingId && typeof overrideText !== "string"
      ? current.messages.findIndex((m) => m.id === editingId) : -1;
    const previous = cut >= 0 ? current.messages.slice(0, cut) : [...current.messages];
    setEditingId(null);
    const history = previous.slice(-CONTEXT_MESSAGES).map(({ role, content }) => ({ role, content }));
    updateMessages(conversationId, (old) => [
      ...(cut >= 0 ? old.slice(0, cut) : old),
      { id: crypto.randomUUID(), role: "user", content: text,
        mode: activeMode === "ask" ? undefined : activeMode },
    ]);
    if (previous.length === 0) void generateTitle(conversationId, text);
    setQuestion("");
    setError("");
    setLoadingId(conversationId);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: payload, history }),
      });
      if (!response.ok) throw new Error(`Chat request failed (${response.status})`);
      const data = await response.json();
      if (/^\*\*Score: /.test(data.reply || "")) {
        const quizMsg = [...previous].reverse().find((m) => m.role === "assistant" && parseQuiz(m.content));
        if (quizMsg) {
          const topic = quizTopic(quizMsg);
          const stems = missedFromGrade(data.reply, parseQuiz(quizMsg.content));
          setMissed((old) => (stems.length
            ? [...old, ...stems.map((stem) => ({ topic, stem, ts: Date.now() }))].slice(-40)
            : old.filter((m) => m.topic !== topic)));  // all right: this topic is done for now
        }
      }
      updateMessages(conversationId, (old) => [
        ...old,
        {
          id: crypto.randomUUID(), role: "assistant",
          content: data.reply || "No answer returned.", question: text,
          lectures: data.lectures || [], images: data.images || [],
          sources: (data.sources || []).map((src) => String(src).slice(0, 700)),
          mode: activeMode === "dissect" ? "dissect" : undefined,
          understoodAs: data.understood_as || undefined,
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

  const startEdit = (message) => {
    setEditingId(message.id);
    setMode(message.mode || "ask");
    setQuestion(message.content);
    setError("");
    window.setTimeout(() => inputRef.current?.focus(), 0);
  };

  // Remove one question and its answer. They also stop being sent to the assistant as context.
  const removeExchange = (message) => {
    if (loadingId || !current) return;
    const idx = messages.findIndex((m) => m.id === message.id);
    if (idx < 0) return;
    let end = idx + 1;
    while (end < messages.length && messages[end].role !== "user") end++;
    const removed = messages.slice(idx, end);
    updateMessages(current.id, (old) => [...old.slice(0, idx), ...old.slice(end)]);
    if (removed.some((m) => m.id === editingId)) { setEditingId(null); setQuestion(""); }
    window.clearTimeout(undoTimer.current);
    setUndo({ conversationId: current.id, idx, removed });
    undoTimer.current = window.setTimeout(() => setUndo(null), 10000);
  };

  const undoRemove = () => {
    if (!undo) return;
    window.clearTimeout(undoTimer.current);
    updateMessages(undo.conversationId, (old) => [
      ...old.slice(0, undo.idx), ...undo.removed, ...old.slice(undo.idx),
    ]);
    setUndo(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setQuestion("");
  };

  // Voice typing: the words land in the box, so the student can fix them before sending
  const toggleMic = () => {
    if (!SpeechRec) return;
    if (listening) { recRef.current?.stop(); return; }
    const rec = new SpeechRec();
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = false;
    const base = question.trim() ? `${question.trim()} ` : "";
    rec.onresult = (event) => {
      let heard = "";
      for (let i = 0; i < event.results.length; i++) heard += event.results[i][0].transcript;
      setQuestion(base + heard);
    };
    rec.onend = () => setListening(false);
    rec.onerror = (event) => {
      setListening(false);
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        setError("Microphone access is blocked. Allow it in your browser's site settings to use voice typing.");
      }
    };
    recRef.current = rec;
    setError("");
    setListening(true);
    try { rec.start(); } catch { setListening(false); }
  };

  const speakAnswer = (message) => {
    if (!CAN_SPEAK) return;
    if (speakingId === message.id) {
      window.speechSynthesis.cancel();
      setSpeakingId(null);
      return;
    }
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(speakableText(message.content));
    utterance.onend = () => setSpeakingId(null);
    utterance.onerror = () => setSpeakingId(null);
    setSpeakingId(message.id);
    window.speechSynthesis.speak(utterance);
  };

  // Study sheet: print (or "Save as PDF") one question and its answer, without the rest of the screen
  const printSheet = (index) => {
    const ids = [messages[index].id];
    if (messages[index - 1]?.role === "user") ids.unshift(messages[index - 1].id);
    setPrintIds(ids);
    window.setTimeout(() => window.print(), 120);
  };

  const copyAnswer = async (message) => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopiedId(message.id);
      window.setTimeout(() => setCopiedId(null), 1500);
    } catch (err) {
      console.warn("Copy failed:", err);
    }
  };

  const pickStarter = (starter) => {
    setMode(starter.mode);
    setQuestion(starter.text);
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
    <div className="app-shell" data-printing={printIds ? "true" : undefined}>
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
        {missedTopics.length > 0 && (
          <div className="missed">
            <div className="sidebar-label">Review what I missed</div>
            {missedTopics.slice(0, 4).map((t) => (
              <button type="button" key={t.topic} disabled={Boolean(loadingId)}
                onClick={() => ask(`Give me 5 quiz questions on ${t.topic}`, "ask")}>
                <span>{t.topic}</span><b>{t.count}</b>
              </button>
            ))}
            <button type="button" className="missed-clear" onClick={() => setMissed([])}>Clear</button>
          </div>
        )}
        <p className="retention-note">
          Conversations and missed-question lists are removed from this browser after {RETENTION_DAYS} days
          without new activity.
        </p>
      </aside>

      <main className="chat-panel" style={{
        backgroundImage: `linear-gradient(90deg, rgba(250,252,253,.88), rgba(250,252,253,.92)), url("${import.meta.env.BASE_URL}anatomy-background.webp")`,
      }}>
        <header className="topbar">
          <strong>Anatomy AI Study Assistant</strong>
          <div className="mode-switch" role="tablist" aria-label="Mode">
            {MODES.map((m) => (
              <button type="button" role="tab" key={m.id} aria-selected={mode === m.id}
                className={mode === m.id ? "active" : ""} onClick={() => setMode(m.id)}>
                <span aria-hidden="true">{m.icon}</span> {m.label}
              </button>
            ))}
          </div>
          <span className="grounded">Grounded in course notes</span>
        </header>
        {mode === "images" ? (
          <DiagramsTab onAsk={(caption) => { setMode("ask"); setQuestion(`Explain ${caption}`); }} />
        ) : (<>
        <section className="messages" aria-live="polite">
          <div className="print-header">
            <strong>Ask Inglis study sheet</strong>
            <span>{new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}</span>
          </div>
          {messages.length === 0 && (
            <div className="welcome">
              <div className="welcome-emblem">✦</div>
              <h1>What would you like to learn?</h1>
              <p>Ask a question, get quizzed, or get dissection steps. Every answer comes from Dr. Inglis's lecture notes.</p>
              <div className="starters">
                {STARTERS.map((s) => (
                  <button type="button" className="starter" key={s.title} onClick={() => pickStarter(s)}>
                    <span className="starter-icon" aria-hidden="true">{s.icon}</span>
                    <strong>{s.title}</strong>
                    <small>{s.text}</small>
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((message, index) => {
            const isLatest = index === messages.length - 1 && !loadingId;
            const quiz = message.role === "assistant" ? parseQuiz(message.content) : null;
            const isGraded = /^\*\*Score: /.test(message.content || "");
            const topic = topicFrom(message.question);
            return (
            <Fragment key={message.id}>
            {messages.length > CONTEXT_MESSAGES && index === messages.length - CONTEXT_MESSAGES && (
              <div className="context-line" role="separator">
                <span>Messages above this line are no longer used for answers</span>
              </div>
            )}
            <div className={`message-row ${printIds?.includes(message.id) ? "print-target" : ""}`}>
              <article className={`message ${message.role === "user" ? "user-message" : "assistant-message"}`}>
                <div className="message-author">
                  {message.role === "user" ? "You" : "Ask Inglis"}
                </div>
                {message.role === "assistant"
                  ? (quiz
                    ? <QuizCards items={quiz} locked={!isLatest}
                        onSubmit={(picks) => ask(picks, "ask")}
                        onShowAnswers={() => ask("show answers", "ask")} />
                    : <>
                        {message.understoodAs && (
                          <p className="understood">Showing answers for “{message.understoodAs}”</p>
                        )}
                        {message.mode === "dissect"
                          ? <StepViewer content={message.content} messageId={message.id} />
                          : <FormattedAnswer content={message.content} />}
                        {message.images?.[0]?.url && <Figure image={message.images[0]} />}
                      </>)
                  : <div className="user-text">
                      {message.mode && <span className="mode-badge">{message.mode === "quiz" ? "Quiz" : "Dissection"}</span>}
                      {message.content}
                    </div>}
                {message.role === "user" && !loadingId && (
                  <div className="msg-tools">
                    <button type="button" className="edit-btn" aria-label="Edit this question"
                      onClick={() => startEdit(message)}>✎ Edit</button>
                    <button type="button" className="edit-btn" aria-label="Remove this question and its answer"
                      onClick={() => removeExchange(message)}>🗑 Remove</button>
                  </div>
                )}
                {message.role === "assistant" && (
                  <>
                    {message.degraded && (
                      <p className="notice">The assistant is temporarily using retrieved note excerpts.</p>
                    )}
                    <SourcePanel lectures={message.lectures} sources={message.sources} reviewed={reviewed} />
                    <div className="feedback-actions">
                      <button type="button" title="Helpful" aria-label="Helpful"
                        disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                        onClick={() => submitFeedback(message, "up")}>👍</button>
                      <button type="button" title="Not helpful" aria-label="Not helpful"
                        disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                        onClick={() => { setFeedbackTarget(message); setFeedbackComment(""); }}>👎</button>
                      <button type="button" title="Copy answer" aria-label="Copy answer"
                        onClick={() => copyAnswer(message)}>
                        {copiedId === message.id ? "✓" : "⧉"}
                      </button>
                      <button type="button" title="Print or save as a study sheet" aria-label="Print or save as a study sheet"
                        onClick={() => printSheet(index)}>🖨</button>
                      {CAN_SPEAK && (
                        <button type="button" title="Read answer aloud" aria-label="Read answer aloud"
                          onClick={() => speakAnswer(message)}>
                          {speakingId === message.id ? "■" : "🔊"}
                        </button>
                      )}
                      {message.feedback && <span>Feedback submitted</span>}
                    </div>
                    {isLatest && !message.degraded && !quiz && (
                      <div className="chips">
                        {isGraded ? (
                          <button type="button" onClick={() => ask("Give me another set of 5 quiz questions", "ask")}>
                            📝 Another set
                          </button>
                        ) : topic && message.question && !PICKS_ONLY.test(message.question) && (
                          <>
                            <button type="button" onClick={() => ask(`Give me 5 quiz questions on ${topic}`, "ask")}>
                              📝 Quiz me on this
                            </button>
                            <button type="button"
                              onClick={() => ask(topic, "dissect")}>
                              🧰 Dissection steps
                            </button>
                            <button type="button" onClick={() => ask(`Explain ${topic} in simpler terms`, "ask")}>
                              💡 Explain simpler
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </>
                )}
              </article>
            </div>
            </Fragment>
            );
          })}
          {loadingId === current?.id && (
            <div className="message-row">
              <article className="message assistant-message">
                <div className="message-author">Ask Inglis</div>
                <span className="thinking">{loadingText}</span>
              </article>
            </div>
          )}
          {error && <div className="chat-error" role="alert">{error}</div>}
          <div ref={messagesEndRef} />
        </section>
        <div className="composer-area">
          {undo && (
            <div className="edit-banner" role="status">
              Removed a question and its answer.
              <button type="button" onClick={undoRemove}>Undo</button>
            </div>
          )}
          {mode === "ask" && !question && !editingId && (
            <div className="hint-row">
              <button type="button" onClick={() => {
                setQuestion("Quiz me on ");
                window.setTimeout(() => {
                  const box = inputRef.current;
                  if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                }, 0);
              }}>📝 Quiz me on…</button>
              <button type="button" onClick={() => {
                setQuestion("Compare ");
                window.setTimeout(() => {
                  const box = inputRef.current;
                  if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                }, 0);
              }}>⚖️ Compare…</button>
              <button type="button" onClick={() => {
                setQuestion("Make a one-page study sheet on ");
                window.setTimeout(() => {
                  const box = inputRef.current;
                  if (box) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                }, 0);
              }}>📄 Study sheet on…</button>
            </div>
          )}
          {editingId && (
            <div className="edit-banner" role="status">
              Editing a question. Sending it replaces that question and everything after it.
              <button type="button" onClick={cancelEdit}>Cancel</button>
            </div>
          )}
          <div className="composer">
            <textarea ref={inputRef} spellCheck autoCorrect="on" autoCapitalize="sentences"
              placeholder={listening ? "Listening… speak your question" : MODES.find((m) => m.id === mode).placeholder}
              aria-label="Ask an anatomy question" value={question} maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault(); ask();
                }
              }}
              disabled={Boolean(loadingId)} rows={2} />
            {SpeechRec && (
              <button type="button" className={`mic-button ${listening ? "on" : ""}`}
                aria-label={listening ? "Stop voice typing" : "Voice typing"} aria-pressed={listening}
                title="Voice typing (your browser's speech service turns your voice into text)"
                disabled={Boolean(loadingId)} onClick={toggleMic}>🎤</button>
            )}
            <button type="button" className="send-button"
              disabled={!question.trim() || Boolean(loadingId)} onClick={() => ask()}>Send</button>
          </div>
          <small>Enter to send · Shift + Enter for a new line</small>
        </div>
        </>)}
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
