# AskInglis backend. Same image runs on AWS, UB CaaS or your laptop.
# Build context: the backend/ folder.  Settings come only from environment variables (never baked in).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Libraries first, so rebuilds are fast when only code changes.
COPY requirements.txt .
# openpyxl (Excel export) and python-pptx (reads lecture slides) are installed here in case requirements.txt lacks them.
RUN pip install -r requirements.txt openpyxl python-pptx

# Run as a normal user, not root.
RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/chroma_store /app/chatlog \
 && chown -R app:app /app
USER app

# Download the small search model now, at build time. Without this the first start tries to
# fetch it from the internet, which fails on a locked-down network.
RUN python -c "from chromadb.utils import embedding_functions as e; e.DefaultEmbeddingFunction()(['warm up'])"

COPY --chown=app:app . .

EXPOSE 8000

# The app answers GET / when it is up ("ready" turns true once the note index is built).
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status==200 else 1)"

# ONE worker on purpose: the answer cache, rate limits and quiz gate live in memory,
# so several workers would each keep their own and the limits would stop working.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
