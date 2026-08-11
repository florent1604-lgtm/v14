"""Validation walk-forward glissante des résultats de backtest V14.

La stratégie reste fixe. Les fenêtres IS/OOS servent à détecter une
dégradation temporelle, pas à sélectionner automatiquement des paramètres.
Toutes les métriques utilisent ``Trade.pnl_r``, déjà net des coûts simulés.
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from titanium.backtest import Resultat, Trade


def _metriques(trades: list[Trade]) -> dict:
    pnls = [float(t.pnl_r) for t in trades]
    n = len(pnls)
    if not pnls:
        return {
            "n": 0,
            "expectancy_r": 0.0,
            "profit_factor": None,
            "win_rate": 0.0,
            "sharpe_per_trade": None,
            "mean_cost_r": 0.0,
        }

    moyenne = statistics.mean(pnls)
    ecart = statistics.stdev(pnls) if n > 1 else 0.0
    gains = sum(x for x in pnls if x > 0)
    pertes = -sum(x for x in pnls if x < 0)
    couts = [float(t.cost_r) for t in trades]
    return {
        "n": n,
        "expectancy_r": round(moyenne, 6),
        "profit_factor": round(gains / pertes, 6) if pertes else None,
        "win_rate": round(sum(x > 0 for x in pnls) / n, 6),
        "sharpe_per_trade": round(moyenne / ecart, 6) if ecart > 0 else None,
        "mean_cost_r": round(statistics.mean(couts), 6) if couts else 0.0,
    }


def analyser_resultat(
    resultat: Resultat,
    *,
    is_trades: int = 60,
    oos_trades: int = 20,
    step: int | None = None,
) -> dict:
    """Construit des fenêtres IS/OOS glissantes pour un actif.

    Les tailles sont exprimées en trades plutôt qu'en barres afin que chaque
    fenêtre contienne une quantité comparable d'information statistique.
    """
    if is_trades < 2 or oos_trades < 2:
        raise ValueError("is_trades et oos_trades doivent etre >= 2")
    pas = oos_trades if step is None else int(step)
    if pas < 1:
        raise ValueError("step doit etre >= 1")

    trades = sorted(resultat.trades, key=lambda t: t.bar_entree)
    requis = is_trades + oos_trades
    fenetres = []
    for numero, debut in enumerate(range(0, len(trades) - requis + 1, pas), 1):
        is_lot = trades[debut:debut + is_trades]
        oos_lot = trades[debut + is_trades:debut + requis]
        met_is = _metriques(is_lot)
        met_oos = _metriques(oos_lot)
        sharpe_oos = met_oos["sharpe_per_trade"]
        alerte = "OOS_SHARPE_NEGATIF" if sharpe_oos is not None and sharpe_oos < 0 else None
        fenetres.append({
            "window": numero,
            "is_from": is_lot[0].bar_entree,
            "is_to": is_lot[-1].bar_entree,
            "oos_from": oos_lot[0].bar_entree,
            "oos_to": oos_lot[-1].bar_entree,
            "is": met_is,
            "oos": met_oos,
            "alert": alerte,
        })

    negatifs = sum(w["alert"] == "OOS_SHARPE_NEGATIF" for w in fenetres)
    return {
        "schema_version": 1,
        "symbol": resultat.symbol,
        "status": "OK" if fenetres else "INSUFFICIENT_TRADES",
        "required_trades": requis,
        "available_trades": len(trades),
        "parameters": {
            "is_trades": is_trades,
            "oos_trades": oos_trades,
            "step": pas,
        },
        "windows": fenetres,
        "summary": {
            "windows": len(fenetres),
            "negative_oos_windows": negatifs,
            "latest_oos_sharpe": (
                fenetres[-1]["oos"]["sharpe_per_trade"] if fenetres else None
            ),
            "alert": "OOS_SHARPE_NEGATIF" if negatifs else None,
        },
    }


def ecrire_rapport(rapport: dict, dossier: str | Path) -> Path:
    """Écrit atomiquement le rapport JSON d'un actif."""
    racine = Path(dossier)
    racine.mkdir(parents=True, exist_ok=True)
    symbole = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(rapport.get("symbol") or "inconnu"))
    cible = racine / f"{symbole}.json"
    temporaire = cible.with_suffix(cible.suffix + ".tmp")
    temporaire.write_text(
        json.dumps(rapport, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporaire.replace(cible)
    return cible
