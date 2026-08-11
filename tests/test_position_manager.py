"""Tests de la gestion dynamique du stop.

La décision étant une fonction pure, elle se teste exhaustivement sans terminal
MT5 ni position réelle — c'est tout l'intérêt de l'avoir séparée de l'I/O.

Les garde-fous priment sur les fonctionnalités : un test qui vérifie qu'on
**n'élargit jamais le risque** vaut plus qu'un test qui vérifie que le trailing
suit bien. Les deux sont là, dans cet ordre.
"""

from __future__ import annotations

import json

import pytest

from titanium.execution.mt5_executor import ExecutionPolicy
from titanium.execution.position_manager import (
    PHASE_BREAKEVEN,
    PHASE_INIT,
    PHASE_TRAILING,
    ManageParams,
    PositionSnapshot,
    TrackedState,
    decide_new_sl,
    load_state,
    manage_once,
    save_state,
)

P = ManageParams(breakeven_r=0.8, trail_start_r=1.2, trail_dist_r=0.8)


def pos(**over) -> PositionSnapshot:
    """Long à 1.1000, stop initial 1.0900 → R = 0.0100."""
    base = dict(ticket="1", symbol="EURUSD", side=1, entry=1.1000, current=1.1000,
                sl=1.0900, tp=1.1200, digits=5, min_stop_distance=0.0002, spread=0.0001)
    base.update(over)
    return PositionSnapshot(**base)


def etat(**over) -> TrackedState:
    base = dict(r=0.0100, phase=PHASE_INIT, peak_fav_r=0.0, symbol="EURUSD", side=1)
    base.update(over)
    return TrackedState(**base)


# ══════════════════════ garde-fous : le plus important ══════════════════════

def test_le_sl_ne_recule_jamais():
    """LE test qui compte. Élargir le risque en cours de route est la faute
    qu'aucune fonctionnalité ne rattrape.

    Cas construit pour atteindre vraiment le garde-fou : le trailing propose
    1.1050 (pic +1.3 R), mais le SL est déjà à 1.1060. Reculer serait rendre
    du gain déjà sécurisé.
    """
    s = etat(phase=PHASE_TRAILING, peak_fav_r=1.3)
    d = decide_new_sl(pos(current=1.1130, sl=1.1060), s, P)
    assert d.new_sl is None
    assert d.reason == "PAS_D_AMELIORATION"


def test_rien_a_faire_quand_aucun_seuil_nest_atteint():
    s = etat(phase=PHASE_BREAKEVEN, peak_fav_r=1.0)
    d = decide_new_sl(pos(current=1.1020, sl=1.1005), s, P)
    assert d.new_sl is None
    assert d.reason == "RIEN_A_FAIRE"


def test_trailing_ne_rend_jamais_le_gain_acquis():
    """Le pic est un cliquet : le prix peut redescendre, le SL ne suit pas."""
    s = etat()
    decide_new_sl(pos(current=1.1200), s, P)          # +2.0 R, pic établi
    haut = s.peak_fav_r
    d = decide_new_sl(pos(current=1.1050, sl=1.1020), s, P)  # retombe à +0.5 R
    assert s.peak_fav_r == haut, "le pic ne doit jamais redescendre"
    assert d.new_sl is None


def test_distance_minimale_du_courtier_respectee():
    """Un SL trop près du prix se fait rejeter (retcode 10016) : on s'abstient."""
    s = etat()
    d = decide_new_sl(pos(current=1.1085, min_stop_distance=0.0100), s, P)
    assert d.new_sl is None
    assert d.reason == "TROP_PRES_DU_PRIX"


def test_sl_du_mauvais_cote_refuse():
    """Un stop au-delà du prix fermerait la position sur-le-champ."""
    s = etat(peak_fav_r=5.0)
    d = decide_new_sl(pos(current=1.1010, sl=1.0900, min_stop_distance=0.0), s, P)
    assert d.new_sl is None
    assert d.reason in ("SL_DU_MAUVAIS_COTE", "TROP_PRES_DU_PRIX")


def test_le_tp_nest_jamais_touche():
    """La gestion ne s'occupe que du stop."""
    s = etat()
    d = decide_new_sl(pos(current=1.1100), s, P)
    assert d.new_sl is not None
    assert not hasattr(d, "new_tp")


@pytest.mark.parametrize("r", [0, -1, float("nan"), float("inf")])
def test_r_invalide_ne_fait_rien(r):
    d = decide_new_sl(pos(current=1.1100), etat(r=r), P)
    assert d.new_sl is None
    assert d.reason == "R_INVALIDE"


