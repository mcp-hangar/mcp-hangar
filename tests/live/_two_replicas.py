"""Two gateways over one PostgreSQL, for the two-replica tier (#1949).

Each module gets a database of its own, created from the server that
``HANGAR_TEST_POSTGRES_DSN`` names and dropped afterwards, so a peer-key name
left by one run never refuses the next one's create. The upstreams are MCP
servers over HTTPS on a host address (``_peer_key_server.py``), each with its
own key; the gateways pin them by that key (ADR-030).

The replicas are `mcp-hangar serve --http` processes started one after the
other. Started at the same instant on an empty database, the second can die
creating the auth tables, which is the defect a replica set starting together
hits rather than this tier's subject.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import httpx

from tests.live.conftest import _free_port

DSN_ENV = "HANGAR_TEST_POSTGRES_DSN"

_SERVER = Path(__file__).resolve().parent / "_peer_key_server.py"

# The tail reads the shared log every 2 s (`EventTailer.DEFAULT_INTERVAL_S`);
# a change reaches a peer well inside this.
CONVERGE_S = 15.0

T = TypeVar("T")


def eventually(probe: Callable[[], T | None], what: str, timeout: float = CONVERGE_S) -> T:
    """Poll ``probe`` until it returns something other than None; fail saying ``what`` never happened."""
    deadline = time.monotonic() + timeout
    while True:
        value = probe()
        if value is not None:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"not within {timeout}s: {what}")
        time.sleep(0.2)


def at_once(*calls: Callable[[], T]) -> list[T]:
    """Run each of ``calls`` on its own thread, released together; their results, in order."""
    barrier = threading.Barrier(len(calls))
    results: list[Any] = [None] * len(calls)

    def run(index: int, call: Callable[[], T]) -> None:
        barrier.wait()
        results[index] = call()

    threads = [threading.Thread(target=run, args=(i, call)) for i, call in enumerate(calls)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert all(r is not None for r in results), "a concurrent call did not finish"
    return results


def name(prefix: str) -> str:
    """A server name no other test in the module uses."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def host_address() -> str | None:
    """This host's address on its default route, or None when it has only loopback.

    Connecting a UDP socket sends nothing; it only picks the source address.
    """
    try:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1
            address = str(s.getsockname()[0])
    except OSError:
        return None
    return None if address.startswith("127.") else address


# --- keys ----------------------------------------------------------------------


@dataclass(frozen=True)
class TlsKey:
    """A key, its self-signed certificate, and the pin of its SubjectPublicKeyInfo."""

    cert: Path
    key: Path
    pin: str


def make_key(directory: Path, label: str, address: str) -> TlsKey:
    """An EC P-256 key on the named curve, with a certificate for ``address``."""
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address(address))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / f"{label}.crt", directory / f"{label}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    spki = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return TlsKey(cert=cert_path, key=key_path, pin=f"sha256:{hashlib.sha256(spki).hexdigest()}")


