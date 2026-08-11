"""Tests de l'exécuteur MT5 — le mur démo↔réel et le calcul de lot.

C'est le module le plus dangereux de V14 : le seul qui peut engager de l'argent.
Les tests sont donc écrits dans l'esprit « prouver qu'il refuse », pas
« prouver qu'il marche ». Aucun test n'envoie d'ordre à un vrai terminal —
tout passe par un faux MT5.

Le contexte qui justifie cette paranoïa : sur cette machine, le terminal était
connecté au compte RÉEL 60261188 le matin et au compte DÉMO 50061786
l'après-midi. Le mur est vérifié à chaque ordre, jamais mis en cache.
"""

from __future__ import annotations

import pytest

from titanium.data.mt5_vendor import AccountSnapshot, SymbolSpec
from titanium.execution import mt5_executor as ex
from titanium.execution.mt5_executor import (
    WALL_DISARMED,
    WALL_LOGIN_MISMATCH,
    WALL_NO_EXPECTED_LOGIN,
    WALL_NOT_DEMO,
    ExecutionPolicy,
    ExecutionRefused,
    LotComputationError,
    assert_can_trade,
    compute_lot,
    place_market_order,
)

DEMO_LOGIN = 50061786
REAL_LOGIN = 60261188


def compte(trade_mode=0, login=DEMO_LOGIN, server="Axi-US50-Demo") -> AccountSnapshot:
    return AccountSnapshot(login=login, server=server, currency="USD",
                           balance=109.16, equity=108.37, margin_free=100.0,
                           is_demo=(trade_mode == 0), trade_mode=trade_mode)


def spec(**over) -> SymbolSpec:
    base = dict(name="EURUSD", digits=5, point=1e-5, volume_min=0.01,
                volume_max=100.0, volume_step=0.01, trade_contract_size=100_000.0,
                spread=12, tick_value=1.0, tick_size=1e-5)
    base.update(over)
    return SymbolSpec(**base)


def armee(**over) -> ExecutionPolicy:
    base = dict(enabled=True, expected_demo_login=DEMO_LOGIN)
    base.update(over)
    return ExecutionPolicy(**base)


@pytest.fixture(autouse=True)
def _reset():
    ex.reset_idempotency()
    yield
    ex.reset_idempotency()


# ══════════════════════════════ LE MUR ══════════════════════════════════════

def test_desarme_par_defaut():
    """Le défaut d'ExecutionPolicy ne doit rien laisser passer."""
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(ExecutionPolicy(), compte())
    assert e.value.code == WALL_DISARMED


def test_compte_reel_refuse():
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(armee(), compte(trade_mode=2, login=REAL_LOGIN,
                                         server="Axi-US52-Live"))
    assert e.value.code == WALL_NOT_DEMO
    assert str(REAL_LOGIN) in e.value.detail


def test_compte_concours_refuse():
    """trade_mode=1 (concours) n'est pas un compte démo."""
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(armee(), compte(trade_mode=1))
    assert e.value.code == WALL_NOT_DEMO


def test_login_different_refuse():
    """Même en démo : si l'opérateur change de compte dans le terminal, refus."""
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(armee(), compte(trade_mode=0, login=99999))
    assert e.value.code == WALL_LOGIN_MISMATCH


def test_sans_login_declare_refuse():
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(ExecutionPolicy(enabled=True), compte())
    assert e.value.code == WALL_NO_EXPECTED_LOGIN


def test_compte_demo_attendu_passe():
    assert_can_trade(armee(), compte()) is None


def test_ordre_des_verrous():
    """Désarmé prime sur tout : un compte réel + désarmé rend EXEC_DISARMED."""
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(ExecutionPolicy(enabled=False),
                         compte(trade_mode=2, login=REAL_LOGIN))
    assert e.value.code == WALL_DISARMED