@pytest.mark.parametrize("side", [0, 2, -3])
def test_side_invalide_ne_fait_rien(side):
    d = decide_new_sl(pos(side=side, current=1.1100), etat(), P)
    assert d.new_sl is None
    assert d.reason == "SIDE_INVALIDE"


@pytest.mark.parametrize("champ", ["entry", "current"])
def test_prix_non_fini_ne_fait_rien(champ):
    champs = {"current": 1.1100}
    champs[champ] = float("nan")
    d = decide_new_sl(pos(**champs), etat(), P)
    assert d.new_sl is None
    assert d.reason == "PRIX_INVALIDE"


# ═════════════════════════════ breakeven ════════════════════════════════════

def test_pas_de_breakeven_avant_le_seuil():
    d = decide_new_sl(pos(current=1.1070), etat(), P)   # +0.7 R < 0.8
    assert d.new_sl is None
    assert d.phase == PHASE_INIT


def test_breakeven_au_seuil():
    s = etat()
    d = decide_new_sl(pos(current=1.1080), s, P)        # +0.8 R
    assert d.new_sl is not None
    assert d.new_sl > 1.1000, "le SL doit passer AU-DESSUS de l'entrée"
    assert s.phase == PHASE_BREAKEVEN


def test_breakeven_garde_un_tampon_au_dessus_de_lentree():
    """Placer le SL pile à l'entrée le ferait toucher par le seul spread."""
    s = etat()
    # tampon = max(spread 0.0003, 5 % de R = 0.0005) = 0.0005
    d = decide_new_sl(pos(current=1.1080, spread=0.0003), s, P)
    assert d.new_sl == pytest.approx(1.1005, abs=1e-6)


def test_tampon_suit_le_spread_quand_il_domine():
    """Sur un instrument a spread large, c'est le spread qui commande."""
    s = etat()
    d = decide_new_sl(pos(current=1.1080, spread=0.0020), s, P)
    assert d.new_sl == pytest.approx(1.1020, abs=1e-6)


def test_tampon_minimum_si_spread_nul():
    """Sans spread connu, on retombe sur 5 % du R."""
    s = etat()
    d = decide_new_sl(pos(current=1.1080, spread=0.0), s, P)
    assert d.new_sl == pytest.approx(1.1000 + 0.05 * 0.0100, abs=1e-6)


def test_breakeven_short_symetrique():
    """Short à 1.1000, stop 1.1100. À +0.8 R le prix est à 1.0920."""
    s = etat(side=-1)
    d = decide_new_sl(pos(side=-1, current=1.0920, sl=1.1100, tp=1.0800), s, P)
    assert d.new_sl is not None
    assert d.new_sl < 1.1000, "SL d'un short doit descendre sous l'entrée"


# ══════════════════════════════ trailing ════════════════════════════════════

def test_trailing_demarre_au_seuil():
    s = etat()
    d = decide_new_sl(pos(current=1.1120), s, P)        # +1.2 R
    assert s.phase == PHASE_TRAILING
    # SL = entrée + (1.2 − 0.8) × R = 1.1000 + 0.0040
    assert d.new_sl == pytest.approx(1.1040, abs=1e-6)


def test_trailing_suit_le_pic():
    s = etat()
    decide_new_sl(pos(current=1.1200), s, P)            # pic +2.0 R
    d = decide_new_sl(pos(current=1.1200, sl=1.1040), s, P)
    # SL = entrée + (2.0 − 0.8) × R = 1.1120
    assert d.new_sl == pytest.approx(1.1120, abs=1e-6)


def test_trailing_prime_sur_breakeven():
    """Un gain qui dépasse d'emblée les deux seuils prend le meilleur SL."""
    s = etat()
    d = decide_new_sl(pos(current=1.1300), s, P)        # +3.0 R
    assert s.phase == PHASE_TRAILING
    assert d.new_sl == pytest.approx(1.1220, abs=1e-6)  # bien mieux que breakeven


def test_trailing_short():
    s = etat(side=-1)
    d = decide_new_sl(pos(side=-1, current=1.0800, sl=1.1100, tp=1.0700), s, P)
    # pic +2.0 R → SL = 1.1000 − 1.2 × 0.0100 = 1.0880
    assert d.new_sl == pytest.approx(1.0880, abs=1e-6)


