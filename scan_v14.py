"""Balayage V14 — la chaîne complète sur données MT5 réelles.

    .venv\\Scripts\\python.exe scan_v14.py EURUSD XAUUSD US500 GER40
    .venv\\Scripts\\python.exe scan_v14.py --deliberer EURUSD

    MT5 → features → portes ET → [délibération] → RiskGate → décision

L'exécution n'est JAMAIS déclenchée ici : ce script décide, il n'agit pas.
Passer à l'acte demande `TITANIUM_EXEC_ENABLED=1` et un appel explicite à
l'orchestrateur avec `execute=True`.

Sans `--deliberer`, aucun appel LLM n'est fait : la chaîne tourne en
déterministe pur, à conviction neutre. C'est le mode à privilégier pour un
balayage large — la délibération ne se justifie que sur les setups déjà validés.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from titanium.data.mt5_vendor import get_rates, shutdown
from titanium.deliberation import GraphDeliberator
from titanium.features.builder import build_feats, risk_context_from
from titanium.orchestrator import OrchestratorConfig, run_once

DEFAUT = ["EURUSD", "XAUUSD", "US500", "GER40", "UK100", "USTECH"]
NOMS_PILIERS = ["data", "G1 sr", "G2 fv", "G3 liq", "G4 ote", "G5 bougie"]


def _configure_console_output() -> None:
    """Evite qu'une console Windows non UTF-8 interrompe le balayage.

    Les symboles d'etat restent lisibles en UTF-8. Sur une console CP1252,
    Python les remplace proprement au lieu de lever ``UnicodeEncodeError``.
    """
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        # Certains flux captures ou deja fermes ne sont pas reconfigurables.
        pass


def _resoudre(symbole: str) -> str:
    """Nom courtier pour un nom logique ; le nom d'origine si ça échoue.

    Le repli est volontaire : terminal fermé ou catalogue illisible, le scan
    doit rester exactement aussi capable qu'avant ce correctif, jamais moins.
    Il tente alors le nom tel qu'il a été tapé.
    """
    try:
        from titanium.data.mt5_dataflows import resolve_mt5_symbol
        return resolve_mt5_symbol(symbole)
    except Exception:  # noqa: BLE001 — un résolveur muet ne doit rien casser
        return symbole


def scanner(symboles: list[str], *, deliberer: bool, prod: bool,
            equity: float, risk_pct: float) -> None:
    delib = None
    if deliberer:
        delib = GraphDeliberator(trade_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    cfg = OrchestratorConfig(require_edge=prod, deliberate=deliberer, execute=False)

    print(f"\n{'=' * 78}")
    print(f"  BALAYAGE V14 — mode {'PROD (quorum 3/4, edge exigé)' if prod else 'EXPLORE (quorum 2/4)'}"
          f"{' + délibération LLM' if deliberer else ' — déterministe pur'}")
    print(f"  capital {equity:.2f} · risque {risk_pct:.2f} % par trade")
    print(f"{'=' * 78}\n")

    entrees = []
    for sym in symboles:
        # ── Le nom logique doit être résolu vers le nom du courtier AVANT
        #    d'interroger le vendeur. `mt5_vendor.get_rates` ne résout rien :
        #    la table des suffixes vit dans `mt5_dataflows`, et la boucle live
        #    n'en a jamais eu besoin puisqu'elle lit le catalogue MT5 déjà
        #    suffixé. Le scan, lui, reçoit des noms tapés à la main.
        #
        #    Sans cette résolution, `scan_v14.py NAS100` répondait « données
        #    indisponibles » pendant que la boucle tradait NAS100.fs — le même
        #    instrument. Un outil de diagnostic qui déclare absent ce que le
        #    moteur exploite ne se contente pas d'être inutile : il fait
        #    conclure faux. Constaté le 09/08/2026, mesuré le 10/08.
        courtier = _resoudre(sym)
        try:
            htf = get_rates(courtier, "H4", 400)
            ltf = get_rates(courtier, "M15", 400)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym:10} données indisponibles ({type(exc).__name__})")
            continue

        feats = build_feats(ltf, htf)
        ctx = risk_context_from(feats, equity=equity, risk_pct=risk_pct)

        out = run_once(sym, feats, ctx, config=cfg, deliberate_fn=delib)

        # Une case par pilier : lecture immédiate de ce qui manque.
        from titanium.gates import confluence_gate
        d = confluence_gate.evaluate(feats, require_edge=prod)
        piliers = " ".join(f"{'✓' if g.passed else '·'}" for g in d.gates) or "—"

        marque = "▶" if out.gate_verdict == "ENTER" else " "
        print(f"{marque} {sym:10} [{piliers}]  {out.gate_verdict:6} {out.reason:24}", end="")
        if out.gate_verdict == "ENTER":
            print(f" conv={out.conviction:.2f} risque={out.risk_money:.2f}")
            entrees.append(out)
        else:
            print()

    print(f"\n  piliers : {' | '.join(NOMS_PILIERS)}")
    print(f"  {len(entrees)} setup(s) retenu(s) sur {len(symboles)} balayé(s)\n")

    for out in entrees:
        from titanium.orchestrator import explain
        print(explain(out), "\n")


def main() -> None:
    _configure_console_output()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    options = {a for a in sys.argv[1:] if a.startswith("--")}

    equity, risk_pct = 108.37, 1.0
    try:
        from titanium.data.mt5_vendor import account_snapshot
        equity = account_snapshot().equity
    except Exception:  # noqa: BLE001 — valeur de repli, le balayage reste utile
        pass

    try:
        scanner(args or DEFAUT, deliberer="--deliberer" in options,
                prod="--prod" in options, equity=equity, risk_pct=risk_pct)
    finally:
        shutdown()


if __name__ == "__main__":
    main()
