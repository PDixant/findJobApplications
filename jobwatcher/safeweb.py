"""Pathfinder reads only public websites.

Careers pages come from your company list, and a list someone shares could point anywhere, so every
page request is checked three ways:

1. Before sending: the address must be http or https, and its host must not be this computer or a
   private network (localhost, 10.x, 192.168.x, link-local, reserved, multicast and so on, in IPv4
   and IPv6). Names are looked up first, so a public-looking name that leads somewhere private is
   refused too.
2. On every redirect: requests sends each hop through the same check.
3. On connecting: the address the connection really reached is checked again, so a name that
   changes its answer between the check and the connection (DNS rebinding) is still refused.

Bodies are read up to a size limit, compressed ones included. A refused address raises
BlockedAddress with a plain reason, which ends up on the Companies screen.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
import zlib
from urllib.parse import urlsplit

from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

# Only the tests turn this on, to read the fake careers sites they run on this computer.
ALLOW_PRIVATE = os.environ.get("PATHFINDER_ALLOW_PRIVATE_SITES") == "1"

PRIVATE_SUFFIXES = (".localhost", ".local", ".localdomain", ".internal", ".intranet", ".lan", ".home.arpa", ".corp")
MAX_PAGE_BYTES = 5_000_000
MAX_JSON_BYTES = 30_000_000
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_CACHE_SECONDS = 300
_cache: dict = {}
_cache_lock = threading.Lock()


class BlockedAddress(Exception):
    """An address Pathfinder won't read. Deliberately not a requests or OS error, so nothing retries
    it or mistakes it for a site being down."""


class TooBig(Exception):
    """A response larger than Pathfinder reads."""


def is_public(ip) -> bool:
    """True for an address on the public internet."""
    try:
        ip = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        if ip.ipv4_mapped:
            return is_public(ip.ipv4_mapped)
        if ip.sixtofour:
            return is_public(ip.sixtofour)
        if ip in _NAT64:
            return is_public(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local
                                 or ip.is_unspecified or ip.is_private)


def _looked_up(host, port):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get((host, port))
        if hit and now - hit[0] < _CACHE_SECONDS:
            return hit[1]
    try:
        found = sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except (socket.gaierror, UnicodeError, OSError):
        found = []  # can't be looked up: the connection fails on its own, with the usual message
    with _cache_lock:
        _cache[(host, port)] = (now, found)
    return found


def address_problem(url, resolve=True):
    """Why this address can't be read, or None if it's fine."""
    if ALLOW_PRIVATE:
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return f"{url} isn't a web address Pathfinder can read."
    if parts.scheme not in ("http", "https"):
        return f"Pathfinder only reads http and https pages, not {parts.scheme or 'this'} addresses."
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return f"{url} has no site name."
    if parts.username or parts.password:
        return "Addresses with a user name or password in them aren't read."
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return None if is_public(literal) else (
            f"{host} is on this computer or a private network, and Pathfinder only reads public websites.")
    if host == "localhost" or host.endswith(PRIVATE_SUFFIXES) or "." not in host:
        return f"{host} is a name for this computer or a private network, and Pathfinder only reads public websites."
    if resolve:
        for ip in _looked_up(host, port or (443 if parts.scheme == "https" else 80)):
            if not is_public(ip):
                return (f"{host} leads to {ip}, an address on this computer or a private network, "
                        "and Pathfinder only reads public websites.")
    return None


class _CheckPeer:
    """Checks the address a connection really reached (direct connections only: through a proxy,
    the proxy is what's reached, and it does the looking up)."""

    def _new_conn(self):
        sock = super()._new_conn()
        if ALLOW_PRIVATE or getattr(self, "proxy", None) is not None:
            return sock
        try:
            peer = sock.getpeername()[0]
        except OSError:
            peer = None
        if peer and not is_public(peer):
            sock.close()
            raise BlockedAddress(f"{self.host} led to {peer}, an address on this computer or a private network, "
                                 "and Pathfinder only reads public websites.")
        return sock


class _HTTPConnection(_CheckPeer, HTTPConnection):
    pass


class _HTTPSConnection(_CheckPeer, HTTPSConnection):
    pass


class _HTTPPool(HTTPConnectionPool):
    ConnectionCls = _HTTPConnection


class _HTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _HTTPSConnection


class PublicOnlyAdapter(HTTPAdapter):
    """A requests adapter that refuses private addresses before sending, on every redirect (each hop
    is sent through here), and on connecting."""

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _HTTPPool, "https": _HTTPSPool}

    def send(self, request, **kwargs):
        proxied = bool(select_proxy(request.url, kwargs.get("proxies") or {}))
        problem = address_problem(request.url, resolve=not proxied)
        if problem:
            raise BlockedAddress(problem)
        return super().send(request, **kwargs)


def guard(session):
    """Make a requests session read only public websites."""
    adapter = PublicOnlyAdapter()
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def read_capped(response, limit):
    """The body, read up to limit bytes (after any compression is undone). TooBig past that."""
    body = bytearray()
    try:
        for chunk in response.iter_content(65536):
            body.extend(chunk)
            if len(body) > limit:
                raise TooBig(f"{urlsplit(response.url).hostname} sent more than {limit // 1_000_000} MB")
    finally:
        response.close()
    return bytes(body)


def gunzip_capped(raw, limit=20_000_000):
    """Undo gzip, stopping at limit bytes so a tiny file can't unpack into gigabytes."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = d.decompress(raw, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise TooBig(f"a compressed file unpacked to more than {limit // 1_000_000} MB")
    return out


__all__ = ["BlockedAddress", "TooBig", "is_public", "address_problem", "guard", "read_capped", "gunzip_capped",
           "PublicOnlyAdapter", "MAX_PAGE_BYTES", "MAX_JSON_BYTES"]
