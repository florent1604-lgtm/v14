"""Contrat des artefacts bruts du rejeu univers (aucun acces MT5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from titanium.backtest import Trade
from titanium.data.archive_barres import (
    ArchiveHorsUniversError,
    ArchiveQualiteError,
)
from tools import rejeu_univers as ru


def _trade(*, entree: str, sortie: str, pnl_r: float, cost_r: float) -> Trade:
    return Trade(
        symbol="EURUSD",
        side=1,
        bar_entree=entree,
        bar_sortie=sortie,
        prix_entree=1.10,
        prix_sortie=1.11,
        sl=1.09,
        tp=1.12,
        r_unit=0.01,
        pnl_r=pnl_r,
        cost_r=cost_r,
        mae_r=-0.3,
        mfe_r=1.2,
        barres=4,
        motif="tp",
        contexte="fx|continuation|3p",
        pillars=3,
        family="continuation",
        indicators={"rsi": 55.0},
    )


def _snapshot(identifiant: str = "a", *, ltf: str = "M15",
              asset_class: str = "fx") -> dict:
    return {
        "snapshot_id": identifiant * 64,
        "schema_version": 2,
        "asset_class": asset_class,
        "protocol": {"ltf": ltf},
    }


def test_artefact_brut_est_deterministe_et_separe_calibration_verification():
    trades = [
        _trade(
            entree="2026-01-01 00:00:00+00:00",
            sortie="2026-01-01 01:00:00+00:00",
            pnl_r=0.8,
            cost_r=0.2,
        ),
        _trade(
            entree="2026-02-01 00:00:00+00:00",
            sortie="2026-02-01 01:00:00+00:00",
            pnl_r=-1.1,
            cost_r=0.1,
        ),
    ]
    snapshot = _snapshot()

    brut_1, manifeste_1 = ru.construire_artefact_brut(
        "EURUSD", trades, coupure="2026-02-01T00:00:00+00:00", snapshot=snapshot
    )
    brut_2, manifeste_2 = ru.construire_artefact_brut(
        "EURUSD", trades, coupure="2026-02-01T00:00:00+00:00", snapshot=snapshot
    )

    assert brut_1 == brut_2
    assert manifeste_1 == manifeste_2
    lignes = [json.loads(ligne) for ligne in brut_1.decode("utf-8").splitlines()]
    assert [ligne["split"] for ligne in lignes] == ["calibration", "verification"]
    assert lignes[0]["gross_r"] == 1.0
    assert lignes[0]["net_r"] == 0.8
    assert lignes[0]["cost_r"] == 0.2
    assert len({ligne["trade_id"] for ligne in lignes}) == 2
    assert all(ligne["trade_id"].startswith("bt:v2:") for ligne in lignes)
    assert lignes[0]["decision_at"] == "2026-01-01T00:15:00+00:00"
    assert lignes[0]["quantity"] == 1.0
    assert lignes[0]["quantity_unit"] == "risk_unit"
    assert lignes[0]["asset_class"] == "fx"
    assert manifeste_1["counts"] == {
        "trades": 2,
        "calibration": 1,
        "verification": 1,
    }


def test_snapshot_scellé_les_sources_le_protocole_et_le_moteur(tmp_path: Path):
    ltf = tmp_path / "EURUSD.M15.parquet"
    htf = tmp_path / "EURUSD.H4.parquet"
    moteur = tmp_path / "backtest.py"
    dependance_builder = tmp_path / "candlesticks.py"
    ltf.write_bytes(b"ltf-v1")
    htf.write_bytes(b"htf-v1")
    moteur.write_bytes(b"engine-v1")
    dependance_builder.write_bytes(b"candlesticks-v1")

    kwargs = {
        "symbole": "EURUSD",
        "ltf_tf": "M15",
        "htf_tf": "H4",
        "asset_class": "fx",
        "barres": None,
        "pas": 1,
        "spec": {"point": 0.00001},
        "qualite": {
            "fraicheur_max_s": 7200.0,
            "ratio_reconstruit_max": 0.05,
            "tolerance_future_s": 15.0,
        },
        "fichiers_entree": {"ltf": ltf, "htf": htf},
        "fichiers_moteur": [moteur, dependance_builder],
    }
    premier = ru.construire_snapshot_rejeu(**kwargs)
    second = ru.construire_snapshot_rejeu(**kwargs)
    assert premier == second
    assert len(premier["snapshot_id"]) == 64
    assert premier["sources"]["ltf"]["sha256"] == ru._sha256(b"ltf-v1")
    assert premier["protocol"]["part_calibration"] == ru.PART_CALIBRATION
    assert premier["protocol"]["quality_gates"]["fraicheur_max_s"] == 7200.0
    assert premier["protocol"]["quality_gates"]["tolerance_future_s"] == 15.0

    brut, manifeste = ru.construire_artefact_brut(
        "EURUSD", [], coupure="2026-02-01T00:00:00+00:00", snapshot=premier
    )
    ru.persister_artefact_brut(tmp_path / "brut", "EURUSD", brut, manifeste)
    assert ru.artefact_brut_valide(
        tmp_path / "brut", "EURUSD", premier["snapshot_id"])

    # Une dependance indirecte du builder invalide le snapshot et la reprise.
    dependance_builder.write_bytes(b"candlesticks-v2")
    modifie = ru.construire_snapshot_rejeu(**kwargs)
    assert modifie["snapshot_id"] != premier["snapshot_id"]
    assert not ru.artefact_brut_valide(
        tmp_path / "brut", "EURUSD", modifie["snapshot_id"])


def test_snapshot_moteur_inclut_les_dependances_directes_du_builder():
    noms = {chemin.name for chemin in ru.FICHIERS_MOTEUR}
    assert {
        "candlesticks.py",
        "indicators.py",
        "smc.py",
        "structure.py",
        "ict_structure.py",
    } <= noms
    assert all(chemin.is_file() for chemin in ru.FICHIERS_MOTEUR)


def test_persistance_atomique_est_validable_et_detecte_une_alteration(tmp_path: Path):
    snapshot = _snapshot("b")
    brut, manifeste = ru.construire_artefact_brut(
        "EURUSD",
        [_trade(
            entree="2026-01-01T00:00:00+00:00",
            sortie="2026-01-01T01:00:00+00:00",
            pnl_r=0.8,
            cost_r=0.2,
        )],
        coupure="2026-02-01T00:00:00+00:00",
        snapshot=snapshot,
    )

    ru.persister_artefact_brut(tmp_path, "EURUSD", brut, manifeste)

    assert ru.artefact_brut_valide(tmp_path, "EURUSD", snapshot["snapshot_id"])
    dossier = tmp_path / "EURUSD"
    assert not list(dossier.glob("*.tmp.*"))
    resume = tmp_path / "EURUSD.json"
    resume.write_text('{"symbole":"EURUSD"}', encoding="utf-8")
    assert not ru.artefact_brut_valide(
        tmp_path, "EURUSD", snapshot["snapshot_id"], resume_path=resume
    )
    (dossier / "trades.ndjson").write_bytes(brut + b"{}\n")
    assert not ru.artefact_brut_valide(tmp_path, "EURUSD", snapshot["snapshot_id"])


def test_persistance_refuse_un_brut_qui_ne_correspond_pas_au_manifeste(tmp_path: Path):
    snapshot = _snapshot("c")
    brut, manifeste = ru.construire_artefact_brut(
        "EURUSD", [], coupure="2026-02-01T00:00:00+00:00", snapshot=snapshot
    )

    with pytest.raises(ValueError, match="manifeste"):
        ru.persister_artefact_brut(tmp_path, "EURUSD", brut + b"{}\n", manifeste)

    assert not (tmp_path / "EURUSD" / "manifest.json").exists()


def test_split_temporel_ne_depend_pas_du_separateur_espace_ou_t():
    avant = _trade(
        entree="2026-01-31 23:59:59+00:00",
        sortie="2026-02-01T00:00:00+00:00",
        pnl_r=0.1,
        cost_r=0.1,
    )
    frontiere = _trade(
        entree="2026-02-01 00:00:00+00:00",
        sortie="2026-02-01T01:00:00+00:00",
        pnl_r=0.1,
        cost_r=0.1,
    )

    calibration, verification = ru.separer_trades(
        [frontiere, avant], "2026-02-01T00:00:00+00:00"
    )

    assert calibration == [avant]
    assert verification == [frontiere]


@pytest.mark.parametrize("ltf", ["", "MN1", "M17"])
def test_artefact_refuse_un_timeframe_sans_cloture_deterministe(ltf: str):
    trade = _trade(
        entree="2026-01-01T00:00:00+00:00",
        sortie="2026-01-01T01:00:00+00:00",
        pnl_r=0.8,
        cost_r=0.2,
    )

    with pytest.raises(ValueError, match="timeframe"):
        ru.construire_artefact_brut(
            "EURUSD", [trade], coupure="2026-02-01T00:00:00+00:00",
            snapshot=_snapshot(ltf=ltf),
        )


def test_artefact_refuse_une_barre_sans_fuseau():
    trade = _trade(
        entree="2026-01-01T00:00:00",
        sortie="2026-01-01T01:00:00+00:00",
        pnl_r=0.8,
        cost_r=0.2,
    )

    with pytest.raises(ValueError, match="fuseau"):
        ru.construire_artefact_brut(
            "EURUSD", [trade], coupure="2026-02-01T00:00:00+00:00",
            snapshot=_snapshot(),
        )


def test_rejouer_symbole_brut_retourne_les_trades_sans_changer_le_resume(monkeypatch):
    # 700 barres : sous PROFONDEUR_MIN_HTF le rejeu declare le symbole hors
    # univers, ce que verifie test_htf_trop_court_sort_le_symbole_de_l_univers.
    n = 700
    index = pd.date_range("2026-01-01", periods=n, freq="30D", tz="UTC")
    barres = pd.DataFrame({
        "open": [1.0] * n,
        "high": [1.1] * n,
        "low": [0.9] * n,
        "close": [1.0] * n,
        "spread": [10.0] * n,
    }, index=index)
    trade = _trade(
        entree=str(index[-1]), sortie=str(index[-1]), pnl_r=0.8, cost_r=0.2
    )
    resultat = SimpleNamespace(
        trades=[trade], n_enter=1, barres_evaluees=700, erreurs=0
    )
    monkeypatch.setattr(ru, "charger_barres", lambda *_a, **_k: barres.copy())
    monkeypatch.setattr(ru, "specifications", lambda: {"EURUSD": {"point": 0.00001}})
    import titanium.backtest as backtest
    monkeypatch.setattr(backtest, "rejouer", lambda *_a, **_k: resultat)

    resume, trades = ru.rejouer_symbole_brut("EURUSD", "M15", "H4", None, 1)

    assert trades == [trade]
    assert resume["global"]["n"] == 1
    assert resume["verification"]["n"] == 1
    assert "trades" not in resume
    historique = ru.rejouer_symbole("EURUSD", "M15", "H4", None, 1)
    assert {k: v for k, v in historique.items() if k != "ecrit_le"} == {
        k: v for k, v in resume.items() if k != "ecrit_le"
    }


def test_traiter_symbole_publie_resume_et_brut_puis_reprend_sur_les_deux(
        tmp_path: Path, monkeypatch):
    dest = tmp_path / "resumes"
    dest_brut = tmp_path / "brut"
    snapshot = _snapshot("d")
    trade = _trade(
        entree="2026-01-01T00:00:00+00:00",
        sortie="2026-01-01T01:00:00+00:00",
        pnl_r=0.8,
        cost_r=0.2,
    )
    resume = {
        "symbole": "EURUSD",
        "coupure": "2026-02-01T00:00:00+00:00",
        "global": {"n": 1},
        "verification": {"n": 0, "esperance_r": 0.0},
    }
    appels = []
    monkeypatch.setattr(ru, "DEST", dest)
    monkeypatch.setattr(ru, "DEST_BRUT", dest_brut, raising=False)
    monkeypatch.setattr(ru, "snapshot_rejeu_courant", lambda **_k: snapshot, raising=False)

    def faux_rejeu(*_a, **_k):
        appels.append("rejeu")
        return dict(resume), [trade]

    monkeypatch.setattr(ru, "rejouer_symbole_brut", faux_rejeu)

    premiere = ru.traiter_symbole("EURUSD", "M15", "H4", None, 1)
    seconde = ru.traiter_symbole("EURUSD", "M15", "H4", None, 1)

    assert premiere == resume
    assert seconde is None
    assert appels == ["rejeu"]
    assert json.loads((dest / "EURUSD.json").read_text(encoding="utf-8")) == resume
    assert ru.artefact_brut_valide(
        dest_brut, "EURUSD", snapshot["snapshot_id"],
        resume_path=dest / "EURUSD.json",
    )
    manifeste = json.loads(
        (dest_brut / "EURUSD" / "manifest.json").read_text(encoding="utf-8")
    )
    resume_brut = (dest / "EURUSD.json").read_bytes()
    assert manifeste["summary"]["sha256"] == ru._sha256(resume_brut)
    assert manifeste["summary"]["bytes"] == len(resume_brut)

    (dest_brut / "EURUSD" / "trades.ndjson").write_text("{}\n", encoding="utf-8")
    assert ru.traiter_symbole("EURUSD", "M15", "H4", None, 1) == resume
    assert appels == ["rejeu", "rejeu"]


def test_interruption_apres_brut_ne_valide_pas_un_ancien_resume(tmp_path: Path,
                                                               monkeypatch):
    dest = tmp_path / "resumes"
    dest_brut = tmp_path / "brut"
    dest.mkdir()
    (dest / "EURUSD.json").write_text('{"ancien":true}', encoding="utf-8")
    snapshot = _snapshot("e")
    trade = _trade(
        entree="2026-01-01T00:00:00+00:00",
        sortie="2026-01-01T01:00:00+00:00",
        pnl_r=0.8,
        cost_r=0.2,
    )
    resume = {
        "symbole": "EURUSD",
        "coupure": "2026-02-01T00:00:00+00:00",
        "global": {"n": 1},
        "verification": {"n": 0, "esperance_r": 0.0},
    }
    appels = []
    monkeypatch.setattr(ru, "DEST", dest)
    monkeypatch.setattr(ru, "DEST_BRUT", dest_brut)
    monkeypatch.setattr(ru, "snapshot_rejeu_courant", lambda **_k: snapshot)

    def faux_rejeu(*_a, **_k):
        appels.append("rejeu")
        return dict(resume), [trade]

    monkeypatch.setattr(ru, "rejouer_symbole_brut", faux_rejeu)
    ecrire_reel = ru._ecrire_atomique

    def interrompre_sur_resume(chemin, contenu):
        if chemin == dest / "EURUSD.json":
            raise OSError("coupure simulee")
        return ecrire_reel(chemin, contenu)

    monkeypatch.setattr(ru, "_ecrire_atomique", interrompre_sur_resume)
    with pytest.raises(OSError, match="coupure simulee"):
        ru.traiter_symbole("EURUSD", "M15", "H4", None, 1, refaire=True)

    monkeypatch.setattr(ru, "_ecrire_atomique", ecrire_reel)
    assert ru.traiter_symbole("EURUSD", "M15", "H4", None, 1) == resume
    assert appels == ["rejeu", "rejeu"]


def test_resultat_vide_n_ecrase_jamais_un_resume_positif(tmp_path: Path,
                                                         monkeypatch):
    dest = tmp_path / "resumes"
    dest_brut = tmp_path / "brut"
    dest.mkdir()
    cible = dest / "EURUSD.json"
    ancien = {"symbole": "EURUSD", "global": {"n": 42}}
    octets_anciens = json.dumps(ancien).encode("utf-8")
    cible.write_bytes(octets_anciens)

    vide = {
        "symbole": "EURUSD",
        "coupure": "2026-02-01T00:00:00+00:00",
        "n_enter": 0,
        "barres_evaluees": 10_000,
        "global": {"n": 0},
        "calibration": {"n": 0},
        "verification": {"n": 0},
    }
    monkeypatch.setattr(ru, "DEST", dest)
    monkeypatch.setattr(ru, "DEST_BRUT", dest_brut)
    monkeypatch.setattr(ru, "snapshot_rejeu_courant", lambda **_k: _snapshot("f"))
    monkeypatch.setattr(ru, "rejouer_symbole_brut", lambda *_a, **_k: (vide, []))

    with pytest.raises(ru.RejeuVideSuspect, match="ancien_n=42"):
        ru.traiter_symbole("EURUSD", "M15", "H4", None, 1, refaire=True)

    assert cible.read_bytes() == octets_anciens
    assert not (dest_brut / "EURUSD" / "manifest.json").exists()


def test_resultat_vide_profond_exige_une_autorisation_explicite():
    sortie = {
        "n_enter": 0,
        "barres_evaluees": ru.BARRES_MIN_ALERTE_ZERO,
        "global": {"n": 0},
        "calibration": {"n": 0},
        "verification": {"n": 0},
    }

    with pytest.raises(ru.RejeuVideSuspect):
        ru.valider_resultat_avant_publication(sortie, [])

    ru.valider_resultat_avant_publication(
        sortie, [], autoriser_vide=True)


def test_validation_refuse_un_resume_incoherent_avec_les_trades():
    sortie = {
        "n_enter": 2,
        "barres_evaluees": 10,
        "global": {"n": 2},
        "calibration": {"n": 1},
        "verification": {"n": 1},
    }

    with pytest.raises(ValueError, match="resume/trades"):
        ru.valider_resultat_avant_publication(sortie, [])


def test_htf_trop_court_sort_le_symbole_de_l_univers(monkeypatch):
    """Une archive trop courte n'est pas une anomalie : c'est un hors-univers.

    USDCOP porte 379 barres H4 journalieres etiquetees H4, la derniere au
    05/03/2026. Une fois la borne de granularite appliquee il ne reste que
    209 barres authentiques, soit moins que l'amorcage et la fenetre de
    features. Le symbole ne peut produire qu'un artefact vide ; il doit sortir
    de l'univers sans arreter le lot, comme USDUSC.
    """
    n = 100
    index = pd.date_range("2026-01-01", periods=n, freq="4h", tz="UTC")
    barres = pd.DataFrame({
        "open": [1.0] * n, "high": [1.1] * n, "low": [0.9] * n,
        "close": [1.0] * n, "spread": [10.0] * n,
    }, index=index)
    monkeypatch.setattr(ru, "charger_barres", lambda *_a, **_k: barres.copy())
    monkeypatch.setattr(ru, "specifications", lambda: {})

    with pytest.raises(ArchiveHorsUniversError, match="plancher"):
        ru.rejouer_symbole_brut("USDCOP", "M15", "H4", None, 1)


def test_hors_univers_est_une_erreur_de_qualite(monkeypatch):
    """Il reste un `ArchiveQualiteError` : aucun appelant existant ne casse."""
    assert issubclass(ArchiveHorsUniversError, ArchiveQualiteError)


def test_symbole_hors_univers_est_consigne_et_n_arrete_pas_le_lot(
        tmp_path: Path, monkeypatch, capsys):
    """Le registre remplace la sentinelle : le lot continue."""
    monkeypatch.setattr(ru, "HORS_UNIVERS", tmp_path / "_HORS_UNIVERS.json")
    monkeypatch.setattr(ru, "ECHEC_SENTINEL", tmp_path / "_RUN_FAILED.json")
    monkeypatch.setattr(ru, "DEST", tmp_path / "resumes")
    monkeypatch.setattr(ru, "inventaire", lambda _tf: {"VIDE": {}, "PLEIN": {}})

    vus = []

    def faux_traitement(symbole, *_a, **_k):
        vus.append(symbole)
        if symbole == "VIDE":
            raise ArchiveHorsUniversError("VIDE M15: aucune barre exploitable")
        return {"global": {"n": 3, "esperance_r": 0.1, "winrate": 0.5},
                "verification": {"n": 1, "esperance_r": 0.1},
                "secondes": 1.0}

    monkeypatch.setattr(ru, "traiter_symbole", faux_traitement)
    monkeypatch.setattr(sys, "argv", ["rejeu_univers.py", "--ltf", "M15"])

    assert ru.main() == 0
    assert vus == ["PLEIN", "VIDE"]
    assert not (tmp_path / "_RUN_FAILED.json").exists()
    registre = json.loads((tmp_path / "_HORS_UNIVERS.json").read_text(encoding="utf-8"))
    assert list(registre) == ["VIDE"]
    assert registre["VIDE"]["type"] == "ArchiveHorsUniversError"
    assert "HORS UNIVERS" in capsys.readouterr().out


def test_une_vraie_anomalie_arrete_toujours_le_lot(tmp_path: Path, monkeypatch):
    """Une barre corrompue reste fail-closed : la sentinelle est publiee."""
    monkeypatch.setattr(ru, "HORS_UNIVERS", tmp_path / "_HORS_UNIVERS.json")
    monkeypatch.setattr(ru, "ECHEC_SENTINEL", tmp_path / "_RUN_FAILED.json")
    monkeypatch.setattr(ru, "DEST", tmp_path / "resumes")
    monkeypatch.setattr(ru, "inventaire", lambda _tf: {"CASSE": {}})

    def faux_traitement(*_a, **_k):
        raise ArchiveQualiteError("CASSE H4: 1 OHLC invalides")

    monkeypatch.setattr(ru, "traiter_symbole", faux_traitement)
    monkeypatch.setattr(sys, "argv", ["rejeu_univers.py", "--ltf", "M15"])

    assert ru.main() == 1
    assert (tmp_path / "_RUN_FAILED.json").is_file()
    assert not (tmp_path / "_HORS_UNIVERS.json").exists()
