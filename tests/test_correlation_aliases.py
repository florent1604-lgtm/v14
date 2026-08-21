"""Garde-fous de concentration pour les alias fortement correles.

Le clustering dynamique ne suffit pas : un cache peut ne contenir qu'un des
deux noms d'un meme sous-jacent.  Le plafond statique des alias doit donc etre
independant du bloc dynamique et toute mesure de positions douteuse doit
refuser l'entree.
"""

from __future__ import annotations

import pytest

from titanium.correlation import (
    ALIAS_MAP_VERSION,
    ALIAS_TO_UNDERLYING,
    Grappes,
    canonicaliser_sous_jacent,
    mesurer_exposition,
    place_disponible,
)


class _Pos:
    def __init__(self, symbol, *, sl=1.099, ouvert=1.100, vol=1.0):
        self.symbol = symbol
        self.sl = sl
        self.price_open = ouvert
        self.volume = vol


class _Spec:
    trade_tick_size = 0.00001
    trade_tick_value = 1.0


_DEFAULT_SPEC = _Spec()


def _mt5(positions, *, spec=_DEFAULT_SPEC):
    return type("M", (), {
        "positions_get": staticmethod(lambda *a, **k: positions),
        "symbol_info": staticmethod(lambda _s: spec),
    })()


def _grappes_separees():
    """Cache volontairement incomplet : les alias ne partagent pas de bloc."""
    return Grappes(
        par_actif={"NAS100.FS": "g1", "USTECH": "g2", "XAUUSD": "g3"},
        membres={"g1": ["NAS100.FS"], "g2": ["USTECH"], "g3": ["XAUUSD"]},
    )


def test_mapping_versionne_ne_fige_que_les_doublons_de_contrat_demontrés():
    assert ALIAS_MAP_VERSION == "h1-contract-aliases-2026-08-20-v1"
    assert len(ALIAS_TO_UNDERLYING) == 18
    assert canonicaliser_sous_jacent("nas100.fs") == "US_NASDAQ_100"
    assert canonicaliser_sous_jacent("USTECH") == "US_NASDAQ_100"
    assert canonicaliser_sous_jacent("BRENT.fs") == "BRENT_CRUDE"
    assert canonicaliser_sous_jacent("UKOIL") == "BRENT_CRUDE"
    assert canonicaliser_sous_jacent("USOIL") == "WTI_CRUDE"
    assert canonicaliser_sous_jacent("WTI.fs") == "WTI_CRUDE"


def test_correlation_forte_sans_doublon_reste_au_cluster_dynamique():
    assert canonicaliser_sous_jacent("US500") != canonicaliser_sous_jacent("USTECH")
    assert canonicaliser_sous_jacent("BTC-JPY") != canonicaliser_sous_jacent("BTCUSD")
    # USDJPC ne devient un alias que sous preuve de fraicheur ; cette porte ne
    # recoit pas encore cette preuve, donc elle reste conservatrice.
    assert canonicaliser_sous_jacent("USDJPC") != canonicaliser_sous_jacent("USDJPY")


def test_un_symbole_hors_mapping_reste_un_sous_jacent_distinct():
    assert canonicaliser_sous_jacent(" EURJPY ") == "EURJPY"
    assert canonicaliser_sous_jacent("EURJPY") != canonicaliser_sous_jacent("NZDJPY")


def test_aliases_partagent_un_plafond_meme_si_cache_les_separe():
    # 1,6 % deja portes sur NAS100.fs ; ajouter 0,5 % via USTECH depasse 2 %.
    pos = _Pos("NAS100.fs", vol=1.6)
    ok, motif = place_disponible(
        "USTECH", 0.5, _mt5([pos]), _grappes_separees(), 10_000.0,
    )
    assert not ok
    assert "sous-jacent US_NASDAQ_100" in motif
    assert "1.60 %" in motif


def test_plafond_de_bloc_reste_independant_du_sous_jacent():
    grappes = Grappes(
        par_actif={"EURJPY": "gJPY", "NZDJPY": "gJPY"},
        membres={"gJPY": ["EURJPY", "NZDJPY"]},
    )
    ok, motif = place_disponible(
        "NZDJPY", 0.5, _mt5([_Pos("EURJPY", vol=1.6)]), grappes, 10_000.0,
    )
    assert not ok
    assert "grappe gJPY" in motif


@pytest.mark.parametrize("equity", [0.0, -1.0, float("nan")])
def test_equite_invalide_est_fail_closed(equity):
    mesure = mesurer_exposition(_mt5([]), _grappes_separees(), equity)
    assert not mesure.valide
    ok, motif = place_disponible("USTECH", 0.5, _mt5([]),
                                 _grappes_separees(), equity)
    assert not ok
    assert "exposition inconnue" in motif


def test_positions_none_est_une_erreur_pas_un_portefeuille_vide():
    mesure = mesurer_exposition(_mt5(None), _grappes_separees(), 10_000.0)
    assert not mesure.valide
    ok, motif = place_disponible("USTECH", 0.5, _mt5(None),
                                 _grappes_separees(), 10_000.0)
    assert not ok
    assert "positions indisponibles" in motif


def test_exception_mt5_est_fail_closed():
    mt5 = type("M", (), {
        "positions_get": staticmethod(
            lambda: (_ for _ in ()).throw(RuntimeError("terminal muet"))
        ),
    })()
    mesure = mesurer_exposition(mt5, _grappes_separees(), 10_000.0)
    assert not mesure.valide
    ok, motif = place_disponible("USTECH", 0.5, mt5,
                                 _grappes_separees(), 10_000.0)
    assert not ok
    assert "terminal muet" in motif


def test_conteneur_de_positions_illisible_est_fail_closed():
    mt5 = _mt5(object())
    mesure = mesurer_exposition(mt5, _grappes_separees(), 10_000.0)
    assert not mesure.valide
    ok, motif = place_disponible(
        "USTECH", 0.5, mt5, _grappes_separees(), 10_000.0,
    )
    assert not ok
    assert "positions illisibles" in motif


def test_position_malformee_est_fail_closed():
    class _PositionMalformee:
        @property
        def symbol(self):
            raise RuntimeError("ticket corrompu")

    mesure = mesurer_exposition(
        _mt5([_PositionMalformee()]), _grappes_separees(), 10_000.0,
    )
    assert not mesure.valide
    assert "ticket corrompu" in mesure.raison


def test_specification_manquante_est_fail_closed():
    mesure = mesurer_exposition(
        _mt5([_Pos("NAS100.fs")], spec=None), _grappes_separees(), 10_000.0,
    )
    assert not mesure.valide
    ok, motif = place_disponible(
        "USTECH", 0.5, _mt5([_Pos("NAS100.fs")], spec=None),
        _grappes_separees(), 10_000.0,
    )
    assert not ok
    assert "specification indisponible" in motif


def test_risque_propose_invalide_est_fail_closed():
    ok, motif = place_disponible(
        "USTECH", float("nan"), _mt5([]), _grappes_separees(), 10_000.0,
    )
    assert not ok
    assert "risque propose invalide" in motif
