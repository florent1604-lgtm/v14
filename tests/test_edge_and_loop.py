"""Tests de la mesure d'edge et de la boucle de gestion.

Deux exigences dominent :

* **« je ne sais pas » ≠ « c'est mauvais ».** Un contexte jamais mesuré rend
  ``None``, jamais ``False``. Les confondre ferait taire définitivement des
  contextes jamais essayés.
* **la boucle ne meurt jamais.** Une position ouverte dont le gestionnaire est
  mort, c'est un stop qui ne bougera plus.
"""

from __future__ import annotations

import importlib
import json

import pytest

from tradingagents import default_config as default_config_module

from titanium.edge import (
    EDGE_THRESHOLD_R,
    MIN_SAMPLES,
    PNL_R_MAX,
    ClosedTrade,
    Context,
    EdgeBook,
    TradeJournal,
    context_from_feats,
)
from titanium.execution.manage_loop import ManageLoop
from titanium.execution.mt5_executor import ExecutionPolicy


@pytest.fixture
def livre(tmp_path):
    """Fabrique un EdgeBook sur un journal NEUF à chaque appel.

    Un fichier partagé entre deux appels dans le même test ferait s'additionner
    les échantillons — piège dans lequel ce fixture est tombé au premier jet.
    """
    compteur = {"n": 0}

    def _make(trades=()):
        compteur["n"] += 1
        j = TradeJournal(tmp_path / f"trades_{compteur['n']}.ndjson")
        for t in trades:
            j.record(t)
        return EdgeBook(journal=j)
    return _make


def trade(ctx="GER40|long|continuation|3p", pnl=1.0, cout=0.1,
          exact_net=True) -> ClosedTrade:
    return ClosedTrade(
        context=ctx, pnl_r=pnl, cost_r=cout, exact_net=exact_net,
    )


CTX = Context("GER40", 1, "continuation", 3)


# ═════════════════ « inconnu » n'est pas « mauvais » ═════════════════════════

def test_contexte_jamais_mesure_est_inconnu(livre):
    v = livre().verdict_for(CTX)
    assert v.edge_ok is None, "un contexte jamais essayé ne doit pas être condamné"
    assert "jamais mesuré" in v.reason


def test_echantillon_insuffisant_est_inconnu(livre):
    """Même avec 19 trades gagnants, on ne conclut pas."""
    lb = livre([trade(pnl=2.0) for _ in range(MIN_SAMPLES - 1)])
    v = lb.verdict_for(CTX)
    assert v.edge_ok is None
    assert v.samples == MIN_SAMPLES - 1
    assert "insuffisant" in v.reason


def test_inconnu_bloque_en_prod():
    """La porte traite None comme un refus en PROD — mais l'information reste
    distincte d'un edge mesuré négatif."""
    from titanium.gates import confluence_gate
    f = {"data_valid": True, "trend": 1, "setup_side": 1,
         "setup_family": "continuation", "on_sr_level": True, "fair_value": True,
         "liquidity": 1, "ote": 1, "candle": 1, "strengths": {},
         "emotion": {"filter_block": None, "stale": False, "confidence": 0.9,
                     "wait": False},
         "cost": {"edge_ok": None, "weekend_block": False}}
    assert confluence_gate.evaluate(f, require_edge=True).code == "BLOCK_EDGE_UNPROVEN"


# ═══════════════════════════ verdicts mesurés ════════════════════════════════

def test_edge_positif_reconnu(livre):
    lb = livre([trade(pnl=1.0) for _ in range(MIN_SAMPLES)])
    v = lb.verdict_for(CTX)
    assert v.edge_ok is True
    assert v.expectancy_r == pytest.approx(1.0)


def test_edge_positif_non_prouve_reste_inconnu(livre):
    lb = livre([
        trade(pnl=1.0, exact_net=False) for _ in range(MIN_SAMPLES)
    ])
    v = lb.verdict_for(CTX)
    assert v.edge_ok is None
    assert "net comptable" in v.reason


def test_edge_negatif_reconnu(livre):
    lb = livre([trade(pnl=-0.5) for _ in range(MIN_SAMPLES)])
    v = lb.verdict_for(CTX)
    assert v.edge_ok is False
    assert v.expectancy_r < 0


def test_edge_negatif_non_prouve_reste_bloque(livre):
    lb = livre([
        trade(pnl=-0.5, exact_net=False) for _ in range(MIN_SAMPLES)
    ])
    assert lb.verdict_for(CTX).edge_ok is False


