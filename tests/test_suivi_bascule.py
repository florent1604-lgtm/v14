"""Une bascule se juge sur des trades clos, jamais sur une intention.

Ces tests fixent les trois precautions du suivi : plancher d'effectif, temoin
hors FX, et ecart compare a son erreur type.
"""
from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "suivi_bascule.py"
    spec = importlib.util.spec_from_file_location("suivi_bascule", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["suivi_bascule"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _module()


BASCULE = datetime(2026, 8, 24, 6, 20, tzinfo=timezone.utc)


def _trade(module, *, jour: int, heure: int, r: float, classe: str = "indices",
           famille: str = "continuation") -> dict:
    return {
        "closed_at": datetime(2026, 8, jour, heure, tzinfo=timezone.utc),
        "pnl_r": r, "asset_class": classe, "famille": famille,
        "context": f"SYM|long|{famille}|3p", "cost_r": 0.05,
    }


def test_sous_le_plancher_aucun_verdict(module):
    trades = [_trade(module, jour=23, heure=1, r=-1.0) for _ in range(50)]
    trades += [_trade(module, jour=24, heure=8, r=+1.0) for _ in range(5)]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["apres"]["n"] == 5
    assert rapport["global"]["ecart"]["verdict"] == "INDECIS"


def test_une_hausse_franche_est_nommee(module):
    trades = [_trade(module, jour=23, heure=1, r=-1.0) for _ in range(60)]
    trades += [_trade(module, jour=24, heure=8, r=+1.0) for _ in range(60)]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["ecart"]["verdict"] == "HAUSSE"
    assert rapport["global"]["ecart"]["delta_r"] == pytest.approx(2.0)


def test_un_ecart_dans_le_bruit_n_est_pas_une_hausse(module):
    r_avant = [-1.0, 1.0] * 30
    r_apres = [-1.0, 1.0] * 30
    r_apres[0] = 1.02
    trades = [_trade(module, jour=23, heure=1, r=r) for r in r_avant]
    trades += [_trade(module, jour=24, heure=8, r=r) for r in r_apres]
    rapport = module.comparer(trades, BASCULE)
    assert rapport["global"]["ecart"]["verdict"] == "INDISTINGUABLE"


def test_le_temoin_hors_fx_ignore_le_fx(module):
    trades = [_trade(module, jour=23, heure=1, r=-2.0, classe="fx")
              for _ in range(40)]
    trades += [_trade(module, jour=23, heure=2, r=+0.10) for _ in range(40)]
    trades += [_trade(module, jour=24, heure=8, r=+0.10) for _ in range(40)]
    rapport = module.comparer(trades, BASCULE)
    # Le global monte parce que le FX disparait...
    assert rapport["global"]["ecart"]["verdict"] == "HAUSSE"
    # ...mais le temoin, lui, ne bouge pas : c'est ce qui prouve que la hausse
    # vient de la suspension et non du marche.
    assert rapport["hors_fx"]["ecart"]["verdict"] == "INDISTINGUABLE"


def test_la_famille_est_lue_dans_la_cle_de_contexte(module):
    trades = [_trade(module, jour=24, heure=8, r=+1.0, famille="reversal")
              for _ in range(3)]
    rapport = module.comparer(trades, BASCULE)
    assert set(rapport["par_famille"]) == {"reversal"}


def test_une_ligne_illisible_est_ignoree_pas_fatale(module, tmp_path):
    journal = tmp_path / "trades.ndjson"
    journal.write_text("\n".join([
        json.dumps({"closed_at": "2026-08-24T08:00:00+00:00", "pnl_r": 1.0,
                    "asset_class": "indices", "context": "A|long|continuation|3p"}),
        "{ ceci n est pas du json",
        json.dumps({"closed_at": "pas une date", "pnl_r": 1.0}),
        json.dumps({"closed_at": "2026-08-24T09:00:00+00:00", "pnl_r": "NaN"}),
    ]), encoding="utf-8")
    trades = module.charger(journal)
    assert len(trades) == 1 and trades[0]["famille"] == "continuation"


def test_le_plancher_est_le_seul_declencheur_de_publication(module):
    trades = [_trade(module, jour=24, heure=8, r=+1.0) for _ in range(19)]
    rapport = module.comparer(trades, BASCULE, effectif_min=20)
    assert module.plancher_atteint(rapport) is False
    trades.append(_trade(module, jour=24, heure=9, r=+1.0))
    rapport = module.comparer(trades, BASCULE, effectif_min=20)
    assert module.plancher_atteint(rapport) is True
# --- le prix de la preuve -------------------------------------------------
#
# Le document de pilotage portait deux chiffres differents pour la meme ligne
# (-53 % dans sa prose, -70,7 % dans son tableau) parce qu'il melait deux bases
# non enoncees. Ces tests fixent la base unique et la proportionnalite qui
# l'empeche de se contredire.
#
# `BASCULE` est le 24/08 a 06:20 Z : les fenetres ci-dessous sont de part et
# d'autre, a 01-02 h (avant) et 08-09 h (apres).


def _trade_prix(*, quand, r: float, risque: float | None = 10.0) -> dict:
    trade = {"closed_at": quand, "pnl_r": r, "asset_class": "indices",
             "famille": "continuation", "context": "SYM|long|continuation|3p",
             "cost_r": 0.05}
    if risque is not None:
        trade["risk_money"] = risque
    return trade


def _deux_fenetres(*, risque: float | None = 10.0):
    """Avant : moyenne -0,5 R, ecart-type 1. Apres : moyenne -0,6 R, meme ecart."""
    avant = ([_trade_prix(quand=datetime(2026, 8, 23, 1, tzinfo=timezone.utc),
                          r=+0.5, risque=risque) for _ in range(30)]
             + [_trade_prix(quand=datetime(2026, 8, 23, 2, tzinfo=timezone.utc),
                            r=-1.5, risque=risque) for _ in range(30)])
    apres = ([_trade_prix(quand=datetime(2026, 8, 24, 8, tzinfo=timezone.utc),
                          r=+0.4, risque=risque) for _ in range(30)]
             + [_trade_prix(quand=datetime(2026, 8, 24, 9, tzinfo=timezone.utc),
                            r=-1.6, risque=risque) for _ in range(30)])
    return avant + apres


def test_le_prix_de_la_preuve_suit_une_seule_formule(module):
    """`n_requis = (seuil x ecart-type post / ecart)**2`, recalcule a la main ici."""
    bloc = module.prix_de_la_preuve(_deux_fenetres(), BASCULE, equite=1000.0)

    ecart_type = round(statistics.stdev([0.4] * 30 + [-1.6] * 30), 4)
    assert bloc["ecart_r"] == pytest.approx(-0.1, abs=1e-9)
    assert bloc["ecart_type_post"] == ecart_type
    assert bloc["n_requis"] == math.ceil((2.0 * ecart_type / 0.1) ** 2)
    assert bloc["n_requis"] == 407

    ligne = next(l for l in bloc["lignes"] if l["risque_pct"] == 0.02)
    assert ligne["perte_eur"] == pytest.approx(407 * 0.6 * 0.02 * 1000.0, abs=0.01)
    assert ligne["perte_pct_compte"] == pytest.approx(488.4, abs=0.1)
    assert ligne["source_du_risque"] == "plafond"


def test_deux_lignes_du_prix_ne_peuvent_pas_se_contredire(module):
    """Chaque ligne ne change QUE le risque : les parts sont proportionnelles.

    C'est exactement ce que l'ancien tableau violait : sa prose annoncait 53 %
    et sa colonne 70,7 % pour la meme ligne.
    """
    bloc = module.prix_de_la_preuve(_deux_fenetres(), BASCULE, equite=1000.0)
    parts = {l["risque_pct"]: l["perte_pct_compte"] for l in bloc["lignes"]}

    assert parts[0.02] == pytest.approx(488.4, abs=0.1)
    assert parts[0.01] == pytest.approx(244.2, abs=0.1)
    assert parts[0.005] == pytest.approx(122.1, abs=0.1)
    assert parts[0.002] == pytest.approx(48.8, abs=0.1)
    assert parts[0.001] == pytest.approx(24.4, abs=0.1)

    # La PART ne depend pas de l'equite : seul le montant en depend.
    autre = module.prix_de_la_preuve(_deux_fenetres(), BASCULE, equite=500.0)
    assert {l["risque_pct"]: l["perte_pct_compte"] for l in autre["lignes"]} == parts
    assert autre["lignes"][0]["perte_eur"] == pytest.approx(
        bloc["lignes"][0]["perte_eur"] / 2, rel=1e-6)


def test_le_risque_engage_est_mesure_et_non_suppose(module):
    """Le plafond par trade n'est pas ce que la boucle engage : on le mesure."""
    bloc = module.prix_de_la_preuve(_deux_fenetres(risque=20.0), BASCULE, equite=1000.0)

    assert bloc["risque_engage_pct"] == pytest.approx(0.02)
    engagees = [l for l in bloc["lignes"] if l["source_du_risque"] == "engage_mesure"]
    plafond = next(l for l in bloc["lignes"] if l["risque_pct"] == 0.02)
    assert len(engagees) == 1
    assert engagees[0]["perte_pct_compte"] == pytest.approx(plafond["perte_pct_compte"])


def test_sans_risque_engage_ni_equite_rien_n_est_chiffre(module):
    sans_risque = module.prix_de_la_preuve(
        _deux_fenetres(risque=None), BASCULE, equite=1000.0)
    assert sans_risque["risque_engage_pct"] is None
    assert all(l["source_du_risque"] == "plafond" for l in sans_risque["lignes"])

    sans_equite = module.prix_de_la_preuve(_deux_fenetres(), BASCULE, equite=0.0)
    assert sans_equite["lignes"] == []
    assert "equite" in sans_equite["motif"]

    vide = module.prix_de_la_preuve(
        [t for t in _deux_fenetres() if t["closed_at"] >= BASCULE],
        BASCULE, equite=1000.0)
    assert vide["n_requis"] is None
    assert "fenetre" in vide["motif"]


def test_le_prix_n_est_imprime_que_si_un_lecteur_le_demande(module, tmp_path, capsys,
                                                              monkeypatch):
    """Pas d'etat sans lecteur : sans `--equite`, le bloc n'existe pas."""
    journal = tmp_path / "trades.ndjson"
    journal.write_text(
        "\n".join(json.dumps(t, default=str) for t in _deux_fenetres()) + "\n",
        encoding="utf-8")
    sortie = str(tmp_path / "suivi.json")
    base = ["suivi_bascule", "--journal", str(journal), "--sortie", sortie,
            "--bascule", BASCULE.isoformat()]

    monkeypatch.setattr(sys, "argv", base + ["--json"])
    assert module.main() == 0
    assert "prix de la preuve" not in capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", base + ["--equite", "1000"])
    assert module.main() == 0
    texte = capsys.readouterr().out
    assert "prix de la preuve (equite 1000.00 EUR)" in texte
    assert "effectif requis 407 clotures" in texte
    assert "engage mesure" in texte


def test_un_journal_a_l_envers_donne_le_meme_prix_qu_a_l_endroit(module, tmp_path):
    """L'ordre des lignes ne doit pas decider du prix de la preuve.

    Mesure : le meme contenu, lignes a l'envers, faisait disparaitre
    `rythme_par_h` et `duree_h` sans le dire — tous les autres champs
    identiques, aucune erreur. Le contrat d'ordre appartient au chargeur.
    """
    contenu = _deux_fenetres()
    chronologie = [t["closed_at"] for t in contenu]

    def prix(trades, nom):
        dossier = tmp_path / nom
        dossier.mkdir(exist_ok=True)
        journal = dossier / "trades.ndjson"
        lignes = "\n".join(json.dumps(t, default=str) for t in trades)
        journal.write_text(lignes + "\n", encoding="utf-8")
        charges = module.charger(journal)
        assert [t["closed_at"] for t in charges] == sorted(chronologie)
        return module.prix_de_la_preuve(charges, BASCULE, equite=1000.0)

    endroit = prix(contenu, "endroit")
    envers = prix(list(reversed(contenu)), "envers")
    assert endroit["rythme_par_h"] is not None
    assert envers["rythme_par_h"] == endroit["rythme_par_h"]
    assert envers["duree_h"] == endroit["duree_h"]
    assert envers["n_requis"] == endroit["n_requis"]
    assert envers["lignes"] == endroit["lignes"]


def test_une_seule_cloture_apres_la_bascule_dit_la_vraie_cause(module):
    """`n = 1` apres la bascule n'est pas une fenetre vide.

    Mesure : le rapport annoncait « une des deux fenetres est vide » alors que la
    meme page affichait `apres 1`, ce qui se lit comme une contradiction. Ce qui
    manque, c'est la dispersion — et c'est ce que le motif doit dire.
    """
    contenu = _deux_fenetres()
    avant = [t for t in contenu if t["closed_at"] < BASCULE]
    apres = [t for t in contenu if t["closed_at"] >= BASCULE][:1]
    assert len(avant) > 1 and len(apres) == 1

    bloc = module.prix_de_la_preuve(avant + apres, BASCULE, equite=1000.0)
    assert bloc["n_observe"] == 1
    assert bloc["n_requis"] is None
    assert "dispersion" in bloc["motif"]
    assert "vide" not in bloc["motif"]


def test_un_journal_illisible_est_refuse_en_nommant_le_chemin(module, tmp_path,
                                                              capsys, monkeypatch):
    """Un chemin sans fichier lisible ne doit pas produire un rapport trompeur.

    Mesure : `--journal <dossier>` rendait un rapport complet concluant « une des
    deux fenetres est vide », c'est-a-dire une cause fausse pour un journal que
    l'outil n'a jamais ouvert. Le refus est explicite et nomme le chemin ; un
    journal lisible mais vide reste, lui, un rapport normal avec son motif.
    """
    dossier = tmp_path / "pas_un_journal.ndjson"
    dossier.mkdir()
    sortie = tmp_path / "jamais_ecrite.json"
    monkeypatch.setattr(sys, "argv", ["suivi_bascule", "--journal", str(dossier),
                                      "--sortie", str(sortie)])
    assert module.main() == 2
    texte = capsys.readouterr().out
    assert str(dossier) in texte
    assert "--journal" in texte
    # La tolerance est dans le message, pas dans un rc muet : le cas
    # « la boucle n'a pas encore clos sa premiere position » est nomme.
    assert "premiere cloture" in texte
    assert not sortie.exists()

    absent = tmp_path / "absent.ndjson"
    monkeypatch.setattr(sys, "argv", ["suivi_bascule", "--journal", str(absent),
                                      "--sortie", str(sortie)])
    assert module.main() == 2
    assert str(absent) in capsys.readouterr().out
    assert not sortie.exists()

    vide = tmp_path / "vide.ndjson"
    vide.write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["suivi_bascule", "--journal", str(vide),
                                      "--sortie", str(sortie), "--equite", "1000"])
    assert module.main() == 0
    assert "une des deux fenetres est vide" in capsys.readouterr().out
    assert sortie.exists()
