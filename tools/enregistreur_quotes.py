"""Archive les quotes L1 du courtier, tick par tick, en append-only.

Pourquoi ce fichier existe
--------------------------
La matrice d'exécution classe 15 politiques sur des quotes **synthétiques** :
son propre encart le dit. Sa recommandation — rejouer les politiques sur des
quotes courtier archivées — est restée lettre morte pour une raison bête :
personne n'archive de quotes. ``symbol_info_tick`` est lu en direct partout
dans le code, et rien n'en garde trace.

Tant que ce fichier ne tourne pas, aucune politique d'exécution ne peut être
mesurée sur des données réelles. Chaque jour sans lui est un jour de données
définitivement perdu — un tick non enregistré ne se retrouve pas.

Ce qu'il fait, et ce qu'il ne fait pas
--------------------------------------
Il LIT. Il n'envoie aucun ordre, ne touche à aucune position, ne modifie aucun
seuil. Un test structurel interdit tout appel d'ordre dans ce module, comme
pour ``mt5_vendor``.

Discipline d'horloge
--------------------
MT5 rend les horodatages en heure SERVEUR (Axi = GMT+3). Les étiqueter UTC a
déjà invalidé un rejeu complet le 12/08/2026 : 43 lignes refusées, fenêtres
décalées de trois heures. Ce module applique ``decalage_serveur_cache`` — le
même chemin que ``get_rates`` — et écrit un marqueur ``horloge: "utc"`` sur
chaque ligne. Un consommateur qui ne trouve pas ce marqueur doit refuser le
fichier plutôt que supposer.

Reprise
-------
L'archive est append-only et reprend après le dernier tick écrit, par symbole.
Relancer l'outil ne duplique rien : un arrêt de service ne crée pas de trou
autre que la période réellement hors ligne.

Usage
-----
    python tools/enregistreur_quotes.py                 # univers portable
    python tools/enregistreur_quotes.py --symboles BTCUSD ETHUSD
    python tools/enregistreur_quotes.py --intervalle 30 --une-passe
"""

from __future__ import annotations

import argparse
import json
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

#: Répertoire d'archive. Un fichier par symbole et par jour UTC : un fichier
#: qui grandit sans fin devient illisible et impossible à traiter en parallèle.
DOSSIER = RACINE / "results" / "quotes"

#: Secondes entre deux collectes. Chaque passage prend le verrou MT5 une fois
#: par symbole ; la boucle de trading tourne à 60 s et ne doit pas être
#: affamée. 60 s laisse le verrou libre l'essentiel du temps tout en ne
#: perdant aucun tick — ``copy_ticks_range`` rend l'intervalle complet, pas un
#: instantané. C'est la différence entre échantillonner et archiver.
INTERVALLE = 60.0

#: Marge de recouvrement à la reprise. Un tick peut arriver au courtier après
#: qu'on a lu l'intervalle qui le contient ; redemander une seconde de plus
#: coûte quelques lignes dédupliquées et évite un trou silencieux.
RECOUVREMENT_S = 1.0

#: Au premier passage sur un symbole, profondeur d'historique réclamée. Le
#: courtier ne garde pas indéfiniment les ticks : demander large une fois
#: récupère ce qui existe encore, et ne coûte rien s'il n'y a rien.
AMORCE_S = 3600.0


def _fichier(symbole: str, jour: str) -> Path:
    return DOSSIER / symbole / f"{jour}.ndjson"


def dernier_horodatage(symbole: str) -> float | None:
    """Millisecondes UTC du dernier tick archivé, ``None`` si aucune archive.

    Lit la dernière ligne du fichier le plus récent. Une ligne tronquée par un
    arrêt brutal est ignorée : on préfère réécrire une seconde de ticks plutôt
    que de repartir d'un horodatage inventé.
    """
    dossier = DOSSIER / symbole
    if not dossier.is_dir():
        return None
    fichiers = sorted(dossier.glob("*.ndjson"))
    for chemin in reversed(fichiers):
        try:
            lignes = chemin.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ligne in reversed(lignes):
            if not ligne.strip():
                continue
            try:
                ts = json.loads(ligne).get("ts_ms")
            except json.JSONDecodeError:
                continue  # ligne tronquée : on descend d'un cran
            if isinstance(ts, (int, float)):
                return float(ts)
    return None


