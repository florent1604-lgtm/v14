"""Quels indicateurs séparent réellement les gagnants des perdants ?

LE PIÈGE QUE CE MODULE EXISTE POUR ÉVITER
------------------------------------------
Tester 100 indicateurs sur 200 trades garantit d'en trouver des « significatifs »
**par pur hasard**. À un seuil de 5 %, cinq indicateurs sur cent ressortiront
alors qu'aucun ne porte d'information. Un système qui câblerait ces cinq-là
dans sa décision aurait l'air brillant en arrière et perdrait de l'argent en
avant. C'est la définition du sur-ajustement, et V12 en a déjà payé le prix :
des configurations à PF 2,31 en Python, PF 0,90 au testeur natif.

TROIS GARDE-FOUS, TOUS OBLIGATOIRES
------------------------------------
1. **Taille d'effet non paramétrique** (delta de Cliff), pas une moyenne. Une
   moyenne se fait démolir par trois trades extrêmes ; Cliff compte simplement
   combien de fois un gagnant dépasse un perdant.
2. **Test de permutation** — on mélange les étiquettes gagnant/perdant des
   milliers de fois pour voir à quelle fréquence le hasard fait aussi bien. Pas
   d'hypothèse de normalité, et aucune dépendance à scipy.
3. **Correction de Benjamini-Hochberg** pour tests multiples. Sans elle, la
   liste des « découvertes » est du bruit trié.

CE QUE CE MODULE NE FAIT PAS
-----------------------------
Il ne câble **rien**. Il produit un classement à lire, discuter et valider
temporellement avant qu'un seul indicateur n'approche d'une porte de décision.
Un discriminant trouvé ici est une **hypothèse**, pas une conclusion.
"""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass, field

#: En dessous, aucun classement n'est produit. 30 trades par groupe est déjà
#: peu ; c'est un plancher, pas un objectif.
MIN_PAR_GROUPE = 30

#: Permutations du test. 2000 donne une résolution de p ≈ 0.0005, suffisante
#: après correction.
N_PERMUTATIONS = 2000

#: Taux de fausses découvertes accepté (Benjamini-Hochberg).
FDR = 0.10

#: |delta| en dessous duquel l'effet est trop faible pour mériter attention,
#: même statistiquement significatif. Convention usuelle : 0.147 = « petit ».
DELTA_NEGLIGEABLE = 0.147


@dataclass
class Discriminant:
    """Le pouvoir de séparation d'UN indicateur."""
    nom: str
    n_gagnants: int
    n_perdants: int
    mediane_gagnants: float
    mediane_perdants: float
    delta: float              # Cliff ∈ [−1, 1] : >0 = plus élevé chez les gagnants
    p_brut: float
    p_corrige: float = 1.0
    retenu: bool = False
    taille: str = ""          # négligeable / petit / moyen / grand

    def to_dict(self) -> dict:
        return asdict(self)


