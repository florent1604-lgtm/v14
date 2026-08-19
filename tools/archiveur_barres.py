"""Archive les barres OHLCV du courtier, par symbole et par timeframe.

Pourquoi ce fichier existe
--------------------------
Le 18/08/2026, l'inventaire des donnees V14 a etabli un fait genant :
``copy_rates_from_pos`` et ``copy_rates_range`` dans ``titanium/data/mt5_vendor``
sont les **seules** sources de M15 et H4 du projet. Pas un CSV, pas un parquet,
pas un cache. Consequence directe et verifiable : ``results/walk_forward/EURUSD.json``
contient 107 trades du 02/06 au 20/07 et **n'est pas rejouable** — le resultat a
survecu, les barres non.

Ce qu'il fait, et ce qu'il ne fait pas
--------------------------------------
Il LIT. Aucun ordre, aucune position, aucun seuil, aucun compte. Le meme test
structurel que ``mt5_vendor`` et ``enregistreur_quotes`` interdit tout appel
d'ordre dans ce module.

Ce que la premiere passe du 19/08/2026 a rate
---------------------------------------------
La passe v1 a produit 54 652 071 barres, et elle etait fausse sur trois points.
Ils sont corriges ici ; le detail des mesures est dans
``collab/prime_agent/runs/mt5-collecte-elargie-20260819/``.

1. **Heure d'ete ignoree.** v1 retirait un decalage serveur CONSTANT, mesure
   aujourd'hui (10 800 s), a des barres couvrant quatre ans. Le serveur d'Axi
   est a GMT+3 en ete et GMT+2 en hiver : toutes les barres d'hiver etaient
   donc datees **une heure trop tot**. Preuve : l'ouverture hebdomadaire du FX
   tombait a 21:00 UTC en janvier comme en juillet dans l'archive v1, alors
   qu'elle est a 22:00 UTC quand New York est en heure normale. Ici le
   decalage est recalcule **barre par barre** (``decalage_utc``), et l'heure
   serveur brute est conservee dans ``time_serveur`` pour que la conversion
   reste auditable au lieu d'etre a croire.

2. **Barres reconstruites non signalees.** 4,1 % des barres v1 ont
   ``high == low`` et ``tick_volume <= 1`` : ce sont des barres fabriquees, pas
   observees. EURUSD H4 remontait a 1971 alors que l'euro date de 1999. Sur une
   serie plate, ``_swings`` retient un extremum par egalite et fabrique un faux
   swing par barre, l'ATR y vaut zero et toute normalisation en R explose. Le
   brut reste brut : on ne purge pas, on **marque** (colonne ``reconstruit``) et
   on publie une borne de debut utilisable par symbole et timeframe dans
   ``_metadonnees.json``. Signale par Claude, verifie par sonde independante.

3. **Premier contact a vide accepte pour une reponse.** Les deux premiers
   symboles de la passe v1 ont rendu H4 vide et l'outil a ecrit 0 barre sans
   broncher ; les memes appels rendent 37 503 et 37 066 barres une fois la
   session chaude. Neuf symboles ont ainsi perdu leur H4. Ici une reponse vide
   est **retentee** avant d'etre crue.

Plafond de 100 000 barres — hors de portee de ce fichier
--------------------------------------------------------
M1, M5 et M15 sortent tous a 100 000 barres a une poignee pres, sur 130 a 147
symboles. Ce n'est pas la profondeur du courtier, c'est le reglage
``MaxBars=100000`` du terminal (``config/common.ini``). ``copy_rates_range``
sur EURUSD M15 en juin 2022 rend 1 barre alors que l'archive commence le
12/08/2022 : la coupure est nette. Tant que ce reglage n'est pas releve dans
l'interface du terminal (Outils > Options > Graphiques > Nombre maximal de
barres), aucune passe ne descendra plus bas, et M15 restera plafonne a environ
2,8 ans. Modifier ce reglage ou redemarrer le terminal est une decision humaine.

Discipline d'horloge
--------------------
MT5 rend les horodatages en heure SERVEUR. Les etiqueter UTC a deja invalide un
rejeu complet le 12/08/2026. Ce module ecrit ``horloge=utc`` et
``schema_version=2`` dans les metadonnees du parquet. Un consommateur qui ne
trouve pas ces marqueurs doit refuser le fichier plutot que supposer.

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
    python tools/archiveur_barres.py --reprendre           # saute ce qui est deja en v2
    python tools/archiveur_barres.py --etat                # inventaire, sans reseau
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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

#: Version du schema ecrit. Un consommateur qui lit 1 lit une archive datee a
#: decalage constant et sans marquage des barres reconstruites : il doit la
#: refuser. Change des que la signification d'une colonne change.
SCHEMA_VERSION = 2

#: Le serveur d'Axi vit a GMT+2 l'hiver et GMT+3 l'ete, et bascule avec l'heure
#: d'ete des Etats-Unis, pas celle de l'Europe. C'est verifiable sans croire
#: personne : l'ouverture hebdomadaire du FX est a 22:00 UTC quand New York est
#: en heure normale et a 21:00 UTC en heure d'ete, et elle tombe toujours a
#: 00:00 lundi en heure serveur.
FUSEAU_REFERENCE = ZoneInfo("America/New_York")
DECALAGE_HIVER_S = 2 * 3600
DECALAGE_ETE_S = 3 * 3600

#: Fichier compagnon de l'archive : profondeur reelle, taux de barres
#: reconstruites et premiere barre utilisable, par symbole et par timeframe.
METADONNEES = DOSSIER / "_metadonnees.json"

#: Une barre plate dont le volume de ticks ne depasse pas ce seuil n'a pas ete
#: observee : elle a ete fabriquee par le courtier pour boucher un trou.
VOLUME_RECONSTRUIT_MAX = 1

#: Fenetre glissante et taux tolere pour declarer une serie exploitable.
FENETRE_UTILE = 100
TAUX_RECONSTRUIT_MAX = 0.05

#: Une reponse vide au premier contact n'est pas une reponse : les deux premiers
#: symboles de la passe du 19/08 ont rendu H4 vide a froid, puis 37 000 barres
#: une fois la session chaude.
RETENTES = 3
ATTENTE_RETENTE_S = 2.0


def _fichier(timeframe: str, symbole: str) -> Path:
    return DOSSIER / timeframe / f"{symbole}.parquet"


def decalage_utc(ts_serveur: int) -> int:
    """Rend le decalage, en secondes, a retirer a une heure SERVEUR pour obtenir UTC.

    Le decalage n'est pas une constante : il vaut 3 h pendant l'heure d'ete de
    New York et 2 h le reste de l'annee. Retirer la valeur du jour a quatre ans
    d'historique — ce que faisait la passe v1 — date toutes les barres d'hiver
    une heure trop tot.

    L'instant UTC depend du decalage, et le decalage depend de l'instant : on
    teste les deux candidats et on garde celui qui est coherent avec lui-meme.
    A la bascule d'automne, une heure d'heure serveur existe deux fois ; le
    candidat d'ete est retenu, comme le fait le terminal qui n'a qu'une seule
    serie a servir.
    """
    for decalage in (DECALAGE_ETE_S, DECALAGE_HIVER_S):
        instant = datetime.fromtimestamp(ts_serveur - decalage, tz=timezone.utc)
        dst = instant.astimezone(FUSEAU_REFERENCE).dst() or timedelta(0)
        attendu = DECALAGE_ETE_S if dst else DECALAGE_HIVER_S
        if attendu == decalage:
            return decalage
    # Trou de printemps : l'heure serveur visee n'existe dans aucun des deux
    # regimes. Le passage se fait a 2 h locales New York, en pleine fermeture
    # du FX ; on tranche pour l'ete, qui est le regime qui commence.
    return DECALAGE_ETE_S


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
        premiere = None
        for essai in range(RETENTES):
            premiere = mt5.copy_rates_from_pos(symbole, tf, 0, PAGE_MAX)
            if premiere is not None and len(premiere):
                break
            # Une reponse vide a froid n'est pas une absence d'historique : le
            # terminal charge la serie a la demande. Neuf symboles ont perdu
            # leur H4 le 19/08 parce que v1 croyait la premiere reponse.
            if essai < RETENTES - 1:
                time.sleep(ATTENTE_RETENTE_S)
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


def marquer_reconstruites(df):
    """Marque les barres fabriquees par le courtier plutot qu'observees.

    Signature retenue : ``high == low`` ET ``tick_volume <= 1``. La platitude
    seule ne suffit pas — une vraie barre M1 sur un exotique illiquide peut
    n'avoir qu'un prix — mais platitude et volume au plancher ensemble ne se
    produisent pas sur un marche qui cote.
    """
    import numpy as np

    plat = np.asarray(df["high"]) == np.asarray(df["low"])
    if "tick_volume" in df.columns:
        creux = np.asarray(df["tick_volume"]) <= VOLUME_RECONSTRUIT_MAX
    else:
        creux = np.ones(len(df), dtype=bool)
    return plat & creux


def premiere_utile(reconstruit) -> int:
    """Rend l'index de la premiere barre a partir de laquelle la serie est exploitable.

    Critere : la premiere position dont la fenetre de ``FENETRE_UTILE`` barres
    suivantes contient moins de ``TAUX_RECONSTRUIT_MAX`` de barres fabriquees.
    Une borne, et non un masque : un consommateur qui coupe avant elle jette un
    prefixe reconstruit sans avoir a raisonner barre par barre.
    """
    import numpy as np

    r = np.asarray(reconstruit, dtype=bool)
    n = len(r)
    if n == 0:
        return 0
    if n <= FENETRE_UTILE:
        return 0 if r.mean() <= TAUX_RECONSTRUIT_MAX else n
    cumul = np.concatenate(([0], np.cumsum(r)))
    fenetres = cumul[FENETRE_UTILE:] - cumul[:-FENETRE_UTILE]
    bonnes = np.flatnonzero(fenetres <= TAUX_RECONSTRUIT_MAX * FENETRE_UTILE)
    if not len(bonnes):
        return n
    debut = int(bonnes[0])
    # La fenetre tolere quelques barres fabriquees ; la borne, elle, ne doit pas
    # tomber SUR l'une d'elles. On avance jusqu'a la premiere barre observee.
    while debut < n and r[debut]:
        debut += 1
    return debut


def ecrire(symbole: str, timeframe: str, barres, decalage_s: int = 0) -> dict:
    """Ecrit le parquet. Rend le resume du fichier ecrit.

    ``decalage_s`` n'est plus applique : il est recalcule barre par barre par
    ``decalage_utc``. Le parametre subsiste pour la trace, et l'heure serveur
    brute est conservee en colonne pour que la conversion soit verifiable a
    posteriori sans redemander le terminal.
    """
    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    if barres is None or len(barres) == 0:
        return {"barres": 0}
    df = pd.DataFrame(barres)
    serveur = df["time"].astype("int64").to_numpy()
    decalages = np.fromiter((decalage_utc(int(t)) for t in serveur),
                            dtype="int64", count=len(serveur))
    df["time_serveur"] = serveur
    df["time_utc"] = serveur - decalages
    df["decalage_s"] = decalages.astype("int32")
    df = df.drop(columns=["time"])
    df["symbole"] = symbole
    df["timeframe"] = timeframe
    df["reconstruit"] = marquer_reconstruites(df)
    colonnes = ["symbole", "timeframe", "time_utc", "time_serveur", "decalage_s",
                "open", "high", "low", "close", "tick_volume", "spread",
                "real_volume", "reconstruit"]
    df = df[[c for c in colonnes if c in df.columns]]

    borne = premiere_utile(df["reconstruit"].to_numpy())
    resume = {
        "barres": int(len(df)),
        "reconstruites": int(df["reconstruit"].sum()),
        "index_premiere_utile": int(borne),
        "debut_utc": int(df["time_utc"].iloc[0]),
        "fin_utc": int(df["time_utc"].iloc[-1]),
        "debut_utile_utc": (int(df["time_utc"].iloc[borne])
                            if borne < len(df) else None),
    }

    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata({
        b"horloge": b"utc",
        b"schema_version": str(SCHEMA_VERSION).encode(),
        b"decalage": b"recalcule par barre (America/New_York, GMT+2/GMT+3)",
        b"decalage_courant_s": str(int(decalage_s)).encode(),
        b"source": b"MT5 copy_rates_from_pos + copy_rates_from",
        b"ecrit_le": datetime.now(timezone.utc).isoformat().encode(),
        b"reconstruit": b"high == low et tick_volume <= 1",
    })
    chemin = _fichier(timeframe, symbole)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    # Ecriture atomique : un parquet tronque par un arret brutal est illisible
    # en entier, pas seulement sur sa derniere ligne.
    provisoire = chemin.with_suffix(".parquet.tmp")
    pq.write_table(table, provisoire, compression="zstd")
    provisoire.replace(chemin)
    return resume


def deja_en_v2(symbole: str, timeframe: str) -> bool:
    """Vrai si le fichier existe deja au schema courant. Sert a la reprise."""
    chemin = _fichier(timeframe, symbole)
    if not chemin.is_file():
        return False
    try:
        import pyarrow.parquet as pq

        meta = pq.ParquetFile(chemin).schema_arrow.metadata or {}
        return meta.get(b"schema_version") == str(SCHEMA_VERSION).encode()
    except Exception:
        return False


def _charger_metadonnees() -> dict:
    if not METADONNEES.is_file():
        return {}
    try:
        charge = json.loads(METADONNEES.read_text(encoding="utf-8"))
    except Exception:
        return {}
    # On ne reprend que les entrees du schema courant : melanger deux schemas
    # dans un compagnon ferait croire a une profondeur qui n'existe plus.
    if charge.get("schema_version") != SCHEMA_VERSION:
        return {}
    return charge.get("timeframes", {})


def _ecrire_metadonnees(meta: dict) -> None:
    METADONNEES.parent.mkdir(parents=True, exist_ok=True)
    charge = {
        "schema_version": SCHEMA_VERSION,
        "ecrit_le": datetime.now(timezone.utc).isoformat(),
        "reconstruit": "high == low et tick_volume <= 1",
        "borne": (f"premiere fenetre de {FENETRE_UTILE} barres sous "
                  f"{TAUX_RECONSTRUIT_MAX:.0%} de barres reconstruites"),
        "timeframes": meta,
    }
    provisoire = METADONNEES.with_suffix(".json.tmp")
    provisoire.write_text(json.dumps(charge, indent=1), encoding="utf-8")
    provisoire.replace(METADONNEES)


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
    ap.add_argument("--reprendre", action="store_true",
                    help="saute les fichiers deja ecrits au schema courant")
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
    total_reconstruites = 0
    meta = _charger_metadonnees()
    for i, s in enumerate(retenus, 1):
        ligne = [f"[{i:>3}/{len(retenus)}] {s:<12}"]
        for tf in args.timeframes:
            if args.reprendre and deja_en_v2(s, tf):
                ligne.append(f"{tf}:saute")
                continue
            try:
                barres = rapatrier(s, tf, decalage)
                resume = ecrire(s, tf, barres, decalage)
            except (Mt5NotAvailableError, NoMarketDataError):
                resume = {"barres": 0}
            except Exception as e:  # un symbole qui casse n'arrete pas l'archive
                ligne.append(f"{tf}:{type(e).__name__}")
                continue
            n = resume["barres"]
            total_barres += n
            total_reconstruites += resume.get("reconstruites", 0)
            if n:
                meta.setdefault(tf, {})[s] = resume
            marque = ""
            if resume.get("reconstruites"):
                marque = f"({resume['reconstruites']}r)"
            ligne.append(f"{tf}:{n}{marque}")
        print("  ".join(ligne), flush=True)
        # Ecrit a chaque symbole : une passe interrompue laisse un compagnon
        # coherent avec ce qui est reellement sur le disque.
        _ecrire_metadonnees(meta)

    duree = time.perf_counter() - debut
    part = 100.0 * total_reconstruites / total_barres if total_barres else 0.0
    print(f"\n{total_barres} barres archivees en {duree:.0f} s. "
          f"{total_reconstruites} reconstruites ({part:.2f} %).", flush=True)
    print(f"Compagnon : {METADONNEES}", flush=True)
    etat()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
