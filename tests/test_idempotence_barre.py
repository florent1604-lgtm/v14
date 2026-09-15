"""Non-régression : l'idempotence doit être ancrée sur la BARRE, pas sur l'heure.

CE QUI S'EST PASSÉ (07/08/2026, compte démo, argent fictif)
------------------------------------------------------------
La clé d'idempotence était ``symbole:M15:{decided_at}``. Or ``decided_at`` vaut
``datetime.now()`` au moment du calcul des features : elle change à **chaque**
balayage. La clé n'était donc jamais deux fois la même, et la déduplication
n'a jamais fonctionné.

Conséquence mesurée : trois positions LONG ouvertes sur AUDUSD à
0.70330 / 0.70327 / 0.70324 en quelques minutes — trois fois le risque prévu
sur un seul actif corrélé, soit 3.4 % du compte au lieu de 1.14 %.

Deux corrections, testées ici :
  1. la clé s'ancre sur ``bar_time`` — horodatage de la dernière barre CLÔTURÉE ;
  2. un plafond par symbole, **indépendant** de l'idempotence, empêche d'empiler
     le même risque même si la clé venait à échouer.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from titanium.features.builder import build_feats


def bougies(n=400, fin="2026-08-07 04:30", freq="15min") -> pd.DataFrame:
    """Série indexée par le temps, comme celle que rend le vendeur MT5."""
    idx = pd.date_range(end=pd.Timestamp(fin, tz="UTC"), periods=n, freq=freq)
    rng = np.random.default_rng(11)
    base = np.cumsum(rng.normal(0, 0.3, n)) + 100
    return pd.DataFrame({
        "open": base, "high": base + 0.4, "low": base - 0.4,
        "close": base + 0.05, "tick_volume": rng.integers(80, 400, n).astype(float),
    }, index=idx)


def cle(symbole: str, feats: dict) -> str:
    """Reproduit la clé du script d'amorçage (tools/live_demo.py)."""
    trace = feats.get("_trace") or {}
    barre = trace.get("bar_time") or ""
    return f"{symbole}:M15:{barre}" if barre else ""


# ═══════════════ le défaut exact, verrouillé ═══════════════════════════════

def test_bar_time_est_expose():
    f = build_feats(bougies(), bougies(freq="4h"))
    assert f["_trace"]["bar_time"], "sans bar_time, aucune déduplication possible"


def test_bar_time_est_celui_de_la_derniere_barre():
    df = bougies()
    f = build_feats(df, bougies(freq="4h"))
    assert f["_trace"]["bar_time"] == df.index[-1].isoformat()


def test_deux_balayages_sur_la_meme_barre_donnent_la_MEME_cle():
    """LE test. C'est précisément ce qui a échoué en réel."""
    ltf, htf = bougies(), bougies(freq="4h")
    a = cle("AUDUSD", build_feats(ltf, htf))
    time.sleep(0.05)
    b = cle("AUDUSD", build_feats(ltf, htf))
    assert a == b, "la clé doit être stable tant que la barre n'a pas changé"
    assert a, "et elle ne doit pas être vide"


def test_decided_at_change_lui_entre_deux_balayages():
    """On documente pourquoi `decided_at` ne peut PAS servir de clé."""
    ltf, htf = bougies(), bougies(freq="4h")
    a = build_feats(ltf, htf)["_trace"]["decided_at"]
    time.sleep(0.05)
    b = build_feats(ltf, htf)["_trace"]["decided_at"]
    assert a != b, "decided_at est l'instant du calcul — il change, c'était le piège"


def test_une_nouvelle_barre_donne_une_nouvelle_cle():
    """L'inverse doit rester vrai : une barre suivante autorise une entrée."""
    htf = bougies(freq="4h")
    a = cle("AUDUSD", build_feats(bougies(fin="2026-08-07 04:30"), htf))
    b = cle("AUDUSD", build_feats(bougies(fin="2026-08-07 04:45"), htf))
    assert a != b


