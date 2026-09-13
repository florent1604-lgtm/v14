# DeepSeek V4 Flash and K3s Design

## Goal

Run Titanium V14's HTTP dashboard, SSE stream, and DeepSeek cognitive worker in a
resource-bounded K3s cluster hosted by WSL2, while keeping the MetaTrader 5 terminal
and guarded DEMO execution on native Windows.

## Platform boundary

K3s does not natively support Windows nodes and the MetaTrader5 Python package is
Windows-only. The cluster therefore never imports or launches MT5. A small K3s adapter
connects to an authenticated bridge bound to the Windows host. The bridge accepts only
sealed V14 DEMO requests and preserves the existing account wall, RiskGate, sizing,
idempotence, position protection, and execution ledger.

The public-facing FastAPI process remains read-only for broker state. Its SSE endpoint
emits cache-health notifications and cannot arm execution or call MT5. Manual intentions
continue through the existing append-only intent queue.

## DeepSeek client

The client uses the OpenAI-compatible API at `https://api.deepseek.com` with model
`deepseek-v4-flash`. It reads `DEEPSEEK_API_KEY` from the process environment and fails
closed when the variable is absent. No key value is read from, copied out of, logged
from, or committed from the local `.env` file.

DeepSeek context caching is automatic. V14 maximizes cache reuse by keeping stable
instructions, schemas, and playbooks at the start of each prompt and appending volatile
market observations afterward. It records only safe usage counters: input/output tokens,
cached input tokens or prompt cache hit/miss tokens, request duration, and outcome.

Calls use explicit connection and total timeouts, bounded retries for transient errors,
and a circuit breaker. An unavailable provider yields `WAIT` or `UNKNOWN`; it never
authorizes an order.

## Kubernetes workloads

The `titanium-api` Deployment serves FastAPI and SSE through a ClusterIP Service. It
requests 250 millicores and 512 MiB, with hard limits of 500 millicores and 1 GiB.

The `titanium-deepseek-worker` Deployment consumes sealed cognitive requests. It
requests 250 millicores and 512 MiB, with hard limits of 1 CPU and 1536 MiB. The API key
comes from a referenced Secret named `titanium-deepseek`; the repository contains only
a non-secret example command and never a Secret value.

The `titanium-mt5-demo-adapter` Deployment forwards guarded requests to the native
Windows bridge. It requests 100 millicores and 128 MiB, with hard limits of 250
millicores and 256 MiB. The adapter is internal-only and has no broker credentials. A
ClusterIP Service exposes it only inside the namespace.

All Deployments use one replica, `RollingUpdate` with zero surge for singleton workers,
read-only root filesystems where supported, dropped Linux capabilities, non-root users,
health probes, and bounded ephemeral storage. A namespace ResourceQuota and LimitRange
prevent aggregate resource drift.

## Windows and WSL2 resources

Pod limits constrain workloads inside K3s. A documented `.wslconfig` example additionally
bounds the WSL2 virtual machine so Kubernetes cannot consume all Windows memory or CPU.
The recommended initial ceiling is 6 GiB RAM, four processors, and 2 GiB swap, subject
to the target machine's installed capacity.

## Data flow

1. V14 creates a sealed candidate with deterministic evidence.
2. The DeepSeek worker evaluates only that candidate and publishes ALLOW/WAIT/BLOCK.
3. The deterministic V14 gates validate freshness, identity, risk, cost, exposure, and
   DEMO account state.
4. The K3s adapter forwards an accepted request to the Windows bridge.
5. The Windows process revalidates the DEMO wall immediately before calling MT5.
6. Execution results and heartbeats return as bounded, non-secret events.
7. FastAPI reads cached state and the SSE endpoint tells clients when to refresh it.

## Failure behavior

Missing secrets, stale policies, bridge authentication errors, timeouts, schema errors,
non-DEMO accounts, missing SL/TP, and ledger inconsistencies all fail closed. The API and
worker can restart without granting execution authority. Existing broker-side SL/TP
remain effective if K3s or WSL2 stops.

## Verification

Unit tests cover endpoint/model configuration, secret absence, cache metrics, redaction,
retry classification, and fail-closed behavior. Manifest tests parse every YAML document
and assert resource limits, probes, security contexts, non-secret configuration, DEMO
mode, and internal Services. FastAPI tests verify health and SSE behavior without MT5.

A deployment smoke test uses `kubectl apply --dry-run=client`, followed by local tests and
Ruff. No live provider request or MT5 order is required to validate the implementation.

