"""Mise à plat hors crypto avant la fermeture hebdomadaire.

La fenêtre enjambe la fin de semaine : tous les tests d'appartenance sont
écrits en heure SERVEUR, jamais en heure locale — c'est l'erreur que le
module existe pour empêcher.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from titanium.execution.weekend_flat import (
    DECALAGE_PLAUSIBLE_MAX_S,
    WeekendFlatParams,
    dans_fenetre,
    decide_weekend_flat,
    heure_serveur_mt5,
)


def _serveur(jour: str, heure: int, minute: int = 0) -> datetime:
    """Instant serveur d'une semaine de référence (lundi 07/09/2026)."""
    jours = {"lundi": 7, "mardi": 8, "mercredi": 9, "jeudi": 10,
             "vendredi": 11, "samedi": 12, "dimanche": 13}
    return datetime(2026, 9, jours[jour], heure, minute, tzinfo=timezone.utc)


class _Tick:
    def __init__(self, time: float) -> None:
        self.time = time


class _Mt5:
    """Doublure : rend un tick par symbole, ou lève si on le lui demande."""

    def __init__(self, ticks: dict, leve: set | None = None) -> None:
        self._ticks = ticks
        self._leve = leve or set()

    def symbol_info_tick(self, symbole):
        if symbole in self._leve:
            raise RuntimeError("terminal indisponible")
        epoch = self._ticks.get(symbole)
        return _Tick(epoch) if epoch is not None else None


# ── La fenêtre ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("moment", [
    ("vendredi", 22, 0),     # borne de début, incluse
    ("vendredi", 23, 59),
    ("samedi", 12, 0),
    ("dimanche", 23, 30),    # réouverture passée, on attend lundi 01:00
])
def test_la_fenetre_couvre_du_vendredi_soir_au_lundi_matin(moment):
    assert dans_fenetre(_serveur(*moment)) is True


@pytest.mark.parametrize("moment", [
    ("vendredi", 21, 59),    # une minute avant l'armement
    ("lundi", 1, 0),         # borne de fin, exclue
    ("lundi", 9, 0),
    ("mercredi", 3, 0),
])
def test_hors_fenetre_le_reste_de_la_semaine(moment):
    assert dans_fenetre(_serveur(*moment)) is False


def test_la_fenetre_boucle_au_lieu_de_s_intercaler():
    """Début 7080 min > fin 60 min : « après OU avant », pas « entre »."""
    params = WeekendFlatParams()
    assert dans_fenetre(_serveur("samedi", 3), params) is True
    assert dans_fenetre(_serveur("mardi", 12), params) is False


def test_une_fenetre_degeneree_est_indecidable_et_n_agit_pas():
    for params in (
        WeekendFlatParams(heure_debut=99),
        WeekendFlatParams(jour_debut=9),
        WeekendFlatParams(jour_debut=4, heure_debut=22,
                          jour_fin=4, heure_fin=22),   # début == fin
    ):
        assert dans_fenetre(_serveur("samedi", 12), params) is None
        d = decide_weekend_flat("indices", _serveur("samedi", 12), params)
        assert (d.should_exit, d.reason) == (False, "FENETRE_INVALIDE")


# ── La décision ───────────────────────────────────────────────────────────

def test_la_crypto_n_est_jamais_mise_a_plat():
    """Elle cote tout le week-end : la fermer serait renoncer au seul marché."""
    d = decide_weekend_flat("crypto", _serveur("samedi", 12))
    assert (d.should_exit, d.reason) == (False, "CLASSE_EXEMPTEE")


@pytest.mark.parametrize("classe", ["indices", "fx", "metaux", "energie",
                                    "agricole"])
def test_tout_le_reste_est_mis_a_plat_dans_la_fenetre(classe):
    d = decide_weekend_flat(classe, _serveur("samedi", 12))
    assert (d.should_exit, d.reason) == (True, "FENETRE_WEEKEND")


def test_une_classe_inconnue_est_mise_a_plat():
    """Sens inverse de l'horloge, et c'est voulu : une crypto mal classée est
    reprise au tour suivant, un indice garde par erreur saigne 48 h de swap."""
    for classe in ("", None, "   "):
        d = decide_weekend_flat(classe, _serveur("samedi", 12))
        assert (d.should_exit, d.reason) == (True, "FENETRE_WEEKEND")


