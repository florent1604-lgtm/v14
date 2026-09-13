from __future__ import annotations

import contextlib
import sys
import threading
import time
from types import SimpleNamespace

from titanium.analysis import discriminants as analyse_discriminants
from titanium.web import state


def test_meta_affiche_hermes_comme_cortex_principal(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_config", lambda: {
        "llm_provider": "ollama",
        "deep_think_llm": "qwen2.5:7b",
        "quick_think_llm": "qwen2.5:3b",
        "results_dir": tmp_path,
    })

    result = state.meta()

    assert result["provider"] == "ollama-local"
    assert result["deep_model"] == "qwen3.5:2b"
    assert result["cortex_primary"] == "hermes-cortex/qwen3.5:2b"
    assert result["cortex_mode"] == "async_advisory"
    assert result["fallback_provider"] == "ollama"
    assert result["fallback_model"] == "qwen2.5:7b"


def test_scan_traite_la_crypto_comme_un_marche_continu(monkeypatch, tmp_path):
    from titanium import edge, orchestrator
    from titanium.data import mt5_vendor
    from titanium.features import builder
    from titanium.gates import confluence_gate

    vus = []
    monkeypatch.setattr(state, "_config", lambda: {"results_dir": tmp_path / "runs"})
    monkeypatch.setattr(state, "account", lambda: {"equity": 1000.0, "currency": "EUR"})
    monkeypatch.setattr(mt5_vendor, "get_rates", lambda *_args, **_kwargs: object())

    def construire(*_args, **kwargs):
        vus.append(kwargs.get("marche_continu"))
        return {"_trace": {}, "setup_side": 0, "setup_family": "", "trend": 0}

    monkeypatch.setattr(builder, "build_feats", construire)
    monkeypatch.setattr(builder, "risk_context_from", lambda *_a, **_k: {})
    decision = SimpleNamespace(gates=[])
    monkeypatch.setattr(confluence_gate, "evaluate", lambda *_a, **_k: decision)
    sortie = SimpleNamespace(
        gate_verdict="WAIT", gate_code="WAIT_NO_SETUP", reason="WAIT_NO_SETUP",
        stopped_at="gates", risk_verdict="", risk_money=0.0,
        stop_distance=None, conviction=0.0, trace=[],
    )
    monkeypatch.setattr(orchestrator, "run_once", lambda *_a, **_k: sortie)
    monkeypatch.setattr(edge, "context_from_feats", lambda *_a, **_k: "ctx")
    monkeypatch.setattr(
        edge.EdgeBook,
        "verdict_for",
        lambda *_a, **_k: SimpleNamespace(
            edge_ok=None, samples=0, expectancy_r=0.0, reason="inconnu",
        ),
    )

    state.scan(["BTCUSD", "EURUSD"])

    assert vus == [True, False]


def test_discriminants_ne_bloque_pas_le_dashboard(tmp_path, monkeypatch):
    source = tmp_path / "excursions.ndjson"
    source.write_text("preuve\n", encoding="utf-8")
    monkeypatch.setattr(state, "_config", lambda: {"results_dir": tmp_path / "runs"})
    monkeypatch.setattr(analyse_discriminants, "depuis_journal", lambda _p: [object()])

    libere = threading.Event()

    def analyser(_ech, *, n_permutations):
        assert n_permutations == 300
        assert libere.wait(timeout=2)
        indicateur = SimpleNamespace(to_dict=lambda: {"nom": "rsi"})
        return SimpleNamespace(
            suffisant=True,
            n_trades=1,
            n_gagnants=1,
            n_perdants=0,
            message="ok",
            discriminants=[indicateur],
        )

    monkeypatch.setattr(analyse_discriminants, "analyser", analyser)
    monkeypatch.setattr(state, "_DISCRIMINANTS_CACHE", {})
    monkeypatch.setattr(state, "_DISCRIMINANTS_BUILDING", False)

    debut = time.perf_counter()
    premier = state.discriminants()
    assert time.perf_counter() - debut < 0.5
    assert premier["pending"] is True

    libere.set()
    limite = time.monotonic() + 2
    while state._DISCRIMINANTS_BUILDING and time.monotonic() < limite:
        time.sleep(0.01)

    second = state.discriminants()
    assert second["pending"] is False
    assert second["top"] == [{"nom": "rsi"}]


# ─────────── flottant affiché : le portage compte autant que le profit ───────
#
# MT5 sépare ``profit`` et ``swap`` ; l'équité du compte, elle, les additionne.
# Mesuré le 12/09/2026 sur le compte démo : equity - balance = profit + swap au
# centime, avec un poste à +6,38 de brut portant -45,25 de swap. Un panneau qui
# n'affiche que le brut est optimiste de 45 EUR sur ce seul poste.


def _position(**champs):
    base = {
        "magic": 14000, "ticket": 1, "symbol": "DAX40.fs", "type": 0,
        "volume": 0.01, "price_open": 25540.0, "price_current": 25565.5,
        "sl": 25443.93, "tp": 25732.14, "profit": 6.38, "swap": 0.0,
    }
    base.update(champs)
    return SimpleNamespace(**base)


