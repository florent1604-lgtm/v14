"""Archive le carnet L2 et les transactions de Binance, en append-only.

Pourquoi ce fichier existe
--------------------------
Six des quinze politiques d'exécution classées par la matrice V14 dépendent d'un
carnet d'ordres : ``pegged``, ``iceberg``, ``post_only``, ``cancel_replace``,
``market_making`` et, en partie, ``adaptive``. Leur fidélité mesurée va de 0,35 à
0,50 parce que le simulateur invente le carnet.

Axi ne diffuse aucun L2 (``market_book_add`` rend ``False``, vérifié). Binance
en diffuse un, toutes les 100 ms, sans clé API. Le rapport du 15/08 constatait
que V12 le recevait déjà et le jetait : ``OrderBookState.history`` est un anneau
de 30 instantanés en RAM, rien sur disque. Ce module écrit ce qui passe.

Un carnet non enregistré ne se retrouve pas. C'est la seule donnée de ce projet
qui soit strictement irremplaçable : les barres OHLCV se retéléchargent, un
carnet d'il y a trois semaines n'existe nulle part.

Ce qu'il fait, et ce qu'il ne fait pas
--------------------------------------
Il LIT des flux publics. Il n'ouvre aucun compte, n'utilise aucune clé, ne signe
aucune requête, n'envoie aucun ordre. Les hôtes par défaut — ``data-api`` et
``data-stream.binance.vision`` — sont les points d'accès *données de marché* de
Binance : ils n'exposent structurellement aucun point d'entrée de trading. Un
test le verrouille.

Ce module ne touche ni MT5, ni les positions, ni un seuil de V14. Enregistrer le
carnet de Binance ne dit rien sur l'opportunité d'exécuter chez Binance — c'est
une décision distincte, qui appartient à Florent.

Ce qui est archivé, et sous quelle forme
-----------------------------------------
Le brut, pas un dérivé. Binance envoie des *différentiels* numérotés ; on écrit
l'instantané d'amorce puis chaque différentiel tel quel. Toute reconstruction
reste alors possible, y compris une que l'on n'a pas prévue. Écrire un « top 20 »
agrégé jetterait de l'information qu'aucun retraitement ne récupère — l'erreur
même que ce module corrige.

Les prix et quantités sont conservés en **chaînes**, exactement comme Binance les
émet. Les convertir en ``float`` à l'écriture introduirait un arrondi définitif
dans une archive censée faire autorité.

Trois lignes de service complètent le flux :

- ``instantane`` — état complet du carnet, à l'amorce, après un trou, et
  périodiquement. Le rythme périodique borne le coût d'un rejeu : sans lui, lire
  le carnet à 23 h exigerait de rejouer les 23 heures précédentes.
- ``trou`` — rupture de séquence détectée. Un trou explicite permet à un
  consommateur de refuser une fenêtre ; un trou silencieux produit un carnet faux
  sans jamais lever d'erreur.
- ``trade`` — transactions agrégées, avec le côté de l'agresseur. C'est ce champ
  qui rend une analyse maker/taker possible.

Discipline d'horloge
--------------------
Binance horodate en millisecondes UTC depuis l'époque : aucun décalage serveur à
retirer, contrairement à MT5. Chaque ligne porte tout de même le marqueur
``horloge: "utc"`` — même contrat que ``enregistreur_quotes`` — et un
``recu_ms`` local. L'écart entre ``E`` et ``recu_ms`` est la latence observée :
c'est la seule mesure de latence réelle dont V14 dispose, et elle ne coûte rien
à conserver.

Usage
-----
    python tools/enregistreur_carnet_binance.py
    python tools/enregistreur_carnet_binance.py --symboles BTCUSDT ETHUSDT
    python tools/enregistreur_carnet_binance.py --duree 60 --instantane 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

#: Répertoire d'archive. Un dossier par symbole, un fichier par jour UTC et par
#: nature de flux : un fichier unique qui grandit sans fin devient illisible et
#: interdit tout traitement parallèle.
DOSSIER = RACINE / "results" / "carnet_binance"

#: Hôtes *données de marché* de Binance. Ils ne servent que des données
#: publiques : aucun point d'entrée de trading n'y répond, ce qui rend
#: l'absence d'ordre structurelle plutôt que déclarative.
HOTE_REST = "https://data-api.binance.vision"
HOTE_WS = "wss://data-stream.binance.vision"

#: Symboles par défaut. BTC et ETH ont un équivalent CFD déjà archivé en L1 côté
#: Axi (`results/quotes/BTCUSD`, `ETHUSD`) : c'est le seul appariement qui
#: permettra de confronter ce qu'on peut exécuter (L1 courtier) et ce qu'on ne
#: peut que simuler (L2 place).
#:
#: PAXG a été écarté malgré son intérêt — proxy de l'or sur carnet central —
#: parce que son équivalent XAUUSD n'a pas de microstructure comparable et que
#: chaque symbole coûte 0,7 Go par jour (mesuré, voir ``VOLUME_JOUR_GO``).
#: Ouvrir large sans mesurer, c'est remplir le disque avant d'avoir la réponse.
SYMBOLES = ("BTCUSDT", "ETHUSDT")

#: Volume brut mesuré le 16/08/2026 : 0,91 Mo pour 60 s et 2 symboles, soit
#: ~27 Mo/h/symbole. Deux symboles remplissent donc ~1,4 Go par jour. La
#: compaction (`--compacter`) ramène un jour clos à environ un dixième.
VOLUME_JOUR_GO = 0.7

#: Profondeur de l'instantané REST. 1000 niveaux couvre largement la zone où se
#: pose un ordre passif ; 5000 coûterait quatre fois le poids pour de la
#: profondeur qu'aucune politique V14 n'atteindra jamais.
PROFONDEUR = 1000

#: Secondes entre deux instantanés périodiques. Un quart d'heure : c'est ce qui
#: borne le coût d'un rejeu — sans point de reprise, lire le carnet à 23 h
#: exigerait de rejouer les 23 heures precedentes — et c'est aussi ce qui rend
#: l'archive vérifiable, puisque le contrôle compare un carnet rejoué au point
#: de reprise suivant. À l'heure, une archive fraîche reste « indécidable »
#: pendant une heure entière. Le coût est négligeable : 4 instantanés par heure
#: et par symbole pèsent 0,4 Mo contre 54 Mo de différentiels.
INSTANTANE_S = 900.0

#: Plafond du délai de reconnexion, en secondes. Une coupure réseau ne doit pas
#: faire boucler la reconnexion à pleine vitesse, ni attendre dix minutes.
RECONNEXION_MAX_S = 30.0


def _maintenant_ms() -> float:
    return datetime.now(timezone.utc).timestamp() * 1000.0


def _jour(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).strftime("%Y-%m-%d")


# ─────────────────────────────────────────── séquence

class SequenceCarnet:
    """Vérifie la chaîne de numéros d'un flux de différentiels Binance.

    Le protocole officiel : après un instantané de numéro ``lastUpdateId``, on
    écarte tout différentiel dont ``u <= lastUpdateId``, le premier retenu doit
    satisfaire ``U <= lastUpdateId + 1 <= u``, puis chaque suivant doit vérifier
    ``U == u_precedent + 1``.

    Cette classe ne maintient PAS le carnet en mémoire, et c'est délibéré : on
    archive le brut, donc seule l'intégrité de la chaîne compte. Maintenir un
    dictionnaire prix→quantité coûterait du temps de boucle sans rien ajouter à
    l'archive — et un consommateur qui reconstruit à froid n'aura de toute façon
    pas confiance dans un carnet reconstruit par le collecteur.
    """

    def __init__(self) -> None:
        self.dernier_id: int | None = None
        self.amorce_id: int | None = None
        self.amorcee = False

    def amorcer(self, last_update_id: int) -> None:
        self.amorce_id = int(last_update_id)
        self.dernier_id = None
        self.amorcee = False

    def verdict(self, premier: int, dernier: int) -> str:
        """Rend ``"applique"``, ``"ignore"`` ou ``"trou"`` pour un différentiel.

        ``ignore`` n'est pas une anomalie : c'est le rattrapage normal des
        différentiels antérieurs à l'instantané. ``trou`` l'est, et impose un
        nouvel instantané.
        """
        if self.amorce_id is None:
            return "trou"
        if not self.amorcee:
            if dernier <= self.amorce_id:
                return "ignore"
            if premier > self.amorce_id + 1:
                return "trou"
            self.amorcee = True
            self.dernier_id = dernier
            return "applique"
        assert self.dernier_id is not None
        if dernier <= self.dernier_id:
            return "ignore"
        if premier != self.dernier_id + 1:
            return "trou"
        self.dernier_id = dernier
        return "applique"


# ─────────────────────────────────────────── mise en ligne

def ligne_instantane(symbole: str, charge: dict[str, Any], raison: str,
                     recu_ms: float) -> dict[str, Any]:
    return {
        "type": "instantane",
        "symbole": symbole,
        "raison": raison,
        "last_update_id": int(charge["lastUpdateId"]),
        "bids": charge["bids"],
        "asks": charge["asks"],
        "recu_ms": recu_ms,
        "horloge": "utc",
        "source": "binance-spot",
    }


def ligne_diff(symbole: str, charge: dict[str, Any], recu_ms: float) -> dict[str, Any]:
    return {
        "type": "diff",
        "symbole": symbole,
        "premier_id": int(charge["U"]),
        "dernier_id": int(charge["u"]),
        "event_ms": float(charge["E"]),
        "bids": charge["b"],
        "asks": charge["a"],
        "recu_ms": recu_ms,
        "latence_ms": recu_ms - float(charge["E"]),
        "horloge": "utc",
        "source": "binance-spot",
    }


def ligne_trou(symbole: str, attendu: int | None, charge: dict[str, Any],
               recu_ms: float) -> dict[str, Any]:
    return {
        "type": "trou",
        "symbole": symbole,
        "attendu_premier_id": attendu,
        "recu_premier_id": int(charge["U"]),
        "recu_dernier_id": int(charge["u"]),
        "recu_ms": recu_ms,
        "horloge": "utc",
        "source": "binance-spot",
    }


def ligne_trade(symbole: str, charge: dict[str, Any], recu_ms: float) -> dict[str, Any]:
    """Une transaction agrégée, avec le côté de celui qui a traversé le spread.

    ``m`` vaut vrai quand l'ACHETEUR était le teneur de marché : l'agresseur est
    alors le vendeur. Inverser ce champ retournerait tout déséquilibre de flux
    sans qu'aucun test de forme ne s'en aperçoive, d'où le nom explicite.
    """
    acheteur_est_maker = bool(charge["m"])
    return {
        "type": "trade",
        "symbole": symbole,
        "agg_id": int(charge["a"]),
        "prix": charge["p"],
        "quantite": charge["q"],
        "trade_ms": float(charge["T"]),
        "event_ms": float(charge["E"]),
        "acheteur_est_maker": acheteur_est_maker,
        "cote_agresseur": "vendeur" if acheteur_est_maker else "acheteur",
        "recu_ms": recu_ms,
        "latence_ms": recu_ms - float(charge["E"]),
        "horloge": "utc",
        "source": "binance-spot",
    }


# ─────────────────────────────────────────── écriture

class Archive:
    """Écriture append-only, un fichier par symbole, jour UTC et nature.

    Les descripteurs restent ouverts et sont vidés à chaque ligne : à 100 ms par
    différentiel, rouvrir le fichier coûterait plus que l'écriture elle-même,
    mais un tampon non vidé perdrait les dernières secondes à chaque arrêt — et
    c'est justement l'arrêt qu'on ne contrôle pas.
    """

    def __init__(self, dossier: Path | None = None) -> None:
        self.dossier = Path(dossier) if dossier is not None else DOSSIER
        self._ouverts: dict[tuple[str, str, str], Any] = {}
        self.compteurs: dict[str, int] = {}

    def _flux(self, symbole: str, nature: str, jour: str):
        cle = (symbole, nature, jour)
        if cle not in self._ouverts:
            chemin = self.dossier / symbole / f"{jour}.{nature}.ndjson"
            chemin.parent.mkdir(parents=True, exist_ok=True)
            self._ouverts[cle] = chemin.open("a", encoding="utf-8")
        return self._ouverts[cle]

    def ecrire(self, ligne: dict[str, Any]) -> None:
        nature = "trades" if ligne["type"] == "trade" else "depth"
        flux = self._flux(ligne["symbole"], nature, _jour(ligne["recu_ms"]))
        flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        flux.flush()
        self.compteurs[ligne["type"]] = self.compteurs.get(ligne["type"], 0) + 1

    def fermer(self) -> None:
        for flux in self._ouverts.values():
            flux.close()
        self._ouverts.clear()


# ─────────────────────────────────────────── relecture

#: Instantanés qui OUVRENT une session : ce qui précède est sans garantie de
#: continuité. ``amorce`` marque un démarrage du collecteur, ``trou`` une
#: rupture de séquence. Un rejeu ne doit jamais franchir l'une de ces bornes ;
#: un instantané ``periodique``, lui, se situe à l'intérieur d'une session
#: continue et sert précisément de point de reprise.
RUPTURES = ("amorce", "trou")


def sessions(lignes: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Découpe l'archive en tranches continues ``(debut, fin)``.

    Une session commence à un instantané de rupture et s'arrête juste avant la
    suivante. Rejouer d'une session à l'autre produit un carnet faux sans
    lever d'erreur : c'est exactement ce qui est arrivé le 16/08/2026 après un
    redémarrage du collecteur, 273 mises à jour perdues en deux secondes et
    une reconstruction silencieusement fausse. La borne est donc explicite.
    """
    departs = [i for i, ligne in enumerate(lignes)
               if ligne["type"] == "instantane" and ligne.get("raison") in RUPTURES]
    return [(depart, departs[rang + 1] if rang + 1 < len(departs) else len(lignes))
            for rang, depart in enumerate(departs)]


