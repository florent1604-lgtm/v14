"""Dimensionnement PAR ACTIF — le lot s'adapte à l'instrument, pas l'inverse.

LE PROBLÈME QUE CE MODULE RÉSOUT
---------------------------------
Un pourcentage de risque global ne veut rien dire sur un petit compte. Mesuré
sur la démo à 67 USD, le **lot minimum du courtier** coûte :

    EURUSD  0.01 →  0.84 USD  =  1.2 % de l'equity
    US500   0.1  →  1.49 USD  =  2.2 %
    GER40   0.1  →  6.29 USD  =  9.4 %
    XAUUSD  0.01 → 17.32 USD  = 25.8 %

À 1 % de risque, **aucun** de ces actifs n'est traçable : le lot minimum
dépasse le budget. Forcer l'entrée quand même (le `allow_min_lot_overrisk` de
V12) revient à risquer 25 % du compte sur un seul trade XAUUSD en croyant en
risquer 1. C'est le piège du micro-capital, et il ne se règle pas avec un
réglage global.

LA RÈGLE
--------
Chaque actif est jugé **sur son propre coût minimum** :

* le lot idéal tient dans le budget cible  → on le prend ;
* le lot minimum coûte plus que la cible mais reste **sous le plafond dur** →
  on entre au lot minimum, et le risque effectif est **nommé**, pas subi ;
* le lot minimum dépasse le plafond → **l'actif est refusé pour ce compte**.
  Pas ce trade : cet actif, tant que l'equity n'a pas grandi.

Le compte décide donc lui-même de son univers traçable. Un compte à 67 USD
trade les paires forex ; à 1 500 USD les indices s'ouvrent d'eux-mêmes, sans
qu'aucune liste ne soit à maintenir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from titanium.data.mt5_vendor import SymbolSpec

#: Risque visé par trade, en % de l'equity. C'est une CIBLE, pas une garantie :
#: le lot minimum du courtier peut la dépasser.
TARGET_RISK_PCT = 1.0

#: Plafond DUR. Au-delà, l'actif est refusé pour ce compte. 2 % est le double
#: de la cible — assez pour absorber le lot minimum des paires forex, trop peu
#: pour laisser passer un indice ou un métal sur un petit compte.
MAX_RISK_PCT = 2.0

#: Part maximale du risque qu'un aller-retour de spread peut consommer.
#:
#: ⚠️ Le code affichait historiquement 25 %, mais `echelle._cout_relatif`
#: doublait un spread ask-bid déjà complet. Le plafond RÉELLEMENT appliqué
#: était donc proche de 12,5 %. Corriger l'unité tout en laissant 25 % aurait
#: élargi silencieusement l'univers crypto de 4 à 17 actifs le 08/08/2026.
#:
#: On conserve le comportement effectif à 12,5 % tant que le journal de trades
#: clos ne permet pas de calibrer l'espérance par décile de coût. 25 % reste
#: une hypothèse à tester, pas une autorisation de trading.
#:
#: CE PLAFOND N'EST PAS UN CONFORT. Le 07/08/2026, l'univers a été ouvert
#: aux 149 symboles du catalogue sans ce contrôle : 112 d'entre eux ont un
#: coût apparent supérieur au plafond, certains absurdes
#: (USDCHC 12 000 %, USDCAC 3 714 %). Résultat mesuré sur une journée :
#: 16 perdants pour 2 gagnants, −318 EUR, dont 17 sorties sur 18 au stop.
#:
#: Un trade dont le quart du risque est perdu avant d'exister ne peut pas
#: gagner : il faut un mouvement d'un quart de R supplémentaire rien que
#: pour revenir à zéro. À 40 %, c'est arithmétiquement condamné.
MAX_COUT_SPREAD_PCT = 0.125

#: Âge maximal de la dernière barre, en heures. Au-delà, l'instrument ne
#: cote plus.
#:
#: ⚠️ Un instrument éteint passait le filtre de coût pour la MAUVAISE
#: raison : USDCHC n'a pas coté depuis le 27/03/2026, son ATR est figé à
#: 0.000002, et un spread parfaitement normal de 12 points divisé par cet
#: ATR mort donnait 12 000 %. Le rejet était juste, le motif faux — et un
#: instrument mort dont le spread se resserrerait passerait la porte.
#: On teste donc la VIE de l'instrument, pas seulement son prix.
MAX_AGE_BARRE_H = 72.0


@dataclass(frozen=True)
class Budget:
    """Décision de dimensionnement pour UN actif, à UN instant."""
    symbol: str
    tradable: bool
    lot: float = 0.0
    risk_money: float = 0.0        # risque réel une fois le lot arrondi
    target_money: float = 0.0      # risque visé avant contrainte de lot
    effective_pct: float = 0.0     # risque réel en % de l'equity
    at_min_lot: bool = False       # True = le lot minimum commande
    reason: str = ""
    #: Unité de temps sur laquelle le stop a été calibré. Voyage avec le
    #: budget : la boucle doit analyser CETTE unité, sinon le stop calculé
    #: ici ne correspondrait à rien de ce qu'elle regarde.
    timeframe: str = "M15"
    #: Part du risque mangée par le spread, à l'unité retenue.
    cout_spread: float = 0.0

    @property
    def overshoot(self) -> float:
        """Rapport risque réel / risque visé. 1.0 = cible respectée."""
        return (self.risk_money / self.target_money) if self.target_money else 0.0


def loss_per_lot(spec: SymbolSpec, stop_distance: float) -> float:
    """Perte en devise du compte pour 1 lot si le stop est touché.

    ``distance / tick_size × tick_value`` — le seul calcul juste dès que la
    devise de cotation diffère de celle du compte.
    """
    if not (stop_distance > 0 and math.isfinite(stop_distance)):
        return 0.0
    if spec.tick_size <= 0 or spec.tick_value <= 0:
        return 0.0
    return (stop_distance / spec.tick_size) * spec.tick_value


def budget_for(spec: SymbolSpec, stop_distance: float, equity: float, *,
               target_pct: float = TARGET_RISK_PCT,
               max_pct: float = MAX_RISK_PCT) -> Budget:
    """Décide du lot pour cet actif, ou refuse l'actif en le nommant.

    Args:
        spec: spécifications du courtier (lot min/pas, valeur du tick).
        stop_distance: distance du stop en unités de prix.
        equity: equity du compte, dans sa devise.
        target_pct: risque visé, en %.
        max_pct: plafond dur, en %. Au-delà, l'actif est refusé.

    Returns:
        Budget — toujours, jamais d'exception.
    """
    if not (equity > 0 and math.isfinite(equity)):
        return Budget(spec.name, False, reason="EQUITY_INVALIDE")
    if not (0 < target_pct <= max_pct):
        return Budget(spec.name, False, reason="PARAMETRES_INCOHERENTS")

    perte = loss_per_lot(spec, stop_distance)
    if perte <= 0:
        return Budget(spec.name, False,
                      reason="SPECS_INCOMPLETES : valeur du tick indisponible")
    if spec.volume_step <= 0 or spec.volume_min <= 0:
        return Budget(spec.name, False, reason="SPECS_INCOMPLETES : pas de volume")

    cible = equity * target_pct / 100.0
    plafond = equity * max_pct / 100.0

    # Arrondi VERS LE BAS : un arrondi supérieur dépasserait le risque visé.
    brut = cible / perte
    lot = math.floor(brut / spec.volume_step + 1e-9) * spec.volume_step
    lot = round(lot, 8)

    if lot >= spec.volume_min:
        lot = min(lot, spec.volume_max)
        risque = lot * perte
        return Budget(spec.name, True, lot=lot, risk_money=round(risque, 2),
                      target_money=round(cible, 2),
                      effective_pct=round(risque / equity * 100, 3),
                      reason="lot dimensionné dans la cible")

    # Le lot idéal est sous le minimum du courtier : c'est le minimum qui commande.
    cout_min = spec.volume_min * perte
    pct_min = cout_min / equity * 100.0

    if cout_min > plafond:
        return Budget(
            spec.name, False, target_money=round(cible, 2),
            effective_pct=round(pct_min, 3),
            reason=(f"actif hors de portée : lot minimum {spec.volume_min} "
                    f"coûte {cout_min:.2f} ({pct_min:.1f} % de l'equity), "
                    f"plafond {max_pct:.1f} %"),
        )

    return Budget(
        spec.name, True, lot=spec.volume_min, risk_money=round(cout_min, 2),
        target_money=round(cible, 2), effective_pct=round(pct_min, 3),
        at_min_lot=True,
        reason=(f"lot minimum imposé : risque {pct_min:.2f} % "
                f"au lieu de {target_pct:.2f} % visés"),
    )


def _age_derniere_barre(symbole: str, timeframe: str, bars: int):
    """Heures écoulées depuis la dernière barre. None si indéterminable.

    Rend None plutôt que 0 quand on ne sait pas : on ne déclare pas vivant
    un instrument sur une absence d'information.
    """
    from datetime import datetime, timezone

    from titanium.data.mt5_vendor import get_rates

    try:
        df = get_rates(symbole, timeframe, min(bars, 8))
        derniere = df.index[-1]
        if derniere.tzinfo is None:
            derniere = derniere.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - derniere).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return None


def tradable_universe(symbols: list[str], equity: float, *,
                      timeframe: str = "M15", bars: int = 200,
                      sl_atr_k: float = 1.5,
                      target_pct: float = TARGET_RISK_PCT,
                      max_pct: float = MAX_RISK_PCT) -> dict[str, Budget]:
    """Quels actifs ce compte peut-il réellement porter, maintenant ?

    Lit MT5 (spécifications + ATR courant) et rend un `Budget` par symbole.
    C'est cette fonction qui remplace toute liste d'actifs codée en dur.
    """
    from titanium.data.mt5_vendor import ensure_symbol, get_rates
    from titanium.features import smc

    # Un seul relevé pour tout l'univers : interroger MT5 par actif
    # multiplierait les allers-retours sur le chemin d'un balayage.
    ouverts = marches_ouverts(symbols) if symbols else {}

    out: dict[str, Budget] = {}
    for sym in symbols:
        try:
            spec = ensure_symbol(sym)
            atr = smc.compute_atr(get_rates(sym, timeframe, bars))
            if atr <= 0:
                out[sym] = Budget(sym, False, reason="ATR indisponible")
                continue
            # ── Porte de coût, AVANT toute considération de taille.
            #     Un actif trop cher n'est pas « petit », il est injouable.
            # ── Le marché est-il OUVERT en ce moment ?
            #     Distinct de « l'instrument est vivant » : un actif
            #     parfaitement sain est fermé le week-end. Entrer dessus se
            #     ferait sur un prix figé, et le stop ne pourrait pas être
            #     géré avant la réouverture — deux jours plus tard.
            if ouverts is not None and not ouverts.get(sym, True):
                out[sym] = Budget(sym, False, reason="marché fermé")
                continue

            # ── L'instrument cote-t-il encore ? Un ATR calculé sur des
            #     barres de mars ne décrit aucun marché actuel.
            age_h = _age_derniere_barre(sym, timeframe, bars)
            if age_h is not None and age_h > MAX_AGE_BARRE_H:
                out[sym] = Budget(
                    sym, False,
                    reason=f"instrument éteint — dernière barre il y a "
                           f"{age_h / 24:.0f} jour(s)")
                continue

            # ── Echelle adaptative.
            #     Le week-end, l'amplitude d'une barre M15 tombe a 58 % de
            #     la semaine sur BTCUSD : le stop devient minuscule pendant
            #     que le spread reste identique. Ce n'est pas le spread qui
            #     explose, c'est le denominateur qui s'effondre — la reponse
            #     est de mesurer sur une fenetre ou la volatilite redevient
            #     significative, pas d'assouplir le plafond de cout.
            from titanium.echelle import choisir as choisir_echelle

            ch = choisir_echelle(sym, spec, plafond=MAX_COUT_SPREAD_PCT,
                                 k_stop=sl_atr_k, reference=timeframe)
            if ch is None or ch.cout > MAX_COUT_SPREAD_PCT:
                motif = (ch.motif if ch is not None
                         else "volatilite illisible a toute echelle")
                out[sym] = Budget(sym, False, reason=motif)
                continue

            distance = sl_atr_k * ch.atr
            b = budget_for(spec, distance, equity,
                           target_pct=target_pct, max_pct=max_pct)
            # `Budget` est GELE : on remplace plutot que d'assigner. Une
            # assignation levait `FrozenInstanceError`, avalee par le
            # `except` du dessous, et l'actif etait refuse avec un message
            # d'erreur Python en guise de motif — AAVE-USD etait pourtant
            # jouable en H4.
            from dataclasses import replace as _remplacer

            out[sym] = _remplacer(
                b,
                timeframe=ch.timeframe,
                cout_spread=round(ch.cout, 4),
                reason=((b.reason + " · " if b.reason else "") + ch.motif)
                if ch.elargie else b.reason)
        except Exception as exc:  # noqa: BLE001 — un actif ne casse pas le balayage
            out[sym] = Budget(sym, False, reason=f"{type(exc).__name__}: {exc}")
    return out


# ═══════════════════════ marché ouvert ou fermé ══════════════════════════

#: Retard maximal d'un actif par rapport au tick le plus frais du catalogue,
#: en minutes, avant de le déclarer fermé.
#:
#: ⚠️ On compare les actifs ENTRE EUX, jamais à l'horloge locale : MT5 rend
#: les horodatages de tick en heure SERVEUR (Axi est à GMT+3). Un calcul
#: contre `datetime.now()` donne des âges négatifs de trois heures et
#: déclarerait tout le monde frais.
#:
#: Le week-end, la crypto continue de coter pendant que le forex s'arrête :
#: le forex prend alors des heures de retard sur le tick le plus récent et
#: sort de lui-même. Aucune règle de calendrier à maintenir.
RETARD_MAX_MIN = 20.0


def marches_ouverts(symboles) -> dict:
    """Quels actifs cotent encore ? Rend ``{symbole: bool}``.

    Un actif dont le dernier tick a des heures de retard sur le marché le
    plus actif est fermé — entrer dessus se ferait sur un prix figé, et le
    stop ne pourrait pas être géré avant la réouverture.
    """
    from titanium.data.mt5_vendor import mt5_session

    derniers: dict = {}
    try:
        import MetaTrader5 as mt5  # noqa: N813

        with mt5_session():
            for s in symboles:
                try:
                    t = mt5.symbol_info_tick(s)
                    if t is not None and t.time:
                        derniers[s] = float(t.time)
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        # Sans mesure, on n'affirme pas qu'un marché est fermé.
        return {s: True for s in symboles}

    if not derniers:
        return {s: True for s in symboles}

    reference = max(derniers.values())
    limite = RETARD_MAX_MIN * 60.0
    return {s: (reference - derniers.get(s, 0.0)) <= limite for s in symboles}
