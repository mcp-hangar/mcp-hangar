"""Parsers for /proc/net/tcp and ss -tnp output -- BSL 1.1 licensed.

Provides pure functions for extracting established TCP connections from
Linux kernel pseudo-files and socket-statistics output. Used by
DockerNetworkMonitor as the primary (ss) and fallback (/proc/net/tcp)
connection discovery mechanisms.

See enterprise/LICENSE.BSL for license terms.
"""

import socket
import struct

import structlog

logger = structlog.get_logger(__name__)


def parse_proc_net_tcp(content: str) -> list[tuple[str, int, str]]:
    """Parse /proc/net/tcp output into (host, port, protocol) tuples.

    Extracts only ESTABLISHED connections (kernel state ``01``) whose
    remote address is not on the loopback interface (127.x.x.x).

    The /proc/net/tcp format stores IP addresses as little-endian 32-bit
    hex values and ports as 16-bit hex values.

    Args:
        content: Raw text content of /proc/net/tcp.

    Returns:
        List of (destination_host, destination_port, "tcp") tuples for
        each established non-loopback connection.
    """
    if not content or not content.strip():
        return []

    connections: list[tuple[str, int, str]] = []
    lines = content.strip().splitlines()

    # Skip the header line
    for line in lines[1:]:
        fields = line.strip().split()
        if len(fields) < 4:
            continue

        state = fields[3]
        if state != "01":  # Only ESTABLISHED
            continue

        remote_addr = fields[2]
        try:
            host_hex, port_hex = remote_addr.split(":")
        except ValueError:
            logger.warning("proc_net_tcp_parse_error", line=line.strip())
            continue

        try:
            # IP is stored as little-endian 32-bit hex
            ip_int = int(host_hex, 16)
            ip_str = socket.inet_ntoa(struct.pack("<I", ip_int))
            port = int(port_hex, 16)
        except (ValueError, struct.error) as exc:
            logger.warning(
                "proc_net_tcp_decode_error",
                host_hex=host_hex,
                port_hex=port_hex,
                error=str(exc),
            )
            continue

        # Filter loopback
        if ip_str.startswith("127."):
            continue

        connections.append((ip_str, port, "tcp"))

    return connections


def parse_ss_output(content: str) -> list[tuple[str, int, str]]:
    """Parse ss -tnp output into (host, port, protocol) tuples.

    Extracts only ESTAB (established) connections whose peer address
    is not loopback (127.x.x.x, ::1).

    Handles both IPv4 addresses (``host:port``) and IPv6 bracket
    notation (``[host]:port``).

    Args:
        content: Raw text output of ``ss -tnp``.

    Returns:
        List of (destination_host, destination_port, "tcp") tuples for
        each established non-loopback connection.
    """
    if not content or not content.strip():
        return []

    connections: list[tuple[str, int, str]] = []

    for line in content.strip().splitlines():
        if not line.startswith("ESTAB"):
            continue

        parts = line.split()
        if len(parts) < 5:
            continue

        peer = parts[4]

        try:
            if peer.startswith("["):
                # IPv6 bracket notation: [::ffff:1.2.3.4]:443
                bracket_end = peer.index("]")
                host = peer[1:bracket_end]
                port = int(peer[bracket_end + 2 :])
            else:
                # IPv4: 93.184.216.34:443
                last_colon = peer.rfind(":")
                host = peer[:last_colon]
                port = int(peer[last_colon + 1 :])
        except (ValueError, IndexError) as exc:
            logger.warning("ss_output_parse_error", peer=peer, error=str(exc))
            continue

        # Filter loopback
        if host in ("127.0.0.1", "::1") or host.startswith("127."):
            continue

        connections.append((host, port, "tcp"))

    return connections