def test_la_classe_est_comparee_sans_egard_a_la_casse():
    d = decide_weekend_flat("CRYPTO", _serveur("samedi", 12))
    assert d.should_exit is False


def test_horloge_inconnue_n_envoie_aucun_ordre():
    """Un ordre est irréversible ; un week-end de swap ne l'est pas."""
    d = decide_weekend_flat("indices", None)
    assert (d.should_exit, d.reason) == (False, "HORLOGE_INCONNUE")


def test_hors_fenetre_aucune_cloture():
    d = decide_weekend_flat("indices", _serveur("mercredi", 12))
    assert (d.should_exit, d.reason) == (False, "HORS_FENETRE")


def test_le_drapeau_desactive_prime_sur_tout():
    d = decide_weekend_flat("indices", _serveur("samedi", 12),
                            WeekendFlatParams(actif=False))
    assert (d.should_exit, d.reason) == (False, "DESACTIVE")


def test_la_perte_n_empeche_pas_la_cloture():
    """Aucun argument de résultat n'entre dans la décision : c'est le point.

    Le portage est un coût certain, le retour du prix une hypothèse. La
    fonction ne connaît même pas le P&L — elle ne peut donc pas hésiter.
    """
    d = decide_weekend_flat("indices", _serveur("vendredi", 22))
    assert d.should_exit is True


# ── L'horloge serveur ─────────────────────────────────────────────────────

def test_l_horloge_lit_le_tick_le_plus_avance():
    """Un symbole endormi sous-estimerait l'heure ; le maximum ne ment pas."""
    ref = datetime(2026, 9, 12, 7, 38, tzinfo=timezone.utc)
    avance = ref + timedelta(hours=3)
    retard = ref + timedelta(hours=3) - timedelta(minutes=40)
    mt5 = _Mt5({"BTCUSD": retard.timestamp(), "ETHUSD": avance.timestamp()})
    assert heure_serveur_mt5(mt5, maintenant=ref) == avance


def test_l_horloge_rend_le_decalage_reel_du_courtier():
    """Axi est à GMT+3 : l'heure serveur doit sortir décalée de trois heures."""
    ref = datetime(2026, 9, 12, 7, 38, tzinfo=timezone.utc)
    mt5 = _Mt5({"BTCUSD": (ref + timedelta(hours=3)).timestamp()})
    lu = heure_serveur_mt5(mt5, maintenant=ref)
    assert (lu - ref) == timedelta(hours=3)


def test_un_tick_absent_ou_nul_ne_date_rien():
    for ticks in ({}, {"BTCUSD": 0}, {"BTCUSD": None}):
        assert heure_serveur_mt5(_Mt5(ticks)) is None


def test_un_terminal_qui_leve_ne_casse_pas_le_tour():
    ref = datetime(2026, 9, 12, 7, 38, tzinfo=timezone.utc)
    mt5 = _Mt5({"ETHUSD": (ref + timedelta(hours=3)).timestamp()},
               leve={"BTCUSD"})
    assert heure_serveur_mt5(mt5, maintenant=ref) is not None


def test_un_decalage_invraisemblable_est_rejete():
    """Au-delà d'un fuseau plausible, ce n'est plus une horloge."""
    ref = datetime(2026, 9, 12, 7, 38, tzinfo=timezone.utc)
    mort = ref - timedelta(seconds=DECALAGE_PLAUSIBLE_MAX_S + 3600)
    assert heure_serveur_mt5(_Mt5({"BTCUSD": mort.timestamp()}),
                             maintenant=ref) is None


def test_une_reference_sans_fuseau_est_refusee():
    mt5 = _Mt5({"BTCUSD": datetime(2026, 9, 12, 10, 38).timestamp()})
    assert heure_serveur_mt5(mt5, maintenant=datetime(2026, 9, 12, 7, 38)) is None


# ── Bout en bout ──────────────────────────────────────────────────────────

def test_horloge_lue_puis_decision_sur_un_samedi_reel():
    """Le cas mesuré du 12/09/2026 : DAX40.fs portée, crypto exemptée."""
    ref = datetime(2026, 9, 12, 7, 38, tzinfo=timezone.utc)   # samedi UTC
    mt5 = _Mt5({"BTCUSD": (ref + timedelta(hours=3)).timestamp()})
    serveur = heure_serveur_mt5(mt5, maintenant=ref)
    assert decide_weekend_flat("indices", serveur).should_exit is True
    assert decide_weekend_flat("crypto", serveur).should_exit is False




