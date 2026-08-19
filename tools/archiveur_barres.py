"""Archive les barres OHLCV du courtier, par symbole et par timeframe.

Pourquoi ce fichier existe
--------------------------
Le 18/08/2026, l'inventaire des donnees V14 a etabli un fait genant :
``copy_rates_from_pos`` et ``copy_rates_range`` dans ``titanium/data/mt5_vendor``
sont les **seules** sources de M15 et H4 du projet. Pas un CSV, pas un parquet,
pas un cache. Consequence directe et verifiable : ``results/walk_forward/EURUSD.json``
contient 107 trades du 02/06 au 20/07 et **n'est pas rejouable** — le resultat a
survecu, les barres non.

Le 19/08/2026, une sonde a montre que le courtier sert bien plus que ce qu'on
lui demandait : M15 depuis 2022, H4 et D1 depuis 2007 a 1971 selon l'actif,
M1 sur environ trois mois. Environ une seconde par symbole et par timeframe.
Autrement dit, quatre ans d'historique sont a portee de main **maintenant**,
au lieu d'un jour accumule par jour.

Ce qu'il fait, et ce qu'il ne fait pas
--------------------------------------
Il LIT. Aucun ordre, aucune position, aucun seuil, aucun compte. Le meme test
structurel que ``mt5_vendor`` et ``enregistreur_quotes`` interdit tout appel
d'ordre dans ce module.

Discipline d'horloge
--------------------
MT5 rend les horodatages en heure SERVEUR (Axi = GMT+3). Les etiqueter UTC a
deja invalide un rejeu complet le 12/08/2026. Ce module retire
``decalage_serveur_cache`` et ecrit ``horloge=utc`` dans les metadonnees du
fichier parquet. Un consommateur qui ne trouve pas ce marqueur doit refuser le
fichier plutot que supposer.

Plafond du pont MT5
-------------------
``copy_rates_from_pos`` refuse au-dela d'environ 99 000 barres par appel
(``Terminal: Invalid params``, mesure du 19/08/2026). La profondeur est donc
atteinte par pages successives vers le passe avec ``copy_rates_from``.

Usage
-----
    python tools/archiveur_barres.py                       # catalogue complet
    python tools/archiveur_barres.py --symboles EURUSD XAUUSD
    python tools/archiveur_barres.py --timeframes M15 H4 D1
    python tools/archiveur_barres.py --etat                # inventaire, sans reseau
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data.mt5_vendor import (  # noqa: E402
    Mt5NotAvailableError,
    NoMarketDataError,
    decalage_serveur_cache,
    ensure_symbol,
    mt5_session,
)

#: Un fichier par symbole et par timeframe. Un fichier unique par symbole
#: melangerait des granularites que rien ne permet de reseparer sans convention.
DOSSIER = RACINE / "results" / "barres"

#: Plafond dur du pont MT5, mesure le 19/08/2026 : 99 000 passe, 100 000 rend
#: ``Terminal: Invalid params`` sans autre signal. On reste sous la barre.
PAGE_MAX = 99_000

#: Nombre maximal de pages remontees par timeframe. Garde-fou : un courtier qui
#: renvoie toujours la meme page ferait boucler l'outil indefiniment. La sortie
#: normale est l'epuisement de l'historique, pas ce plafond.
PAGES_MAX = 40

TIMEFRAMES_DEFAUT = ("M1", "M5", "M15", "H1", "H4", "D1")


def _fichier(timeframe: str, symbole: str) -> Path:
    return DOSSIER / timeframe / f"{symbole}.parquet"


def _constante_tf(mt5, timeframe: str):
    nom = f"TIMEFRAME_{timeframe}"
    if not hasattr(mt5, nom):
        raise ValueError(f"timeframe inconnu : {timeframe}")
    return getattr(mt5, nom)


def rapatrier(symbole: str, timeframe: str, decalage_s: int,
              pages_max: int = PAGES_MAX) -> list:
    """Rend toutes les barres disponibles, des plus anciennes aux plus recentes.

    Remonte par pages vers le passe. Chaque page est ancree sur la barre la
    plus ancienne deja obtenue : c'est le seul moyen de depasser le plafond de
    ``copy_rates_from_pos``, qui compte depuis la barre courante.
    """
    import numpy as np

    with mt5_session() as mt5:
        tf = _constante_tf(mt5, timeframe)
        premiere = mt5.copy_rates_from_pos(symbole, tf, 0, PAGE_MAX)
        if premiere is None or len(premiere) == 0:
            return []
        pages = [premiere]
        ancre = int(premiere[0]["time"])
        for _ in range(pages_max - 1):
            # ``copy_rates_from`` rend les barres qui PRECEDENT l'ancre.
            page = mt5.copy_rates_from(
                symbole, tf,
                datetime.fromtimestamp(ancre - 1, tz=timezone.utc),
                PAGE_MAX,
            )
            if page is None or len(page) == 0:
                break
            plus_ancienne = int(page[0]["time"])
            if plus_ancienne >= ancre:
                break  # le courtier ne remonte plus : historique epuise
            pages.append(page)
            ancre = plus_ancienne
            if len(page) < PAGE_MAX:
                break

    brut = np.concatenate(list(reversed(pages)))
    # Les pages se recouvrent d'une barre au minimum : on deduplique sur le
    # temps, en gardant la derniere version rendue (la plus recente lecture).
    ordre = np.argsort(brut["time"], kind="stable")
    brut = brut[ordre]
    garde = np.ones(len(brut), dtype=bool)
    garde[:-1] = brut["time"][1:] != brut["time"][:-1]
    return brut[garde]


def ecrire(symbole: str, timeframe: str, barres, decalage_s: int) -> int:
    """Ecrit le parquet. Rend le nombre de barres ecrites."""
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    if barres is None or len(barres) == 0:
        return 0
    df = pd.DataFrame(barres)
    # Heure SERVEUR -> UTC. C'est la seule transformation appliquee.
    df["time_utc"] = df["time"].astype("int64") - int(decalage_s)
    df = df.drop(columns=["time"])
    df["symbole"] = symbole
    df["timeframe"] = timeframe
    colonnes = ["symbole", "timeframe", "time_utc", "open", "high", "low",
                "close", "tick_volume", "spread", "real_volume"]
    df = df[[c for c in colonnes if c in df.columns]]

    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({
        b"horloge": b"utc",
        b"decalage_serveur_s": str(int(decalage_s)).encode(),
        b"source": b"MT5 copy_rates_from_pos + copy_rates_from",
        b"ecrit_le": datetime.now(timezone.utc).isoformat().encode(),
    })
    chemin = _fichier(timeframe, symbole)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    # Ecriture atomique : un parquet tronque par un arret brutal est illisible
    # en entier, pas seulement sur sa derniere ligne.
    provisoire = chemin.with_suffix(".parquet.tmp")
    pq.write_table(table, provisoire, compression="zstd")
    provisoire.replace(chemin)
    return len(df)


def etat() -> None:
    """Inventaire de l'archive, sans toucher au reseau ni a MT5."""
    if not DOSSIER.is_dir():
        print("aucune archive de barres.")
        return
    total_o = 0
    for dtf in sorted(DOSSIER.iterdir()):
        if not dtf.is_dir():
            continue
        fichiers = sorted(dtf.glob("*.parquet"))
        octets = sum(f.stat().st_size for f in fichiers)
        total_o += octets
        print(f"  {dtf.name:<4} {len(fichiers):>4} symboles  {octets / 1e6:>8.1f} Mo")
    print(f"  {'TOTAL':<4} {'':>4}           {total_o / 1e6:>8.1f} Mo")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--timeframes", nargs="*", default=list(TIMEFRAMES_DEFAUT))
    ap.add_argument("--etat", action="store_true", help="inventaire puis sortie")
    args = ap.parse_args()

    if args.etat:
        etat()
        return 0

    try:
        if args.symboles:
            symboles = list(args.symboles)
        else:
            with mt5_session() as mt5:
                symboles = [s.name for s in mt5.symbols_get()
                            if getattr(s, "visible", False)]
    except Mt5NotAvailableError as e:
        print(f"MT5 indisponible : {e}", flush=True)
        return 1
    if not symboles:
        print("Aucun symbole.", flush=True)
        return 1

    retenus = []
    for s in symboles:
        try:
            ensure_symbol(s)
            retenus.append(s)
        except (NoMarketDataError, Mt5NotAvailableError):
            continue

    decalage = decalage_serveur_cache(tuple(retenus[:8]))
    print(f"Archive de barres : {len(retenus)} symboles x {len(args.timeframes)} "
          f"timeframes -> {DOSSIER}", flush=True)
    print(f"Lecture seule. Decalage serveur retire : {decalage} s.", flush=True)

    debut = time.perf_counter()
    total_barres = 0
    for i, s in enumerate(retenus, 1):
        ligne = [f"[{i:>3}/{len(retenus)}] {s:<12}"]
        for tf in args.timeframes:
            try:
                barres = rapatrier(s, tf, decalage)
                n = ecrire(s, tf, barres, decalage)
            except (Mt5NotAvailableError, NoMarketDataError):
                n = 0
            except Exception as e:  # un symbole qui casse n'arrete pas l'archive
                ligne.append(f"{tf}:{type(e).__name__}")
                continue
            total_barres += n
            ligne.append(f"{tf}:{n}")
        print("  ".join(ligne), flush=True)

    duree = time.perf_counter() - debut
    print(f"\n{total_barres} barres archivees en {duree:.0f} s.", flush=True)
    etat()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
