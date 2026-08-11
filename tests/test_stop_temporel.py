"""Le stop temporel — ce qu'il coupe, ce qu'il ne doit jamais couper.

L'HYPOTHÈSE QU'IL TESTE
-----------------------
Mesuré sur 4 actifs × 5000 barres M15 : les gagnants se résolvent en 5-7 barres
médianes avec une MFE médiane de 1.52-1.86 R ; les perdants plafonnent à
0.32-0.48 R de MFE médiane. Séparation d'un facteur 3 à 4. Si un trade n'est pas
allé chercher +0.5 R au bout de 10 barres, il ressemble statistiquement à un
perdant.

CE QUE CES TESTS PROTÈGENT
---------------------------
1. **Le défaut ne bouge rien.** `stop_temporel=None` doit rendre exactement les
   mêmes sorties qu'avant l'ajout du paramètre. Un backtest dont la baseline
   dérive ne mesure plus rien.
2. **L'ordre dans la barre.** SL et TP sont testés AVANT le stop temporel. Une
   barre qui touche le stop est une perte au stop — si le temporel passait en
   premier, il transformerait des pertes pleines en sorties tièdes et
   fabriquerait un gain qui n'existe pas.
3. **Le critère est le PIC**, pas le prix courant. « N'a pas atteint +0.5 R » se
   lit « n'y est jamais monté ».
"""

from __future__ import annotations

import pandas as pd
import pytest

from titanium.backtest import _simuler_sortie


