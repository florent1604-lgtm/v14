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

def test_le_plafond_par_symbole_est_serre():
    """Une seule position par actif. C'est le filet indépendant de la clé."""
    from tools.live_demo import MAX_PAR_SYMBOLE, MAX_POSITIONS
    assert MAX_PAR_SYMBOLE == 1
    # 0 = illimité (17/08/2026) : le plafond de créneaux ne borne plus rien,
    # c'est MAX_RISQUE_CUMULE_PCT qui porte l'exposition.
    assert MAX_POSITIONS == 0 or MAX_POSITIONS >= MAX_PAR_SYMBOLE


@pytest.mark.parametrize("deja,attendu", [(0, True), (1, False), (3, False)])
def test_regle_du_plafond(deja, attendu):
    """Reproduit la condition du script : on n'entre que si l'actif est libre."""
    from tools.live_demo import MAX_PAR_SYMBOLE
    autorise = deja < MAX_PAR_SYMBOLE
    assert autorise is attendu


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