def reconstruire_carnet(lignes: list[dict[str, Any]], *, depuis: int = 0,
                        jusqu_a_id: int | None = None) -> tuple[dict, dict, int]:
    """Rejoue une session et rend ``(bids, asks, differentiels_appliques)``.

    Cette fonction est le contrat de l'archive : si elle ne suffit pas à
    retrouver le carnet, le format est faux, et aucun volume de collecte ne le
    rattrapera. Elle vit donc ici, avec l'écriture, et non dans un script à
    part que personne ne tiendrait à jour.

    ``depuis`` est l'index de la SESSION, pas celui d'un instantané quelconque.
    Le rejeu s'arrête à la fin de cette session : franchir une rupture rendrait
    un carnet plausible et faux.

    Règles de rejeu : partir de l'instantané d'ouverture, ignorer les
    différentiels dont ``dernier_id`` ne dépasse pas le sien — ils sont
    antérieurs, l'archive les garde par principe — puis appliquer les suivants
    dans l'ordre. Une quantité nulle SUPPRIME le niveau ; c'est ainsi que
    Binance exprime un retrait, et le lire comme « niveau à zéro » laisserait un
    carnet plein de fantômes.
    """
    tranches = sessions(lignes)
    if not tranches:
        raise ValueError("archive sans instantane d ouverture : rien a reconstruire")
    depart, arret = tranches[depuis]
    base = lignes[depart]
    bids = dict(base["bids"])
    asks = dict(base["asks"])
    appliques = 0
    for ligne in lignes[depart + 1:arret]:
        if ligne["type"] != "diff":
            continue
        if ligne["dernier_id"] <= base["last_update_id"]:
            continue
        if jusqu_a_id is not None and ligne["dernier_id"] > jusqu_a_id:
            break
        for carnet, cote in ((bids, "bids"), (asks, "asks")):
            for prix, quantite in ligne[cote]:
                if float(quantite) == 0.0:
                    carnet.pop(prix, None)
                else:
                    carnet[prix] = quantite
        appliques += 1
    return bids, asks, appliques


