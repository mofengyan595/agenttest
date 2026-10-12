"""Small local-console boundary; not authentication for a public deployment."""

import re
from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
DEFAULT_ORIGINS = frozenset(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1", "[::1]")
    for port in (8000, 18000, 5173, 4173)
)


def loopback(host: str) -> bool:
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


class LocalAccessMiddleware:
    def __init__(self, app, allowed_origins=DEFAULT_ORIGINS):
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS
                    or parsed.username or parsed.password or parsed.path or parsed.query
                    or parsed.fragment):
                raise ValueError("Only explicit local origins are supported")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        peer = (scope.get("client") or ("", 0))[0]
        testing = peer == "testclient"
        hosts = headers.getlist("host")
        try:
            parsed = urlsplit("http://" + (hosts[0] if len(hosts) == 1 else ""))
            allowed = (r"(?:localhost|127\.0\.0\.1|\[::1\]"
                       + ("|testserver" if testing else "") + ")")
            host_ok = bool(len(hosts) == 1 and re.fullmatch(
                allowed + r"(?::[0-9]{1,5})?", hosts[0], re.I))
            host_ok = host_ok and (parsed.port is None or 1 <= parsed.port <= 65535)
        except ValueError:
            host_ok = False
        status, message = None, ""
        if not host_ok:
            status, message = 400, "Unexpected Host"
        elif not testing and not loopback(peer):
            status, message = 403, "Loopback clients only"
        elif headers.get("sec-fetch-site") == "cross-site":
            status, message = 403, "Cross-site browser request forbidden"
        elif headers.getlist("origin") and (
            len(headers.getlist("origin")) != 1 or
            headers["origin"] not in self.allowed_origins
        ):
            status, message = 403, "Unexpected Origin"
        if status:
            return await PlainTextResponse(message, status_code=status)(scope, receive, send)
        return await self.app(scope, receive, send)
