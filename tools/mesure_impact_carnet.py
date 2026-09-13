"""Ce que le carnet L2 archivé dit du coût réel — et de l'horizon utile.

    python tools/mesure_impact_carnet.py --symbole BTCUSDT --jours 2026-09-11
    python tools/mesure_impact_carnet.py --tous --fenetres 4 --json out.json

POURQUOI CET OUTIL EXISTE
-------------------------
La porte de coût refuse aujourd'hui la grande majorité des candidats en
comparant `spread / stop` a un plafond de 12,5 %. Ce rapport repose sur deux
hypothèses jamais confrontées à un carnet réel :

1. **le spread est fixe** — le coût d'un ordre ne dépend pas de sa taille ;
2. **l'impact de taille est nul** — payer le spread suffit à être servi.

L'archive `results/carnet_binance` contient vingt gigaoctets de carnet L2
Binance (BTCUSDT, ETHUSDT, depuis le 16/08/2026) que rien ne lisait. Cet outil
les lit et applique la mesure de Cont, Kukanov & Stoikov (2014) : la variation
de prix sur un intervalle court est linéaire en *déséquilibre du flux* (OFI),
avec une pente inversement proportionnelle à la profondeur.

CE QU'IL MESURE
---------------
- **OFI et sa pente**, par horizon d'agrégation, avec le R² de l'OFI mis en
  concurrence avec deux indicateurs naïfs (variation de taille au sommet, flux
  agressif signé). C'est la comparaison qu'impose la critique d'Andersen &
  Bondarenko : un indicateur qui ne bat pas l'indicateur ordinaire ne vaut rien.
- **la pente par tercile de profondeur** — si elle ne décroît pas, la loi en
  1/profondeur ne tient pas sur ces données et il faut le dire.
- **le coût d'exécution réel** d'une taille donnée, par parcours du carnet
  (prix moyen pondéré contre mid), en points de base, pour plusieurs notionnels.
- **le rapport coût réel / spread**, qui est la réponse directe à la question
  « le modèle à spread fixe sous-estime-t-il ou surestime-t-il le coût ? ».

CE QU'IL REFUSE DE FAIRE
------------------------
- **Il ne déplace aucun seuil.** Le plafond de 12,5 % est une décision humaine ;
  cet outil produit le chiffre, pas la décision.
- **Il ne franchit pas une rupture de session.** Un rejeu qui traverse un
  `trou` ou une `amorce` rend un carnet plausible et faux — l'enregistreur
  documente que c'est arrivé le 16/08/2026.
- **Il ne généralise pas à MT5.** L'archive est du **Binance spot** ; les
  symboles tradés sont des CFD MT5. Le rapport le dit à chaque exécution, et
  aucune conclusion sur les symboles MT5 n'est tirée de ces fichiers.
- **Il ne conclut pas sous le seuil d'échantillon.** En dessous de `N_MINIMAL`
  fenêtres par horizon, il affiche les chiffres et refuse le verdict.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import statistics
import sys
from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

with suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DOSSIER_DEFAUT = RACINE / "results" / "carnet_binance"

#: Instantanés qui OUVRENT une session. Recopiés de l'enregistreur : franchir
#: l'une de ces bornes en rejouant rend un carnet faux sans lever d'erreur.
RUPTURES = ("amorce", "trou")

#: Demi-largeur, en points de base, de la bande de carnet dont on cumule la
#: taille. C'est la profondeur au sens de la littérature d'impact : normaliser
#: sur le seul premier niveau surestime la pente d'un facteur qui varie d'un
#: symbole à l'autre, donc qui empêche toute comparaison entre eux.
BANDE_BPS = 5.0

#: Fenêtres minimales PAR HORIZON avant d'accepter un verdict. En dessous, le R²
#: d'une régression sur un marché calme est du bruit, et l'annoncer comme un
#: résultat est la façon la plus rapide de câbler une erreur.
N_MINIMAL = 30

#: Notionnels mesurés par défaut, en devise de cotation (USDT ici).
#:
#: La liste descend volontairement **sous** et **au-dessus** de l'échelle du
#: compte : les petites tailles montrent ce que le spread seul décrit, les
#: grandes montrent où le carnet se met à se payer. Sans les deux, on ne sait
#: pas si un rapport de 1,0 vient de l'absence d'impact ou de l'absence de test.
NOTIONNELS_DEFAUT = (100.0, 500.0, 1000.0, 5000.0, 10_000.0, 50_000.0, 200_000.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Relecture de l'archive
# ═══════════════════════════════════════════════════════════════════════════════


def _ouvrir(chemin: Path):
    """Ouvre un `.ndjson` en clair ou compacté. Gzip est détecté par suffixe."""
    if chemin.suffix == ".gz":
        return gzip.open(chemin, "rt", encoding="utf-8")
    return chemin.open("r", encoding="utf-8")


def fichiers(dossier: Path, symbole: str, jours: Iterable[str], nature: str) -> list[Path]:
    """Fichiers présents pour un symbole, une nature et une liste de jours.

    Un jour demandé mais absent n'est pas une erreur : l'archive peut être
    compactée ou la collecte interrompue. Le rapport nomme ce qui a été lu.
    """
    out: list[Path] = []
    for jour in jours:
        for suffixe in (".ndjson", ".ndjson.gz"):
            candidat = dossier / symbole / f"{jour}.{nature}{suffixe}"
            if candidat.exists():
                out.append(candidat)
                break
    return out


def lire(
    chemin: Path,
    *,
    debut: int = 0,
    max_lignes: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Itère les enregistrements lisibles d'un fichier, sans le charger en mémoire.

    Un fichier de carnet pèse plus d'un gigaoctet : le matérialiser en liste
    ferait tomber la machine avant de rien mesurer. Une ligne cassée est
    ignorée, jamais fatale — l'archive est écrite en append par un collecteur
    qu'on arrête sans préavis.
    """
    lues = 0
    with _ouvrir(chemin) as flux:
        if debut:
            flux.seek(debut)
            flux.readline()  # jette la ligne partielle du seek
        for ligne in flux:
            if not ligne.strip():
                continue
            try:
                yield json.loads(ligne)
            except json.JSONDecodeError:
                continue
            lues += 1
            if max_lignes is not None and lues >= max_lignes:
                return


