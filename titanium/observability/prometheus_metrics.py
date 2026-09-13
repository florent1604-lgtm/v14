"""Export Prometheus de V14.

Le serveur vit dans le processus dashboard, mais les métriques système ciblent
le groupe de processus ``tools/live_demo.py``. Les compteurs métier sont
alimentés à partir du battement et de la mémoire centrale : aucune lecture MT5,
aucun ordre et aucun changement de décision dans ce module.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

import psutil
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

RACINE = Path(__file__).resolve().parent.parent.parent
RESULTATS = RACINE / "results"
METRICS_PORT = 9108
BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

REGISTRY = CollectorRegistry(auto_describe=True)
REQUESTS = Counter(
    "bot_requests_total", "Requêtes HTTP reçues par V14", ["status"], registry=REGISTRY
)
REQUEST_DURATION = Histogram(
    "bot_request_duration_seconds",
    "Latence des requêtes HTTP V14",
    buckets=BUCKETS,
    registry=REGISTRY,
)
MESSAGES = Counter(
    "bot_messages_processed_total", "Candidats de marché évalués par le moteur", registry=REGISTRY
)
QUEUE = Gauge(
    "bot_queue_size", "Décisions en attente d'une proposition exacte du cerveau", registry=REGISTRY
)
ERRORS = Counter(
    "bot_errors_total", "Erreurs et alertes internes V14", ["type"], registry=REGISTRY
)
UPTIME = Gauge(
    "bot_uptime_seconds", "Durée de vie du processus moteur actif", registry=REGISTRY
)

_LOCK = threading.Lock()
_STARTED = False
_LAST_MESSAGES = 0
_LAST_ALERTS = 0


def observe_request(status: int, duration_seconds: float) -> None:
    """Enregistre une requête terminée ; ne peut pas casser sa réponse."""
    try:
        REQUESTS.labels(status=str(int(status))).inc()
        REQUEST_DURATION.observe(max(0.0, float(duration_seconds)))
    except (TypeError, ValueError):
        return


def record_error(error_type: str) -> None:
    """Incrémente une famille d'erreur à cardinalité bornée."""
    propre = "".join(c for c in str(error_type) if c.isalnum() or c in "_-")[:64]
    ERRORS.labels(type=propre or "unknown").inc()


def _heartbeat() -> dict:
    try:
        return json.loads((RESULTATS / "loop_heartbeat.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _central_counts() -> tuple[int, int]:
    """Rend ``(backlog, alertes)`` sans créer ni verrouiller la base."""
    path = RESULTATS / "organism_memory.sqlite3"
    if not path.exists():
        return 0, 0
    try:
        uri = f"file:{path.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.05) as db:
            backlog = int(db.execute(
                """SELECT COUNT(*) FROM (
                       SELECT decision_ref FROM events GROUP BY decision_ref
                       HAVING SUM(kind='engine.request') > 0
                          AND SUM(kind='brain.proposal') = 0
                   )"""
            ).fetchone()[0])
            alerts = int(db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='system.alert'"
            ).fetchone()[0])
        return backlog, alerts
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 0, 0


def _engine_processes() -> list[psutil.Process]:
    trouves = []
    for process in psutil.process_iter(["cmdline"]):
        try:
            commande = " ".join(process.info.get("cmdline") or []).replace("\\", "/")
            if "tools/live_demo.py" in commande:
                trouves.append(process)
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            continue
    return trouves


def _refresh_runtime() -> None:
    global _LAST_ALERTS, _LAST_MESSAGES

    heartbeat = _heartbeat()
    courant = int((heartbeat.get("stats") or {}).get("evalues") or 0)
    backlog, alerts = _central_counts()
    processus = _engine_processes()

    with _LOCK:
        delta = courant - _LAST_MESSAGES if courant >= _LAST_MESSAGES else courant
        if delta > 0:
            MESSAGES.inc(delta)
        _LAST_MESSAGES = courant

        delta_alertes = alerts - _LAST_ALERTS if alerts >= _LAST_ALERTS else alerts
        if delta_alertes > 0:
            ERRORS.labels(type="organism_alert").inc(delta_alertes)
        _LAST_ALERTS = alerts
        QUEUE.set(backlog)

        if processus:
            naissance = min(p.create_time() for p in processus)
            UPTIME.set(max(0.0, time.time() - naissance))
        else:
            UPTIME.set(0.0)


class EngineProcessCollector:
    """Expose CPU/RAM du groupe moteur sous les noms Prometheus standards."""

    def collect(self):
        _refresh_runtime()
        rss = 0.0
        cpu = 0.0
        naissance = 0.0
        for process in _engine_processes():
            try:
                rss += float(process.memory_info().rss)
                temps = process.cpu_times()
                cpu += float(temps.user + temps.system)
                cree = float(process.create_time())
                naissance = cree if not naissance else min(naissance, cree)
            except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                continue

        memoire = GaugeMetricFamily(
            "process_resident_memory_bytes", "Mémoire résidente du moteur V14"
        )
        memoire.add_metric([], rss)
        yield memoire

        processeur = CounterMetricFamily(
            "process_cpu_seconds", "Temps CPU cumulé du moteur V14"
        )
        processeur.add_metric([], cpu)
        yield processeur

        demarrage = GaugeMetricFamily(
            "process_start_time_seconds", "Époque de démarrage du moteur V14"
        )
        demarrage.add_metric([], naissance)
        yield demarrage


REGISTRY.register(EngineProcessCollector())


def _refresh_loop() -> None:
    while True:
        try:
            _refresh_runtime()
        except Exception:  # noqa: BLE001 — la supervision ne tue jamais le dashboard
            record_error("metrics_refresh")
        time.sleep(5)


def start_metrics_server(*, port: int = METRICS_PORT, addr: str = "0.0.0.0") -> bool:
    """Démarre une seule fois le serveur métriques détaché du serveur métier."""
    global _STARTED
    with _LOCK:
        if _STARTED:
            return False
        _STARTED = True
    try:
        _refresh_runtime()
        start_http_server(int(port), addr=addr, registry=REGISTRY)
        threading.Thread(
            target=_refresh_loop, name="v14-prometheus-refresh", daemon=True
        ).start()
        return True
    except Exception:
        with _LOCK:
            _STARTED = False
        raise
