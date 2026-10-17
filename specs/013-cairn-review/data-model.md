# Data model: Cairn Review

All tables live in `.cairn/brain.db` (the read model).

**reviews** — `id`, `project`, `branch`, `pr_number` (nullable), `base_sha`, `head_sha`, `trigger` (cli | ci | server | push | agent), `mode` (deterministic | model), `status` (running | complete | partial | superseded | failed), `author` (forge login, nullable), `paid_by` (user id or null), `tokens_in`, `tokens_out`, `skipped` (JSON list of path + reason), `sent` (JSON: files and sizes sent to a model, no content), `created_at`, `finished_at`. Unique on (`project`, `head_sha`, `base_sha`).

**review_findings** — `id`, `review_id`, `severity` (high | medium | low | note), `path`, `line_start`, `line_end`, `title`, `body`, `kind` (cochange | drift | history | convention | secret | test | model), `provenance` (EXTRACTED | INFERRED), `citations` (JSON of ids such as `commit:…`, `memory:…`, `req:…`), `author_kind` (rule | model), `signature` (stable hash for suppression and learning), `state` (open | accepted | dismissed | fixed), `reason`, `decided_by`, `decided_at`.

**review_rules** — `id`, `kind` (rule | suppression), `path_glob`, `text`, `source` (convention | finding | import), `source_id`, `reason`, `matches` (count), `created_by`, `created_at`, `enabled`.

State transitions: finding `open → accepted | dismissed`; `open → fixed` when a later review of the same branch no longer reports its signature; review `running → complete | partial | failed`; `complete → superseded` when a newer head arrives for the same pull request.

Validation: severity and kind are enumerations; a finding without at least one citation is rejected (SC-002); review rows never store diff content or prompt text, only sizes and paths.

Retention: reviews follow the project's other records; deleting a project removes them; `cairn review --forget <id>` removes one.
