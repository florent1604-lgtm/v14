"""Tests du panel d'indicateurs et de l'analyse discriminante.

Ces deux modules servent à MESURER, jamais à décider. Les tests protègent donc
deux choses différentes :

* le **panel** doit produire des valeurs comparables entre actifs et ne jamais
  influencer un verdict ;
* l'**analyse** doit rejeter le bruit. C'est sa seule raison d'être : sans
  correction pour tests multiples, tester 100 indicateurs sur 200 trades sort
  cinq « découvertes » par pur hasard.
"""

from __future__ import annotations

import random

import numpy as np
import pandas as pd
import pytest

from titanium.analysis.discriminants import (
    DELTA_NEGLIGEABLE,
    MIN_PAR_GROUPE,
    analyser,
    cliff_delta,
    depuis_journal,
)
from titanium.features import indicators
from titanium.features.builder import build_feats


def bougies(n=600, freq="15min", graine=5) -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp("2026-08-07 04:30", tz="UTC"),
                        periods=n, freq=freq)
    rng = np.random.default_rng(graine)
    base = np.cumsum(rng.normal(0, 0.3, n)) + 100
    return pd.DataFrame({
        "open": base, "high": base + 0.4, "low": base - 0.4,
        "close": base + 0.05, "tick_volume": rng.integers(80, 400, n).astype(float),
    }, index=idx)


def echantillon(n=200, effet=1.2, n_bruits=6, graine=7):
    """Jeu à vérité connue : un signal réel, plusieurs bruits purs."""
    rng = random.Random(graine)
    out = []
    for i in range(n):
        gagnant = i % 2 == 0
        panel = {"vrai": rng.gauss(effet if gagnant else 0.0, 1.0)}
        for b in range(n_bruits):
            panel[f"bruit{b}"] = rng.gauss(0, 1)
        out.append((panel, 1.0 if gagnant else -1.0))
    return out


# ════════════════ le panel ne décide jamais ═════════════════════════════════

def test_panel_desactive_par_defaut():
    """Un calcul de 100 séries à chaque balayage coûterait cher pour rien."""
    f = build_feats(bougies(), bougies(freq="4h"))
    assert f["_trace"]["indicators"] == {}


def test_panel_actif_sur_demande():
    f = build_feats(bougies(), bougies(freq="4h"), with_indicators=True)
    assert len(f["_trace"]["indicators"]) > 40


def test_le_panel_ne_change_aucun_verdict():
    """LE test qui garantit que la mesure reste une mesure."""
    from titanium.gates import confluence_gate
    ltf, htf = bougies(), bougies(freq="4h")
    sans = confluence_gate.evaluate(build_feats(ltf, htf))
    avec = confluence_gate.evaluate(build_feats(ltf, htf, with_indicators=True))
    assert sans.verdict == avec.verdict
    assert sans.code == avec.code
    assert sans.side == avec.side


def test_aucune_porte_ne_lit_le_panel():
    """Garde-fou structurel : si un jour une porte lit `indicators`, ce test casse."""
    from pathlib import Path
    for module in ("titanium/gates/confluence_gate.py", "titanium/risk/riskgate.py"):
        src = Path(module).read_text(encoding="utf-8")
        assert "indicators" not in src, f"{module} ne doit pas lire le panel"


# ════════════════ normalisation : comparable entre actifs ═══════════════════

def test_oscillateurs_bornes_repris_tels_quels():
    p = indicators.calculer(bougies(), prefixe="ltf_")
    rsi = p.get("ltf_rsi_14")
    assert rsi is not None and 0 <= rsi <= 100


def test_niveaux_convertis_en_ecart_atr():
    """Un prix brut n'est pas comparable entre EURUSD et un indice."""
    p = indicators.calculer(bougies(), prefixe="ltf_")
    assert "ltf_close_50_sma_atr" in p
    assert "ltf_close_50_sma" not in p, "le niveau brut ne doit pas fuiter"
    assert abs(p["ltf_close_50_sma_atr"]) < 100


def test_volatilite_en_pourcentage():
    p = indicators.calculer(bougies(), prefixe="ltf_")
    assert "ltf_atr_14_pct" in p
    assert 0 < p["ltf_atr_14_pct"] < 100


def test_echelles_differentes_donnent_des_valeurs_comparables():
    """Le même marché à deux échelles de prix doit produire des indicateurs
    normalisés proches — c'est tout l'objet de la normalisation."""
    petit = bougies()
    grand = petit * 100.0          # même forme, prix ×100
    a = indicators.calculer(petit)
    b = indicators.calculer(grand)
    assert a["rsi_14"] == pytest.approx(b["rsi_14"], abs=.5)
    assert a["close_50_sma_atr"] == pytest.approx(b["close_50_sma_atr"], abs=.05)
    assert a["atr_14_pct"] == pytest.approx(b["atr_14_pct"], rel=.02)


def test_deux_timeframes_prefixes():
    p = indicators.panel(bougies(), bougies(freq="4h"))
    assert any(k.startswith("ltf_") for k in p)
    assert any(k.startswith("htf_") for k in p)


@pytest.mark.parametrize("entree", [None, pd.DataFrame(), bougies(5)])
def test_donnees_insuffisantes_rendent_un_panel_vide(entree):
    assert indicators.calculer(entree) == {}


