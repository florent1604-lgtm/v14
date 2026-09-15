"""Ce que le triplement du budget de risque represente — derive du CODE livre.

POURQUOI CET OUTIL EXISTE
-------------------------
Le 13/09/2026, deux plafonds ont ete releves :

    plafond par grappe de correlation   2,0 %  ->  5,7 %
    budget global d'exposition          6,0 %  -> 17,1 %

La hausse est entree au dossier **sans mesure**. Cet outil produit cette mesure,
et il ne lit AUCUN chiffre de prose : les plafonds sont importes des modules qui
les declarent, l'equite vient du compte, et les conversions en exposition et en
marge passent par `titanium.sizing.loss_per_lot` — la fonction que l'executeur
emploie deja pour dimensionner un lot.

    .venv\\Scripts\\python.exe -X utf8 tools/mesure_budget_risque.py
    .venv\\Scripts\\python.exe -X utf8 tools/mesure_budget_risque.py --equite 1865

LECTURE SEULE
-------------
`mt5.initialize()` + `account_info` + `positions_get` + `symbol_info`, rien
d'autre : aucun ordre, aucun stop, aucun parametre de trading. Sans terminal MT5
joignable, l'outil se rabat sur `--equite` et le declare.

CE QUE LA MESURE NE PERMET PAS DE CONCLURE
------------------------------------------
Elle ne rejoue aucune arene, aucun backtest, aucun seed. Elle ne dit donc RIEN
sur la rentabilite du budget releve, ni sur la probabilite que les stops soient
touches ensemble. Elle convertit des plafonds en euros, en exposition et en
marge, sur l'etat reel du compte a l'instant du releve.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

#: Valeurs d'AVANT, relevees dans les diffs des commits qui les ont changees :
#:   `8a2a1d1`  risk: porter le plafond par grappe a 5.7 pct      (2,0 -> 5,7)
#:   `998a48d`  risk: aligner le budget global a 17.1 pct         (6,0 -> 17,1)
ANCIEN_GRAPPE_PCT = 2.0
ANCIEN_GLOBAL_PCT = 6.0

#: Le facteur par lequel le budget global a ete multiplie, et la lecture qui
#: compte : trois grappes independantes saturent l'enveloppe globale.
FACTEUR_GRAPPES = 3.0


@dataclass(frozen=True)
class Plafonds:
    """Les plafonds en vigueur, LUS dans le code qui les declare."""

    grappe_pct: float
    global_pct: float
    panier_pct: float
    par_symbole: int
    par_trade_pct: float

    @property
    def invariant_tenu(self) -> bool:
        """`3 x plafond de grappe == budget global` : l'invariant declare."""
        return math.isclose(
            FACTEUR_GRAPPES * self.grappe_pct, self.global_pct, abs_tol=1e-9
        )

    @property
    def grappes_saturantes(self) -> float:
        """Combien de grappes pleines le budget global laisse tenir."""
        return self.global_pct / self.grappe_pct


def plafonds() -> Plafonds:
    """Importe les plafonds la ou ils vivent. Un seul proprietaire chacun."""
    from titanium.correlation import MAX_RISQUE_GRAPPE_PCT
    from titanium.sizing import MAX_RISK_PCT
    from tools.live_demo import (
        MAX_PAR_SYMBOLE,
        MAX_RISQUE_CUMULE_PCT,
        MAX_RISQUE_PANIER_PCT,
    )

    return Plafonds(
        grappe_pct=float(MAX_RISQUE_GRAPPE_PCT),
        global_pct=float(MAX_RISQUE_CUMULE_PCT),
        panier_pct=float(MAX_RISQUE_PANIER_PCT),
        par_symbole=int(MAX_PAR_SYMBOLE),
        par_trade_pct=float(MAX_RISK_PCT),
    )


def euros(equite: float, pct: float) -> float:
    """Le risque que ce pourcentage represente en devise du compte."""
    return equite * pct / 100.0


# ═══════════════════════ le livre reel, en lecture seule ═══════════════════════