def test_cout_inconnu_ne_saffiche_pas_comme_gratuit(livre):
    v = livre([trade(cout=None) for _ in range(MIN_SAMPLES)]).verdict_for(CTX)
    assert v.mean_cost_r is None
    assert v.known_cost_rate == 0.0
    assert v.exact_cost_rate == 0.0


def test_couverture_des_couts_est_mesuree(livre):
    trades = [ClosedTrade(CTX.key(), 1.0, cost_r=0.2, exact_cost=True)]
    trades += [ClosedTrade(CTX.key(), 1.0, cost_r=None) for _ in range(3)]
    v = livre(trades).verdict_for(CTX)
    assert v.mean_cost_r == pytest.approx(0.2)
    assert v.known_cost_rate == pytest.approx(0.25)
    assert v.exact_cost_rate == pytest.approx(0.25)
    assert v.exact_net_rate == pytest.approx(0.25)


def test_esperance_nulle_ne_suffit_pas(livre):
    """Un contexte à espérance nulle ne paie pas le risque pris."""
    lb = livre([trade(pnl=0.0) for _ in range(MIN_SAMPLES)])
    assert lb.verdict_for(CTX).edge_ok is False


def test_winrate_eleve_mais_esperance_negative(livre):
    """LA leçon la plus chère de V12 : winrate élevé ≠ profit.
    18 gains de +0.2 R et 2 pertes de −2.5 R → 90 % de réussite, espérance
    négative. On mesure l'espérance, pas le taux de réussite."""
    trades = [trade(pnl=0.2) for _ in range(18)] + [trade(pnl=-2.5) for _ in range(2)]
    lb = livre(trades)
    v = lb.verdict_for(CTX)
    assert v.win_rate == pytest.approx(0.9)
    assert v.expectancy_r < 0
    assert v.edge_ok is False, "un winrate de 90 % ne doit pas ouvrir le PROD"


def test_seuil_respecte(livre):
    juste_sous = EDGE_THRESHOLD_R - 0.01
    assert livre([trade(pnl=juste_sous) for _ in range(MIN_SAMPLES)]
                 ).verdict_for(CTX).edge_ok is False
    assert livre([trade(pnl=EDGE_THRESHOLD_R) for _ in range(MIN_SAMPLES)]
                 ).verdict_for(CTX).edge_ok is True


def test_contextes_isoles(livre):
    """Un contexte porteur ne doit pas contaminer un contexte perdant."""
    trades = ([trade(ctx="A", pnl=1.0) for _ in range(MIN_SAMPLES)]
              + [trade(ctx="B", pnl=-1.0) for _ in range(MIN_SAMPLES)])
    lb = livre(trades)
    lb.refresh()
    assert lb._cache["A"].edge_ok is True
    assert lb._cache["B"].edge_ok is False


def test_resume_trie_par_esperance(livre):
    trades = ([trade(ctx="faible", pnl=0.1) for _ in range(MIN_SAMPLES)]
              + [trade(ctx="fort", pnl=2.0) for _ in range(MIN_SAMPLES)])
    resume = livre(trades).summary()
    assert resume[0].context == "fort"


# ═══════════════════════════ journal append-only ═════════════════════════════

def test_aller_retour(tmp_path):
    j = TradeJournal(tmp_path / "t.ndjson")
    j.record(trade(pnl=1.5, cout=0.2))
    relu = j.read_all()
    assert len(relu) == 1
    assert relu[0].pnl_r == 1.5
    assert relu[0].cost_r == 0.2
    assert relu[0].closed_at, "l'horodatage doit être posé automatiquement"


@pytest.mark.parametrize("valeur", ["false", 0, 1, None, [], {}])
def test_marqueur_exact_net_non_booleen_reste_fail_closed(tmp_path, valeur):
    f = tmp_path / "t.ndjson"
    f.write_text(json.dumps({
        "context": CTX.key(), "pnl_r": 1.0, "exact_net": valeur,
    }) + "\n", encoding="utf-8")
    trade_relu = TradeJournal(f).read_all()[0]
    assert trade_relu.exact_net is False


def test_taux_exact_compare_avant_arrondi(livre):
    trades = [trade(exact_net=True) for _ in range(17_999)]
    trades.extend(trade(exact_net=False) for _ in range(2_001))
    verdict = livre(trades).verdict_for(CTX)
    assert verdict.exact_net_rate == pytest.approx(0.9, abs=1e-4)
    assert verdict.edge_ok is None