def verifier_archive(chemin: Path, *, profondeur: int = 20) -> dict[str, Any]:
    """Contrôle qu'un fichier ``.depth.ndjson`` se rejoue bien, session par session.

    Le test : dans une session continue, reconstruire depuis l'instantané
    d'ouverture jusqu'au numéro d'un instantané périodique, et comparer le
    sommet des deux carnets. Deux états produits par des chemins indépendants —
    l'un rejoué, l'autre reçu de la place — doivent coïncider exactement. C'est
    la seule vérification qui prouve que l'archive vaut ce qu'elle prétend.

    Les ruptures ne sont pas des échecs : un redémarrage du collecteur ou une
    coupure réseau ouvre une session, et l'archive le dit. Elles sont comptées
    et rapportées, parce qu'un consommateur doit savoir combien de fois il
    devra repartir d'un instantané.
    """
    lignes = [json.loads(x) for x in
              Path(chemin).read_text(encoding="utf-8").splitlines() if x.strip()]
    tranches = sessions(lignes)
    verifiees = divergentes = appliques_total = 0
    for rang, (depart, arret) in enumerate(tranches):
        cibles = [ligne for ligne in lignes[depart + 1:arret]
                  if ligne["type"] == "instantane"]
        if not cibles:
            continue
        cible = cibles[0]
        bids, asks, appliques = reconstruire_carnet(
            lignes, depuis=rang, jusqu_a_id=cible["last_update_id"])
        sommet_bids = sorted(bids.items(), key=lambda kv: -float(kv[0]))[:profondeur]
        sommet_asks = sorted(asks.items(), key=lambda kv: float(kv[0]))[:profondeur]
        verifiees += 1
        appliques_total += appliques
        if (sommet_bids != [tuple(x) for x in cible["bids"][:profondeur]]
                or sommet_asks != [tuple(x) for x in cible["asks"][:profondeur]]):
            divergentes += 1
    if not verifiees:
        verdict = "indecidable"
    elif divergentes:
        verdict = "divergent"
    else:
        verdict = "reconstruit"
    return {
        "chemin": str(chemin),
        "verdict": verdict,
        "lignes": len(lignes),
        "sessions": len(tranches),
        "sessions_verifiees": verifiees,
        "sessions_divergentes": divergentes,
        "differentiels_appliques": appliques_total,
        "trous": sum(1 for ligne in lignes if ligne["type"] == "trou"),
    }