def _df(bougies: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Construit un LTF minimal depuis une liste de (high, low, close)."""
    return pd.DataFrame(
        {"high": [b[0] for b in bougies],
         "low": [b[1] for b in bougies],
         "close": [b[2] for b in bougies],
         "open": [b[2] for b in bougies],
         "volume": [1.0] * len(bougies)},
        index=pd.date_range("2026-01-01", periods=len(bougies), freq="15min"))


def _plat(n: int, prix: float = 100.0) -> list[tuple[float, float, float]]:
    """`n` bougies rigoureusement immobiles — ni SL, ni TP, ni excursion."""
    return [(prix, prix, prix)] * n


#: Gestion neutralisée : ces tests portent sur le stop temporel, pas sur le
#: breakeven ni le trailing. Des seuils inatteignables les mettent hors jeu.
INERTE = {"breakeven_r": 99.0, "trail_start_r": 99.0, "trail_dist_r": 0.5,
          "max_barres": 200}


# ── 1. Le défaut ne change rien ──────────────────────────────────────────────

@pytest.mark.parametrize("bougies,attendu", [
    # touche le SL à la barre 3
    ([(100, 100, 100)] * 3 + [(100, 97, 98)] + _plat(5), "sl"),
    # touche le TP à la barre 2
    ([(100, 100, 100)] * 2 + [(104, 100, 103)] + _plat(5), "tp"),
    # rien ne se passe : butoir de durée
    (_plat(12), "temps"),
])
def test_defaut_none_reproduit_le_comportement_historique(bougies, attendu):
    """`stop_temporel=None` : mêmes sorties qu'avant l'ajout du paramètre."""
    ltf = _df(bougies)
    sans = _simuler_sortie(ltf, 0, 1, 100.0, 98.0, 104.0,
                           **{**INERTE, "max_barres": 8})
    assert sans[2] == attendu
    # L'argument explicitement None doit être indiscernable de son absence.
    avec_none = _simuler_sortie(ltf, 0, 1, 100.0, 98.0, 104.0,
                                **{**INERTE, "max_barres": 8},
                                stop_temporel=None)
    assert avec_none == sans


def test_rejouer_expose_le_parametre_et_le_desactive_par_defaut():
    """La signature de `rejouer` porte le paramètre, à None."""
    import inspect

    from titanium.backtest import rejouer

    p = inspect.signature(rejouer).parameters["stop_temporel"]
    assert p.default is None
    assert p.kind is inspect.Parameter.KEYWORD_ONLY


# ── 2. L'ordre dans la barre : SL et TP d'abord ──────────────────────────────

def test_sl_prime_sur_le_stop_temporel_dans_la_meme_barre():
    """Une barre qui touche le stop est une perte au stop, pas une sortie tiède.

    C'est LE piège du stop temporel : s'il passait en premier, il sortirait à la
    clôture (ici 98.5, soit −0.75 R) au lieu du stop (98.0, soit −1 R). Sur des
    centaines de trades, ce demi-R inventé suffirait à faire passer une règle
    perdante pour gagnante.
    """
    ltf = _df(_plat(3) + [(100, 97.5, 98.5)] + _plat(6))
    i, prix, motif, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(3, 0.5))
    assert motif == "sl"
    assert prix == pytest.approx(98.0)
    assert i == 3


def test_tp_prime_sur_le_stop_temporel_dans_la_meme_barre():
    """Symétrique : un objectif touché à la barre butoir reste un `tp`."""
    ltf = _df(_plat(3) + [(104.5, 100, 100.2)] + _plat(6))
    # pic n'atteint pas le seuil à la clôture, mais le TP a été touché.
    _, prix, motif, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(4, 3.0))
    assert motif == "tp"
    assert prix == pytest.approx(104.0)


# ── 3. Le déclenchement lui-même ─────────────────────────────────────────────

def test_sortie_temporelle_a_la_barre_exacte_et_au_marche():
    """Barre N atteinte, pic sous le seuil ⇒ sortie à la clôture de la barre N."""
    ltf = _df(_plat(4) + [(100.2, 100, 100.1)] + _plat(8))
    i, prix, motif, _, pic = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(5, 0.5))
    assert motif == "stop_temporel"
    assert i == 5                       # cinquième barre après l'entrée
    assert prix == pytest.approx(100.0)  # clôture de cette barre, au marché
    assert pic < 0.5


def test_pas_de_sortie_avant_la_barre_butoir():
    """Barre N−1 : le trade est encore en sursis, quel que soit son pic."""
    ltf = _df(_plat(20))
    i, _, motif, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(10, 0.5))
    assert motif == "stop_temporel"
    assert i == 10                       # ni 9, ni 11


def test_le_pic_epargne_un_trade_qui_est_redescendu():
    """Monté à +0.9 R puis revenu à zéro : le temporel ne le coupe pas.

    Le critère est la meilleure excursion, pas le prix courant. Un trade qui a
    prouvé qu'il pouvait courir relève du trailing, pas du couperet.
    """
    ltf = _df([(100, 100, 100), (101.8, 100, 100.0)] + _plat(20))
    _, _, motif, _, pic = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(5, 0.5))
    assert pic == pytest.approx(0.9)
    assert motif == "temps"              # survit jusqu'au butoir de durée


def test_seuil_zero_ne_coupe_que_ce_qui_n_a_jamais_ete_favorable():
    """`seuil_r=0.0` : le trade doit être monté au-dessus de l'entrée.

    Ce cas est le piège d'implémentation : la MFE rapportée est plancherée à 0
    (`pic = max(pic, fav)` partant de 0.0), donc « pic < 0.0 » serait
    structurellement impossible et le seuil 0.0 aurait été un no-op silencieux
    — cinq combinaisons du balayage rendues inertes sans que rien ne le signale.
    D'où le pic non plancheré dédié au critère temporel.
    """
    jamais = _df([(99.9, 99.5, 99.8)] * 20)
    _, _, motif, _, _ = _simuler_sortie(
        jamais, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(5, 0.0))
    assert motif == "stop_temporel"

    # La bougie favorable est en position 1 : l'entrée est la clôture de la
    # bougie 0, que la boucle ne rescanne jamais.
    un_peu = _df([(99.9, 99.5, 99.8), (100.1, 99.5, 99.8)]
                 + [(99.9, 99.5, 99.8)] * 20)
    _, _, motif, _, _ = _simuler_sortie(
        un_peu, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(5, 0.0))
    assert motif == "temps"


def test_la_mfe_rapportee_reste_plancheree_a_zero():
    """Le pic non plancheré ne doit PAS fuiter dans la MFE rapportée.

    `mfe_r` est déjà consommée par l'analyse discriminante et les rapports
    existants : la voir devenir négative changerait des chiffres publiés.
    """
    jamais = _df([(99.9, 99.5, 99.8)] * 20)
    for st in (None, (5, 0.0)):
        _, _, _, _, mfe = _simuler_sortie(
            jamais, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=st)
        assert mfe == 0.0


def test_cote_vendeur_symetrique():
    """Un short se juge sur la même mécanique, signe inversé."""
    ltf = _df(_plat(10))
    i, prix, motif, _, _ = _simuler_sortie(
        ltf, 0, -1, 100.0, 102.0, 96.0, **INERTE, stop_temporel=(4, 0.5))
    assert motif == "stop_temporel"
    assert i == 4
    assert prix == pytest.approx(100.0)


def test_short_le_sl_prime_aussi():
    """Le short ne bénéficie d'aucune indulgence d'ordre non plus."""
    ltf = _df(_plat(3) + [(102.5, 100, 101.5)] + _plat(6))
    _, prix, motif, _, _ = _simuler_sortie(
        ltf, 0, -1, 100.0, 102.0, 96.0, **INERTE, stop_temporel=(3, 0.5))
    assert motif == "sl"
    assert prix == pytest.approx(102.0)


# ── 4. Interaction avec la gestion dynamique ─────────────────────────────────

def test_un_trade_passe_au_trailing_echappe_au_temporel():
    """Pic ≥ trail_start ⇒ pic ≥ seuil aussi : le temporel n'a plus rien à dire.

    Le contraire signalerait un bug d'ordre : on couperait au marché un trade
    dont le stop est déjà remonté au-dessus de l'entrée.
    """
    ltf = _df([(100, 100, 100), (102.6, 100, 102.4)] + _plat(20))
    _, _, motif, _, pic = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 110.0,
        breakeven_r=0.8, trail_start_r=1.2, trail_dist_r=0.8, max_barres=200,
        stop_temporel=(5, 0.75))
    assert pic >= 1.2
    assert motif in {"trailing", "temps"}
    assert motif != "stop_temporel"


def test_le_temporel_borne_la_duree_sous_max_barres():
    """Avec le temporel actif, aucun trade mou ne va jusqu'au butoir de 200."""
    ltf = _df(_plat(300))
    i_sans, _, motif_sans, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE)
    i_avec, _, motif_avec, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(10, 0.5))
    assert (i_sans, motif_sans) == (200, "temps")
    assert (i_avec, motif_avec) == (10, "stop_temporel")


