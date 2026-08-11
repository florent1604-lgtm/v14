"""Tests du vendeur MT5.

Deux familles :

* **unitaires** — un faux module MT5 injecté, donc exécutables partout (CI,
  Linux, machine sans terminal). Ils couvrent la logique qui compte : rejet des
  entrées invalides, abandon de la bougie en cours, taxonomie d'erreurs.
* **intégration** — marqués ``integration``, sautés si le terminal n'est pas
  joignable. Ils ne font que LIRE : aucun ordre, jamais.

Invariant vérifié explicitement : le module ne contient aucun appel de passage
d'ordre. C'est ce qui autorise à le brancher sur un compte réel.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from tradingagents.dataflows.errors import (
    NoMarketDataError,
    VendorError,
    VendorNotConfiguredError,
)
from titanium.data import mt5_vendor as v


# ────────────────────────────── faux terminal ───────────────────────────────

class FakeSymbolInfo:
    def __init__(self, name="EURUSD", visible=True):
        self.name = name
        self.visible = visible
        self.digits = 5
        self.point = 1e-5
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.trade_contract_size = 100_000.0
        self.spread = 12
        self.trade_tick_value = 1.0
        self.trade_tick_size = 1e-5


class FakeAccountInfo:
    def __init__(self, trade_mode=0, login=123):
        self.login = login
        self.server = "Axi-Demo"
        self.currency = "EUR"
        self.balance = 1000.0
        self.equity = 1010.0
        self.margin_free = 990.0
        self.trade_mode = trade_mode


class FakeMt5:
    """Terminal minimal. Expose UNIQUEMENT ce que le vendeur a le droit d'utiliser."""

    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H4 = 16388
    TIMEFRAME_D1 = 16408
    TIMEFRAME_W1 = 32769
    TIMEFRAME_MN1 = 49153

    def __init__(self, *, rates=None, init_ok=True, account=None, symbol=True):
        self._rates = rates
        self._init_ok = init_ok
        self._account = account if account is not None else FakeAccountInfo()
        self._symbol = symbol
        self.shutdown_calls = 0

    def initialize(self):
        return self._init_ok

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return (-1, "faux terminal")

    def account_info(self):
        return self._account

    def symbol_info(self, name):
        return FakeSymbolInfo(name) if self._symbol else None

    def symbol_select(self, name, enable):
        return True

    def copy_rates_from_pos(self, symbol, tf, start, count):
        return self._rates

    def symbol_info_tick(self, symbol):
        class T:
            time_msc = 1_770_000_000_000
            bid = 1.1000
            ask = 1.1002
            last = 1.1001
        return T()


@pytest.fixture
def fake(monkeypatch):
    """Injecte un faux terminal et remet le vendeur à zéro autour du test."""
    def _install(**kwargs):
        m = FakeMt5(**kwargs)
        monkeypatch.setattr(v, "_mt5", lambda: m)
        monkeypatch.setattr(v, "_initialized", False)
        return m
    yield _install
    v._initialized = False


def rates(n: int):
    """n bougies horodatées, la dernière étant « en cours »."""
    import numpy as np
    base = 1_770_000_000
    return np.array(
        [(base + i * 14400, 1.1, 1.2, 1.0, 1.1 + i * 0.001, 100, 2, 0) for i in range(n)],
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"),
               ("close", "<f8"), ("tick_volume", "<u8"), ("spread", "<i4"),
               ("real_volume", "<u8")],
    )


# ─────────────────────── invariant : lecture seule ──────────────────────────

INTERDITS = frozenset({
    "order_send", "order_check", "order_calc_margin",
    "positions_get", "positions_total", "history_orders_get",
})


