"""Rejeu historique — la même chaîne de décision, sur le passé.

POURQUOI CE MODULE DÉCIDE DE LA RENTABILITÉ
--------------------------------------------
La mesure d'edge exige 20 trades clos par contexte, et l'analyse discriminante
30 par groupe. En live, à quelques trades par jour, cela prend des mois. Rejouer
l'historique donne les mêmes échantillons en quelques minutes.

C'est le seul moyen de répondre à « quels contextes sont porteurs ? » avant
d'avoir dépensé un an de trading pour le découvrir.

QUATRE RÈGLES, ET ELLES NE SONT PAS NÉGOCIABLES
------------------------------------------------
1. **Aucun regard vers l'avenir.** À la barre *i*, les features ne voient que
   les barres ≤ *i*. C'est obtenu par découpage progressif, pas par confiance :
   `build_feats` reçoit une tranche, jamais la série complète.

2. **Le coût est appliqué.** Spread réel du courtier à l'entrée ET à la sortie.
   V12 a mesuré que le coût est le tueur dominant — un backtest sans coût est
   une fiction consolante. Un profit factor de 2 sans frais peut valoir 0,8
   avec.

3. **La sortie est simulée barre par barre**, avec la même gestion qu'en live :
   stop, objectif, puis breakeven et trailing. Un backtest qui sort « au
   meilleur moment » ne mesure rien.

4. **Le stop et l'objectif touchés dans la même barre comptent comme une
   PERTE.** On ne sait pas lequel a été touché en premier sans données
   intra-barre — supposer le gain est le biais le plus courant et le plus
   flatteur des backtests maison.

CE QUE ÇA NE REMPLACE PAS
--------------------------
Une validation temporelle. Un résultat en échantillon (*in-sample*) ne prouve
rien : V12 a produit XAUUSD à PF 2,31 en Python et PF 0,90 au testeur natif.
Utiliser `decouper_walk_forward()` et ne jamais conclure sur l'IS seul.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime

import pandas as pd

#: Barres minimales avant la première évaluation — l'EMA200 HTF en a besoin.
AMORCAGE = 250

#: Durée de vie maximale d'une position, en barres LTF. Sans butoir, un trade
#: peut rester ouvert jusqu'à la fin de l'historique et fausser l'échantillon.
MAX_BARRES = 200


def cout_spread_r(spread: float, r_unit: float) -> float:
    """Un spread ask-bid complet rapporté à la distance de risque.

    Le spread est réparti en deux demi-spreads dans le rejeu (entrée et
    sortie), mais il n'est payé qu'une fois au total.
    """
    return float(spread) / float(r_unit) if float(r_unit) > 0 else 0.0


@dataclass
class Trade:
    """Un trade simulé, avec tout ce qu'il faut pour l'analyser ensuite."""
    symbol: str
    side: int
    bar_entree: str
    bar_sortie: str = ""
    prix_entree: float = 0.0
    prix_sortie: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    r_unit: float = 0.0            # distance entrée↔stop, en prix
    pnl_r: float = 0.0             # résultat NET de coûts, en R
    cost_r: float = 0.0            # coût aller-retour, en R
    mae_r: float = 0.0             # pire excursion
    mfe_r: float = 0.0             # meilleure excursion
    barres: int = 0
    motif: str = ""                # sl / tp / trailing / temps / stop_temporel / fin
    contexte: str = ""             # clé de contexte pour la mesure d'edge
    pillars: int = 0
    family: str = ""
    indicators: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Resultat:
    """Bilan d'un rejeu. Les chiffres sont NETS de coûts."""
    symbol: str = ""
    trades: list[Trade] = field(default_factory=list)
    barres_evaluees: int = 0
    n_enter: int = 0
    erreurs: int = 0

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def esperance_r(self) -> float:
        return sum(t.pnl_r for t in self.trades) / self.n if self.n else 0.0

    @property
    def winrate(self) -> float:
        return sum(1 for t in self.trades if t.pnl_r > 0) / self.n if self.n else 0.0

    @property
    def profit_factor(self) -> float:
        gains = sum(t.pnl_r for t in self.trades if t.pnl_r > 0)
        pertes = -sum(t.pnl_r for t in self.trades if t.pnl_r < 0)
        return (gains / pertes) if pertes > 0 else (math.inf if gains > 0 else 0.0)

    @property
    def cout_moyen_r(self) -> float:
        return sum(t.cost_r for t in self.trades) / self.n if self.n else 0.0

    def resume(self) -> dict:
        return {
            "symbol": self.symbol, "trades": self.n,
            "barres_evaluees": self.barres_evaluees, "enter": self.n_enter,
            "esperance_r": round(self.esperance_r, 4),
            "winrate": round(self.winrate, 4),
            "profit_factor": round(self.profit_factor, 3)
            if math.isfinite(self.profit_factor) else None,
            "cout_moyen_r": round(self.cout_moyen_r, 4),
            "erreurs": self.erreurs,
        }


