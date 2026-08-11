"""Tableau de bord de Titanium V14 — http://localhost:8095

Serveur stdlib (`http.server`), zéro dépendance web, écoute sur 127.0.0.1
uniquement. Ports déjà pris sur cette machine : 8090 (Titanium V12), 8080 et
8765 (JARVIS), 3000 (Open WebUI) — d'où 8095.

    python tools/dashboard.py            # sert l'interface
    python tools/dashboard.py --tests    # relève la suite et alimente la page

ROUTES
------
``GET  /``              l'interface
``GET  /api/state``     état complet (mur, compte, vendeurs, briques, edge, …)
``GET  /api/command-center`` gouvernance, tâches et preuves locales
``GET  /api/tasks``     projection du journal append-only commun
``GET  /api/scan``      balayage déterministe (paramètres ``symbols``, ``prod``)
``GET  /api/run/<i>``   détail d'une analyse multi-agents
``POST /api/tasks``     crée/transite une tâche locale, sans lancer d'agent

**Lecture seule.** Aucune route ne passe d'ordre, ne déplace un stop ni
n'appelle un LLM. Le balayage lit MT5 et calcule des features — c'est le seul
appel coûteux, et il n'est jamais automatique.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.console_output import configure_console_output  # noqa: E402

PORT = 8095
UI = Path(__file__).resolve().parent / "ui"

#: Briques Titanium suivies : (libellé, module, fichier de tests).
#: Ajouter une brique ici la fait apparaître dans l'interface ET dans le relevé
#: de tests — les deux lisaient auparavant deux listes distinctes, et le
#: compteur oubliait silencieusement les nouveaux fichiers.
BRIQUES = [
    ("Portes ET de confluence", "titanium/gates/confluence_gate.py", "test_confluence_gate.py"),
    ("RiskGate unifié", "titanium/risk/riskgate.py", "test_riskgate.py"),
    ("Détecteurs + features", "titanium/features/builder.py", "test_features.py"),
    ("Moteur de chandeliers", "titanium/features/candlesticks.py", "test_candlesticks.py"),
    ("Délibérateur", "titanium/deliberation.py", "test_deliberation.py"),
    ("Vendeur MT5 (lecture)", "titanium/data/mt5_vendor.py", "test_mt5_vendor.py"),
    ("Vendeur MT5 (agents)", "titanium/data/mt5_dataflows.py", "test_mt5_dataflows.py"),
    ("Exécution + mur", "titanium/execution/mt5_executor.py", "test_mt5_executor.py"),
    ("Gestion de position", "titanium/execution/position_manager.py", "test_position_manager.py"),
    ("Dimensionnement par actif", "titanium/sizing.py", "test_sizing.py"),
    ("Edge + boucle de gestion", "titanium/edge.py", "test_edge_and_loop.py"),
    ("Orchestrateur", "titanium/orchestrator.py", "test_orchestrator.py"),
    ("Panel + discriminants", "titanium/analysis/discriminants.py", "test_indicators_discriminants.py"),
    ("Pont MQL5 · zones", "titanium/bridge/mt5_zones.py", "test_bridge_mt5_zones.py"),
    ("Idempotence par barre", "tools/live_demo.py", "test_idempotence_barre.py"),
    ("Command Center + tâches", "titanium/web/command_center.py", "test_command_center.py"),
]

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
         ".ico": "image/x-icon"}


class Handler(BaseHTTPRequestHandler):
    server_version = "TitaniumV14"

    # ── envoi ───────────────────────────────────────────────────────────────

    def _send(self, corps: bytes, type_mime: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", type_mime)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, donnees, code: int = 200) -> None:
        self._send(json.dumps(donnees, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def _fichier(self, chemin: Path) -> None:
        if not chemin.is_file():
            self._send(b"introuvable", "text/plain; charset=utf-8", 404)
            return
        self._send(chemin.read_bytes(),
                   TYPES.get(chemin.suffix, "application/octet-stream"))

    # ── routage ─────────────────────────────────────────────────────────────

    def do_GET(self):  # noqa: N802
        route = urlparse(self.path)
        chemin = route.path
        params = parse_qs(route.query)

        try:
            if chemin in ("/", "/index.html", "/poste"):
                # Le poste de contrôle est la page par défaut ; l'ancienne
                # reste servie sous /classique pour comparaison.
                return self._fichier(UI / "poste.html")

            if chemin == "/command":
                return self._fichier(UI / "command.html")

            if chemin == "/classique":
                return self._fichier(UI / "index.html")

            if chemin.startswith("/ui/"):
                # Empêche toute remontée hors du dossier d'interface.
                cible = (UI / chemin[4:]).resolve()
                if UI.resolve() not in cible.parents and cible != UI.resolve():
                    return self._send(b"interdit", "text/plain", 403)
                return self._fichier(cible)

            if chemin == "/api/state":
                from titanium.web import state
                return self._json(state.state())

            if chemin == "/api/command-center":
                from titanium.web import command_center, state
                return self._json(command_center.snapshot(state.state()))

            if chemin == "/api/tasks":
                from titanium.collab_tasks import snapshot
                return self._json(snapshot())

            if chemin == "/api/scan":
                from titanium.web import state
                bruts = params.get("symbols", [""])[0].strip()
                symboles = [s.strip().upper() for s in bruts.split(",") if s.strip()]
                prod = params.get("prod", ["0"])[0] in ("1", "true", "on")
                return self._json(state.scan(symboles or None, prod=prod))

            if chemin == "/api/chart":
                from titanium.web import state
                sym = params.get("symbol", [""])[0].strip().upper()
                tf = params.get("tf", ["M15"])[0].strip().upper()
                n = params.get("bars", ["180"])[0]
                try:
                    n = int(n)
                except ValueError:
                    n = 180
                return self._json(state.chart(sym, timeframe=tf, barres=n))

            if chemin == "/api/univers":
                from titanium.web import state
                return self._json({"symbols": state.univers_disponible()})

            if chemin == "/api/carte":
                from titanium.web import carte
                return self._json(carte.carte())

            if chemin == "/api/faiblesses":
                from titanium.web import carte
                return self._json({"faiblesses": carte.faiblesses()})

            if chemin == "/api/medecin":
                from titanium.web import medecin
                return self._json(medecin.bilan())

            if chemin == "/api/promotion":
                from titanium.web import state
                return self._json(state.promotion())

            if chemin.startswith("/api/run/"):
                from titanium.web import state
                try:
                    i = int(chemin.rsplit("/", 1)[-1])
                except ValueError:
                    return self._json({"error": "index invalide"}, 400)
                tous = state.runs(limit=50)
                if not 0 <= i < len(tous):
                    return self._json({"error": "run inconnu"}, 404)
                return self._json(tous[i])

            return self._send(b"introuvable", "text/plain; charset=utf-8", 404)

        except Exception as exc:  # noqa: BLE001 — une sonde ne tue pas le serveur
            # ⚠️ La trace complète part au JOURNAL, jamais à la page. Afficher
            # un traceback Python dans une interface de supervision expose des
            # chemins de fichiers et se lit comme une panne totale alors qu'il
            # s'agit souvent d'un symbole indisponible une seconde.
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            self._json({"error": f"{type(exc).__name__}: {exc}"[:200]}, 500)

    def do_POST(self):  # noqa: N802
        route = urlparse(self.path)
        if route.path != "/api/tasks":
            return self._send(b"introuvable", "text/plain; charset=utf-8", 404)

        origin = self.headers.get("Origin", "")
        if origin:
            parsed = urlparse(origin)
            if parsed.scheme not in ("http", "https") or parsed.hostname not in (
                "127.0.0.1", "localhost",
            ):
                return self._json({"error": "origine interdite"}, 403)
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            return self._json({"error": "Content-Type application/json requis"}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            size = 0
        if not 1 <= size <= 16_384:
            return self._json({"error": "corps JSON absent ou trop grand"}, 413)

        try:
            data = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("objet JSON attendu")
            from titanium.collab_tasks import TaskError, create_task, snapshot, update_task
            action = str(data.pop("action", "create")).strip().lower()
            if action == "create":
                task = create_task(data)
            elif action == "update":
                task = update_task(str(data.pop("id", "")), data)
            else:
                raise TaskError("action invalide")
            return self._json({"task": task, "snapshot": snapshot()}, 201)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            return self._json({"error": str(exc)[:200]}, 400)

    def log_message(self, *a):
        pass  # la page interroge souvent : pas de bruit console


# ═════════════════════════════ relevé de tests ═══════════════════════════════

def relever_tests() -> None:
    """Lance la suite et enregistre le résultat pour l'interface."""
    print("Suite de tests en cours…")
    py = RACINE / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)

    r = subprocess.run([str(py), "-m", "pytest", "tests/", "-q", "--no-header"],
                       cwd=RACINE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    sortie = r.stdout or ""
    passes = int(m.group(1)) if (m := re.search(r"(\d+) passed", sortie)) else 0
    echecs = int(m.group(1)) if (m := re.search(r"(\d+) failed", sortie)) else 0

    par_fichier = {}
    for _, _, nom in BRIQUES:
        if not (RACINE / "tests" / nom).exists():
            continue
        rf = subprocess.run([str(py), "-m", "pytest", f"tests/{nom}", "-q",
                             "--no-header", "--collect-only"], cwd=RACINE,
                            capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
        if m := re.search(r"(\d+) tests? collected", rf.stdout or ""):
            par_fichier[nom] = int(m.group(1))

    titanium = sum(par_fichier.values())
    donnees = {"total": passes + echecs, "echecs": echecs, "titanium": titanium,
               "socle": passes + echecs - titanium, "par_fichier": par_fichier,
               "quand": datetime.now().strftime("%d/%m/%Y %H:%M")}
    cible = RACINE / "results" / "tests.json"
    cible.parent.mkdir(parents=True, exist_ok=True)
    cible.write_text(json.dumps(donnees, indent=2), encoding="utf-8")
    print(f"  {passes} passés, {echecs} échec(s) — dont {titanium} titanium")


if __name__ == "__main__":
    configure_console_output()
    if "--tests" in sys.argv:
        relever_tests()
        sys.exit(0)
    # ── Préchargement des modules lourds, EN MONOTHREAD.
    #
    #    ⚠️ Le serveur est multithread et les gestionnaires importent en
    #    différé. Deux requêtes simultanées peuvent alors importer la même
    #    chaîne de modules en même temps et l'une la voit à moitié
    #    initialisée — « partially initialized module 'typing' has no
    #    attribute 'ClassVar' ». Le module est sain ; c'est la concurrence
    #    d'import qui casse. Les charger ici, avant le premier client,
    #    remplit `sys.modules` et rend les imports différés instantanés.
    for _mod in ("titanium.web.state", "titanium.web.medecin",
                 "titanium.web.carte", "titanium.analysis.promotion",
                 "titanium.analysis.promotion_registry", "titanium.edge",
                 "titanium.correlation", "titanium.gates.confluence_gate",
                 "titanium.features.builder"):
        try:
            __import__(_mod)
        except Exception as _e:  # noqa: BLE001
            print(f"  préchargement {_mod} : {type(_e).__name__}")

    # ── Indexation du code au démarrage.
    #    Faite ici et pas à la première requête : la carte sert aussi aux
    #    analystes, qui peuvent la demander avant qu'un humain ait ouvert la
    #    page. Elle coûte moins d'une seconde et n'est refaite que si le
    #    dépôt a changé — l'empreinte des dates de modification en décide.
    try:
        from titanium.web import carte as _carte

        _c = _carte.carte()
        _r = _c.get("resume", {})
        print(f"Carte du code : {_r.get('modules', 0)} modules · "
              f"{_r.get('lignes', 0):,} lignes · "
              f"{_r.get('faiblesses', 0)} faiblesse(s) relevée(s)")
    except Exception as _exc:  # noqa: BLE001 — l'index n'est pas vital
        print(f"Carte du code indisponible : {type(_exc).__name__}")

    print(f"Tableau de bord V14 — http://localhost:{PORT}   (Ctrl+C pour arrêter)")
    # ThreadingHTTPServer et non HTTPServer : un balayage prend plusieurs
    # secondes (lecture MT5 + calcul des features). En mono-thread il gèle
    # TOUTE l'interface — y compris le rafraîchissement d'état — pendant ce
    # temps. Constaté au navigateur, pas supposé.
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
