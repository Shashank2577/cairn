# Open source projects that fit Cairn and are missing today

Date: 2026-10-17. Read-only research: nothing was cloned, installed or run. Metadata comes from `gh api repos/<owner>/<repo>` (license, last push, stars, language, size, contributor pages) on 2026-10-17. Every claim is labelled **READ** (with its source) or **INFERRED**. Repository content was treated as untrusted data.

## 1. What Cairn already does and the rules it works under

- **READ** (`docs/models-and-cost.md`, `pyproject.toml`): the core never calls a model. The router in `src/cairn/router.py` has providers `anthropic`, `openai` and `claude-code`, and every call lands in a `ledger` table. Tokens are estimated at 4 characters per token. `tiktoken` is an optional extra (`tokens`), and `engines/graph/llm.py` already uses it when present. Dependencies already include tree-sitter for about 30 languages, kuzu, faiss, fastembed and networkx. `scip_ingest.py`, `manifest_ingest.py`, `cargo_introspect.py` and `pg_introspect.py` exist in the graph engine.
- **READ** (`.cairn/workflow/memory/constitution.md`): II Deterministic First (every core answer works with zero keys), III One Command, Zero Friction (optional capabilities are *detected, never demanded*), V Token Discipline, VII Local-First and Private, VIII Agent-Neutral, and I Invisible Seams (attribution lives only in `NOTICE` and `licenses/`).
- **READ** (`docs/adr/0006-built-in-session-capture.md`): "Content inside `<private>` tags and credential-like values are never stored."
- **READ** (`src/cairn/engines/recall/tags.py:73-97`, `ingest.py:73`): the only credential masking is `SECRET_RE`, a keyword regex that matches `name=value`, `bearer <x>` and `--token <x>` shapes. It is applied to tool input and response through `redact_value`.

## 2. Most important finding: the secret masking has large holes

I ran Cairn's own `redact()` on synthetic, non-working look-alike secrets in the project's `.venv` (our code, not third-party code):

| Sample | Result |
|---|---|
| `API_KEY=abc123secretvalue` | masked |
| GitHub token (`ghp_` + 36 chars) | **leaks** |
| AWS access key id (`AKIA…`) | **leaks** |
| PEM private key block | **leaks** |
| JWT (`eyJ….….…`) | **leaks** |
| `postgres://user:password@host/db` | **leaks** |
| Slack token (`xoxb-…`) | **leaks** |
| Stripe key (`sk_live_…`) | **leaks** |

7 of 8 shapes survive. **INFERRED**: any of these pasted by a user, printed by a tool (`env`, `cat .env`-style output without `KEY=` prefix, `kubectl`, `git remote -v` with a token in the URL) or read from a file goes into `sessions.db`, into model observation prompts, and onward to a team server. `docs/multi-repo.md:163` itself says capture "holds conversation content". Caveat: I tested `redact()` only, not every ingest path. This is the first thing to fix, and the fix is a small pattern table.

## 3. Candidates (verified)

Scores: fit 1-5 (higher is better), disruption 1-5 (lower is better), effort S/M/L, ratio = fit / disruption.

