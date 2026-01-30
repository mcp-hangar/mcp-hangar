# Manual Testing: Runtime Provider Injection (Hot-Loading)

This guide covers step-by-step manual testing of Phase 2 hot-loading features.

## Prerequisites

1. Python 3.11+ installed
2. Node.js with npx available (for npm packages)
3. uv/uvx available (for PyPI packages)
4. Docker or Podman (optional, for OCI packages)

```bash
# Verify prerequisites
python --version   # Should be 3.11+
npx --version      # Should return version
uvx --version      # Should return version
docker --version   # Optional
```

## Test 1: Start MCP Hangar Server

### 1.1 Start in HTTP Mode

```bash
cd packages/core

# Start server in HTTP mode
python -m mcp_hangar.server.cli serve --http --port 8080

# Expected output:
# - "bootstrap_complete" with providers list
# - "hot_loading_initialized"
# - "http_server_started" on port 8080
```

### 1.2 Verify Health Endpoints

```bash
# In another terminal
curl http://localhost:8080/health/live
# Expected: {"status": "healthy"}

curl http://localhost:8080/health/ready
# Expected: {"status": "healthy", "ready_providers": N, "total_providers": N}
```

## Test 2: Hot-Load a Provider via MCP Client

### 2.1 Using Python Script

Create a test script `test_hotload.py`:

```python
import asyncio
from mcp_hangar.server.bootstrap import bootstrap
from mcp_hangar.application.commands import LoadProviderCommand, UnloadProviderCommand
from mcp_hangar.server.state import get_runtime_providers

async def main():
    # Bootstrap
    ctx = bootstrap()
    print(f"Hot-loading enabled: {ctx.load_provider_handler is not None}")

    # Load a provider
    cmd = LoadProviderCommand(
        name="io.github.j0hanz/filesystem-context",  # Known working server
        force_unverified=False,
        user_id="manual-test",
    )

    result = await ctx.load_provider_handler.handle(cmd)
    print(f"Status: {result.status}")
    print(f"Provider ID: {result.provider_id}")
    print(f"Tools: {len(result.tools) if result.tools else 0}")

    if result.status == "loaded":
        # Verify in store
        store = get_runtime_providers()
        print(f"In runtime store: {store.exists(result.provider_id)}")

        # Unload
        unload_cmd = UnloadProviderCommand(
            provider_id=result.provider_id,
            user_id="manual-test",
        )
        ctx.unload_provider_handler.handle(unload_cmd)
        print(f"Unloaded. Store count: {store.count()}")

asyncio.run(main())
```

Run it:
```bash
python test_hotload.py
```

### 2.2 Expected Results

```
Hot-loading enabled: True
Status: loaded
Provider ID: io-github-j0hanz-filesystem-context
Tools: 10
In runtime store: True
Unloaded. Store count: 0
```

## Test 3: Test hangar_list with Runtime Providers

### 3.1 Load a Provider and Check List

```python
import asyncio
from mcp_hangar.server.bootstrap import bootstrap
from mcp_hangar.application.commands import LoadProviderCommand
from mcp_hangar.server.tools.hangar import hangar_list

async def test_list():
    ctx = bootstrap()

    # Load provider
    cmd = LoadProviderCommand(
        name="io.github.j0hanz/filesystem-context",
        force_unverified=False,
        user_id="test",
    )
    await ctx.load_provider_handler.handle(cmd)

    # Check hangar_list
    result = hangar_list()
    print("Configured providers:", len(result["providers"]))
    print("Runtime providers:", len(result["runtime_providers"]))

    for rp in result["runtime_providers"]:
        print(f"  - {rp['provider_id']}: {rp['state']} (verified: {rp['verified']})")

asyncio.run(test_list())
```

### 3.2 Expected Output

```
Configured providers: 1
Runtime providers: 1
  - io-github-j0hanz-filesystem-context: ready (verified: True)
```

## Test 4: Test Missing Secrets Handling

### 4.1 Try Loading Provider Requiring Secrets

```python
import asyncio
from mcp_hangar.server.bootstrap import bootstrap
from mcp_hangar.application.commands import LoadProviderCommand

async def test_missing_secrets():
    ctx = bootstrap()

    # Try to load a provider that requires env vars
    cmd = LoadProviderCommand(
        name="stripe",  # Requires STRIPE_API_KEY
        force_unverified=False,
        user_id="test",
    )

    result = await ctx.load_provider_handler.handle(cmd)
    print(f"Status: {result.status}")
    print(f"Message: {result.message}")
    if result.instructions:
        print(f"Instructions:\n{result.instructions[:500]}...")

asyncio.run(test_missing_secrets())
```