def compacter(racine: Path, *, aujourdhui: str | None = None) -> list[tuple[Path, float]]:
    """Compresse les archives des jours CLOS. Rend ``(chemin, taux)`` par fichier.

    Le carnet à 100 ms coûte environ 0,7 Go par jour et par symbole en clair.
    Du JSON aussi répétitif se comprime d'un facteur dix : sans compaction,
    l'archive devient un problème de disque avant d'être une réponse.

    Le fichier du jour en cours n'est jamais touché : un collecteur y écrit,
    et compresser sous ses pieds perdrait la fin de la journée. Le fichier
    clair est supprimé seulement après relecture intégrale du ``.gz`` — une
    compaction qui perd des lignes serait pire que pas de compaction.
    """
    import gzip

    jour_courant = aujourdhui or _jour(_maintenant_ms())
    resultats = []
    for chemin in sorted(Path(racine).rglob("*.ndjson")):
        if chemin.name.startswith(jour_courant):
            continue
        cible = chemin.with_suffix(".ndjson.gz")
        if cible.exists():
            continue
        brut = chemin.read_bytes()
        with gzip.open(cible, "wb", compresslevel=6) as sortie:
            sortie.write(brut)
        with gzip.open(cible, "rb") as controle:
            if controle.read() != brut:
                cible.unlink(missing_ok=True)
                raise OSError(f"compaction infidele, fichier clair conserve : {chemin}")
        taille = chemin.stat().st_size
        chemin.unlink()
        resultats.append((cible, cible.stat().st_size / taille if taille else 1.0))
    return resultats