def test_le_module_ne_contient_aucun_passage_dordre():
    """Garde-fou structurel : si quelqu'un ajoute un jour `order_send` ici, ce
    test casse. L'exécution doit rester derrière le mur démo↔réel.

    On analyse l'AST et non le texte : la docstring du module *cite* ces noms
    pour expliquer l'interdit, et un `grep` naïf s'y prendrait les pieds. Ce
    qui compte, c'est qu'aucun appel ni accès d'attribut ne les utilise.
    """
    import ast

    arbre = ast.parse(Path(v.__file__).read_text(encoding="utf-8"))
    utilises = {
        n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)
    } | {
        n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)
    }
    fautifs = sorted(utilises & INTERDITS)
    assert not fautifs, f"appels interdits dans un module lecture seule : {fautifs}"


# ──────────────────────────── taxonomie d'erreurs ───────────────────────────

def test_erreur_mt5_est_une_erreur_de_vendeur():
    """La couche de routage doit pouvoir basculer sur yfinance sans connaître MT5."""
    assert issubclass(v.Mt5NotAvailableError, VendorNotConfiguredError)
    assert issubclass(v.Mt5NotAvailableError, VendorError)
    assert issubclass(v.Mt5NotAvailableError, ValueError)


def test_module_absent_leve_une_erreur_de_vendeur(monkeypatch):
    import builtins
    vrai_import = builtins.__import__

    def sans_mt5(name, *a, **k):
        if name == "MetaTrader5":
            raise ImportError("no module named MetaTrader5")
        return vrai_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", sans_mt5)
    with pytest.raises(v.Mt5NotAvailableError, match="module MetaTrader5 absent"):
        v._mt5()


def test_terminal_injoignable_leve(fake):
    fake(init_ok=False)
    with pytest.raises(v.Mt5NotAvailableError, match="injoignable"):
        v.account_snapshot()


def test_is_available_ne_leve_jamais(fake):
    fake(init_ok=False)
    assert v.is_available() is False


# ──────────────────────────── verrou de sérialisation ───────────────────────

def test_le_verrou_est_reentrant():
    """`get_rates` appelle `ensure_symbol`, qui reprend le verrou. Un Lock
    simple provoquerait un interblocage — c'est la leçon `mt5_lock` de V12."""
    assert isinstance(v.mt5_lock, type(threading.RLock()))
    with v.mt5_lock:
        with v.mt5_lock:
            pass  # ne doit pas bloquer


def test_session_initialise_une_seule_fois(fake):
    m = fake(rates=rates(3))
    appels = []
    m.initialize = lambda: (appels.append(1), True)[1]
    with v.mt5_session():
        pass
    with v.mt5_session():
        pass
    assert len(appels) == 1


# ─────────────────────────────── compte ─────────────────────────────────────

def test_snapshot_demo(fake):
    fake(account=FakeAccountInfo(trade_mode=0, login=999))
    a = v.account_snapshot()
    assert a.is_demo is True
    assert a.trade_mode == 0
    assert a.login == 999


def test_snapshot_reel(fake):
    fake(account=FakeAccountInfo(trade_mode=2))
    a = v.account_snapshot()
    assert a.is_demo is False


def test_contest_nest_pas_demo(fake):
    """trade_mode=1 (concours) n'est pas un compte démo : seul 0 l'est."""
    fake(account=FakeAccountInfo(trade_mode=1))
    assert v.account_snapshot().is_demo is False


def test_account_info_vide_leve(fake):
    m = fake()
    m.account_info = lambda: None
    with pytest.raises(v.Mt5NotAvailableError):
        v.account_snapshot()


# ─────────────────────────────── symboles ───────────────────────────────────

def test_symbole_inconnu_leve_no_market_data(fake):
    fake(symbol=False)
    with pytest.raises(NoMarketDataError) as e:
        v.ensure_symbol("NEXISTEPAS")
    assert e.value.symbol == "NEXISTEPAS"


def test_specs_symbole(fake):
    fake()
    s = v.ensure_symbol("EURUSD")
    assert s.digits == 5
    assert s.volume_min == 0.01
    assert s.volume_step == 0.01
    assert s.tick_value == 1.0
    assert s.tick_size == 1e-5


