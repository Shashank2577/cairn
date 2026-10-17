# Blind accuracy: GoogleCloudPlatform/microservices-demo (Online Boutique)

Third real product (T002), commissioned because the robot-shop fixes were developed against that fixture,
which made it a regression floor instead of a blind measure. Pinned commit `38e7348` (Apache-2.0), fetched
into the test cache, not vendored. Ground truth committed on its own (`7beab4d`) before extraction ran,
written from the README service table, the architecture diagram and the Kubernetes manifest names only —
no source file was read before the score below was recorded. Test:
`tests/system/test_blind_online_boutique.py` (pins these numbers as a regression floor).

| | Recall | Precision (extracted claims) |
|---|---|---|
| Elements | **0.92** (12 of 13) | **0.79** |
| Relationships | **0.00** (0 of 17) | **0.00** |
| Labelled inferred or ambiguous | 16% of the view | |

## The finding: gRPC

Every service-to-service call in this product is **gRPC** (the README says so, and the code confirms it),
and the extractor has no gRPC client rules. Relationship recall 0.00 is the honest measurement of that
gap: containers and stores are found, the calls between them are not. Fix: gRPC client rules over the
existing `http` rule shape — the connect target (`grpc.NewClient(addr)` and friends) is an address like
any other, the manifest's env values (`PRODUCT_CATALOG_SERVICE_ADDR=productcatalogservice:3550`) resolve
it, and the `http-config`/`http-route-match` machinery applies unchanged (task T058).

## Elements: what matched and what did not

- Missed: `ecb` (the European Central Bank rates fetch, flagged "uncertain from docs alone" in the truth).
  Honest miss; would need a config-URL or SDK rule for it.
- Extra (`false` by this truth, but real in the repository): `shoppingassistantservice` — present in
  `src/` at the pinned commit but absent from the README's service table and from the main Kubernetes
  manifests, so the doc-derived truth could not list it — and its PostgreSQL, which the extractor found
  from the source. The truth stays frozen; these are recorded as honest extras, like robot-shop's
  `fluentd` and `load`.
- Extra (artifact): a container named `src` (investigated in T059) and the two PostgreSQL relationships
  attributed to `productcatalogservice` and `src` (a store use whose owner attribution and target
  resolution both deserve a look; T059).

## Known attribution gap this run exposed

`cartservice`'s Kubernetes unit has only an image (no Dockerfile in the repo), so the build marks it
`image_only`, provenance inferred — and `owner()` never assigns code to image-only units, so its C#
Redis use (StackExchange rule exists) is unattributed and the `cartservice -> redis-cart` edge is missed.
Fixing image-with-source attribution is recorded as part of T059.

## After the gRPC fixes (T058, T059) — 2026-10-15

Developed openly against this fixture after the blind numbers above were recorded, robot-shop style:

- **T058, gRPC client rules**: `grpc.NewClient`/`Dial` (Go), `grpc.insecure_channel` (Python),
  `ManagedChannelBuilder.forAddress` (Java), `GrpcChannel.ForAddress` (C#) as ordinary `http`-shape
  catalog rules — a gRPC connect target is an address like any other. Plus **configured-address
  wiring**: an address-marked environment name (…HOST, …ADDR, …URL, …ENDPOINT) the deploy description
  wires to a sibling names the edge even when both the call and the read hide behind first-party
  helpers (this product's `mustMapEnv`/`mustConnGRPC`), the value resolves the target and is never
  stored, and an address the product cannot resolve (an OTEL collector) makes no claim. The protocol
  label ("gRPC") is claimed only when a real client call site carries the edge, never from wiring
  alone.
- **T059, image-with-source attribution**: a Kubernetes workload with only an image whose name matches
  a path segment of a code unit (`cartservice` and `src/cartservice/src/Dockerfile`) adopts that unit's
  code and its name — no more `src` container, extracted provenance. The ASP.NET Redis registration
  (`AddStackExchangeRedisCache`) joined the stackexchange-redis rule with env-style config keys
  (REDIS_ADDR and friends), which is what closed `cartservice -> redis-cart`.

| | Recall | Precision (extracted claims) |
|---|---|---|
| Elements | **0.92** (12 of 13) | **0.86** |
| Relationships | **0.94** (16 of 17) | **0.84** |
| Labelled inferred or ambiguous | 15% of the view | |

The only remaining misses are the ECB rates fetch (flagged "uncertain from docs alone" in the truth; it
would need an SDK or config rule for a system the docs themselves hedge on). The extra elements and
edges are real at this commit and absent from the doc-derived truth: `shoppingassistantservice` (in
`src/`, absent from the README table and the main manifests) and the Postgres stores — the README says
productcatalog "provides the list of products from a JSON file" while the code reads Postgres via pgx,
and cartservice ships an AlloyDB (Postgres) cart store beside the Redis one. **The extractor was right
and the documentation was stale**, which is rather the point of the feature.

These numbers are no longer blind (the rules were developed against this fixture); the test pins them
as a regression floor. The unbiased protocol is now proven on three products: truth from docs committed
first, blind run recorded, fixes, floor. The next unbiased round needs a fourth product — ideally one
with GraphQL or a service mesh, the two call styles still unmeasured.