### 4.2 Expected Output

```
Status: missing_secrets
Message: Missing required secrets: STRIPE_API_KEY
Instructions:
Missing secrets for provider 'stripe':
  - STRIPE_API_KEY

To provide these secrets, either:
1. Set environment variables:
   export STRIPE_API_KEY="your-value"
...
```

## Test 5: Test Unverified Provider Rejection

### 5.1 Try Loading Unverified Provider Without Flag

```python
import asyncio
from mcp_hangar.server.bootstrap import bootstrap
from mcp_hangar.application.commands import LoadProviderCommand
from mcp_hangar.domain.exceptions import UnverifiedProviderError

async def test_unverified():
    ctx = bootstrap()

    # Search for an unverified provider first
    from mcp_hangar.infrastructure.registry.client import RegistryClient
    client = RegistryClient()
    results = await client.search("test", limit=20)

    unverified = next((r for r in results if not r.is_official), None)
    if unverified:
        cmd = LoadProviderCommand(
            name=unverified.name,
            force_unverified=False,  # Should reject
            user_id="test",
        )

        try:
            await ctx.load_provider_handler.handle(cmd)
        except UnverifiedProviderError as e:
            print(f"Correctly rejected: {e}")

    await client.close()

asyncio.run(test_unverified())
```

### 5.2 Expected Output

```
Correctly rejected: Provider 'xxx' is not officially verified. Use force_unverified=True to load anyway.
```

## Test 6: Test Registry Search

### 6.1 Search Registry Directly

```python
import asyncio
from mcp_hangar.infrastructure.registry.client import RegistryClient
from mcp_hangar.infrastructure.registry.cache import RegistryCache

async def test_registry():
    cache = RegistryCache()
    client = RegistryClient(cache=cache)

    # Search
    print("Searching for 'filesystem'...")
    results = await client.search("filesystem", limit=5)
    print(f"Found {len(results)} results:")
    for r in results:
        print(f"  - {r.name} (official: {r.is_official})")

    # Get details
    if results:
        print(f"\nGetting details for {results[0].id}...")
        details = await client.get_server(results[0].id)
        if details:
            print(f"  Name: {details.name}")
            print(f"  Packages: {[(p.registry_type, p.identifier) for p in details.packages]}")
            print(f"  Required env vars: {details.required_env_vars}")

    # Test caching
    print("\nTesting cache (second search should be instant)...")
    import time
    start = time.time()
    await client.search("filesystem", limit=5)
    print(f"  Cached response in {(time.time() - start)*1000:.1f}ms")

    await client.close()

asyncio.run(test_registry())
```

## Test 7: Test Output Redaction

### 7.1 Verify Secrets Are Redacted

```python
from mcp_hangar.domain.security.redactor import OutputRedactor

# Create redactor with known secrets
redactor = OutputRedactor(known_secrets={
    "API_KEY": "sk_live_abc123def456",
    "DB_PASSWORD": "super_secret_pass",
})

# Test redaction
test_cases = [
    "Error: API key sk_live_abc123def456 is invalid",
    "Connection failed with password: super_secret_pass",
    "GitHub token: ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxx",
]

print("Testing output redaction:")
for text in test_cases:
    result = redactor.redact(text)
    print(f"  Original: {text[:50]}...")
    print(f"  Redacted: {result[:50]}...")
    print()
```

### 7.2 Expected Output

```
Testing output redaction:
  Original: Error: API key sk_live_abc123def456 is invalid...
  Redacted: Error: API key [REDACTED:API_KEY] is invalid...

  Original: Connection failed with password: super_secret_pass...
  Redacted: Connection failed with password: [REDACTED:DB_PASSWORD]...
  ...
```

## Test 8: Test Runtime Store Thread Safety

### 8.1 Concurrent Operations

