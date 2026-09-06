# Repository Guidelines

## Project Structure & Module Organization

`titanium/` contains trading logic: signal gates, risk and sizing,
MT5 execution, data adapters, analysis, and the web dashboard. `tradingagents/`
contains the LLM deliberation graph and provider clients; `cli/` exposes the
command-line application. Put maintenance and analysis utilities in `tools/`,
tests in `tests/`, configuration in `config/`, and audit notes in
`docs/`. MQL5 sources live under `titanium/bridge/`. Treat `results/`, `data/`,
logs, caches, and local `.env` files as private unless a sealed artifact is
committed.

## Build, Test, and Development Commands

Use the project virtual environment on Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_limit_orders.py -q
.\.venv\Scripts\python.exe -m ruff check titanium tradingagents cli tools tests
```

The editable install provides the `tradingagents` CLI. Use the repository
`.bat` launchers only when the requested PAPER/DEMO service operation is
explicitly authorized; they may start MT5-facing processes.

## Coding Style & Naming Conventions

Target Python 3.10+, use four-space indentation, type hints for public APIs,
and a 100-character line target. Ruff enforces Pyflakes, pycodestyle, isort,
BugBear, pyupgrade, comprehensions, and simplification rules. Use
`snake_case` for modules/functions, `PascalCase` for classes, and
`UPPER_SNAKE_CASE` for constants. Keep Hermes decisions separate from
deterministic execution guards and make persisted artifacts reproducible.

## Testing Guidelines

Pytest discovers `tests/test_*.py`. Add focused regression tests with every
behavioral fix; prefer isolated fixtures and deterministic inputs. Available
markers are `unit`, `integration`, and `smoke`. External API, MT5, or live
provider tests must skip safely when credentials/services are absent. Run the
targeted test first, then the complete suite before integration.

## Commit & Pull Request Guidelines

History favors concise imperative subjects such as `fix: ...`, `docs: ...`,
or `V14: ...`. Keep commits single-purpose. Pull requests must explain the
problem, implementation, operational risk, test evidence, and any affected
configuration or sealed artifacts; link the relevant task and include UI
screenshots only for visible dashboard changes.

## Security & Agent Workflow

Never commit secrets or read/write `.env`; update `.env.example` instead. V14
remains PAPER/DEMO only. Aucun skill n'autorise un ordre reel, la modification
de `.env`, l'armement, le redemarrage d'un service, ou une promotion de seuil
ou de configuration sans validation humaine explicite. Before agent
collaboration, read `collab/HERMES_BRIDGE.md`. Run
`tools/gitnexus_team.ps1 sync`, inspect impact before editing code, and run
GitNexus `detect-changes` before committing. Hermes is V14's cognitive decision
pilot in DEMO. The 2026-09-06 mandate permits Hermes on the critical path and
MT5 calls through guarded DEMO execution; this is authority, not a bypass.
No real orders or arming; missing, stale or inconsistent inputs mean `WAIT`.
Hermes publishes sealed, fresh `ALLOW/WAIT/BLOCK` policies. Use `.agents/skills`
as the canonical local skill catalog and publish handoffs via the CollabHub
documented in the bridge. Preserve unrelated dirty-worktree changes.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **titanium-v14** (11371 symbols, 22409 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/titanium-v14/context` | Codebase overview, check index freshness |
| `gitnexus://repo/titanium-v14/clusters` | All functional areas |
| `gitnexus://repo/titanium-v14/processes` | All execution flows |
| `gitnexus://repo/titanium-v14/process/{name}` | Step-by-step execution trace |

<!-- gitnexus:end -->
