# 🔒 Security Audit Report: TASK-001 Authentication & Authorization

## Executive Summary

| Kategoria | Implementacja | Status |
|-----------|---------------|--------|
| **Autentykacja API Key** | ✅ Zaimplementowane | Produkcyjne z zastrzeżeniami |
| **Autentykacja JWT/OIDC** | ✅ Zaimplementowane | Produkcyjne |
| **Autoryzacja RBAC** | ✅ Zaimplementowane | Produkcyjne |
| **Thread Safety** | ✅ Naprawione | Produkcyjne |
| **Rate Limiting** | ❌ Brak | **KRYTYCZNE** |
| **Persistent Storage** | ✅ SQLite/PostgreSQL | Produkcyjne |
| **Integracja z serwerem HTTP** | ✅ Zaimplementowane | Opt-in |
| **Audit Logging** | ✅ Zaimplementowane | Produkcyjne |

---

## 🚨 Krytyczne luki bezpieczeństwa

### 1. BRAK RATE LIMITING NA AUTENTYKACJĘ

**Severity:** KRYTYCZNA
**Status:** NIEZAIMPLEMENTOWANE
**Ryzyko:** Brute-force attacks, DoS

**Dowód z testów:**

```
100 failed attempts took 0.005s (should be rate-limited)
```

**Opis:**
Aktualnie nie ma żadnego ograniczenia na liczbę nieudanych prób autentykacji.
Atakujący może wykonać tysiące prób na sekundę.

**Rekomendacja:**

```python
# Dodać do AuthenticationMiddleware:
class AuthRateLimiter:
    def __init__(self):
        self._attempts: dict[str, list[float]] = {}  # IP -> timestamps
        self._lock = threading.Lock()
        self.max_attempts = 10  # per window
        self.window_seconds = 60
        self.lockout_seconds = 300

    def check_rate_limit(self, ip: str) -> bool:
        # Implementacja token bucket per IP
        pass
```

---

### 2. ✅ PERSISTENT STORAGE - ZAIMPLEMENTOWANE

**Severity:** Rozwiązane
**Status:** ZAIMPLEMENTOWANE

**Opis:**
Zaimplementowano trzy backendy storage:

- `memory` - dla development/testing (dane tracone przy restart)
- `sqlite` - dla single-instance deployments
- `postgresql` - dla multi-instance deployments (production)

**Konfiguracja:**

```yaml
auth:
  storage:
    driver: sqlite  # memory | sqlite | postgresql
    path: data/auth.db  # dla sqlite

    # dla postgresql:
    # host: localhost
    # port: 5432
    # database: mcp_hangar
    # user: mcp_hangar
    # password: ${MCP_AUTH_DB_PASSWORD}
```

**Pliki:**

- `mcp_hangar/infrastructure/auth/sqlite_store.py`
- `mcp_hangar/infrastructure/auth/postgres_store.py`

---

### 3. BRAK IP BINDING DLA KLUCZY API

**Severity:** ŚREDNIA
**Status:** NIEZAIMPLEMENTOWANE
**Ryzyko:** Kradzież klucza umożliwia dostęp z dowolnego IP

**Dowód z testów:**

```python
# Key from different IP is allowed
for ip in ["192.168.1.1", "10.0.0.1", "172.16.0.1"]:
    # All succeed - no IP restriction
```

**Rekomendacja:**
Dodać opcjonalne IP allowlist per klucz:

```python
@dataclass
class ApiKeyMetadata:
    # ... existing fields ...
    allowed_ips: frozenset[str] | None = None  # None = all IPs allowed
```

---

## ⚠️ Średnie problemy bezpieczeństwa

### 4. BRAK ROTACJI KLUCZY

**Status:** NIEZAIMPLEMENTOWANE

**Opis:**
Nie ma mechanizmu automatycznej rotacji kluczy API.
Klucze pozostają ważne do ręcznego unieważnienia.

**Rekomendacja:**