def _simuler_sortie(ltf: pd.DataFrame, i_entree: int, side: int,
                    entree: float, sl: float, tp: float, *,
                    breakeven_r: float, trail_start_r: float,
                    trail_dist_r: float,
                    max_barres: int,
                    stop_temporel: tuple[int, float] | None = None,
                    ) -> tuple[int, float, str, float, float]:
    """Déroule la position barre par barre. Rend (index, prix, motif, mae_R, mfe_R).

    La gestion reproduit celle du live : breakeven puis trailing en cliquet.

    Args:
        stop_temporel: ``(barres_max, seuil_r)`` ou ``None``. **None par
            défaut** — le comportement historique ne bouge pas. Activé, le trade
            est sorti au marché (clôture de la barre) si, à la barre
            ``barres_max`` comptée depuis l'entrée, la meilleure excursion
            (le pic favorable, en R) n'a jamais atteint ``seuil_r``. Motif
            ``stop_temporel``.

            Le critère est le **pic**, pas le prix courant : « n'a pas atteint
            +0.5 R » se lit « n'y est jamais monté », pas « n'y est pas en ce
            moment ». Un trade monté à +0.9 R puis redescendu a prouvé qu'il
            pouvait courir — c'est au trailing de le gérer, pas au temporel.
    """
    r = abs(entree - sl)
    if r <= 0:
        return i_entree, entree, "r_nul", 0.0, 0.0

    # Déplié une fois : ce test tourne à chaque barre de chaque trade.
    st_barres, st_seuil = (-1, 0.0)
    if stop_temporel is not None:
        st_barres = int(stop_temporel[0])
        st_seuil = float(stop_temporel[1])

    stop = sl
    pic = 0.0
    # Pic NON plancheré — le seul à servir au stop temporel. `pic` (la MFE
    # rapportée) démarre à 0.0 et ne descend jamais : « pic < 0.0 » y serait
    # impossible et `seuil_r=0.0` deviendrait silencieusement inopérant. Or
    # « n'est jamais monté au-dessus de l'entrée » est précisément la version la
    # plus douce de l'hypothèse, celle qu'on veut pouvoir tester.
    # Pour tout seuil > 0 les deux critères coïncident.
    pic_brut = -math.inf
    mae = 0.0
    fin = min(i_entree + max_barres, len(ltf) - 1)

    for i in range(i_entree + 1, fin + 1):
        haut = float(ltf["high"].iloc[i])
        bas = float(ltf["low"].iloc[i])

        fav = ((haut - entree) if side > 0 else (entree - bas)) / r
        adv = ((bas - entree) if side > 0 else (entree - haut)) / r
        pic = max(pic, fav)
        pic_brut = max(pic_brut, fav)
        mae = min(mae, adv)

        touche_sl = (bas <= stop) if side > 0 else (haut >= stop)
        touche_tp = (haut >= tp) if side > 0 else (bas <= tp)

        # Règle 4 : les deux dans la même barre ⇒ on compte la PERTE. Sans
        # données intra-barre on ne sait pas l'ordre, et supposer le gain est
        # le biais qui flatte tous les backtests maison.
        if touche_sl:
            motif = "sl" if stop == sl else "trailing"
            return i, stop, motif, mae, pic
        if touche_tp:
            return i, tp, "tp", mae, pic

        # Stop temporel — testé APRÈS le stop et l'objectif, jamais avant.
        # L'ordre est critique : une barre qui touche le SL est une perte au
        # stop, pas une sortie temporelle. Inverser l'ordre déplacerait des
        # pertes pleines vers un motif plus flatteur et inventerait un gain.
        if st_barres >= 0 and (i - i_entree) >= st_barres and pic_brut < st_seuil:
            return i, float(ltf["close"].iloc[i]), "stop_temporel", mae, pic

        # Gestion dynamique — appliquée APRÈS les tests de touche de la barre,
        # sinon on déplacerait le stop avec une information de la barre en cours.
        if pic >= trail_start_r:
            candidat = entree + side * (pic - trail_dist_r) * r
            if side * (candidat - stop) > 0:
                stop = candidat
        elif pic >= breakeven_r and stop == sl:
            stop = entree + side * 0.05 * r      # tampon de coûts

    return fin, float(ltf["close"].iloc[fin]), "temps", mae, pic