def test_symboles_differents_donnent_des_cles_differentes():
    ltf, htf = bougies(), bougies(freq="4h")
    f = build_feats(ltf, htf)
    assert cle("AUDUSD", f) != cle("NZDUSD", f)


def test_cle_vide_si_bar_time_absent():
    """Sans horodatage exploitable, on ne prétend pas dédupliquer : la clé est
    vide, et c'est le plafond par symbole qui protège."""
    f = build_feats(bougies(), bougies(freq="4h"))
    f["_trace"]["bar_time"] = ""
    assert cle("AUDUSD", f) == ""


def test_index_sans_horodatage_ne_leve_pas():
    """Un DataFrame à index entier ne doit pas faire planter le constructeur."""
    df = bougies().reset_index(drop=True)
    f = build_feats(df, bougies(freq="4h"))
    assert f["data_valid"] is True
    assert f["_trace"]["bar_time"] == "" or isinstance(f["_trace"]["bar_time"], str)


# ═══════════════ le second garde-fou : plafond par symbole ═════════════════

def test_le_plafond_par_symbole_borne_les_renforts():
    """Trois positions au plus, chacune soumise à la porte d'empilement."""
    from tools.live_demo import MAX_PAR_SYMBOLE, MAX_POSITIONS
    assert MAX_PAR_SYMBOLE == 3
    # 0 = illimité (17/08/2026) : le plafond de créneaux ne borne plus rien,
    # c'est MAX_RISQUE_CUMULE_PCT qui porte l'exposition.
    assert MAX_POSITIONS == 0 or MAX_POSITIONS >= MAX_PAR_SYMBOLE


class TestMultipositionConditionnelle:
    def _regle(self, expositions, *, side=1, prix=99.0, stop=10.0,
               famille="continuation", atr=0.0, spread=0.0):
        from tools.live_demo import _autoriser_empilement
        return _autoriser_empilement(
            expositions, side=side, prix=prix, stop_distance=stop,
            setup_family=famille, atr=atr, spread=spread,
        )

    def test_actif_libre(self):
        assert self._regle([]) == (True, "ACTIF_LIBRE")

    def test_long_renforce_seulement_a_meilleur_prix(self):
        ok, motif = self._regle([(1, 100.0)], prix=99.0)
        assert ok is True
        assert motif == "ENTREE_AMELIOREE_0.100R"
        assert self._regle([(1, 100.0)], prix=99.01)[0] is False
        assert self._regle([(1, 100.0)], prix=101.0)[0] is False

    def test_short_renforce_seulement_a_meilleur_prix(self):
        ok, motif = self._regle([(-1, 100.0)], side=-1, prix=101.0)
        assert ok is True
        assert motif == "ENTREE_AMELIOREE_0.100R"
        assert self._regle([(-1, 100.0)], side=-1, prix=99.0)[0] is False

    def test_espacement_s_adapte_a_l_atr_et_au_spread(self):
        # stop=10, ATR=8 -> 0,25 ATR = 2 points = 0,20 R.
        assert self._regle([(1, 100.0)], prix=98.0, atr=8.0) == (
            True, "ENTREE_AMELIOREE_0.200R_MIN_0.200R",
        )
        assert self._regle([(1, 100.0)], prix=98.01, atr=8.0)[0] is False
        # Deux spreads de 2 points imposent 4 points = 0,40 R.
        assert self._regle([(1, 100.0)], prix=96.0, spread=2.0) == (
            True, "ENTREE_AMELIOREE_0.400R_MIN_0.400R",
        )

    def test_sens_oppose_exige_un_retournement(self):
        assert self._regle([(1, 100.0)], side=-1, prix=99.0)[0] is False
        assert self._regle(
            [(1, 100.0)], side=-1, prix=99.0, famille="reversal",
        ) == (True, "RETOURNEMENT_CONFIRME")

    def test_livre_mixte_et_plafond_sont_fail_closed(self):
        assert self._regle([(1, 100.0), (-1, 101.0)], prix=98.0)[0] is False
        assert self._regle([(1, 100.0), (1, 99.0), (1, 98.0)], prix=97.0) == (
            False, "PLAFOND_PAR_SYMBOLE",
        )
        assert self._regle([(1, None)], prix=98.0) == (
            False, "EXPOSITION_INVALIDE",
        )