def test_seuils_configurables():
    strict = ManageParams(breakeven_r=2.0, trail_start_r=3.0, trail_dist_r=0.5)
    d = decide_new_sl(pos(current=1.1100), etat(), strict)   # +1.0 R
    assert d.new_sl is None, "seuil relevé : +1 R ne doit plus déclencher"


def test_params_depuis_la_config():
    p = ManageParams.from_config({"manage_breakeven_r": 1.5,
                                  "manage_trail_start_r": 2.0,
                                  "manage_trail_dist_r": 0.4})
    assert (p.breakeven_r, p.trail_start_r, p.trail_dist_r) == (1.5, 2.0, 0.4)


def test_params_illisibles_retombent_sur_les_defauts():
    p = ManageParams.from_config({"manage_breakeven_r": "beaucoup",
                                  "manage_trail_start_r": float("nan")})
    assert p.breakeven_r == 0.8
    assert p.trail_start_r == 1.2


def test_config_du_projet_porte_les_valeurs_du_postmortem():
    from tradingagents.default_config import DEFAULT_CONFIG
    p = ManageParams.from_config(DEFAULT_CONFIG)
    assert p.breakeven_r == 0.8, "valeur issue du post-mortem V12 (23 % des pertes)"


# ═══════════════════════════ persistance ════════════════════════════════════

def test_aller_retour_disque(tmp_path):
    f = tmp_path / "state.json"
    save_state(f, {"42": etat(phase=PHASE_TRAILING, peak_fav_r=2.5)})
    relu = load_state(f)
    assert relu["42"].r == 0.0100
    assert relu["42"].phase == PHASE_TRAILING
    assert relu["42"].peak_fav_r == 2.5


def test_fichier_illisible_ne_bloque_pas(tmp_path):
    """Un état corrompu ne doit jamais empêcher de gérer les positions."""
    f = tmp_path / "state.json"
    f.write_text("{ ceci n'est pas du json", encoding="utf-8")
    assert load_state(f) == {}


def test_fichier_absent_ne_bloque_pas(tmp_path):
    assert load_state(tmp_path / "jamais_ecrit.json") == {}


def test_ecriture_atomique(tmp_path):
    """Pas de fichier temporaire laissé derrière, pas de JSON tronqué."""
    f = tmp_path / "state.json"
    save_state(f, {"1": etat()})
    assert json.loads(f.read_text(encoding="utf-8"))["1"]["r"] == 0.0100
    assert not list(tmp_path.glob("*.tmp"))


# ═══════════════════════ passage complet (faux MT5) ═════════════════════════

class FakePos:
    def __init__(self, ticket=1, magic=14_000, comment="titanium-v14",
                 type_=0, open_=1.1000, current=1.1100, sl=1.0900, tp=1.1200):
        self.ticket = ticket
        self.magic = magic
        self.comment = comment
        self.symbol = "EURUSD"
        self.type = type_
        self.price_open = open_
        self.price_current = current
        self.sl = sl
        self.tp = tp


class FakeMt5:
    TRADE_ACTION_SLTP = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, positions=(), retcode=10009):
        self._positions = positions
        self._retcode = retcode
        self.envois = []

    def positions_get(self):
        return self._positions

    def symbol_info(self, s):
        class I:
            point = 1e-5
            digits = 5
            trade_stops_level = 10
        return I()

    def symbol_info_tick(self, s):
        class T:
            bid = 1.1099
            ask = 1.1101
        return T()

    def order_send(self, req):
        self.envois.append(req)

        class R:
            retcode = self._retcode
        return R()


ARMEE = ExecutionPolicy(enabled=True, expected_demo_login=50061786, magic=14_000)


def compte_demo():
    from titanium.data.mt5_vendor import AccountSnapshot
    return AccountSnapshot(login=50061786, server="Axi-US50-Demo", currency="USD",
                           balance=109.0, equity=108.0, margin_free=100.0,
                           is_demo=True, trade_mode=0)


def test_passage_deplace_le_sl(tmp_path):
    m = FakeMt5(positions=(FakePos(),))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["managed"] == 1
    assert r["moved"] == 1
    envoi = m.envois[0]
    assert envoi["action"] == FakeMt5.TRADE_ACTION_SLTP
    assert envoi["tp"] == pytest.approx(1.1200), "le TP doit être renvoyé inchangé"