# ── Câblage dans le passage de gestion ────────────────────────────────────
#
# ⚠️ Ces tests NE PEUVENT PAS épingler une date absolue. `manage_once` appelle
# `heure_serveur_mt5` sans référence, donc la vraisemblance du tick est
# comparée à l'horloge RÉELLE : un epoch figé au vendredi 22:00 dépasse les
# 14 h de tolérance dès le lendemain midi. L'horloge devient alors inconnue et
# les tests se mettent à passer « pour la mauvaise raison » — zéro sortie,
# mais parce que rien n'est décidé plutôt que parce que la règle a tranché.
# C'est arrivé le 12/09/2026, écart mesuré 14.17 h contre 14.0 h de seuil.
#
# On construit donc une heure serveur RELATIVE à maintenant, et une fenêtre
# taillée autour du jour courant. La règle est exercée quel que soit le jour
# où la suite tourne.

class _Pos:
    def __init__(self, symbol: str, ticket: int = 1) -> None:
        self.ticket = ticket
        self.magic = 14_000
        self.comment = "titanium-v14"
        self.symbol = symbol
        self.type = 0
        self.price_open = 1.1000
        self.price_current = 1.1100
        self.sl = 1.0900
        self.tp = 1.1200
        self.volume = 1.0


class _Mt5Gestion:
    """Doublure complète. Ses ticks portent l'heure SERVEUR du scénario."""

    TRADE_ACTION_SLTP = 2
    TRADE_ACTION_DEAL = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 2
    ORDER_FILLING_RETURN = 3

    def __init__(self, positions=(), epochs: dict | None = None,
                 epoch_defaut: float = 0.0) -> None:
        self._positions = positions
        self._epochs = epochs or {}
        self._defaut = epoch_defaut
        self.envois = []

    def positions_get(self):
        return self._positions

    def symbol_info(self, s):
        class SymbolInfo:
            point = 1e-5
            digits = 5
            trade_stops_level = 10
            filling_mode = 2
        return SymbolInfo()

    def symbol_info_tick(self, s):
        epoch = self._epochs.get(s, self._defaut)

        class T:
            bid = 1.1099
            ask = 1.1101
            time = epoch
        return T()

    def order_send(self, req):
        self.envois.append(req)

        class R:
            retcode = 10009
        return R()

    def order_calc_profit(self, *a, **k):
        return -100.0

    def history_deals_get(self, *a, **k):
        return []


def _horloge_serveur_plausible(decalage_h: float = 3.0) -> datetime:
    """Heure serveur crédible : Axi est à GMT+3, la tolérance est de 14 h."""
    return datetime.now(timezone.utc) + timedelta(hours=decalage_h)


def _fenetre_couvrant(instant: datetime) -> WeekendFlatParams:
    """Fenêtre qui couvre tout le jour de ``instant``, quel qu'il soit."""
    jour = instant.weekday()
    return WeekendFlatParams(jour_debut=jour, heure_debut=0, minute_debut=0,
                             jour_fin=(jour + 1) % 7, heure_fin=0,
                             minute_fin=0)


def _fenetre_excluant(instant: datetime) -> WeekendFlatParams:
    """Fenêtre calée sur le surlendemain : elle ne peut pas contenir l'instant."""
    jour = (instant.weekday() + 2) % 7
    return WeekendFlatParams(jour_debut=jour, heure_debut=0, minute_debut=0,
                             jour_fin=(jour + 1) % 7, heure_fin=0,
                             minute_fin=0)


def _compte_demo():
    from titanium.data.mt5_vendor import AccountSnapshot
    return AccountSnapshot(login=50061786, server="Axi-US50-Demo",
                           currency="USD", balance=100.0, equity=100.0,
                           margin_free=90.0, is_demo=True, trade_mode=0)


def _clotures(mt5) -> list:
    """Les clôtures seules : la gestion envoie aussi des SL (action SLTP)."""
    return [e for e in mt5.envois
            if e.get("action") == _Mt5Gestion.TRADE_ACTION_DEAL]


