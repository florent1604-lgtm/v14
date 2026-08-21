"""Tests du lecteur d'archive de barres.

Ce lecteur est la porte par laquelle tout backtest reproductible passera. Deux
choses doivent etre impossibles : lire une archive v1 en croyant lire une v2
(les barres d'hiver y sont datees une heure trop tot, sans aucun signe), et
calibrer sur le prefixe de barres fabriquees par le courtier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data import archive_barres as ab  # noqa: E402


def _ecrire_archive(dossier: Path, symbole: str, timeframe: str, n: int,
                    reconstruites: int, version: int = 2,
                    debut: int = 1_600_000_000) -> None:
    df = pd.DataFrame({
        "symbole": [symbole] * n,
        "timeframe": [timeframe] * n,
        "time_utc": [debut + 900 * i for i in range(n)],
        "time_serveur": [debut + 900 * i + 7200 for i in range(n)],
        "decalage_s": [7200] * n,
        "open": [1.0] * n,
        "high": [1.0 if i < reconstruites else 1.5 for i in range(n)],
        "low": [1.0] * n,
        "close": [1.0] * n,
        "tick_volume": pd.Series([1 if i < reconstruites else 50
                                  for i in range(n)], dtype="uint64"),
        "spread": [10] * n,
        "real_volume": pd.Series([0] * n, dtype="uint64"),
        "reconstruit": [i < reconstruites for i in range(n)],
    })
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({b"schema_version": str(version).encode(),
                                           b"horloge": b"utc"})
    chemin = dossier / timeframe / f"{symbole}.parquet"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, chemin)


def _remplacer_colonne(chemin: Path, colonne: str, index: int, valeur) -> None:
    """Modifie une cellule de fixture en preservant les metadonnees Parquet."""
    fp = pq.ParquetFile(chemin)
    meta = fp.schema_arrow.metadata
    df = fp.read().to_pandas()
    df.loc[index, colonne] = valeur
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table.replace_schema_metadata(meta), chemin)


@pytest.fixture
def archive(tmp_path, monkeypatch):
    import json

    _ecrire_archive(tmp_path, "TESTUSD", "M15", 500, reconstruites=120)
    (tmp_path / "_metadonnees.json").write_text(json.dumps({
        "schema_version": 2,
        "timeframes": {"M15": {"TESTUSD": {"barres": 500, "reconstruites": 120,
                                           "index_premiere_utile": 120}}},
    }), encoding="utf-8")
    monkeypatch.setattr(ab, "DOSSIER", tmp_path)
    monkeypatch.setattr(ab, "METADONNEES", tmp_path / "_metadonnees.json")
    ab._metadonnees.cache_clear()
    yield tmp_path
    ab._metadonnees.cache_clear()


def test_contrat_get_rates(archive):
    df = ab.charger_barres("TESTUSD", "M15")
    assert list(df.columns) == list(ab.COLONNES)
    assert isinstance(df.index, pd.DatetimeIndex)
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing


def test_le_prefixe_fabrique_est_ecarte_par_defaut(archive):
    df = ab.charger_barres("TESTUSD", "M15")
    assert len(df) == 380  # 500 - 120 barres fabriquees
    assert (df["high"] != df["low"]).all()


def test_le_brut_reste_accessible_a_qui_le_demande(archive):
    df = ab.charger_barres("TESTUSD", "M15", depuis_borne_utile=False,
                           exclure_reconstruites=False)
    assert len(df) == 500


def test_count_rend_les_barres_les_plus_recentes(archive):
    df = ab.charger_barres("TESTUSD", "M15", 50)
    entier = ab.charger_barres("TESTUSD", "M15")
    assert len(df) == 50
    assert df.index[-1] == entier.index[-1]


def test_volumes_en_flottant(archive):
    """uint64 : une soustraction negative y boucle a 2**64, pas a un negatif."""
    df = ab.charger_barres("TESTUSD", "M15")
    assert df["tick_volume"].dtype.kind == "f"
    assert df["real_volume"].dtype.kind == "f"


def test_une_archive_v1_est_refusee(tmp_path, monkeypatch):
    _ecrire_archive(tmp_path, "VIEUX", "M15", 50, reconstruites=0, version=1)
    monkeypatch.setattr(ab, "DOSSIER", tmp_path)
    monkeypatch.setattr(ab, "METADONNEES", tmp_path / "_metadonnees.json")
    ab._metadonnees.cache_clear()
    with pytest.raises(ab.ArchiveObsoleteError):
        ab.charger_barres("VIEUX", "M15")


def test_symbole_absent(archive):
    with pytest.raises(ab.ArchiveIndisponibleError):
        ab.charger_barres("INEXISTANT", "M15")


def test_colonnes_completes_sur_demande(archive):
    df = ab.charger_barres("TESTUSD", "M15", colonnes_get_rates=False)
    assert "reconstruit" in df.columns
    assert "decalage_s" in df.columns


def test_ohlc_low_nul_est_refuse_fail_closed(archive):
    chemin = archive / "M15" / "TESTUSD.parquet"
    _remplacer_colonne(chemin, "low", 200, 0.0)

    with pytest.raises(ab.ArchiveQualiteError, match="OHLC invalides"):
        ab.charger_barres("TESTUSD", "M15")


@pytest.mark.parametrize(("colonne", "valeur"), [
    ("open", 2.0),
    ("close", 2.0),
    ("high", 0.5),
])
def test_ohlc_incoherents_sont_refuses(archive, colonne, valeur):
    chemin = archive / "M15" / "TESTUSD.parquet"
    _remplacer_colonne(chemin, colonne, 200, valeur)

    with pytest.raises(ab.ArchiveQualiteError, match="OHLC invalides"):
        ab.charger_barres("TESTUSD", "M15")


def test_fraicheur_configurable_refuse_une_archive_obsolete(archive):
    derniere = pd.Timestamp(1_600_000_000 + 900 * 499, unit="s", tz="UTC")

    with pytest.raises(ab.ArchiveQualiteError, match="archive obsolete"):
        ab.charger_barres(
            "TESTUSD", "M15", fraicheur_max_s=3600,
            maintenant_utc=derniere + pd.Timedelta(hours=2),
        )

    # Sans porte configuree, une archive historique reste lisible.
    assert not ab.charger_barres(
        "TESTUSD", "M15", maintenant_utc=derniere + pd.Timedelta(days=365),
    ).empty


def test_metrique_et_porte_du_ratio_reconstruit(archive):
    with pytest.raises(ab.ArchiveQualiteError, match="ratio reconstruit"):
        ab.charger_barres(
            "TESTUSD", "M15", depuis_borne_utile=False,
            ratio_reconstruit_max=0.20,
        )

    df = ab.charger_barres(
        "TESTUSD", "M15", depuis_borne_utile=False,
        ratio_reconstruit_max=0.25,
    )
    qualite = df.attrs["archive_quality"]
    assert qualite["barres_reconstruites"] == 120
    assert qualite["barres_avant_exclusion"] == 500
    assert qualite["ratio_reconstruit"] == pytest.approx(0.24)
    assert qualite["barres_retournees"] == 380
    assert qualite["ohlc_invalides"] == 0


def test_index_sans_doublon_a_la_bascule_de_printemps(tmp_path, monkeypatch):
    """Deux etiquettes serveur peuvent retomber sur le meme instant UTC.

    Ca n'arrive que sur les actifs cotes en continu, quelques barres par
    decennie — mais un index duplique casse tout alignement inter-actifs, donc
    la regle est tranchee au chargement.
    """
    import json

    import numpy as np

    n = 10
    df = pd.DataFrame({
        "symbole": ["DUP"] * n,
        "timeframe": ["H1"] * n,
        # deux barres portent le meme time_utc
        "time_utc": [1_600_000_000 + 3600 * i for i in [0, 1, 1, 2, 3, 4, 5, 6, 7, 8]],
        "time_serveur": [1_600_000_000 + 3600 * i + 7200 for i in range(n)],
        "decalage_s": [7200] * n,
        "open": 100.0 + np.arange(n, dtype=float),
        "high": 101.0 + np.arange(n, dtype=float),
        "low": 99.0 + np.arange(n, dtype=float),
        "close": 100.0 + np.arange(n, dtype=float),
        "tick_volume": pd.Series([50] * n, dtype="uint64"),
        "spread": [10] * n,
        "real_volume": pd.Series([0] * n, dtype="uint64"),
        "reconstruit": [False] * n,
    })
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({b"schema_version": b"2"})
    chemin = tmp_path / "H1" / "DUP.parquet"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, chemin)
    (tmp_path / "_metadonnees.json").write_text(json.dumps(
        {"schema_version": 2, "timeframes": {}}), encoding="utf-8")
    monkeypatch.setattr(ab, "DOSSIER", tmp_path)
    monkeypatch.setattr(ab, "METADONNEES", tmp_path / "_metadonnees.json")
    ab._metadonnees.cache_clear()

    lu = ab.charger_barres("DUP", "H1")
    assert not lu.index.has_duplicates
    assert len(lu) == n - 1
    ab._metadonnees.cache_clear()
