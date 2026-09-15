"""L'optimiseur d'allocation mesure-t-il ce qu'il pretend ?

La seule preuve qui vaille est celle du calibrateur de fenetre : injecter un
avantage dont on connait le SIGNE, verifier que l'outil le trouve, puis le
retourner et verifier qu'il ne trouve plus rien. Sans cette seconde mesure, un
outil qui rend toujours le meme verdict passerait le premier test.

Le cas qui a réellement eu lieu pendant l'ecriture est fige ici : une selection
dont la taille vaut TOUT le vivier rend un null degenere — les tirages sont
identiques, donc p=1,0 par construction, et le verdict « aucun effet » tombe
meme quand l'avantage est enorme. Le test de l'avantage plante est ce qui rend
ce defaut impossible a repasser inapercu.

Aucun acces MT5, aucun reseau : les journaux sont fabriques en fixtures, et le
decoupage est celui de l'outil (par le temps, sur les trades tries).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import optimiser_allocation_cohorte as opt

pytestmark = pytest.mark.unit

DEBUT = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)
TOURS = 40


def _ligne(symbole, pnl_r, quand, *, classe="crypto", sortie="init", cote="long"):
    return {"context": f"{symbole}|{cote}|continuation|M15", "pnl_r": pnl_r,
            "closed_at": quand.isoformat(), "risk_money": 100.0,
            "asset_class": classe, "exit_reason": sortie}


def _ecrire(tmp_path, lignes, nom="trades.ndjson"):
    chemin = tmp_path / nom
    chemin.write_text("\n".join(json.dumps(ligne) for ligne in lignes) + "\n", encoding="utf-8")
    return chemin


def _serie(profils, tours=TOURS, symbole_tardif=None):
    """`profils` : symbole -> (esperance IS, esperance OOS). Une ronde = un tour.

    Le decoupage se fait sur les trades tries, donc `tours` rondes identiques
    donnent le meme nombre de trades par symbole de part et d'autre de la coupe.
    """
    lignes = []
    coupe = tours * opt.PART_IS
    for tour in range(tours):
        dedans = tour < coupe
        for symbole, (is_, oos) in profils.items():
            lignes.append(_ligne(symbole, is_ if dedans else oos, DEBUT + timedelta(hours=tour)))
        if symbole_tardif is not None and not dedans:
            lignes.append(_ligne(symbole_tardif, -0.5, DEBUT + timedelta(hours=tour)))
    return lignes


def _rapport(tmp_path, profils, **kw):
    return opt.mesurer(opt.lire_journal(_ecrire(tmp_path, _serie(profils, **kw))))


def test_un_avantage_plante_est_trouve(tmp_path):
    profils = {f"BON{i}": (0.5, 0.5) for i in range(4)}
    profils.update({f"MAUVAIS{i}": (-0.2, -0.2) for i in range(8)})

    selection = _rapport(tmp_path, profils)["selection"]

    assert len(selection["vivier"]) == 12
    assert len(selection["retenus"]) == 4
    assert selection["oos_retenus"]["esperance_r"] == pytest.approx(0.5)
    # Le null doit porter la TAILLE de la selection, pas celle du vivier.
    assert selection["null"]["taille"] == 4
    # Sous le defaut degenere ce p valait 1,0 par construction, meme avec un
    # avantage enorme : c'est ce que ce test rend impossible a repasser.
    assert selection["null"]["p"] < opt.SEUIL_RAPPORT


def test_un_avantage_retourne_ne_trouve_plus_rien(tmp_path):
    profils = {f"S{i}": ((0.2, -0.2) if i % 2 == 0 else (-0.2, 0.2)) for i in range(12)}

    selection = _rapport(tmp_path, profils)["selection"]

    assert len(selection["retenus"]) == 6
    assert selection["oos_retenus"]["esperance_r"] == pytest.approx(-0.2)
    assert selection["null"]["p"] > opt.SEUIL_RAPPORT


def test_les_trois_compartiments_couvrent_toute_la_fenetre_de_jugement(tmp_path):
    profils = {f"S{i}": ((0.3, -0.1) if i % 3 == 0 else (-0.1, -0.1)) for i in range(12)}

    selection = _rapport(tmp_path, profils, symbole_tardif="TARDIF")["selection"]

    # Un symbole vu SEULEMENT dans la fenetre de jugement doit etre compte.
    assert "TARDIF" in selection["non_mesures"]
    couverts = (selection["oos_retenus"]["n"] + selection["oos_exclus"]["n"]
                + selection["oos_non_mesures"]["n"])
    assert couverts == selection["oos_tout"]["n"]


def test_la_coupe_suit_le_temps_et_non_l_ordre_du_fichier(tmp_path):
    lignes = _serie({f"S{i}": (0.1, -0.1) for i in range(6)})
    chemin = _ecrire(tmp_path, list(reversed(lignes)))

    trades = opt.lire_journal(chemin)
    instants = [str(trade["closed_at"]) for trade in trades]
    assert instants == sorted(instants)

    is_, oos = opt.couper(trades)
    assert is_[-1]["closed_at"] < oos[0]["closed_at"]


def test_un_journal_absent_ou_vide_sort_en_erreur(tmp_path, capsys):
    assert opt.main(["--journal", str(tmp_path / "absent.ndjson")]) == 2
    assert "introuvable" in capsys.readouterr().err

    vide = tmp_path / "vide.ndjson"
    vide.write_text("", encoding="utf-8")
    assert opt.main(["--journal", str(vide)]) == 2
    assert "vide ou illisible" in capsys.readouterr().err


def test_les_lignes_illisibles_sont_ecartees(tmp_path):
    chemin = tmp_path / "mixte.ndjson"
    chemin.write_text(
        json.dumps(_ligne("EURUSD", 1.0, DEBUT)) + "\n"
        + "pas du json\n"
        + json.dumps({"sans": "pnl"}) + "\n"
        + json.dumps({"pnl_r": "texte", "closed_at": DEBUT.isoformat()}) + "\n",
        encoding="utf-8")

    trades = opt.lire_journal(chemin)

    assert len(trades) == 1
    assert opt.symbole(trades[0]) == "EURUSD"


def test_une_cohorte_fournie_est_jugee_et_ses_inconnus_nommes(tmp_path):
    profils = {f"S{i}": (0.1, -0.1) for i in range(4)}
    chemin = _ecrire(tmp_path, _serie(profils))

    bloc = opt.mesurer(opt.lire_journal(chemin),
                       cohorte=["S0", "S1", "JAMAIS-VU"])["cohorte"]

    assert bloc["symboles"] == 3
    assert bloc["inconnus_du_journal"] == ["JAMAIS-VU"]
    assert bloc["n"] == 80


def test_les_euros_sont_le_r_fois_le_risque_engage(tmp_path):
    rapport = opt.mesurer(opt.lire_journal(_ecrire(tmp_path, _serie({"S0": (0.5, 0.5)}))))

    assert rapport["journal"]["n"] == 40
    assert rapport["journal"]["euros"] == pytest.approx(2000.0)
    assert rapport["journal"]["total_r"] == pytest.approx(20.0)
def test_un_null_trop_court_ne_conclut_pas(tmp_path, capsys):
    """Un null d'un seul tirage rend p=0,0 sur des donnees de hasard.

    Le chiffre reste vrai — aucun des tirages ne fait mieux — mais un tirage
    unique ne peut pas separer le seuil du hasard : l'outil doit refuser le
    verdict, pas le rendre.
    """
    profils = {f"BON{i}": (0.5, 0.5) for i in range(4)}
    profils.update({f"MAUVAIS{i}": (-0.2, -0.2) for i in range(8)})
    chemin = _ecrire(tmp_path, _serie(profils))

    selection = opt.mesurer(opt.lire_journal(chemin), tirages=1)["selection"]

    assert selection["null"]["tirages"] == 1
    assert selection["null"]["p"] == 0.0
    assert selection["jugement"]["possible"] is False
    assert "plancher" in selection["jugement"]["motif"]

    assert opt.main(["--journal", str(chemin), "--tirages", "1"]) == 0
    sortie = capsys.readouterr().out
    assert "AUCUN VERDICT" in sortie
    assert "effet au-dela du hasard" not in sortie


def test_le_motif_du_plancher_est_le_meme_que_le_rapport_le_dise(tmp_path):
    """Le plancher est un proprietaire unique : la fonction et le rapport s'accordent."""
    profils = {f"S{i}": (0.2, 0.2) for i in range(6)}
    rapport = opt.mesurer(opt.lire_journal(_ecrire(tmp_path, _serie(profils))),
                          tirages=opt.TIRAGES_MIN - 1)

    selection = rapport["selection"]
    assert selection["jugement"]["motif"] == opt.motif_non_jugeable(selection)

    suffisant = opt.mesurer(opt.lire_journal(_ecrire(tmp_path, _serie(profils), "b.ndjson")),
                            tirages=opt.TIRAGES_MIN)
    assert suffisant["selection"]["jugement"]["possible"] is True