def _passer(symbole, epochs, params, tmp_path):
    from titanium.execution.mt5_executor import ExecutionPolicy
    from titanium.execution.position_manager import ManageParams, manage_once

    mt5 = _Mt5Gestion(positions=(_Pos(symbole),), epochs=epochs)
    rapport = manage_once(
        mt5,
        policy=ExecutionPolicy(enabled=True, expected_demo_login=50061786,
                               magic=14_000),
        params=ManageParams(),
        state_path=tmp_path / "s.json",
        account=_compte_demo(),
        manage_exits=True,
        weekend_flat=params,
    )
    return mt5, rapport


def _epochs(serveur: datetime, symbole: str,
            retard_symbole: timedelta = timedelta(0)) -> dict:
    """Ticks : la crypto date le serveur, le symbole peut être en retard."""
    return {
        "BTCUSD": serveur.timestamp(),
        "ETHUSD": serveur.timestamp(),
        symbole: (serveur - retard_symbole).timestamp(),
    }


def test_le_passage_de_gestion_ferme_un_indice_dans_la_fenetre(tmp_path):
    """Le câblage, pas seulement la décision : un ordre doit réellement partir."""
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer("EURUSD", _epochs(serveur, "EURUSD"),
                           _fenetre_couvrant(serveur), tmp_path)
    assert rapport["weekend_exit_sent"] == 1
    assert rapport["exit_sent"] == 1
    clotures = _clotures(mt5)
    assert len(clotures) == 1
    assert clotures[0]["position"] == 1
    assert clotures[0]["type"] == _Mt5Gestion.ORDER_TYPE_SELL   # clôt un long
    assert "weekend" in clotures[0]["comment"]


def test_le_passage_de_gestion_epargne_la_crypto(tmp_path):
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer("BTCUSD", _epochs(serveur, "BTCUSD"),
                           _fenetre_couvrant(serveur), tmp_path)
    assert rapport["weekend_exit_sent"] == 0
    assert _clotures(mt5) == []


def test_aucune_mise_a_plat_hors_fenetre(tmp_path):
    """Horloge lisible et marché ouvert : seule la fenêtre doit décider."""
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer("EURUSD", _epochs(serveur, "EURUSD"),
                           _fenetre_excluant(serveur), tmp_path)
    assert rapport["weekend_exit_sent"] == 0
    assert _clotures(mt5) == []


def test_le_drapeau_desactive_coupe_le_cablage(tmp_path):
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer("EURUSD", _epochs(serveur, "EURUSD"),
                           WeekendFlatParams(actif=False), tmp_path)
    assert rapport["weekend_exit_sent"] == 0
    assert _clotures(mt5) == []


def test_la_gestion_n_insiste_pas_sur_un_marche_ferme(tmp_path):
    """Sans cette garde : un ordre refusé toutes les dix secondes, 36 h durant.

    Le cas réel du samedi — la crypto date le serveur, mais le dernier tick de
    l'indice remonte à la clôture du vendredi. Fenêtre active, horloge lisible,
    et pourtant aucun ordre ne doit partir.
    """
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer(
        "EURUSD",
        _epochs(serveur, "EURUSD", retard_symbole=timedelta(hours=10)),
        _fenetre_couvrant(serveur), tmp_path,
    )
    assert rapport["weekend_exit_sent"] == 0
    assert _clotures(mt5) == []


def test_un_retard_sous_le_seuil_laisse_fermer(tmp_path):
    """Frontière de `marche_cote` : 10 min de retard, le marché cote encore."""
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer(
        "EURUSD",
        _epochs(serveur, "EURUSD", retard_symbole=timedelta(minutes=10)),
        _fenetre_couvrant(serveur), tmp_path,
    )
    assert rapport["weekend_exit_sent"] == 1
    assert len(_clotures(mt5)) == 1


def test_une_horloge_muette_n_envoie_rien(tmp_path):
    """Ticks sans heure : on ignore le jour, donc on ne touche à rien."""
    serveur = _horloge_serveur_plausible()
    mt5, rapport = _passer("EURUSD", {}, _fenetre_couvrant(serveur), tmp_path)
    assert rapport["weekend_exit_sent"] == 0
    assert _clotures(mt5) == []
