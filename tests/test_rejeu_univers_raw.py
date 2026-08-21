"""Contrat des artefacts bruts du rejeu univers (aucun acces MT5)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from titanium.backtest import Trade
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
    snapshot = {"snapshot_id": "a" * 64, "schema_version": 1}

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
    assert all(ligne["trade_id"].startswith("bt:v1:") for ligne in lignes)
    assert manifeste_1["counts"] == {
        "trades": 2,
        "calibration": 1,
        "verification": 1,
    }


def test_snapshot_scellé_les_sources_le_protocole_et_le_moteur(tmp_path: Path):
    ltf = tmp_path / "EURUSD.M15.parquet"
    htf = tmp_path / "EURUSD.H4.parquet"
    moteur = tmp_path / "backtest.py"
    ltf.write_bytes(b"ltf-v1")
    htf.write_bytes(b"htf-v1")
    moteur.write_bytes(b"engine-v1")

    kwargs = {
        "symbole": "EURUSD",
        "ltf_tf": "M15",
        "htf_tf": "H4",
        "barres": None,
        "pas": 1,
        "spec": {"point": 0.00001},
        "qualite": {"fraicheur_max_s": 7200.0, "ratio_reconstruit_max": 0.05},
        "fichiers_entree": {"ltf": ltf, "htf": htf},
        "fichiers_moteur": [moteur],
    }
    premier = ru.construire_snapshot_rejeu(**kwargs)
    second = ru.construire_snapshot_rejeu(**kwargs)
    assert premier == second
    assert len(premier["snapshot_id"]) == 64
    assert premier["sources"]["ltf"]["sha256"] == ru._sha256(b"ltf-v1")
    assert premier["protocol"]["part_calibration"] == ru.PART_CALIBRATION
    assert premier["protocol"]["quality_gates"]["fraicheur_max_s"] == 7200.0

    moteur.write_bytes(b"engine-v2")
    modifie = ru.construire_snapshot_rejeu(**kwargs)
    assert modifie["snapshot_id"] != premier["snapshot_id"]


def test_persistance_atomique_est_validable_et_detecte_une_alteration(tmp_path: Path):
    snapshot = {"snapshot_id": "b" * 64, "schema_version": 1}
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
    snapshot = {"snapshot_id": "c" * 64, "schema_version": 1}
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


def test_rejouer_symbole_brut_retourne_les_trades_sans_changer_le_resume(monkeypatch):
    index = pd.date_range("2026-01-01", periods=4, freq="30D", tz="UTC")
    barres = pd.DataFrame({
        "open": [1.0] * 4,
        "high": [1.1] * 4,
        "low": [0.9] * 4,
        "close": [1.0] * 4,
        "spread": [10.0] * 4,
    }, index=index)
    trade = _trade(
        entree=str(index[-1]), sortie=str(index[-1]), pnl_r=0.8, cost_r=0.2
    )
    resultat = SimpleNamespace(
        trades=[trade], n_enter=1, barres_evaluees=4, erreurs=0
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
    snapshot = {"snapshot_id": "d" * 64, "schema_version": 1}
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
    snapshot = {"snapshot_id": "e" * 64, "schema_version": 1}
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
