from __future__ import annotations

import ipaddress
import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    ProxyHandler,
    Request,
    build_opener,
)

_USER_AGENT = "EverythingLLM/1.0"
_MAX_RESPONSE_BYTES = 20_000_000
_MAX_REDIRECTS = 5
_MAX_REQUESTS = 50
_MAX_TIMEOUT_SECONDS = 30
_REQUEST_COUNT = 0
_HOST_LAST_REQUEST: dict[str, float] = {}
_REQUEST_LOCK = threading.Lock()


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _validate_public_https_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Only public HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed.")
    try:
        port = parsed.port or 443
        addresses = {
            ipaddress.ip_address(info[4][0])
            for info in socket.getaddrinfo(parsed.hostname, port)
        }
    except (OSError, ValueError) as error:
        raise ValueError(f"Could not resolve public host {parsed.hostname!r}.") from error
    if not addresses or any(not address.is_global for address in addresses):
        raise ValueError("Requests to private, local, or reserved IP addresses are blocked.")


def _get(url: str, headers: dict[str, str] | None, timeout: int) -> tuple[bytes, str]:
    opener = build_opener(ProxyHandler({}), _NoRedirectHandler())
    current_url = url
    merged_headers = {"User-Agent": _USER_AGENT, **(headers or {})}

    for redirect_number in range(_MAX_REDIRECTS + 1):
        _validate_public_https_url(current_url)
        _throttle_request(urlsplit(current_url).hostname or "")
        request = Request(current_url, headers=merged_headers)
        try:
            response = opener.open(
                request,
                timeout=max(1, min(int(timeout), _MAX_TIMEOUT_SECONDS)),
            )
        except HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                raise
            location = error.headers.get("Location")
            error.close()
            if not location or redirect_number == _MAX_REDIRECTS:
                raise ValueError("The request exceeded the redirect limit or had no Location.") from error
            current_url = urljoin(current_url, location)
            continue
        except URLError as error:
            raise ValueError(f"Request failed: {error.reason}") from error

        with response:
            content = response.read(_MAX_RESPONSE_BYTES + 1)
            content_type = response.headers.get("Content-Type", "")
        if not content:
            raise ValueError(f"Empty response from {current_url}.")
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ValueError("Response exceeds the 20 MB limit.")
        return content, content_type

    raise ValueError("The request exceeded the redirect limit.")


def _throttle_request(host: str) -> None:
    global _REQUEST_COUNT
    with _REQUEST_LOCK:
        if _REQUEST_COUNT >= _MAX_REQUESTS:
            raise ValueError("The task exceeded the 50-request web access limit.")
        now = time.monotonic()
        delay = 1.0 - (now - _HOST_LAST_REQUEST.get(host, 0.0))
        if delay > 0:
            time.sleep(delay)
        _REQUEST_COUNT += 1
        _HOST_LAST_REQUEST[host] = time.monotonic()


def get_text(
    url: str,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> str:
    if params:
        from urllib.parse import urlencode

        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params, doseq=True)}"
    content, _ = _get(url, headers, timeout)
    return content.decode("utf-8", errors="replace")


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
) -> dict | list:
    if params:
        from urllib.parse import urlencode

        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(params, doseq=True)}"
    content, content_type = _get(url, headers, timeout)
    if "json" not in content_type.lower():
        raise ValueError(f"Expected a JSON response, received {content_type or 'unknown content type'}.")
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("The server returned invalid JSON.") from error
    if not isinstance(value, (dict, list)):
        raise ValueError("The JSON response must be an object or array.")
    return value
