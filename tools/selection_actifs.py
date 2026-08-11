"""Établit la hiérarchie de balayage du catalogue.

    python tools/selection_actifs.py            # relève et classe
    python tools/selection_actifs.py --sans-tv  # sans avis TradingView

À lancer une fois par jour, hors du chemin critique. Le résultat est lu par
la boucle, qui règle sa cadence et sa profondeur d'historique dessus.

Aucun actif n'est écarté. Ce qui est décidé ici, c'est **à quelle fréquence
et avec quelle finesse** chacun est examiné — les plus liquides méritant le
plus d'attention parce qu'ils sont les plus mesurables, pas parce qu'ils
seraient meilleurs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.selection import (  # noqa: E402
    FORCES, RANG_A, RANG_B, RANG_C, Fiche, _cout_relatif, classer, ecrire,
)

SORTIE = RACINE / "results" / "selection_actifs.json"

#: Correspondance pour l'avis TradingView. Le screener attend un couple
#: (place, marché) que MT5 ne fournit pas : on le déduit de la classe.
TV_PLACE = {
    "crypto": ("BITSTAMP", "crypto"),
    "fx": ("FX_IDC", "forex"),
    "metaux": ("OANDA", "cfd"),
    "indices": ("OANDA", "cfd"),
    "energie": ("OANDA", "cfd"),
}


def avis_tradingview(symbole: str, classe: str) -> str:
    """Recommandation agrégée de TradingView. "" si indisponible.

    Volontairement tolérant : cet avis pondère faiblement le classement, et
    une place de cotation absente ne doit pas faire échouer le relevé.
    """
    place = TV_PLACE.get(classe)
    if not place:
        return ""
    try:
        from tradingview_ta import TA_Handler, Interval

        h = TA_Handler(symbol=symbole, exchange=place[0], screener=place[1],
                       interval=Interval.INTERVAL_1_HOUR)
        return str(h.get_analysis().summary.get("RECOMMENDATION", ""))
    except Exception:  # noqa: BLE001
        return ""


def relever(avec_tv: bool = True) -> list:
    """Mesure chaque actif du catalogue. Ne lève jamais sur un actif."""
    import MetaTrader5 as mt5  # noqa: N813

    from titanium.data.mt5_vendor import ensure_symbol, get_rates, mt5_session
    from titanium.edge import EdgeBook, TradeJournal, asset_class_of

    livre = EdgeBook(journal=TradeJournal(RACINE / "results" / "trades.ndjson"))
    par_contexte = {v.context: v for v in livre.summary()}

    fiches = []
    with mt5_session():
        catalogue = [s.name for s in (mt5.symbols_get() or []) if s.visible]

    print(f"catalogue : {len(catalogue)} actifs\n")
    for i, sym in enumerate(catalogue, 1):
        f = Fiche(symbol=sym, classe=asset_class_of(sym))
        try:
            spec = ensure_symbol(sym)
            df = get_rates(sym, "M15", 120)
            atr = float((df["high"] - df["low"]).tail(20).mean())
            f.cout_relatif = _cout_relatif(spec.spread * spec.point, atr)
            if "v" in df.columns:
                f.activite = float(df["v"].tail(60).mean())
            elif "tick_volume" in df.columns:
                f.activite = float(df["tick_volume"].tail(60).mean())
        except Exception as exc:  # noqa: BLE001
            f.motif = f"illisible : {type(exc).__name__}"
            fiches.append(f)
            continue

        # Edge mesuré : on prend le meilleur contexte connu de cet actif.
        pertinents = [v for k, v in par_contexte.items() if k.startswith(sym + "|")]
        if pertinents:
            meilleur = max(pertinents, key=lambda v: v.samples)
            f.edge_r, f.edge_n = meilleur.expectancy_r, meilleur.samples

        if avec_tv:
            f.avis_tv = avis_tradingview(sym, f.classe)

        fiches.append(f)
        if i % 25 == 0:
            print(f"  {i}/{len(catalogue)}…", flush=True)

    return fiches


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sans-tv", action="store_true",
                    help="ne pas interroger TradingView (plus rapide)")
    a = ap.parse_args()

    fiches = classer(relever(avec_tv=not a.sans_tv))
    ecrire(fiches, SORTIE)

    rangs = {r: [f for f in fiches if f.rang == r]
             for r in (RANG_A, RANG_B, RANG_C)}
    print(f"\n{'rang':5} {'actifs':>7}  cadence   historique")
    for r, cad, bar in ((RANG_A, "chaque tour", 600),
                        (RANG_B, "1 tour / 3", 400),
                        (RANG_C, "1 tour / 10", 300)):
        print(f"  {r:3} {len(rangs[r]):>7}  {cad:<12} {bar} barres")

    print(f"\nRang A ({len(rangs[RANG_A])}) :")
    for f in rangs[RANG_A][:24]:
        marque = " ←forcé" if f.forcee else ""
        print(f"  {f.symbol:12} {f.classe:8} score {f.score:.3f}  {f.motif}{marque}")

    illisibles = [f for f in fiches if f.motif.startswith("illisible")]
    if illisibles:
        print(f"\n{len(illisibles)} actif(s) illisible(s) — conservés au rang C, "
              "jamais écartés.")

    print(f"\n→ {SORTIE}")
    print("La boucle relit ce fichier à chaud : aucun redémarrage nécessaire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
