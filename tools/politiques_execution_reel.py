#!/usr/bin/env python
"""Les 15 politiques d'execution, jugees sur des barres M5 REELLES.

Ce que ce module corrige
------------------------
La matrice de ``titanium.execution_sim`` classe les politiques sur une marche
aleatoire : symbole ``SYNTH``, douze snapshots par scenario, carnet invente.
Son propre encart le dit (``data_fidelity = synthetic_l1``). Un classement
obtenu ainsi decrit le generateur de nombres, pas le courtier.

Ici, tout ce qui peut etre reel l'est :

* les barres viennent de ``results/barres/M5`` -- archive du compte DEMO ;
* le **spread est celui de chaque barre**, pas une constante : l'archive porte
  la colonne ``spread`` en points, releve par le courtier ;
* les DECISIONS viennent des artefacts du rejeu (``rejeu_univers_brut``), dont
  le manifeste, les sceaux et l'empreinte moteur sont VERIFIES avant lecture ;
* la profondeur du carnet, elle, reste reconstruite -- c'est dit dans chaque
  ligne produite (``fidelite``), et non enfoui dans une note de bas de page.

Ce que la revue independante a corrige (24/08/2026)
---------------------------------------------------
Codex et Hermes ont rendu six NO-GO sur la premiere version (hub offsets 578,
582, 586, 588). Ils sont traites ici, chacun a un test :

1. **L'OHLC MetaTrader5 est le BID, pas le milieu.** La premiere version
   posait ``bid = close - spread/2`` : sur ``close=100, spread=0.20`` elle
   fabriquait ``bid=99.90 / ask=100.10`` au lieu de ``bid=100 / ask=100.20``.
   Tout achat passif etait donc servi 10 points trop bas.
2. **L'enveloppe d'une VENTE est le haut de l'ASK**, pas le haut du bid :
   ``matching.py`` compare ``market.high`` a la limite de vente. Un snapshot
   porte donc ``low`` = plus bas du BID et ``high`` = plus haut de l'ASK.
3. **La politique du bot n'est plus une copie** : ``v14_live`` appelle
   ``titanium.execution.limit_pricing``, la meme fonction que la boucle.
4. **Plus de regard vers le futur** : une barre M5 n'est connue qu'a sa
   cloture. La decision est planifiee sur la DERNIERE barre CLOSE, et
   l'appariement commence a la barre SUIVANTE. Un ordre dont l'expiration
   tombe a l'interieur d'une barre (TTL 120 s sur M5) n'est pas resolvable :
   il est declare INDETERMINE ex ante, jamais compte 0.
5. **Le classement ne retient que les politiques directionnelles et
   resolvables** : la tenue de marche, le multi-jambes et le maker-puis-hedge
   ne repondent pas a la question posee ; une politique dont toutes les
   tranches tombent dans la meme barre M5 n'est pas separable de ses voisines.
6. **Un artefact non scelle n'est pas lu** : manifeste, ``artifact_type``,
   sceaux, compteurs et empreinte moteur sont valides avant la premiere ligne.

La question a laquelle il repond
--------------------------------
« Quelle politique laisse le plus de R par DECISION ? » -- par decision, et non
par trade rempli : une politique passive qui n'est pas servie ne fait pas un
trade neutre, elle fait un trade qui n'existe pas, et son R vaut zero. Compter
la moyenne sur les seuls remplissages flatterait mecaniquement les plus
timides.

    effet_r(decision) = rempli ? part * (net_r + gain_entree_r) : 0

ou ``gain_entree_r`` est l'ecart de prix d'entree contre la politique MARKET,
normalise par la distance de stop du trade.

Discipline de choix
-------------------
Le classement se choisit sur la CALIBRATION et se juge sur la VERIFICATION,
comme tout le reste du projet. Les deux segments viennent du rejeu lui-meme.

Limite assumee, ecrite dans le rapport
--------------------------------------
Une barre M5 ne resout pas l'intra-barre. Les politiques qui ne different que
par un ordonnancement de quelques secondes (TWAP, POV, adaptive) sont ici
indistinguables : leur seul effet mesurable est le niveau de prix et le fait
d'etre servi ou non. Elles sont donc SORTIES du classement, pas classees en
silence. Les separer demande l'archive de ticks L1, qui ne commence qu'au
22/08/2026 -- et cette archive ne donne qu'une borne SUPERIEURE de touche,
jamais un taux de remplissage (revue Hermes H2).

Lecture seule. Aucun ordre, aucun compte, aucun seuil touche.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import statistics as st
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from titanium.execution_sim.config import load_config  # noqa: E402
from titanium.execution_sim.models import (  # noqa: E402
    BookLevel,
    ExecutionIntent,
    MarketSnapshot,
    Side,
)
from titanium.execution_sim.runner import (  # noqa: E402
    ALL_POLICIES,
    engine_fingerprint,
    executer_sur_snapshots,
)
from tools import epoque_rejeu  # noqa: E402
from tools.rejeu_univers import artefact_brut_valide  # noqa: E402

BARRES = RACINE / "results" / "barres"
SPECIFICATIONS = BARRES / "_specifications.json"
BRUT = RACINE / "results" / "rejeu_univers_brut"
RESUMES = RACINE / "results" / "rejeu_univers"
SORTIE = RACINE / "results" / "politiques_execution_reel.json"

#: Barres M5 offertes a une politique pour se faire servir, APRES la decision.
#: 12 barres = 1 h, soit l'ordre de grandeur du TTL le plus long de la boucle.
FENETRE = 12
SECONDES_PAR_BARRE = 300.0

#: Contrat des artefacts de rejeu lus ici. Il n'existe pas de ``schema 4`` :
#: « v4 » designe la GENERATION du backfill, le contrat machine reste 2
#: (rectification Codex, hub offset 588).
ARTIFACT_TYPE = "v14.offline_replay.trades"
SCHEMA_ARTEFACT = 2

#: Le cout du courtier sur ces instruments EST le spread, deja porte par chaque
#: barre. Ajouter des frais en points de base par-dessus compterait deux fois.
#: L'option existe pour les venues qui facturent vraiment au notionnel.
MAKER_BPS_DEFAUT = 0.0
TAKER_BPS_DEFAUT = 0.0

STATUT_MESURE = "MESURE"
STATUT_BLOQUE = "ANALYSIS_BLOCKED"
STATUT_SANS_ARTEFACT = "NO_ARTIFACT_IN_VALID_CORPUS"

#: Refus qui portent sur l'INTEGRITE du corpus lu. Aucun symbole valide pour
#: l'une de ces raisons n'est pas un classement vide : c'est une mesure qui
#: n'a pas eu lieu.
MOTIFS_BLOQUANTS = ("manifeste_absent", "artifact_type", "schema_version",
                    "epoque_moteur", "sceaux_ou_compteurs_invalides",
                    "resume_absent")


def _sha256(donnees: bytes) -> str:
    return hashlib.sha256(donnees).hexdigest()


def _sha256_fichier(chemin: Path) -> str:
    try:
        return _sha256(Path(chemin).read_bytes())
    except OSError:
        return ""


def _canonique(objet: object) -> bytes:
    return json.dumps(objet, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def statut_analyse(valides: int, refuses: dict[str, str]) -> tuple[str, str]:
    """Statut publiable : mesure faite, corpus sans artefact, ou analyse bloquee.

    Zero artefact valide n'est jamais un resultat. Le 25/08/2026, un commit
    sur un fichier moteur a fait refuser 147/147 artefacts en silence, avec un
    code de sortie nul.
    """
    # L'INTEGRITE prime sur le rendement : un seul artefact demande dont le
    # sceau est casse bloque le classement, meme si les autres sont valides.
    # Un classement partiel a l'air d'un classement complet (bloqueur 1 de la
    # revue Codex, hub offset 649).
    bloquants = sorted({
        prefixe for motif in refuses.values()
        for prefixe in MOTIFS_BLOQUANTS if str(motif).startswith(prefixe)
    })
    if bloquants:
        return STATUT_BLOQUE, "|".join(bloquants)
    if valides:
        return STATUT_MESURE, ""
    return STATUT_SANS_ARTEFACT, ""


#: Sous cet effectif, une cellule ne conclut pas.
EFFECTIF_MIN = 60

#: Segments de la coupe chronologique refaite sur la couverture M5.
SEGMENTS = ("choix", "jugement")

#: Politiques qui ne repondent pas a la question « comment entrer dans une
#: position directionnelle decidee ». La tenue de marche cote des deux cotes,
#: le multi-jambes porte une jambe opposee, le maker-puis-hedge n'a de sens
#: qu'avec le cout de sa couverture -- que ce banc ne mesure pas.
NON_DIRECTIONNELLES = (
    "market_making",
    "multi_leg_simultaneous",
    "maker_then_hedge_taker",
)

#: Ces politiques existent dans le simulateur, mais leur comportement annonce
#: n'est pas reproduit par le runner a la granularite M5 : cancel/replace est
#: pose en fin de fenetre au lieu de son TTL, le peg n'est jamais repricie,
#: VWAP ne recoit pas de profil historique et POV n'est pas rappele sur les
#: volumes futurs. Les mesurer reste utile pour diagnostiquer le harnais ; les
#: classer serait attribuer leurs scores a des politiques qui n'ont pas tourne.
NON_FIDELES_M5 = (
    "cancel_replace",
    "pegged",
    "vwap",
    "pov",
)

#: Les quinze politiques de recherche ET celle qui tourne vraiment, moins
#: celles qui ne repondent pas a la question.
POLITIQUES = tuple(p for p in (*ALL_POLICIES, "v14_live")
                   if p not in NON_DIRECTIONNELLES)

#: Motifs d'exclusion du classement, ecrits dans le rapport.
MOTIF_NON_DIRECTIONNELLE = "non_directionnelle"
MOTIF_INTRA_BARRE = "sequence_intra_barre_non_resolue"
MOTIF_NON_FIDELE_M5 = "implementation_runner_non_fidele_a_la_politique"
MOTIF_COHORTE_PARTIELLE = "cohorte_resolue_incomplete_non_comparable"


def specifications(chemin: Path = SPECIFICATIONS) -> dict:
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def charger_barres(symbole: str, tf: str = "M5", racine: Path = BARRES) -> pd.DataFrame:
    chemin = Path(racine) / tf / f"{symbole}.parquet"
    colonnes = ["time_utc", "open", "high", "low", "close", "tick_volume",
                "spread", "reconstruit"]
    barres = pd.read_parquet(chemin, columns=colonnes)
    # Une barre reconstruite a high == low : le carnet qu'on en tirerait serait
    # un carnet plat, ou toute politique passive est servie au prix exact.
    barres = barres[~barres["reconstruit"].astype(bool)]
    return barres.sort_values("time_utc").reset_index(drop=True)


def _carnet(bid: float, ask: float, volume: float, point: float) -> tuple:
    """Deux niveaux de chaque cote. La PROFONDEUR est reconstruite : l'archive
    ne porte que le sommet, et ce module le dit dans chaque ligne produite."""
    profondeur = max(volume, 1.0)
    ecart = max(ask - bid, point)
    return (
        (BookLevel(bid, profondeur * 0.4), BookLevel(bid - ecart, profondeur * 0.6)),
        (BookLevel(ask, profondeur * 0.4), BookLevel(ask + ecart, profondeur * 0.6)),
    )


def _bid_ask(barre, point: float) -> tuple[float, float]:
    """L'OHLC MetaTrader5 est cote BID. L'ask s'en deduit par le spread.

    C'etait le P0 le plus couteux de la premiere version : traiter ``close``
    comme un milieu deplacait tout achat passif d'un demi-spread en sa faveur.
    """
    bid = float(barre["close"])
    return bid, bid + max(float(barre["spread"]), 0.0) * point


def snapshot_de_decision(barre, symbole: str, *, point: float,
                         instant: int) -> MarketSnapshot:
    """Ce que l'on SAIT a l'instant de la decision, et rien de plus.

    ``barre`` est la derniere barre M5 CLOSE a cet instant : son close, son
    haut et son bas sont acquis. L'enveloppe du snapshot est volontairement
    DEGENEREE (``low = bid``, ``high = ask``) : a l'instant precis de la
    decision, seul le sommet du carnet existe. Sans cela, un ordre pose a
    l'instant t serait servi grace au parcours de la barre qui commence a t --
    c'est-a-dire grace a l'avenir (revue Hermes, hub offset 582).
    """
    bid, ask = _bid_ask(barre, point)
    volume = float(barre["tick_volume"])
    niveaux_bid, niveaux_ask = _carnet(bid, ask, volume, point)
    amplitude = float(barre["high"]) - float(barre["low"])
    return MarketSnapshot(
        timestamp=datetime.fromtimestamp(int(instant), tz=timezone.utc),
        symbol=symbole, bid=bid, ask=ask,
        bid_levels=niveaux_bid, ask_levels=niveaux_ask,
        open=bid, high=ask, low=bid, close=bid,
        volume=volume,
        # Volatilite MESUREE sur la barre deja close, jamais sur la suivante.
        volatility_bps=amplitude / max(bid, 1e-9) * 10_000.0,
        event_id=f"{symbole}:decision:{int(instant)}",
    )


def snapshots_de_matching(barres: pd.DataFrame, debut: int, symbole: str, *,
                          point: float, fenetre: int = FENETRE) -> list[MarketSnapshot]:
    """Barres POSTERIEURES a la decision, avec leur enveloppe touchable.

    Chaque snapshot est horodate a l'OUVERTURE de sa barre : c'est l'instant a
    partir duquel un ordre vit pendant tout l'intervalle ``[open, open+300)``.
    L'enveloppe donne ``low`` = plus bas du BID (ce qu'un achat peut toucher)
    et ``high`` = plus haut de l'ASK (ce qu'une vente peut toucher). Le
    simulateur compare exactement ces deux champs aux limites.
    """
    tranche = barres.iloc[debut:debut + fenetre]
    snapshots: list[MarketSnapshot] = []
    for _, barre in tranche.iterrows():
        bid, ask = _bid_ask(barre, point)
        spread = ask - bid
        volume = float(barre["tick_volume"])
        niveaux_bid, niveaux_ask = _carnet(bid, ask, volume, point)
        bas, haut = float(barre["low"]), float(barre["high"]) + spread
        snapshots.append(MarketSnapshot(
            timestamp=datetime.fromtimestamp(int(barre["time_utc"]), tz=timezone.utc),
            symbol=symbole, bid=bid, ask=ask,
            bid_levels=niveaux_bid, ask_levels=niveaux_ask,
            open=float(barre["open"]), high=haut, low=bas, close=bid,
            volume=volume,
            volatility_bps=(float(barre["high"]) - float(barre["low"]))
            / max(bid, 1e-9) * 10_000.0,
            event_id=f"{symbole}:{int(barre['time_utc'])}",
        ))
    return snapshots


def manifeste(symbole: str, brut: Path = BRUT) -> dict:
    try:
        return json.loads((Path(brut) / symbole / "manifest.json")
                          .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def valider_artefact(symbole: str, *, brut: Path = BRUT,
                     resumes: Path = RESUMES,
                     empreinte_attendue: str | None = None) -> tuple[bool, str]:
    """Refuse tout artefact non scelle, hors contrat ou d'une autre epoque.

    Lire ``trades.ndjson`` sans passer par la ne dit pas quel moteur a produit
    ces trades : deux generations melangees donnent un classement qui decrit
    l'historique des commits. La validation reutilise le validateur du rejeu
    lui-meme (sceau du manifeste, octets et sha256 du fichier, compteurs,
    identifiants, arithmetique) et y ajoute le type d'artefact et l'epoque.
    """
    meta = manifeste(symbole, brut)
    if not meta:
        return False, "manifeste_absent"
    if meta.get("artifact_type") != ARTIFACT_TYPE:
        return False, f"artifact_type={meta.get('artifact_type')!r}"
    if meta.get("schema_version") != SCHEMA_ARTEFACT:
        return False, f"schema_version={meta.get('schema_version')!r}"
    attendue = (empreinte_attendue if empreinte_attendue is not None
                else epoque_rejeu.empreinte_courante())
    signee = epoque_rejeu.empreinte_manifeste(meta)
    if attendue and signee != attendue:
        return False, f"epoque_moteur={signee[:16] or 'absente'}"
    # Le resume entre dans le sceau du manifeste : le valider sans lui laisse
    # passer un artefact dont le resume publie ne correspond plus aux trades.
    resume = Path(resumes) / f"{symbole}.json"
    if not resume.exists():
        return False, "resume_absent"
    if not artefact_brut_valide(Path(brut), symbole, resume_path=resume):
        return False, "sceaux_ou_compteurs_invalides"
    return True, "ok"


def decisions(symbole: str, *, limite: int | None = None,
              brut: Path = BRUT, empreinte_attendue: str | None = None) -> list[dict]:
    """Decisions reelles du rejeu : instant, sens, stop, resultat net.

    ECHEC FERME : sans artefact valide, aucune decision n'est rendue. Un
    classement bati sur un artefact non verifie serait un classement sur une
    provenance inconnue.
    """
    valide, motif = valider_artefact(symbole, brut=brut,
                                     empreinte_attendue=empreinte_attendue)
    if not valide:
        return []
    chemin = Path(brut) / symbole / "trades.ndjson"
    sorties: list[dict] = []
    try:
        flux = chemin.open("r", encoding="utf-8")
    except OSError:
        return sorties
    with flux:
        for ligne in flux:
            if not ligne.strip():
                continue
            try:
                trade = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            try:
                instant = datetime.fromisoformat(
                    str(trade["decision_at"]).replace("Z", "+00:00"))
                sorties.append({
                    # Sans identifiant ni instant, une ligne de sortie n'est
                    # pas reauditable : on ne peut plus refaire l'appariement.
                    "decision_id": str(trade["trade_id"]),
                    "decision_at": int(instant.timestamp()),
                    "side": int(trade["side"]),
                    "split": str(trade.get("split") or ""),
                    "r_unit": float(trade["r_unit"]),
                    "net_r": float(trade["net_r"]),
                    "prix_entree": float(trade["prix_entree"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
    if limite is not None and len(sorties) > limite:
        pas = max(1, len(sorties) // limite)
        sorties = sorties[::pas][:limite]
    return sorties


def couvertes(decisions_: list[dict], barres: pd.DataFrame, *,
              fenetre: int = FENETRE,
              duree: float = SECONDES_PAR_BARRE) -> list[dict]:
    """Decisions dont le voisinage M5 est reellement observe, sans trou.

    Trois conditions, chacune verifiee et comptee :

    1. **couverture** -- l'archive M5 est plafonnee a 100 000 barres, soit
       environ un an, alors que le rejeu decide sur plusieurs annees de M15.
       Sans ce filtre, une decision de 2023 irait chercher la premiere barre
       disponible de 2025 : le simulateur executerait un ordre a un prix sans
       rapport et rendrait un classement parfaitement faux avec un air
       parfaitement serieux.
    2. **grille** -- la decision doit tomber sur une ouverture de barre M5.
       Le rejeu decide a la cloture d'une barre M15, donc sur la grille ; une
       decision hors grille laisserait un intervalle non observe entre l'ordre
       et la premiere barre.
    3. **continuite** -- la barre precedente (celle qui porte l'information de
       decision) et les ``fenetre`` barres suivantes doivent etre contigues.
       Un trou de seance est du temps non observe : on ne peut y affirmer ni
       un remplissage ni une absence de remplissage.

    ``barre_decision`` indexe la derniere barre CLOSE, ``barre`` la premiere
    barre d'appariement.
    """
    if barres.empty:
        return []
    temps = barres["time_utc"].to_numpy()
    pas = int(duree)
    positions = {int(t): i for i, t in enumerate(temps)}
    gardees = []
    for decision in decisions_:
        instant = int(decision["decision_at"])
        debut = positions.get(instant)
        if debut is None or debut == 0:
            continue
        if positions.get(instant - pas) != debut - 1:
            continue
        if debut + fenetre > len(temps):
            continue
        if int(temps[debut + fenetre - 1]) != instant + pas * (fenetre - 1):
            continue
        decision = dict(decision)
        decision["barre_decision"] = debut - 1
        decision["barre"] = debut
        gardees.append(decision)
    return gardees


def _prix_moyen(ordres, side: Side) -> tuple[float, float]:
    """Quantite remplie et prix moyen des ordres d'ENTREE, du bon cote.

    Le filtre sur le sens n'est pas cosmetique : une jambe opposee (couverture,
    multi-jambes) remplie a un autre prix ferait baisser le prix moyen d'entree
    d'un achat sans qu'aucun achat n'ait ete mieux servi.
    """
    quantite = prix = 0.0
    for ordre in ordres:
        if ordre.metadata.get("hedge_leg"):
            continue
        if ordre.side is not side:
            continue
        if ordre.filled_quantity <= 0:
            continue
        quantite += ordre.filled_quantity
        prix += ordre.avg_fill_price * ordre.filled_quantity
    return (quantite, prix / quantite if quantite > 0 else 0.0)


def _premier_contact_prix(ordres, snapshots: list[MarketSnapshot], side: Side,
                          *, inclusif: bool) -> datetime | None:
    """Premier contact/franchissement OHLC, sans inferer un remplissage."""
    premier: datetime | None = None
    for ordre in ordres:
        if ordre.metadata.get("hedge_leg") or ordre.side is not side:
            continue
        if ordre.limit_price is None:
            continue
        debut = ordre.created_at + timedelta(milliseconds=ordre.scheduled_offset_ms)
        for snapshot in snapshots[1:]:
            if snapshot.timestamp < debut:
                continue
            if ordre.expires_at is not None and snapshot.timestamp >= ordre.expires_at:
                break
            limite = float(ordre.limit_price)
            if ordre.side is Side.BUY:
                atteint = snapshot.low <= limite if inclusif else snapshot.low < limite
            else:
                atteint = snapshot.high >= limite if inclusif else snapshot.high > limite
            if atteint and (premier is None or snapshot.timestamp < premier):
                premier = snapshot.timestamp
                break
    return premier


def _premier_service_synthetique(ordres, side: Side) -> datetime | None:
    instants = [fill.timestamp for ordre in ordres
                if not ordre.metadata.get("hedge_leg") and ordre.side is side
                for fill in ordre.fills]
    return min(instants) if instants else None


def expiration_indeterminee(ordres, *, duree: float = SECONDES_PAR_BARRE) -> bool:
    """Vrai si une expiration tombe A L'INTERIEUR d'une barre.

    Un ordre valable 120 s sur des barres de 300 s n'est pas mesurable : la
    barre qui contient son expiration ne dit pas QUAND elle a touche. Le
    simulateur, lui, verrait la barre entiere et pourrait declarer un
    remplissage survenu jusqu'a 180 s APRES l'expiration.

    Le test est fait EX ANTE, a partir du seul plan de l'ordre : il ne regarde
    ni le remplissage ni le resultat. Exclure sur le resultat biaiserait le
    taux de remplissage vers le bas en ne retirant que les touches.
    """
    for ordre in ordres:
        if ordre.expires_at is None:
            continue
        vie = (ordre.expires_at - ordre.created_at).total_seconds()
        tranches = vie / max(duree, 1e-9)
        if abs(tranches - round(tranches)) > 1e-9:
            return True
    return False


def sequence_intra_barre(ordres, *, duree: float = SECONDES_PAR_BARRE) -> bool:
    """Vrai si toutes les tranches d'un decoupage tombent dans la meme barre.

    TWAP sur 60 s, adaptive sur 30 s, iceberg qui se recharge en 100 ms : a la
    granularite M5, leurs tranches sont simultanees. Le banc peut encore les
    executer, mais il ne peut pas les DISTINGUER de la version « tout d'un
    coup » : leur donner un rang serait presenter un ordre du bruit comme un
    resultat.

    Le remplacement produit par ``cancel_replace`` est ecarte du test : il est
    pose au dernier snapshot de la fenetre, donc une heure plus tard, et son
    decalage programme vaut zero par construction.
    """
    tranches = [ordre for ordre in ordres
                if not ordre.metadata.get("hedge_leg")
                and not ordre.client_order_id.endswith(":replacement")]
    if len(tranches) < 2:
        return False
    decalages = [int(ordre.scheduled_offset_ms) for ordre in tranches]
    return (max(decalages) - min(decalages)) < duree * 1000.0


def marquer_segments(decisions_: list[dict], part_choix: float = 2 / 3) -> list[dict]:
    """Coupe chronologique PROPRE A LA COUVERTURE M5.

    Le rejeu coupe ses segments sur toute la profondeur M15 -- plusieurs
    annees. L'archive M5 ne couvre que la derniere annee : presque toutes les
    decisions auditables tombent du meme cote de cette coupe-la, et la regle
    « choisir sur la calibration » n'aurait plus rien a choisir. On refait donc
    une coupe aux deux tiers SUR LES DECISIONS COUVERTES, et on conserve le
    segment du rejeu a titre d'information.
    """
    ordonnees = sorted(decisions_, key=lambda d: d["decision_at"])
    coupure = int(len(ordonnees) * part_choix)
    marquees = []
    for rang, decision in enumerate(ordonnees):
        decision = dict(decision)
        decision["split_rejeu"] = decision.get("split", "")
        decision["split"] = "choix" if rang < coupure else "jugement"
        marquees.append(decision)
    return marquees


def _config_a_l_echelle(config: dict, prix: float) -> dict:
    """Plafonds de risque proportionnels au notionnel d'une unite."""
    notionnel = max(float(prix), 1.0)
    ajuste = json.loads(json.dumps(config))
    risque = ajuste["risk"]
    risque["max_gross_exposure"] = notionnel * 100.0
    risque["max_net_exposure"] = notionnel * 100.0
    risque["max_daily_loss"] = notionnel * 100.0
    risque["max_drawdown"] = notionnel * 100.0
    return ajuste