```python
import threading
import time
from mcp_hangar.infrastructure.runtime_store import RuntimeProviderStore, LoadMetadata
from datetime import datetime
from unittest.mock import MagicMock

def test_thread_safety():
    store = RuntimeProviderStore()
    errors = []

    def add_providers(prefix, count):
        try:
            for i in range(count):
                provider = MagicMock()
                provider.provider_id = f"{prefix}-{i}"
                metadata = LoadMetadata(
                    loaded_at=datetime.now(),
                    loaded_by="test",
                    source="test",
                    verified=True,
                )
                store.add(provider, metadata)
        except Exception as e:
            errors.append(e)

    def read_store(iterations):
        try:
            for _ in range(iterations):
                store.list_all()
                store.count()
        except Exception as e:
            errors.append(e)

    # Create threads
    threads = []
    for i in range(5):
        t = threading.Thread(target=add_providers, args=(f"thread-{i}", 20))
        threads.append(t)
    for _ in range(3):
        t = threading.Thread(target=read_store, args=(50,))
        threads.append(t)

    # Run
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"Thread safety test completed in {time.time() - start:.2f}s")
    print(f"Errors: {len(errors)}")
    print(f"Providers in store: {store.count()}")
    print(f"Expected: 100")

test_thread_safety()
```

### 8.2 Expected Output

```
Thread safety test completed in 0.XXs
Errors: 0
Providers in store: 100
Expected: 100
```

## Test 9: Full Integration Test

### 9.1 Complete Flow

```bash
# Terminal 1: Start server
cd packages/core
python -m mcp_hangar.server.cli serve --http --port 8080

# Terminal 2: Run integration test
python -c "
import asyncio
from mcp_hangar.server.bootstrap import bootstrap
from mcp_hangar.application.commands import LoadProviderCommand, UnloadProviderCommand
from mcp_hangar.server.state import get_runtime_providers
from mcp_hangar.server.tools.hangar import hangar_list, hangar_status

async def integration_test():
    print('=== Full Integration Test ===')

    ctx = bootstrap()
    store = get_runtime_providers()

    # 1. Initial state
    print('\n1. Initial state:')
    status = hangar_status()
    print(f'   Configured providers: {status[\"summary\"][\"total_providers\"]}')
    print(f'   Runtime providers: {status[\"summary\"].get(\"runtime_providers\", 0)}')

    # 2. Load provider
    print('\n2. Loading provider...')
    cmd = LoadProviderCommand(
        name='io.github.j0hanz/filesystem-context',
        force_unverified=False,
        user_id='integration-test',
    )
    result = await ctx.load_provider_handler.handle(cmd)
    print(f'   Status: {result.status}')
    print(f'   Provider: {result.provider_id}')
    print(f'   Tools: {len(result.tools) if result.tools else 0}')

    # 3. Verify in list
    print('\n3. Verifying in hangar_list...')
    listing = hangar_list()
    print(f'   Runtime providers: {len(listing[\"runtime_providers\"])}')

    # 4. Verify in status
    print('\n4. Verifying in hangar_status...')
    status = hangar_status()
    print(f'   Total providers: {status[\"summary\"][\"total_providers\"]}')
    print(f'   Runtime providers: {status[\"summary\"].get(\"runtime_providers\", 0)}')

    # 5. Unload
    print('\n5. Unloading provider...')
    unload_cmd = UnloadProviderCommand(
        provider_id=result.provider_id,
        user_id='integration-test',
    )
    ctx.unload_provider_handler.handle(unload_cmd)
    print(f'   Store count after unload: {store.count()}')

    # 6. Final state
    print('\n6. Final state:')
    status = hangar_status()
    print(f'   Runtime providers: {status[\"summary\"].get(\"runtime_providers\", 0)}')

    print('\n=== Integration Test PASSED ===')

asyncio.run(integration_test())
"
```

## Troubleshooting

### Common Issues

1. **"npx: command not found"**
   - Install Node.js: https://nodejs.org/

2. **"uvx: command not found"**
   - Install uv: `curl -LsSf https://astral.sh/uv/install.sh | sh`

3. **Provider fails to start**
   - Check stderr in logs for specific error
   - Some providers require command-line arguments
   - Some providers have bugs in their code

4. **"Provider not found in registry"**
   - Verify the exact server name in the registry
   - Use search first to find available servers

5. **Missing secrets**
   - Set required environment variables
   - Or create files in `~/.config/mcp-hangar/secrets/`

### Useful Debug Commands

```bash
# Check available runtimes
python -c "
import shutil
print('npx:', shutil.which('npx'))
print('uvx:', shutil.which('uvx'))
print('docker:', shutil.which('docker'))
print('podman:', shutil.which('podman'))
"

# Search registry
python -c "
import asyncio
from mcp_hangar.infrastructure.registry.client import RegistryClient
async def search(q):
    client = RegistryClient()
    results = await client.search(q, limit=10)
    for r in results:
        print(f'{r.name}: {r.description[:60]}...')
    await client.close()
asyncio.run(search('YOUR_QUERY'))
"
```
