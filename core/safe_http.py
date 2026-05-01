from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from django.conf import settings


class UnsafeOutboundRequest(ValueError):
    pass


@dataclass(frozen=True)
class OutboundPolicy:
    allowed_hosts: set[str]
    allow_private_networks: bool = False
    allow_redirects: bool = False


def _is_private_ip(ip: str) -> bool:
    try:
        value = ipaddress.ip_address(ip)
    except ValueError:
        return True

    return bool(
        value.is_private
        or value.is_loopback
        or value.is_link_local
        or value.is_multicast
        or value.is_reserved
        or value.is_unspecified
    )


def _host_allowed(hostname: str, allowed_hosts: set[str]) -> bool:
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    for allowed in allowed_hosts:
        allowed_norm = (allowed or "").strip().lower().rstrip(".")
        if not allowed_norm:
            continue
        if allowed_norm.startswith(".") and host.endswith(allowed_norm[1:]):
            return True
        if host == allowed_norm:
            return True
    return False


def _resolve_public_ips(hostname: str) -> set[str]:
    host = (hostname or "").strip()
    if not host:
        return set()
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeOutboundRequest("Unable to resolve outbound host.") from exc

    ips: set[str] = set()
    for family, _, _, _, sockaddr in infos:
        if family == socket.AF_INET:
            ips.add(sockaddr[0])
        elif family == socket.AF_INET6:
            ips.add(sockaddr[0])
    return ips


def validate_outbound_url(*, url: str, policy: OutboundPolicy) -> None:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeOutboundRequest("Outbound request blocked: invalid scheme.")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeOutboundRequest("Outbound request blocked: missing hostname.")

    if not _host_allowed(hostname, policy.allowed_hosts):
        raise UnsafeOutboundRequest("Outbound request blocked: host not allowlisted.")

    if policy.allow_private_networks:
        return

    allow_dev_local = bool(getattr(settings, "DEBUG", False)) and hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }
    if allow_dev_local:
        return

    for ip in _resolve_public_ips(hostname):
        if _is_private_ip(ip):
            raise UnsafeOutboundRequest(
                "Outbound request blocked: private network destination."
            )


def safe_request(
    method: str,
    url: str,
    *,
    policy: OutboundPolicy,
    timeout: float | tuple[float, float] = (5.0, 30.0),
    headers: dict | None = None,
    **kwargs,
) -> requests.Response:
    validate_outbound_url(url=url, policy=policy)

    request_headers = {"User-Agent": "MyChama"}
    if headers:
        request_headers.update(headers)

    allow_redirects = bool(kwargs.pop("allow_redirects", policy.allow_redirects))
    return requests.request(
        method=str(method).upper(),
        url=url,
        timeout=timeout,
        allow_redirects=allow_redirects,
        headers=request_headers,
        **kwargs,
    )

