# Offline Conformance Suite

Run from the repository root:

```bash
uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q tests/conformance
uv run --offline --no-project --with pytest==8.4.2 --with jsonschema==4.25.1 python -m pytest -q
```

Tools must already be cached for `--offline`. Tests do not resolve a hostname,
connect a socket, start a JVM, or require diagnostic credentials. The parent
`tests/conftest.py` blocks DNS and socket connection APIs; this suite also tests
that guard. The injected transport records policy arguments and returns local
bytes or exceptions. It never constructs a URL or follows a Location header.

## Coverage and evidence levels

| Matrix | Fixture / test | What is actually verified |
| --- | --- | --- |
| Boot 2.7 Servlet | `boot-2.7-servlet/` | javax descriptor, MVC dispatcher, V2 details / V3 components, two Flyway beans in one context |
| Boot 3.4 Servlet | `boot-3.4-servlet/` | Jakarta descriptor, MVC dispatcher, nested conditions, startup parent tree, Liquibase changeSets |
| Boot 4.1 WebFlux | `boot-4.1-webflux/` | Reactive handler descriptor and dispatcherHandlers; separate supplementary Jakarta Servlet example |
| Bean and Condition algorithms | `test_multiversion_fixtures.py` | Actual normalizers/handler, aliases, parent contexts, ANY/ALL/NONE children and two-hop source-factory-property attribution |
| Eight tools | `contracts/*.json`, `test_tools_contract.py` | Nonempty typed examples, input/outputSchema validation, URL/header/method escalation rejection, negative mutations, risk annotations and deterministic definition order |
| Connection refused / read timeout | `test_faults.py` | Real ResponseGateReader converts injected errno exceptions into safe codes; shared errors conform for all eight tools |
| 404 / startup unavailable | `test_faults.py` | Exposure recipe includes config key; startup recipe checks exposure and proposes native BufferingApplicationStartup configuration with approval |
| 401 / 403 / redirects | `test_faults.py` | Real gate checks status before JSON; GET + follow_redirects=False; exactly one request; structured authorization recipe |
| Hidden Health details | `faults/health-hidden*` | Real gate preserves DOWN-only body; independent partial golden has no fabricated child components |
| 2,500 Beans | `large/beans-2500.json`, `test_large_response.py` | Actual filtering and paging, disjoint successive pages without rereading, complete index before slicing, bounded associations at default depth |
| Every validated result | `assertions.py` | Published schema, explicit RFC3339 checker, risk booleans, eight-name order, identical structured/text copies and complete JSON-RPC ≤16,384 UTF-8 bytes |

Seven tool handlers are not implemented. Their examples and shared errors are
contract tests, **not execution tests for those tools**. Health V2/V3, routing,
startup, migrations and OpenAPI raw-shape tests prepare regression inputs for
future normalizers; they do not claim those normalizers already exist. The
hidden-health expected response is likewise an explicit contract example.
`list_tools()` is the existing definition catalog, not a live MCP tools/list
exchange. No SDK host is introduced by this suite.

## Transport boundary

`src/spring_runtime_mcp/transport/response_gate.py` implements an injectable
request/response policy boundary. It passes fixed GET, disabled redirects,
5-second timeout and an 8 MiB limit to a registered transport, enforces its own
await timeout, checks status/media type before JSON, and bounds accepted bytes.
No socket connector is supplied. Actual connected-IP validation, TLS and
streaming byte enforcement must still be implemented and tested when adding a
production transport; the injected mock does not prove those properties.

Fault recipes never change target configuration. Startup 404 does not prove
which of endpoint exposure, security or absent startup collection caused it;
the recipe explicitly asks the operator to check those prerequisites. For
hidden Health details, review `management.endpoint.health.show-details` and
existing authorization locally; never enable unrestricted details automatically.

The offline jsonschema installation lacks its optional RFC3339 checker.
`assertions.FORMATS` registers a deterministic calendar/offset checker so malformed
date-time values cannot silently pass. Negative tests retain the malformed values.

## Recorded verification

2026-10-16: all **338 tests passed in 3.74 seconds**, including **222** conformance
cases. Strict mypy passed for 26 source modules; Ruff lint and formatting passed;
the existing contract validator passed its 144+ assertions. Public tool schemas
and runtime dependencies were unchanged.
