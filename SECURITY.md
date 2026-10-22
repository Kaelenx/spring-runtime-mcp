# Security Policy

This project exists to give coding agents *safe* access to Spring Boot runtime
state, so anything that lets a tool call write to a target, reach a host other
than the registered origin, follow a redirect, bypass TLS or target
authentication, exceed the response budget, or leak a credential, raw
`env`/`configprops` value, exception text or upstream free text into a result,
cache entry or log is a security bug.

## Reporting

Please use GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**) rather than a public issue or pull
request. Include the tool name, the arguments, a sanitized copy of the Actuator
or OpenAPI response that triggered the behaviour, and the client you used.

You should receive an acknowledgement within a few days. Fixes are released as
a patch version with a CHANGELOG entry; please allow time for that before
disclosing publicly.

## Scope

In scope:

- The MCP server in `src/spring_runtime_mcp/` and the published wheel.
- The committed tool contract (`tools-contract.json`) and the sanitization /
  output-guard rules the tests enforce.

Out of scope:

- Misconfiguration of the *target* application (for example exposing
  `heapdump` or `show-values: always`). The target setup guide documents the
  intended read-only configuration; the server is designed to stay safe even
  when the target is over-exposed, so reports where it does not are in scope.
- Vulnerabilities in Spring Boot, the MCP client, or Python itself.

## What we will not do

A failing security test is never weakened, skipped or deleted to make a build
green. If a legitimate change conflicts with a static rule in
`tests/security/`, the conflict is recorded and resolved explicitly.
