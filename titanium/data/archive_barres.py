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
#: Borne de granularite reelle, publiee par ``tools/borne_granularite.py``.
BORNES_GRANULARITE = RACINE / "results" / "bornes_granularite.json"

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


def _ohlc_invalides(df, colonnes_ohlc):
    """Masque des barres dont l'OHLC est inexploitable.

    Trois familles : valeur non finie, valeur nulle ou negative, et incoherence
    interne (bas au-dessus du haut, ouverture ou cloture hors de la plage).

    Extrait pour etre applique DEUX fois sur des perimetres differents : une
    mesure sur l'archive entiere, qui informe, et un refus sur la fenetre
    rendue, qui decide.
    """
    import numpy as np
    import pandas as pd

    ohlc = df.loc[:, list(colonnes_ohlc)].apply(pd.to_numeric, errors="coerce")
    finies = np.isfinite(ohlc.to_numpy()).all(axis=1)
    positives = (ohlc > 0).all(axis=1).to_numpy()
    coherentes = (
        (ohlc["low"] <= ohlc["high"])
        & ohlc["open"].between(ohlc["low"], ohlc["high"], inclusive="both")
        & ohlc["close"].between(ohlc["low"], ohlc["high"], inclusive="both")
    ).to_numpy()
    return ~(finies & positives & coherentes)


class ArchiveQualiteError(ValueError):
    """L'archive ne satisfait pas un invariant ou une porte de qualite."""


class ArchiveHorsUniversError(ArchiveQualiteError):
    """Le symbole n'a pas d'archive exploitable : il est hors univers.

    A distinguer d'une archive CORROMPUE. Une barre invalide au milieu d'une
    serie est une anomalie : elle doit arreter le traitement, parce qu'elle
    signale que quelque chose s'est casse. Une archive vide, ou reduite a rien
    par les bornes, ne signale aucune casse : le courtier n'a simplement pas
    l'historique. Confondre les deux a coute deux arrets de backfill le
    22/08/2026 — DJ30.fs a 19h03, puis USDUSC evite de justesse.

    L'appelant qui traite un univers entier doit passer au symbole suivant.
    """


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


@lru_cache(maxsize=1)
def _bornes_granularite() -> dict:
    """Bornes de granularite reelle, par timeframe puis par symbole."""
    if not BORNES_GRANULARITE.is_file():
        return {}
    charge = json.loads(BORNES_GRANULARITE.read_text(encoding="utf-8"))
    par_tf: dict[str, dict] = {}
    for symbole, timeframes in (charge.get("symboles") or {}).items():
        for timeframe, mesure in (timeframes or {}).items():
            par_tf.setdefault(timeframe.upper(), {})[symbole.upper()] = mesure
    return par_tf


def granularite(symbole: str, timeframe: str) -> dict:
    """Mesure de granularite reelle du couple, telle que publiee."""
    return (_bornes_granularite().get(timeframe.upper(), {})
            .get(symbole.upper(), {}))


def _barres_grossieres_temps(secondes):
    """Masque des barres grossieres, a partir d'horodatages en secondes."""
    import numpy as np

    secondes = np.asarray(secondes, dtype="int64")
    if secondes.size < 3:
        return np.zeros(secondes.size, dtype=bool)
    ecarts = np.diff(secondes)
    jour = (ecarts % 86400 == 0) & (ecarts >= 86400)
    masque = np.zeros(secondes.size, dtype=bool)
    masque[1:-1] = jour[:-1] & jour[1:]
    return masque


def _barres_grossieres(index):
    """Masque des barres dont les DEUX ecarts voisins sont des multiples de 24 h.

    Meme critere que ``tools/borne_granularite.py``, et pour la meme raison :
    un jour ferie isole produit UN grand ecart, une serie journaliere en
    produit une chaine. Exiger les deux ecarts est ce qui les distingue.

    Applique ici sur la fenetre RENDUE : la borne decide, cette mesure verifie.
    """
    return _barres_grossieres_temps(
        index.tz_convert("UTC").tz_localize(None)
        .astype("datetime64[s]").astype("int64"))


def resume(symbole: str, timeframe: str) -> dict:
    """Profondeur, barres fabriquees et borne utile, tels que publies."""
    return _metadonnees().get(timeframe.upper(), {}).get(symbole.upper(), {})


def inventaire(timeframe: str) -> dict:
    """Tous les symboles connus pour ce timeframe."""
    return _metadonnees().get(timeframe.upper(), {})