def test_append_seulement(tmp_path):
    j = TradeJournal(tmp_path / "t.ndjson")
    for i in range(3):
        j.record(trade(pnl=float(i)))
    assert len(j.read_all()) == 3


def test_ligne_corrompue_sautee(tmp_path):
    """Un journal abîmé ne doit jamais empêcher de trader."""
    f = tmp_path / "t.ndjson"
    j = TradeJournal(f)
    j.record(trade(pnl=1.0))
    with f.open("a", encoding="utf-8") as fh:
        fh.write("{ceci n'est pas du json\n")
        fh.write(json.dumps({"context": "X"}) + "\n")          # pnl_r manquant
        fh.write(json.dumps({"context": "X", "pnl_r": "nan"}) + "\n")
    j.record(trade(pnl=2.0))
    relu = j.read_all()
    assert len(relu) == 2
    assert [t.pnl_r for t in relu] == [1.0, 2.0]


def test_pnl_r_fini_mais_implausible_est_rejete(tmp_path):
    """Une valeur finie a 10^8 R ne doit jamais ouvrir la porte PROD."""
    f = tmp_path / "t.ndjson"
    j = TradeJournal(f)
    j.record(trade(pnl=-0.5))
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "context": CTX.key(),
            "pnl_r": PNL_R_MAX + 1.0,
            "ticket": "corrompu",
        }) + "\n")

    relu = j.read_all()

    assert [t.pnl_r for t in relu] == [-0.5]
    assert j.rejected_lines == 1


def test_ligne_rejetee_bloque_un_edge_apparemment_positif(tmp_path):
    f = tmp_path / "t.ndjson"
    j = TradeJournal(f)
    for _ in range(MIN_SAMPLES):
        j.record(trade(pnl=1.0))
    with f.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "context": CTX.key(), "pnl_r": PNL_R_MAX + 1.0,
        }) + "\n")

    verdict = EdgeBook(journal=j).verdict_for(CTX)

    assert verdict.edge_ok is False
    assert "journal invalide" in verdict.reason


def test_journal_absent_est_vide(tmp_path):
    assert TradeJournal(tmp_path / "jamais.ndjson").read_all() == []


# ═══════════════════════ contexte depuis les features ════════════════════════

def test_contexte_compte_les_piliers_ALIGNES_pas_les_non_nuls():
    """Le contexte vient des PORTES, jamais de `strengths`.

    `strengths` mesure la non-nullité d'un pilier (`liquidity != 0`) ; la
    porte mesure son ALIGNEMENT (`liquidity == side`). Un setup à deux
    piliers, avec deux autres contre-alignés, était journalisé « 4p » et
    rangé parmi les setups les plus forts — rendant fausse toute mesure
    d'edge par nombre de piliers. Défaut relevé le 07/08/2026.
    """
    from titanium.edge import context_from_decision
    from titanium.gates.confluence_gate import GateResult

    decision = type("D", (), {
        "side": 1, "setup_family": "continuation",
        "gates": [
            GateResult("data_valid", True, ""),        # hors signature
            GateResult("trend_sr", True, ""),          # obligatoire, compté
            GateResult("fair_value", True, ""),        # pilier aligné
            GateResult("liquidity", False, ""),        # CONTRE-aligné
            GateResult("ote_ob", False, ""),           # CONTRE-aligné
            GateResult("candle_confirmed", True, ""),  # pilier aligné
        ],
    })()

    ctx = context_from_decision("GER40", decision)
    assert ctx.n_pillars == 3, "2 piliers alignés + trend_sr obligatoire"
    assert ctx.key() == "GER40|long|continuation|3p"


def test_contexte_ne_compte_pas_data_valid():
    """`data_valid` est un contrôle de validité, pas un pilier de setup."""
    from titanium.edge import context_from_decision
    from titanium.gates.confluence_gate import GateResult

    d = type("D", (), {"side": 1, "setup_family": "continuation",
                       "gates": [GateResult("data_valid", True, "")]})()
    assert context_from_decision("GER40", d).n_pillars == 0


def test_les_deux_constructions_concordent():
    """`context_from_feats` délègue à la porte : entretenir un second
    comptage le ferait diverger du premier."""
    from titanium.edge import context_from_decision
    from titanium.gates import confluence_gate

    feats = {"data_valid": True, "setup_side": 1, "trend": 1,
             "setup_family": "continuation", "on_sr_level": True,
             "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
             "strengths": {}}
    d = confluence_gate.evaluate(feats)
    assert (context_from_feats("GER40", feats).key()
            == context_from_decision("GER40", d).key())


