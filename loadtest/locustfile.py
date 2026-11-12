"""AskInglis load test: simulated students using the chat continuously.

Run (20 students, 5 minutes, results in the terminal):
  locust -f locustfile.py --headless -u 20 -r 2 -t 5m --host http://localhost:8000 --csv results

Or with the browser dashboard:  locust -f locustfile.py --host http://localhost:8000   then open http://localhost:8089

Environment variables
  FAKE_IPS=1      give every simulated student their own address (X-Forwarded-For), so your real per-address rate limits can
                  stay ON during the test, exactly as 40 students on 40 devices would look. Default on. Needs
                  TRUSTED_PROXY_HOPS=1 (the default). Use FAKE_IPS=0 to test one address hammering the server.
  UNIQUE=1        add a unique tag to every question so the answer cache cannot hide the real cost (default on)
  ADMIN_KEY=...   if set, an admin user also reads /admin/chatlog now and then
  THINK_MIN / THINK_MAX   seconds a student "reads" between actions (default 5 and 20)
Only CHATLOG_DIR needs changing for the test (point it at a throwaway folder so test questions do not fill the real
log). Leave the rate limits at their real values: each student has their own fake address.
"""
import os
import random
import uuid

from locust import HttpUser, between, task

UNIQUE = os.environ.get("UNIQUE", "1") != "0"
THINK_MIN = float(os.environ.get("THINK_MIN", "5"))
THINK_MAX = float(os.environ.get("THINK_MAX", "20"))
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
FAKE_IPS = os.environ.get("FAKE_IPS", "1") != "0"

QUESTIONS = [
    "What does the biceps brachii do?", "Which nerve supplies the deltoid?", "What are the branches of the brachial plexus?",
    "Where does the radial nerve run in the arm?", "What is the origin and insertion of the brachialis?",
    "How do I find the musculocutaneous nerve at the table?", "What are the boundaries of the carotid triangle?",
    "Which muscles make up the rotator cuff?", "What does the sternocleidomastoid do?", "What supplies the thyroid gland?",
    "Where does the vagus nerve run in the neck?", "What are the layers of the abdominal wall?",
    "What is the inguinal canal?", "Which arteries supply the heart?", "What is the cubital fossa?",
    "Name the muscles of the posterior forearm.", "What is the femoral triangle?", "What does the gluteus maximus do?",
    "How do I tell the sciatic nerve from the tibial nerve?", "What passes through the carpal tunnel?",
    "Explain the major muscles and their functions in the Head & Neck. Include important attachments and actions.",
    "Explain the major nerves and blood vessels in the Thorax, including the structures they supply.",
    "Describe how the anatomy of the Upper Limb might appear in a muscular specimen.",
]
TOPICS = ["biceps brachii", "brachial plexus", "carotid triangle", "rotator cuff", "inguinal canal", "femoral triangle"]
SEEN = [
    "I can see a long muscle with a white tendon on the front of the upper arm. Where am I, and what should I do next?",
    "I can see a large nerve running beside an artery near the armpit. Where am I, and what should I do next?",
    "I can see a flat triangular muscle over the shoulder. Where am I, and what should I do next?",
]


def tag(text):
    return f"{text} (ref {uuid.uuid4().hex[:6]})" if UNIQUE else text


class StudentBase(HttpUser):
    abstract = True
    wait_time = between(THINK_MIN, THINK_MAX)

    def on_start(self):
        self.session = uuid.uuid4().hex[:12]
        self.history = []
        if FAKE_IPS:
            self.client.headers["X-Forwarded-For"] = f"10.{random.randint(1, 250)}.{random.randint(0, 250)}.{random.randint(1, 250)}"

    def chat(self, message, name, mode="", history=None):
        body = {"message": message, "history": history or [], "session": self.session}
        if mode:
            body["mode"] = mode
        with self.client.post("/chat", json=body, name=name, catch_response=True, timeout=120) as r:
            if r.status_code != 200:
                r.failure(f"HTTP {r.status_code}")
                return None
            try:
                data = r.json()
            except ValueError:
                r.failure("not JSON")
                return None
            if not data.get("reply"):
                r.failure("empty reply")
                return None
            if data.get("degraded"):
                r.failure("degraded reply (AI unavailable)")
            return data


class Student(StudentBase):
    """The normal mix of student actions (this is the default test)."""

    @task(10)
    def ask_question(self):
        q = tag(random.choice(QUESTIONS))
        data = self.chat(q, "chat: first question")
        if data:
            self.history = [{"role": "user", "content": q}, {"role": "assistant", "content": data["reply"][:1500]}]
            self.client.post("/title", json={"message": q, "reply": data["reply"][:500]}, name="title")

    @task(5)
    def follow_up(self):
        if not self.history:
            return
        q = tag("Can you explain that more simply?")
        data = self.chat(q, "chat: follow-up", history=self.history)
        if data:
            self.history = (self.history + [{"role": "user", "content": q},
                                            {"role": "assistant", "content": data["reply"][:1500]}])[-8:]

    @task(3)
    def quiz(self):
        self.chat(tag(f"quiz me on the {random.choice(TOPICS)}"), "quiz: generate")

    @task(3)
    def lab_bench(self):
        self.chat(tag(f"Dissection guide for {random.choice(TOPICS)}"), "lab bench: steps", mode="dissect")

    @task(3)
    def where_am_i(self):
        self.chat(random.choice(SEEN), "where am i", mode="locate")

    @task(2)
    def translate(self):
        text = "**Biceps brachii flexes the elbow and supinates the forearm.**\n\n- Supplied by the **musculocutaneous nerve**"
        self.client.post("/translate", json={"text": tag(text), "target_language": random.choice(["Korean", "Spanish", "Vietnamese"])},
                         name="translate")

    @task(3)
    def glossary(self):
        self.client.post("/gloss", json={"term": random.choice(["elbow", "radioulnar", "musculocutaneous", "fascia", "tendon"]),
                                         "target_language": random.choice(["Korean", "Hindi", "Vietnamese"])}, name="glossary")

    @task(2)
    def images(self):
        self.client.get("/images", params={"query": random.choice(TOPICS)}, name="images")

    @task(1)
    def reviewed(self):
        self.client.get("/reviewed", name="reviewed badges")


class QuizStudent(StudentBase):
    """Quiz requests only. Run it with:  locust -f locustfile.py QuizStudent --headless -u 40 -r 4 -t 5m --host ...
    Use it to check quiz speed under load after changing the quiz settings."""

    @task
    def quiz(self):
        self.chat(tag(f"quiz me on the {random.choice(TOPICS)}"), "quiz: generate")


class Admin(HttpUser):
    """One optional instructor reading the log while students use the chat."""
    weight = 1 if ADMIN_KEY else 0
    wait_time = between(20, 40)

    @task
    def chatlog(self):
        self.client.get("/admin/chatlog", params={"days": 1, "limit": 100}, headers={"X-Admin-Key": ADMIN_KEY}, name="admin: chatlog")