# ─────────────────────────────────────────── réseau

def instantane_rest(symbole: str, *, profondeur: int = PROFONDEUR,
                    hote: str = HOTE_REST) -> dict[str, Any]:
    """Instantané complet du carnet. Requête publique, sans clé ni signature."""
    import urllib.request

    url = f"{hote}/api/v3/depth?symbol={symbole}&limit={int(profondeur)}"
    with urllib.request.urlopen(url, timeout=15) as reponse:
        return json.loads(reponse.read().decode("utf-8"))


def url_flux(symboles: tuple[str, ...], *, hote: str = HOTE_WS) -> str:
    flux = []
    for symbole in symboles:
        bas = symbole.lower()
        flux.append(f"{bas}@depth@100ms")
        flux.append(f"{bas}@aggTrade")
    return f"{hote}/stream?streams={'/'.join(flux)}"


async def _messages_websocket(url: str):
    """Générateur de messages bruts. Isolé pour rester remplaçable en test."""
    import websockets  # import tardif : dépendance optionnelle de l'archive

    async with websockets.connect(url, ping_interval=20, max_queue=None) as socket:
        async for message in socket:
            yield message


# ─────────────────────────────────────────── boucle

async def enregistrer(
    symboles: tuple[str, ...],
    *,
    archive: Archive,
    duree_s: float = 0.0,
    instantane_s: float = INSTANTANE_S,
    messages=None,
    instantane=None,
    horloge=None,
) -> dict[str, int]:
    """Consomme le flux et archive. Rend les compteurs par nature de ligne.

    Deux décisions gouvernent cette boucle.

    **L'instantané est demandé après le premier différentiel reçu, jamais
    avant.** Prendre l'instantané puis ouvrir le flux laisse un trou de la durée
    de la connexion — mesuré à deux trous d'amorce sur deux symboles lors du
    premier essai. En attendant que le flux coule, l'instantané tombe forcément
    à l'intérieur de la chaîne de numéros, et l'amorce est propre. Les
    différentiels émis pendant l'appel REST ne sont pas perdus : ils
    s'accumulent dans la file de la connexion et arrivent ensuite dans l'ordre.

    **Tout différentiel reçu est écrit, y compris antérieur à l'instantané.** La
    séquence sert à *annoter* les ruptures, pas à filtrer. Un collecteur qui
    décide de jeter une ligne est un collecteur qui peut jeter la mauvaise ; le
    consommateur, lui, retrouve l'état exact par les numéros. C'est la même
    règle que pour l'anneau RAM de V12 : ce qui n'est pas écrit est perdu.

    ``messages`` et ``instantane`` sont injectables pour que toute la logique —
    séquence, trous, resynchronisation, rotation de fichier — soit testable sans
    réseau. Un collecteur qu'on ne peut essayer qu'en direct n'est pas vérifiable
    avant la panne.
    """
    messages = messages or (lambda: _messages_websocket(url_flux(symboles)))
    instantane = instantane or (lambda s: instantane_rest(s))
    horloge = horloge or _maintenant_ms

    sequences = {s: SequenceCarnet() for s in symboles}
    prochain_instantane: dict[str, float] = {}
    debut = time.monotonic()

    async def poser_instantane(symbole: str, raison: str, *, reamorcer: bool) -> None:
        """Écrit un instantané complet.

        ``reamorcer`` distingue deux usages qu'il serait grave de confondre :
        l'amorce et la reprise après trou repartent la chaîne, l'instantané
        périodique n'est qu'un point de reprise pour le lecteur et doit laisser
        la chaîne intacte. Réinitialiser à chaque instantané périodique ferait
        passer pour « déjà vus » les différentiels encore en file.
        """
        charge = await asyncio.to_thread(instantane, symbole)
        if reamorcer:
            sequences[symbole].amorcer(charge["lastUpdateId"])
        archive.ecrire(ligne_instantane(symbole, charge, raison, horloge()))
        prochain_instantane[symbole] = time.monotonic() + instantane_s

    async for brut in messages():
        enveloppe = json.loads(brut)
        charge = enveloppe.get("data", enveloppe)
        flux = str(enveloppe.get("stream", ""))
        recu = horloge()

        if flux.endswith("@aggTrade") or charge.get("e") == "aggTrade":
            archive.ecrire(ligne_trade(str(charge["s"]), charge, recu))
        else:
            symbole = str(charge["s"])
            sequence = sequences.setdefault(symbole, SequenceCarnet())
            if sequence.amorce_id is None:
                await poser_instantane(symbole, "amorce", reamorcer=True)
            verdict = sequence.verdict(int(charge["U"]), int(charge["u"]))
            archive.ecrire(ligne_diff(symbole, charge, recu))
            if verdict == "trou":
                attendu = None if sequence.dernier_id is None else sequence.dernier_id + 1
                archive.ecrire(ligne_trou(symbole, attendu, charge, recu))
                await poser_instantane(symbole, "trou", reamorcer=True)

        for symbole, echeance in list(prochain_instantane.items()):
            if time.monotonic() >= echeance:
                await poser_instantane(symbole, "periodique", reamorcer=False)

        if duree_s and time.monotonic() - debut >= duree_s:
            break

    return dict(archive.compteurs)