def rejouer(symbol: str, ltf: pd.DataFrame, htf: pd.DataFrame, *,
            spread: float = 0.0,
            require_edge: bool = False,
            sl_atr_k: float = 1.5,
            rr_ratio: float = 2.0,
            breakeven_r: float = 0.8,
            trail_start_r: float = 1.2,
            trail_dist_r: float = 0.8,
            amorcage: int = AMORCAGE,
            max_barres: int = MAX_BARRES,
            stop_temporel: tuple[int, float] | None = None,
            pas: int = 1,
            avec_indicateurs: bool = True,
            fenetre: int = 400) -> Resultat:
    """Rejoue la chaîne de décision sur l'historique. Ne lève jamais.

    Args:
        symbol: instrument (sert au contexte, pas au calcul).
        ltf: bougies de la timeframe d'entrée, index temporel croissant.
        htf: bougies de la timeframe haute.
        spread: spread ask-bid complet du courtier en unités de prix. Il est
            réparti en deux demi-spreads (entrée + sortie), donc payé une seule
            fois au total. Le laisser à 0 produit un résultat flatteur et faux.
        stop_temporel: `(barres_max, seuil_r)` ou `None`. **None par défaut** :
            les résultats déjà mesurés ne bougent pas. Voir `_simuler_sortie`.
        pas: n'évaluer qu'une barre sur `pas`. Accélère un balayage large.
        avec_indicateurs: calcule le panel sur les barres retenues seulement —
            c'est rare, donc peu coûteux, et c'est tout ce dont l'analyse
            discriminante a besoin.

    Returns:
        Resultat — trades, statistiques nettes de coûts.
    """
    from titanium.edge import context_from_feats
    from titanium.features.builder import build_feats
    from titanium.gates import confluence_gate

    res = Resultat(symbol=symbol)
    if ltf is None or htf is None or len(ltf) < amorcage + 10:
        return res

    # Index HTF aligné : pour chaque barre LTF, la dernière barre HTF CLÔTURÉE.
    # `searchsorted` en 'right' puis −1 : jamais la barre HTF en cours.
    try:
        pos_htf = htf.index.searchsorted(ltf.index, side="right") - 1
    except Exception:  # noqa: BLE001
        return res

    i = amorcage
    n = len(ltf)
    while i < n - 1:
        j = int(pos_htf[i])
        if j < amorcage:
            i += pas
            continue

        try:
            feats = build_feats(
                ltf.iloc[max(0, i - fenetre + 1):i + 1],
                htf.iloc[max(0, j - fenetre + 1):j + 1],
                with_indicators=False)
            res.barres_evaluees += 1
            d = confluence_gate.evaluate(feats, require_edge=require_edge)
        except Exception:  # noqa: BLE001
            res.erreurs += 1
            i += pas
            continue

        if not d.entered:
            i += pas
            continue
        res.n_enter += 1

        trace = feats.get("_trace") or {}
        atr = float(trace.get("atr") or 0.0)
        if atr <= 0:
            i += pas
            continue

        side = d.side
        # Entrée à la clôture de la barre de décision, spread payé.
        entree = float(ltf["close"].iloc[i]) + side * spread / 2.0
        r_unit = sl_atr_k * atr
        sl = entree - side * r_unit
        tp = entree + side * r_unit * rr_ratio

        i_sortie, prix_sortie, motif, mae, mfe = _simuler_sortie(
            ltf, i, side, entree, sl, tp,
            breakeven_r=breakeven_r, trail_start_r=trail_start_r,
            trail_dist_r=trail_dist_r, max_barres=max_barres,
            stop_temporel=stop_temporel)

        # Seconde moitié du spread payée à la sortie : une moitié à l'entrée
        # + une moitié ici = un spread ask-bid complet, pas deux.
        prix_sortie -= side * spread / 2.0
        brut_r = (prix_sortie - entree) * side / r_unit
        cout_r = cout_spread_r(spread, r_unit)

        ctx = context_from_feats(symbol, feats, side)
        indic = {}
        if avec_indicateurs:
            try:
                from titanium.features import indicators
                indic = indicators.panel(
                    ltf.iloc[max(0, i - fenetre + 1):i + 1],
                    htf.iloc[max(0, j - fenetre + 1):j + 1])
            except Exception:  # noqa: BLE001
                pass

        res.trades.append(Trade(
            symbol=symbol, side=side,
            bar_entree=str(ltf.index[i]), bar_sortie=str(ltf.index[i_sortie]),
            prix_entree=round(entree, 8), prix_sortie=round(prix_sortie, 8),
            sl=round(sl, 8), tp=round(tp, 8), r_unit=round(r_unit, 8),
            pnl_r=round(brut_r, 4), cost_r=round(cout_r, 4),
            mae_r=round(mae, 4), mfe_r=round(mfe, 4),
            barres=i_sortie - i, motif=motif,
            contexte=ctx.key(), pillars=ctx.n_pillars,
            family=str(feats.get("setup_family") or ""),
            indicators=indic))

        # On reprend APRÈS la sortie : pas de positions superposées, comme en
        # live où le plafond par symbole vaut 1.
        i = max(i + pas, i_sortie + 1)

    return res


