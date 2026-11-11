import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./App.css";
import "./GuidedStudy.css";
import "./AnatomyTheme.css";

const API_URL = (
  import.meta.env.VITE_BACKEND_URL || "http://localhost:8000"
).replace(/\/$/, "");

const STORAGE_KEY = "askInglisConversations";
const RETENTION_DAYS = 30;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;
const CHECK_INTERVAL_MS = 60 * 1000;

// The ways to use the assistant. Quiz reuses the same /chat endpoint; the lab bench is a side panel:
// the mode only decides how the student's words are phrased for the backend.
const MODES = [
  { id: "ask", label: "Ask & Quiz", icon: "🧠", placeholder: "Ask a question, or type “quiz me on the brachium”" },
  { id: "guided", label: "Guided", icon: "🧭", placeholder: "Ask a follow-up question" },
  { id: "images", label: "Diagrams", icon: "🖼️", placeholder: "" },
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

// Read-aloud text for an ordinary answer: no markdown symbols, no tables.
const speakableText = (content) => String(content || "")
  .split("\n")
  .filter((l) => !/^\s*\|/.test(l))
  .map((l) => l.replace(/^\s*#{1,4}\s+/, "").replace(/^\s*[-*•]\s+/, "").replace(/^\s*\d+[.)]\s+/, "").replace(/[*_`]/g, "").trim())
  .filter(Boolean)
  .join(". ")
  .replace(/\.\s*\./g, ".")
  .slice(0, 4000);

const CAN_SPEAK = typeof window !== "undefined" && "speechSynthesis" in window;

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

// Layered guide: numbered steps, each followed by "You should see / Do not cut / Can't find it?" lines
const NUMBERED = /^\s{0,3}\d+[.)]\s+/;
const EXTRA = /^\s*(?:[-*•]\s*)?\**\s*(you should see|see|do not cut|careful|can['’]t find it|stuck)\s*\**\s*[:?]\s*\**\s*(.*)$/i;

function splitLayered(content) {
  const lines = String(content || "").replace(/\r\n/g, "\n").split("\n");
  const steps = [];
  const extras = [];
  let first = -1;
  let last = -1;
  lines.forEach((line, i) => {
    if (NUMBERED.test(line)) {
      steps.push(line.replace(NUMBERED, "").trim());
      extras.push({});
      if (first < 0) first = i;
      last = i;
      return;
    }
    if (!steps.length) return;
    const m = EXTRA.exec(line);
    if (!m || !m[2].trim()) return;
    const label = m[1].toLowerCase();
    const key = label.includes("see") ? "see" : /cut|careful/.test(label) ? "careful" : "stuck";
    extras[extras.length - 1][key] = m[2].trim().replace(/\*+$/, "");
    last = i;
  });
  if (steps.length < 3 || !extras.some((e) => e.see || e.careful || e.stuck)) return null;
  return {
    head: lines.slice(0, first).join("\n").trim(),
    steps,
    extras,
    tail: lines.slice(last + 1).join("\n").trim(),
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
  const parts = useMemo(() => splitLayered(content) || splitSteps(content), [content]);
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

  const { head, steps, tail, extras } = parts;
  const extra = extras?.[index];
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
    const text = `Step ${index + 1}. ${steps[index].replace(/[*`]/g, "")}${extra?.see ? ` You should see: ${extra.see.replace(/[*`]/g, "")}` : ""}`;
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
        {extra && (extra.see || extra.careful || extra.stuck) && (
          <div className="step-extras">
            {extra.see && <div className="step-extra see"><b>👁 You should see</b><span>{formatInline(extra.see)}</span></div>}
            {extra.careful && <div className="step-extra careful"><b>⛔ Do not cut</b><span>{formatInline(extra.careful)}</span></div>}
            {extra.stuck && <div className="step-extra stuck"><b>🔎 Can’t find it?</b><span>{formatInline(extra.stuck)}</span></div>}
          </div>
        )}
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

const CONFIDENCE = [
  { id: "sure", label: "Sure" },
  { id: "unsure", label: "Not sure" },
  { id: "guess", label: "Guessing" },
];

function QuizCards({ items, locked, onSubmit, onShowAnswers }) {
  const [picks, setPicks] = useState({});
  const [sureness, setSureness] = useState({});
  const answered = Object.keys(picks).length;
  const submit = () => onSubmit(
    items.filter((q) => picks[q.num]).map((q) => `${q.num}${picks[q.num]}`).join(" "),
    Object.fromEntries(items.filter((q) => picks[q.num]).map((q) => [q.num, sureness[q.num] || "unsure"]))
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
          {picks[q.num] && (
            <div className="quiz-sure" role="group" aria-label={`How sure are you about question ${q.num}?`}>
              <span>How sure are you?</span>
              {CONFIDENCE.map((c) => (
                <button type="button" key={c.id} aria-pressed={sureness[q.num] === c.id}
                  className={`sure-chip ${sureness[q.num] === c.id ? "on" : ""}`}
                  onClick={() => setSureness((old) => ({ ...old, [q.num]: c.id }))}>{c.label}</button>
              ))}
            </div>
          )}
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

// After grading: right/wrong crossed with how sure the student said they were.
function GradeBreakdown({ reply, confidence }) {
  if (!confidence) return null;
  const rows = [...String(reply).matchAll(/\*\*Question (\d+): (Correct|Not quite|Not answered)/g)]
    .map((m) => ({ num: Number(m[1]), right: m[2] === "Correct", sure: confidence[m[1]] === "sure" }));
  if (!rows.length) return null;
  const nums = (list) => list.map((r) => r.num);
  const groups = [
    { key: "gap", tone: "bad", title: "Confident but wrong", hint: "Study these first. You thought you knew them.", list: rows.filter((r) => !r.right && r.sure) },
    { key: "work", tone: "warn", title: "Wrong, and you were unsure", hint: "You already sensed these were weak.", list: rows.filter((r) => !r.right && !r.sure) },
    { key: "shaky", tone: "info", title: "Right, but not sure", hint: "Review once so it sticks.", list: rows.filter((r) => r.right && !r.sure) },
    { key: "solid", tone: "good", title: "Solid", hint: "Right, and you were sure.", list: rows.filter((r) => r.right && r.sure) },
  ].filter((g) => g.list.length);
  return (
    <div className="grade-breakdown" aria-label="How sure you were, compared with your score">
      <strong>Where to focus</strong>
      {groups.map((g) => (
        <div key={g.key} className={`grade-row ${g.tone}`}>
          <span className="grade-title">{g.title}</span>
          <span className="grade-qs">{nums(g.list).map((n) => `Q${n}`).join(", ")}</span>
          <span className="grade-hint">{g.hint}</span>
        </div>
      ))}
    </div>
  );
}

// Make the "why you chose that" paragraph stand out from the rest of the explanation.
const highlightChoice = (text) => String(text || "").replace(/^You chose \*\*(.+?)\*\*\.\s*/gm, "**Why you chose “$1”:** ");

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

// ---------- Guided Study (region + topic + specimen -> one focused question) ----------

const REGIONS = ["Head & Neck", "Thorax", "Abdomen", "Pelvis", "Upper Limb", "Lower Limb", "Back"];
const TOPICS = ["Muscles & Functions", "Nerves & Blood Supply", "Dissection Landmarks", "Anatomical Relationships"];
const SPECIMENS = ["Typical", "Thin", "Muscular", "Obese", "Elderly"];

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
  const word = specimen.toLowerCase();
  const article = /^[aeiou]/.test(word) ? "an" : "a";
  const variation = specimen === "Typical" ? ""
    : ` Then describe how the anatomy might appear in ${article} ${word} specimen, separating typical anatomy from possible variations.`;
  return `${questions[topic]}${variation} Use the course lecture notes as the primary reference. `
    + "Explain clearly with headings and bullet points. Start any part that is not in the notes with “General knowledge:”.";
}

// What the student sees: a plain question, not the instructions sent to the model.
function studyLine(region, topic, specimen) {
  const area = region === "Head & Neck" ? "head and neck" : region.toLowerCase();
  const lines = {
    "Muscles & Functions": `What are the major muscles of the ${area}, what do they attach to, and what do they do?`,
    "Nerves & Blood Supply": `Which nerves and blood vessels run through the ${area}, and what do they supply?`,
    "Dissection Landmarks": `Which landmarks should I find in the ${area} during the lab, and what lies next to each?`,
    "Anatomical Relationships": `How are the main structures of the ${area} arranged relative to each other?`,
  };
  const word = specimen.toLowerCase();
  const extra = specimen === "Typical" ? "" : ` Also show how ${/^[aeiou]/.test(word) ? "an" : "a"} ${word} specimen may differ.`;
  return `${lines[topic]}${extra}`;
}

function GuidedStudySetup({ onStart, onFreeChat, disabled }) {
  const [region, setRegion] = useState("");
  const [topic, setTopic] = useState(TOPICS[0]);
  const [specimen, setSpecimen] = useState(SPECIMENS[0]);

  return (
    <div className="study-setup">
      <div className="study-eyebrow">GUIDED ANATOMY STUDY</div>
      <h1>Build your study session</h1>
      <p className="study-intro">Choose what you want to explore. Ask Inglis will prepare a focused question.</p>
      <button type="button" className="study-change-mode" onClick={onFreeChat} disabled={disabled}>
        ← Switch to free chat
      </button>
      <div className="study-form">
        <div className="study-step">
          <div className="study-step-heading"><span>01</span>Choose an anatomical region</div>
          <div className="study-options study-regions">
            {REGIONS.map((item) => (
              <button key={item} type="button" aria-pressed={region === item} disabled={disabled}
                className={`study-choice ${region === item ? "selected" : ""}`}
                onClick={() => setRegion(item)}>{item}</button>
            ))}
          </div>
        </div>
        <div className="study-step">
          <div className="study-step-heading"><span>02</span>Choose a learning topic</div>
          <div className="study-options study-topics">
            {TOPICS.map((item) => (
              <button key={item} type="button" aria-pressed={topic === item} disabled={disabled}
                className={`study-choice ${topic === item ? "selected" : ""}`}
                onClick={() => setTopic(item)}>{item}</button>
            ))}
          </div>
        </div>
        <div className="study-step">
          <label className="study-step-heading" htmlFor="study-specimen">
            <span>03</span>Specimen characteristics<em>(optional)</em>
          </label>
          <select id="study-specimen" className="study-select" value={specimen} disabled={disabled}
            onChange={(event) => setSpecimen(event.target.value)}>
            {SPECIMENS.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
        </div>
        <div className="study-preview">
          <div className="study-preview-label">YOUR QUESTION</div>
          <p>{region ? studyLine(region, topic, specimen)
            : "Select an anatomical region to preview your study question."}</p>
        </div>
        <button className="study-start" type="button" disabled={!region || disabled}
          onClick={() => onStart({ region, topic, specimen })}>Start guided study →</button>
      </div>
    </div>
  );
}

// ---------- Anatomy look: logo mark (a stack of vertebrae) and the ECG line shown while thinking ----------

function BrandMark({ size = 22 }) {
  const bones = [
    { y: 2.5, w: 9 }, { y: 9.2, w: 11 }, { y: 15.9, w: 13 }, { y: 22.6, w: 15 },
  ];
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" role="img" aria-label="Spine logo" focusable="false">
      {bones.map(({ y, w }) => (
        <g key={y} fill="#f8f3e8">
          <rect x={16 - w / 2} y={y} width={w} height="5.4" rx="2.4" />
          <rect x={16 - w / 2 - 4.6} y={y + 1.5} width="5.4" height="2.4" rx="1.2" />
          <rect x={16 + w / 2 - 0.8} y={y + 1.5} width="5.4" height="2.4" rx="1.2" />
        </g>
      ))}
    </svg>
  );
}

function EcgLine() {
  return (
    <svg className="ecg" viewBox="0 0 64 20" aria-hidden="true" focusable="false">
      <path d="M0 10 H18 L22 10 L26 2 L31 18 L35 10 H64" />
    </svg>
  );
}

// ---------- Conversation names: short, readable, cut at a word (never mid-word) ----------

const SMALL_WORDS = new Set(["a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "vs", "with"]);

// The AI sometimes answers with a generic title like "Human Anatomy Study". Those say nothing, so we ignore them.
const GENERIC_TITLE = /^(a |an |the )?((human )?anatomy( study| chat| question| session| assistant| help)?|study( chat| session)?|anatomy lab|new conversation|conversation|chat)$/i;
const isGenericTitle = (t) => !t || GENERIC_TITLE.test(String(t).replace(/[….\s]+$/, "").trim());
const nameOf = (item) => {
  if (!isGenericTitle(item.title) && !/^Human Anatomy Stud/i.test(item.title)) return item.title;
  const first = item.messages?.find((m) => m.role === "user");
  return first ? smartTitle(first.content, first.mode) : "New conversation";
};

function smartTitle(text, mode) {
  const original = String(text || "").replace(/\s+/g, " ").trim();
  if (!original) return "New conversation";
  if (mode === "locate") {
    const region = original.match(/dissecting the ([^.]+)\./i)?.[1];
    const seen = original.replace(/^.*?what i can see:\s*/i, "").replace(/\.?\s*where am i.*$/i, "");
    const label = region ? region.replace(/\b\w/g, (c) => c.toUpperCase()) : seen.split(" ").slice(0, 4).join(" ");
    return `Where am I: ${label}`.slice(0, 34);
  }
  const isQuiz = /\b(quiz|test)\s+me\b|\bmcqs?\b|\bpractice (questions|quiz)\b/i.test(original);
  let t = original
    .replace(/[?.!:;,]+$/, "")
    .replace(/^(please|hey|hi)[,\s]+/i, "")
    .replace(/^(can|could|would) you\s+(please\s+)?/i, "")
    .replace(/^(quiz|test) me\s+(on|about)\s+/i, "")
    .replace(/^(give me|make me|write)\s+(some\s+|\d+\s+)?(practice\s+)?(quiz\s+)?(questions|mcqs?)\s+(on|about)\s+/i, "")
    .replace(/^(what|which|where|how|why|when)\s+(is|are|was|were|does|do|did|can|should)\s+/i, "")
    .replace(/^(tell me|explain|describe|show me|give me|find me|find)\s+(about\s+)?/i, "")
    .replace(/^(the|a|an)\s+/i, "")
    .replace(/\s+(do|does|work|mean|connect to)$/i, "")
    .replace(/[?.!:;,]+$/, "")
    .trim();
  if (!t) t = original;
  t = t.split(" ").map((word, i) => {
    if (word.length > 1 && word === word.toUpperCase()) return word;
    const lower = word.toLowerCase();
    return i > 0 && SMALL_WORDS.has(lower) ? lower : lower.charAt(0).toUpperCase() + lower.slice(1);
  }).join(" ");
  const prefix = mode === "dissect" ? "Dissect: " : mode === "images" ? "Diagram: " : isQuiz ? "Quiz: " : "";
  const room = 34 - prefix.length;
  if (t.length > room) {
    const cut = t.slice(0, room + 1);
    t = cut.slice(0, Math.max(cut.lastIndexOf(" "), 12)).replace(/[\s,;:(-]+$/, "");
    t = t.replace(/(\s+(a|an|and|at|by|for|from|in|of|on|or|the|to|vs|with))+$/i, "") + "…";
  }
  return prefix + t;
}

// ---------- Where am I?: describe what you see in words. Text only, never photos. ----------

function WhereAmI({ busy, onSubmit, onClose }) {
  const [region, setRegion] = useState("");
  const [text, setText] = useState("");
  const submit = () => {
    const description = text.trim();
    if (!description || busy) return;
    onSubmit({ region, description });
  };

  return (
    <section className="where-panel" aria-label="Where am I?">
      <div className="where-head">
        <strong>📍 Where am I?</strong>
        <button type="button" className="where-close" onClick={onClose} aria-label="Close Where am I">×</button>
      </div>
      <p className="where-note">
        Describe what you see in your own words: shape, colour, direction, what sits next to it.
        Please do not upload or paste photos of donors. They are protected.
      </p>
      <div className="where-row">
        <select value={region} onChange={(event) => setRegion(event.target.value)} aria-label="Region (optional)">
          <option value="">Region (optional)</option>
          {REGIONS.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
      </div>
      <textarea value={text} rows={3} maxLength={900} aria-label="What you can see"
        placeholder="e.g. A long strap-like muscle on the front of the upper arm, with a thick tendon near the elbow"
        onChange={(event) => setText(event.target.value)} />
      <div className="where-actions">
        <button type="button" className="send-button" disabled={!text.trim() || busy} onClick={submit}>Find my place</button>
      </div>
    </section>
  );
}

// ---------- Lab bench: a side panel that stays open next to the chat while the student is at the table ----------

const BENCH_KEY = "askInglisBench";

// "The Biceps Brachii?" and "biceps brachii" are the same topic, so they must give the same guide.
const normalizeTopic = (t) => String(t || "").toLowerCase().replace(/\s+/g, " ").replace(/[?.!]+$/, "").replace(/^(the|a|an)\s+/, "").trim();
const niceTopic = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);

// ---------- Due for review: steps flagged Revisit and quiz topics missed come back after 1, 3 and 7 days ----------
const DUE_KEY = "askInglisDue";
const DUE_DAYS = [1, 3, 7];
const DAY_MS = 86400000;

function readDue() {
  try {
    const list = JSON.parse(localStorage.getItem(DUE_KEY) || "[]");
    return Array.isArray(list)
      ? list.filter((d) => d && d.topic && Number.isFinite(d.ts) && d.stage < DUE_DAYS.length && Date.now() - d.ts < RETENTION_MS)
      : [];
  } catch {
    return [];
  }
}

function writeDue(list) {
  try { localStorage.setItem(DUE_KEY, JSON.stringify(list)); } catch { /* storage full or blocked */ }
}

function planBenchReview(topic, count) {
  const list = readDue().filter((d) => d.topic !== topic);
  if (count > 0) list.push({ topic, count, stage: 0, ts: Date.now() });
  writeDue(list);
}

function advanceBenchReview(topic) {
  writeDue(readDue().map((d) => (d.topic === topic ? { ...d, stage: d.stage + 1, ts: Date.now() } : d)));
}

// What is due today: lab steps to revisit, and quiz topics missed at least a day ago.
function dueNow(missed) {
  const now = Date.now();
  const days = (ts) => Math.max(1, Math.floor((now - ts) / DAY_MS));
  const bench = readDue()
    .filter((d) => now - d.ts >= DUE_DAYS[d.stage] * DAY_MS)
    .map((d) => ({ kind: "bench", topic: d.topic, count: d.count, days: days(d.ts) }));
  const byTopic = new Map();
  for (const m of missed) {
    const e = byTopic.get(m.topic) || { count: 0, ts: 0 };
    byTopic.set(m.topic, { count: e.count + 1, ts: Math.max(e.ts, m.ts) });
  }
  const quiz = [...byTopic]
    .filter(([, e]) => now - e.ts >= DAY_MS)
    .map(([topic, e]) => ({ kind: "quiz", topic, count: e.count, days: days(e.ts) }));
  return [...bench, ...quiz];
}

function loadBench() {
  try {
    const saved = JSON.parse(localStorage.getItem(BENCH_KEY) || "null");
    if (saved && saved.topic && saved.content && Date.now() - saved.ts < RETENTION_MS) return saved;
  } catch {
    /* no saved session */
  }
  return null;
}

const plainLine = (t, max) => {
  const line = String(t || "").split("\n").map((l) => l.replace(/[*`#>_]/g, "").replace(/^\s*[-•\d.)]+\s*/, "").trim()).find(Boolean) || "";
  return line.length > max ? `${line.slice(0, max - 1).replace(/\s+\S*$/, "")}…` : line;
};

// Built from the steps the student actually saw. No extra model call, so it always matches the guide.
function benchSummary(topic, steps, marks, asked, found) {
  const clear = steps.filter((_, i) => marks[i] === "ok" && !asked[i]).length;
  const lines = [`**Lab session: ${topic}**`, "", `${clear} of ${steps.length} steps felt clear.`, "", "**What we covered**"];
  steps.forEach((text, i) => {
    const needs = marks[i] === "help" || asked[i];
    const tag = needs ? "revisit" : marks[i] === "ok" ? "clear" : "skipped";
    const said = found[i] ? ` Where am I? said: ${plainLine(found[i], 110)}` : "";
    lines.push(`${i + 1}. ${plainLine(text, 110)} (${tag}).${said}`);
  });
  const revisit = steps.map((_, i) => i + 1).filter((n) => marks[n - 1] === "help" || asked[n - 1]);
  if (revisit.length) lines.push("", `**Revisit:** step ${revisit.join(", ")}`);
  return lines.join("\n");
}

function LabBench({ open, seed, onClose, onSave, onQuiz }) {
  const [saved] = useState(loadBench);
  const [topic, setTopic] = useState(saved?.topic || "");
  const [draft, setDraft] = useState("");
  const [content, setContent] = useState(saved?.content || "");
  const [lectures, setLectures] = useState(saved?.lectures || []);
  const [sources, setSources] = useState(saved?.sources || []);
  const [status, setStatus] = useState("idle");
  const [index, setIndex] = useState(saved?.index || 0);
  const [marks, setMarks] = useState(saved?.marks || []);
  const [asked, setAsked] = useState(saved?.asked || []);
  const [found, setFound] = useState(saved?.found || []);
  const [added, setAdded] = useState(false);
  const [finished, setFinished] = useState(false);
  const [whereOpen, setWhereOpen] = useState(false);
  const [overview, setOverview] = useState(false);
  const [locating, setLocating] = useState(false);
  const [located, setLocated] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [problem, setProblem] = useState("");

  const parts = useMemo(() => splitLayered(content) || splitSteps(content), [content]);
  const steps = parts?.steps || [];
  const extra = parts?.extras?.[index];

  useEffect(() => {
    try {
      if (content) localStorage.setItem(BENCH_KEY, JSON.stringify({ topic, content, lectures, sources, index, marks, asked, found, ts: Date.now() }));
    } catch {
      /* storage full or blocked */
    }
  }, [topic, content, lectures, sources, index, marks, asked, found]);

  useEffect(() => () => { if (CAN_SPEAK) window.speechSynthesis.cancel(); }, []);

  const start = async (raw) => {
    const wanted = normalizeTopic(raw);
    if (!wanted) return;
    setTopic(wanted); setStatus("loading"); setFinished(false); setLocated("");
    setIndex(0); setMarks([]); setAsked([]); setFound([]); setAdded(false); setContent(""); setProblem("");
    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: buildPayload("dissect", wanted), history: [], mode: "dissect" }),
      });
      if (!response.ok) throw new Error(`Lab bench request failed (${response.status})`);
      const data = await response.json();
      setContent(data.reply || "");
      setLectures(data.lectures || []);
      setSources(data.sources || []);
      setStatus("idle");
    } catch (error) {
      console.error(error);
      setStatus("error");
    }
  };

  useEffect(() => {
    if (!seed) return undefined;
    const id = window.setTimeout(() => start(seed.topic), 0);
    return () => window.clearTimeout(id);
  }, [seed]);

  const stopSpeech = () => { if (CAN_SPEAK) window.speechSynthesis.cancel(); setSpeaking(false); };
  const go = (next) => { stopSpeech(); setIndex(Math.max(0, Math.min(steps.length - 1, next))); };
  const speak = () => {
    if (speaking) { stopSpeech(); return; }
    const text = `Step ${index + 1}. ${steps[index].replace(/[*`]/g, "")}${extra?.see ? ` You should see: ${extra.see.replace(/[*`]/g, "")}` : ""}`;
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.cancel();
    setSpeaking(true);
    window.speechSynthesis.speak(utterance);
  };

  const finish = () => {
    stopSpeech();
    setFinished(true);
    planBenchReview(niceTopic(topic), steps.filter((_, i) => marks[i] === "help" || asked[i]).length);
  };

  const clear = () => {
    stopSpeech();
    setTopic(""); setContent(""); setLectures([]); setSources([]); setIndex(0); setMarks([]); setAsked([]); setFound([]); setAdded(false);
    setFinished(false); setLocated(""); setWhereOpen(false); setStatus("idle");
    try { localStorage.removeItem(BENCH_KEY); } catch { /* ignore */ }
  };

  const locate = async ({ region, description }) => {
    if (locating) return;
    setLocating(true); setProblem(""); setWhereOpen(false);
    setAsked((old) => { const next = [...old]; next[index] = true; return next; });
    const where = region ? `I am dissecting the ${region.toLowerCase()}. ` : `I am working on ${topic}. `;
    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: `${where}What I can see: ${description}. Where am I, and what should I do next?`, history: [], mode: "locate" }),
      });
      if (!response.ok) throw new Error(`Where am I request failed (${response.status})`);
      const data = await response.json();
      setLocated(data.reply || "");
      setFound((old) => { const next = [...old]; next[index] = data.reply || ""; return next; });
    } catch (error) {
      console.error(error);
      setProblem("Could not get an answer. Please check the connection and try again.");
    } finally {
      setLocating(false);
    }
  };

  const printChecklist = () => {
    const done = () => { document.body.classList.remove("printing-checklist"); window.removeEventListener("afterprint", done); };
    document.body.classList.add("printing-checklist");
    window.addEventListener("afterprint", done);
    window.setTimeout(() => window.print(), 80);
  };

  const mark = (value) => setMarks((old) => { const next = [...old]; next[index] = value; return next; });
  const gotIt = () => { mark("ok"); if (index < steps.length - 1) go(index + 1); else finish(); };
  const clearCount = steps.filter((_, i) => marks[i] === "ok" && !asked[i]).length;

  return (
    <>
    <aside className={`bench ${open ? "open" : ""}`} aria-label="Lab bench" aria-hidden={!open}>
      <div className="bench-head">
        <div>
          <strong>🔬 Lab bench</strong>
          <span>{niceTopic(topic) || "Your guide at the table"}</span>
        </div>
        <button type="button" className="bench-close" onClick={onClose} aria-label="Close lab bench">×</button>
      </div>

      <div className="bench-body">
        {!topic && status !== "loading" && (
          <div className="bench-start">
            <p>Type a structure or region. The guide stays here while you keep asking questions in the chat.</p>
            <input value={draft} maxLength={120} placeholder="e.g. the anterior triangle of the neck"
              aria-label="Structure or region to dissect"
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") start(draft); }} />
            <button type="button" className="send-button" disabled={!draft.trim()} onClick={() => start(draft)}>Start</button>
          </div>
        )}

        {status === "loading" && <p className="bench-loading"><EcgLine /> Building your step-by-step guide…</p>}
        {status === "error" && (
          <div className="bench-error" role="alert">
            Could not build the guide. Check the connection and try again.
            <button type="button" onClick={() => start(topic)}>Try again</button>
          </div>
        )}

        {topic && status === "idle" && !parts && content && (
          <div className="bench-plain">
            <FormattedAnswer content={content} />
            <button type="button" className="step-link" onClick={clear}>Start a different structure</button>
          </div>
        )}

        {parts && !finished && (
          <>
            {parts.head && <div className="bench-head-text"><FormattedAnswer content={parts.head} /></div>}
            {overview ? (
              <div className="bench-overview">
                <div className="bench-overview-head">
                  <strong>All {steps.length} steps</strong>
                  <button type="button" onClick={() => setOverview(false)}>Back to step {index + 1}</button>
                </div>
                <ol>
                  {steps.map((text, i) => {
                    const needs = marks[i] === "help" || asked[i];
                    return (
                      <li key={i}>
                        <button type="button" className={`ov-item ${i === index ? "current" : ""} ${needs ? "slow" : ""}`}
                          onClick={() => { go(i); setOverview(false); }}>
                          <span className="ov-num">{i + 1}</span>
                          <span className="ov-text">{formatInline(text)}</span>
                          <span className="ov-tag">{needs ? "Revisit" : marks[i] === "ok" ? "Clear" : i === index ? "Here" : ""}</span>
                        </button>
                      </li>
                    );
                  })}
                </ol>
              </div>
            ) : (<>
            <div className="bench-step">
              <div className="step-count">Step {index + 1} of {steps.length}</div>
              <div className="step-bar"><span style={{ width: `${((index + 1) / steps.length) * 100}%` }} /></div>
              <p className="step-text">{formatInline(steps[index])}</p>
              {extra && (extra.see || extra.careful || extra.stuck) && (
                <div className="step-extras">
                  {extra.see && <div className="step-extra see"><b>👁 You should see</b><span>{formatInline(extra.see)}</span></div>}
                  {extra.careful && <div className="step-extra careful"><b>⛔ Do not cut</b><span>{formatInline(extra.careful)}</span></div>}
                  {extra.stuck && <div className="step-extra stuck"><b>🔎 Can’t find it?</b><span>{formatInline(extra.stuck)}</span></div>}
                </div>
              )}
            </div>

            <div className="bench-marks" role="group" aria-label="How is this step going?">
              <button type="button" className={`mark-help ${marks[index] === "help" ? "on" : ""}`}
                aria-pressed={marks[index] === "help"} onClick={() => { mark("help"); setWhereOpen(true); }}>🆘 Need help</button>
              <button type="button" className={`mark-ok ${marks[index] === "ok" ? "on" : ""}`}
                aria-pressed={marks[index] === "ok"} onClick={gotIt}>
                {index < steps.length - 1 ? "✅ Got it, next →" : "✅ Got it, finish"}
              </button>
            </div>

            <div className="step-actions">
              <button type="button" disabled={index === 0} onClick={() => go(index - 1)}>← Back</button>
              {CAN_SPEAK && <button type="button" onClick={speak}>{speaking ? "■ Stop" : "🔊 Read"}</button>}
              {index < steps.length - 1
                ? <button type="button" onClick={() => go(index + 1)}>Skip →</button>
                : <button type="button" onClick={finish}>Finish ✓</button>}
            </div>
            </>)}

            <div className="bench-tools">
              <button type="button" aria-pressed={overview} onClick={() => setOverview((o) => !o)}>☰ All steps</button>
              <button type="button" onClick={printChecklist}>🖨 Print checklist</button>
              <button type="button" onClick={finish}>Finish session</button>
            </div>

            {whereOpen && <WhereAmI busy={locating} onClose={() => setWhereOpen(false)} onSubmit={locate} />}
            {locating && <p className="bench-loading"><EcgLine /> Matching what you see to the notes…</p>}
            {problem && <p className="where-problem" role="alert">{problem}</p>}
            {located && (
              <div className="bench-located">
                <div className="bench-located-head">
                  <strong>📍 Where you are</strong>
                  <button type="button" onClick={() => setLocated("")} aria-label="Dismiss">×</button>
                </div>
                <FormattedAnswer content={located} />
              </div>
            )}
            {parts.tail && <div className="bench-tail"><FormattedAnswer content={parts.tail} /></div>}
            {lectures.length > 0 && <p className="bench-source">From: {lectures.join(", ")}</p>}
          </>
        )}

        {parts && finished && (
          <div className="bench-done">
            <h3>Session summary</h3>
            <p className="bench-total">{clearCount} of {steps.length} steps felt clear</p>
            <ol className="bench-pace">
              {steps.map((text, i) => {
                const needs = marks[i] === "help" || asked[i];
                return (
                  <li key={i} className={needs ? "slow" : ""}>
                    <span className="pace-label">Step {i + 1}</span>
                    <span className="pace-text">{formatInline(text)}</span>
                    <span className="pace-tag">{needs ? "Revisit" : marks[i] === "ok" ? "Clear" : "Skipped"}</span>
                  </li>
                );
              })}
            </ol>
            <div className="bench-done-actions">
              <button type="button" className="primary" disabled={added}
                onClick={() => { onSave(benchSummary(niceTopic(topic), steps, marks, asked, found), lectures, sources); setAdded(true); }}>
                {added ? "Added to your chat ✓" : "Add summary to chat"}
              </button>
              <button type="button" onClick={() => onQuiz(niceTopic(topic))}>📝 Quiz me on this structure</button>
              <button type="button" onClick={() => { setFinished(false); setIndex(0); }}>Back to steps</button>
              <button type="button" onClick={clear}>New structure</button>
            </div>
          </div>
        )}
      </div>
      {topic && !finished && parts && (
        <button type="button" className="step-link bench-new" onClick={clear}>Start a different structure</button>
      )}
    </aside>
    {parts && createPortal(
      <div className="checklist-print" aria-hidden="true">
        <h1>Dissection checklist</h1>
        <h2>{niceTopic(topic)}</h2>
        <p className="cl-meta">{new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })} · Ask Inglis</p>
        {parts.head && <p className="cl-head">{plainLine(parts.head, 240)}</p>}
        <ol>
          {steps.map((text, i) => {
            const ex = parts.extras?.[i];
            return (
              <li key={i}>
                <span className="cl-box" />
                <div>
                  <p className="cl-step">{formatInline(text)}</p>
                  {ex?.see && <p className="cl-note"><b>You should see:</b> {formatInline(ex.see)}</p>}
                  {ex?.careful && <p className="cl-note"><b>Do not cut:</b> {formatInline(ex.careful)}</p>}
                  {ex?.stuck && <p className="cl-note"><b>Can’t find it?</b> {formatInline(ex.stuck)}</p>}
                </div>
              </li>
            );
          })}
        </ol>
        {lectures.length > 0 && <p className="cl-foot">From: {lectures.join(", ")}. A study aid only. Follow your instructor and the lab rules.</p>}
      </div>,
      document.body
    )}
    </>
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
  const [readingId, setReadingId] = useState(null);
  useEffect(() => () => { if (CAN_SPEAK) window.speechSynthesis.cancel(); }, []);
  const [reviewed, setReviewed] = useState({});
  const [editingId, setEditingId] = useState(null);
  const [undo, setUndo] = useState(null);
  const [printIds, setPrintIds] = useState(null);
  const undoTimer = useRef(null);
  const inputRef = useRef(null);
  const [benchOpen, setBenchOpen] = useState(false);
  const [benchSeed, setBenchSeed] = useState(null);
  const [missed, setMissed] = useState(readMissed);
  const [greeting] = useState(() => {
    const hour = new Date().getHours();
    return hour < 5 ? "Hello" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  });
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
    // The welcome screen starts at the top; a conversation follows the newest message.
    if (messages.length === 0) messagesEndRef.current?.parentElement?.scrollTo({ top: 0 });
    else messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, loadingId, activeId]);

  const selectConversation = (id) => {
    // Viewing an old chat does not reset the seven-day countdown.
    setActiveId(id);
    setMode((m) => (m === "guided" ? "ask" : m));
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
      const firstMessage = nextMessages.find((msg) => msg.role === "user");
      const firstQuestion = firstMessage?.content;
      return {
        ...item,
        messages: nextMessages,
        title: item.title === "New conversation" && firstQuestion
          ? smartTitle(firstQuestion, firstMessage.mode) : item.title,
        lastUsedAt: Date.now(),
      };
    }));
  };

  const generateTitle = async (conversationId, firstQuestion, titleMode) => {
    const fallbackTitle = smartTitle(firstQuestion, titleMode === "ask" ? undefined : titleMode);
    const hint = titleMode === "dissect" ? "Dissection steps for: "
      : titleMode === "locate" ? "Locating a structure in the dissection lab: "
      : titleMode === "images" ? "Diagram search: " : "";
    try {
      const response = await fetch(`${API_URL}/title`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: hint + firstQuestion }),
      });
      if (!response.ok) throw new Error(`Title request failed (${response.status})`);
      const data = await response.json();
      const title = typeof data.title === "string"
        ? data.title.trim().slice(0, 60) : "";
      if (isGenericTitle(title) || /^Human Anatomy Stud/i.test(title)) return;
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

  const ask = async (overrideText, overrideMode, skipTitle = false, apiMode = "", shownText = "", meta = null) => {
    const text = (typeof overrideText === "string" ? overrideText : question).trim();
    const picked = overrideMode || mode;
    const activeMode = picked === "guided" ? "ask" : picked;
    if (!text || !current || loadingId) return;
    const payload = apiMode === "locate" ? text : buildPayload(activeMode, text);
    const storeMode = apiMode === "locate" ? "locate" : activeMode === "ask" ? undefined : activeMode;
    setLoadingText(
      apiMode === "locate" ? "Matching what you see to the notes…"
        : /\bquiz questions\b|\banother set\b/i.test(payload) ? "Writing and checking questions. This takes about 20 to 40 seconds…"
        : PICKS_ONLY.test(payload) || /\banswers?\b/i.test(payload) ? "Checking your answers…"
        : activeMode === "dissect" ? "Finding the dissection steps…"
        : "Thinking…"
    );
    const conversationId = current.id;
    const cut = editingId && typeof overrideText !== "string"
      ? current.messages.findIndex((m) => m.id === editingId) : -1;
    const previous = cut >= 0 ? current.messages.slice(0, cut) : [...current.messages];
    setEditingId(null);
    const history = previous.slice(-CONTEXT_MESSAGES).map(({ role, content }) => ({ role, content }));
    updateMessages(conversationId, (old) => [
      ...(cut >= 0 ? old.slice(0, cut) : old),
      { id: crypto.randomUUID(), role: "user", content: shownText || text, mode: storeMode, ...(meta || {}) },
    ]);
    if (previous.length === 0 && !skipTitle) void generateTitle(conversationId, text, storeMode || activeMode);
    setQuestion("");
    setError("");
    setLoadingId(conversationId);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: payload, history, mode: activeMode === "dissect" ? (apiMode || "dissect") : undefined }),
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
          content: data.reply || "No answer returned.", question: shownText || text,
          lectures: data.lectures || [], images: data.images || [],
          sources: (data.sources || []).map((src) => String(src).slice(0, 700)),
          mode: apiMode === "locate" ? "locate" : activeMode === "dissect" ? "dissect" : undefined,
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
    setMode(message.mode === "images" ? "images" : "ask");
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

  // Study sheet: print (or "Save as PDF") one question and its answer, without the rest of the screen
  const printSheet = (index) => {
    const ids = [messages[index].id];
    if (messages[index - 1]?.role === "user") ids.unshift(messages[index - 1].id);
    setPrintIds(ids);
    window.setTimeout(() => window.print(), 120);
  };

  const stopReading = () => { if (CAN_SPEAK) window.speechSynthesis.cancel(); setReadingId(null); };
  const readAloud = (message) => {
    if (readingId === message.id) { stopReading(); return; }
    const utterance = new SpeechSynthesisUtterance(speakableText(message.content));
    utterance.onend = () => setReadingId(null);
    utterance.onerror = () => setReadingId(null);
    window.speechSynthesis.cancel();
    setReadingId(message.id);
    window.speechSynthesis.speak(utterance);
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

  const openBench = (topic) => {
    setBenchOpen(true);
    if (topic) setBenchSeed((old) => ({ topic, nonce: (old?.nonce || 0) + 1 }));
  };

  const saveBenchSummary = (summary, lectures = [], sources = []) => {
    if (!current) return;
    updateMessages(current.id, (old) => [...old, {
      id: crypto.randomUUID(), role: "assistant", content: summary, question: "",
      lectures, images: [], sources, local: true, feedback: null,
    }]);
  };

  const quizFromBench = (topic) => {
    if (window.matchMedia("(max-width: 900px)").matches) setBenchOpen(false);  // the bench covers the chat on a phone
    void ask(`Give me 5 quiz questions on ${topic}`, "ask");
  };

  const prefill = (text) => {
    setMode("ask");
    setQuestion(text);
  };

  // Welcome screen: where the student left off, and the stages of a lab day.
  const resume = [];
  if (messages.length === 0) {
    const bench = loadBench();
    const parts = bench ? (splitLayered(bench.content) || splitSteps(bench.content)) : null;
    if (parts?.steps?.length) {
      const total = parts.steps.length;
      const revisit = parts.steps.filter((_, i) => bench.marks?.[i] === "help" || bench.asked?.[i]).length;
      resume.push({
        key: "bench", icon: "🔬", title: niceTopic(bench.topic),
        detail: `Lab bench · step ${Math.min((bench.index || 0) + 1, total)} of ${total}${revisit ? ` · ${revisit} to revisit` : ""}`,
        go: () => openBench(),
      });
    }
    const due = dueNow(missed);
    due.slice(0, 3).forEach((d) => {
      const when = `${d.days} day${d.days > 1 ? "s" : ""} ago`;
      resume.push(d.kind === "bench"
        ? { key: `due-b-${d.topic}`, icon: "⏰", title: niceTopic(d.topic), tag: "Due for review",
            detail: `Lab bench · ${d.count} step${d.count > 1 ? "s" : ""} to revisit · from ${when}`,
            go: () => { advanceBenchReview(d.topic); openBench(d.topic); } }
        : { key: `due-q-${d.topic}`, icon: "⏰", title: niceTopic(d.topic), tag: "Due for review",
            detail: `Quiz · ${d.count} missed · ${when}`,
            go: () => ask(`Give me 5 quiz questions on ${d.topic}`, "ask") });
    });
    if (missedTopics.length && !due.some((d) => d.kind === "quiz")) {
      const top = missedTopics[0];
      resume.push({
        key: "missed", icon: "📝", title: `Review what I missed: ${top.topic}`,
        detail: `${missedTopics.length} topic${missedTopics.length > 1 ? "s" : ""} from past quizzes`,
        go: () => ask(`Give me 5 quiz questions on ${top.topic}`, "ask"),
      });
    }
    const last = conversations.find((c) => c.id !== current?.id && c.messages.length > 0);
    if (last) {
      resume.push({ key: "chat", icon: "💬", title: nameOf(last), detail: "Your last conversation", go: () => selectConversation(last.id) });
    }
    resume.sort((x, y) => (y.tag ? 1 : 0) - (x.tag ? 1 : 0));  // due items first
    resume.splice(4);
  }
  const labDay = [
    { title: "In class", hint: "Understand it first", actions: [
      { icon: "🫀", label: "Ask about a structure", go: () => prefill("What does the biceps brachii do?") },
      { icon: "🖼️", label: "Find a diagram", go: () => setMode("images") },
    ] },
    { title: "Before lab", hint: "Plan your session", actions: [
      { icon: "🧭", label: "Guided study", go: () => chooseMode("guided") },
    ] },
    { title: "At the table", hint: "Dissect with a guide", actions: [
      { icon: "🔬", label: "Open the lab bench", go: () => openBench() },
    ] },
    { title: "After lab", hint: "Check what stuck", actions: [
      { icon: "📝", label: "Quiz me", go: () => prefill("Quiz me on the brachium") },
      { icon: "🔁", label: "Review what I missed", hide: missedTopics.length === 0,
        go: () => ask(`Give me 5 quiz questions on ${missedTopics[0].topic}`, "ask") },
    ] },
  ];

  // Guided tab on a chat that already has messages starts a fresh conversation.
  const chooseMode = (id) => {
    if (id === "guided" && messages.length > 0) newConversation();
    setMode(id);
  };

  const startGuided = (options) => {
    if (!current || loadingId) return;
    const id = current.id;
    setConversations((old) => old.map((item) => (item.id === id
      ? { ...item, title: `${options.region}: ${options.topic}`, studyContext: options } : item)));
    setMode("ask");
    void ask(buildStudyQuestion(options.region, options.topic, options.specimen), "ask", true, "", studyLine(options.region, options.topic, options.specimen));
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
          <span className="brand-symbol"><BrandMark /></span>
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
                onClick={() => selectConversation(item.id)} title={nameOf(item)}>
                {nameOf(item)}
              </button>
              <button className="delete-chat" type="button" title="Delete conversation"
                aria-label={`Delete ${nameOf(item)}`} onClick={() => deleteConversation(item.id)}>
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
        backgroundImage: `linear-gradient(90deg, rgba(245,247,250,.88), rgba(245,247,250,.93)), url("${import.meta.env.BASE_URL}anatomy-background.webp")`,
      }}>
        <header className="topbar">
          <strong>Anatomy AI Study Assistant</strong>
          <div className="mode-switch" role="tablist" aria-label="Mode">
            {MODES.map((m) => (
              <button type="button" role="tab" key={m.id} aria-selected={mode === m.id}
                className={mode === m.id ? "active" : ""} onClick={() => chooseMode(m.id)}>
                <span aria-hidden="true">{m.icon}</span> {m.label}
              </button>
            ))}
          </div>
          <button type="button" className="bench-toggle" aria-pressed={benchOpen}
            onClick={() => (benchOpen ? setBenchOpen(false) : openBench())}>🔬 Lab bench</button>
        </header>
        {mode === "images" ? (
          <DiagramsTab onAsk={(caption) => { setMode("ask"); setQuestion(`Explain ${caption}`); }} />
        ) : (<>
        <section className="messages" aria-live="polite">
          <div className="print-header">
            <strong>Ask Inglis study sheet</strong>
            <span>{new Date().toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}</span>
          </div>
          {messages.length === 0 && mode === "guided" && (
            <GuidedStudySetup onStart={startGuided} onFreeChat={() => setMode("ask")}
              disabled={Boolean(loadingId)} />
          )}
          {messages.length === 0 && mode !== "guided" && (
            <div className="welcome lab-welcome">
              <div className="welcome-top">
                <div className="welcome-emblem"><BrandMark size={34} /></div>
                <div className="plate-label">Human anatomy study</div>
              </div>
              <h1>{greeting}. What would you like to do?</h1>
              <p>Every answer comes from Dr. Inglis's lecture notes. Pick a stage of your lab day, or just type below.</p>

              {resume.length > 0 && (
                <div className="resume" aria-label="Continue where you left off">
                  <div className="resume-label">Continue where you left off</div>
                  {resume.map((r) => (
                    <button type="button" className="resume-item" key={r.key} disabled={Boolean(loadingId)} onClick={r.go}>
                      <span aria-hidden="true">{r.icon}</span>
                      <span className="resume-text"><strong>{r.title}{r.tag && <em className="due-tag">{r.tag}</em>}</strong><small>{r.detail}</small></span>
                      <span className="resume-go" aria-hidden="true">→</span>
                    </button>
                  ))}
                </div>
              )}

              <ol className="lab-day" aria-label="Your lab day">
                {labDay.map((stage, n) => (
                  <li className="lab-stage" key={stage.title}>
                    <span className="stage-num" aria-hidden="true">{n + 1}</span>
                    <strong>{stage.title}</strong>
                    <small>{stage.hint}</small>
                    <div className="stage-actions">
                      {stage.actions.filter((a) => !a.hide).map((a) => (
                        <button type="button" key={a.label} onClick={a.go}>{a.icon} {a.label}</button>
                      ))}
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          )}
          {current?.studyContext && messages.length > 0 && (
            <div className="study-context-bar">
              <span>🧭 Guided study</span>
              <span>{current.studyContext.region}</span>
              <span>{current.studyContext.topic}</span>
              {current.studyContext.specimen !== "Typical" && <span>{current.studyContext.specimen} specimen</span>}
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
                        onSubmit={(picks, confidence) => ask(picks, "ask", false, "", "", { confidence })}
                        onShowAnswers={() => ask("show answers", "ask")} />
                    : <>
                        {message.understoodAs && (
                          <p className="understood">Showing answers for “{message.understoodAs}”</p>
                        )}
                        {message.mode === "dissect"
                          ? <StepViewer content={message.content} messageId={message.id} />
                          : isGraded
                            ? <>
                                <GradeBreakdown reply={message.content} confidence={messages[index - 1]?.confidence} />
                                <FormattedAnswer content={highlightChoice(message.content)} />
                              </>
                            : <FormattedAnswer content={message.content} />}
                        {message.images?.[0]?.url && <Figure image={message.images[0]} />}
                      </>)
                  : <div className="user-text">
                      {message.mode && <span className="mode-badge">{message.mode === "quiz" ? "Quiz" : message.mode === "locate" ? "Where am I?" : "Dissection"}</span>}
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
                    {!message.local && message.degraded && (
                      <p className="notice">The assistant is temporarily using retrieved note excerpts.</p>
                    )}
                    {(!message.local || message.lectures?.length > 0) && <SourcePanel lectures={message.lectures} sources={message.sources} reviewed={reviewed} />}
                    <div className="feedback-actions">
                      {/* A lab summary is the student's own note, not an answer from the course notes, so no thumbs: they would reach the professor's review queue. */}
                      {!message.local && (
                        <>
                          <button type="button" title="Helpful" aria-label="Helpful"
                            disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                            onClick={() => submitFeedback(message, "up")}>👍</button>
                          <button type="button" title="Not helpful" aria-label="Not helpful"
                            disabled={Boolean(message.feedback) || Boolean(feedbackPendingId)}
                            onClick={() => { setFeedbackTarget(message); setFeedbackComment(""); }}>👎</button>
                        </>
                      )}
                      {CAN_SPEAK && !message.local && !quiz && !isGraded && !message.mode && (
                        <button type="button" title={readingId === message.id ? "Stop reading" : "Read answer aloud"}
                          aria-label={readingId === message.id ? "Stop reading" : "Read answer aloud"}
                          aria-pressed={readingId === message.id}
                          onClick={() => readAloud(message)}>{readingId === message.id ? "■" : "🔊"}</button>
                      )}
                      <button type="button" title="Copy answer" aria-label="Copy answer"
                        onClick={() => copyAnswer(message)}>
                        {copiedId === message.id ? "✓" : "⧉"}
                      </button>
                      <button type="button" title="Print or save as a study sheet" aria-label="Print or save as a study sheet"
                        onClick={() => printSheet(index)}>🖨</button>
                      {message.feedback && <span>Feedback submitted</span>}
                    </div>
                    {isLatest && !message.local && !message.degraded && !quiz && (
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
                            <button type="button" onClick={() => openBench(topic)}>
                              🔬 Take this to the lab
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
                <span className="thinking"><EcgLine />{loadingText}</span>
              </article>
            </div>
          )}
          {error && <div className="chat-error" role="alert">{error}</div>}
          <div ref={messagesEndRef} />
        </section>
        {!(mode === "guided" && messages.length === 0) && (
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
              placeholder={MODES.find((m) => m.id === mode).placeholder}
              aria-label="Ask an anatomy question" value={question} maxLength={2000}
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault(); ask();
                }
              }}
              disabled={Boolean(loadingId)} rows={2} />
            <button type="button" className="send-button"
              disabled={!question.trim() || Boolean(loadingId)} onClick={() => ask()}>Send</button>
          </div>
          <small>Enter to send · Shift + Enter for a new line</small>
        </div>
        )}
        </>)}
      </main>
      <LabBench open={benchOpen} seed={benchSeed} onClose={() => setBenchOpen(false)} onSave={saveBenchSummary} onQuiz={quizFromBench} />

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
