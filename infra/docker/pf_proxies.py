"""Trusted-proxy list for the API container.

The API sits behind exactly one proxy: the `web` container (Caddy) on the compose network.
Caddy decides the real client IP (its own `trusted_proxies` + `trusted_proxies_strict`) and
sends it to the API as a single-value X-Forwarded-For. The API must trust X-Forwarded-For only
from that hop — never from "*" (uvicorn then takes the left-most, client-controlled address).

TRUSTED_PROXIES for the API (ARCH §11, comma-separated IPs/CIDRs) may contain the token
``auto``: it expands to loopback + the subnets this container is directly attached to (the
compose networks: only our own containers live there; the API publishes no port).

    python /usr/local/lib/pf/pf_proxies.py "auto"           → 127.0.0.1/32,::1/128,172.18.0.0/16,…
    python /usr/local/lib/pf/pf_proxies.py "auto,10.0.0.5"  → … plus 10.0.0.5/32

Invalid entries and wildcards ("*", 0.0.0.0/0, ::/0) are rejected (exit 2) — trusting
everybody would let any client choose its IP (login rate limits, bans, session IPs).
The same list is passed to uvicorn ``--forwarded-allow-ips`` (ARCH §11: no other set).
"""

from __future__ import annotations

import ipaddress
import socket
import struct
import sys

LOOPBACK = ("127.0.0.1/32", "::1/128")
IPNet = ipaddress.IPv4Network | ipaddress.IPv6Network


def ipv4_connected(route_text: str) -> list[IPNet]:
    """Directly connected IPv4 subnets from /proc/net/route (gateway 0, destination != 0)."""
    nets: list[IPNet] = []
    for line in route_text.splitlines()[1:]:
        f = line.split()
        if len(f) < 8:
            continue
        try:
            dest, gw, mask = int(f[1], 16), int(f[2], 16), int(f[7], 16)
        except ValueError:
            continue
        if gw != 0 or dest == 0 or mask == 0:
            continue
        d = socket.inet_ntoa(struct.pack("<I", dest))
        m = socket.inet_ntoa(struct.pack("<I", mask))
        nets.append(ipaddress.ip_network(f"{d}/{m}", strict=False))
    return nets


def ipv6_connected(if_inet6_text: str) -> list[IPNet]:
    """Global/ULA IPv6 subnets from /proc/net/if_inet6 (skip loopback and link-local)."""
    nets: list[IPNet] = []
    for line in if_inet6_text.splitlines():
        f = line.split()
        if len(f) < 6:
            continue
        raw, plen, scope = f[0], f[2], f[3]
        if scope != "00" or len(raw) != 32:  # 00 = global scope
            continue
        try:
            addr = ipaddress.IPv6Address(":".join(raw[i : i + 4] for i in range(0, 32, 4)))
            nets.append(ipaddress.ip_network(f"{addr}/{int(plen, 16)}", strict=False))
        except ValueError:
            continue
    return nets


def _read(path: str) -> str:
    try:
        with open(path, encoding="ascii", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def expand(value: str, route_text: str | None = None, if_inet6_text: str | None = None) -> str:
    """Normalize a TRUSTED_PROXIES value; ``auto`` → loopback + connected subnets.

    Accepts commas and/or spaces as separators. Raises ValueError on garbage or wildcards.
    """
    out: list[str] = []

    def add(net: IPNet) -> None:
        if net.prefixlen == 0:
            raise ValueError(f"trusting every address ({net}) is not allowed")
        s = str(net)
        if s not in out:
            out.append(s)

    for item in value.replace(",", " ").split():
        if item == "*":
            raise ValueError('"*" is not allowed: list the proxy addresses or use "auto"')
        if item == "auto":
            for lb in LOOPBACK:
                add(ipaddress.ip_network(lb))
            rt = _read("/proc/net/route") if route_text is None else route_text
            i6 = _read("/proc/net/if_inet6") if if_inet6_text is None else if_inet6_text
            for net in ipv4_connected(rt) + ipv6_connected(i6):
                add(net)
            continue
        try:
            add(ipaddress.ip_network(item, strict=False))
        except ValueError as e:
            raise ValueError(f"bad TRUSTED_PROXIES entry {item!r}: {e}") from None
    if not out:
        for lb in LOOPBACK:
            add(ipaddress.ip_network(lb))
    return ",".join(out)


def main(argv: list[str]) -> int:
    value = argv[1] if len(argv) > 1 else "auto"
    try:
        print(expand(value))
    except ValueError as e:
        print(f"TRUSTED_PROXIES: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