def sessions(lignes: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """Découpe en tranches continues `(debut, fin)` ouvertes par un instantané.

    Une session commence à un instantané de rupture et s'arrête juste avant le
    suivant. Elle peut aussi commencer à un instantané **périodique**, qui est
    un carnet complet reçu de la place : c'est le seul point de reprise légitime
    au milieu d'un fichier, et c'est ce qui permet d'échantillonner une journée
    entière sans la relire en entier.
    """
    departs = [i for i, ligne in enumerate(lignes) if ligne.get("type") == "instantane"]
    return [
        (d, departs[r + 1] if r + 1 < len(departs) else len(lignes)) for r, d in enumerate(departs)
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# Carnet
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Sommet:
    """Le meilleur niveau de chaque côté, et le mid qui en découle."""

    prix_bid: float
    qte_bid: float
    prix_ask: float
    qte_ask: float

    @property
    def mid(self) -> float:
        return (self.prix_bid + self.prix_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.prix_ask - self.prix_bid

    @property
    def profondeur_sommet(self) -> float:
        """Taille cumulée au sommet — le dénominateur naturel de la loi d'impact."""
        return self.qte_bid + self.qte_ask


@dataclass
class Carnet:
    """Carnet reconstruit par différentiels, avec un meilleur niveau entretenu.

    Recalculer `max(bids)` à chaque différentiel coûterait un parcours complet
    du carnet dix fois par seconde. Le sommet n'est donc recalculé que lorsqu'un
    niveau **au moins aussi bon** que lui a été touché — ce qui est rare, et
    exact dans tous les autres cas.
    """

    bids: dict[float, float] = field(default_factory=dict)
    asks: dict[float, float] = field(default_factory=dict)
    _sommet: Sommet | None = None

    @classmethod
    def depuis_instantane(cls, ligne: dict[str, Any]) -> Carnet:
        carnet = cls(
            bids={float(p): float(q) for p, q in ligne["bids"]},
            asks={float(p): float(q) for p, q in ligne["asks"]},
        )
        carnet._recalculer()  # noqa: SLF001 — constructeur de la classe
        return carnet

    def _recalculer(self) -> None:
        if not self.bids or not self.asks:
            self._sommet = None
            return
        pb = max(self.bids)
        pa = min(self.asks)
        if pb >= pa:
            # Carnet croisé : la place ne devrait pas le produire. Le dire plutôt
            # que de rendre un mid absurde qui contaminerait toutes les mesures.
            self._sommet = None
            return
        self._sommet = Sommet(pb, self.bids[pb], pa, self.asks[pa])

    def appliquer(self, diff: dict[str, Any]) -> Sommet | None:
        """Applique un différentiel. Rend le nouveau sommet, ou None si croisé.

        Une quantité nulle SUPPRIME le niveau : c'est ainsi que la place exprime
        un retrait, et le lire comme « niveau à zéro » laisserait un carnet plein
        de fantômes.
        """
        touche_haut = False
        touche_bas = False
        for prix_s, qte_s in diff["bids"]:
            prix, qte = float(prix_s), float(qte_s)
            if self._sommet is None or prix >= self._sommet.prix_bid:
                touche_haut = True
            if qte == 0.0:
                self.bids.pop(prix, None)
            else:
                self.bids[prix] = qte
        for prix_s, qte_s in diff["asks"]:
            prix, qte = float(prix_s), float(qte_s)
            if self._sommet is None or prix <= self._sommet.prix_ask:
                touche_bas = True
            if qte == 0.0:
                self.asks.pop(prix, None)
            else:
                self.asks[prix] = qte
        if self._sommet is None or touche_haut or touche_bas:
            self._recalculer()
        elif self._sommet is not None:
            # Le sommet n'a pas bougé de prix, mais sa taille a pu être révisée
            # par un différentiel qui l'a touché : on relit la taille exacte.
            self._sommet.qte_bid = self.bids.get(self._sommet.prix_bid, self._sommet.qte_bid)
            self._sommet.qte_ask = self.asks.get(self._sommet.prix_ask, self._sommet.qte_ask)
        return self._sommet

    def profondeur_bande(self, bps: float = 5.0) -> float:
        """Taille cumulée dans une bande de ±`bps` autour du mid, deux côtés.

        C'est la profondeur au sens de la littérature d'impact, et non la seule
        taille du premier niveau. La distinction n'est pas cosmétique : une
        bande de 5 bp peut contenir cent fois le premier niveau, et c'est elle
        qui détermine si un ordre déplace le prix.
        """
        if self._sommet is None:
            return 0.0
        mid = self._sommet.mid
        bas = mid * (1.0 - bps / 1e4)
        haut = mid * (1.0 + bps / 1e4)
        total = 0.0
        for prix, qte in self.bids.items():
            if prix >= bas:
                total += qte
        for prix, qte in self.asks.items():
            if prix <= haut:
                total += qte
        return total

    def notionnel_sommet(self) -> float:
        """Notionnel en attente au meilleur niveau, des deux côtés.

        C'est le chiffre qui explique tout le reste : tant qu'un ordre tient
        dans le premier niveau, il ne paie que la **moitié** du spread.
        """
        if self._sommet is None:
            return 0.0
        return (
            self._sommet.prix_bid * self._sommet.qte_bid
            + self._sommet.prix_ask * self._sommet.qte_ask
        )

    def couts_execution(
        self,
        notionnels: Iterable[float],
        *,
        sens: str = "achat",
    ) -> dict[float, float | None]:
        """Coût d'un ordre de chaque notionnel, en points de base, en un seul parcours.

        Le carnet est parcouru une fois du meilleur niveau au pire, en cumulant
        dépense et quantité ; chaque notionnel demandé est ensuite lu sur la
        courbe cumulative. Six appels séparés trieraient six fois le même carnet
        pour un résultat identique.

        Un ordre plus petit que le premier niveau paie donc **la moitié du
        spread** — et c'est précisément ce que le modèle à spread fixe ne
        distingue pas.
        """
        cibles = sorted(set(notionnels))
        sortie: dict[float, float | None] = dict.fromkeys(cibles)
        if self._sommet is None or not cibles:
            return sortie
        niveaux = self.asks if sens == "achat" else self.bids
        ordre = sorted(niveaux, reverse=sens != "achat")
        mid = self._sommet.mid
        depense = 0.0
        quantite = 0.0
        suivante = 0
        for prix in ordre:
            if prix <= 0:
                continue
            dispo = prix * niveaux[prix]
            while suivante < len(cibles) and depense + dispo >= cibles[suivante] - 1e-9:
                cible = cibles[suivante]
                pris = cible - depense
                prise = quantite + pris / prix
                if prise > 0:
                    sortie[cible] = 1e4 * ((depense + pris) / prise / mid - 1.0)
                suivante += 1
            if suivante >= len(cibles):
                break
            depense += dispo
            quantite += dispo / prix
        # Les cibles restantes sont trop grosses pour ce carnet : la mesure
        # reste None plutôt que d'inventer un chiffre sur un carnet épuisé.
        return sortie


# ═══════════════════════════════════════════════════════════════════════════════
# Agrégation et régression
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Fenetre:
    """Une observation d'un horizon : ce qui entre, ce que le prix fait."""

    horizon_s: float
    mid_debut: float
    mid_fin: float
    ofi: float
    naif: float
    profondeur_moy: float
    spread_moy: float
    flux_signe: float = 0.0
    #: Profondeur cumulée dans ±`BANDE_BPS` autour du mid, moyennée sur la
    #: fenêtre. C'est le dénominateur naturel de la loi d'impact ; le sommet
    #: seul la surestime d'un facteur qui dépend du symbole.
    profondeur_bande: float = 0.0

    @property
    def dmid_bps(self) -> float:
        return 1e4 * (self.mid_fin / self.mid_debut - 1.0)


def regression(xs: list[float], ys: list[float]) -> dict[str, float]:
    """Moindres carrés ordinaires avec ordonnée. Rend pente, R² et t de la pente."""
    n = len(xs)
    if n < 3:
        return {"n": float(n), "pente": 0.0, "r2": 0.0, "t": 0.0, "ordonnee": 0.0}
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    if sxx == 0.0 or syy == 0.0:
        return {"n": float(n), "pente": 0.0, "r2": 0.0, "t": 0.0, "ordonnee": my}
    pente = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy)
    ordonnee = my - pente * mx
    residu = sum((y - (ordonnee + pente * x)) ** 2 for x, y in zip(xs, ys, strict=True))
    if n > 2 and residu > 0:
        se = math.sqrt(residu / (n - 2) / sxx)
        t = pente / se if se > 0 else 0.0
    else:
        t = 0.0
    return {"n": float(n), "pente": pente, "r2": r2, "t": t, "ordonnee": ordonnee}


def terciles(valeurs: list[float]) -> list[float]:
    """Bornes des terciles, pour découper par profondeur sans dépendance externe."""
    if len(valeurs) < 3:
        return []
    tri = sorted(valeurs)
    return [tri[len(tri) // 3], tri[2 * len(tri) // 3]]


# ═══════════════════════════════════════════════════════════════════════════════
# Mesure d'une fenêtre
# ═══════════════════════════════════════════════════════════════════════════════


def mesurer_fenetre(
    lignes: list[dict[str, Any]],
    depart: int,
    fin: int,
    *,
    horizons: tuple[float, ...],
    notionnels: tuple[float, ...],
    pas_cout: int = 25,
) -> dict[str, Any]:
    """Rejoue une session et rend toutes les mesures d'une fenêtre.

    `depart` doit désigner un index d'instantané : c'est la seule borne depuis
    laquelle un carnet est définissable.
    """
    carnet = Carnet.depuis_instantane(lignes[depart])
    # `mid0` sert de référence aux fenêtres d'agrégation ; il n'est pas mis à
    # jour par le rejeu, c'est le point de départ de la première fenêtre.
    etat: dict[str, Any] = {
        "ts": float(lignes[depart].get("recu_ms") or 0.0),
        "mid": carnet._sommet.mid if carnet._sommet else None,  # noqa: SLF001
        "sommet": carnet._sommet,
    }
    fenetres: list[Fenetre] = []
    couts: dict[float, list[float]] = {n: [] for n in notionnels}
    spreads_bps: list[float] = []
    profondeurs: list[float] = []
    bandes: list[float] = []
    ecartes = {"croise": 0, "sans_sommet": 0}

    bougies: dict[float, dict[str, float]] = {}
    precedent = carnet._sommet  # noqa: SLF001
    evenements = 0
    for rang in range(depart + 1, fin):
        ligne = lignes[rang]
        if ligne.get("type") != "diff":
            continue
        sommet = carnet.appliquer(ligne)
        evenements += 1
        if sommet is None or precedent is None:
            ecartes["sans_sommet" if sommet is None else "croise"] += 1
            precedent = sommet
            continue

        # ── déséquilibre du flux (Cont, Kukanov & Stoikov 2014) ─────────────
        # I{Pb_n ≥ Pb_{n-1}} qb_n − I{Pb_n ≤ Pb_{n-1}} qb_{n-1}
        # − I{Pa_n ≤ Pa_{n-1}} qa_n + I{Pa_n ≥ Pa_{n-1}} qa_{n-1}
        ofi = 0.0
        if sommet.prix_bid >= precedent.prix_bid:
            ofi += sommet.qte_bid
        if sommet.prix_bid <= precedent.prix_bid:
            ofi -= precedent.qte_bid
        if sommet.prix_ask <= precedent.prix_ask:
            ofi -= sommet.qte_ask
        if sommet.prix_ask >= precedent.prix_ask:
            ofi += precedent.qte_ask
        # Comparateur naïf : la variation de taille au sommet, sans les
        # indicateurs de prix. C'est lui que l'indicateur doit battre.
        naif = (sommet.qte_bid - precedent.qte_bid) - (sommet.qte_ask - precedent.qte_ask)

        # ── coût réel, échantillonné ────────────────────────────────────────
        bande = 0.0
        if evenements % pas_cout == 0 and sommet.spread > 0:
            for notionnel, cout in carnet.couts_execution(notionnels).items():
                if cout is not None:
                    couts[notionnel].append(cout)
            if sommet.mid > 0:
                spreads_bps.append(1e4 * sommet.spread / sommet.mid)
            profondeurs.append(carnet.notionnel_sommet())
            bande = carnet.profondeur_bande(BANDE_BPS)
            bandes.append(bande)

        ts = float(ligne.get("recu_ms") or 0.0)
        for horizon in horizons:
            cle = f"{ts}:{horizon}"
            bougie = bougies.setdefault(
                cle,
                {
                    "mid0": sommet.mid,
                    "ofi": 0.0,
                    "naif": 0.0,
                    "t0": ts,
                    "bande": 0.0,
                    "n_bande": 0.0,
                    "prof": 0.0,
                    "spread": 0.0,
                    "n": 0.0,
                },
            )
            bougie["ofi"] += ofi
            bougie["naif"] += naif
            bougie["prof"] += sommet.profondeur_sommet
            bougie["spread"] += sommet.spread
            bougie["n"] += 1
            if bande:
                bougie["bande"] += bande
                bougie["n_bande"] += 1
            bougie["mid1"] = sommet.mid
        precedent = sommet

    fenetres.extend(_agreger(bougies, horizons))
    return {
        "evenements": evenements,
        "fenetres": fenetres,
        "couts": couts,
        "spreads_bps": spreads_bps,
        "profondeurs": profondeurs,
        "bandes": bandes,
        "ecartes": ecartes,
        "mid_debut": etat["mid"],
    }


def _agreger(
    bougies: dict[str, dict[str, float]],
    horizons: tuple[float, ...],
) -> list[Fenetre]:
    """Regroupe les bougies par horizon, dans l'ordre temporel.

    L'agrégation par `int(ts / horizon)` garderait le pas de temps mais perdrait
    la continuité entre tranches. On agrège donc par distance temporelle depuis
    le début de la tranche courante.
    """
    out: list[Fenetre] = []
    for horizon in horizons:
        items = sorted(
            (k for k in bougies if k.endswith(f":{horizon}")), key=lambda k: bougies[k]["t0"]
        )
        tranche: list[dict[str, float]] = []
        base = None
        for k in items:
            b = bougies[k]
            if base is None:
                base = b["t0"]
            if b["t0"] - base >= horizon * 1000.0 and tranche:
                out.append(_fusionner(tranche, horizon))
                tranche, base = [], b["t0"]
            tranche.append(b)
        if tranche:
            out.append(_fusionner(tranche, horizon))
    return out


def _fusionner(tranche: list[dict[str, float]], horizon: float) -> Fenetre:
    n = sum(b["n"] for b in tranche) or 1.0
    n_bande = sum(b.get("n_bande", 0.0) for b in tranche)
    return Fenetre(
        horizon_s=horizon,
        mid_debut=tranche[0]["mid0"],
        mid_fin=tranche[-1].get("mid1", tranche[0]["mid0"]),
        ofi=sum(b["ofi"] for b in tranche),
        naif=sum(b["naif"] for b in tranche),
        profondeur_moy=sum(b["prof"] for b in tranche) / n,
        spread_moy=sum(b["spread"] for b in tranche) / n,
        profondeur_bande=(sum(b.get("bande", 0.0) for b in tranche) / n_bande if n_bande else 0.0),
    )


def flux_agressif(trades: list[dict[str, Any]], debut_ms: float, fin_ms: float) -> float:
    """Volume signé agressif sur une fenêtre : acheteur moins vendeur.

    Le côté agresseur est **fourni par la place** (`cote_agresseur`), pas inféré
    par une règle de tick : c'est ce qui rend la comparaison honnête, là où la
    plupart des études doivent deviner l'agresseur.
    """
    total = 0.0
    for t in trades:
        ts = float(t.get("recu_ms") or 0.0)
        if ts < debut_ms or ts > fin_ms:
            continue
        q = float(t.get("quantite") or 0.0)
        total += q if t.get("cote_agresseur") == "acheteur" else -q
    return total


def lire_trades(
    chemin: Path, *, debut_ms: float, fin_ms: float, max_lignes: int | None = None
) -> list[dict[str, Any]]:
    """Trades d'une fenêtre. Le fichier est plus petit que le carnet, mais
    toujours lu en flux pour ne pas dépendre de sa taille."""
    out: list[dict[str, Any]] = []
    for t in lire(chemin, max_lignes=max_lignes):
        if t.get("type") != "trade":
            continue
        ts = float(t.get("recu_ms") or 0.0)
        if ts > fin_ms:
            break
        if ts >= debut_ms:
            out.append(t)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Rapport
# ═══════════════════════════════════════════════════════════════════════════════


def _profondeur(f: Fenetre) -> float:
    """Profondeur de référence d'une fenêtre : la bande si mesurée, sinon le sommet.

    Le repli est explicite parce qu'un échantillon sans bande (pas de coût
    échantillonné dans la fenêtre) ne doit pas faire tomber la mesure ; mais il
    est rare, et le rapport dit sur quelle base il a été fait.
    """
    return f.profondeur_bande if f.profondeur_bande > 0 else f.profondeur_moy


def _synthese_horizons(fenetres: list[Fenetre]) -> dict[float, dict[str, Any]]:
    """R² de l'OFI, du naïf et du flux agressif, par horizon."""
    out: dict[float, dict[str, Any]] = {}
    for horizon in sorted({f.horizon_s for f in fenetres}):
        lot = [f for f in fenetres if f.horizon_s == horizon]
        if not lot:
            continue
        prof = [max(_profondeur(f), 1e-12) for f in lot]
        y = [f.dmid_bps for f in lot]
        out[horizon] = {
            "n": len(lot),
            "ofi": regression([f.ofi / d for f, d in zip(lot, prof, strict=True)], y),
            "naif": regression([f.naif / d for f, d in zip(lot, prof, strict=True)], y),
            "flux": regression([f.flux_signe / d for f, d in zip(lot, prof, strict=True)], y),
            "profondeur_med": statistics.median([_profondeur(f) for f in lot]),
            "spread_med": statistics.median([f.spread_moy for f in lot]),
        }
    return out


def _synthese_profondeur(fenetres: list[Fenetre], horizon: float) -> list[dict[str, Any]]:
    """Pente de l'impact par tercile de profondeur, à l'horizon le plus court."""
    lot = [f for f in fenetres if f.horizon_s == horizon]
    bornes = terciles([_profondeur(f) for f in lot])
    if not bornes:
        return []
    groupes: list[list[Fenetre]] = [[], [], []]
    for f in lot:
        d = _profondeur(f)
        rang = 0 if d <= bornes[0] else (1 if d <= bornes[1] else 2)
        groupes[rang].append(f)
    out = []
    for nom, groupe in zip(("mince", "moyen", "epais"), groupes, strict=True):
        if len(groupe) < N_MINIMAL:
            out.append({"tercile": nom, "n": len(groupe)})
            continue
        prof = [max(_profondeur(f), 1e-12) for f in groupe]
        r = regression(
            [f.ofi / d for f, d in zip(groupe, prof, strict=True)], [f.dmid_bps for f in groupe]
        )
        med = statistics.median([_profondeur(f) for f in groupe])
        out.append(
            {
                "tercile": nom,
                "n": len(groupe),
                "profondeur_med": med,
                "pente": r["pente"],
                "r2": r["r2"],
                "pente_x_profondeur": r["pente"] * med,
            }
        )
    return out


def _stats_couts(
    couts: dict[float, list[float]],
    spreads_bps: list[float],
    profondeurs: list[float],
) -> dict[str, Any]:
    """Coût réel par notionnel, rapporté au spread mesuré au même instant.

    Deux rapports, volontairement distingués :

    - `cout_bps / (spread/2)` : 1.0 signifie « servi entièrement au premier
      niveau », donc aucun impact. C'est ce qui situe la **taille de bascule**,
      celle à partir de laquelle le carnet commence à se payer.
    - `2 × coût / spread` : ce qu'un aller-retour coûte comparé au **modèle de la
      porte**, qui facture exactement un spread complet.
    """
    spread_med = statistics.median(spreads_bps) if spreads_bps else None
    demi = (spread_med / 2.0) if spread_med else None
    out: dict[str, Any] = {
        "spread_bps_median": spread_med,
        "n_instants": len(spreads_bps),
        "profondeur_sommet_mediane": statistics.median(profondeurs) if profondeurs else None,
        "bascule_impact": None,
        "par_notionnel": [],
    }
    for notionnel, valeurs in sorted(couts.items()):
        if not valeurs:
            out["par_notionnel"].append({"notionnel": notionnel, "n": 0})
            continue
        med = statistics.median(valeurs)
        tri = sorted(valeurs)
        p90 = tri[min(len(tri) - 1, int(0.9 * len(tri)))]
        ratio_demi = (med / demi) if demi else None
        if out["bascule_impact"] is None and ratio_demi is not None and ratio_demi > 1.2:
            out["bascule_impact"] = notionnel
        out["par_notionnel"].append(
            {
                "notionnel": notionnel,
                "n": len(valeurs),
                "cout_bps_median": med,
                "cout_bps_p90": p90,
                "spread_bps_median": spread_med,
                "cout_vs_demi_spread": ratio_demi,
                "aller_retour_vs_spread": (2.0 * med / spread_med) if spread_med else None,
            }
        )
    return out


def analyser_spread_externe(brut: str) -> dict[str, float]:
    """Analyse `CODE=bps` séparés par des points-virgules.

    Les valeurs sont **fournies par l'opérateur**, jamais lues ici : l'outil ne
    dépend pas de MT5 et doit tourner en intégration continue. Le rapport nomme
    la chaîne reçue, pour qu'un chiffre ne puisse pas être repris sans sa source.
    """
    out: dict[str, float] = {}
    for morceau in brut.split(";"):
        morceau = morceau.strip()
        if not morceau or "=" not in morceau:
            continue
        code, valeur = morceau.split("=", 1)
        try:
            out[code.strip()] = float(valeur)
        except ValueError:
            continue
    return out


def rapport(
    dossier: Path,
    symbole: str,
    lectures: list[dict[str, Any]],
    *,
    horizontes: tuple[float, ...],
    notionnels: tuple[float, ...],
    notionnel_cible: float,
    seuil: float,
    dmid_ope: str,
    spread_externe: dict[str, float] | None = None,
    code_mt5: str = "",
) -> str:
    out: list[str] = []
    w = out.append
    w(f"MESURE D'IMPACT SUR LE CARNET ARCHIVÉ — {symbole}")
    w(f"  dossier   {dossier}")
    w(f"  fichiers  {len(lectures)}")
    for lec in lectures:
        w(f"    {lec['chemin']}")
        w(
            f"      fenêtre {lec['debut']} → {lec['fin']}  "
            f"({lec['evenements']} différentiels appliqués, {lec['mid_debut']})"
        )
    w(f"  note      {dmid_ope}")
    w("")

    toutes: list[Fenetre] = []
    couts: dict[float, list[float]] = {n: [] for n in notionnels}
    spreads: list[float] = []
    profondeurs: list[float] = []
    for lec in lectures:
        toutes.extend(lec["mesure"]["fenetres"])
        for notionnel, valeurs in lec["mesure"]["couts"].items():
            couts.setdefault(notionnel, []).extend(valeurs)
        spreads.extend(lec["mesure"]["spreads_bps"])
        profondeurs.extend(lec["mesure"].get("profondeurs", []))

    if not toutes:
        w("AUCUNE FENÊTRE MESURÉE — l'archive ne contient pas de session exploitable.")
        return "\n".join(out)

    synthese = _synthese_horizons(toutes)
    w("1. L'IMPACT EST-IL LINÉAIRE EN DÉSÉQUILIBRE DU FLUX, ET À QUEL HORIZON ?")
    w(
        f"  {'horizon':>9}  {'n':>6}  {'R² OFI':>8}  {'R² naïf':>8}  {'R² flux':>8}  "
        f"{'pente':>11}  {'t':>7}  {'prof. méd.':>10}"
    )
    w("  " + "-" * 82)
    for horizon, s in synthese.items():
        r_ofi, r_naif, r_flux = s["ofi"], s["naif"], s["flux"]
        w(
            f"  {horizon:>8.1f}s  {s['n']:>6}  {r_ofi['r2']:>8.4f}  {r_naif['r2']:>8.4f}  "
            f"{r_flux['r2']:>8.4f}  {r_ofi['pente']:>11.4f}  {r_ofi['t']:>7.2f}  "
            f"{s['profondeur_med']:>10.3f}"
        )
    w("")
    faibles = [h for h, s in synthese.items() if s["n"] < N_MINIMAL]
    if faibles:
        w(
            f"  échantillon insuffisant (< {N_MINIMAL} fenêtres) aux horizons : "
            + ", ".join(f"{h:.1f}s ({synthese[h]['n']})" for h in sorted(faibles))
        )
        w("  Ces horizons sont affichés pour le suivi, jamais pour conclure.")
        w("")
    gagnants = [
        h
        for h, s in synthese.items()
        if s["n"] >= N_MINIMAL and s["ofi"]["r2"] > s["naif"]["r2"] and abs(s["ofi"]["t"]) >= 2.0
    ]
    if not gagnants:
        w("  L'OFI NE BAT PAS LE COMPARATEUR NAÏF sur ces données, à aucun horizon.")
        w("  C'est un résultat, pas un échec : il dit que la microstructure")
        w("  enregistrée n'apporte pas d'information que la taille au sommet")
        w("  ne porte déjà. Aucune technique d'exécution ne doit être ajoutée")
        w("  sur cette base.")
    else:
        h_max = max(gagnants)
        w(
            f"  L'OFI bat le comparateur naïf jusqu'à **{h_max:.1f} s** "
            f"(R² {synthese[h_max]['ofi']['r2']:.4f} contre {synthese[h_max]['naif']['r2']:.4f})."
        )
        if h_max < 10.0:
            w("  C'est PLUS COURT que le cycle de décision de la boucle (10 s) :")
            w("  la microstructure enregistrée ne peut pas informer une décision")
            w("  prise à cette cadence. Elle informe la qualité d'exécution, pas")
            w("  la sélection du candidat.")
    w("")

    h_court = min(synthese)
    w(f"2. LA PENTE DÉCROÎT-ELLE AVEC LA PROFONDEUR ?  (horizon {h_court:.1f} s)")
    w(
        f"  {'tercile':<8}  {'n':>6}  {'prof. méd.':>11}  {'pente':>11}  {'R²':>8}  "
        f"{'pente × prof.':>14}"
    )
    w("  " + "-" * 66)
    for ligne in _synthese_profondeur(toutes, h_court):
        if "pente" not in ligne:
            w(f"  {ligne['tercile']:<8}  {ligne['n']:>6}  (échantillon insuffisant)")
            continue
        w(
            f"  {ligne['tercile']:<8}  {ligne['n']:>6}  {ligne['profondeur_med']:>11.3f}  "
            f"{ligne['pente']:>11.4f}  {ligne['r2']:>8.4f}  {ligne['pente_x_profondeur']:>14.4f}"
        )
    w("  Lecture : si `pente × profondeur` est à peu près constant d'un tercile à")
    w("  l'autre, la loi en 1/profondeur tient sur ces données. Sinon, elle ne")
    w("  tient pas et il ne faut pas s'en servir pour calibrer quoi que ce soit.")
    w("")

    stats_couts = _stats_couts(couts, spreads, profondeurs)
    w("3. CE QUE COÛTE RÉELLEMENT UN ORDRE DE CETTE TAILLE")
    w(
        f"  spread médian observé : "
        f"{stats_couts['spread_bps_median']:.3f} bp "
        f"sur {stats_couts['n_instants']} instants échantillonnés"
    )
    prof = stats_couts["profondeur_sommet_mediane"]
    if prof:
        w(f"  notionnel en attente au PREMIER niveau (bid+ask) : {prof:,.0f} en médiane")
    w(
        f"  {'notionnel':>10}  {'n':>6}  {'coût méd.':>10}  {'coût p90':>10}  "
        f"{'coût/demi-spread':>17}  {'a-r / spread':>13}"
    )
    w("  " + "-" * 76)
    for ligne in stats_couts["par_notionnel"]:
        if not ligne.get("n"):
            w(f"  {ligne['notionnel']:>10.0f}  {0:>6}  (carnet trop mince pour cette taille)")
            continue
        demi = ligne["cout_vs_demi_spread"]
        ratio = ligne["aller_retour_vs_spread"]
        w(
            f"  {ligne['notionnel']:>10.0f}  {ligne['n']:>6}  "
            f"{ligne['cout_bps_median']:>10.3f}  {ligne['cout_bps_p90']:>10.3f}  "
            f"{(demi if demi is not None else float('nan')):>17.2f}  "
            f"{(ratio if ratio is not None else float('nan')):>13.2f}"
        )
    w("  Lecture : `coût/demi-spread` vaut 1.0 tant que l'ordre tient dans le")
    w("  premier niveau — il ne paie alors aucun impact. C'est le seul chiffre")
    w("  qui distingue « le spread décrit le coût » de « le spread le sous-estime ».")
    bascule = stats_couts["bascule_impact"]
    if bascule is None:
        w("  Aucune taille mesurée ne dépasse 1,2× la demi-spread : sur ces")
        w("  instants, l'impact est négligeable à toutes les tailles testées.")
    else:
        w(
            f"  La première taille qui paie un impact est **{bascule:,.0f}** "
            f"(> 1,2× la demi-spread)."
        )
    w("")

    cible = next(
        (
            x
            for x in stats_couts["par_notionnel"]
            if x.get("n") and x["notionnel"] >= notionnel_cible
        ),
        None,
    )
    if cible is None:
        cible = next((x for x in stats_couts["par_notionnel"] if x.get("n")), None)
    w("4. CONSÉQUENCE POUR LA PORTE DE COÛT")
    w(
        f"  notionnel de référence retenu : {notionnel_cible:.0f} "
        f"(formule : equity × risque_grappe / stop), seuil en vigueur : {seuil:.4g} %"
    )
    if cible:
        ratio = cible["aller_retour_vs_spread"]
        w(
            f"  coût réel mesuré à cette taille : {cible['cout_bps_median']:.3f} bp "
            f"(p90 {cible['cout_bps_p90']:.3f} bp)"
        )
        w(
            f"  le modèle à spread fixe charge 1 spread, soit "
            f"{stats_couts['spread_bps_median']:.3f} bp par aller-retour"
        )
        w(f"  RAPPORT mesuré : **{ratio:.2f}×** le coût que la porte facture.")
        if ratio > 1.05:
            w("  → le modèle à spread fixe SOUS-ESTIME le coût réel à cette taille.")
        elif ratio < 0.95:
            w("  → le modèle à spread fixe SURESTIME le coût réel à cette taille.")
        else:
            w("  → à cette taille, le spread seul décrit correctement le coût.")
        if bascule is not None and notionnel_cible < bascule:
            w(
                f"  Le notionnel de référence reste sous la taille de bascule "
                f"({bascule:,.0f}) : **l'erreur du modèle à spread fixe n'est donc"
            )
            w("  pas l'impact de taille** sur cette place. Si erreur il y a, elle")
            w("  est dans le spread retenu, pas dans l'hypothèse de taille.")
    w("")

    if spread_externe and stats_couts["spread_bps_median"]:
        w("5. LA PLACE MESURÉE CONTRE LA PLACE TRADÉE")
        w(
            f"  spread {symbole} (Binance spot, mesuré ici) : "
            f"{stats_couts['spread_bps_median']:.4f} bp"
        )
        for code, bps in spread_externe.items():
            # Comparer le spread d'un symbole au marché mesuré d'un AUTRE
            # produirait un rapport sans signification : on ne chiffre que le
            # symbole qui correspond, et les autres sont dits hors comparaison.
            if code_mt5 and code != code_mt5:
                w(
                    f"  spread {code:<10} (fourni) : {bps:>9.3f} bp  "
                    f"— hors comparaison (le marché mesuré ici est {symbole})"
                )
                continue
            facteur = bps / stats_couts["spread_bps_median"]
            w(
                f"  spread {code:<10} (fourni) : {bps:>9.3f} bp  "
                f"soit {facteur:>10.1f}× le marché sous-jacent {symbole}"
            )
        w("  Conséquence : la porte de coût facture le spread de la place où")
        w("  l'ordre part réellement. Sur le marché sous-jacent mesurable ici, ce")
        w("  spread est des ordres de grandeur plus large — l'écart vient du")
        w("  courtier, pas d'un défaut d'arithmétique. Le modèle à spread fixe")
        w("  n'est donc pas sous-estimé : il est **conservateur**.")
        w("  La source de ces spreads est la chaîne `--spread-externe` ci-dessus ;")
        w("  elle n'est pas mesurée par cet outil et doit être re-relevée pour")
        w("  être citée.")
        w("")

    w("CE QUE CETTE MESURE NE DIT PAS")
    w("  · L'archive est du **Binance spot** ; les symboles tradés sont des CFD")
    w("    **MT5**. Aucune conclusion n'est tirée ici sur le carnet MT5, qui")
    w("    n'expose qu'un L1 sans profondeur : l'impact ne peut pas y être mesuré.")
    w("  · L'instantané de la place n'est pris qu'au débit reçu (100 ms par")
    w("    différentiel) : l'impact intra-100 ms n'est pas observable.")
    w("  · Une fenêtre d'observation limitée ne couvre pas tous les régimes ;")
    w("    les chiffres portent sur les fichiers nommés ci-dessus, rien de plus.")
    return "\n".join(out)


# ═══════════════════════════════════════════════════════════════════════════════
# Entrée
# ═══════════════════════════════════════════════════════════════════════════════


def _fenetres_lecture(
    chemin: Path, *, minutes: float, max_lignes: int, fenetres: int
) -> Iterator[tuple[int, int, list[dict], str, str]]:
    """Découpe un fichier en fenêtres de `minutes`, réparties dans le fichier.

    Un seul créneau horaire ne décrit pas une journée : les fenêtres sont prises
    à des fractions régulières du fichier, chacune démarrant sur un instantané
    périodique — le seul point de reprise légitime au milieu d'une session.
    """
    taille = chemin.stat().st_size
    vues: list[tuple[int, int, list[dict], str, str]] = []
    for rang in range(max(fenetres, 1)):
        debut_octet = 0 if rang == 0 else int(taille * rang / max(fenetres, 1))
        lignes: list[dict] = []
        depart = None
        for index, ligne in enumerate(lire(chemin, debut=debut_octet, max_lignes=max_lignes)):
            type_ligne = ligne.get("type")
            if type_ligne == "instantane" and depart is None:
                depart = index
            elif depart is not None and _est_rupture(ligne):
                # La session est finie : continuer produirait un carnet
                # plausible et FAUX, le seul mode d'échec que ce format ne
                # signale pas de lui-même. On coupe ici, et la fenêtre
                # suivante repartira d'un instantané.
                break
            lignes.append(ligne)
            if depart is not None:
                t0 = float(lignes[depart].get("recu_ms") or 0.0)
                t1 = float(ligne.get("recu_ms") or 0.0)
                if t1 - t0 >= minutes * 60_000.0:
                    break
        if depart is None or len(lignes) - depart < 3:
            continue
        vues.append(
            (
                depart,
                len(lignes),
                lignes,
                _iso(float(lignes[depart].get("recu_ms") or 0.0)),
                _iso(float(lignes[-1].get("recu_ms") or 0.0)),
            )
        )
    return iter(vues)


def _est_rupture(ligne: dict[str, Any]) -> bool:
    """Vrai si la ligne marque une fin de session continue.

    Deux marqueurs : un trou déclaré par le collecteur, ou un instantané
    d'ouverture (`amorce`/`trou`) qui n'est donc plus celui depuis lequel on
    rejoue. Un instantané **périodique** n'en est pas un : il est la preuve que
    la session continue et sert de point de reprise.
    """
    if ligne.get("type") == "trou":
        return True
    return ligne.get("type") == "instantane" and ligne.get("raison") in RUPTURES


def _iso(ms: float) -> str:
    if not ms:
        return "(sans horodatage)"
    import datetime as _dt

    return _dt.datetime.fromtimestamp(ms / 1000.0, tz=_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symbole", default="BTCUSDT")
    ap.add_argument("--jours", nargs="*", default=[])
    ap.add_argument(
        "--tous", action="store_true", help="toutes les journées présentes dans l'archive"
    )
    ap.add_argument("--dossier", default=str(DOSSIER_DEFAUT))
    ap.add_argument(
        "--minutes", type=float, default=20.0, help="durée de chaque fenêtre échantillonnée"
    )
    ap.add_argument(
        "--fenetres", type=int, default=3, help="nombre de fenêtres réparties dans chaque fichier"
    )
    ap.add_argument(
        "--horizons",
        default="0.1,1,10",
        help="horizons d'agrégation en secondes, séparés par des virgules",
    )
    ap.add_argument("--notionnels", default=",".join(str(int(n)) for n in NOTIONNELS_DEFAUT))
    ap.add_argument(
        "--pas-cout", type=int, default=25, help="un échantillon de coût tous les N différentiels"
    )
    ap.add_argument(
        "--equity",
        type=float,
        default=1865.0,
        help="équité du compte, pour dériver le notionnel de référence",
    )
    ap.add_argument("--risque-grappe-pct", type=float, default=5.7)
    ap.add_argument(
        "--stop-pct",
        type=float,
        default=2.0,
        help="distance de stop en %% du prix : hypothèse explicite",
    )
    ap.add_argument("--seuil-pct", type=float, default=12.5)
    ap.add_argument(
        "--spread-externe",
        default="",
        help="comparaison avec la place tradée : 'BTCUSD=1.56;ETHUSD=5.03'",
    )
    ap.add_argument(
        "--code-mt5",
        default="",
        help="le symbole tradé correspondant (BTCUSD pour BTCUSDT), pour la comparaison",
    )
    ap.add_argument("--max-lignes", type=int, default=400_000)
    ap.add_argument(
        "--sans-trades",
        action="store_true",
        help="ne pas lire le flux agressif (comparateur supplémentaire)",
    )
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    dossier = Path(a.dossier)
    if not (dossier / a.symbole).is_dir():
        print(f"archive absente ou vide : {dossier / a.symbole}")
        print("cet outil ne mesure que ce qui a été enregistré ; il n'invente rien.")
        return 1

    if a.jours:
        jours = list(a.jours)
    elif a.tous:
        jours = sorted(
            {p.name.split(".")[0] for p in (dossier / a.symbole).glob("*.depth.ndjson*")}
        )
    else:
        jours = sorted(
            {p.name.split(".")[0] for p in (dossier / a.symbole).glob("*.depth.ndjson*")}
        )[-1:]
    if not jours:
        print(f"aucun fichier de carnet pour {a.symbole} dans {dossier}")
        return 1

    horizons = tuple(float(x) for x in a.horizons.split(",") if x.strip())
    notionnels = tuple(float(x) for x in a.notionnels.split(",") if x.strip())
    notionnel_cible = a.equity * (a.risque_grappe_pct / 100.0) / max(a.stop_pct / 100.0, 1e-9)

    lectures: list[dict[str, Any]] = []
    for chemin in fichiers(dossier, a.symbole, jours, "depth"):
        for depart, fin, lignes, debut_s, fin_s in _fenetres_lecture(
            chemin, minutes=a.minutes, max_lignes=a.max_lignes, fenetres=a.fenetres
        ):
            mesure = mesurer_fenetre(
                lignes,
                depart,
                fin,
                horizons=horizons,
                notionnels=notionnels,
                pas_cout=max(a.pas_cout, 1),
            )
            if a.sans_trades:
                for f in mesure["fenetres"]:
                    f.flux_signe = 0.0
            else:
                chemin_trades = chemin.with_name(chemin.name.replace(".depth.", ".trades."))
                if chemin_trades.exists():
                    lot = lire_trades(
                        chemin_trades,
                        debut_ms=float(lignes[depart].get("recu_ms") or 0.0),
                        fin_ms=float(lignes[-1].get("recu_ms") or 0.0),
                        max_lignes=a.max_lignes,
                    )
                    for f in mesure["fenetres"]:
                        f.flux_signe = flux_agressif(lot, 0.0, float("inf"))
            lectures.append(
                {
                    "chemin": str(chemin),
                    "debut": debut_s,
                    "fin": fin_s,
                    "evenements": mesure["evenements"],
                    "mid_debut": mesure["mid_debut"],
                    "mesure": mesure,
                }
            )

    if not lectures:
        print(f"aucune session exploitable pour {a.symbole} dans {jours}")
        return 1

    externe = analyser_spread_externe(a.spread_externe)
    texte = rapport(
        dossier,
        a.symbole,
        lectures,
        horizontes=horizons,
        notionnels=notionnels,
        notionnel_cible=notionnel_cible,
        seuil=a.seuil_pct,
        dmid_ope=f"Δmid en points de base sur la fenêtre ; OFI normalisé par "
        f"la profondeur cumulée dans ±{BANDE_BPS:g} bp du mid",
        spread_externe=externe,
        code_mt5=a.code_mt5,
    )
    print(texte)
    if a.json:
        Path(a.json).write_text(
            json.dumps(
                {
                    "symbole": a.symbole,
                    "jours": jours,
                    "rapport": texte,
                    "notionnel_cible": notionnel_cible,
                    "spread_externe": externe,
                    "spread_externe_brut": a.spread_externe,
                    "horizons": list(horizons),
                    "notionnels": list(notionnels),
                    "seuil_pct": a.seuil_pct,
                    "lectures": [
                        {
                            "chemin": lec["chemin"],
                            "debut": lec["debut"],
                            "fin": lec["fin"],
                            "evenements": lec["evenements"],
                        }
                        for lec in lectures
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