def test_panel_ne_leve_jamais():
    assert indicators.panel(None, None) == {}
    assert indicators.panel(bougies(3), None) == {}


# ════════════════ delta de Cliff ════════════════════════════════════════════

def test_delta_separation_totale():
    assert cliff_delta([5, 6, 7], [1, 2, 3]) == pytest.approx(1.0)
    assert cliff_delta([1, 2, 3], [5, 6, 7]) == pytest.approx(-1.0)


def test_delta_nul_si_identiques():
    assert cliff_delta([1, 2, 3], [1, 2, 3]) == pytest.approx(0.0)


def test_delta_insensible_aux_valeurs_extremes():
    """Une moyenne se ferait retourner par un seul trade aberrant ; Cliff non."""
    a, b = [5, 6, 7], [1, 2, 3]
    normal = cliff_delta(a, b)
    avec_aberrant = cliff_delta(a, [1, 2, 10_000])
    assert normal == pytest.approx(1.0)
    assert avec_aberrant > 0.3, "un seul extrême ne doit pas inverser le verdict"


def test_delta_sur_groupes_vides():
    assert cliff_delta([], [1, 2]) == 0.0
    assert cliff_delta([1, 2], []) == 0.0


# ════════════════ l'analyse rejette le bruit ════════════════════════════════

def test_le_vrai_signal_est_retenu():
    r = analyser(echantillon(), n_permutations=600)
    assert r.suffisant
    assert "vrai" in [d.nom for d in r.retenus]


def test_aucun_bruit_nest_retenu():
    """LA raison d'être du module. Sans correction, ~5 % des bruits passeraient."""
    r = analyser(echantillon(), n_permutations=600)
    bruits = [d.nom for d in r.retenus if d.nom.startswith("bruit")]
    assert bruits == [], f"bruit retenu à tort : {bruits}"


def test_la_correction_change_le_verdict():
    """On documente que le p brut seul serait trompeur."""
    r = analyser(echantillon(n_bruits=12, graine=3), n_permutations=600)
    presque = [d for d in r.discriminants
               if d.nom.startswith("bruit") and d.p_brut < 0.10]
    for d in presque:
        assert d.p_corrige > d.p_brut
        assert not d.retenu, "un bruit à p brut < 0.10 ne doit pas survivre à BH"


def test_effet_negligeable_rejete_meme_si_significatif():
    """Sur un gros échantillon, tout devient significatif. Seule la taille
    d'effet dit si c'est utile."""
    r = analyser(echantillon(n=600, effet=0.05, n_bruits=2), n_permutations=400)
    for d in r.retenus:
        assert abs(d.delta) >= DELTA_NEGLIGEABLE


def test_echantillon_insuffisant_refuse_de_conclure():
    r = analyser(echantillon(n=20), n_permutations=200)
    assert not r.suffisant
    assert r.retenus == []
    assert "insuffisant" in r.message


def test_le_plancher_est_par_groupe_pas_au_total():
    """80 trades mais 5 gagnants : insuffisant, même si le total paraît correct."""
    ech = [({"x": 1.0}, 1.0)] * 5 + [({"x": 0.0}, -1.0)] * 75
    assert not analyser(ech, n_permutations=100).suffisant


def test_resultat_reproductible():
    """Graine fixée : deux analyses des mêmes données doivent coïncider."""
    e = echantillon()
    a = analyser(e, n_permutations=400)
    b = analyser(e, n_permutations=400)
    assert [d.p_brut for d in a.discriminants] == [d.p_brut for d in b.discriminants]


def test_p_value_jamais_nulle():
    """Un p de 0 exact serait un artefact du nombre fini de permutations."""
    r = analyser(echantillon(effet=6.0), n_permutations=300)
    assert all(d.p_brut > 0 for d in r.discriminants)


def test_donnees_vides_ne_levent_pas():
    r = analyser([])
    assert not r.suffisant
    assert r.n_trades == 0


def test_valeurs_non_finies_ignorees():
    ech = echantillon(n=120)
    ech[0][0]["vrai"] = float("nan")
    ech[1][0]["vrai"] = float("inf")
    r = analyser(ech, n_permutations=300)
    vrai = next(d for d in r.discriminants if d.nom == "vrai")
    assert vrai.n_gagnants + vrai.n_perdants <= 118


def test_message_rappelle_que_ce_sont_des_hypotheses():
    r = analyser(echantillon(), n_permutations=400)
    assert "HYPOTHÈSES" in r.message or "Aucun indicateur" in r.message


# ════════════════ lecture du journal ════════════════════════════════════════

def test_journal_absent(tmp_path):
    assert depuis_journal(tmp_path / "rien.ndjson") == []


def test_journal_ignore_les_lignes_sans_panel(tmp_path):
    import json
    f = tmp_path / "t.ndjson"
    f.write_text(
        json.dumps({"pnl_r": 1.0}) + "\n"                          # sans panel
        + json.dumps({"pnl_r": 1.0, "indicators": {"a": 1}}) + "\n"
        + "{ligne cassée\n"
        + json.dumps({"indicators": {"a": 2}}) + "\n",             # sans résultat
        encoding="utf-8")
    assert len(depuis_journal(f)) == 1