def test_compte_reel_possible_seulement_avec_drapeau_explicite():
    """allow_real_account existe pour rendre la décision cherchable, pas pour
    le confort. Il ne dispense PAS de la vérification de login."""
    p = armee(allow_real_account=True, expected_demo_login=REAL_LOGIN)
    assert_can_trade(p, compte(trade_mode=2, login=REAL_LOGIN))

    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(armee(allow_real_account=True),
                         compte(trade_mode=2, login=REAL_LOGIN))
    assert e.value.code == WALL_LOGIN_MISMATCH


# ═══════════════════════════ CALCUL DE LOT ══════════════════════════════════

def test_lot_nominal():
    """100 € de risque, stop 50 pips (0.00500), tick_value 1 / tick_size 1e-5
    → perte par lot = 500 → lot = 0.2."""
    lot, over = compute_lot(spec(), stop_distance=0.005, risk_money=100.0)
    assert lot == pytest.approx(0.2)
    assert over == 1.0


def test_lot_arrondi_vers_le_bas():
    """Un arrondi au supérieur ferait dépasser le risque voulu : interdit."""
    lot, _ = compute_lot(spec(volume_step=0.1), stop_distance=0.005, risk_money=100.0)
    assert lot == pytest.approx(0.2)
    lot, _ = compute_lot(spec(volume_step=0.1), stop_distance=0.005, risk_money=149.0)
    assert lot == pytest.approx(0.2)  # 0.298 → 0.2, pas 0.3


def test_lot_plafonne_au_max():
    lot, _ = compute_lot(spec(volume_max=0.5), stop_distance=0.005, risk_money=100_000.0)
    assert lot == 0.5


def test_lot_sous_minimum_refuse_par_defaut():
    """Le cas du petit compte : 1 € de risque ne finance pas 0.01 lot.
    Entrer quand même = risquer plus que prévu. Refus par défaut."""
    with pytest.raises(LotComputationError) as e:
        compute_lot(spec(), stop_distance=0.005, risk_money=1.0)
    assert e.value.code == "LOT_SOUS_MINIMUM"
    assert "×5.00" in e.value.detail  # 5 € au minimum contre 1 € voulu


def test_lot_sous_minimum_accepte_si_demande():
    lot, over = compute_lot(spec(), stop_distance=0.005, risk_money=1.0,
                            allow_min_lot_overrisk=True)
    assert lot == 0.01
    assert over == pytest.approx(5.0)


@pytest.mark.parametrize("champ,valeur,code", [
    ("tick_size", 0.0, "SPECS_INCOMPLETES"),
    ("tick_value", 0.0, "SPECS_INCOMPLETES"),
    ("volume_step", 0.0, "SPECS_INCOMPLETES"),
])
def test_specs_incompletes_refusent(champ, valeur, code):
    with pytest.raises(LotComputationError) as e:
        compute_lot(spec(**{champ: valeur}), stop_distance=0.005, risk_money=100.0)
    assert e.value.code == code


@pytest.mark.parametrize("stop", [0, -1, float("nan"), float("inf")])
def test_stop_invalide_refuse(stop):
    with pytest.raises(LotComputationError) as e:
        compute_lot(spec(), stop_distance=stop, risk_money=100.0)
    assert e.value.code == "STOP_INVALIDE"


@pytest.mark.parametrize("risque", [0, -50, float("nan")])
def test_risque_invalide_refuse(risque):
    with pytest.raises(LotComputationError) as e:
        compute_lot(spec(), stop_distance=0.005, risk_money=risque)
    assert e.value.code == "RISQUE_INVALIDE"


# ═════════════════════════ ENVOI (faux terminal) ════════════════════════════

class FakeResult:
    def __init__(self, retcode, order=555, price=1.1002, comment="ok"):
        self.retcode = retcode
        self.order = order
        self.price = price
        self.comment = comment


class FakeMt5:
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, retcode=10009, tick=True, filling_mode=2):
        self.retcode = retcode
        self._tick = tick
        self._filling = filling_mode
        self.requetes = []

    def symbol_info(self, s):
        class I:
            filling_mode = self._filling
        return I()

    def symbol_info_tick(self, s):
        if not self._tick:
            return None

        class T:
            bid = 1.1000
            ask = 1.1002
        return T()

    def order_send(self, req):
        self.requetes.append(req)
        return FakeResult(self.retcode)

    def last_error(self):
        return (-1, "faux")


