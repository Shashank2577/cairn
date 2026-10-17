# What framework support depends on

"Support every framework Cairn supports" has no natural end, so this page states exactly what the
deterministic extraction (zero model calls) needs, what Cairn already has, and how a new framework is
added as **data plus tests** rather than code.

## 1. Three things a framework needs

| Need | Comes from | Cost |
|---|---|---|
| **A parser for the language** | a tree-sitter grammar Cairn already bundles (section 4) | none if bundled |
| **A syntax pattern for routes, clients and stores** | one of five pattern kinds (section 2), written as a catalog rule | a rule plus a fixture |
| **A detection signal** | a dependency in the manifest (`pyproject.toml`, `package.json`, `pom.xml`/`build.gradle`, `go.mod`, …) | already read by Cairn's manifest parsers |

## 2. The five pattern kinds (the engine implements these once)

| Kind | Example | What the engine reads from the parse tree |
|---|---|---|
| `decorator` | `@app.get("/orders")` (FastAPI, Flask) | decorator name, positional and keyword string arguments, the decorated function |
| `annotation` | `@GetMapping("/orders")` on a method, `@RequestMapping("/api")` on the class (Spring) | annotation name and `value`/`path` arguments, class-level prefix joined to method-level path |
| `call` | `app.post("/checkout", handler)` (Express, Fastify, Gin, net/http), `fetch(\`${BASE}/api/orders\`)`, `r.publish("order.created")` | receiver and method name, first string or template argument, the handler or callee |
| `file-route` | `app/api/orders/route.ts` exporting `POST` (Next.js), `urls.py` `path("orders/", view)` (Django) | file path convention plus exported names, or a list of route calls |
| `config` | `DATABASE_URL`, compose `image: postgres:16`, `spring.datasource.url` | key names (never values) in env, compose, Kubernetes and properties files |

**Today**: Python and TypeScript emit decorator *edges* (the name, not the arguments); Java collects
annotation *names* (not the arguments); Go has no decorator concept and no route extraction. So the build
adds argument capture for `decorator` and `annotation`, and the `call` and `file-route` kinds, once each in
the engine (task T013). After that, a framework is a catalog entry.

## 3. A catalog rule is data

Rules live in `src/cairn/system/catalog/frameworks/*.yaml`; a project can add its own in
`.cairn/catalog/`. A rule names the language, the detection dependency, and patterns of the five kinds:

```yaml
framework: fastapi
language: python
detect: {dependency: fastapi}
routes:
  - kind: decorator
    match: "{receiver}.{method}"          # receiver is an APIRouter or FastAPI instance
    methods: [get, post, put, patch, delete]
    path: arg0
  - kind: call
    match: "{receiver}.include_router"   # mount prefix
    prefix: kwarg.prefix
---
framework: express
language: javascript
detect: {dependency: express}
routes:
  - kind: call
    match: "{app|router}.{method}"
    methods: [get, post, put, patch, delete, all]
    path: arg0
---
framework: spring-web
language: java
detect: {dependency: org.springframework.boot:spring-boot-starter-web}
routes:
  - kind: annotation
    match: [GetMapping, PostMapping, PutMapping, PatchMapping, DeleteMapping, RequestMapping]
    path: [value, path, arg0]
    class_prefix: RequestMapping
```

Adding a framework means one YAML rule, one fixture repository with its ground truth, and the golden test
that runs on it. No engine change unless the framework needs a sixth pattern kind.

## 4. First build, and what the catalog could grow into

**First build (fixtures and golden tests in this feature):**

| Language (bundled parser) | Inbound routes | Outbound HTTP | Messaging | Stores |
|---|---|---|---|---|
| Python | FastAPI, Flask (`decorator`), Django (`file-route` via `urls.py`) | requests, httpx (`call`) | redis-py, kafka-python/confluent, pika, boto3 SQS | psycopg, asyncpg, SQLAlchemy engine URLs, pymongo |
| JavaScript / TypeScript | Express, Fastify (`call`), Next.js route handlers (`file-route`) | fetch, axios | ioredis/redis, kafkajs, amqplib, AWS SDK SQS | pg, mysql2, mongodb, Prisma datasource |
| Java | Spring MVC/WebFlux (`annotation`) | RestTemplate, WebClient, Feign (`annotation`) | Spring Kafka, Spring AMQP | JDBC URLs, Spring Data config keys |
| Go | net/http, Gin (`call`) | net/http client | go-redis, segmentio/kafka-go, amqp091 | database/sql drivers, pgx |

**Other languages Cairn already parses** (bundled grammars: no new dependency to add rules for them):
C, C++, C#, Kotlin, Scala, Groovy, Ruby, PHP, Swift, Rust, Lua, Zig, Elixir, Objective-C, Julia, Verilog,
Fortran, PowerShell, Bash, JSON, YAML. **Optional extras** (installed on demand): SQL, HCL/Terraform,
Pascal, OCaml, Common Lisp, VB.NET, R, Erlang, Solidity, DM.

Natural next catalog entries on those parsers: ASP.NET Core (C#, `annotation` + `call`), Ktor and Spring
(Kotlin), Rails (Ruby, `file-route` via `routes.rb`), Laravel and Symfony (PHP), Axum and Actix (Rust,
`call` and attribute macros), Phoenix (Elixir, `call` in the router), Play (Scala), Vapor (Swift).

## 5. What stays out

- Routing defined only at runtime or in a cloud console: declared in `system.yaml` instead.
- Dynamically built paths that never contain a literal or a template: the call is kept and its target is
  marked "unresolved HTTP target", not guessed.
- Reflection, metaprogramming and code generation without generated files in the repository.

Every rule runs on the parse Cairn already makes during sync, so adding frameworks adds no model calls and
only the cost of matching patterns against nodes already in memory.
