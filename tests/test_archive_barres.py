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
                    reconstruites: int, version: int = 2) -> None:
    debut = 1_600_000_000
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