@pytest.fixture
def terminal(monkeypatch):
    """Branche un faux terminal + un compte démo conforme."""
    def _install(*, acc=None, mt5=None, symbole=None):
        m = mt5 or FakeMt5()
        from contextlib import contextmanager

        @contextmanager
        def session():
            yield m

        monkeypatch.setattr(ex, "account_snapshot", lambda: acc or compte())
        monkeypatch.setattr(ex, "ensure_symbol", lambda s: symbole or spec())
        monkeypatch.setattr(ex, "mt5_session", session)
        return m
    return _install


def test_ordre_nominal(terminal):
    m = terminal()
    r = place_market_order("EURUSD", 1, risk_money=100.0, stop_distance=0.005,
                           tp_distance=0.010, policy=armee())
    assert r.sent is True
    assert r.reason == "OK"
    assert r.ticket == 555
    assert r.lot == pytest.approx(0.2)
    req = m.requetes[0]
    assert req["type"] == FakeMt5.ORDER_TYPE_BUY
    assert req["sl"] == pytest.approx(1.0952)   # ask 1.1002 − 0.005
    assert req["tp"] == pytest.approx(1.1102)   # ask 1.1002 + 0.010
    assert req["magic"] == 14_000


def test_ordre_vente_inverse_sl_tp(terminal):
    m = terminal()
    place_market_order("EURUSD", -1, risk_money=100.0, stop_distance=0.005,
                       tp_distance=0.010, policy=armee())
    req = m.requetes[0]
    assert req["type"] == FakeMt5.ORDER_TYPE_SELL
    assert req["price"] == pytest.approx(1.1000)  # bid
    assert req["sl"] == pytest.approx(1.105)
    assert req["tp"] == pytest.approx(1.090)


def test_sans_tp(terminal):
    m = terminal()
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert m.requetes[0]["tp"] == 0.0


def test_mode_de_remplissage_choisi_selon_le_masque(terminal):
    m = terminal(mt5=FakeMt5(filling_mode=1))  # FOK seulement
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert m.requetes[0]["type_filling"] == FakeMt5.ORDER_FILLING_FOK

    m2 = terminal(mt5=FakeMt5(filling_mode=2))  # IOC
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert m2.requetes[0]["type_filling"] == FakeMt5.ORDER_FILLING_IOC


# ───────────────── refus : jamais d'exception vers le moteur ────────────────

def test_mur_ferme_ne_leve_pas(terminal):
    terminal(acc=compte(trade_mode=2, login=REAL_LOGIN))
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert r.sent is False
    assert r.reason == WALL_NOT_DEMO
    assert any(c["gate"] == "wall" and not c["passed"] for c in r.checks)


def test_desarme_ne_leve_pas(terminal):
    terminal()
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=ExecutionPolicy())
    assert r.sent is False
    assert r.reason == WALL_DISARMED


def test_snapshot_qui_plante_ne_leve_pas(monkeypatch):
    def boum():
        raise ConnectionError("terminal parti")
    monkeypatch.setattr(ex, "account_snapshot", boum)
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert r.sent is False
    assert r.reason == "WALL_ERREUR"


def test_lot_impossible_ne_leve_pas(terminal):
    terminal()
    r = place_market_order("EURUSD", 1, risk_money=1.0, stop_distance=0.005,
                           policy=armee())
    assert r.sent is False
    assert r.reason == "LOT_SOUS_MINIMUM"


def test_retcode_erreur_rapporte(terminal):
    terminal(mt5=FakeMt5(retcode=10016))  # invalid stops
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert r.sent is False
    assert r.reason == "RETCODE_10016"
    assert r.retcode == 10016


def test_pas_de_prix(terminal):
    terminal(mt5=FakeMt5(tick=False))
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee())
    assert r.sent is False
    assert r.reason == "PAS_DE_PRIX"