def test_une_fenetre_de_jugement_vide_est_nommee(tmp_path, capsys):
    """`--part-is 1.0` laisse la fenetre de jugement vide : rapport rempli, rien dit."""
    profils = {f"S{i}": (0.2, -0.1) for i in range(6)}
    chemin = _ecrire(tmp_path, _serie(profils))

    selection = opt.mesurer(opt.lire_journal(chemin), part_is=1.0)["selection"]

    assert selection["oos_tout"]["n"] == 0
    assert selection["jugement"]["possible"] is False
    assert "--part-is" in selection["jugement"]["motif"]

    assert opt.main(["--journal", str(chemin), "--part-is", "1.0"]) == 0
    assert "AUCUN VERDICT" in capsys.readouterr().out


def test_une_regle_qui_ne_retenit_rien_le_dit(tmp_path, capsys):
    """Un `--min-n` plus grand que le vivier ne retient aucun symbole."""
    profils = {f"S{i}": (0.2, -0.1) for i in range(4)}
    chemin = _ecrire(tmp_path, _serie(profils))

    selection = opt.mesurer(opt.lire_journal(chemin), min_n=10_000)["selection"]

    assert selection["retenus"] == []
    assert selection["null"]["tirages"] == 0
    assert selection["jugement"]["possible"] is False
    assert "aucun symbole retenu" in selection["jugement"]["motif"]

    assert opt.main(["--journal", str(chemin), "--min-n", "10000"]) == 0
    assert "AUCUN VERDICT" in capsys.readouterr().out