def _mediane(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if not n:
        return 0.0
    m = n // 2
    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2.0


def cliff_delta(a: list[float], b: list[float]) -> float:
    """Delta de Cliff : P(a > b) − P(a < b), dans [−1, 1].

    Non paramétrique et insensible aux valeurs extrêmes — contrairement à une
    différence de moyennes, qu'un seul trade aberrant suffit à retourner.
    """
    if not a or not b:
        return 0.0
    # Tri + balayage : O(n log n) au lieu du O(n²) de la double boucle naïve.
    sb = sorted(b)
    n_b = len(sb)
    sup = inf = 0
    for x in a:
        lo, hi = 0, n_b
        while lo < hi:                       # nb d'éléments de b strictement < x
            mid = (lo + hi) // 2
            if sb[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        moins = lo
        lo, hi = 0, n_b
        while lo < hi:                       # nb d'éléments de b <= x
            mid = (lo + hi) // 2
            if sb[mid] <= x:
                lo = mid + 1
            else:
                hi = mid
        egaux = lo - moins
        sup += moins
        inf += n_b - moins - egaux
    total = len(a) * n_b
    return (sup - inf) / total if total else 0.0


def _libelle_taille(d: float) -> str:
    a = abs(d)
    if a < DELTA_NEGLIGEABLE:
        return "négligeable"
    if a < 0.33:
        return "petit"
    if a < 0.474:
        return "moyen"
    return "grand"


def _p_permutation(a: list[float], b: list[float], observe: float,
                   n: int, graine: int) -> float:
    """Fréquence à laquelle le hasard produit un effet au moins aussi fort.

    On mélange les étiquettes, pas les valeurs : c'est l'appartenance au groupe
    « gagnant » qu'on teste, et rien d'autre.
    """
    rng = random.Random(graine)
    ensemble = a + b
    na = len(a)
    cible = abs(observe)
    au_moins = 0
    for _ in range(n):
        rng.shuffle(ensemble)
        if abs(cliff_delta(ensemble[:na], ensemble[na:])) >= cible:
            au_moins += 1
    # +1 au numérateur et au dénominateur : un p-value de 0 exact est un
    # artefact du nombre fini de permutations, jamais une certitude.
    return (au_moins + 1) / (n + 1)


def _benjamini_hochberg(ps: list[float], fdr: float) -> list[bool]:
    """Contrôle du taux de fausses découvertes. Rend un masque de rejets."""
    m = len(ps)
    if not m:
        return []
    ordre = sorted(range(m), key=lambda i: ps[i])
    seuil_max = -1
    for rang, i in enumerate(ordre, start=1):
        if ps[i] <= fdr * rang / m:
            seuil_max = rang
    retenus = [False] * m
    for rang, i in enumerate(ordre, start=1):
        if rang <= seuil_max:
            retenus[i] = True
    return retenus


@dataclass
class Rapport:
    """Résultat complet d'une analyse. Lisible tel quel."""
    n_trades: int = 0
    n_gagnants: int = 0
    n_perdants: int = 0
    n_indicateurs: int = 0
    fdr: float = FDR
    suffisant: bool = False
    message: str = ""
    discriminants: list[Discriminant] = field(default_factory=list)

    @property
    def retenus(self) -> list[Discriminant]:
        return [d for d in self.discriminants if d.retenu]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["retenus"] = [x.to_dict() for x in self.retenus]
        return d


def analyser(echantillons: list[tuple[dict, float]], *,
             min_par_groupe: int = MIN_PAR_GROUPE,
             n_permutations: int = N_PERMUTATIONS,
             fdr: float = FDR,
             graine: int = 12345) -> Rapport:
    """Classe les indicateurs par pouvoir de séparation gagnants / perdants.

    Args:
        echantillons: liste de ``(panel_indicateurs, pnl_R)`` — un couple par
            trade clos. ``pnl_R`` est le résultat **net de coûts**, en R.
        min_par_groupe: plancher d'échantillon par groupe.
        n_permutations: nombre de permutations du test.
        fdr: taux de fausses découvertes accepté.
        graine: fixée — deux analyses des mêmes données doivent coïncider.

    Returns:
        Rapport — jamais d'exception, même sur données vides ou incohérentes.
    """
    rap = Rapport(n_trades=len(echantillons), fdr=fdr)

    gagnants = [ind for ind, r in echantillons if r > 0]
    perdants = [ind for ind, r in echantillons if r <= 0]
    rap.n_gagnants, rap.n_perdants = len(gagnants), len(perdants)

    if rap.n_gagnants < min_par_groupe or rap.n_perdants < min_par_groupe:
        rap.message = (
            f"échantillon insuffisant : {rap.n_gagnants} gagnants / "
            f"{rap.n_perdants} perdants, minimum {min_par_groupe} par groupe. "
            "Toute conclusion tirée maintenant serait du bruit.")
        return rap

    noms = sorted({k for ind in gagnants + perdants for k in ind})
    rap.n_indicateurs = len(noms)

    bruts: list[Discriminant] = []
    for nom in noms:
        a = [float(v) for ind in gagnants
             if (v := ind.get(nom)) is not None and math.isfinite(float(v))]
        b = [float(v) for ind in perdants
             if (v := ind.get(nom)) is not None and math.isfinite(float(v))]
        if len(a) < min_par_groupe or len(b) < min_par_groupe:
            continue           # indicateur trop souvent absent : on l'ignore

        d = cliff_delta(a, b)
        p = _p_permutation(a, b, d, n_permutations, graine)
        bruts.append(Discriminant(
            nom=nom, n_gagnants=len(a), n_perdants=len(b),
            mediane_gagnants=round(_mediane(a), 5),
            mediane_perdants=round(_mediane(b), 5),
            delta=round(d, 4), p_brut=round(p, 5),
            taille=_libelle_taille(d)))

    if not bruts:
        rap.message = "aucun indicateur suffisamment renseigné dans les deux groupes"
        return rap

    masque = _benjamini_hochberg([x.p_brut for x in bruts], fdr)
    m = len(bruts)
    for i, x in enumerate(bruts):
        # p corrigé « à la BH » : borné à 1, monotone après tri.
        rang = sorted(range(m), key=lambda j: bruts[j].p_brut).index(i) + 1
        x.p_corrige = round(min(1.0, x.p_brut * m / rang), 5)
        # Un effet négligeable n'est pas retenu, même significatif : sur un gros
        # échantillon, tout finit par être significatif sans être utile.
        x.retenu = bool(masque[i]) and abs(x.delta) >= DELTA_NEGLIGEABLE

    rap.discriminants = sorted(bruts, key=lambda x: -abs(x.delta))
    rap.suffisant = True
    n_ret = len(rap.retenus)
    rap.message = (
        f"{n_ret} discriminant(s) retenu(s) sur {m} testés "
        f"(FDR {fdr:.0%}, effet ≥ {DELTA_NEGLIGEABLE}). "
        + ("Ce sont des HYPOTHÈSES : à valider temporellement avant tout câblage."
           if n_ret else "Aucun indicateur ne sépare les groupes de façon crédible."))
    return rap


def depuis_journal(chemin) -> list[tuple[dict, float]]:
    """Extrait les couples (indicateurs, résultat) du journal des trades.

    Ne retient que les lignes portant un panel : un trade journalisé avant
    l'activation du panel n'a rien à dire ici.
    """
    import json
    from pathlib import Path

    p = Path(chemin)
    if not p.exists():
        return []
    out: list[tuple[dict, float]] = []
    for ligne in p.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            d = json.loads(ligne)
            ind = d.get("indicators")
            pnl = float(d["pnl_r"])
            if ind and math.isfinite(pnl):
                out.append((ind, pnl))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return out
