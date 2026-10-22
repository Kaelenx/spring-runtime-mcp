# Synthetic Bean and Condition Fixtures

These disposable, sanitized examples are authored from the ExecPlan's inline
JSON structures. They are not recordings from a running Spring Boot instance
and do not establish compatibility with any particular Boot release.

- `parent-child/beans.json`: parent lookup, aliases, local name shadowing, and
  three-hop dependencies; Stripe gateway/client are intentionally absent.
- `stripe-missing/conditions.json` and `source-catalog.json`: interface-to-
  implementation mapping, missing required client, and missing property at the
  client's factory method. The catalog describes source expectations, not
  observed runtime Beans.
- `factory-method/`: replaces the gateway component condition source with
  `example.PaymentConfiguration#stripePaymentGateway`; its declared return type
  is the interface, with an explicit implementation type. Reuses parent-child
  Beans rather than duplicating data.
- `nested-conditions/`: ANY containing ALL and NONE, explicit condition-class
  paths, and an observed `RemoteClient` for the nested positive Bean match.
- `shape-metadata.json`: documents synthetic Boot 2.7/3.x/4.x structural profiles.
  Its version labels are coverage intentions, never runtime version evidence.

The shared `contexts`, `parentId`, aliases, and matched/notMatched shapes are
tested independently of media-type or framework-version inference. No secrets,
production addresses, real source files, or credentials are included. Fixture
loaders return a new object per read so mutation cannot contaminate another test.
All default tests block outbound DNS and socket connections.
