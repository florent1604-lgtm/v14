# DeepSeek V4 Flash and K3s Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect the guarded V14 cognitive worker to DeepSeek V4 Flash and package the read-only API, SSE stream, and Windows MT5 DEMO adapter for resource-bounded K3s on WSL2.

**Architecture:** A focused DeepSeek client supplies structured JSON and safe cache metrics to the existing Hermes cortex. Linux K3s hosts the FastAPI/SSE service, cognitive worker, and an internal adapter; native Windows remains the only process allowed to import MetaTrader5 or reach the DEMO terminal.

**Tech Stack:** Python 3.12, OpenAI Python SDK, FastAPI, SSE, Docker, Kubernetes/K3s, WSL2, pytest, Ruff.

## Global Constraints

- PAPER/DEMO only; no real order and no weakening of RiskGate or the account wall.
- Never read, print, copy, or commit the local `.env` value.
- DeepSeek endpoint is exactly `https://api.deepseek.com`; model is `deepseek-v4-flash`.
- K3s runs in Linux under WSL2; MetaTrader5 remains native Windows.
- Every container has CPU, memory, and ephemeral-storage requests and limits.
- Existing unrelated dirty-worktree changes remain untouched.

---

### Task 1: DeepSeek client and cache telemetry

**Files:**
- Create: `titanium/deepseek_client.py`
- Create: `tests/test_deepseek_client.py`

**Interfaces:**
- Produces: `DeepSeekClient.from_env()`, `DeepSeekClient.complete_json(prompt, system)`, `DeepSeekUsage.to_dict()`.
- Consumes: `DEEPSEEK_API_KEY` from the process environment and the OpenAI-compatible Chat Completions API.

- [ ] Write tests that require the official endpoint/model, fail closed with a missing key, parse structured JSON, normalize both DeepSeek cache counters and OpenAI cached-token counters, and redact provider errors.
- [ ] Run `pytest tests/test_deepseek_client.py -q` and verify failures are caused by the missing module.
- [ ] Implement a client with injected transport support, bounded timeouts, no secret-bearing exception text, and a stable prompt prefix.
- [ ] Run `pytest tests/test_deepseek_client.py -q` and verify all tests pass.

### Task 2: Connect DeepSeek to the existing cognitive worker

**Files:**
- Modify: `titanium/hermes_cortex.py`
- Modify: `tests/test_hermes_abonnement.py`
- Create: `tests/test_hermes_deepseek.py`

**Interfaces:**
- Consumes: `DeepSeekClient.complete_json`.
- Produces: the existing `_ask()` dictionary contract and `circuit_status()` fields without exposing credentials.

- [ ] Run GitNexus impact analysis for `_ask` and `circuit_status`; stop and report any HIGH or CRITICAL result before editing.
- [ ] Add failing tests proving that `deepseek-api` routes through the new client, cache usage is journaled without secrets, and provider failure opens the existing circuit and yields the current fail-closed behavior.
- [ ] Run the two targeted test files and verify RED.
- [ ] Add the provider branch while keeping Ollama and CLI transports available as explicit fallbacks.
- [ ] Run the targeted tests and verify GREEN.

### Task 3: Container entry points and health checks

**Files:**
- Create: `deploy/k3s/Dockerfile`
- Create: `deploy/k3s/entrypoint.py`
- Create: `titanium/web/mt5_adapter.py`
- Create: `tests/test_k3s_entrypoint.py`
- Create: `tests/test_mt5_adapter.py`

**Interfaces:**
- Produces: roles `api`, `deepseek-worker`, and `mt5-demo-adapter`; endpoints `/healthz`, `/readyz`, and internal proxy endpoints.
- Consumes: existing `titanium.web.live_app`, `tools.analystes`, and a Windows bridge URL/token supplied through environment variables.

- [ ] Write failing tests for role selection, health endpoints, DEMO-only configuration, allowed routes, authentication, and rejection of unknown or real-account requests.
- [ ] Run both targeted files and verify RED.
- [ ] Implement the smallest entrypoint and adapter that satisfy the tests; the adapter must never import MetaTrader5.
- [ ] Run targeted tests and verify GREEN.

### Task 4: K3s manifests with strict quotas

**Files:**
- Create: `deploy/k3s/titanium-v14.yaml`
- Create: `deploy/k3s/kustomization.yaml`
- Create: `tests/test_k3s_manifests.py`

**Interfaces:**
- Produces: namespace, ResourceQuota, LimitRange, three Deployments, and internal Services.
- Consumes: Secret names `titanium-deepseek` and `titanium-mt5-bridge`, never literal values.

- [ ] Write a failing manifest contract test that parses all resources and checks replicas, probes, security contexts, DEMO flags, secret references, image pull policy, and exact resource ceilings from the design.
- [ ] Run `pytest tests/test_k3s_manifests.py -q` and verify RED.
- [ ] Write the manifests and Kustomize resource list without any Secret payload.
- [ ] Run the manifest test and `kubectl apply --dry-run=client -k deploy/k3s` when kubectl is installed.

### Task 5: Windows/WSL2 operator documentation

**Files:**
- Create: `docs/DEPLOIEMENT_K3S_WINDOWS.md`
- Modify: `.env.example`

**Interfaces:**
- Documents: WSL2 resource limits, K3s installation, image build/import, out-of-band Secret creation, Windows bridge address, apply/rollback, and health verification.

- [ ] Add safe example variable names and placeholders, never local values.
- [ ] Document a `.wslconfig` ceiling of 6 GB RAM, four processors, and 2 GB swap and explain how to tune it.
- [ ] Document that K3s does not support native Windows nodes and that MT5 stays on Windows.
- [ ] Verify all commands and run a secret-pattern scan over `deploy/k3s` and the new documentation.

### Task 6: Full verification, isolated commit, and service restart

**Files:**
- Review only the files listed in Tasks 1-5.

**Interfaces:**
- Produces: committed implementation and a verified service report.

- [ ] Run targeted tests, then `pytest -q` and Ruff over changed Python files.
- [ ] Run `git diff --check` and scan staged files for credential-like literals.
- [ ] Stage only this feature's files and run GitNexus `detect_changes(scope="staged")`.
- [ ] Commit with `feat: integrate DeepSeek and K3s deployment`.
- [ ] Restart only the authorized V14 services, verify exactly one instance per role, and confirm MT5 remains DEMO.
- [ ] Report service health, API/SSE checks, DeepSeek configuration presence without calling or printing the key, cache instrumentation readiness, resource limits, test results, and any deployment limitation.

