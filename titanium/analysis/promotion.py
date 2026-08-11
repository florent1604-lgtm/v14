"""Décide si une cellule (classe d'actif, S≥3) peut passer en mode PROD.

Ce module ne trade pas, n'exécute rien et n'écrit rien : il rend un verdict.
La décision d'ouvrir le mode réel appartient au registre, et au geste humain
qui l'alimente.

Pourquoi promouvoir par CELLULE et non par symbole
---------------------------------------------------
Le registre d'edge distingue ~204 seaux `(symbole, sens, famille, piliers)`.
Sous l'hypothèse nulle, un critère à 20 trades et +0.05 R rend environ 41 %
de faux positifs par seau — soit ~84 seaux ouvrant le mode réel par pur
hasard. Le seuil des vingt trades n'était donc pas une garantie, c'était une
cérémonie.

Regrouper par `(classe d'actif, S≥3)` ramène à **quatre** cellules. C'est la
différence entre un critère qui mesure et un critère qui tire au sort.

Pourquoi la strate S≥3 et non l'agrégat
----------------------------------------
`require_edge` n'a que deux effets sur la porte : le quorum et le test
d'edge. Le sizing, le SL/TP, le RiskGate et la gestion sont identiques.
L'ensemble des entrées PROD est donc un **sous-ensemble strict** des entrées
EXPLORE, et non une autre population. Mesurer sur l'agrégat serait invalide
(paradoxe de Simpson : la strate S=2, la plus peuplée, domine la moyenne) ;
mesurer sur la strate S≥3 est valide et sans extrapolation.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# ── Constantes du protocole. Chacune répond à une faiblesse identifiée.
STRATE_MIN_SUPPORT = 3       # la strate mesurée est celle du quorum PROD
MIN_TRADES_CELLULE = 60      # puissance ~62 % contre un edge de +0.30 R, σ=1
BORNE_BASSE_PLANCHER = 0.05  # en R — remplace un facteur d'escompte arbitraire
PF_MIN = 1.30                # marge au-dessus du seuil de rentabilité
BOOTSTRAP_N = 10_000
BLOC = 5                     # longueur de bloc : absorbe l'autocorrélation
FDR_Q = 0.10
SEGMENTS = 3
PART_COUT_MAX = 0.40         # le coût ne doit pas manger 40 % de l'espérance
PART_EXACT_MIN = 0.90        # 90 % des trades doivent porter un coût mesuré
FENETRE_DEMOTION = 30


@dataclass(frozen=True)
class Cell:
    """Unité de promotion."""

    asset_class: str
    support_bucket: str = "S>=3"

    def key(self) -> str:
        return f"{self.asset_class}|{self.support_bucket}"


@dataclass
class PromotionVerdict:
    cell: str
    n: int = 0
    expectancy_r: float = 0.0
    lower_95_r: float = 0.0
    profit_factor: float = 0.0
    sigma_r: float = 0.0
    segments_r: list = field(default_factory=list)
    mean_cost_r: float = 0.0
    part_exact: float = 0.0
    n_requis: int = 0
    survit_fdr: bool = False
    eligible: bool = False
    bloquants: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "cell": self.cell, "n": self.n,
            "expectancy_r": round(self.expectancy_r, 4),
            "lower_95_r": round(self.lower_95_r, 4),
            "profit_factor": round(self.profit_factor, 3),
            "sigma_r": round(self.sigma_r, 4),
            "segments_r": [round(x, 4) for x in self.segments_r],
            "mean_cost_r": round(self.mean_cost_r, 4),
            "part_exact": round(self.part_exact, 3),
            "n_requis": self.n_requis,
            "survit_fdr": self.survit_fdr,
            "eligible": self.eligible,
            "bloquants": list(self.bloquants),
        }


def block_bootstrap_lower(xs, *, alpha: float = 0.05,
                          n_boot: int = BOOTSTRAP_N,
                          bloc: int = BLOC,
                          seed: int = 20260807) -> float:
    """Borne basse unilatérale de la moyenne, par blocs mobiles.

    Le bootstrap i.i.d. classique suppose des trades indépendants. Ils ne le
    sont pas : ils se suivent dans le temps, partagent un régime de marché,
    et plusieurs peuvent être ouverts simultanément sur des actifs corrélés.
    Rééchantillonner par **blocs contigus** conserve cette dépendance locale
    et rend une borne honnête plutôt que flatteuse.
    """
    xs = [float(x) for x in xs]
    n = len(xs)
    if n == 0:
        return float("-inf")
    if n < bloc:
        bloc = n

    rng = random.Random(seed)
    debuts_possibles = n - bloc          # inclusif, d'où le +1 plus bas
    moyennes = []
    for _ in range(n_boot):
        total = 0.0
        pris = 0
        while pris < n:
            debut = rng.randint(0, debuts_possibles)
            # On ne boucle PAS sur la fin du tableau : `xs[i % n]` recollerait
            # la fin au début et fabriquerait une continuité qui n'existe pas.
            fin = min(debut + bloc, n)
            for i in range(debut, fin):
                if pris >= n:
                    break
                total += xs[i]
                pris += 1
        moyennes.append(total / n)
    moyennes.sort()
    return moyennes[int(alpha * n_boot)]


def sample_size_required(sigma: float, observed_r: float, *,
                         plancher: float = BORNE_BASSE_PLANCHER,
                         z: float = 1.645) -> int:
    """Nombre de trades pour que la borne basse dépasse le plancher.

    ⚠️ À n'utiliser que pour **planifier**, jamais pour justifier. Calculé sur
    l'espérance observée, il est optimiste par construction : si l'espérance
    vraie est plus faible, le n réel sera plus grand.
    """
    if not math.isfinite(sigma) or sigma <= 0:
        return 999_999
    if observed_r <= plancher:
        return 999_999
    return math.ceil((z * sigma / (observed_r - plancher)) ** 2)


def _segments(xs, n_segments: int = SEGMENTS):
    if not xs:
        return [[] for _ in range(n_segments)]
    k, m = divmod(len(xs), n_segments)
    return [xs[i * k + min(i, m):(i + 1) * k + min(i + 1, m)]
            for i in range(n_segments)]


def _p_unilaterale(xs, plancher: float) -> float:
    """p-valeur approchée de H₀ : espérance ≤ plancher.

    Test de Student unilatéral. Approché, mais **réel** — bien préférable à
    une p-valeur fabriquée (0.01 si éligible, 1.0 sinon), qui donnerait à la
    correction de multiplicité l'apparence de la rigueur sans la substance.
    """
    n = len(xs)
    if n < 3:
        return 1.0
    moy = sum(xs) / n
    var = sum((x - moy) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return 0.0 if moy > plancher else 1.0
    t = (moy - plancher) / math.sqrt(var / n)
    # Approximation normale : à n ≥ 60, l'écart au Student est négligeable.
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def evaluate_cells(trades, *, rejected_lines: int = 0) -> list:
    """Applique le protocole à chaque cellule. Ne modifie rien."""
    from titanium.analysis.discriminants import _benjamini_hochberg

    seaux: dict = {}
    for t in trades:
        if getattr(t, "source", "live") != "live":
            continue
        if getattr(t, "support_pillars", 0) < STRATE_MIN_SUPPORT:
            continue
        classe = getattr(t, "asset_class", "")
        if not classe:
            continue          # une classe inconnue n'est jamais promouvable
        seaux.setdefault(f"{classe}|S>=3", []).append(t)

    verdicts = []
    for cle, lot in seaux.items():
        v = PromotionVerdict(cell=cle)
        xs = [float(t.pnl_r) for t in lot]
        n = len(xs)
        v.n = n
        if n == 0:
            v.bloquants.append("C1_AUCUN_TRADE")
            verdicts.append(v)
            continue

        v.expectancy_r = sum(xs) / n
        v.sigma_r = (sum((x - v.expectancy_r) ** 2 for x in xs) / n) ** 0.5
        v.n_requis = sample_size_required(v.sigma_r, v.expectancy_r)
        couts_connus = [
            float(t.cost_r) for t in lot if getattr(t, "cost_r", None) is not None
        ]
        v.mean_cost_r = (
            sum(couts_connus) / len(couts_connus) if couts_connus else 0.0
        )
        v.part_exact = sum(1 for t in lot
                           if getattr(t, "exact_cost", False)) / n

        gains = [x for x in xs if x > 0]
        pertes = [x for x in xs if x < 0]
        v.profit_factor = (sum(gains) / abs(sum(pertes))
                           if pertes and sum(pertes) != 0 else float("inf"))

        seg = _segments(xs)
        v.segments_r = [sum(s) / len(s) if s else 0.0 for s in seg]
        v.lower_95_r = (block_bootstrap_lower(xs) if n >= 10
                        else float("-inf"))

        # ── Les huit critères. Tous doivent tomber.
        if n < MIN_TRADES_CELLULE:
            v.bloquants.append(f"C1_TAILLE({n}/{MIN_TRADES_CELLULE})")
        if v.part_exact < PART_EXACT_MIN:
            v.bloquants.append(
                f"C3_COUT_MESURE({v.part_exact:.0%}/{PART_EXACT_MIN:.0%})")
        if v.lower_95_r <= BORNE_BASSE_PLANCHER:
            v.bloquants.append(
                f"C4_BORNE_BASSE({v.lower_95_r:+.3f}<={BORNE_BASSE_PLANCHER})")
        if v.profit_factor < PF_MIN:
            v.bloquants.append(f"C5_PF({v.profit_factor:.2f}<{PF_MIN})")
        positifs = sum(1 for s in v.segments_r if s > 0)
        pire = min(v.segments_r) if v.segments_r else 0.0
        if positifs < 2 or pire < -0.10:
            v.bloquants.append(
                f"C6_STABILITE({positifs}/3 positifs, pire {pire:+.3f})")
        if v.expectancy_r > 0 and v.mean_cost_r / v.expectancy_r > PART_COUT_MAX:
            v.bloquants.append(
                f"C8_COUT_RELATIF({v.mean_cost_r / v.expectancy_r:.0%})")

        verdicts.append(v)

    # ── C7 : correction de multiplicité sur TOUTES les cellules évaluées.
    #    `_benjamini_hochberg` rend un MASQUE de rejets, pas des q-valeurs :
    #    traiter sa sortie comme un nombre et la comparer à 0.10 inverserait
    #    silencieusement le critère (`True > 0.10` étant vrai, toute cellule
    #    éligible se verrait bloquée).
    if verdicts:
        ps = [_p_unilaterale([float(t.pnl_r) for t in seaux[v.cell]],
                             BORNE_BASSE_PLANCHER) for v in verdicts]
        rejets = _benjamini_hochberg(ps, FDR_Q)
        for v, rejete in zip(verdicts, rejets):
            v.survit_fdr = bool(rejete)
            if not v.survit_fdr:
                v.bloquants.append("C7_FDR_NON_SIGNIFICATIF")

    try:
        rejets_journal = max(0, int(rejected_lines))
    except (TypeError, ValueError):
        rejets_journal = 1

    for v in verdicts:
        if rejets_journal:
            v.bloquants.append(f"C0_JOURNAL_REJETS({rejets_journal})")
        v.eligible = not v.bloquants
    return verdicts


def check_demotion(trades, promues) -> list:
    """Cellules à rétrograder : les N derniers trades ne paient plus.

    Une promotion doit pouvoir se défaire toute seule. Sans rétrogradation
    automatique, une cellule promue sur un régime favorable resterait ouverte
    après le retournement de ce régime.
    """
    a_revoquer = []
    for cle in promues:
        classe = cle.split("|")[0]
        lot = [t for t in trades
               if getattr(t, "asset_class", "") == classe
               and getattr(t, "support_pillars", 0) >= STRATE_MIN_SUPPORT
               and getattr(t, "source", "live") == "live"]
        recents = [float(t.pnl_r) for t in lot[-FENETRE_DEMOTION:]]
        if len(recents) >= 10 and sum(recents) / len(recents) <= 0:
            a_revoquer.append(cle)
    return a_revoquer