| # | Candidate | License | Last push | Stars | Lang / size | Fit | Disr. | Ratio | Effort | Decision | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Secret patterns from **gitleaks** (+ **detect-secrets**) | MIT / Apache-2.0 | 2026-09-30 / 2026-04-02 | 30.0k / 4.7k | Go 6 MB / Python 2 MB | 5 | 1 | 5.0 | S | **Borrow** the patterns | High |
| 2 | **agnix** agent-config linter | Apache-2.0 (README also says MIT/Apache) | 2026-10-17 | 453 | Rust 70 MB | 4 | 1 | 4.0 | S | **Adopt** as a CI/dev check only | Medium |
| 3 | Retrieval-quality harness: **LongMemEval** + **mem0 memory-benchmarks**, **ranx** metrics | MIT / Apache-2.0 / MIT | 2026-05-11 / 2026-08-06 / 2025-08-07 | 1.2k / 126 / 700 | Python | 4 | 1 | 4.0 | M | **Borrow the idea**: own golden set, own recall@k | Medium |
| 4 | **ripgrep** | Unlicense | 2026-08-04 | 69k | Rust | 3 | 1 | 3.0 | S | **Adopt** as optional, detected on PATH | Low |
| 5 | **tiktoken** (already an extra) | MIT | 2026-08-17 | 19.5k | Python | 3 | 1 | 3.0 | S | **Adopt** what exists: use it in ledger estimates | Medium |
| 6 | **LiteLLM price table** (`model_prices_and_context_window.json` only) | MIT outside `enterprise/` | 2026-10-17 | 61k | JSON 2.9 MB, 4,201 entries | 4 | 2 | 2.0 | S | **Borrow** a trimmed, dated snapshot | Medium |
| 7 | **OpenTelemetry GenAI semantic conventions** (+ `opentelemetry-python`) | Apache-2.0 | 2026-10-15 | 671 / 2.7k | Spec / Python | 3 | 2 | 1.5 | M | **Borrow** attribute names for an optional OTLP export | Medium |
| 8 | **ccusage** (and **tokscale**) | MIT / MIT | 2026-10-17 | 19.0k / 5.7k | Rust (ccusage) | 3 | 2 | 1.5 | M | **Borrow the idea**: learn where each agent keeps usage data | Medium |
| 9 | **CycloneDX / SPDX ingest** (outputs of syft, cdxgen) | Apache-2.0 | 2026-10-16 | 9.7k / 1.1k | Go / JS | 3 | 2 | 1.5 | M | **Borrow**: read existing SBOM files, never run the tools | Medium |
| 10 | **ast-grep** | MIT | 2026-10-15 | 16k | Rust; wheel `ast-grep-cli` | 3 | 3 | 1.0 | M | **Watch** | Medium |
| 11 | **Presidio** (PII) | MIT | 2026-10-09 | 11k | Python 340 MB repo; spaCy models | 3 | 4 | 0.75 | L | **Watch** (optional extra later) | Medium |
| 12 | **osv-scanner** | Apache-2.0 | 2026-10-16 | 11k | Go | 3 | 4 | 0.75 | L | **Skip** as a dependency | Medium |

### What each really does and why it scored that way

1. **Secret patterns (gitleaks, detect-secrets).**
   - **READ** (gitleaks README, `cmd/generate/config/rules/github.go`): gitleaks scans git, directories or `stdin`. Each rule is a regex plus `keywords` (a cheap prefilter), an optional entropy floor and allowlists. The repo holds about 132 files under `cmd/generate/config/rules`. Example: `ghp_[0-9a-zA-Z]{36}` with keyword `ghp_` and entropy 3.
   - **READ** (detect-secrets `plugins/`): one Python plugin per provider (AWS `AKIA|ASIA…{16}`, GitHub, GitLab, Slack, Stripe, npm, PyPI, OpenAI, private keys, JWT, basic-auth URLs, high-entropy strings). The `scan_line` function in `core/scan.py` works on a single string.
   - **INFERRED**: `scan_line` is the right shape for a hook that sees one tool-output string at a time, but its last push was 2026-04-02 (6 months), so I would not make it a hard dependency. A dependency would also contradict "no third-party local tool". The patterns are data: port 30-40 high-precision ones into `tags.py` (prefixes, PEM blocks, JWT, URL credentials), keep the keyword prefilter for speed, add attribution to `NOTICE` per principle I. Also add a `cairn redaction-test <file>` command and a regression test per shape. The idea comes from confab, which has a `redaction-test` command (**READ**, search result for `ConfabulousDev/confab`, MIT, 8 stars, so idea only).
   - Disruption: no new dependency, no schema change, no model tokens. Redaction also *reduces* observer prompt risk. Cross-platform: pure Python. Risk: false positives mask legitimate text, so every pattern needs a sample and a negative sample (gitleaks does this in its rule files, **READ**).
