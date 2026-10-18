"""Fetching a calendar link that a user typed in, without letting it reach anything inside our own network.

A link is only fetched if:
  * it is http or https (webcal:// is treated as https), on the usual ports, with no user name or password in it;
  * EVERY address its host name resolves to is a public internet address (not private, loopback, link-local, a cloud
    metadata address, multicast...). The check runs on the addresses actually used for the connection, so a name that
    answers with a public address once and an internal one the next time (DNS rebinding) gets nowhere;
  * each redirect passes the same checks again, and there are at most three of them;
  * the answer is small (2 MB), arrives quickly, and really is a calendar."""
import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ALLOWED_PORTS = {80, 443}
USER_AGENT = "NearBnB-calendar-sync/1.0"
Resolver = Callable[[str, int], Awaitable[list[str]]]


class FeedError(Exception):
    """A problem with a calendar link, worded so it can be shown to the host as it is."""


@dataclass
class FetchResult:
    status: int            # 200, or 304 when nothing changed since the last fetch
    body: bytes = b""
    etag: str | None = None
    last_modified: str | None = None


def validate_url(raw: str) -> str:
    """Checks the link itself (no network). Returns the cleaned link."""
    url = (raw or "").strip()
    if not url or len(url) > 2000:
        raise FeedError("Paste the calendar link from the other site.")
    if url.lower().startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise FeedError("The link must start with https:// (or webcal://).")
    if not parts.hostname:
        raise FeedError("That does not look like a web address.")
    if parts.username or parts.password:
        raise FeedError("Links containing a user name or password are not accepted.")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise FeedError("That web address has an invalid port.") from None
    if port not in ALLOWED_PORTS:
        raise FeedError("Calendar links must use the standard web ports.")
    return url


def is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


async def system_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FeedError("That web address could not be found.") from None
    return list(dict.fromkeys(info[4][0] for info in infos))


async def public_addresses(host: str, port: int, resolver: Resolver) -> list[str]:
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
        addresses = [str(literal)]
    except ValueError:
        addresses = await resolver(host, port)
    if not addresses:
        raise FeedError("That web address could not be found.")
    if not all(is_public(a) for a in addresses):
        raise FeedError("That address is not a public website, so it cannot be used.")
    return addresses


def _netloc(address: str, port: int, scheme: str) -> str:
    host = f"[{address}]" if ":" in address else address
    default = 443 if scheme == "https" else 80
    return host if port == default else f"{host}:{port}"


async def fetch_feed(url: str, *, etag: str | None = None, last_modified: str | None = None,
                     transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver = system_resolver) -> FetchResult:
    current = validate_url(url)
    async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            port = parts.port or (443 if parts.scheme == "https" else 80)
            addresses = await public_addresses(parts.hostname, port, resolver)
            target = f"{parts.scheme}://{_netloc(addresses[0], port, parts.scheme)}{parts.path or '/'}" + (f"?{parts.query}" if parts.query else "")
            headers = {"Host": parts.netloc.rsplit("@", 1)[-1], "User-Agent": USER_AGENT, "Accept": "text/calendar, text/plain;q=0.9, */*;q=0.5"}
            if hop == 0 and etag:
                headers["If-None-Match"] = etag
            if hop == 0 and last_modified:
                headers["If-Modified-Since"] = last_modified
            # Connect to the address we just checked, and still present the real name for the certificate.
            extensions = {"sni_hostname": parts.hostname} if parts.scheme == "https" else {}
            try:
                async with client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FeedError("The calendar link redirects nowhere.")
                        current = validate_url(urljoin(current, location))
                        continue
                    if response.status_code == 304:
                        return FetchResult(304, etag=etag, last_modified=last_modified)
                    if response.status_code != 200:
                        raise FeedError(f"The other site answered with an error (HTTP {response.status_code}). Check that the link is still valid.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES:
                            raise FeedError("The calendar file is too large (over 2 MB).")
                    data = bytes(body)
                    if not re.match(rb"^\s*(\xef\xbb\xbf)?\s*BEGIN:VCALENDAR", data, re.IGNORECASE):
                        raise FeedError("That link did not return a calendar. Use the iCal (.ics) link from the other site.")
                    return FetchResult(200, data, response.headers.get("etag"), response.headers.get("last-modified"))
            except httpx.TimeoutException:
                raise FeedError("The other site took too long to answer.") from None
            except httpx.HTTPError:
                raise FeedError("Could not connect to the other site.") from None
        raise FeedError("The calendar link redirects too many times.")
