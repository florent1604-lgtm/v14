"""Le compteur tourne-t-il vraiment ? Quatre vérifications, une commande.

    python tools/verifier_accumulation.py

À lancer après quelques heures de démo. Il répond à « dans combien de temps »
par un chiffre, pas par une impression — et il dit surtout si les trades
accumulés **comptent**, ce qui n'a rien d'évident : une strate mal renseignée
ou une classe vide produit des lignes qui ne compteront jamais.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.analysis.promotion import (  # noqa: E402
    MIN_TRADES_CELLULE, STRATE_MIN_SUPPORT, evaluate_cells,
)
from titanium.edge import TradeJournal  # noqa: E402

JOURNAL = RACINE / "results" / "trades.ndjson"


def _configure_console_output() -> None:
    """Degrade les glyphes proprement sur les consoles Windows CP1252."""
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(errors="replace")
    except (OSError, ValueError):
        pass


def main() -> int:
    _configure_console_output()
    journal = TradeJournal(JOURNAL)
    trades = journal.read_all()
    if journal.rejected_lines:
        print(f"⚠️  journal invalide : {journal.rejected_lines} ligne(s) "
              "rejetee(s) ; edge et promotion bloques.")
    if not trades:
        print("✗ journal vide — aucune clôture enregistrée.")
        print("  La boucle tourne-t-elle, et des positions se sont-elles")
        print("  fermées depuis son démarrage ?")
        return 1

    print(f"journal : {len(trades)} trades clos\n")
    verdicts = []

    # ── 1. La strate est-elle renseignée, et exacte ?
    strates = Counter(t.support_pillars for t in trades)
    sans_strate = strates.get(0, 0)
    print("1. STRATE (piliers alignés)")
    for s in sorted(strates):
        marque = "  ← compte pour PROD" if s >= STRATE_MIN_SUPPORT else ""
        print(f"   S={s} : {strates[s]:4}{marque}")
    ok1 = sans_strate < len(trades)
    if sans_strate:
        print(f"   ⚠️  {sans_strate} sans strate — lignes antérieures au"
              " câblage, elles ne compteront pas")
    verdicts.append(("strate renseignée", ok1))

    # ── 2. Le coût est-il MESURÉ, et non reconstruit ?
    exacts = sum(1 for t in trades if t.exact_cost)
    part = exacts / len(trades)
    print(f"\n2. COÛT EXACT : {exacts}/{len(trades)} ({part:.0%})")
    if part < 0.9:
        print("   ⚠️  sous 90 %, le critère C3 bloquera la promotion.")
        print("   Le PnL reste net comptable, mais la décomposition complète")
        print("   spread + commission + swap + fee n'est pas encore exacte.")
    verdicts.append(("coût mesuré ≥ 90 %", part >= 0.9))

    # ── 3. Le mode et le quorum sont-ils cohérents ?
    modes = Counter(t.mode for t in trades)
    quorums = Counter(t.quorum for t in trades)
    print(f"\n3. MODE : {dict(modes)} · QUORUM : {dict(quorums)}")
    ok3 = all(m in ("explore", "prod") and m for m in modes)
    verdicts.append(("mode renseigné", ok3))

    # ── 4. La classe d'actif est-elle résolue ?
    classes = Counter(t.asset_class or "(VIDE)" for t in trades)
    print(f"\n4. CLASSE : {dict(classes)}")
    vides = classes.get("(VIDE)", 0)
    if vides:
        print(f"   ⚠️  {vides} sans classe — **jamais promouvables**.")
    verdicts.append(("classe résolue", vides == 0))

    # ── Le compteur
    print("\n" + "─" * 62)
    cellules = evaluate_cells(trades, rejected_lines=journal.rejected_lines)
    if not cellules:
        eligibles = sum(1 for t in trades
                        if t.support_pillars >= STRATE_MIN_SUPPORT
                        and t.asset_class and t.source == "live")
        print(f"AUCUNE CELLULE — {eligibles} trade(s) en strate S≥3 classée.")
        print("Le compteur démarre au premier trade S≥3 sur un actif classé.")
    else:
        print(f"{'cellule':22} {'n':>5}/{MIN_TRADES_CELLULE:<4} "
              f"{'espérance':>10} {'PF':>6}  bloquants")
        for v in sorted(cellules, key=lambda x: -x.n):
            pf = "∞" if v.profit_factor == float("inf") else f"{v.profit_factor:.2f}"
            b = v.bloquants[0].split("(")[0] if v.bloquants else "ÉLIGIBLE"
            print(f"{v.cell:22} {v.n:>5}/{MIN_TRADES_CELLULE:<4} "
                  f"{v.expectancy_r:>+10.3f} {pf:>6}  {b}")

    print("─" * 62)
    for nom, ok in verdicts:
        print(f"  {'✓' if ok else '✗'} {nom}")
    tous = all(ok for _, ok in verdicts)
    print(f"\n{'Le compteur tourne.' if tous else 'Le compteur est bridé — voir les ⚠️ ci-dessus.'}")
    return 0 if tous else 1


if __name__ == "__main__":
    raise SystemExit(main())
