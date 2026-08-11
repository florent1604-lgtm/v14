"""Choix de l'unité de temps selon la volatilité mesurée.

Le problème
-----------
La stratégie calibre son stop sur `1.5 × ATR(M15)`. Ce réglage suppose une
volatilité de jour ouvré. Le week-end, l'amplitude d'une barre M15 tombe à
**58 % de la semaine** sur BTCUSD (mesuré sur 4 805 barres, 6 mois) — le
stop devient minuscule pendant que le spread reste identique.

Le 08/08/2026, **les trente cryptos** étaient refusées par la porte de coût.
Le bot ne tradait rien, et il avait raison de ne rien trader à cette échelle.

Le principe
-----------
Ce n'est pas le spread qui explose, c'est le DÉNOMINATEUR qui s'effondre. La
réponse n'est donc pas d'assouplir le plafond de coût — ce serait revenir
aux pertes du 07/08 — mais de **mesurer la volatilité sur une fenêtre où
elle redevient significative**.

    BTCUSD, samedi     M15  18 %      H1  4 %      H4  2 %
    ETHUSD, samedi     M15  55 %      H1 10 %      H4  5 %

Ces ratios comptent un spread complet sur l'aller-retour. L'ancienne formule
multipliait encore ce spread par deux, alors que `spec.spread × spec.point`
est déjà l'écart ask-bid complet. Elle rendait donc chaque actif deux fois
plus cher que le backtest et que le P&L réel.

On retient la plus PETITE unité de temps qui passe la porte de coût. La plus
petite, parce qu'un stop H4 immobilise un créneau pendant des heures : on
n'élargit que du strict nécessaire.

Le retour à M15 est automatique
--------------------------------
Aucun calendrier. Le choix se refait à chaque évaluation sur l'ATR courant :
dès que la volatilité remonte — reprise du dimanche soir, ou simplement le
pic récurrent de 16-18h — M15 repasse sous le plafond et redevient l'échelle
retenue. Une règle horaire se tromperait le jour où le courtier décale son
ouverture ; les prix, non.

Ce que le pic de 16-18h change, et ce qu'il ne change pas
----------------------------------------------------------
Il est réel et récurrent : ×1.19 sur BTCUSD, ×1.22 sur ETHUSD, sur les deux
actifs indépendamment. Mais il ramène l'amplitude à ~164 contre 238 en
semaine — **encore 31 % en dessous**. Il atténue l'effondrement, il ne
l'annule pas. C'est précisément l'argument pour préparer l'entrée sur une
unité large : un stop calibré en H1 traverse le creux de la mi-journée et
tient encore quand le pic arrive.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Unités candidates, de la plus fine à la plus large. On s'arrête à H4 :
#: au-delà, une position immobiliserait un créneau plus d'une journée pour
#: un compte qui n'en a que huit.
ECHELLE = ("M15", "H1", "H4")

#: Barres d'ATR par unité. Vingt partout : c'est la fenêtre sur laquelle le
#: reste du moteur raisonne, et en changer romprait la comparabilité.
BARRES_ATR = 20


@dataclass(frozen=True)
class Choix:
    """Unité retenue, et pourquoi."""

    timeframe: str
    cout: float                  # part du risque mangée par le spread
    atr: float
    elargie: bool                # a-t-on dû quitter l'unité de référence ?
    motif: str = ""

    def to_dict(self) -> dict:
        return {"timeframe": self.timeframe, "cout": round(self.cout, 4),
                "atr": self.atr, "elargie": self.elargie, "motif": self.motif}


def _cout_relatif(spread_prix: float, atr: float, k_stop: float) -> float:
    """Aller-retour de spread rapporté à la distance de stop."""
    if atr <= 0:
        return 9.9
    # `spread_prix` est déjà ask-bid. Un aller-retour paie ce spread une fois :
    # demi-spread à l'entrée + demi-spread à la sortie dans le backtest.
    return spread_prix / (k_stop * atr)


def cout_relatif_stop(spec, stop_distance: float) -> float:
    """Coût du spread rapporté au stop réellement retenu.

    Sert au second contrôle juste avant l'ordre. Le choix d'échelle utilise un
    ATR estimé ; la décision finale utilise le stop produit par RiskGate. Les
    deux doivent parler la même unité et respecter le même plafond.
    """
    try:
        spread_prix = float(getattr(spec, "spread", 0) or 0) * \
            float(getattr(spec, "point", 0) or 0)
        distance = float(stop_distance)
    except (TypeError, ValueError):
        return 9.9
    if distance <= 0:
        return 9.9
    return spread_prix / distance


def choisir(symbole: str, spec, *, plafond: float, k_stop: float = 1.5,
            reference: str = "M15", lecteur=None) -> Choix | None:
    """Plus petite unité de temps où le coût passe sous le plafond.

    Args:
        spec: spécifications du symbole (``spread``, ``point``).
        plafond: part maximale du risque que le spread peut manger.
        k_stop: multiplicateur d'ATR de la distance de stop.
        lecteur: fonction ``(symbole, tf, n) -> DataFrame``. Injectée pour
            que la décision soit testable sans terminal.

    Returns:
        Le meilleur choix, ou ``None`` si aucune unité ne passe — auquel cas
        l'actif est réellement injouable et doit être refusé, pas dégradé.
    """
    if lecteur is None:
        from titanium.data.mt5_vendor import get_rates as lecteur  # noqa: N813

    spread_prix = float(getattr(spec, "spread", 0) or 0) * \
        float(getattr(spec, "point", 0) or 0)

    depart = ECHELLE.index(reference) if reference in ECHELLE else 0
    meilleur = None
    from titanium.features.smc import compute_atr

    for tf in ECHELLE[depart:]:
        try:
            df = lecteur(symbole, tf, BARRES_ATR + 10)
            # Même définition que le moteur de features et RiskGate : true
            # range avec gaps + lissage de Wilder. Une moyenne high-low
            # sous-estimait le risque après un gap et pouvait choisir la
            # mauvaise échelle.
            atr = float(compute_atr(df))
        except Exception:  # noqa: BLE001
            continue
        if atr <= 0:
            continue

        cout = _cout_relatif(spread_prix, atr, k_stop)
        if meilleur is None or cout < meilleur.cout:
            meilleur = Choix(timeframe=tf, cout=cout, atr=atr,
                             elargie=(tf != reference))

        if cout <= plafond:
            # La PLUS PETITE qui passe : un stop plus large immobilise un
            # créneau plus longtemps, on n'élargit que du nécessaire.
            return Choix(
                timeframe=tf, cout=cout, atr=atr, elargie=(tf != reference),
                motif=("" if tf == reference else
                       f"volatilité effondrée en {reference} — "
                       f"élargi à {tf}, coût {cout:.0%}"))

    # Aucune unité ne passe : on rend le meilleur essai pour que l'appelant
    # puisse NOMMER le refus avec un chiffre, plutôt que dire « trop cher ».
    if meilleur is not None:
        return Choix(timeframe=meilleur.timeframe, cout=meilleur.cout,
                     atr=meilleur.atr, elargie=True,
                     motif=f"injouable à toute échelle — meilleur "
                           f"{meilleur.timeframe} à {meilleur.cout:.0%}")
    return None
