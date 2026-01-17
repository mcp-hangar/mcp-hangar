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

### 5. TIMING ATTACK - MINIMALNE RYZYKO

**Status:** CZĘŚCIOWO ROZWIĄZANE

**Dowód z testów:**
```
Valid key avg: 0.069ms
Invalid key avg: 0.088ms
Difference: 0.019ms
```

**Opis:**
Różnica czasowa między walidacją poprawnego i niepoprawnego klucza
jest niewielka (~0.02ms), ale teoretycznie wykrywalna przy wielu próbach.

**Rekomendacja:**
Użyć `hmac.compare_digest()` dla constant-time comparison:
```python
import hmac

def _verify_key_hash(self, provided_hash: str, stored_hash: str) -> bool:
    return hmac.compare_digest(provided_hash.encode(), stored_hash.encode())
```

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
5. Implementować rotację kluczy
6. Dodać constant-time comparison

### Priorytet 3 (v1.2)
7. Zintegrować z HashiCorp Vault
8. Dodać SCIM provisioning
9. Implementować mTLS authentication

---

## 📊 Pokrycie testami bezpieczeństwa

| Obszar | Testy | Status |
|--------|-------|--------|
| Brute-force | test_rapid_failed_attempts | ✅ (wykrywa brak rate limiting) |
| Timing attack | test_key_enumeration_via_timing | ✅ |
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
