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
`UPPER_SNAKE_CASE` for constants. Keep deterministic decisions separate from
LLM advice and make persisted artifacts reproducible.

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
GitNexus `detect-changes` before committing. Prime is the technical lead; use
`.agents/skills` as the canonical local skill catalog and publish handoffs via
the CollabHub documented in the bridge. Preserve unrelated dirty-worktree changes.
