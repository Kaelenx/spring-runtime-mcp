# Synthetic Cross-Version Diagnostic Fixtures

These 48 JSON files are local, synthetic and credential-free. Directory versions
are scenario labels; none was captured from a running target. `manifest.json`
records provenance, shape references and `realInstanceIntegrationVerified=false`.
Tests must not turn these samples into claims of exact-version integration support.

Each profile supplies beans, conditions, explicit source/type facts, mappings,
health, Flyway, Liquibase, startup, OpenAPI and configuration response examples.
Boot 2.7 additionally includes V2 Health. MVC descriptors use javax (2.7) or
Jakarta (3.4). Native WebFlux uses ServerWebExchange/Mono, so its supplementary
Jakarta Servlet descriptor is explicitly separate from the WebFlux route tree.

The shared Bean scenarios contain parent/child contexts, aliases and a source
catalog that permits a verified PaymentGateway → StripeClient factory → missing
stripe.enabled chain. Nested conditions remain in standard negativeMatches
message strings; they are not invented JSON children. SourceCatalog is internal
fixture evidence, not an Actuator field or a new public tool argument.

Raw structure references:

- [Spring Boot mappings](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/mappings.html): dispatcher trees and JVM method descriptors.
- [Startup](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/startup.html): timeline events, durations, tags and parent IDs; GET snapshots only.
- [Liquibase](https://docs.spring.io/spring-boot/3.4/api/rest/actuator/liquibase.html): case-sensitive changeSets and changeLog fields.
- [Repository normalization design](../../../docs/architecture/cross-version-normalization.md): the remaining field mapping and visibility rules.

Migrations preserve each context and migration Bean identity. Flyway checksum is
a signed number; Liquibase checksum is a string with its version prefix. Neither
an empty changeSets array nor an applied migration inventory proves there are no
pending scripts. Liquibase has no invented execution duration or Flyway version.
Startup step durations overlap and must not be summed as total startup time.

`contracts/` holds independent nonempty expected examples for eight published
output schemas. They are checked as committed data, never regenerated from the
schema during tests. Seven examples do not represent implemented handlers.
`faults/` provides status/body/error injection and the hidden-Health expected
partial contract. `large/beans-2500.json` is a deterministic 2,500-node chain for
filtering, page and graph-budget regression tests. Only sanitized projections
may enter cursor storage; these raw inputs remain confined to test memory.

Run commands and the evidence matrix are in [the suite guide](../../conformance/README.md).
