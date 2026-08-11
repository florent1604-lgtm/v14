"""Boucle de gestion des positions — appelle `manage_once` à intervalle régulier.

Séparée du gestionnaire lui-même : `manage_once` est un passage pur et testable,
cette boucle n'est que la cadence. Mélanger les deux, c'est ce qui rend la
version de V12 intestable sans terminal ouvert.

TROIS PROPRIÉTÉS
----------------
* **Ne meurt jamais sur une erreur.** Un passage qui échoue est journalisé, la
  boucle continue. Une position ouverte sans gestionnaire vivant, c'est un stop
  qui ne bougera plus.
* **Le mur est vérifié à chaque passage**, pas au démarrage : le terminal peut
  changer de compte pendant que la boucle tourne.
* **Arrêt propre** via un `threading.Event`, pour ne pas laisser un thread
  orphelin tenir le verrou MT5.

Usage :

    from titanium.execution.manage_loop import ManageLoop
    boucle = ManageLoop(); boucle.start()
    ...
    boucle.stop()

Ou en ligne de commande, pour observer :

    .venv\\Scripts\\python.exe -m titanium.execution.manage_loop
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from titanium.execution.mt5_executor import ExecutionPolicy
from titanium.execution.position_manager import ManageParams, manage_once

#: Intervalle par défaut. 15 s : assez fin pour un trailing utile, assez large
#: pour ne pas monopoliser le verrou MT5 (V12 tourne à la même cadence).
DEFAULT_INTERVAL = 15.0

#: Délai avant le premier passage, le temps que le terminal se stabilise.
STARTUP_DELAY = 5.0


@dataclass
class ManageLoop:
    """Boucle périodique de gestion. Démarre désarmée si la politique l'est."""
    interval: float = DEFAULT_INTERVAL
    policy: ExecutionPolicy | None = None
    params: ManageParams | None = None
    state_path: Path | None = None
    startup_delay: float = STARTUP_DELAY

    #: Dernier rapport, pour le dashboard.
    last: dict = field(default_factory=dict)
    #: Compteurs cumulés depuis le démarrage.
    stats: dict = field(default_factory=lambda: {"passages": 0, "deplacements": 0,
                                                 "erreurs": 0})

    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.policy is None:
            self.policy = ExecutionPolicy.from_config()
        if self.params is None:
            self.params = ManageParams.from_config()
        if self.state_path is None:
            from tradingagents.default_config import DEFAULT_CONFIG
            self.state_path = Path(DEFAULT_CONFIG["results_dir"]).parent / "positions.json"

    # ── un passage ──────────────────────────────────────────────────────────

    def tick(self) -> dict:
        """Un passage de gestion. Ne lève jamais."""
        try:
            import MetaTrader5 as mt5  # noqa: N813

            from titanium.data.mt5_vendor import mt5_lock, mt5_session

            with mt5_lock:
                with mt5_session():
                    rapport = manage_once(mt5, policy=self.policy, params=self.params,
                                          state_path=self.state_path,
                                          manage_stops=bool(self.policy.enabled))
        except Exception as exc:  # noqa: BLE001 — la boucle survit à tout
            rapport = {"managed": 0, "moved": 0,
                       "reason": f"PASSAGE_ERREUR: {type(exc).__name__}: {exc}",
                       "details": []}

        rapport["at"] = datetime.now(timezone.utc).isoformat()
        self.last = rapport
        self.stats["passages"] += 1
        self.stats["deplacements"] += int(rapport.get("moved", 0))
        if rapport.get("reason", "").startswith(("PASSAGE_ERREUR", "POSITIONS_INDISPONIBLES")):
            self.stats["erreurs"] += 1
        return rapport

    # ── cycle de vie ────────────────────────────────────────────────────────

    def run(self) -> None:
        """Boucle bloquante. `stop()` la fait sortir proprement."""
        if self._stop.wait(self.startup_delay):
            return
        while not self._stop.is_set():
            self.tick()
            self._stop.wait(self.interval)

    def start(self) -> "ManageLoop":
        """Démarre la boucle dans un thread démon."""
        if self._thread and self._thread.is_alive():
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, name="titanium-manage",
                                        daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 5.0) -> None:
        """Arrête la boucle et attend la fin du thread."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())


def main() -> None:
    """Lance la boucle au premier plan, avec un compte rendu par passage."""
    boucle = ManageLoop()
    p = boucle.policy
    print(f"Boucle de gestion — intervalle {boucle.interval:.0f} s")
    print(f"  armement : {'ARMÉ' if p.enabled else 'désarmé'}"
          f" · login attendu : {p.expected_demo_login}")
    print(f"  seuils   : breakeven +{boucle.params.breakeven_r} R · "
          f"trailing dès +{boucle.params.trail_start_r} R "
          f"(distance {boucle.params.trail_dist_r} R)")
    print(f"  état     : {boucle.state_path}")
    if not p.enabled:
        print("\n  ⚠️  Exécution désarmée : la boucle tournera mais ne modifiera aucun stop.")
    print("\nCtrl+C pour arrêter.\n")
    try:
        boucle.run()
    except KeyboardInterrupt:
        boucle.stop()
        print(f"\nArrêt. {boucle.stats['passages']} passage(s), "
              f"{boucle.stats['deplacements']} stop(s) déplacé(s), "
              f"{boucle.stats['erreurs']} erreur(s).")


if __name__ == "__main__":
    main()
