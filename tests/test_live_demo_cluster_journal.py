"""Journal additif des grappes pour chaque candidat ENTER."""

from __future__ import annotations

import inspect
import json
import sys
from types import SimpleNamespace

import tools.live_demo as live


class _Grappes:
    par_actif = {"EURUSD": "g7", "GBPUSD": "g7", "USTECH": "g7"}
    membres = {"g7": ["EURUSD", "GBPUSD"]}

    @staticmethod
    def grappe_de(_symbol):
        return "g7"


def _candidate():
    return {
        "sym": "EURUSD",
        "feats": {"_trace": {"bar_time": "2026-08-11T19:00:00Z",
                              "timeframe": "H1"}},
        "out": SimpleNamespace(side=1, risk_money=25.0),
        "dec": SimpleNamespace(setup_family="continuation"),
        "support": 3,
        "rank": 0.72,
    }


def test_cluster_journal_is_append_only_and_deduplicated(monkeypatch, tmp_path):
    import titanium.correlation as correlation

    output = tmp_path / "candidats_grappe.ndjson"
    monkeypatch.setattr(live, "CANDIDATS_GRAPPE", output)
    monkeypatch.setattr(live, "_GRAPPES", _Grappes())
    monkeypatch.setattr(live, "_CANDIDATS_GRAPPE_VUS", set())
    monkeypatch.setattr(correlation, "risque_par_grappe",
                        lambda *_args, **_kwargs: {"g7": 1.25})

    assert live._journaliser_grappes([_candidate()], 5_000.0) == 1
    assert live._journaliser_grappes([_candidate()], 5_000.0) == 0

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["candidate_key"] == "EURUSD:H1:2026-08-11T19:00:00Z"
    assert rows[0]["cluster"] == "g7"
    assert rows[0]["cluster_risk_engaged_pct"] == 1.25
    assert rows[0]["proposed_risk_pct"] == 0.5
    assert rows[0]["setup_family"] == "continuation"


def test_journal_de_grappes_survit_a_l_absence_de_metatrader(monkeypatch, tmp_path):
    """La mesure doit fonctionner sur une machine sans le paquet MetaTrader5.

    L'import etait inconditionnel alors que l'usage est conditionne a
    ``_GRAPPES`` : sans le paquet, l'ImportError etait avalee par le ``except``
    de fin et la fonction rendait 0. La mesure disparaissait en silence, y
    compris la part qui n'a besoin d'aucun courtier — et c'est exactement ce
    que la CI Linux a revele le 18/08/2026.

    Ce test bloque l'import au niveau du ``meta_path``, ce qui reproduit un
    runner sans MT5 sans dependre de la plateforme : il echoue donc aussi sous
    Windows si la regression revient.
    """
    import sys
    from types import SimpleNamespace

    import tools.live_demo as live

    class _SansMetaTrader:
        def find_spec(self, nom, chemin=None, cible=None):
            if nom == "MetaTrader5":
                raise ImportError("No module named 'MetaTrader5'")
            return None

    monkeypatch.setattr(live, "CANDIDATS_GRAPPE", tmp_path / "candidats.ndjson")
    monkeypatch.setattr(live, "_CANDIDATS_GRAPPE_VUS", set())
    monkeypatch.setattr(live, "_GRAPPES", None)
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)
    monkeypatch.delitem(sys.modules, "MetaTrader5", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_SansMetaTrader(), *sys.meta_path])

    candidats = [{
        "sym": "EURUSD",
        "feats": {"_trace": {"bar_time": "2026-08-11T19:00:00Z", "timeframe": "H1"}},
        "out": SimpleNamespace(side=1, risk_money=25.0),
        "dec": SimpleNamespace(setup_family="continuation"),
    }]

    assert live._journaliser_grappes(candidats, 5000.0) == 1, (
        "sans MetaTrader5, la mesure de grappe doit encore etre ecrite"
    )
    assert (tmp_path / "candidats.ndjson").exists()


def test_garde_grappe_refuse_sans_arbre(monkeypatch):
    """Une porte de risque absente n'est jamais equivalente a un portefeuille vide."""
    monkeypatch.setattr(live, "_GRAPPES", None)
    ok, motif = live._place_dans_la_grappe("USTECH", 0.5)
    assert not ok
    assert "indisponibles" in motif.lower()


def test_garde_grappe_refuse_si_terminal_en_erreur(monkeypatch):
    monkeypatch.setattr(live, "_GRAPPES", _Grappes())

    import titanium.correlation as correlation

    monkeypatch.setattr(
        correlation,
        "place_disponible",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("terminal muet")),
    )
    ok, motif = live._place_dans_la_grappe("USTECH", 0.5)
    assert not ok
    assert "ERREUR_RISQUE_CORRELE" in motif


def test_garde_grappe_refuse_un_symbole_absent_du_cache(monkeypatch):
    from titanium.correlation import Grappes

    monkeypatch.setattr(
        live,
        "_GRAPPES",
        Grappes(par_actif={"US500": "g1"}, membres={"g1": ["US500"]}),
    )

    ok, motif = live._place_dans_la_grappe("NOUVEAU", 0.2)

    assert not ok
    assert motif == "GRAPPE_SYMBOLE_ABSENT: NOUVEAU"


def test_lot_minimum_revalide_alias_avec_risque_effectif(monkeypatch):
    """0,5 % effectif doit etre teste, meme si l'intention valait 0,2 %."""
    from titanium.correlation import Grappes
    from titanium.data import mt5_vendor

    position = SimpleNamespace(
        symbol="NAS100.fs", sl=1.099, price_open=1.100, volume=1.6,
    )
    specification = SimpleNamespace(
        trade_tick_size=0.00001, trade_tick_value=1.0,
    )
    faux_mt5 = SimpleNamespace(
        positions_get=lambda: [position],
        symbol_info=lambda _symbole: specification,
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", faux_mt5)
    monkeypatch.setattr(
        mt5_vendor, "account_snapshot",
        lambda: SimpleNamespace(equity=10_000.0),
    )
    monkeypatch.setattr(
        live, "_GRAPPES",
        Grappes(
            par_actif={"NAS100.FS": "g1", "USTECH": "g2"},
            membres={"g1": ["NAS100.FS"], "g2": ["USTECH"]},
        ),
    )
    budget_lot_min = SimpleNamespace(
        tradable=True, at_min_lot=True, effective_pct=0.5,
    )

    ok, motif = live._revalider_grappe_apres_sizing(
        "USTECH", budget_lot_min,
    )

    assert not ok
    assert "sous-jacent US_NASDAQ_100" in motif
    assert "1.60 %" in motif


def test_gate_correle_unique_utilise_le_risque_post_sizing_avant_ordre():
    source = inspect.getsource(live.tour)
    budget = source.index("budget = budget_for")
    non_tradable = source.index("if not budget.tradable", budget)
    gate_effectif = source.index("_revalider_grappe_apres_sizing")
    ordre = source.index("res = place_limit_order")

    assert budget < non_tradable < gate_effectif < ordre
    assert "_place_dans_la_grappe(sym, conf.pct)" not in source


def test_revalidation_post_sizing_refuse_un_risque_effectif_invalide(monkeypatch):
    monkeypatch.setattr(
        live, "_place_dans_la_grappe",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("la porte aval ne doit pas recevoir un risque invalide")
        ),
    )
    for valeur in (None, 0.0, float("nan")):
        budget = SimpleNamespace(tradable=True, effective_pct=valeur)
        ok, motif = live._revalider_grappe_apres_sizing("USTECH", budget)
        assert not ok
        assert "RISQUE_EFFECTIF" in motif
