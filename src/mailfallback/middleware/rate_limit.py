import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_RATE_LIMITED_PATHS = {
    "/api/auth/login": (10, 60),
    "/login": (10, 60),
    "/profile/password": (5, 60),
    "/api/sync/all": (3, 60),
    "/api/accounts": (5, 60),
    "/api/restore": (3, 60),
}

_RATE_LIMITED_PREFIXES = {
    "/api/sync/": (5, 60),
}

_counters: dict[str, list[float]] = defaultdict(list)


def reset_rate_limits():
    _counters.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST",):
            return await call_next(request)

        config = _RATE_LIMITED_PATHS.get(request.url.path)
        if not config:
            for prefix, prefix_config in _RATE_LIMITED_PREFIXES.items():
                if request.url.path.startswith(prefix):
                    config = prefix_config
                    break
        if not config:
            return await call_next(request)

        max_requests, window = config
        client_ip = request.client.host if request.client else "unknown"
        key = f"{client_ip}:{request.url.path}"

        now = time.monotonic()
        timestamps = _counters[key]
        _counters[key] = [t for t in timestamps if now - t < window]

        if len(_counters[key]) >= max_requests:
            return JSONResponse(
                {"detail": "Too many requests, please try again later"},
                status_code=429,
            )

        _counters[key].append(now)
        return await call_next(request)