def evaluer_symbole(symbole: str, politiques: tuple[str, ...],
                    config_base: dict, *,
                    limite: int | None, seed: int = 14_082_026,
                    fenetre: int = FENETRE,
                    empreinte_attendue: str | None = None) -> list[dict]:
    """Une ligne par (decision, politique). La ligne MARKET sert de reference."""
    specs = specifications().get(symbole) or {}
    point = float(specs.get("point") or 0.0)
    tick = float(specs.get("tick_size") or point or 0.0)
    digits = int(specs.get("digits") or 0)
    if point <= 0 or tick <= 0:
        return []
    barres = charger_barres(symbole)
    if barres.empty:
        return []
    brutes = decisions(symbole, limite=None, empreinte_attendue=empreinte_attendue)
    retenues = couvertes(brutes, barres, fenetre=fenetre)
    if limite is not None and len(retenues) > limite:
        pas = max(1, len(retenues) // limite)
        retenues = retenues[::pas][:limite]
    retenues = marquer_segments(retenues)
    # MARKET est la reference de prix d'entree : sans elle, aucun gain n'est
    # calculable. Elle est donc toujours executee, meme si l'appelant ne l'a
    # pas demandee.
    a_executer = tuple(dict.fromkeys(("market", *politiques)))
    lignes: list[dict] = []
    for decision in retenues:
        snapshots = [snapshot_de_decision(
            barres.iloc[int(decision["barre_decision"])], symbole,
            point=point, instant=int(decision["decision_at"]))]
        snapshots += snapshots_de_matching(barres, int(decision["barre"]), symbole,
                                           point=point, fenetre=fenetre)
        if len(snapshots) < 2:
            continue
        # Les plafonds de risque du simulateur sont libelles en devise. Ecrits
        # pour un prix synthetique de 100, ils rejettent en silence tout ordre
        # sur un actif a 112 000 -- et le classement devient un classement de
        # niveaux de prix. On les met a l'echelle du notionnel de l'ordre.
        config = _config_a_l_echelle(config_base, snapshots[0].mid)
        side = Side(decision["side"])
        resultats: dict[str, tuple] = {}
        for politique in a_executer:
            intent = ExecutionIntent(
                intent_id=f"{symbole}:{decision['decision_id']}",
                symbol=symbole, side=side, quantity=1.0,
                # La politique de la boucle a besoin de la distance de stop et
                # des decimales : ce sont les donnees connues A LA DECISION,
                # jamais posterieures.
                metadata={"stop_distance": decision["r_unit"], "digits": digits})
            ordres = executer_sur_snapshots(
                politique, snapshots, intent, config=config, seed=seed,
                latency_ms=0, tick_size=tick,
                seconds_per_snapshot=SECONDES_PAR_BARRE)
            quantite, prix = _prix_moyen(ordres, side)
            contact = _premier_contact_prix(ordres, snapshots, side, inclusif=True)
            franchissement = _premier_contact_prix(
                ordres, snapshots, side, inclusif=False)
            service = _premier_service_synthetique(ordres, side)
            resultats[politique] = (quantite, prix,
                                    expiration_indeterminee(ordres),
                                    sequence_intra_barre(ordres),
                                    contact, franchissement, service)
        reference = resultats.get(
            "market", (0.0, 0.0, False, False, None, None, None))[1]
        for politique in politiques:
            (quantite, prix, indetermine, intra_barre, contact,
             franchissement, service_at) = resultats[politique]
            part = min(max(float(quantite), 0.0), 1.0)
            service_synthetique = part > 0 and prix > 0
            gain = ((reference - prix) * int(side) / decision["r_unit"]
                    if service_synthetique and reference > 0 else 0.0)
            # Un service synthetique partiel prend une position partielle : il touche
            # la meme fraction du R. Compter 1 R pour 40 % de la taille
            # flatterait les politiques qui decoupent sans finir.
            effet = (part * (decision["net_r"] + gain)
                     if service_synthetique else 0.0)
            lignes.append({
                "symbole": symbole,
                "decision_id": decision["decision_id"],
                "decision_at": decision["decision_at"],
                "side": int(side),
                "politique": politique,
                "split": decision["split"],
                "split_rejeu": decision["split_rejeu"],
                "indetermine": bool(indetermine),
                "intra_barre": bool(intra_barre),
                "prix_touche_inclusif": contact is not None,
                "prix_touche_at": contact.isoformat() if contact else None,
                "prix_franchi": franchissement is not None,
                "prix_franchi_at": (franchissement.isoformat()
                                     if franchissement else None),
                "service_synthetique_scenario": bool(service_synthetique),
                "service_synthetique_at": service_at.isoformat() if service_at else None,
                "part_service_synthetique": round(part, 4),
                "prix_entree_simule": (round(prix, 10)
                                        if service_synthetique else None),
                "gain_entree_r": round(gain, 6),
                "net_r": decision["net_r"],
                "effet_r_service_synthetique": round(effet, 6),
                "fidelite": "barres_M5_reelles|ohlc_bid|ask=bid+spread|"
                            "profondeur_reconstruite",
            })
    return lignes


def motif_exclusion(politique: str, lignes: list[dict]) -> str:
    """Pourquoi une politique ne peut pas etre classee ici -- ou chaine vide."""
    if politique in NON_DIRECTIONNELLES:
        return MOTIF_NON_DIRECTIONNELLE
    if politique in NON_FIDELES_M5:
        return MOTIF_NON_FIDELE_M5
    if any(ligne.get("intra_barre") for ligne in lignes
           if ligne["politique"] == politique):
        return MOTIF_INTRA_BARRE
    if any(ligne.get("indetermine") for ligne in lignes
           if ligne["politique"] == politique):
        return MOTIF_COHORTE_PARTIELLE
    return ""


def agreger(lignes: list[dict], *, effectif_min: int = EFFECTIF_MIN) -> dict:
    """Par politique/segment : contact, franchissement et service synthetique.

    Les lignes INDETERMINEES sortent du numerateur ET du denominateur : elles
    ne sont ni un remplissage ni une absence de remplissage.
    """
    par_politique: dict[str, dict] = {}
    politiques = sorted({ligne["politique"] for ligne in lignes})
    for politique in politiques:
        bloc: dict[str, dict] = {}
        for segment in SEGMENTS:
            toutes = [ligne for ligne in lignes
                      if ligne["politique"] == politique
                      and ligne["split"] == segment]
            sous = [ligne for ligne in toutes if not ligne.get("indetermine")]
            indetermines = len(toutes) - len(sous)
            if not sous:
                bloc[segment] = {"n": 0, "n_indetermine": indetermines,
                                 "n_contacts_evaluables": 0,
                                 "n_contacts_inclusifs": 0,
                                 "n_franchissements": 0,
                                 "n_services_synthetiques": 0,
                                 "taux_contact_inclusif": None,
                                 "taux_franchissement": None,
                                 "taux_service_synthetique_scenario": None,
                                 "gain_entree_r_borne_sup": None,
                                 "effet_r_service_synthetique": None,
                                 "effectif_suffisant": False}
                continue
            contacts_evaluables = [ligne for ligne in sous
                                   if ligne.get("prix_touche_inclusif") is not None]
            contacts = [ligne for ligne in contacts_evaluables
                        if ligne["prix_touche_inclusif"]]
            franchissements = [ligne for ligne in contacts_evaluables
                               if ligne["prix_franchi"]]
            services = [ligne for ligne in sous
                        if ligne["service_synthetique_scenario"]]
            effets = [ligne["effet_r_service_synthetique"] for ligne in sous]
            bloc[segment] = {
                "n": len(sous),
                "n_indetermine": indetermines,
                "n_contacts_evaluables": len(contacts_evaluables),
                "n_contacts_inclusifs": len(contacts),
                "n_franchissements": len(franchissements),
                "n_services_synthetiques": len(services),
                "taux_contact_inclusif": (round(len(contacts) /
                                                 len(contacts_evaluables), 4)
                                            if contacts_evaluables else None),
                "taux_franchissement": (round(len(franchissements) /
                                               len(contacts_evaluables), 4)
                                          if contacts_evaluables else None),
                "taux_service_synthetique_scenario": round(
                    len(services) / len(sous), 4),
                "gain_entree_r_borne_sup": (round(st.fmean(
                    [ligne["gain_entree_r"] for ligne in services]), 6)
                    if services else None),
                "effet_r_service_synthetique": round(st.fmean(effets), 6),
                "effet_r_par_service_synthetique": (round(st.fmean(
                    [ligne["effet_r_service_synthetique"] for ligne in services]), 6)
                    if services else None),
                "effectif_suffisant": len(sous) >= effectif_min,
            }
        bloc["exclue_du_classement"] = motif_exclusion(politique, lignes)
        par_politique[politique] = bloc
    return par_politique


def classer(par_politique: dict) -> list[dict]:
    """Choix sur la CALIBRATION, jugement sur la VERIFICATION.

    Une politique exclue (non directionnelle, ou dont l'ordonnancement n'est
    pas resolvable a la granularite M5) reste MESUREE et publiee, mais ne
    figure pas au classement : lui donner un rang serait presenter comme un
    resultat ce que la donnee ne separe pas.
    """
    lignes = []
    for politique, bloc in par_politique.items():
        if bloc.get("exclue_du_classement"):
            continue
        cal, ver = bloc[SEGMENTS[0]], bloc[SEGMENTS[1]]
        if not cal.get("effectif_suffisant"):
            continue
        lignes.append({
            "politique": politique,
            "choix_effet_r_service_synthetique": cal["effet_r_service_synthetique"],
            "choix_service_synthetique": cal["taux_service_synthetique_scenario"],
            "choix_contact_inclusif": cal["taux_contact_inclusif"],
            "jugement_effet_r_service_synthetique":
                ver.get("effet_r_service_synthetique"),
            "jugement_service_synthetique":
                ver.get("taux_service_synthetique_scenario"),
            "jugement_contact_inclusif": ver.get("taux_contact_inclusif"),
            "jugement_effectif_suffisant": ver.get("effectif_suffisant", False),
            "jugement_gain_entree_r_borne_sup": ver.get("gain_entree_r_borne_sup"),
            "n_indetermine": cal.get("n_indetermine", 0) + ver.get("n_indetermine", 0),
        })
    return sorted(lignes,
                  key=lambda ligne: -(
                      ligne["choix_effet_r_service_synthetique"] or -9))


def _percentile(valeurs: list[float], quantile: float) -> float | None:
    """Percentile lineaire deterministe, sans dependance scientifique."""
    if not valeurs:
        return None
    triees = sorted(float(v) for v in valeurs)
    position = (len(triees) - 1) * quantile
    bas = int(position)
    haut = min(bas + 1, len(triees) - 1)
    poids = position - bas
    return triees[bas] * (1.0 - poids) + triees[haut] * poids


def _ic_bootstrap_par_symbole(
    deltas_par_symbole: dict[str, list[float]], *,
    repetitions: int = 5_000, seed: int = 14,
) -> dict:
    """IC apparie par cluster symbole, sous deux ponderations explicites.

    Les decisions d'un meme actif ne sont pas independantes. Le bootstrap tire
    donc des ACTIFS avec remise, jamais des decisions isolees. La ponderation
    ``decision`` conserve le poids historique de chaque actif ; la ponderation
    ``symbole`` donne une voix egale a chaque actif et expose la sensibilite aux
    exotiques tres prolifiques.
    """
    symboles = sorted(s for s, valeurs in deltas_par_symbole.items() if valeurs)
    if not symboles:
        return {
            "n_symboles": 0,
            "repetitions": 0,
            "seed": seed,
            "valide": False,
            "decision_weighted": None,
            "symbol_equal": None,
        }
    moyennes = {s: st.fmean(deltas_par_symbole[s]) for s in symboles}
    observe_decision = st.fmean(
        delta for s in symboles for delta in deltas_par_symbole[s])
    observe_symbole = st.fmean(moyennes.values())
    if len(symboles) < 2 or repetitions <= 0:
        return {
            "n_symboles": len(symboles),
            "repetitions": 0,
            "seed": seed,
            "valide": False,
            "decision_weighted": {
                "moyenne": round(observe_decision, 6), "ic95": None},
            "symbol_equal": {
                "moyenne": round(observe_symbole, 6), "ic95": None},
        }
    rng = random.Random(seed)
    boot_decision: list[float] = []
    boot_symbole: list[float] = []
    for _ in range(repetitions):
        tires = [rng.choice(symboles) for _ in symboles]
        boot_decision.append(st.fmean(
            delta for s in tires for delta in deltas_par_symbole[s]))
        boot_symbole.append(st.fmean(moyennes[s] for s in tires))
    return {
        "n_symboles": len(symboles),
        "repetitions": repetitions,
        "seed": seed,
        "valide": True,
        "decision_weighted": {
            "moyenne": round(observe_decision, 6),
            "ic95": [round(_percentile(boot_decision, 0.025), 6),
                     round(_percentile(boot_decision, 0.975), 6)],
        },
        "symbol_equal": {
            "moyenne": round(observe_symbole, 6),
            "ic95": [round(_percentile(boot_symbole, 0.025), 6),
                     round(_percentile(boot_symbole, 0.975), 6)],
        },
    }


def comparer_cohorte_commune(lignes: list[dict],
                             politique: str = "v14_live") -> dict:
    """Compare une politique et MARKET sur les memes decisions resolues.

    L'attrition est publiee : les TTL intra-barre rendent une part non aleatoire
    des decisions indeterminee. Un uplift sans ce taux de resolution ferait
    passer une selection de spreads moderes pour la population complete.
    """
    cibles = {(ligne["symbole"], ligne["decision_id"], ligne["split"])
              for ligne in lignes
              if ligne["politique"] == politique
              and not ligne.get("indetermine")}
    index = {(ligne["symbole"], ligne["decision_id"], ligne["split"],
              ligne["politique"]): ligne for ligne in lignes}
    sortie: dict[str, dict] = {}
    for segment in SEGMENTS:
        cohorte_politique = [ligne for ligne in lignes
                             if ligne["politique"] == politique
                             and ligne["split"] == segment]
        paires = []
        for symbole, decision_id, split in sorted(cibles):
            if split != segment:
                continue
            cible = index.get((symbole, decision_id, split, politique))
            marche = index.get((symbole, decision_id, split, "market"))
            if cible is not None and marche is not None:
                paires.append((cible, marche))
        if not paires:
            sortie[segment] = {"n": 0, "politique": politique,
                               "effet_r_politique": None,
                               "effet_r_market": None,
                               "uplift_r_vs_market": None,
                               "n_total_politique": len(cohorte_politique),
                               "taux_resolution": 0.0,
                               "attrition_non_aleatoire_possible": bool(
                                   cohorte_politique),
                               "bootstrap_cluster_symbole":
                                   _ic_bootstrap_par_symbole({})}
            continue
        effet_politique = st.fmean(
            paire[0]["effet_r_service_synthetique"] for paire in paires)
        effet_market = st.fmean(
            paire[1]["effet_r_service_synthetique"] for paire in paires)
        deltas_par_symbole: dict[str, list[float]] = {}
        for cible, marche in paires:
            deltas_par_symbole.setdefault(cible["symbole"], []).append(
                cible["effet_r_service_synthetique"]
                - marche["effet_r_service_synthetique"])
        taux_resolution = (len(paires) / len(cohorte_politique)
                           if cohorte_politique else 0.0)
        sortie[segment] = {
            "n": len(paires),
            "n_total_politique": len(cohorte_politique),
            "taux_resolution": round(taux_resolution, 6),
            "attrition_non_aleatoire_possible": len(paires) < len(cohorte_politique),
            "politique": politique,
            "effet_r_politique": round(effet_politique, 6),
            "effet_r_market": round(effet_market, 6),
            "uplift_r_vs_market": round(effet_politique - effet_market, 6),
            "contrat": "meme_decision_resolue|service_synthetique_scenario",
            "bootstrap_cluster_symbole": _ic_bootstrap_par_symbole(
                deltas_par_symbole),
        }
    return sortie


def mesurer(symboles: list[str], *, politiques: tuple[str, ...] = POLITIQUES,
            limite: int | None = 200, maker_bps: float = MAKER_BPS_DEFAUT,
            taker_bps: float = TAKER_BPS_DEFAUT,
            effectif_min: int = EFFECTIF_MIN, fenetre: int = FENETRE,
            lignes_sortie: Path | None = None, brut: Path = BRUT,
            resumes: Path = RESUMES, pin_epoque: str | None = None) -> dict:
    config = load_config()
    config["execution"]["fees"] = {"maker_bps": maker_bps, "taker_bps": taker_bps}
    # L'epoque de reference est celle du CORPUS demande, pas celle de l'arbre
    # de travail : sinon un commit sans effet sur le rejeu perime en silence
    # 147 artefacts scelles (panne du 25/08/2026). Une demande vide n'est pas
    # une mesure a zero symbole : c'est une analyse qui n'a pas eu lieu.
    etat = (epoque_rejeu.etat_epoque(brut, symboles, pin=pin_epoque) if symboles
            else {"corpus_epoch": "", "workspace_engine_epoch":
                  epoque_rejeu.empreinte_courante(),
                  "workspace_matches_corpus": False, "pin": pin_epoque or None,
                  "manifests": []})
    empreinte = etat["corpus_epoch"]
    lignes: list[dict] = []
    refuses: dict[str, str] = {}
    for symbole in symboles:
        valide, motif = valider_artefact(symbole, brut=brut, resumes=resumes,
                                         empreinte_attendue=empreinte)
        if not valide:
            refuses[symbole] = motif
            continue
        lignes.extend(evaluer_symbole(symbole, politiques, config,
                                      limite=limite, fenetre=fenetre,
                                      empreinte_attendue=empreinte))
    par_politique = agreger(lignes, effectif_min=effectif_min)
    if lignes_sortie is not None:
        Path(lignes_sortie).parent.mkdir(parents=True, exist_ok=True)
        with Path(lignes_sortie).open("w", encoding="utf-8") as flux:
            for ligne in lignes:
                flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
    decisions_retenues = len({(ligne["symbole"], ligne["decision_id"])
                              for ligne in lignes})
    mesures = len(symboles) - len(refuses)
    statut, motif_bloquant = ((STATUT_BLOQUE, "CORPUS_VIDE") if not symboles
                              else statut_analyse(mesures, refuses))
    return {
        "schema_version": 3,
        "statut": statut,
        "motif_bloquant": motif_bloquant or None,
        "mesure_le": datetime.now(timezone.utc).isoformat(),
        "moteur_execution": engine_fingerprint(),
        "epoque_rejeu": empreinte,
        "epoque": {
            **etat,
            "manifests_sha256": _sha256(_canonique(etat["manifests"])),
        },
        "code": {
            "analyse_sha256": _sha256_fichier(Path(__file__)),
            "limit_pricing_sha256": _sha256_fichier(
                RACINE / "titanium" / "execution" / "limit_pricing.py"),
        },
        "artefacts": {"artifact_type": ARTIFACT_TYPE,
                      "schema_version": SCHEMA_ARTEFACT,
                      "valides": mesures,
                      "refuses": refuses},
        "symboles": [s for s in symboles if s not in refuses],
        "decisions_par_symbole": limite,
        "decisions_retenues": decisions_retenues,
        "frais": {"maker_bps": maker_bps, "taker_bps": taker_bps},
        "fenetre_barres": fenetre,
        "secondes_par_barre": SECONDES_PAR_BARRE,
        "fidelite": "barres_M5_reelles|ohlc_bid|ask=bid+spread|"
                    "profondeur_reconstruite",
        "causalite": "plan sur la derniere barre CLOSE ; appariement sur les "
                     "barres suivantes ; expiration hors grille = INDETERMINE",
        "contrat_metrique": "Trois faits distincts sont publies: contact OHLC "
                            "inclusif, franchissement strict et service "
                            "synthetique du scenario de file/profondeur. "
                            "Seul le premier est une observation de prix; "
                            "aucun n'est une preuve de remplissage passif.",
        "avertissement": "l'esperance de base du rejeu est NEGATIVE. A effet_r "
                         "par decision, une politique gagne des qu'elle est "
                         "moins souvent servie : le rang seul ne dit donc rien. "
                         "Les effets publies dependent du scenario synthetique "
                         "de service et ne constituent pas une esperance live. "
                         "Aucun rang ici n'autorise "
                         "une promotion.",
        "limite_connue": "une barre M5 ne resout pas l'intra-barre : les "
                         "politiques qui ne different que par un "
                         "ordonnancement de quelques secondes sont "
                         "indistinguables ici et sortent du classement",
        "lignes": len(lignes),
        "lignes_detaillees": str(lignes_sortie) if lignes_sortie else "",
        "par_politique": par_politique,
        "classement": classer(par_politique),
        "cohortes_communes": {
            "v14_live_vs_market": comparer_cohorte_commune(lignes, "v14_live")
        },
    }


def resumer(rapport: dict) -> str:
    lignes = [
        f"politiques d'execution sur barres M5 REELLES — moteur "
        f"{rapport['moteur_execution']}",
        f"  {len(rapport['symboles'])} symboles, "
        f"{rapport.get('decisions_retenues', 0)} decisions, "
        f"{rapport['lignes']} lignes (decision x politique)",
        f"  epoque rejeu : {rapport.get('epoque_rejeu', '')[:16]} — "
        f"{rapport['artefacts']['valides']} artefacts valides, "
        f"{len(rapport['artefacts']['refuses'])} refuses",
        f"  arbre de travail : "
        f"{(rapport.get('epoque') or {}).get('workspace_engine_epoch', '')[:16]}"
        f"{'' if (rapport.get('epoque') or {}).get('workspace_matches_corpus') else ' — DIFFERENT de la generation mesuree (ecart permis, publie)'}",
        f"  fidelite : {rapport['fidelite']}",
        f"  causalite : {rapport['causalite']}",
        "",
        "CONTACT = enveloppe OHLC inclusive. SERVICE = scenario synthetique "
        "de file/profondeur, jamais un fill observe.",
        "",
        f"{'politique':<20}{'choix effet':>13}{'service':>9}"
        f"{'JUGEMENT effet':>16}{'service':>9}{'contact':>10}"
        f"{'gain entree':>13}{'indet':>7}",
    ]
    for ligne in rapport["classement"]:
        juge = ligne["jugement_effet_r_service_synthetique"]
        gain = ligne["jugement_gain_entree_r_borne_sup"]
        contact = ligne.get("jugement_contact_inclusif")
        lignes.append(
            f"{ligne['politique']:<20}"
            f"{ligne['choix_effet_r_service_synthetique']:>+13.4f}"
            f"{100 * (ligne['choix_service_synthetique'] or 0):>8.1f}%"
            f"{(juge if juge is not None else 0):>+16.4f}"
            f"{100 * (ligne['jugement_service_synthetique'] or 0):>8.1f}%"
            f"{100 * (contact or 0):>9.1f}%"
            f"{(gain if gain is not None else 0):>+13.4f}"
            f"{ligne.get('n_indetermine', 0):>7}")
    if not rapport["classement"]:
        lignes.append("  (aucune politique n'atteint l'effectif minimal)")
    lignes += ["", f"CONTRAT : {rapport.get('contrat_metrique', '')}",
               "", f"AVERTISSEMENT : {rapport.get('avertissement', '')}"]
    exclues = {nom: bloc["exclue_du_classement"]
               for nom, bloc in rapport["par_politique"].items()
               if bloc.get("exclue_du_classement")}
    if exclues:
        lignes += ["", "MESUREES MAIS HORS CLASSEMENT :"]
        lignes += [f"  {nom:<24}{motif}" for nom, motif in sorted(exclues.items())]
    indetermines = sum(bloc[segment].get("n_indetermine", 0)
                       for bloc in rapport["par_politique"].values()
                       for segment in SEGMENTS)
    lignes += ["", f"lignes indeterminees (expiration intra-barre) : {indetermines}"]
    lignes += [f"LIMITE : {rapport['limite_connue']}."]
    return "\n".join(lignes)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--limite", type=int, default=200,
                    help="decisions echantillonnees par symbole")
    ap.add_argument("--politiques", nargs="*", default=list(POLITIQUES))
    ap.add_argument("--maker-bps", type=float, default=MAKER_BPS_DEFAUT)
    ap.add_argument("--taker-bps", type=float, default=TAKER_BPS_DEFAUT)
    ap.add_argument("--effectif-min", type=int, default=EFFECTIF_MIN)
    ap.add_argument("--fenetre", type=int, default=FENETRE,
                    help="barres M5 offertes a une politique pour etre servie")
    ap.add_argument("--sortie", type=Path, default=SORTIE)
    ap.add_argument("--brut", type=Path, default=BRUT,
                    help="racine des artefacts bruts scelles a mesurer")
    ap.add_argument("--resumes", type=Path, default=RESUMES,
                    help="racine des resumes lies aux manifestes")
    ap.add_argument("--empreinte", default=None,
                    help="epingle la generation attendue du corpus. ASSERTION: "
                         "une valeur differente refuse la mesure au lieu de la "
                         "contourner.")
    ap.add_argument("--lignes", type=Path, default=None,
                    help="NDJSON detaille (decision_id, instant, sens, prix)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    symboles = args.symboles or sorted(
        chemin.stem for chemin in (BARRES / "M5").glob("*.parquet"))
    try:
        rapport = mesurer(symboles, politiques=tuple(args.politiques),
                          limite=args.limite, maker_bps=args.maker_bps,
                          taker_bps=args.taker_bps,
                          effectif_min=args.effectif_min,
                          fenetre=args.fenetre, lignes_sortie=args.lignes,
                          brut=args.brut, resumes=args.resumes,
                          pin_epoque=args.empreinte)
    except epoque_rejeu.EpoqueCorpusError as erreur:
        blocage = epoque_rejeu.publier_blocage(args.sortie, {
            "blocking_reason": erreur.motif,
            "detail": erreur.detail,
            "banc": "politiques_execution_reel",
        })
        print(json.dumps({"statut": STATUT_BLOQUE,
                          "motif_bloquant": erreur.motif,
                          "detail": erreur.detail, "ecrit": False,
                          "rapport_bloque": str(blocage)},
                         ensure_ascii=False, indent=2))
        return 2
    if rapport["statut"] == STATUT_BLOQUE:
        # Ne rien ecrire A LA PLACE du classement : un classement vide qui
        # ecrase le dernier classement valide fait passer une panne pour une
        # absence de signal. Le blocage est publie a cote, car un tableau de
        # bord ne lit pas un code de retour.
        epoque = {cle: rapport["epoque"][cle] for cle in (
            "corpus_epoch", "workspace_engine_epoch",
            "workspace_matches_corpus")}
        blocage = epoque_rejeu.publier_blocage(args.sortie, {
            "blocking_reason": rapport["motif_bloquant"],
            "artefacts": rapport["artefacts"],
            "epoque": epoque,
            "banc": "politiques_execution_reel",
        })
        print(json.dumps({"statut": rapport["statut"],
                          "motif_bloquant": rapport["motif_bloquant"],
                          "artefacts": rapport["artefacts"],
                          "epoque": epoque,
                          "ecrit": False,
                          "rapport_bloque": str(blocage)},
                         ensure_ascii=False, indent=2))
        return 2
    epoque_rejeu.lever_blocage(args.sortie)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(json.dumps(rapport, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(json.dumps(rapport, ensure_ascii=False, indent=2) if args.json
          else resumer(rapport))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
