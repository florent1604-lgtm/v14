"""Plafond d'exposition par grappe de corrélation.

Le défaut que ce module comble
-------------------------------
`MAX_RISQUE_CUMULE_PCT` compte le risque total mais il est **aveugle à la
corrélation**. Le 07/08/2026, huit positions ouvertes respectaient le budget
de 6 % — et six d'entre elles portaient le yen, corrélées à **0.69** en
moyenne. Ce n'étaient pas six paris, c'était un pari pris six fois. Le
budget global était respecté ; l'exposition réelle valait le triple.

Pourquoi des grappes et non des classes d'actif
-----------------------------------------------
Un plafond par classe n'aurait rien vu : NZDJPY est du forex, JPN225 un
indice, ETH-JPY de la crypto. Trois classes différentes, un seul pari sur
le yen. Le regroupement doit venir des **prix**, pas des étiquettes.

D'où le clustering hiérarchique de Riskfolio-Lib, qui range les actifs
selon leur comportement observé. On n'utilise ni son optimiseur ni ses
poids continus — V14 prend des positions discrètes à risque fixe. On lui
emprunte la matrice de corrélation et l'arbre, rien de plus, et c'est un
usage partiel assumé.

Ce que ce module ne fait pas
----------------------------
Il ne décide d'aucune entrée. Il répond à « cette grappe porte-t-elle déjà
trop ? » et laisse l'appelant conclure.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
CACHE = RACINE / "results" / "grappes.json"

#: Risque cumulé maximal par grappe, en % de l'équité.
#:
#: Un tiers du budget global (6 %) : trois grappes indépendantes peuvent
#: le saturer, une seule ne le peut pas. C'est la définition opérationnelle
#: de « diversifié » pour ce bot.
MAX_RISQUE_GRAPPE_PCT = 2.0

#: Corrélation au-delà de laquelle deux actifs sont un seul pari.
#: 0.60 est bas exprès : à 0.69 mesuré entre paires JPY, un seuil à 0.80
#: les aurait laissées passer.
SEUIL_CORRELATION = 0.60

#: Fraîcheur de l'arbre de corrélation. Les régimes changent, mais pas en
#: une heure — et le recalcul lit 300 barres sur 54 actifs.
TTL_GRAPPES_S = 6 * 3600

#: Nombre de grappes visé.
#:
#: Le choix automatique (`opt_k_method`) rendait k=3 sur 55 actifs : des
#: paquets de vingt qui mêlaient BRENT, EURJPY et COCOA. Bloquer un trade
#: pétrole parce que quatre paires yen sont ouvertes serait faux.
#: À k=12, les familles se lisent d'elles-mêmes — les huit paires JPY
#: ensemble, les indices US ensemble, les indices européens à part, les
#: métaux précieux à part — sans qu'aucune étiquette n'ait été fournie.
NB_GRAPPES = 12


@dataclass
class Grappes:
    """Répartition des actifs en grappes de comportement."""

    par_actif: dict = field(default_factory=dict)   # symbole → id de grappe
    membres: dict = field(default_factory=dict)     # id → [symboles]
    calcule_le: float = 0.0
    methode: str = ""

    def grappe_de(self, symbole: str) -> str:
        """Grappe d'un actif. Un inconnu forme sa propre grappe.

        Le rattacher arbitrairement à une grappe existante inventerait une
        corrélation qu'on n'a pas mesurée ; l'isoler ne bride rien à tort.
        """
        return self.par_actif.get(str(symbole).upper(), f"seul:{symbole}")

    def to_dict(self) -> dict:
        return {"par_actif": self.par_actif, "membres": self.membres,
                "calcule_le": self.calcule_le, "methode": self.methode}


def _rendements(symboles, barres: int = 300, timeframe: str = "M15"):
    """Matrice de rendements alignés. Rend (DataFrame, symboles retenus)."""
    import pandas as pd

    from titanium.data.mt5_vendor import get_rates, mt5_session

    colonnes = {}
    with mt5_session():
        for s in symboles:
            try:
                df = get_rates(s, timeframe, barres)
                serie = df["close"].pct_change()
                # Un actif figé n'a pas de corrélation mesurable : l'inclure
                # produirait des NaN qui contamineraient toute la matrice.
                if serie.std() > 0:
                    colonnes[s] = serie
            except Exception:  # noqa: BLE001
                continue
    if len(colonnes) < 2:
        return pd.DataFrame(), []
    d = pd.DataFrame(colonnes).dropna()
    return d, list(d.columns)


def calculer(symboles, *, seuil: float = SEUIL_CORRELATION,
             barres: int = 300) -> Grappes:
    """Regroupe les actifs par comportement observé. Ne lève jamais.

    Utilise le clustering hiérarchique de Riskfolio-Lib quand il est
    disponible, et retombe sinon sur un regroupement direct par seuil de
    corrélation — moins fin, mais qui ne dépend d'aucune bibliothèque
    externe et suffit à repérer six positions sur le même sous-jacent.
    """
    g = Grappes(calcule_le=time.time())
    rend, retenus = _rendements(symboles, barres=barres)
    if not retenus:
        return g

    corr = rend.corr()

    try:
        import riskfolio as rp

        # `assets_clusters` range les actifs par proximité de comportement.
        # `linkage="ward"` et le critère de Gap Index évitent d'imposer un
        # nombre de grappes arbitraire.
        arbre = rp.assets_clusters(returns=rend, codependence="pearson",
                                   linkage="ward", k=NB_GRAPPES,
                                   leaf_order=True)
        for _, ligne in arbre.iterrows():
            actif = str(ligne["Assets"]).upper()
            # ⚠️ L'étiquette est une CHAÎNE — « Cluster 3 », pas 3. Un
            # `int()` direct lève, et l'exception renvoyait silencieusement
            # au repli : on croyait utiliser le clustering hiérarchique
            # alors qu'on agglomérait par seuil.
            brut = str(ligne["Clusters"]).strip()
            num = "".join(c for c in brut if c.isdigit()) or brut
            grappe = f"g{num}"
            g.par_actif[actif] = grappe
            g.membres.setdefault(grappe, []).append(actif)
        if g.par_actif:
            g.methode = "riskfolio/ward"
            return g
    except Exception:  # noqa: BLE001 — repli sans dépendance
        pass

    # ── Repli : agglomération simple au-dessus du seuil.
    reste = list(retenus)
    n = 0
    while reste:
        tete = reste.pop(0)
        grappe = f"g{n}"
        n += 1
        membres = [tete]
        for autre in list(reste):
            try:
                if abs(float(corr.loc[tete, autre])) >= seuil:
                    membres.append(autre)
                    reste.remove(autre)
            except Exception:  # noqa: BLE001
                continue
        for m in membres:
            g.par_actif[m.upper()] = grappe
        g.membres[grappe] = [m.upper() for m in membres]
    g.methode = f"seuil {seuil:.2f}"
    return g


def charger(symboles, *, ttl: float = TTL_GRAPPES_S) -> Grappes:
    """Grappes en vigueur, recalculées seulement si périmées."""
    try:
        if CACHE.exists():
            d = json.loads(CACHE.read_text(encoding="utf-8"))
            if time.time() - float(d.get("calcule_le", 0)) < ttl:
                return Grappes(par_actif=d.get("par_actif", {}),
                               membres=d.get("membres", {}),
                               calcule_le=d.get("calcule_le", 0),
                               methode=d.get("methode", ""))
    except Exception:  # noqa: BLE001
        pass

    g = calculer(symboles)
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(g.to_dict(), ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(CACHE)
    except Exception:  # noqa: BLE001
        pass
    return g


def risque_par_grappe(mt5, grappes: Grappes, equity: float) -> dict:
    """Risque engagé par grappe, en % de l'équité. Ne lève jamais."""
    out: dict = {}
    if equity <= 0:
        return out
    try:
        for p in (mt5.positions_get() or []):
            spec = mt5.symbol_info(p.symbol)
            if spec is None or not spec.trade_tick_size:
                continue
            if not p.sl:
                r = 2.0                    # sans stop : on suppose le plafond
            else:
                d = abs(p.price_open - p.sl)
                r = ((d / spec.trade_tick_size) * spec.trade_tick_value
                     * p.volume) / equity * 100.0
            cle = grappes.grappe_de(p.symbol)
            out[cle] = out.get(cle, 0.0) + r
    except Exception:  # noqa: BLE001
        return {}
    return out


def place_disponible(symbole: str, risque_pct: float, mt5, grappes: Grappes,
                     equity: float, *,
                     plafond: float = MAX_RISQUE_GRAPPE_PCT) -> tuple:
    """La grappe de cet actif peut-elle absorber ce risque de plus ?

    Returns:
        ``(autorisé, motif)``. Le motif est chiffré : « grappe g3 déjà à
        1.8 % » se vérifie, « trop corrélé » ne se vérifie pas.
    """
    cle = grappes.grappe_de(symbole)
    engage = risque_par_grappe(mt5, grappes, equity).get(cle, 0.0)
    if engage + risque_pct <= plafond:
        return True, ""
    membres = grappes.membres.get(cle, [])
    voisins = ", ".join(m for m in membres if m != symbole.upper())[:60]
    return False, (f"grappe {cle} déjà à {engage:.2f} % "
                   f"(plafond {plafond:.1f} %) — avec {voisins or 'aucun'}")