# ── 5. Robustesse ────────────────────────────────────────────────────────────

def test_r_nul_reste_prioritaire():
    """Entrée = stop : le trade est invalide avant toute question de durée."""
    ltf = _df(_plat(20))
    out = _simuler_sortie(ltf, 0, 1, 100.0, 100.0, 104.0,
                          **INERTE, stop_temporel=(5, 0.5))
    assert out[2] == "r_nul"


def test_historique_trop_court_ne_leve_pas():
    """Moins de barres que le butoir temporel : sortie normale en fin de série."""
    ltf = _df(_plat(4))
    i, _, motif, _, _ = _simuler_sortie(
        ltf, 0, 1, 100.0, 98.0, 104.0, **INERTE, stop_temporel=(50, 0.5))
    assert i == 3
    assert motif == "temps"


def test_liste_acceptee_comme_couple():
    """`[10, 0.5]` vaut `(10, 0.5)` — un couple lu d'un JSON reste utilisable."""
    ltf = _df(_plat(20))
    a = _simuler_sortie(ltf, 0, 1, 100.0, 98.0, 104.0,
                        **INERTE, stop_temporel=(6, 0.5))
    b = _simuler_sortie(ltf, 0, 1, 100.0, 98.0, 104.0,
                        **INERTE, stop_temporel=[6, 0.5])
    assert a == b