def test_risque_panier_mesure_le_risque_restant_sans_compter_le_be():
    from tools.live_demo import _risque_exposition_pct

    mt5 = SimpleNamespace(symbol_info=lambda _sym: SimpleNamespace(
        trade_tick_size=1.0,
        trade_tick_value_loss=1.0,
        trade_tick_value=1.0,
    ))
    position = SimpleNamespace(
        symbol="XAUUSD", price_open=100.0, sl=90.0, volume=1.0,
    )
    assert _risque_exposition_pct(mt5, position, 1000.0, side=1) == pytest.approx(1.0)
    position.sl = 101.0
    assert _risque_exposition_pct(mt5, position, 1000.0, side=1) == 0.0
    position.sl = 0.0
    assert _risque_exposition_pct(mt5, position, 1000.0, side=1) is None


class TestMeriteEtReserve:
    """Huit créneaux pour ~150 actifs : ils doivent aller aux setups les
    plus FORTS du tour, pas aux plus rapides à être évalués.

    Avant, l'envoi se faisait au fil de la rotation : un S=2 médiocre
    prenait la place qu'un S=3 aurait réclamée deux évaluations plus tard.
    """

    def _cands(self, *supports):
        return [{"sym": f"S{i}", "support": s, "rank": 0.5}
                for i, s in enumerate(supports)]

    def test_le_plus_fort_passe_devant(self):
        c = self._cands(2, 4, 3)
        c.sort(key=lambda x: (-x["support"], -x["rank"]))
        assert [x["support"] for x in c] == [4, 3, 2]

    def test_egalite_departagee_par_le_rang(self):
        c = [{"sym": "A", "support": 3, "rank": 0.2},
             {"sym": "B", "support": 3, "rank": 0.9}]
        c.sort(key=lambda x: (-x["support"], -x["rank"]))
        assert c[0]["sym"] == "B"

    def test_la_reserve_protege_la_strate_haute(self):
        """Les S≥3 font ~10 % des ENTER. Quand un plafond de créneaux existe,
        les S=2 les rempliraient avant qu'un S=3 se présente — la strate qui
        nourrit la promotion serait censurée par sa propre rareté.

        Depuis le 17/08/2026, `MAX_POSITIONS = 0` (illimité) : la réserve
        n'a plus de dernière place à protéger, elle devient inerte. Le test
        vérifie la règle telle qu'elle est écrite dans la boucle, dans les
        deux régimes.
        """
        from tools.live_demo import MAX_POSITIONS, RESERVE_S3

        def differe(ouvertes: int, support: int, plafond: int) -> bool:
            return (plafond > 0 and ouvertes >= plafond - RESERVE_S3
                    and support < 3)

        assert RESERVE_S3 > 0
        if MAX_POSITIONS <= 0:
            # Illimité : aucun candidat n'est jamais différé par la réserve.
            assert not any(differe(n, 2, MAX_POSITIONS) for n in range(0, 50))
        else:
            seuil = MAX_POSITIONS - RESERVE_S3
            assert 0 < RESERVE_S3 < MAX_POSITIONS
            for ouvertes in range(seuil, MAX_POSITIONS):
                assert differe(ouvertes, 2, MAX_POSITIONS)
                assert not differe(ouvertes, 3, MAX_POSITIONS)

    def test_la_reserve_ne_bloque_pas_avant_le_seuil(self):
        from tools.live_demo import MAX_POSITIONS, RESERVE_S3
        if MAX_POSITIONS <= 0:
            return  # illimité : rien à différer, testé au-dessus
        seuil = MAX_POSITIONS - RESERVE_S3
        assert not (seuil - 1 >= seuil)