async def _boucle_resiliente(symboles: tuple[str, ...], archive: Archive,
                             duree_s: float, instantane_s: float) -> dict[str, int]:
    """Reconnecte avec un délai croissant. Une coupure crée un trou daté, pas
    une fin de collecte : c'est le seul comportement acceptable pour une archive
    censée tourner des semaines."""
    debut = time.monotonic()
    attente = 1.0
    while True:
        restant = 0.0 if not duree_s else duree_s - (time.monotonic() - debut)
        if duree_s and restant <= 0:
            break
        try:
            await enregistrer(symboles, archive=archive, duree_s=restant,
                              instantane_s=instantane_s)
            if duree_s:
                break
        except asyncio.CancelledError:
            raise
        except Exception as erreur:  # réseau, DNS, message inattendu
            print(f"  coupure : {type(erreur).__name__} {erreur} — reprise dans "
                  f"{attente:.0f}s", flush=True)
            await asyncio.sleep(attente)
            attente = min(attente * 2, RECONNEXION_MAX_S)
        else:
            attente = 1.0
    return dict(archive.compteurs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symboles", nargs="*", default=list(SYMBOLES))
    ap.add_argument("--duree", type=float, default=0.0,
                    help="secondes de collecte ; 0 = sans fin")
    ap.add_argument("--instantane", type=float, default=INSTANTANE_S,
                    help="secondes entre deux instantanes complets")
    ap.add_argument("--dossier", default=None)
    ap.add_argument("--verifier", default=None, metavar="DOSSIER",
                    help="ne collecte pas : rejoue les archives et rend le verdict")
    ap.add_argument("--compacter", default=None, metavar="DOSSIER",
                    help="ne collecte pas : comprime les archives des jours clos")
    args = ap.parse_args(argv)

    if args.compacter:
        faits = compacter(Path(args.compacter))
        if not faits:
            print("Aucun jour clos a compacter.", flush=True)
            return 0
        for chemin, taux in faits:
            print(f"  {chemin.name}  -> {taux:.1%} de la taille initiale", flush=True)
        return 0

    if args.verifier:
        racine = Path(args.verifier)
        fichiers = sorted(racine.rglob("*.depth.ndjson"))
        if not fichiers:
            print(f"Aucune archive de carnet sous {racine}", flush=True)
            return 1
        divergents = 0
        for fichier in fichiers:
            rapport = verifier_archive(fichier)
            divergents += rapport["verdict"] == "divergent"
            print(f"{rapport['verdict']:>12}  {fichier.relative_to(racine)}  "
                  f"lignes={rapport['lignes']}  "
                  f"sessions={rapport.get('sessions', 0)}"
                  f"(verifiees {rapport.get('sessions_verifiees', 0)})  "
                  f"diffs={rapport.get('differentiels_appliques', 0)}  "
                  f"trous={rapport.get('trous', 0)}", flush=True)
        return 1 if divergents else 0

    symboles = tuple(s.upper() for s in args.symboles)
    archive = Archive(Path(args.dossier) if args.dossier else None)
    print(f"Archive du carnet L2 Binance : {', '.join(symboles)} -> {archive.dossier}",
          flush=True)
    print("Flux publics, aucune cle, aucun ordre, aucun compte.", flush=True)
    try:
        compteurs = asyncio.run(
            _boucle_resiliente(symboles, archive, args.duree, args.instantane))
    except KeyboardInterrupt:
        compteurs = dict(archive.compteurs)
    finally:
        archive.fermer()
    print(f"{datetime.now(timezone.utc):%H:%M:%S}Z  " + "  ".join(
        f"{nature}={nombre}" for nature, nombre in sorted(compteurs.items())), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
