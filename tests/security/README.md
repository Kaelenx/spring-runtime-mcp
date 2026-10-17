# Red-Team Regression Suite

Run the requested TypeScript entry with Node 22+ (no npm dependencies):

```bash
node --test tests/security/redteam-audit.test.ts
```

It uses Node's built-in runner to execute the five actual Python test modules
through cached, offline uv environments. TAP is test output, not MCP STDIO.
Run the same cases directly with:

```bash
uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/security
```

| Module | Attack surface |
| --- | --- |
| `test_redteam_network.py` | Dynamic host fields, metadata/private/mapped/noncanonical IP inputs, mutable registration, redirects, malformed responses, zero-DNS rebinding input and safe client exceptions |
| `test_redteam_readonly.py` | Fixed GET constructor, startup, path/encoding aliases for forbidden endpoints, immutable capabilities, method overrides and exhaustive production-call AST inventory |
| `test_redteam_leakage.py` | Normal/embedded JSON, descriptor taint, URI credentials, JOSE/PEM, encoding variants, serializer/cache bypasses, raw errors, filter-count oracle and policy-change eviction |
| `test_redteam_injection.py` | Raw descriptions/comments/details, local-only messages/recipes, long prose, second serialization, Bean labels, condition suffixes and no request to runtime-provided links |
| `test_redteam_boundaries.py` | Confusable SSRF origins cannot reach DNS/transport, HTTP error bodies are not parsed or echoed, secret-bearing config/health/migration-shaped fields fail closed, static GET and stdout/logger inventory |
| `test_redteam_stdio.py` | Valid JSON-RPC, invalid argv, exceptions/cancellation, Python/native/cached stdout, atexit, confined file reads and symlink/byte-limit attacks |

All credentials are synthesized in test code; no SSH file, real diagnostic host,
DNS service or external HTTP endpoint is accessed. The parent conftest prohibits
network connections. Do not weaken a failing case to accept raw output or a
request; fix the corresponding production boundary.

The [audit report](../../docs/audits/security-redteam-2026-10-17.md) is a historical
snapshot predating the HTTP connector and stdio host. See the
[release record](../../CHANGELOG.md) for the current verification scope. These
tests do not prove live DNS pinning, peer-IP validation or official SDK compliance.
