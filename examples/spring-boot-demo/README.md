# Spring Boot demo target

A disposable Spring Boot 3.4 application used to verify `spring-runtime-mcp`
against a real Actuator process (not synthetic fixtures).

What it contains:

- `PaymentGateway` → `StripePaymentGateway` → `StripeClient`, where
  `StripeClient` is gated by `@ConditionalOnProperty(stripe.enabled=true)` and
  `PaymentGateway` by `@ConditionalOnBean(StripeClient.class)`. With the
  property absent, both are missing.
- `GET /orders/{id}` on `OrderController`.
- Flyway `V1__create_orders.sql` over in-memory H2.
- A read-only management port on `127.0.0.1:18081` (see
  `application-local-mcp.yml`) and `BufferingApplicationStartup` for
  `GET /actuator/startup`.
- `source-catalog.json`: explicit workspace facts for the two Stripe beans so
  the missing-bean analysis has factory and property evidence to follow.

## Run

```bash
mvn -q package -DskipTests
java -jar target/spring-runtime-mcp-demo-0.1.0.jar
```

The app listens on `http://127.0.0.1:8080` and Actuator on
`http://127.0.0.1:18081`.

## Verify the MCP server against it

From the repository root, with the app running:

```bash
PYTHONPATH=src uv run --no-project --with jsonschema==4.25.1 --with mcp \
  python -m spring_runtime_mcp.cli \
  --base-url http://127.0.0.1:18081 \
  --source-catalog examples/spring-boot-demo/source-catalog.json
```

Or regenerate the committed transcript end to end:

```bash
PYTHONPATH=src uv run --no-project --with jsonschema==4.25.1 --with mcp \
  python scripts/check_demo_app.py
```

`transcript.jsonl` records `initialize`, `tools/list` and one call to each of
the read tools.

## What the transcript shows

`springboot_inspect_beans` with `beanNamePattern: "paymentGateway"` returns a
three-node blocking path:

```text
REQUIRED_BEAN_MISSING      (StripeConfiguration#paymentGateway)
  → SOURCE_FACTORY_NOT_CREATED
    → PROPERTY_NOT_FOUND   (StripeConfiguration#stripeClient, stripe.enabled)
```

`springboot_verify_mappings` returns the real MVC mapping for
`GET /orders/{id}`, including the handler descriptor
`(Ljava/lang/String;)Lexample/OrderController$Order;`.

`springboot_check_migrations` reports Flyway version `1`
(`V1__create_orders.sql`, state `SUCCESS`) and keeps `pendingVisibility` as
`unknown` — it does not claim there are no pending migrations.

`springboot_detect_drift` with `expectedBeans` scoped to `demo` and
`expectedScopeComplete: true` reports `paymentGateway` as missing with
`evidenceLevel: observed` and no root-cause claim (`INSUFFICIENT_EVIDENCE`).

`springboot_diagnose_health` reports `UP`; details are hidden by the target
configuration, so the tool does not invent component details.

`springboot_analyze_startup` reads the buffered `GET startup` snapshot and
paginates the slow steps within the 16 KiB frame budget.

## Why some results are `partial`

The conditions report contains condition grammars this server does not model
yet (for example `OnAvailableEndpointCondition`), and Boot's `dependencies`
arrays reference infrastructure objects (`environment`, the bean factory, the
application context itself) that are not registered beans. Both facts are
recorded as gaps (`CONDITION_REPORT_INCOMPLETE`,
`NOT_FOUND_IN_COMPLETE_VISIBLE_HIERARCHY`) instead of being silently treated as
complete evidence.