def test_tp_short_est_renvoye_strictement_inchange(tmp_path):
    """Le chemin short protège aussi le TP contre toute dérive silencieuse."""
    m = FakeMt5(positions=(FakePos(
        type_=1, open_=1.1000, current=1.0900, sl=1.1100, tp=1.0700,
    ),))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["moved"] == 1
    assert m.envois[0]["tp"] == pytest.approx(1.0700)


def test_positions_etrangeres_ignorees(tmp_path):
    """Ne jamais toucher au stop d'une position ouverte à la main."""
    m = FakeMt5(positions=(FakePos(magic=999, comment="manuel"),))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["managed"] == 0
    assert m.envois == []


def test_mur_ferme_ne_gere_rien(tmp_path):
    """Gérer un SL est un ordre : le mur s'applique aussi."""
    m = FakeMt5(positions=(FakePos(),))
    r = manage_once(m, policy=ExecutionPolicy(), params=P,
                    state_path=tmp_path / "s.json", account=compte_demo())
    assert r["reason"] == "EXEC_DISARMED"
    assert m.envois == []


def test_compte_reel_ne_gere_rien(tmp_path):
    from titanium.data.mt5_vendor import AccountSnapshot
    reel = AccountSnapshot(login=60261188, server="Axi-US52-Live", currency="EUR",
                           balance=20.0, equity=20.0, margin_free=15.0,
                           is_demo=False, trade_mode=2)
    m = FakeMt5(positions=(FakePos(),))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=reel)
    assert r["reason"] == "WALL_NOT_DEMO"
    assert m.envois == []


def test_position_sans_sl_initial_ignoree(tmp_path):
    """Sans SL d'origine, R est inconnu : on ne peut rien mesurer."""
    m = FakeMt5(positions=(FakePos(sl=0),))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["moved"] == 0
    assert "pas de SL initial" in " ".join(r["details"])


def test_r_fige_a_la_premiere_observation(tmp_path):
    """Après un déplacement de SL, R ne doit PAS être recalculé — sinon toutes
    les mesures en R dérivent."""
    f = tmp_path / "s.json"
    m = FakeMt5(positions=(FakePos(),))
    manage_once(m, policy=ARMEE, params=P, state_path=f, account=compte_demo())
    r_initial = load_state(f)["1"].r

    m2 = FakeMt5(positions=(FakePos(sl=1.1005, current=1.1150),))
    manage_once(m2, policy=ARMEE, params=P, state_path=f, account=compte_demo())
    assert load_state(f)["1"].r == r_initial


def test_tickets_fermes_purges(tmp_path):
    f = tmp_path / "s.json"
    save_state(f, {"1": etat(), "999": etat()})
    manage_once(FakeMt5(positions=(FakePos(),)), policy=ARMEE, params=P,
                state_path=f, account=compte_demo())
    assert "999" not in load_state(f), "un ticket fermé doit disparaître de l'état"


def test_refus_du_courtier_rapporte(tmp_path):
    m = FakeMt5(positions=(FakePos(),), retcode=10016)
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["moved"] == 0
    assert "10016" in " ".join(r["details"])


def test_une_position_en_erreur_ne_casse_pas_la_boucle(tmp_path):
    class Cassee(FakePos):
        @property
        def price_open(self):
            raise RuntimeError("champ corrompu")

        @price_open.setter
        def price_open(self, v):
            pass

    m = FakeMt5(positions=(Cassee(ticket=1), FakePos(ticket=2)))
    r = manage_once(m, policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["managed"] == 2
    assert r["moved"] == 1, "la seconde position doit être gérée malgré la première"


def test_positions_indisponibles_ne_leve_pas(tmp_path):
    class Muet(FakeMt5):
        def positions_get(self):
            raise ConnectionError("terminal parti")

    r = manage_once(Muet(), policy=ARMEE, params=P, state_path=tmp_path / "s.json",
                    account=compte_demo())
    assert r["moved"] == 0
    assert "POSITIONS_INDISPONIBLES" in r["reason"]


def test_positions_none_est_une_erreur_et_ne_purge_pas(tmp_path):
    class Muet(FakeMt5):
        def positions_get(self):
            return None

        def last_error(self):
            return (-10005, "terminal absent")

    f = tmp_path / "s.json"
    save_state(f, {"1": etat()})
    r = manage_once(Muet(), policy=ARMEE, params=P, state_path=f,
                    account=compte_demo())
    assert r["reason"].startswith("POSITIONS_INDISPONIBLES")
    assert "1" in load_state(f), "une panne MT5 ne doit jamais purger l'état"