@dataclass(frozen=True)
class Ligne:
    symbole: str
    ticket: str
    volume: float
    prix: float
    stop_distance: float
    risque_eur: float
    notionnel_eur: float
    marge_eur: float


@dataclass(frozen=True)
class Livre:
    disponible: bool
    equite: float = 0.0
    solde: float = 0.0
    levier: float = 0.0
    devise: str = ""
    serveur: str = ""
    motif: str = ""
    lignes: tuple[Ligne, ...] = ()

    @property
    def risque_eur(self) -> float:
        return sum(ligne.risque_eur for ligne in self.lignes)

    @property
    def notionnel_eur(self) -> float:
        return sum(ligne.notionnel_eur for ligne in self.lignes)

    @property
    def marge_eur(self) -> float:
        return sum(ligne.marge_eur for ligne in self.lignes)


def lire_le_livre() -> Livre:
    """Le compte et ses positions ouvertes. Aucune ecriture, aucun ordre."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError as exc:  # pragma: no cover - poste sans MT5
        return Livre(False, motif=f"MetaTrader5 absent : {exc}")

    try:
        from titanium.data.mt5_vendor import SymbolSpec
        from titanium.sizing import loss_per_lot
    except ImportError as exc:  # pragma: no cover
        return Livre(False, motif=f"titanium.sizing indisponible : {exc}")

    def _spec(info) -> SymbolSpec:
        """Le meme objet que  construit, sans ouvrir de session.

        On ne peut pas appeler  ici : il ouvre sa propre
        , dont la fermeture couperait la notre. On ne fait non
        plus  : cet outil LIT, il n'abonne rien.
        """
        return SymbolSpec(
            name=str(info.name), digits=int(info.digits), point=float(info.point),
            volume_min=float(info.volume_min), volume_max=float(info.volume_max),
            volume_step=float(info.volume_step),
            trade_contract_size=float(info.trade_contract_size),
            spread=int(info.spread),
            tick_value=float(getattr(info, "trade_tick_value", 0.0) or 0.0),
            tick_size=float(getattr(info, "trade_tick_size", 0.0) or 0.0),
        )

    if not mt5.initialize():
        return Livre(False, motif=f"MT5 injoignable : {mt5.last_error()}")
    try:
        compte = mt5.account_info()
        if compte is None:
            return Livre(False, motif="account_info indisponible")
        equite = float(compte.equity)
        levier = float(compte.leverage or 0.0)
        lignes: list[Ligne] = []
        for position in mt5.positions_get() or []:
            info = mt5.symbol_info(position.symbol)
            if info is None:
                continue
            spec = _spec(info)
            prix = float(position.price_current or position.price_open)
            volume = float(position.volume)
            stop = abs(float(position.price_open) - float(position.sl))
            # Meme formule que l'executeur : distance / tick_size x tick_value.
            risque = loss_per_lot(spec, stop) * volume if stop > 0 else math.nan
            notionnel = volume * spec.trade_contract_size * prix
            lignes.append(Ligne(
                symbole=position.symbol,
                ticket=str(position.ticket),
                volume=volume,
                prix=prix,
                stop_distance=stop,
                risque_eur=risque,
                notionnel_eur=notionnel,
                marge_eur=notionnel / levier if levier > 0 else math.nan,
            ))
        return Livre(
            True, equite=equite, solde=float(compte.balance), levier=levier,
            devise=str(compte.currency), serveur=str(compte.server),
            lignes=tuple(lignes),
        )
    finally:
        mt5.shutdown()


# ═════════════════════════════ rapport ═════════════════════════════


def _ligne(titre: str) -> None:
    print(f"\n{titre}")
    print("-" * len(titre))


def rapport(livre: Livre, equite_forcage: float | None) -> dict:
    """Le rapport complet, en structure — imprimable et comparable."""
    p = plafonds()
    equite = equite_forcage if equite_forcage else livre.equite
    source_equite = "--equite" if equite_forcage else "compte MT5"

    out: dict = {
        "releve_a": datetime.now(timezone.utc).isoformat(),
        "equite_eur": round(equite, 2),
        "source_equite": source_equite,
        "plafonds": asdict(p),
        "invariant": {
            "enonce": f"{FACTEUR_GRAPPES:g} x {p.grappe_pct} = {p.global_pct}",
            "tenu": p.invariant_tenu,
            "grappes_saturantes": round(p.grappes_saturantes, 4),
        },
    }

    _ligne("1. LES PLAFONDS, LUS DANS LE CODE")
    print(f"  grappe de correlation : {p.grappe_pct:.1f} %  "
          f"(titanium/correlation.py)")
    print(f"  budget global         : {p.global_pct:.1f} %  (tools/live_demo.py)")
    print(f"  panier par symbole    : {p.panier_pct:.1f} %  (tools/live_demo.py)")
    print(f"  positions par symbole : {p.par_symbole}")
    print(f"  plafond par trade     : {p.par_trade_pct:.1f} %  (titanium/sizing.py)")
    print(f"  equite retenue        : {equite:.2f} EUR  ({source_equite})")

    _ligne("2. INVARIANT")
    etat = "TENU" if p.invariant_tenu else "ROMPU"
    print(f"  {FACTEUR_GRAPPES:g} x {p.grappe_pct} = {p.global_pct}  -> {etat}")
    print(f"  grappes pleines que le budget global laisse tenir : "
          f"{p.grappes_saturantes:.2f}")

    nouveau_g, nouveau_gl = euros(equite, p.grappe_pct), euros(equite, p.global_pct)
    ancien_g, ancien_gl = (euros(equite, ANCIEN_GRAPPE_PCT),
                           euros(equite, ANCIEN_GLOBAL_PCT))
    out["euros"] = {
        "nouveau_grappe": round(nouveau_g, 2),
        "nouveau_global": round(nouveau_gl, 2),
        "ancien_grappe": round(ancien_g, 2),
        "ancien_global": round(ancien_gl, 2),
        "facteur": round(p.global_pct / ANCIEN_GLOBAL_PCT, 4),
    }

    _ligne("3. CE QUE CELA FAIT EN EUROS")
    print(f"  {'':22} {'AVANT':>12} {'APRES':>12} {'FACTEUR':>9}")
    print(f"  {'grappe (2,0 -> 5,7)':22} {ancien_g:>10.2f} EUR "
          f"{nouveau_g:>10.2f} EUR {p.grappe_pct / ANCIEN_GRAPPE_PCT:>8.2f}x")
    print(f"  {'global (6,0 -> 17,1)':22} {ancien_gl:>10.2f} EUR "
          f"{nouveau_gl:>10.2f} EUR {p.global_pct / ANCIEN_GLOBAL_PCT:>8.2f}x")
    print(f"  ecart sur l'enveloppe globale : +{nouveau_gl - ancien_gl:.2f} EUR")

    _ligne("4. CE QUI N'A PAS BOUGE")
    print(f"  plafond par trade : {p.par_trade_pct:.1f} % = "
          f"{euros(equite, p.par_trade_pct):.2f} EUR "
          "(titanium/sizing.py, inchange)")
    print(f"  panier par symbole : {p.panier_pct:.1f} % = "
          f"{euros(equite, p.panier_pct):.2f} EUR (inchange)")
    print("  -> le risque d'UN trade n'a pas triple. C'est le NOMBRE de paris")
    print("     non correles que l'enveloppe autorise qui a triple.")

    if livre.lignes:
        _ligne("5. LE LIVRE REEL")
        print(f"  compte {livre.serveur} · {livre.devise} · levier 1:{livre.levier:.0f} "
              f"· solde {livre.solde:.2f}")
        print(f"  {'symbole':12} {'vol':>6} {'stop':>9} {'risque EUR':>11} "
              f"{'notionnel':>11} {'marge':>8}")
        for ligne in livre.lignes:
            print(f"  {ligne.symbole:12} {ligne.volume:>6.2f} "
                  f"{ligne.stop_distance:>9.2f} {ligne.risque_eur:>10.2f} "
                  f"{ligne.notionnel_eur:>10.2f} {ligne.marge_eur:>8.2f}")
        engage = livre.risque_eur / equite * 100.0 if equite else 0.0
        print(f"  {'TOTAL':12} {'':>6} {'':>9} {livre.risque_eur:>10.2f} "
              f"{livre.notionnel_eur:>10.2f} {livre.marge_eur:>8.2f}")
        print(f"  risque engage {engage:.2f} % de l'equite "
              f"-> occupation {engage / p.global_pct * 100:.1f} % du budget "
              f"({p.global_pct:.1f} %)")
        out["livre"] = {
            "risque_eur": round(livre.risque_eur, 2),
            "notionnel_eur": round(livre.notionnel_eur, 2),
            "marge_eur": round(livre.marge_eur, 2),
            "engage_pct": round(engage, 3),
            "occupation_budget_pct": round(engage / p.global_pct * 100, 2),
            "positions": len(livre.lignes),
        }
        ratio = livre.notionnel_eur / livre.risque_eur if livre.risque_eur else 0.0
        out["ratio_notionnel_sur_risque"] = round(ratio, 2)
    else:
        print(f"\n5. LE LIVRE REEL\n{'-' * 15}\n  indisponible : {livre.motif}")

    _ligne("6. SCENARIO SATURE — trois grappes a leur plafond")
    print(f"  risque total          : {nouveau_gl:.2f} EUR "
          f"({p.global_pct:.1f} % de l'equite)")
    print(f"  perte si TOUS les stops sont touches, en meme temps : "
          f"{nouveau_gl:.2f} EUR")
    print(f"  comparaison a l'ancien scenario sature : {ancien_gl:.2f} EUR "
          f"(+{nouveau_gl - ancien_gl:.2f} EUR de perte potentielle)")
    sature = {"risque_eur": round(nouveau_gl, 2),
              "perte_si_tous_stops": round(nouveau_gl, 2)}
    if livre.lignes and livre.risque_eur > 0:
        ratio = livre.notionnel_eur / livre.risque_eur
        notionnel = nouveau_gl * ratio
        marge = notionnel / livre.levier if livre.levier > 0 else math.nan
        print(f"  exposition correspondante : {notionnel:.2f} EUR de notionnel "
              f"(ratio mesure {ratio:.1f} x le risque)")
        print(f"  marge correspondante      : {marge:.2f} EUR a 1:{livre.levier:.0f}")
        sature.update(notionnel_eur=round(notionnel, 2),
                      marge_eur=round(marge, 2), ratio=round(ratio, 2))
        print("  ⚠️  le notionnel est extrapole du RATIO mesure sur le livre "
              "actuel :")
        print("     il suppose des distances de stop comparables.")
    else:
        print("  exposition et marge : non calculables ici — le livre est "
              "illisible ou son risque est nul")
    out["scenario_sature"] = sature

    _ligne("7. CE QUE CETTE MESURE NE PERMET PAS DE CONCLURE")
    for ligne in (
        "Aucune arene, aucun backtest, aucun seed n'a ete rejoue : rien ici ne",
        "dit que le budget releve est rentable, ni qu'il est soutenable.",
        "Aucune correlation de sorties : la perte 'tous stops touches' est une",
        "borne haute simultanee, pas une esperance de perte.",
        "La marge est derivee (notionnel / levier) : MT5 rend `margin_initial`",
        "a 0 pour ces symboles, donc la marge n'est pas lue, elle est calculee.",
        "Un notionnel extrapole depuis le livre courant ne vaut que si les",
        "distances de stop du scenario sature restent comparables.",
    ):
        print(f"  {ligne}")
    return out


def main() -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    analyseur.add_argument("--equite", type=float, default=None,
                           help="equite de reference, si MT5 est injoignable")
    analyseur.add_argument("--json", type=Path, default=None,
                           help="ecrit le rapport structure a ce chemin")
    args = analyseur.parse_args()

    livre = lire_le_livre()
    if not livre.disponible and args.equite is None:
        print(f"MT5 indisponible ({livre.motif}) : relancer avec --equite <montant>.")
        return 1
    resultat = rapport(livre, args.equite)
    if args.json:
        args.json.write_text(
            json.dumps(resultat, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"\nrapport ecrit : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