def test_tick_value_absent_ne_casse_pas(fake):
    """Certains courtiers n'exposent pas trade_tick_value : on retombe sur 0.0,
    et c'est au calcul de lot de refuser — pas au vendeur de planter."""
    m = fake()
    info = FakeSymbolInfo()
    del info.trade_tick_value
    m.symbol_info = lambda name: info
    assert v.ensure_symbol("EURUSD").tick_value == 0.0


# ──────────────────────────────── bougies ───────────────────────────────────

def test_timeframe_inconnu_rejete(fake):
    fake(rates=rates(5))
    with pytest.raises(ValueError, match="timeframe"):
        v.get_rates("EURUSD", "H3", 10)


@pytest.mark.parametrize("count", [0, -1])
def test_count_non_positif_rejete(fake, count):
    fake(rates=rates(5))
    with pytest.raises(ValueError, match="count"):
        v.get_rates("EURUSD", "H4", count)


def test_bougie_en_cours_ecartee_par_defaut(fake):
    """closed_only=True est le défaut : décider sur une bougie non clôturée
    est l'erreur la plus commune en algo."""
    fake(rates=rates(6))
    df = v.get_rates("EURUSD", "H4", 5)
    assert len(df) == 5
    # la dernière bougie brute (index 5) doit avoir disparu
    assert df["close"].iloc[-1] == pytest.approx(1.1 + 4 * 0.001)


def test_bougie_en_cours_conservee_si_demande(fake):
    fake(rates=rates(6))
    df = v.get_rates("EURUSD", "H4", 6, closed_only=False)
    assert df["close"].iloc[-1] == pytest.approx(1.1 + 5 * 0.001)


def test_historique_vide_leve(fake):
    fake(rates=None)
    with pytest.raises(NoMarketDataError, match="aucune bougie"):
        v.get_rates("EURUSD", "H4", 10)


def test_une_seule_bougie_non_cloturee_leve(fake):
    """Une unique bougie en cours, écartée, ne laisse rien : c'est un manque
    de données, pas un DataFrame vide silencieux."""
    fake(rates=rates(1))
    with pytest.raises(NoMarketDataError, match="CLÔTURÉE"):
        v.get_rates("EURUSD", "H4", 5)


def test_index_est_en_utc(fake):
    fake(rates=rates(4))
    df = v.get_rates("EURUSD", "H4", 3)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing


# ───────────────────────────────── tick ─────────────────────────────────────

def test_tick(fake):
    fake()
    t = v.get_tick("EURUSD")
    assert t["bid"] == pytest.approx(1.1000)
    assert t["spread"] == pytest.approx(0.0002)
    assert t["time"].endswith("+00:00")


# ───────────────────────── intégration (terminal réel) ──────────────────────

pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
def test_terminal_reel_lecture():
    """Lecture seule sur le terminal réellement installé. Sauté s'il est fermé."""
    if not v.is_available():
        pytest.skip("terminal MT5 non joignable")
    a = v.account_snapshot()
    assert a.login > 0
    assert a.trade_mode in (0, 1, 2)
    df = v.get_rates("EURUSD", "H4", 10)
    assert len(df) == 10
    assert {"open", "high", "low", "close"}.issubset(df.columns)


# ── Reconnexion après redémarrage du terminal ────────────────────────────
#
# Défaut du 07/08/2026 : `_initialized` restait vrai après un redémarrage de
# MT5, et toutes les lectures échouaient ensuite en boucle sur « IPC send
# failed ». La boucle de trading mourait et ne repartait jamais — contraire
# au flux continu attendu.

def test_reinitialise_si_le_terminal_est_mort(fake):
    m = fake(rates=rates(3))
    appels = []
    m.initialize = lambda: (appels.append(1), True)[1]
    m.terminal_info = lambda: object()
    with v.mt5_session():
        pass
    # Le terminal disparaît.
    m.terminal_info = lambda: None
    with v.mt5_session():
        pass
    assert len(appels) == 2, "la session n'a pas été rétablie"


