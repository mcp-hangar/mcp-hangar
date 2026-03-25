"""Tests for Docker network monitor: /proc/net/tcp and ss output parsers.

Tests cover:
- parse_proc_net_tcp: hex IP decoding, port extraction, state filtering, loopback filtering
- parse_ss_output: ESTAB line extraction, IPv6 bracket notation, loopback filtering
"""

import pytest


# ---------------------------------------------------------------------------
# Fixtures: realistic /proc/net/tcp and ss output
# ---------------------------------------------------------------------------

PROC_NET_TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"

# Fields: sl local_addr remote_addr state ...
# States: 01=ESTABLISHED, 0A=LISTEN, 06=TIME_WAIT
# IP format: little-endian hex. "0100007F" = 127.0.0.1, "2200A8C0" = 192.168.0.34
# Port format: hex. "01BB" = 443, "0050" = 80

PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL = (
    "   0: 0200A8C0:C350 2234D85E:01BB 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12345 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_LISTEN = (
    "   1: 00000000:0050 00000000:0000 0A 00000000:00000000 00:00000000 00000000"
    "     0        0 12346 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_TIME_WAIT = (
    "   2: 0200A8C0:C351 2234D85E:01BB 06 00000000:00000000 00:00000000 00000000"
    "     0        0 12347 1 0000000000000000 100 0 0 10 0"
)

PROC_NET_TCP_LOOPBACK = (
    "   3: 0100007F:C352 0100007F:0035 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12348 1 0000000000000000 100 0 0 10 0"
)

# Second established connection to a different destination: 10.0.0.1:80
# 10.0.0.1 in little-endian = 0100000A
PROC_NET_TCP_ESTABLISHED_SECOND = (
    "   4: 0200A8C0:C353 0100000A:0050 01 00000000:00000000 00:00000000 00000000"
    "     0        0 12349 1 0000000000000000 100 0 0 10 0"
)


SS_OUTPUT_HEADER = "State    Recv-Q Send-Q  Local Address:Port   Peer Address:Port  Process"

SS_OUTPUT_ESTAB = 'ESTAB    0      0      172.17.0.2:45678    93.184.216.34:443   users:(("python",pid=1,fd=5))'

SS_OUTPUT_LISTEN = "LISTEN   0      128    0.0.0.0:8080         0.0.0.0:*"

SS_OUTPUT_LOOPBACK = 'ESTAB    0      0      127.0.0.1:45679     127.0.0.1:5432   users:(("python",pid=1,fd=6))'

SS_OUTPUT_IPV6_LOOPBACK = 'ESTAB    0      0      [::1]:45680         [::1]:5432   users:(("python",pid=1,fd=7))'

SS_OUTPUT_IPV6_MAPPED = (
    'ESTAB    0      0      [::ffff:172.17.0.2]:45681  [::ffff:1.2.3.4]:443   users:(("python",pid=1,fd=8))'
)

SS_OUTPUT_SECOND_ESTAB = 'ESTAB    0      0      172.17.0.2:45682    10.0.0.1:80   users:(("python",pid=1,fd=9))'


# ===========================================================================
# Tests for parse_proc_net_tcp
# ===========================================================================