- Dodać `rotate_key()` method
- Generować nowy klucz, stary ważny przez grace period
- Webhook do powiadomienia o rotacji

---

### 5. TIMING ATTACK - ROZWIĄZANE

**Status:** ROZWIĄZANE

**Rozwiązanie (2026-02-15):**

Wszystkie 4 backendy storage używają constant-time hash comparison:

1. **InMemoryApiKeyStore** - iteruje wszystkie klucze bez wcześniejszego wyjścia, używa `hmac.compare_digest` dla każdego porównania
2. **SQLiteApiKeyStore** - wykonuje dummy comparison na miss dla wyrównania czasów
3. **PostgresApiKeyStore** - wykonuje dummy comparison na miss dla wyrównania czasów
4. **EventSourcedApiKeyStore** - używa `constant_time_key_lookup` dla iteracji indeksu

**Implementacja:**

```python
# Utility module: mcp_hangar/infrastructure/auth/constant_time.py
def constant_time_key_lookup(target_hash: str, hash_dict: dict[str, V]) -> V | None:
    result: V | None = None
    target_bytes = target_hash.encode("utf-8")

    for stored_hash, value in hash_dict.items():
        if hmac.compare_digest(target_bytes, stored_hash.encode("utf-8")):
            result = value  # No break - continues iterating

    return result

# SQLite/Postgres stores:
_DUMMY_HASH = "0" * 64
if row is None:
    hmac.compare_digest(key_hash.encode("utf-8"), _DUMMY_HASH.encode("utf-8"))
    return None
```

**Weryfikacja:**

Automatyczne testy regresji w `tests/unit/test_timing_attack_prevention.py`:

- Strukturalne testy potwierdzające użycie `hmac.compare_digest`
- Testy czasowe sprawdzające że stosunek valid/invalid < 5x
- Testy pozycji klucza w słowniku (powinny być podobne czasy)

**Pliki:**

- `mcp_hangar/infrastructure/auth/constant_time.py` (utility)
- `mcp_hangar/infrastructure/auth/api_key_authenticator.py` (InMemory)
- `mcp_hangar/infrastructure/auth/sqlite_store.py` (SQLite)
- `mcp_hangar/infrastructure/auth/postgres_store.py` (Postgres)
- `mcp_hangar/infrastructure/auth/event_sourced_store.py` (EventSourced)
- `tests/unit/test_timing_attack_prevention.py` (automated regression tests)

---

### 6. BRAK SZYFROWANIA KLUCZY W PAMIĘCI

**Status:** NIEZAIMPLEMENTOWANE

**Opis:**
Klucze API są przechowywane jako hashe SHA-256, ale sam hash
jest w pamięci w plaintext. Memory dump może ujawnić hashe.

**Rekomendacja dla produkcji:**

- Użyć secure enclave (HSM)
- Lub szyfrować hashe kluczem z env var

---

### 7. BRAK LIMITU DŁUGOŚCI SESJI JWT

**Status:** ZALEŻNE OD IDP

**Opis:**
JWT lifetime zależy od konfiguracji IdP (np. Keycloak).
MCP-Hangar sprawdza `exp` claim, ale nie wymusza max lifetime.

**Rekomendacja:**

```python
MAX_TOKEN_LIFETIME = 3600  # 1 hour

def _validate_token_lifetime(self, claims: dict) -> None:
    iat = claims.get("iat")
    exp = claims.get("exp")
    if exp - iat > MAX_TOKEN_LIFETIME:
        raise InvalidCredentialsError("Token lifetime exceeds maximum")
```

---

## ✅ Poprawnie zaimplementowane

### 8. Thread Safety

- `InMemoryApiKeyStore` - RLock dodany ✅
- `InMemoryRoleStore` - RLock dodany ✅
- Concurrent tests przechodzą ✅

### 9. Input Validation

- Walidacja długości klucza API (MAX=256) ✅
- Walidacja formatu PrincipalId ✅
- Unicode handling ✅

### 10. Token Expiration