@contextmanager
def upstream(key: TlsKey, address: str, log_dir: Path) -> Iterator[str]:
    """Serve ``_peer_key_server.py`` under ``key`` on ``address``; yield its endpoint."""
    port = _free_port()
    with (log_dir / f"upstream-{port}.log").open("wb") as log:
        proc = subprocess.Popen(
            [sys.executable, str(_SERVER), str(key.cert), str(key.key), address, str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:

        def listening() -> bool | None:
            try:
                with socket.create_connection((address, port), timeout=0.5):
                    return True
            except OSError:
                return None

        eventually(listening, f"the upstream on {address}:{port} listens", timeout=20)
        yield f"https://{address}:{port}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# --- the database ----------------------------------------------------------------


@dataclass(frozen=True)
class Database:
    """One database on the test server, and direct reads of what the replicas wrote."""

    host: str
    port: int
    user: str
    password: str
    dbname: str

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        import psycopg2

        conn = psycopg2.connect(
            host=self.host, port=self.port, user=self.user, password=self.password, dbname=self.dbname
        )
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
        finally:
            conn.close()

    def row(self, server: str) -> dict[str, Any] | None:
        """The live configuration row of ``server``, as recorded; a deleted one is kept with `enabled` off."""
        rows = self.query("SELECT config_json FROM mcp_server_configs WHERE mcp_server_id = %s AND enabled", (server,))
        if not rows:
            return None
        value = rows[0][0]
        return value if isinstance(value, dict) else json.loads(value)

    def recorded_key_ref(self, server: str) -> str | None:
        """The key_ref the peer-key name record holds for ``server``."""
        rows = self.query("SELECT key_ref FROM peer_key_names WHERE name = %s", (server,))
        return str(rows[0][0]) if rows else None

    def position(self) -> int:
        """The highest position in the shared log."""
        return int(self.query("SELECT COALESCE(MAX(global_position), 0) FROM events")[0][0])

    def events_produced_by(self, producer: str, server: str, after: int) -> list[str]:
        """Event types ``producer`` appended to ``server``'s stream after ``after``."""
        rows = self.query(
            "SELECT event_type FROM events WHERE stream_id = %s AND global_position > %s AND data->>'produced_by' = %s",
            (f"mcp_server:{server}", after, producer),
        )
        return [str(r[0]) for r in rows]

    def config_block(self) -> str:
        return (
            f"host: {self.host}\n    port: {self.port}\n    database: {self.dbname}\n"
            f"    user: {self.user}\n    password: {self.password}"
        )


@contextmanager
def fresh_database(dsn: str) -> Iterator[Database]:
    """Create a database for one module on the server ``dsn`` names; drop it afterwards."""
    import psycopg2
    from psycopg2.extensions import parse_dsn

    params = parse_dsn(dsn)
    dbname = f"hangar_live_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(dsn)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{dbname}"')
        yield Database(
            host=params.get("host", "127.0.0.1"),
            port=int(params.get("port", 5432)),
            user=params.get("user", "postgres"),
            password=params.get("password", ""),
            dbname=dbname,
        )
    finally:
        with admin.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')
        admin.close()


def seed_keys(database: Database, principals: list[str]) -> dict[str, str]:
    """One API key per principal (`svc:<p>`) in the shared auth store; the raw keys by principal."""
    from mcp_hangar.auth.infrastructure.postgres_store import PostgresApiKeyStore
    from mcp_hangar.infrastructure.persistence.database_common import PostgresConfig, PostgresConnectionFactory

    factory = PostgresConnectionFactory(
        PostgresConfig(
            host=database.host,
            port=database.port,
            database=database.dbname,
            user=database.user,
            password=database.password,
            min_connections=1,
            max_connections=2,
        )
    )
    try:
        store = PostgresApiKeyStore(factory)
        store.initialize()
        return {p: store.create_key(principal_id=f"svc:{p}", name=p) for p in principals}
    finally:
        factory.close()


_REPLICA_CONFIG = """\
logging:
  level: INFO
persistence:
  backend: postgresql
  postgresql:
    {database}
auth:
  enabled: true
  allow_anonymous: false
  api_key:
    enabled: true
    header_name: X-API-Key
  storage:
    driver: postgresql
    {database}
  role_assignments:
{roles}
ssrf:
  trusted_networks:
    - "{address}/32"
mcp_servers: {{}}
"""


def replica_config(database: Database, address: str, roles: dict[str, str]) -> str:
    """The configuration every replica runs: the shared database, auth over it, and the upstreams' address."""
    lines = "\n".join(f'    - principal: "svc:{p}"\n      role: {r}\n      scope: global' for p, r in roles.items())
    return _REPLICA_CONFIG.format(database=database.config_block(), roles=lines, address=address)


# --- a replica ---------------------------------------------------------------------


@dataclass
class Replica:
    """A running gateway, reached over its REST API and its MCP endpoint."""

    label: str
    base_url: str
    keys: dict[str, str]

    def request(self, method: str, path: str, principal: str, body: dict[str, Any] | None = None) -> httpx.Response:
        return httpx.request(
            method,
            f"{self.base_url}/api/mcp_servers{path}",
            json=body,
            headers={"X-API-Key": self.keys[principal]},
            timeout=30.0,
        )

    def create(self, principal: str, body: dict[str, Any]) -> httpx.Response:
        return self.request("POST", "/", principal, body)

    def update(self, principal: str, server: str, body: dict[str, Any]) -> httpx.Response:
        return self.request("PUT", f"/{server}", principal, body)

    def delete(self, principal: str, server: str) -> httpx.Response:
        return self.request("DELETE", f"/{server}", principal)

    def forget(self, principal: str, server: str) -> httpx.Response:
        return self.request("DELETE", f"/{server}/peer_identity", principal)

    def state(self, principal: str, server: str) -> str | None:
        """The state of this replica's copy of ``server``, or None when it holds none."""
        got = self.request("GET", f"/{server}", principal)
        return str(got.json().get("state")) if got.status_code == 200 else None

    def instance_id(self, principal: str) -> str:
        got = httpx.get(f"{self.base_url}/api/system/", headers={"X-API-Key": self.keys[principal]}, timeout=10.0)
        return str(got.json()["system"]["instance"]["instance_id"])

    def call(self, principal: str, server: str) -> dict[str, Any]:
        """One ``echo`` through ``hangar_call``; the call's result, or the batch's when it never ran."""
        from mcp import ClientSession

        from tests.live._mcp_client import open_mcp_streams

        async def _call() -> dict[str, Any]:
            headers = {"X-API-Key": self.keys[principal]}
            async with open_mcp_streams(f"{self.base_url}/mcp", headers) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "hangar_call",
                        {"calls": [{"mcp_server": server, "tool": "echo", "arguments": {"text": "hello"}}]},
                    )
                    data = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
                    if not (isinstance(data, dict) and isinstance(data.get("results"), list)):
                        text = "".join(
                            getattr(c, "text", "") for c in result.content if getattr(c, "type", None) == "text"
                        )
                        data = json.loads(text) if text else {}
                    assert isinstance(data, dict), f"no batch result: {data!r}"
                    # A batch refused before it ran (an unknown server) has no results.
                    first: dict[str, Any] = data["results"][0] if data.get("results") else data
                    return first

        return asyncio.run(asyncio.wait_for(_call(), timeout=60))


def peer_key_server(server: str, endpoint: str, key_ref: str, pins: list[str]) -> dict[str, Any]:
    """The create body of a key-pinned server."""
    return {
        "mcp_server_id": server,
        "mode": "remote",
        "endpoint": endpoint,
        "tls": {"peer_identity": {"key_ref": key_ref, "spki_sha256": pins}},
    }


def key_list(key_ref: str, pins: list[str]) -> dict[str, Any]:
    """The update body that sets a server's key list."""
    return {"tls": {"peer_identity": {"key_ref": key_ref, "spki_sha256": pins}}}


def recorded_pins(row: dict[str, Any] | None) -> list[str] | None:
    """The key list a configuration row holds, or None when it holds no peer identity."""
    identity = ((row or {}).get("tls") or {}).get("peer_identity")
    return list(identity["spki_sha256"]) if identity else None