def test_cle_de_contexte_distingue_le_sens():
    a = Context("GER40", 1, "continuation", 3).key()
    b = Context("GER40", -1, "continuation", 3).key()
    assert a != b


# ═════════════════════════ boucle de gestion ═════════════════════════════════

def test_boucle_desarmee_ne_touche_rien(tmp_path):
    b = ManageLoop(policy=ExecutionPolicy(), state_path=tmp_path / "s.json")
    r = b.tick()
    assert r["moved"] == 0
    assert b.stats["passages"] == 1


def test_un_passage_ne_leve_jamais(tmp_path, monkeypatch):
    """Même si tout casse, la boucle doit rendre un rapport."""
    import titanium.data.mt5_vendor as v
    monkeypatch.setattr(v, "mt5_session", lambda: (_ for _ in ()).throw(
        ConnectionError("terminal parti")))
    b = ManageLoop(policy=ExecutionPolicy(), state_path=tmp_path / "s.json")
    r = b.tick()
    assert "reason" in r
    assert r["moved"] == 0


def test_erreur_comptee(tmp_path, monkeypatch):
    import titanium.execution.manage_loop as ml
    monkeypatch.setattr(ml, "manage_once",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boum")))
    b = ManageLoop(policy=ExecutionPolicy(), state_path=tmp_path / "s.json")
    b.tick()
    assert b.stats["erreurs"] == 1


def test_demarrage_et_arret_propres(tmp_path):
    b = ManageLoop(interval=0.05, startup_delay=0.0, policy=ExecutionPolicy(),
                   state_path=tmp_path / "s.json")
    b.start()
    assert b.running
    b.stop()
    assert not b.running


def test_double_demarrage_sans_effet(tmp_path):
    b = ManageLoop(interval=0.05, startup_delay=0.0, policy=ExecutionPolicy(),
                   state_path=tmp_path / "s.json")
    b.start()
    premier = b._thread
    b.start()
    assert b._thread is premier, "ne pas démarrer deux threads sur le même verrou MT5"
    b.stop()


def test_arret_pendant_le_delai_de_demarrage(tmp_path):
    """`stop()` doit sortir même si la boucle n'a pas encore fait un passage."""
    b = ManageLoop(interval=99, startup_delay=99, policy=ExecutionPolicy(),
                   state_path=tmp_path / "s.json")
    b.start()
    b.stop(timeout=2.0)
    assert not b.running
    assert b.stats["passages"] == 0


def test_parametres_lus_dans_la_config(tmp_path, monkeypatch):
    """La boucle lit ses paramètres dans LA config, pas dans des valeurs en dur.

    Ce test affirmait ``expected_demo_login is not None`` sur un environnement
    non maîtrisé : la valeur venait du `.env` du poste, absent du dépôt. Il
    passait donc en local et échouait en CI, et surtout il ne vérifiait rien —
    il constatait que la machine du développeur était configurée. C'est le
    piège que `env_var_names()` existe pour fermer, et trois tests y avaient
    échappé.

    On repart d'un environnement propre, on injecte une valeur TÉMOIN, et on
    exige qu'elle ressorte à l'autre bout. C'est strictement plus fort que
    ``is not None`` : un `from_config` qui renverrait une constante passerait
    l'ancienne version et tombe sur celle-ci.

    Un ``skipif`` aurait rendu le test inerte plutôt que correct.
    """
    for cle in default_config_module.env_var_names():
        monkeypatch.delenv(cle, raising=False)
    monkeypatch.setenv("TITANIUM_DEMO_LOGIN", "99887766")
    importlib.reload(default_config_module)

    b = ManageLoop(state_path=tmp_path / "s.json")

    assert b.params.breakeven_r == 0.8
    assert b.policy.expected_demo_login == 99887766, (
        "la politique doit refleter la config, pas une valeur en dur"
    )


def test_login_absent_de_la_config_desarme(tmp_path, monkeypatch):
    """Sans login déclaré, la politique reste None — et donc le mur refuse.

    Corollaire du test précédent : c'est le cas que la CI rencontrait et que
    personne ne voyait. Il doit être un comportement attendu et verrouillé, pas
    un accident de configuration. Règle de conception V14 : une variable absente
    ou mal orthographiée DÉSARME, elle n'arme jamais.
    """
    for cle in default_config_module.env_var_names():
        monkeypatch.delenv(cle, raising=False)
    importlib.reload(default_config_module)

    b = ManageLoop(state_path=tmp_path / "s.json")

    assert b.policy.expected_demo_login is None
    assert not b.policy.enabled
