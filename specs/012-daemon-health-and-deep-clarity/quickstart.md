# Quickstart: validate 012

1. Healthy: `cairn up`, `cairn doctor` shows Server and Deep rows with no warning; `node tests/ui/smoke.mjs --base <url>` exits 0.
2. Stale code: copy the package to a temp dir, start the server from it, delete the dir; `cairn status` and `cairn doctor` name `code-missing` and the fix; the UI shows one banner; `cairn up` replaces the server and the smoke check passes.
3. Deep reason: run the server with PATH lacking `claude` (CLI present in `/opt/homebrew/bin`): deep is on and names the path; with no CLI and no key: off, with the exact fix.
4. Budgets: doctor and Project settings list three budgets with value, usage and consequence.
5. `pytest -q` (green) including the browser smoke case on a machine with Chrome and Node 22.