@pytest.mark.parametrize("side", [0, None, 2])
def test_side_invalide(terminal, side):
    terminal()
    r = place_market_order("EURUSD", side, 100.0, 0.005, policy=armee())
    assert r.reason == "SIDE_INVALIDE"


# ──────────────────────────── idempotence ───────────────────────────────────

def test_meme_cle_nenvoie_quune_fois(terminal):
    m = terminal()
    a = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(),
                           idempotency_key="EURUSD:H4:2026-08-06T08:00")
    b = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(),
                           idempotency_key="EURUSD:H4:2026-08-06T08:00")
    assert a.sent is True
    assert b.sent is False
    assert b.reason == "DEJA_ENVOYE"
    assert len(m.requetes) == 1


def test_cles_differentes_passent(terminal):
    m = terminal()
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(), idempotency_key="k1")
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(), idempotency_key="k2")
    assert len(m.requetes) == 2


def test_cle_non_memorisee_si_lordre_echoue(terminal):
    """Un ordre refusé doit pouvoir être retenté sous la même clé."""
    terminal(mt5=FakeMt5(retcode=10016))
    place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(), idempotency_key="k")
    m = terminal(mt5=FakeMt5(retcode=10009))
    r = place_market_order("EURUSD", 1, 100.0, 0.005, policy=armee(), idempotency_key="k")
    assert r.sent is True


# ────────────────── politique depuis LA config de V14 ──────────────────────
# Une seule source : DEFAULT_CONFIG, alimenté par les TITANIUM_* du .env.

def test_config_vide_donne_une_politique_desarmee():
    p = ExecutionPolicy.from_config({})
    assert p.enabled is False
    assert p.expected_demo_login is None
    assert p.allow_real_account is False


def test_config_arme_le_demo():
    p = ExecutionPolicy.from_config({"exec_enabled": True, "demo_login": DEMO_LOGIN})
    assert p.enabled is True
    assert p.expected_demo_login == DEMO_LOGIN
    assert_can_trade(p, compte())


def test_login_zero_vaut_non_declare():
    """0 est le défaut de DEFAULT_CONFIG : il doit se lire « rien de déclaré »,
    surtout pas « compte 0 »."""
    p = ExecutionPolicy.from_config({"exec_enabled": True, "demo_login": 0})
    assert p.expected_demo_login is None
    with pytest.raises(ExecutionRefused) as e:
        assert_can_trade(p, compte())
    assert e.value.code == WALL_NO_EXPECTED_LOGIN


def test_login_illisible_retombe_sur_none():
    p = ExecutionPolicy.from_config({"exec_enabled": True, "demo_login": "abc"})
    assert p.expected_demo_login is None


@pytest.mark.parametrize("valeur", ["1", "true", "yes", "on", "TRUE", "oui", "", None])
def test_compte_reel_refuse_toute_graphie_approximative(valeur):
    """Seule la phrase exacte ouvre le réel. « yes » ne suffit pas."""
    p = ExecutionPolicy.from_config({"allow_real_account": valeur})
    assert p.allow_real_account is False


def test_compte_reel_exige_la_phrase_exacte():
    p = ExecutionPolicy.from_config({
        "allow_real_account": ExecutionPolicy.REAL_ACCOUNT_PHRASE,
    })
    assert p.allow_real_account is True


def test_config_reelle_du_projet_est_desarmee():
    """Garde-fou : le .env livré ne doit jamais armer l'exécution."""
    from tradingagents.default_config import DEFAULT_CONFIG

    p = ExecutionPolicy.from_config(DEFAULT_CONFIG)
    assert p.allow_real_account is False, "le .env ouvre le COMPTE RÉEL"


def test_surrisque_rapporte(terminal):
    terminal()
    r = place_market_order("EURUSD", 1, risk_money=1.0, stop_distance=0.005,
                           policy=armee(allow_min_lot_overrisk=True))
    assert r.sent is True
    assert r.lot == 0.01
    assert r.overrisk_ratio == pytest.approx(5.0)
    assert r.risk_money_effective == pytest.approx(5.0)
