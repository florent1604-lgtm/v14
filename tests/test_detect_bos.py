"""Rupture de structure (BOS) — détecteur pur, sans appelant.

La distinction qui fait tout le module : une MÈCHE au-delà d'un swing est un
balayage de liquidité, une CLÔTURE au-delà est une rupture de structure. V14
détecte déjà le premier (`detect_liquidity_sweep`) ; les confondre reviendrait
à appeler « cassure » ce que la méthode de Florent traite comme un piège.

Second invariant : le niveau rompu doit être un vrai point de swing — un
extremum local entouré de barres des deux côtés — et non le maximum de la
fenêtre. Avec un maximum de fenêtre, tout nouveau plus-haut devient une
« rupture », ce qui vide le signal de son sens.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from titanium.features.smc import BUY, SELL, detect_bos


def _df(highs, lows, closes=None):
    highs = [float(x) for x in highs]
    lows = [float(x) for x in lows]
    closes = [float(x) for x in (closes if closes is not None else lows)]
    return pd.DataFrame({
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
    })


def _plateau(n, base=100.0):
    """Barres plates : aucun swing marqué, sert de fond neutre."""
    return [base] * n, [base - 1.0] * n, [base - 0.5] * n


# ───────────────────────────────── rupture haussière

def test_cloture_au_dessus_du_dernier_swing_haut_est_une_rupture():
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 110.0, 108.0, 109.0        # swing haut isolé
    h[11], b[11], c[11] = 112.0, 109.0, 111.0     # clôture au-dessus
    rompu, niveau = detect_bos(_df(h, b, c), BUY)
    assert rompu is True
    assert niveau == 110.0, "le niveau rendu doit être le swing rompu"


def test_meche_au_dessus_sans_cloture_n_est_pas_une_rupture():
    """C'est un balayage de liquidité, pas une cassure — le piège même que la
    méthode cherche à éviter."""
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 110.0, 108.0, 109.0
    h[11], b[11], c[11] = 115.0, 109.0, 109.5     # mèche au-dessus, clôture en dessous
    rompu, _ = detect_bos(_df(h, b, c), BUY)
    assert rompu is False


def test_cloture_exactement_sur_le_niveau_ne_rompt_pas():
    """Une égalité n'est pas un dépassement. Sans cette borne, un marché figé
    sur un niveau produirait une rupture à chaque barre."""
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 110.0, 108.0, 109.0
    h[11], b[11], c[11] = 110.0, 109.0, 110.0
    rompu, _ = detect_bos(_df(h, b, c), BUY)
    assert rompu is False


# ───────────────────────────────── rupture baissière

def test_cloture_sous_le_dernier_swing_bas_est_une_rupture():
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 99.0, 90.0, 91.0           # swing bas isolé
    h[11], b[11], c[11] = 92.0, 88.0, 89.0        # clôture en dessous
    rompu, niveau = detect_bos(_df(h, b, c), SELL)
    assert rompu is True
    assert niveau == 90.0


def test_une_rupture_haussiere_n_est_pas_une_rupture_baissiere():
    """Le sens demandé est respecté : un détecteur qui rendrait True dans les
    deux sens serait un détecteur de volatilité, pas de structure."""
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 110.0, 108.0, 109.0
    h[11], b[11], c[11] = 112.0, 109.0, 111.0
    assert detect_bos(_df(h, b, c), SELL)[0] is False


# ───────────────────────────────── causalité

def test_une_cassure_anterieure_au_swing_ne_compte_pas():
    """La rupture doit venir APRÈS le swing. Compter une barre antérieure
    ferait lire l'avenir à l'envers et inventerait des ruptures."""
    h, b, c = _plateau(14)
    h[2], b[2], c[2] = 130.0, 120.0, 129.0        # sommet ancien, non rompu ensuite
    h[9], b[9], c[9] = 110.0, 108.0, 109.0        # swing plus récent
    rompu, niveau = detect_bos(_df(h, b, c), BUY)
    assert rompu is False, "aucune barre postérieure au swert ne clôture au-dessus"
    assert niveau == 110.0, "le niveau de reference reste le swing le plus recent"


def test_seul_le_swing_le_plus_recent_sert_de_reference():
    h, b, c = _plateau(18)
    h[4], b[4], c[4] = 120.0, 118.0, 119.0        # swing ancien, plus haut
    h[10], b[10], c[10] = 110.0, 108.0, 109.0     # swing recent, plus bas
    h[17], b[17], c[17] = 112.0, 109.0, 111.0     # rompt le recent, pas l ancien
    rompu, niveau = detect_bos(_df(h, b, c), BUY)
    assert rompu is True
    assert niveau == 110.0


# ───────────────────────────────── robustesse

def test_donnees_insuffisantes_rendent_faux():
    for n in (0, 1, 5):
        h, b, c = _plateau(n)
        assert detect_bos(_df(h, b, c), BUY) == (False, 0.0)


def test_dataframe_absent_rend_faux():
    assert detect_bos(None, BUY) == (False, 0.0)


def test_colonnes_manquantes_rendent_faux():
    df = pd.DataFrame({"high": [1.0] * 12, "low": [0.5] * 12})   # pas de close
    assert detect_bos(df, BUY) == (False, 0.0)


def test_valeurs_non_finies_rendent_faux():
    """Une donnée corrompue doit taire le signal, jamais en inventer un."""
    h, b, c = _plateau(12)
    h[5], b[5], c[5] = 110.0, 108.0, 109.0
    h[11], b[11], c[11] = 112.0, 109.0, 111.0
    h[7] = np.nan
    assert detect_bos(_df(h, b, c), BUY) == (False, 0.0)


def test_un_plateau_produit_un_swing_degenere_sans_rupture():
    """Piège documenté plutôt que corrigé.

    `_swings` retient un extremum par ``==`` : sur une série parfaitement
    plate, CHAQUE barre intérieure est à la fois swing haut et swing bas. Le
    détecteur rend donc un niveau — le plateau lui-même — et non ``0.0``.

    Ce n'est pas un défaut de `detect_bos` : c'est la définition partagée avec
    la zone OTE, et la changer ici ferait diverger les deux. Ce qui compte est
    qu'aucune rupture ne soit inventée, puisque aucune clôture ne dépasse le
    plateau. Le cas se rencontre en vrai sur les instruments à faible
    résolution de tick, où des plus-hauts identiques se répètent.
    """
    h, b, c = _plateau(20)
    rompu, niveau = detect_bos(_df(h, b, c), BUY)
    assert rompu is False
    assert niveau == 100.0


def test_la_fenetre_borne_la_recherche():
    """`lookback` doit vraiment restreindre la recherche du swing.

    Construction : un sommet marque en 10, un creux immediat, puis une hausse
    STRICTEMENT monotone. Une serie monotone ne contient aucun swing — chaque
    barre est depassee par la suivante — donc une fenetre courte qui ne couvre
    que la hausse ne trouve aucune reference.

    Deux premisses fausses ont ete corrigees pour arriver ici : un fond plat
    produit des swings degeneres partout, et un sommet ANCIEN n'est de toute
    facon jamais la reference puisque seul le swing le plus recent compte. Sans
    queue monotone, ce test passait sans rien prouver.
    """
    h = [90.0 + i for i in range(10)] + [110.0, 105.0, 106.0, 107.0]
    h += [108.0 + i for i in range(26)]
    b = [x - 2.0 for x in h]
    c = [x - 1.0 for x in h]
    df = _df(h, b, c)

    rompu_large, niveau_large = detect_bos(df, BUY, lookback=40)
    assert niveau_large == 110.0, "sur la fenetre large, le sommet en 10 est la reference"
    assert rompu_large is True, "la hausse posterieure cloture au-dessus de 110"

    assert detect_bos(df, BUY, lookback=10) == (False, 0.0), (
        "fenetre courte = hausse monotone seule = aucun swing, donc aucune reference"
    )
