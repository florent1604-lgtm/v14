"""Lecture de l'archive de barres, au contrat de ``mt5_vendor.get_rates``.

Pourquoi ce fichier existe
--------------------------
Jusqu'au 19/08/2026, ``get_rates`` etait la seule porte d'entree des barres du
projet : tout backtest exigeait le terminal ouvert, et aucun rejeu n'etait
reproductible — ``results/walk_forward/EURUSD.json`` contient 107 trades dont
les barres n'existent plus nulle part. ``tools/archiveur_barres.py`` a fige
55 millions de barres sur disque ; ce module les rend au **meme contrat** que
``get_rates``, pour que ``titanium.backtest.rejouer`` ne fasse pas la
difference : index UTC croissant, colonnes ``open/high/low/close``, volumes en
flottant.

La borne, et pourquoi elle n'est pas optionnelle en pratique
------------------------------------------------------------
4,1 % des barres de l'archive sont **fabriquees** par le courtier : ``high ==
low`` et ``tick_volume <= 1``, jusqu'a 10,3 % en M1. EURUSD H4 remonte a 1971,
soit vingt-huit ans avant l'euro. Sur une serie plate, ``_swings`` retient un
extremum par egalite : trois cents barres plates produisent trois cents faux
swings, l'ATR y vaut zero et la normalisation en R explose. Lire l'archive
depuis l'index 0 revient donc a calibrer sur du bruit fabrique.

``charger_barres`` demarre par defaut a la borne publiee par l'archiveur dans
``_metadonnees.json`` et jette les barres fabriquees residuelles. Un appelant
qui veut le brut doit le demander explicitement — c'est plus penible que
l'inverse, et c'est voulu.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
DOSSIER = RACINE / "results" / "barres"
METADONNEES = DOSSIER / "_metadonnees.json"

#: Schema d'archive accepte. La v1 datait les barres d'hiver une heure trop tot
#: (decalage serveur constant) et ne marquait pas les barres fabriquees : la
#: lire en croyant lire la v2 produirait des sessions decalees sans aucun signe.
SCHEMA_ATTENDU = 2

#: Colonnes rendues, dans l'ordre de ``get_rates``.
COLONNES = ("open", "high", "low", "close", "tick_volume", "spread",
            "real_volume")


class ArchiveIndisponibleError(FileNotFoundError):
    """L'archive n'existe pas, ou pas pour ce couple symbole/timeframe."""


class ArchiveObsoleteError(ValueError):
    """L'archive existe mais n'est pas au schema attendu."""


def chemin(symbole: str, timeframe: str) -> Path:
    return DOSSIER / timeframe.upper() / f"{symbole.upper()}.parquet"


def disponible(symbole: str, timeframe: str) -> bool:
    return chemin(symbole, timeframe).is_file()


@lru_cache(maxsize=1)
def _metadonnees() -> dict:
    if not METADONNEES.is_file():
        return {}
    charge = json.loads(METADONNEES.read_text(encoding="utf-8"))
    if charge.get("schema_version") != SCHEMA_ATTENDU:
        raise ArchiveObsoleteError(
            f"_metadonnees.json est au schema {charge.get('schema_version')!r}, "
            f"attendu {SCHEMA_ATTENDU}")
    return charge.get("timeframes", {})


def resume(symbole: str, timeframe: str) -> dict:
    """Profondeur, barres fabriquees et borne utile, tels que publies."""
    return _metadonnees().get(timeframe.upper(), {}).get(symbole.upper(), {})


def inventaire(timeframe: str) -> dict:
    """Tous les symboles connus pour ce timeframe."""
    return _metadonnees().get(timeframe.upper(), {})


def charger_barres(symbole: str, timeframe: str = "H4", count: int | None = None,
                   *, depuis_borne_utile: bool = True,
                   exclure_reconstruites: bool = True,
                   colonnes_get_rates: bool = True):
    """Rend les barres archivees, au contrat de ``get_rates``.

    Args:
        symbole: instrument tel que nomme par le courtier.
        timeframe: M1, M5, M15, H1, H4 ou D1.
        count: nombre de barres les plus RECENTES a rendre. ``None`` = tout.
        depuis_borne_utile: demarre a la premiere barre exploitable publiee par
            l'archiveur. Mettre a ``False`` expose le prefixe fabrique.
        exclure_reconstruites: jette les barres fabriquees residuelles apres la
            borne. Elles sont rares et isolees ; les garder fabriquerait des
            swings a l'egalite.
        colonnes_get_rates: restreint aux colonnes de ``get_rates``. ``False``
            conserve ``time_serveur``, ``decalage_s`` et ``reconstruit``.

    Raises:
        ArchiveIndisponibleError: aucun fichier pour ce couple.
        ArchiveObsoleteError: fichier ecrit par une version anterieure du schema.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    fichier = chemin(symbole, timeframe)
    if not fichier.is_file():
        raise ArchiveIndisponibleError(
            f"aucune archive {timeframe.upper()} pour {symbole.upper()} "
            f"({fichier})")

    fp = pq.ParquetFile(fichier)
    meta = fp.schema_arrow.metadata or {}
    version = meta.get(b"schema_version")
    if version != str(SCHEMA_ATTENDU).encode():
        raise ArchiveObsoleteError(
            f"{fichier.name} est au schema {version!r}, attendu "
            f"{SCHEMA_ATTENDU}. Relancer tools/archiveur_barres.py.")

    df = fp.read().to_pandas()
    if depuis_borne_utile:
        borne = int(resume(symbole, timeframe).get("index_premiere_utile", 0))
        if borne:
            df = df.iloc[borne:]
    if exclure_reconstruites and "reconstruit" in df.columns:
        df = df[~df["reconstruit"].astype(bool)]

    df = df.copy()
    df["time"] = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    df = df.set_index("time").sort_index()
    # MT5 rend les volumes en uint64 : une soustraction negative y boucle a
    # 2**64. Meme conversion que mt5_vendor, pour la meme raison.
    for col in ("tick_volume", "real_volume"):
        if col in df.columns and df[col].dtype.kind in "ui":
            df[col] = df[col].astype("float64")
    if colonnes_get_rates:
        df = df[[c for c in COLONNES if c in df.columns]]
    else:
        df = df.drop(columns=["time_utc"], errors="ignore")
    if count is not None:
        df = df.tail(int(count))
    return df