- Expired keys rejected ✅
- JWT exp claim verified ✅
- JWT nbf claim verified ✅

### 11. Key Revocation

- Natychmiastowe odrzucenie ✅
- Audit log ✅

### 12. HTTPS Warnings

- Ostrzeżenia dla non-HTTPS OIDC issuer ✅
- Ostrzeżenia dla non-HTTPS JWKS URI ✅

### 13. Trusted Proxies

- X-Forwarded-For tylko z trusted proxies ✅
- Konfigurowalny zestaw proxy ✅

### 14. Constant-Time Key Comparison

- hmac.compare_digest dla wszystkich porównań hashy ✅
- Wszystkie 4 backendy (InMemory, SQLite, Postgres, EventSourced) zabezpieczone ✅
- Automatyczne testy regresji dodane (`test_timing_attack_prevention.py`) ✅
- Utility module: `mcp_hangar/infrastructure/auth/constant_time.py` ✅

---

## 📋 Dodatkowe testy do wykonania

### Testy penetracyjne

```bash
# 1. Brute-force API key
for i in {1..10000}; do
  curl -H "X-API-Key: mcp_attempt_$i" http://localhost:9000/mcp
done

# 2. Token replay z innego IP
TOKEN=$(get_keycloak_token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:9000/mcp  # IP1
curl -H "Authorization: Bearer $TOKEN" http://different-ip:9000/mcp  # IP2

# 3. Expired token acceptance window
# Get token, wait for expiry, test if accepted within grace period

# 4. Role escalation attempt
# Create developer, try to call admin-only endpoints
```

### Load testing

```bash
# Concurrent auth with k6
k6 run -u 100 -d 60s auth_load_test.js
```

### Fuzzing

```python
# Fuzz API key format
import atheris
atheris.Setup(sys.argv, fuzz_api_key_auth)
atheris.Fuzz()
```

---

## 🔧 Rekomendowane kolejne kroki

### Priorytet 1 (przed produkcją)

1. **Implementować rate limiting** na autentykację per IP
2. **Dodać persistent storage** (SQLite/PostgreSQL)
3. **Dodać CLI** dla zarządzania kluczami (`mcp-hangar auth create-key`)

### Priorytet 2 (v1.1)

4. Dodać IP allowlist dla kluczy
2. Implementować rotację kluczy

### Priorytet 3 (v1.2)

7. Zintegrować z HashiCorp Vault
2. Dodać SCIM provisioning
3. Implementować mTLS authentication

---

## 📊 Pokrycie testami bezpieczeństwa

| Obszar | Testy | Status |
|--------|-------|--------|
| Brute-force | test_rapid_failed_attempts | ✅ (wykrywa brak rate limiting) |
| Timing attack | test_key_enumeration_via_timing + test_timing_attack_prevention.py | ✅ ROZWIĄZANE |
| Token expiration | test_expired_key_is_rejected | ✅ |
| Key revocation | test_revoked_key_is_immediately_rejected | ✅ |
| Concurrent access | test_concurrent_* | ✅ |
| Input validation | test_empty/long/unicode_api_key | ✅ |
| Authorization bypass | test_anonymous/escalation | ✅ |
| Token replay | test_same_token_can_be_used | ✅ |

---

## Appendix: Konfiguracja produkcyjna

```yaml
# config.yaml - Produkcja
auth:
  enabled: true
  allow_anonymous: false

  # Rate limiting (gdy zaimplementowane)
  rate_limit:
    enabled: true
    max_attempts: 10
    window_seconds: 60
    lockout_seconds: 300

  api_key:
    enabled: true
    header_name: X-API-Key
    # storage: postgresql  # gdy zaimplementowane

  oidc:
    enabled: true
    issuer: https://auth.company.com  # HTTPS required!
    audience: mcp-hangar
    max_token_lifetime: 3600

  # IP restrictions
  trusted_proxies:
    - 10.0.0.0/8
    - 172.16.0.0/12
```