def test_ne_reinitialise_pas_si_vivant(fake):
    m = fake(rates=rates(3))
    appels = []
    m.initialize = lambda: (appels.append(1), True)[1]
    m.terminal_info = lambda: object()
    for _ in range(4):
        with v.mt5_session():
            pass
    assert len(appels) == 1


def test_sonde_absente_ne_fait_pas_thrasher(fake):
    """Sans `terminal_info`, on suppose vivant.

    Conclure « mort » d'une absence de réponse relancerait la session à
    chaque passage — or MT5 supporte mal les cycles initialize/shutdown
    rapprochés."""
    m = fake(rates=rates(3))
    appels = []
    m.initialize = lambda: (appels.append(1), True)[1]

    def boum():
        raise RuntimeError("binding sans terminal_info")
    m.terminal_info = boum
    for _ in range(3):
        with v.mt5_session():
            pass
    assert len(appels) == 1


# ── Volumes : uint64 et bouclage ─────────────────────────────────────────
#
# MT5 rend `tick_volume` et `real_volume` en uint64. Une soustraction
# negative y BOUCLE a 2**64 = 1.8446744e19 au lieu de rendre un negatif.
# Signale par Florent le 08/08/2026 dans les donnees enregistrees.
#
# Le danger n'est pas la valeur aberrante, qui se voit, mais sa
# contamination des statistiques en aval — dont l'analyse discriminante,
# qui conclurait sur une poignee de 1e19.

def test_volumes_convertis_en_flottant(fake):
    import numpy as np
    m = fake(rates=rates(5))
    brut = np.array(
        [(1_700_000_000, 1.0, 1.1, 0.9, 1.05, np.uint64(500), 2, np.uint64(50)),
         (1_700_000_900, 1.05, 1.2, 1.0, 1.15, np.uint64(300), 2, np.uint64(30))],
        dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
               ("close", "f8"), ("tick_volume", "u8"), ("spread", "i4"),
               ("real_volume", "u8")])
    m.copy_rates_from_pos = lambda *a, **k: brut
    df = v.get_rates("X", "M15", 2, closed_only=False)
    assert df["tick_volume"].dtype.kind == "f"
    assert df["real_volume"].dtype.kind == "f"


def test_une_baisse_de_volume_ne_boucle_pas(fake):
    """500 → 300 doit donner −200, jamais 1.8446744e19."""
    import numpy as np
    m = fake(rates=rates(5))
    brut = np.array(
        [(1_700_000_000, 1.0, 1.1, 0.9, 1.05, np.uint64(500), 2, np.uint64(0)),
         (1_700_000_900, 1.05, 1.2, 1.0, 1.15, np.uint64(300), 2, np.uint64(0))],
        dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
               ("close", "f8"), ("tick_volume", "u8"), ("spread", "i4"),
               ("real_volume", "u8")])
    m.copy_rates_from_pos = lambda *a, **k: brut
    df = v.get_rates("X", "M15", 2, closed_only=False)
    d = df["tick_volume"].diff().iloc[-1]
    assert d == -200.0, f"bouclage uint64 : {d}"


def test_aucune_valeur_proche_de_deux_puissance_64(fake):
    """Filet large : rien ne doit approcher 2**64 apres lecture."""
    import numpy as np
    m = fake(rates=rates(5))
    n = 30
    vols = np.array([1000 if i % 2 else 10 for i in range(n)], dtype="u8")
    brut = np.array(
        [(1_700_000_000 + i * 900, 1.0, 1.1, 0.9, 1.05, vols[i], 2,
          np.uint64(0)) for i in range(n)],
        dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"),
               ("close", "f8"), ("tick_volume", "u8"), ("spread", "i4"),
               ("real_volume", "u8")])
    m.copy_rates_from_pos = lambda *a, **k: brut
    df = v.get_rates("X", "M15", n, closed_only=False)
    for serie in (df["tick_volume"].diff(), df["tick_volume"].pct_change()):
        assert serie.abs().max() < 1e12