def charger_barres(symbole: str, timeframe: str = "H4", count: int | None = None,
                   *, depuis_borne_utile: bool = True,
                   granularite_stricte: bool = True,
                   exclure_reconstruites: bool = True,
                   colonnes_get_rates: bool = True,
                   fraicheur_max_s: float | None = None,
                   ratio_reconstruit_max: float | None = None,
                   tolerance_future_s: float = 0.0,
                   depuis_utc=None,
                   maintenant_utc=None):
    """Rend les barres archivees, au contrat de ``get_rates``.

    Args:
        symbole: instrument tel que nomme par le courtier.
        timeframe: M1, M5, M15, H1, H4 ou D1.
        count: nombre de barres les plus RECENTES a rendre. ``None`` = tout.
        depuis_utc: borne basse temporelle. Les barres anterieures sont
            ecartees AVANT la validation OHLC, parce qu'on ne refuse pas une
            archive pour des barres qu'on ne rend pas. Sert au chargement du
            HTF, qui n'a pas de ``count`` naturel : sa portee utile est celle
            du LTF qu'il accompagne, pas la profondeur du fichier.
        depuis_borne_utile: demarre a la premiere barre exploitable publiee par
            l'archiveur. Mettre a ``False`` expose le prefixe fabrique.
        exclure_reconstruites: jette les barres fabriquees residuelles apres la
            borne. Elles sont rares et isolees ; les garder fabriquerait des
            swings a l'egalite.
        colonnes_get_rates: restreint aux colonnes de ``get_rates``. ``False``
            conserve ``time_serveur``, ``decalage_s`` et ``reconstruit``.
        fraicheur_max_s: age maximal de la derniere barre, en secondes. La
            porte est desactivee avec ``None`` pour permettre les rejeux
            historiques deliberes.
        ratio_reconstruit_max: part maximale de barres reconstruites dans la
            plage post-borne, entre 0 et 1. La mesure precede leur exclusion.
        tolerance_future_s: avance maximale acceptee de la derniere barre par
            rapport a la reference UTC. Zero refuse toute barre future.
        maintenant_utc: reference UTC injectable pour les tests reproductibles.

    Raises:
        ArchiveIndisponibleError: aucun fichier pour ce couple.
        ArchiveObsoleteError: fichier ecrit par une version anterieure du schema.
        ArchiveQualiteError: OHLC invalide, archive trop vieille ou ratio de
            barres reconstruites superieur a la porte configuree.
    """
    import pandas as pd
    import pyarrow.parquet as pq

    if fraicheur_max_s is not None:
        fraicheur_max_s = float(fraicheur_max_s)
        if not 0 <= fraicheur_max_s < float("inf"):
            raise ValueError("fraicheur_max_s doit etre un nombre fini >= 0")
    if ratio_reconstruit_max is not None:
        ratio_reconstruit_max = float(ratio_reconstruit_max)
        if not 0 <= ratio_reconstruit_max <= 1:
            raise ValueError("ratio_reconstruit_max doit etre compris entre 0 et 1")
    tolerance_future_s = float(tolerance_future_s)
    if not 0 <= tolerance_future_s < float("inf"):
        raise ValueError("tolerance_future_s doit etre un nombre fini >= 0")

    symbole_normalise = symbole.upper()
    timeframe_normalise = timeframe.upper()
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
    if "reconstruit" not in df.columns:
        raise ArchiveObsoleteError(
            f"{fichier.name} se declare au schema {SCHEMA_ATTENDU} mais la "
            "colonne obligatoire 'reconstruit' est absente. Relancer "
            "tools/archiveur_barres.py.")
    borne = 0
    if depuis_borne_utile:
        borne = int(resume(symbole, timeframe).get("index_premiere_utile", 0))

    # BORNE DE GRANULARITE — le courtier sert la serie JOURNALIERE sous
    # l'etiquette demandee quand son historique intraday ne remonte pas si
    # loin. Le fichier declare H4 sur toutes ses lignes ; 69 symboles sur 149
    # portent en realite du journalier sur leur portion ancienne, 4 en portent
    # jusque dans le M15, et USDCOP en porte jusqu'au 05/03/2026 — soit sur la
    # TOTALITE de son rejeu, verification comprise.
    #
    # Un ATR calcule sur un melange de barres 4 h et 24 h additionne deux
    # echelles de volatilite : le resultat n'est pas bruite, il est faux. La
    # borne demarre apres la DERNIERE barre grossiere, parce que les deux
    # granularites s'entrelacent sur la zone de transition.
    borne_gran = 0
    if granularite_stricte:
        borne_gran = int(granularite(symbole, timeframe)
                         .get("index_premiere_fine", 0))
    debut = max(borne, borne_gran)
    if debut:
        df = df.iloc[debut:]

    # Horodatages de la SOURCE sur la portee retenue, captures avant que les
    # barres fabriquees ne soient jetees. La granularite se mesure sur eux :
    # retirer une barre reconstruite creuse un trou de 24 h qui imite une
    # serie journaliere sans en etre une. Mesurer apres coup ferait refuser
    # DOGUSD pour un trou, pas pour une granularite.
    temps_source = df["time_utc"].to_numpy(copy=True)
    barres_avant_exclusion = int(len(df))
    barres_reconstruites = (
        int(df["reconstruit"].fillna(False).astype(bool).sum())
        if "reconstruit" in df.columns else 0
    )
    ratio_reconstruit = (
        barres_reconstruites / barres_avant_exclusion
        if barres_avant_exclusion else 0.0
    )
    if (ratio_reconstruit_max is not None
            and ratio_reconstruit > ratio_reconstruit_max):
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: ratio reconstruit "
            f"{ratio_reconstruit:.3%} > {ratio_reconstruit_max:.3%}")

    if exclure_reconstruites and "reconstruit" in df.columns:
        df = df[~df["reconstruit"].fillna(False).astype(bool)]

    df = df.copy()
    colonnes_ohlc = ("open", "high", "low", "close")
    manquantes = [col for col in (*colonnes_ohlc, "time_utc")
                  if col not in df.columns]
    if manquantes:
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: colonnes requises "
            f"absentes: {', '.join(manquantes)}")
    if df.empty:
        raise ArchiveHorsUniversError(
            f"{symbole_normalise} {timeframe_normalise}: aucune barre exploitable")

    # Mesure sur l'archive ENTIERE, pour la tracabilite. Elle ne decide de
    # rien : le refus porte plus bas, sur la seule fenetre effectivement
    # rendue. Voir la note de portee attachee a ce refus.
    invalides_archive = _ohlc_invalides(df, colonnes_ohlc)
    nombre_invalides_archive = int(invalides_archive.sum())

    df["time"] = pd.to_datetime(df["time_utc"], unit="s", utc=True)
    if df["time"].isna().any():
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: horodatage UTC invalide")
    df = df.set_index("time").sort_index()
    # Bascule de printemps : le serveur saute une heure, mais certains actifs
    # cotes en continu (crypto) portent quand meme une etiquette dans le trou.
    # Deux etiquettes serveur consecutives retombent alors sur le meme instant
    # UTC. Trois a huit barres par serie et par decennie, jamais sur le FX —
    # mais un index duplique casse tout alignement inter-actifs, donc on
    # tranche ici, une fois : on garde la derniere, celle qui est deja dans le
    # nouveau regime.
    if df.index.has_duplicates:
        df = df[~df.index.duplicated(keep="last")]
    derniere_barre = df.index[-1]
    reference = (pd.Timestamp.now(tz="UTC") if maintenant_utc is None
                 else pd.Timestamp(maintenant_utc))
    if reference.tzinfo is None:
        reference = reference.tz_localize("UTC")
    else:
        reference = reference.tz_convert("UTC")
    age_secondes = float((reference - derniere_barre).total_seconds())
    avance_secondes = max(0.0, -age_secondes)
    if avance_secondes > tolerance_future_s:
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: barre future de "
            f"{avance_secondes:.0f}s > tolerance {tolerance_future_s:.0f}s")
    if fraicheur_max_s is not None and age_secondes > fraicheur_max_s:
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: archive obsolete, "
            f"age {age_secondes:.0f}s > {fraicheur_max_s:.0f}s")
    # MT5 rend les volumes en uint64 : une soustraction negative y boucle a
    # 2**64. Meme conversion que mt5_vendor, pour la meme raison.
    for col in ("tick_volume", "real_volume"):
        if col in df.columns and df[col].dtype.kind in "ui":
            df[col] = df[col].astype("float64")
    if depuis_utc is not None:
        borne_basse = pd.Timestamp(depuis_utc)
        borne_basse = (borne_basse.tz_localize("UTC")
                       if borne_basse.tzinfo is None
                       else borne_basse.tz_convert("UTC"))
        df = df[df.index >= borne_basse]
        if df.empty:
            raise ArchiveQualiteError(
                f"{symbole_normalise} {timeframe_normalise}: aucune barre "
                f"depuis {borne_basse.isoformat()}")

    if count is not None:
        df = df.tail(int(count))
        if df.empty:
            raise ArchiveQualiteError(
                f"{symbole_normalise} {timeframe_normalise}: aucune barre "
                f"exploitable dans les {int(count)} dernieres")

    # PORTEE DU REFUS — la fenetre rendue, pas le fichier.
    #
    # Cette validation tournait sur l'archive entiere, avant que ``count`` ne
    # la reduise quelques lignes plus bas. Consequence mesuree le 22/08/2026 :
    # UNE barre DJ30.fs du 23 novembre 2009, portant ``low = 0.00``, a fait
    # echouer le lot 0 du backfill a 19:03, qui a publie ``_RUN_FAILED.json``,
    # qui a arrete les sept autres lots. Un rejeu de 149 symboles interrompu a
    # 32 par une barre qu'aucun trade ne touche et que la fonction jetait de
    # toute facon.
    #
    # La barre est authentique : ``copy_rates_range`` la rend aujourd'hui
    # encore avec ``low = 0.00``, sur D1, H4 et H1. Le defaut est chez le
    # courtier, pas dans l'archive — il n'est donc ni reparable a partir de la
    # source, ni interpolable sans fabriquer un prix qui n'a jamais existe.
    #
    # Le garde-fou garde toute sa force : une barre invalide DANS la fenetre
    # rendue fait toujours echouer, fail-closed. Ce qui change est seulement
    # qu'on ne refuse plus des donnees qu'on ne rend pas. Le compte sur
    # l'archive complete reste publie dans ``archive_quality`` pour que
    # l'anomalie demeure visible au lieu d'etre tue.
    invalides = _ohlc_invalides(df, colonnes_ohlc)
    nombre_invalides = int(invalides.sum())
    if nombre_invalides:
        exemples = df.loc[invalides, "time_utc"].head(3).astype(str).tolist()
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: {nombre_invalides} "
            f"OHLC invalides (exemples time_utc: {', '.join(exemples)})")

    # Le refus de granularite porte, lui aussi, sur la fenetre RENDUE. La
    # borne a normalement tout ecarte ; cette mesure est ce qui le prouve,
    # bulletin par bulletin, au lieu de faire confiance a un fichier de bornes
    # qui peut dater d'avant la derniere collecte.
    grossieres_rendues = 0
    if granularite_stricte and len(df):
        import numpy as np

        premier = int(df["time_utc"].iloc[0]) if "time_utc" in df.columns else None
        dernier = int(df["time_utc"].iloc[-1]) if "time_utc" in df.columns else None
        if premier is not None:
            fenetre = temps_source[(temps_source >= premier)
                                   & (temps_source <= dernier)]
            grossieres_rendues = int(_barres_grossieres_temps(fenetre).sum())
            del np
    if grossieres_rendues:
        raise ArchiveQualiteError(
            f"{symbole_normalise} {timeframe_normalise}: {grossieres_rendues} "
            "barres a granularite journaliere dans la fenetre rendue ; "
            "relancer tools/borne_granularite.py")

    if colonnes_get_rates:
        df = df[[c for c in COLONNES if c in df.columns]]
    else:
        df = df.drop(columns=["time_utc"], errors="ignore")
    df.attrs["archive_quality"] = {
        "symbole": symbole_normalise,
        "timeframe": timeframe_normalise,
        "borne_utile": borne,
        "borne_granularite": borne_gran,
        "granularite_stricte": bool(granularite_stricte),
        "barres_grossieres_archive": int(
            granularite(symbole, timeframe).get("barres_grossieres", 0)),
        "barres_grossieres_rendues": grossieres_rendues,
        "barres_avant_exclusion": barres_avant_exclusion,
        "barres_reconstruites": barres_reconstruites,
        "ratio_reconstruit": float(ratio_reconstruit),
        "ohlc_invalides": nombre_invalides,
        "ohlc_invalides_archive": nombre_invalides_archive,
        "depuis_utc": None if depuis_utc is None else str(depuis_utc),
        "derniere_barre_utc": derniere_barre.isoformat(),
        "age_secondes": age_secondes,
        "avance_secondes": avance_secondes,
        "tolerance_future_s": tolerance_future_s,
        "barres_retournees": int(len(df)),
    }
    return df