def _courtier(monkeypatch, tmp_path, positions, *, leve=None):
    """Branche ``state.positions()`` sur un faux courtier, sans MT5 réel."""
    from titanium.data import mt5_vendor
    from titanium.execution import mt5_executor, position_manager

    monkeypatch.setattr(state, "_config",
                        lambda: {"results_dir": tmp_path / "results"})
    monkeypatch.setattr(mt5_executor.ExecutionPolicy, "from_config",
                        classmethod(lambda cls, _c: SimpleNamespace(magic=14000)))
    monkeypatch.setattr(position_manager.ManageParams, "from_config",
                        classmethod(lambda cls, _c: SimpleNamespace()))
    monkeypatch.setattr(position_manager, "load_state", lambda _p: {})
    monkeypatch.setattr(mt5_vendor, "mt5_lock", contextlib.nullcontext())
    monkeypatch.setattr(mt5_vendor, "mt5_session",
                        lambda: contextlib.nullcontext())

    def positions_get():
        if leve is not None:
            raise leve
        return positions

    monkeypatch.setitem(sys.modules, "MetaTrader5", SimpleNamespace(
        positions_get=positions_get, orders_get=lambda: (),
    ))


def _enveloppe(donnees, *, erreur=None, stale=False):
    return {"data": donnees, "erreur": erreur, "jamais_lu": False,
            "stale": stale, "age_s": 1}


def _rendre(contexte):
    from titanium.web.dashboard_app import gabarits

    return gabarits.env.get_template("components/positions.html").render(**contexte)


def test_le_flottant_affiche_inclut_le_portage(monkeypatch, tmp_path):
    """Le total publié est profit + swap, jamais la somme des profits bruts."""
    _courtier(monkeypatch, tmp_path, [
        _position(ticket=1, profit=6.38, swap=-45.25),
        _position(ticket=2, symbol="XLMUSD", profit=-4.10, swap=0.0),
    ])

    d = state.positions()

    assert [ligne["net"] for ligne in d["positions"]] == [-38.87, -4.10]
    assert d["portage_total"] == -45.25
    assert d["net_total"] == -42.97
    # La preuve que le défaut est fermé : le brut donnait l'espoir inverse.
    assert round(sum(ligne["profit"] for ligne in d["positions"]), 2) == 2.28
    assert d["net_total"] != 2.28


def test_un_portage_absent_ou_nul_vaut_zero(monkeypatch, tmp_path):
    """Borne : un poste sans ``swap`` (ou à ``None``) ne fait pas échouer la carte."""
    sans_swap = _position(ticket=1)
    del sans_swap.swap
    _courtier(monkeypatch, tmp_path, [sans_swap, _position(ticket=2, swap=None)])

    d = state.positions()

    assert [ligne["swap"] for ligne in d["positions"]] == [0.0, 0.0]
    assert d["net_total"] == 12.76


def test_aucune_position_rend_un_total_nul_pas_une_erreur(monkeypatch, tmp_path):
    """Entrée vide : ``positions_get`` peut rendre ``None`` sans poste ouvert."""
    _courtier(monkeypatch, tmp_path, None)

    d = state.positions()

    assert d["positions"] == []
    assert d["net_total"] == 0.0
    assert d["portage_total"] == 0.0


def test_lecture_impossible_ne_rend_pas_zero(monkeypatch, tmp_path):
    """Un flottant illisible vaut ``None``, jamais ``0.0`` : la carte se tait."""
    _courtier(monkeypatch, tmp_path, [], leve=RuntimeError("MT5 injoignable"))

    d = state.positions()

    assert d["positions"] == []
    assert d["net_total"] is None
    assert d["portage_total"] is None


def test_la_carte_affiche_le_net_et_le_portage(monkeypatch, tmp_path):
    """Point d'entrée réel : le gabarit rend un net différent du brut."""
    donnees = {
        "positions": [{"ticket": "1", "symbol": "DAX40.fs", "side": 1,
                       "volume": 0.01, "entry": 25540.0, "current": 25565.5,
                       "sl": 25443.93, "tp": 25732.14, "profit": 6.38,
                       "swap": -45.25, "net": -38.87, "phase": "init",
                       "fav_r": 0.265}],
        "pending": [], "net_total": -38.87, "portage_total": -45.25,
    }

    rendu = _rendre({"b": _enveloppe(donnees),
                     "intentions": {"en_attente": [], "peremption_s": 900},
                     "tickets_en_attente": set()})

    assert "-38.87" in rendu, "la cellule doit porter le net, pas le brut"
    assert "+6.38" not in rendu
    assert "portage -45.25" in rendu
    assert "Flottant net des positions V14" in rendu


def test_la_carte_tolere_une_charge_anterieure_au_portage():
    """Un processus en cours sert l'ancienne charge : le fragment ne casse pas.

    Le lecteur met cinq secondes en cache, et `state.py` n'est rechargé qu'au
    redémarrage : entre l'écriture du gabarit et celle du processus, une charge
    sans ``net`` ni ``swap`` doit continuer de s'afficher.
    """
    ancienne = {
        "positions": [{"ticket": "1", "symbol": "DAX40.fs", "side": 1,
                       "volume": 0.01, "entry": 25540.0, "current": 25565.5,
                       "sl": None, "tp": None, "profit": 6.38,
                       "phase": "init", "fav_r": None}],
        "pending": [],
    }

    rendu = _rendre({"b": _enveloppe(ancienne),
                     "intentions": {"en_attente": [], "peremption_s": 900},
                     "tickets_en_attente": set()})

    assert "+6.38" in rendu
    assert "Flottant net" not in rendu, "pas de total sans clé : on ne l'invente pas"
    assert "portage" not in rendu


def test_la_carte_se_rend_sans_contexte_dintentions():
    """La route du tableau de bord ne passe pas ``intentions`` : le gabarit tient."""
    rendu = _rendre({"b": _enveloppe({"positions": [], "pending": []})})

    assert "Aucune position ouverte." in rendu