class TestParseProcNetTcp:
    """Tests for parsing /proc/net/tcp content."""

    def test_established_connection_extracted_with_correct_ip_and_port(self):
        """ESTABLISHED (state 01) connections should be extracted with decoded IP:port."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL
        result = parse_proc_net_tcp(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        # 2234D85E little-endian -> 94.216.52.34
        assert host == "94.216.52.34"
        assert port == 443
        assert protocol == "tcp"

    def test_listen_state_is_skipped(self):
        """LISTEN (state 0A) connections should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_LISTEN
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_time_wait_state_is_skipped(self):
        """TIME_WAIT (state 06) connections should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_TIME_WAIT
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_loopback_destination_is_filtered_out(self):
        """Connections to 127.x.x.x should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_LOOPBACK
        result = parse_proc_net_tcp(content)

        assert result == []

    def test_little_endian_hex_ip_decoded_correctly(self):
        """Little-endian hex IP '2200A8C0' should decode to '192.168.0.34'."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        # 2200A8C0 little-endian: bytes C0 A8 00 22 = 192.168.0.34
        # Make an established connection TO 192.168.0.34:80
        line = (
            "   5: 0100000A:C354 2200A8C0:0050 01 00000000:00000000 00:00000000 00000000"
            "     0        0 12350 1 0000000000000000 100 0 0 10 0"
        )
        content = PROC_NET_TCP_HEADER + "\n" + line
        result = parse_proc_net_tcp(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "192.168.0.34"
        assert port == 80

    def test_hex_port_decoded_correctly(self):
        """Hex port '01BB' should decode to 443."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = PROC_NET_TCP_HEADER + "\n" + PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL
        result = parse_proc_net_tcp(content)

        assert result[0][1] == 443

    def test_empty_input_returns_empty_list(self):
        """Empty string input should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        result = parse_proc_net_tcp("")
        assert result == []

    def test_header_only_input_returns_empty_list(self):
        """Input with only the header line should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        result = parse_proc_net_tcp(PROC_NET_TCP_HEADER)
        assert result == []

    def test_multiple_established_connections(self):
        """Multiple ESTABLISHED connections to non-loopback destinations are all returned."""
        from enterprise.behavioral.proc_net_parser import parse_proc_net_tcp

        content = "\n".join(
            [
                PROC_NET_TCP_HEADER,
                PROC_NET_TCP_ESTABLISHED_TO_EXTERNAL,
                PROC_NET_TCP_LISTEN,
                PROC_NET_TCP_TIME_WAIT,
                PROC_NET_TCP_LOOPBACK,
                PROC_NET_TCP_ESTABLISHED_SECOND,
            ]
        )
        result = parse_proc_net_tcp(content)

        assert len(result) == 2
        hosts = {r[0] for r in result}
        assert "94.216.52.34" in hosts
        assert "10.0.0.1" in hosts


# ===========================================================================
# Tests for parse_ss_output
# ===========================================================================


class TestParseSsOutput:
    """Tests for parsing ss -tnp output."""

    def test_estab_line_extracted_with_correct_host_and_port(self):
        """ESTAB lines should be extracted with correct host:port."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_ESTAB
        result = parse_ss_output(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "93.184.216.34"
        assert port == 443
        assert protocol == "tcp"

    def test_non_estab_lines_are_skipped(self):
        """Non-ESTAB lines (header, LISTEN, etc.) should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_LISTEN
        result = parse_ss_output(content)

        assert result == []

    def test_loopback_ipv4_destination_filtered_out(self):
        """Connections to 127.0.0.1 should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_LOOPBACK
        result = parse_ss_output(content)

        assert result == []

    def test_loopback_ipv6_destination_filtered_out(self):
        """Connections to [::1] should be filtered out."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_IPV6_LOOPBACK
        result = parse_ss_output(content)

        assert result == []

    def test_ipv6_bracket_notation_handled(self):
        """IPv6 bracket notation [::ffff:1.2.3.4]:443 should be parsed correctly."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = SS_OUTPUT_HEADER + "\n" + SS_OUTPUT_IPV6_MAPPED
        result = parse_ss_output(content)

        assert len(result) == 1
        host, port, protocol = result[0]
        assert host == "::ffff:1.2.3.4"
        assert port == 443

    def test_empty_input_returns_empty_list(self):
        """Empty string input should return an empty list."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        result = parse_ss_output("")
        assert result == []

    def test_multiple_estab_connections(self):
        """Multiple ESTAB lines to non-loopback destinations are all returned."""
        from enterprise.behavioral.proc_net_parser import parse_ss_output

        content = "\n".join(
            [
                SS_OUTPUT_HEADER,
                SS_OUTPUT_ESTAB,
                SS_OUTPUT_LISTEN,
                SS_OUTPUT_LOOPBACK,
                SS_OUTPUT_SECOND_ESTAB,
            ]
        )
        result = parse_ss_output(content)

        assert len(result) == 2
        hosts = {r[0] for r in result}
        assert "93.184.216.34" in hosts
        assert "10.0.0.1" in hosts