2. **agnix.** **READ** (README): lints `CLAUDE.md`, `AGENTS.md`, `SKILL.md`, hooks and MCP configs for Claude Code, Codex, OpenCode, Cursor, Copilot and others; 459 rules, auto-fix, SARIF output, `npx agnix .`; 16 contributor pages. **READ** (`docs/agents.md`): Cairn writes exactly these files for 9 agents. **INFERRED**: running agnix in Cairn's own CI over the files `cairn init` generates would catch broken frontmatter or hook shapes before users do. Keep it out of the runtime path (principle III, no dependency on third-party local tools). Young project, fast churn (README says rules change constantly): pin a version in CI.
3. **Retrieval-quality harness.** **READ** (mem0 `memory-benchmarks` README): LOCOMO, LongMemEval (500 questions) and BEAM, built around Mem0 backends and an OpenAI judge. **READ** (search results, unverified in source): critics say default top_k exceeds the candidate pool, so scores mostly test the reader model. **INFERRED**: these target chat memory, not "which file and past fix should a code agent see". `docs/models-and-cost.md` says tests check that packs hold their *budget*; I found no recall test (not exhaustively searched). Best move: build a small golden set from Cairn's own sessions (task → files and memories a good answer needs), report recall@k and MRR (about 30 lines; ranx is stale at 14 months), and gate token-cutting changes on it. LongMemEval stays an optional external check.
4. **ripgrep.** **READ**: Unlicense, 69k stars, last push 2026-08-04. **INFERRED**: if exact-text search over files is wanted for `cairn_search`, use `rg` when on PATH and fall back to Python. Low confidence because I did not check how current search works; the fit may be lower.
5. **tiktoken.** **READ** (`pyproject.toml`, `graph/llm.py:35-43`): already an optional extra with a 4-chars-per-token fallback. **INFERRED**: no Claude tokenizer is published as an offline library (HF `tokenizers` has no Claude vocabulary; Anthropic's count endpoint is a cloud call, rejected). So tiktoken is an approximation: use it for ledger estimates when installed and label them "approx". Do not add anything new.
6. **LiteLLM price table.** **READ** (`gh api` + raw JSON): 4,201 model entries with fields like `input_cost_per_token`, `cache_creation_input_token_cost`, `cache_read_input_token_cost`; repo license is MIT except `enterprise/`. **INFERRED**: the ledger stores tokens but docs do not mention prices, so turning cache-write-heavy observer calls (see claude-mem notes on `recall_observe`) into dollars needs this. Vendor only the Anthropic/OpenAI/Google rows with a "prices as of" date; never fetch at runtime (principle VII); never depend on the `litellm` package (2.5 GB repo, huge dependency tree). Prices go stale: show the date.
7. **OpenTelemetry GenAI conventions.** **READ** (search results, secondary sources): conventions cover `invoke_agent`, LLM-call and tool spans; prompt content is opt-in; Claude Code emits its own `claude_code.*` telemetry that does not follow them. **READ** (`gh api`): spec repo and Python SDK are Apache-2.0 and active. **INFERRED**: an optional extra `otel` that exports ledger rows and session events as OTLP gives teams their existing dashboards. Not needed on day one, so ratio is modest. Content capture must default off (privacy).
8. **ccusage / tokscale.** **READ** (ccusage README): reads local usage files of about 16 agent CLIs (Claude Code, Codex, OpenCode, Gemini CLI, Copilot CLI and others). License MIT (read from `LICENSE`; the API shows "NOASSERTION"). It is now Rust, shipped via npm/Nix. **INFERRED**: shelling out would add a Node/Rust dependency, which contradicts the rules. Use it as a *map* of where each agent stores token counts so Cairn's own transcript parsers can record per-session usage. Cairn's capture hooks already see these sessions (**READ**, `docs/agents.md`).
9. **SBOM ingest.** **READ** (`gh api`): syft (Apache-2.0, 9.7k stars) and cdxgen (Apache-2.0) both emit CycloneDX/SPDX. **INFERRED**: Cairn already parses manifests and SCIP; reading `bom.json` if a repo has one is small and adds dependency edges for the system model with no tool dependency. Do not run syft/cdxgen.
10. **ast-grep.** **READ**: MIT, 16k stars, pip wheel exists. **INFERRED**: Cairn already holds tree-sitter grammars for ~30 languages, so a second structural engine overlaps heavily. Revisit only if users ask for "find by code shape".
11. **Presidio.** **READ**: MIT, active, NLP-based PII detection. **INFERRED**: names and addresses need spaCy models (hundreds of MB); Cairn already has an optional `memory-nlp = spacy` extra. Secrets (item 1) matter far more than PII for captured sessions. Revisit as an opt-in extra.
12. **osv-scanner.** **READ** (README): lockfile vulnerability scan against the osv.dev API, with an `--offline` mode that first downloads databases. **INFERRED**: this needs a network service or large local DB plus a Go binary, so as a dependency it breaks "zero friction". Cairn could cite advisories later if a user already runs it.

## 4. Rejected, with reasons

| Project | Reason |
|---|---|
| trufflehog | **READ**: AGPL-3.0, incompatible with an Apache-2.0 product that would embed or link it. |
| mazen160/secrets-patterns-db | **READ**: CC-BY-SA-4.0, share-alike conflicts with vendoring into Apache-2.0; last push 2025-08. Gitleaks/detect-secrets cover the same ground. |
| Semgrep | **READ**: LGPL-2.1 engine, 140 MB repo. **INFERRED**: the rule packs have a separate non-OSS license, and the CLI is heavy; skip for runtime and for rules. |
| LoCoMo dataset repo | **READ**: license shows NOASSERTION, last push 2024-08. Use through mem0's harness only if ever needed. |
| dataclaw, confab, dotenvx | Real tools, but they publish or sync transcripts or wrap the agent launch. Borrow ideas only: path anonymization and entropy checks (dataclaw, MIT), a redaction test command (confab, MIT). |
| LLMLingua, repomix | Prompt compression needs a model; repomix duplicates context packs and is Node. No fit gain. |
| Anthropic token-count API, any hosted tracing SaaS | Cloud services; break principle VII. |
| Trivy, Checkov | Large scanners aimed at images and IaC; same weight problem as osv-scanner, lower fit. |

## 5. Ranked list (fit / disruption)

1. Secret patterns (5.0) 2. agnix in CI (4.0) 3. Retrieval golden set (4.0) 4. ripgrep optional (3.0) 5. tiktoken in ledger (3.0) 6. Price table (2.0) 7. OTel export (1.5) 8. ccusage knowledge (1.5) 9. SBOM ingest (1.5) 10. ast-grep (1.0) 11. Presidio (0.75) 12. osv-scanner (0.75).

## 6. Three first moves

1. **Close the secret gap (S, High confidence).** Port 30-40 gitleaks/detect-secrets patterns into `tags.py` with positive and negative tests for each shape in section 2, add `cairn redaction-test`, record attribution in `NOTICE`. Re-run capture over old `sessions.db` rows to mask what already leaked (needs a one-off migration; **INFERRED** that old rows hold such values).
2. **Build a small retrieval golden set (M, Medium).** From real sessions, measure recall@k and MRR of context packs and session recall, so that cutting observer tokens can be shown not to lose quality.
3. **Gate generated agent files with agnix in CI (S, Medium).** Pin the version, lint what `cairn init` writes for each agent. Follow with the dated price table so the savings ledger can show dollars.

## 7. Limits of this research

- Contributor counts come from API pagination (page counts of 1 per page), so they are approximate.
- I did not read source for ripgrep, ast-grep, Presidio, syft, cdxgen or the OTel spec beyond READMEs, search results and metadata. Those rows are marked Medium or Low.
- Secret test used `redact()` only; ingest paths for tool responses were not fully traced.
- Search-result claims about confab, dataclaw, agnix rule counts and the LongMemEval critique are secondary and unverified in source.