def decouper_walk_forward(res: Resultat, n_segments: int = 3) -> list[Resultat]:
    """Découpe les trades en segments temporels consécutifs.

    Un résultat global n'apprend rien : c'est la **stabilité** d'un segment à
    l'autre qui distingue un edge d'un artefact. Un contexte porteur sur le
    premier tiers et perdant sur les deux autres est un sur-ajustement, pas une
    découverte.
    """
    if not res.trades or n_segments < 2:
        return [res]
    tries = sorted(res.trades, key=lambda t: t.bar_entree)
    taille = max(1, len(tries) // n_segments)
    out = []
    for k in range(n_segments):
        debut = k * taille
        fin = len(tries) if k == n_segments - 1 else (k + 1) * taille
        seg = Resultat(symbol=f"{res.symbol}#{k + 1}")
        seg.trades = tries[debut:fin]
        out.append(seg)
    return out


def journaliser(res: Resultat, chemin) -> int:
    """Écrit les trades au format du journal d'edge. Rend le nombre de lignes."""
    import json
    from pathlib import Path

    p = Path(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a", encoding="utf-8") as f:
        for t in res.trades:
            f.write(json.dumps({
                "context": t.contexte,
                "pnl_r": t.pnl_r,
                "cost_r": t.cost_r,
                "closed_at": t.bar_sortie,
                "ticket": f"bt:{t.symbol}:{t.bar_entree}",
                "exit_reason": t.motif,
                "indicators": t.indicators,
                "source": "backtest",
            }, ensure_ascii=False) + "\n")
            n += 1
    return n