def collecter(symbole: str, decalage_s: int) -> int:
    """Archive les ticks parus depuis la dernière collecte. Rend leur nombre.

    ``decalage_s`` est l'écart serveur↔UTC en secondes, mesuré par le vendeur.
    Il est retiré des horodatages MT5 pour que l'archive soit en vrai UTC.
    """
    import numpy as np

    depuis_ms = dernier_horodatage(symbole)
    maintenant = datetime.now(timezone.utc).timestamp()
    debut_utc = (maintenant - AMORCE_S if depuis_ms is None
                 else depuis_ms / 1000.0 - RECOUVREMENT_S)

    # copy_ticks_range attend des bornes en heure SERVEUR : on repasse en
    # serveur pour interroger, et on reviendra en UTC pour écrire.
    with mt5_session() as mt5:
        brut = mt5.copy_ticks_range(
            symbole,
            datetime.fromtimestamp(debut_utc + decalage_s, tz=timezone.utc),
            datetime.fromtimestamp(maintenant + decalage_s, tz=timezone.utc),
            mt5.COPY_TICKS_INFO,
        )

    if brut is None or len(brut) == 0:
        return 0

    seuil_ms = depuis_ms if depuis_ms is not None else -1.0
    par_jour: dict[str, list[str]] = {}
    for t in brut:
        ts_ms = float(t["time_msc"]) - decalage_s * 1000.0
        if ts_ms <= seuil_ms:
            continue  # déjà archivé : le recouvrement fait son travail
        bid, ask = float(t["bid"]), float(t["ask"])
        if bid <= 0.0 or ask <= 0.0:
            continue  # un tick sans les deux côtés n'est pas une quote L1
        jour = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")
        par_jour.setdefault(jour, []).append(json.dumps({
            "symbole": symbole,
            "ts_ms": ts_ms,
            "bid": bid,
            "ask": ask,
            "spread": ask - bid,
            "last": float(t["last"]) if not np.isnan(t["last"]) else None,
            "volume": float(t["volume_real"] or t["volume"]),
            "flags": int(t["flags"]),
            # Marqueur d'horloge : sans lui, un consommateur ne peut pas
            # distinguer un horodatage corrigé d'un horodatage serveur.
            "horloge": "utc",
            "decalage_serveur_s": decalage_s,
        }, ensure_ascii=False))

    total = 0
    for jour, lignes in par_jour.items():
        chemin = _fichier(symbole, jour)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        with chemin.open("a", encoding="utf-8") as f:
            f.write("\n".join(lignes) + "\n")
        total += len(lignes)
    return total


def univers_portable() -> list[str]:
    """Symboles réellement cotables maintenant, lus au catalogue du courtier."""
    with mt5_session() as mt5:
        symboles = mt5.symbols_get()
    if not symboles:
        return []
    return [s.name for s in symboles if getattr(s, "visible", False)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symboles", nargs="*", default=None,
                    help="symboles à archiver (défaut : univers visible)")
    ap.add_argument("--intervalle", type=float, default=INTERVALLE)
    ap.add_argument("--une-passe", action="store_true",
                    help="une seule collecte puis sortie")
    args = ap.parse_args()

    try:
        symboles = args.symboles or univers_portable()
    except Mt5NotAvailableError as e:
        print(f"MT5 indisponible : {e}", flush=True)
        return 1
    if not symboles:
        print("Aucun symbole à archiver.", flush=True)
        return 1

    retenus = []
    for s in symboles:
        try:
            ensure_symbol(s)
            retenus.append(s)
        except (NoMarketDataError, Mt5NotAvailableError):
            continue
    print(f"Archive des quotes L1 : {len(retenus)} symboles -> {DOSSIER}", flush=True)
    print("Lecture seule : aucun ordre, aucune position, aucun seuil.", flush=True)

    while True:
        decalage = decalage_serveur_cache(tuple(retenus[:8]))
        total, actifs = 0, 0
        for s in retenus:
            try:
                n = collecter(s, decalage)
            except (Mt5NotAvailableError, NoMarketDataError):
                continue
            except Exception as e:  # un symbole qui casse n'arrête pas l'archive
                print(f"  {s} : {type(e).__name__} {e}", flush=True)
                continue
            total += n
            actifs += 1 if n else 0
        print(f"{datetime.now(timezone.utc):%H:%M:%S}Z  {total:>6} ticks  "
              f"{actifs}/{len(retenus)} actifs  decalage={decalage}s", flush=True)
        if args.une_passe:
            return 0
        time.sleep(args.intervalle)


if __name__ == "__main__":
    raise SystemExit(main())
