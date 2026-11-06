from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

MAX_BYTES = 1_500_000


def _is_public(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        for info in socket.getaddrinfo(parsed.hostname, None):
            ip_value = ipaddress.ip_address(info[4][0])
            if ip_value.is_private or ip_value.is_loopback or ip_value.is_link_local or ip_value.is_reserved:
                return False
    except socket.gaierror:
        return False
    return True


async def fetch_text(url: str, max_chars: int = 6000) -> str | None:
    async with httpx.AsyncClient(timeout=10, follow_redirects=False, headers={"User-Agent": "EverythingLLM/1.0"}) as client:
        current_url = url
        for _ in range(3):
            if not _is_public(current_url):
                return None
            response = await client.get(current_url)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return None
                current_url = urljoin(current_url, location)
                continue
            if response.status_code != 200 or len(response.content) > MAX_BYTES:
                return None
            text = trafilatura.extract(response.text, include_comments=False)
            if not text:
                return None
            return text[:max_chars]
    return None
