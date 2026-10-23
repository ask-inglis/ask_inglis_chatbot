import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# How many proxies sit in front of the app. Render adds one. On your first deploy,
# print request.headers.get("x-forwarded-for") and check this.
TRUSTED_PROXY_HOPS = int(os.environ.get("TRUSTED_PROXY_HOPS", "1"))


def allowed_origins() -> list[str]:
    """Websites allowed to call this API from a browser (ALLOWED_ORIGINS in .env)."""
    raw = os.environ.get("ALLOWED_ORIGINS", "")
    origins = [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]
    if not origins:
        print("WARNING: ALLOWED_ORIGINS is not set, so every website can call this API")
        return ["*"]
    print(f"Allowed origins: {origins}")
    return origins


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if parts:
        # Count from the right: entries further left can be faked by the caller
        return parts[-min(TRUSTED_PROXY_HOPS, len(parts))]
    return request.client.host if request.client else "unknown"


_hits: dict[tuple[str, str], deque] = defaultdict(deque)


def rate_limit(name: str, limit: int, window: int = 60):
    """At most `limit` requests per `window` seconds from one address. 0 turns it off."""

    def dependency(request: Request):
        if limit <= 0:
            return
        now = time.time()
        q = _hits[(name, client_ip(request))]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            wait = int(window - (now - q[0])) + 1
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(wait)},
            )
        q.append(now)
        if len(_hits) > 5000:  # drop idle addresses so memory can't grow forever
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
                _hits.pop(k, None)

    return dependency


_daily = {"day": None, "count": 0}


def daily_cap(limit: int):
    """At most `limit` requests per day across all users. 0 turns it off."""

    def dependency():
        if limit <= 0:
            return
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if _daily["day"] != today:
            _daily["day"], _daily["count"] = today, 0
        if _daily["count"] >= limit:
            raise HTTPException(
                status_code=429,
                detail="The study assistant has reached its daily limit. Please try again tomorrow.",
            )
        _daily["count"] += 1

    return dependency