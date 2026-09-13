"""Momentum intraday sur les symboles que la porte de coût laisse passer.

    python tools/mesure_momentum_intraday.py --symboles US500 GER40
    python tools/mesure_momentum_intraday.py --symbole US500 --json out.json

POURQUOI CET OUTIL EXISTE
-------------------------
Gao, Han, Li & Zhou (2018), *Market Intraday Momentum* : le retour de la
**première** demi-heure de la séance prédit celui de la **dernière**, mesuré
depuis la clôture de la veille. Publié sur le S&P 500, il est l'hypothèse au coût
de test le plus faible du catalogue (H1 à H6, doc `CATALOGUE_HYPOTHESES_ALPHA`).

Et il porte sur les seuls symboles qui survivent au coût : la porte laisse
passer US500 et GER40 sur la plupart de leurs unités, pendant qu'elle refuse la
crypto environ 85 % du temps pour un spread qui vaut jusqu'à deux fois le stop.
Si un effet institutionnel doit survivre ici, c'est là qu'il faut le chercher.

Ce que l'outil mesure, et ce qui rend un test NÉGATIF utile
---------------------------------------------------------
1. **l'effet brut** — pente de la régression du retour de clôture sur celui
   d'ouverture, avec R² et `t` ;
2. **le comparateur naïf, dans le même test** — sans lui, un R² non nul ne
   prouve rien : l'autocorrélation du retour de clôture, le retour de la veille,
   le retour du milieu de séance, et la volatilité passée pour la magnitude.
   C'est la leçon directe de la controverse VPIN ;
3. **la séparation dedans / dehors** — les dix premiers pour cent des séances
   servent à voir, les suivants à trancher, et le chiffre qui compte est celui
   du dehors ;
4. **le nombre de tests**, avec le seuil de famille qu'il impose ;
5. **la taille d'effet confrontée au coût d'aller-retour MESURÉ du symbole**,
   pris dans la colonne `spread` de l'archive et non dans un spread d'une autre
   place. Un effet qui existe mais coûte plus cher qu'il ne rapporte n'est pas
   un effet exploitable.

CE QU'IL REFUSE DE FAIRE
------------------------
- **Il ne déplace aucun seuil.** Le plafond de 12,5 %, la boucle et les plafonds
  de risque ne sont pas touchés : cet outil mesure, il ne décide pas.
- **Il ne conclut pas sur la magnitude seule.** Un effet de 1 point de base qui
  coûte 5 à exécuter est annoncé comme inexploitable, pas comme un résultat.
- **Il n'invente pas de séance.** La fenêtre est détectée dans les données par
  le volume, avec une tolérance qui absorbe le changement d'heure ; une journée
  dont la couverture est insuffisante est écartée et comptée, jamais complétée.
- **Il ne lit pas MT5.** Tout vient de l'archive de barres sur disque, donc il
  tourne en intégration continue, sans terminal.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

with suppress(AttributeError, ValueError):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Symboles que la porte de coût laisse réellement passer, d'après la mesure du
#: tunnel. C'est le point de départ, pas une liste de confort.
SYMBOLES_DEFAUT = ("US500", "GER40")

#: Durée de la séance de référence, en minutes. 390 = 09:30-16:00, la séance
#: actions américaine, celle que le papier étudie.
DUREE_SEANCE_MIN = 390

#: Largeur de la « demi-heure » du papier, en minutes.
LARGEUR_FENETRE_MIN = 30

#: Décalage maximal, en minutes, entre le début de séance détecté sur
#: l'échantillon et celui d'un jour donné. Absorbe le changement d'heure sans
#: avoir besoin d'une base de fuseaux, absente de Windows.
DECALAGE_MAX_MIN = 60

#: Part des séances tenue de côté pour trancher. Le reste sert à regarder.
PART_HORS_ECHANTILLON = 0.30

#: Couverture minimale d'une séance pour qu'elle soit mesurable.
COUVERTURE_MIN = 0.80

#: Nombre de tests de la famille, pour le seuil corrigé : deux symboles ×
#: (deux variantes d'ouverture, la clôture de la veille, le milieu de séance,
#: deux comparateurs de magnitude). Le seuil est annoncé ET appliqué.
TESTS_FAMILLE = 12


# ═══════════════════════════════════════════════════════════════════════════════
# Statistiques (sans dépendance : l'outil doit tourner partout)
# ═══════════════════════════════════════════════════════════════════════════════


def regression(xs: list[float], ys: list[float]) -> dict[str, float]:
    """Moindres carrés ordinaires avec ordonnée. R², pente et t de la pente."""
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
    t = 0.0
    if n > 2 and residu > 0:
        se = math.sqrt(residu / (n - 2) / sxx)
        t = pente / se if se > 0 else 0.0
    return {"n": float(n), "pente": pente, "r2": r2, "t": t, "ordonnee": ordonnee}


def ecart_conditionnel(
    valeurs: list[float],
    predicteur: list[float],
) -> dict[str, float]:
    """Écart moyen des deux groupes, en points de base, avec son erreur type.

    C'est la quantité exploitable : ce qu'un pari directionnel pris sur le
    signe du prédicteur capturerait **avant** coût. L'erreur type est celle de
    la différence de deux moyennes indépendantes.
    """
    haut = [v for v, p in zip(valeurs, predicteur, strict=True) if p > 0]
    bas = [v for v, p in zip(valeurs, predicteur, strict=True) if p < 0]
    if len(haut) < 3 or len(bas) < 3:
        return {
            "n_haut": len(haut),
            "n_bas": len(bas),
            "ecart_bps": 0.0,
            "erreur_type_bps": 0.0,
            "t": 0.0,
        }
    mh = statistics.mean(haut)
    mb = statistics.mean(bas)
    sh = statistics.stdev(haut) / math.sqrt(len(haut))
    sb = statistics.stdev(bas) / math.sqrt(len(bas))
    et = math.sqrt(sh * sh + sb * sb)
    ecart = 1e4 * (mh - mb)
    return {
        "n_haut": len(haut),
        "n_bas": len(bas),
        "ecart_bps": ecart,
        "erreur_type_bps": 1e4 * et,
        "t": (mh - mb) / et if et > 0 else 0.0,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Séances
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Seance:
    """Une journée mesurable : ce que la veille et l'ouverture annoncent."""

    jour: str
    debut_min: int  # minute du jour UTC
    #: Retour de la première demi-heure **depuis la clôture de la veille** :
    #: c'est la définition du papier, et elle contient donc le gap overnight.
    r_ouverture: float
    #: Même fenêtre, mesurée depuis l'ouverture de la séance : la variante
    #: sans le gap, publiée à côté pour que l'écart entre les deux soit visible.
    r_ouverture_session: float
    r_cloture: float
    r_milieu: float
    r_cloture_veille: float
    amplitude_ouverture: float
    volatilite_passee: float
    spread_bps: float
    prix_cloture: float
    barres: int


def _minutes(index) -> list[int]:
    return [t.hour * 60 + t.minute for t in index]


def _decalages_bougie(index) -> int:
    """Pas de temps en minutes, lu sur l'index plutôt que supposé."""
    if len(index) < 2:
        return 5
    return max(1, int((index[1] - index[0]).total_seconds() // 60))


def detecter_debuts(donnees, *, duree_min: int, pas_min: int) -> tuple[int, list]:
    """Début de séance modal, puis un début par jour autour de ce mode.

    Le mode est cherché sur tout l'échantillon : c'est la fenêtre de volume
    maximal, donc la séance réelle de l'instrument. Chaque jour choisit ensuite
    entre `mode − tolérance`, `mode` et `mode + tolérance` — ce qui suit le
    changement d'heure sans base de fuseaux, et empêche un jour creux de
    déplacer la séance vers une plage arbitraire.
    """
    minutes = _minutes(donnees.index)
    volumes = donnees["tick_volume"].to_numpy(dtype=float)
    largeur = max(1, duree_min // pas_min)

    debut_mode, meilleur = None, -1.0
    borne = len(volumes) - largeur
    for i in range(max(0, borne) + 1):
        somme = float(volumes[i : i + largeur].sum())
        if somme > meilleur:
            meilleur, debut_mode = somme, i
    if debut_mode is None:
        return 0, []
    mode_min = minutes[debut_mode] - (minutes[debut_mode] % pas_min)

    candidats = [mode_min - DECALAGE_MAX_MIN, mode_min, mode_min + DECALAGE_MAX_MIN]
    par_jour: list[tuple[Any, int]] = []
    groupes: dict[Any, list[int]] = {}
    for rang, t in enumerate(donnees.index):
        groupes.setdefault(t.date(), []).append(rang)
    for jour in sorted(groupes):
        positions = groupes[jour]
        if len(positions) < largeur:
            continue
        meilleur_min, meilleur_volume = None, -1.0
        for candidat in candidats:
            pris = [p for p in positions if candidat <= minutes[p] < candidat + duree_min]
            if len(pris) < largeur * COUVERTURE_MIN:
                continue
            volume = sum(volumes[p] for p in pris)
            if volume > meilleur_volume:
                meilleur_volume, meilleur_min = volume, candidat
        if meilleur_min is not None:
            # On garde l'objet date : c'est la clé des index par jour, et le
            # convertir ici en chaîne ferait un dictionnaire qui ne trouve rien.
            par_jour.append((jour, meilleur_min))
    return mode_min, par_jour


# ═══════════════════════════════════════════════════════════════════════════════
# Mesure d'un symbole
# ═══════════════════════════════════════════════════════════════════════════════


def lire_point(archive: Path, symbole: str) -> float | None:
    """Taille du point, lue dans les spécifications publiées par l'archiveur.

    Sans elle, la colonne `spread` (en points) ne se convertit pas en distance
    de prix, et le coût resterait un nombre sans unité.
    """
    chemin = Path(archive) / "_specifications.json"
    if not chemin.exists():
        return None
    try:
        specs = json.loads(chemin.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    entree = specs.get(symbole) if isinstance(specs, dict) else None
    if not isinstance(entree, dict):
        return None
    point = entree.get("point")
    return float(point) if isinstance(point, (int, float)) and point > 0 else None


def chargeur_depuis(archive: Path):
    """Chargeur branché sur l'emplacement d'archive demandé.

    Le module d'archive cherche ses fichiers à côté du dépôt
    (`RACINE/results/barres`). C'est juste pour une installation normale et faux
    pour un arbre de travail ou une archive déplacée. On lui **injecte** donc
    l'emplacement plutôt que de réimplémenter ici ses garde-fous — borne utile,
    barres reconstruites, schéma — dont une seconde copie finirait par diverger.
    """
    from titanium.data import archive_barres as ab  # noqa: PLC0415

    ab.DOSSIER = Path(archive)
    ab.METADONNEES = Path(archive) / "_metadonnees.json"
    for nom in ("_metadonnees", "_bornes_granularite"):
        cache = getattr(ab, nom, None)
        if hasattr(cache, "cache_clear"):
            cache.cache_clear()
    return ab.charger_barres


def mesurer_symbole(
    symbole: str, *, timeframe: str, archive: Path, duree_min: int, largeur_min: int, charger=None
) -> dict[str, Any]:
    """Construit les séances mesurables d'un symbole depuis l'archive de barres."""
    if charger is None:  # import paresseux : pas de MT5 ici
        charger = chargeur_depuis(archive)
    donnees = charger(symbole, timeframe).sort_index()
    if donnees.empty:
        return {
            "symbole": symbole,
            "seances": [],
            "motif": "archive vide",
            "debut_mode_min": None,
            "jours_ecartes": 0,
        }

    pas_min = _decalages_bougie(donnees.index)
    debut_mode, par_jour = detecter_debuts(donnees, duree_min=duree_min, pas_min=pas_min)
    point = lire_point(archive, symbole)

    minutes = _minutes(donnees.index)
    clotures = donnees["close"].to_numpy(dtype=float)
    spreads = donnees["spread"].to_numpy(dtype=float)
    index_par_jour: dict[Any, list[int]] = {}
    for rang, t in enumerate(donnees.index):
        index_par_jour.setdefault(t.date(), []).append(rang)

    largeur = max(1, largeur_min // pas_min)
    seances: list[Seance] = []
    ecartes = 0
    cloture_veille_prix: float | None = None
    for jour, debut in par_jour:
        positions = [p for p in index_par_jour[jour] if debut <= minutes[p] < debut + duree_min]
        if len(positions) < (duree_min // pas_min) * COUVERTURE_MIN:
            ecartes += 1
            continue
        positions.sort()
        ouverture = positions[:largeur]
        cloture_fenetre = positions[-largeur:]
        milieu = positions[largeur:-largeur]
        if not ouverture or not cloture_fenetre or not milieu:
            ecartes += 1
            continue
        if cloture_veille_prix is None or cloture_veille_prix <= 0:
            # La première séance de l'archive n'a pas de veille : elle sert à
            # établir la référence, elle n'est pas mesurable.
            cloture_veille_prix = clotures[positions[-1]]
            ecartes += 1
            continue
        # Le retour de la DERNIÈRE demi-heure part du prix à l'heure du début de
        # fenêtre, c'est-à-dire de la clôture de la barre qui la précède — pas
        # de la première barre de la fenêtre, qui inclurait cinq minutes de trop.
        rang_debut = positions.index(cloture_fenetre[0])
        if rang_debut == 0:
            ecartes += 1
            continue
        r_ouverture = clotures[ouverture[-1]] / cloture_veille_prix - 1.0
        r_ouverture_session = clotures[ouverture[-1]] / clotures[positions[0]] - 1.0
        r_cloture = clotures[cloture_fenetre[-1]] / clotures[positions[rang_debut - 1]] - 1.0
        r_milieu = (clotures[milieu[-1]] / clotures[milieu[0]] - 1.0) if milieu else 0.0
        cloture_veille_prix = clotures[positions[-1]]
        spread_med = statistics.median([spreads[p] for p in positions])
        prix = clotures[positions[-1]]
        if point is None or prix <= 0:
            spread_bps = float("nan")
        else:
            # UN spread par aller-retour : entrée et sortie au marché, ce qui
            # est le mode d'entrée déclaré du système.
            spread_bps = 1e4 * (spread_med * point) / prix
        seances.append(
            Seance(
                jour=str(jour),
                debut_min=debut,
                r_ouverture=r_ouverture,
                r_ouverture_session=r_ouverture_session,
                r_cloture=r_cloture,
                r_milieu=r_milieu,
                r_cloture_veille=float("nan"),
                amplitude_ouverture=abs(r_ouverture),
                volatilite_passee=float("nan"),
                spread_bps=spread_bps,
                prix_cloture=float(prix),
                barres=len(positions),
            )
        )

    seances.sort(key=lambda s: s.jour)
    # Les deux prédicteurs naïfs qui n'existent qu'en série : ils ont besoin de
    # l'historique, donc ils se remplissent après coup.
    for rang, seance in enumerate(seances):
        if rang >= 1:
            seance.r_cloture_veille = seances[rang - 1].r_cloture
        passees = [s.r_cloture for s in seances[max(0, rang - 5) : rang]]
        seance.volatilite_passee = statistics.pstdev(passees) if len(passees) >= 3 else float("nan")
    return {
        "symbole": symbole,
        "seances": seances,
        "motif": None,
        "debut_mode_min": debut_mode,
        "pas_min": pas_min,
        "point": point,
        "jours_ecartes": ecartes,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Rapport
# ═══════════════════════════════════════════════════════════════════════════════


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def quantile_bilateral(alpha: float) -> float:
    """Quantile de la loi normale centrée réduite, par bissection sur `erf`.

    Écrit ici plutôt qu'importé : une correction du nombre de tests qui dépend
    d'une dépendance absente ne serait appliquée nulle part.
    """
    cible = 1.0 - alpha / 2.0
    bas, haut = 0.0, 10.0
    for _ in range(80):
        milieu = (bas + haut) / 2.0
        if 0.5 * (1.0 + math.erf(milieu / math.sqrt(2.0))) < cible:
            bas = milieu
        else:
            haut = milieu
    return (bas + haut) / 2.0


def seuil_corrige() -> float:
    """Le seuil annoncé **et** appliqué : Bonferroni sur la famille de tests.

    Un seul propriétaire, pour que le chiffre imprimé dans l'en-tête et celui qui
    décide du verdict ne puissent pas être deux nombres différents.
    """
    return quantile_bilateral(0.05 / TESTS_FAMILLE)


def _couts(seances: list[Seance]) -> dict[str, float]:
    valides = [s.spread_bps for s in seances if s.spread_bps == s.spread_bps]
    if not valides:
        return {"median_bps": float("nan"), "p90_bps": float("nan"), "n": 0}
    tri = sorted(valides)
    return {
        "median_bps": statistics.median(valides),
        "p90_bps": tri[min(len(tri) - 1, int(0.9 * len(tri)))],
        "n": len(valides),
    }


def _regression_filtree(brut, cible) -> dict[str, float]:
    """Régression d'un prédicteur sur une cible, lignes incomplètes écartées."""
    couples = [(x, v) for x, v in zip(brut, cible, strict=True) if x == x]
    return regression([c[0] for c in couples], [c[1] for c in couples])


def _regressions(seances: list[Seance]) -> dict[str, dict[str, dict[str, float]]]:
    """Les sept régressions d'un échantillon, **calculées une seule fois**.

    Rendues en données et non en texte : le rapport imprimé et l'artefact JSON
    lisent la même source, donc un chiffre ne peut pas diverger entre les deux.
    Les deux familles sont séparées parce que le rapport les présente avec deux
    mises en forme distinctes.
    """
    y = [s.r_cloture for s in seances]
    ymag = [abs(v) for v in y]
    signes = [
        ("ouverture (H4)", [s.r_ouverture for s in seances]),
        ("ouverture session", [s.r_ouverture_session for s in seances]),
        ("cloture veille", [s.r_cloture_veille for s in seances]),
        ("milieu de seance", [s.r_milieu for s in seances]),
    ]
    # Comparateur naïf de magnitude : la volatilité passée explique-t-elle
    # l'amplitude aussi bien que l'ouverture ? C'est la critique qui a coulé
    # d'autres indicateurs, et elle doit être posée ici aussi.
    # Les étiquettes portent leurs barres verticales ici et non dans le gabarit :
    # elles partent telles quelles dans l'artefact JSON, où le nom doit se lire
    # seul. `bloc_predictions` les rend à l'identique.
    magnitudes = [
        ("|ouverture|", [s.amplitude_ouverture for s in seances]),
        ("|ouv. session|", [abs(s.r_ouverture_session) for s in seances]),
        ("vol. passee", [s.volatilite_passee for s in seances]),
    ]
    return {
        "signes": {nom: _regression_filtree(brut, y) for nom, brut in signes},
        "magnitudes": {nom: _regression_filtree(brut, ymag) for nom, brut in magnitudes},
    }


def _effet(seances: list[Seance], cout_bps: float, seuil_t: float) -> dict[str, Any]:
    """L'écart des deux groupes, son coût et son verdict — en données."""
    e = ecart_conditionnel([s.r_cloture for s in seances], [s.r_ouverture for s in seances])
    e_session = ecart_conditionnel(
        [s.r_cloture for s in seances], [s.r_ouverture_session for s in seances]
    )
    bas = e["ecart_bps"] - 2.0 * e["erreur_type_bps"] - cout_bps
    if e["t"] == e["t"] and abs(e["t"]) < seuil_t:
        verdict = f"INDÉCIS au seuil de famille (|t| < {seuil_t:.2f})."
    elif bas <= 0:
        verdict = "EXPLOITABLE NON PROUVÉ : la borne basse ne couvre pas le coût."
    else:
        verdict = "EXPLOITABLE sur cet échantillon (borne basse > coût)."
    return {
        "ecart_bps": e["ecart_bps"],
        "erreur_type_bps": e["erreur_type_bps"],
        "t": e["t"],
        "n_haut": e["n_haut"],
        "n_bas": e["n_bas"],
        "hors_gap_bps": e_session["ecart_bps"],
        "hors_gap_erreur_bps": e_session["erreur_type_bps"],
        "hors_gap_t": e_session["t"],
        "cout_bps": cout_bps,
        "net_bps": e["ecart_bps"] - cout_bps,
        "borne_basse_bps": bas,
        "verdict": verdict,
    }


def _bloc_predictions(regs: dict[str, dict[str, dict[str, float]]], titre: str) -> list[str]:
    """Rend en texte les régressions déjà calculées. Ne calcule rien."""
    out = [titre]
    out.append(f"  {'predicteur':<18} {'n':>5}  {'R²':>8}  {'pente':>10}  {'t':>7}")
    out.append("  " + "-" * 54)
    for nom, r in regs["signes"].items():
        out.append(
            f"  {nom:<18} {int(r['n']):>5}  {r['r2']:>8.5f}  {r['pente']:>10.4f}  {r['t']:>7.2f}"
        )
    for nom, r in regs["magnitudes"].items():
        out.append(
            f"  |{nom:<16} {int(r['n']):>5}  {r['r2']:>8.5f}  {r['pente']:>10.4f}  {r['t']:>7.2f}"
        )
    out.append("")
    return out


def _bloc_effet(d: dict[str, Any]) -> list[str]:
    """Rend en texte l'effet déjà calculé. Ne calcule rien."""
    return [
        f"  écart des deux groupes  {d['ecart_bps']:+8.3f} bp "
        f"(± {d['erreur_type_bps']:.3f}, t = {d['t']:+.2f}, "
        f"n = {int(d['n_haut'])}/{int(d['n_bas'])})",
        f"  variante hors gap       {d['hors_gap_bps']:+8.3f} bp "
        f"(± {d['hors_gap_erreur_bps']:.3f}, t = {d['hors_gap_t']:+.2f})",
        f"  coût d'aller-retour mesuré  {d['cout_bps']:6.3f} bp",
        f"  NET après coût          {d['net_bps']:+8.3f} bp   "
        f"borne basse à 2σ {d['borne_basse_bps']:+8.3f} bp",
        f"  → {d['verdict']}",
        "",
    ]


def analyser(
    resultats: list[dict[str, Any]], *, part_hors: float, seuil_t: float
) -> list[dict[str, Any]]:
    """Les chiffres d'un symbole, calculés une seule fois et rendus en données.

    C'est le propriétaire unique : `rapport` n'imprime que ce que cette fonction
    rend, et l'artefact JSON n'écrit que ce qu'elle rend.
    """
    lignes: list[dict[str, Any]] = []
    for res in resultats:
        seances: list[Seance] = res.get("seances") or []
        cout = _couts(seances)
        coupe = int(len(seances) * (1.0 - part_hors))
        dedans, dehors = seances[:coupe], seances[coupe:]
        lignes.append(
            {
                "symbole": res["symbole"],
                "motif": res.get("motif"),
                "seances": len(seances),
                "jours_ecartes": res.get("jours_ecartes"),
                "debut_mode_min": res.get("debut_mode_min"),
                "pas_min": res.get("pas_min"),
                "point": res.get("point"),
                "premiere_seance": seances[0].jour if seances else None,
                "derniere_seance": seances[-1].jour if seances else None,
                "barres_mediane": (
                    statistics.median([s.barres for s in seances]) if seances else None
                ),
                "cout": cout,
                "coupe": {"dedans": len(dedans), "dehors": len(dehors)},
                "regressions": {
                    "tout": _regressions(seances),
                    "dedans": _regressions(dedans),
                    "dehors": _regressions(dehors),
                },
                "effet": {
                    "tout": _effet(seances, cout["median_bps"], seuil_t),
                    "dehors": _effet(dehors, cout["median_bps"], seuil_t),
                },
            }
        )
    return lignes


def rapport(
    resultats: list[dict[str, Any]],
    *,
    duree_min: int,
    largeur_min: int,
    part_hors: float,
    tf: str,
    archive: Path,
) -> str:
    out: list[str] = []
    w = out.append
    seuil_t = seuil_corrige()
    w("MOMENTUM INTRADAY — H4 (Gao, Han, Li & Zhou 2018) SUR LES SYMBOLES RETENUS")
    w(f"  archive   {archive}")
    w(
        f"  timeframes {tf} · séance {duree_min} min · fenêtre {largeur_min} min · "
        f"hors échantillon {part_hors:.0%}"
    )
    w(
        f"  famille de {TESTS_FAMILLE} tests : seuil corrigé |t| > {seuil_t:.3f} "
        f"(Bonferroni, α = 0,05/{TESTS_FAMILLE}), et non 1,960"
    )
    w("")

    chiffres = analyser(resultats, part_hors=part_hors, seuil_t=seuil_t)
    for c in chiffres:
        w(f"### {c['symbole']}")
        if c["motif"]:
            w(f"  AUCUNE MESURE — {c['motif']}")
            w("")
            continue
        w(
            f"  séance détectée au volume : début modal {_hhmm(c['debut_mode_min'])} UTC, "
            f"pas {c['pas_min']} min"
        )
        if not c["seances"]:
            w("  AUCUNE SÉANCE MESURABLE — archive absente ou trop courte.")
            w("")
            continue
        w(
            f"  séances mesurables : {c['seances']}  ({c['premiere_seance']} → "
            f"{c['derniere_seance']}) · {c['jours_ecartes']} journée(s) écartée(s) "
            f"pour couverture insuffisante"
        )
        w(f"  barres par séance (médiane) : {c['barres_mediane']:.0f}")
        cout = c["cout"]
        w(
            f"  spread mesuré dans l'archive : médiane {cout['median_bps']:.3f} bp, "
            f"p90 {cout['p90_bps']:.3f} bp sur {cout['n']} séances"
        )
        w("")

        regs = c["regressions"]
        out.extend(_bloc_predictions(regs["tout"], "  TOUT L'ÉCHANTILLON"))
        out.extend(_bloc_predictions(regs["dedans"], "  DEDANS (sert à regarder)"))
        out.extend(_bloc_predictions(regs["dehors"], "  DEHORS (sert à trancher)"))
        w("  EFFET EXPLOITABLE, EN POINTS DE BASE")
        w("  (écart entre retour de clôture moyen après une ouverture haussière et")
        w("   après une ouverture baissière — ce qu'un pari directionnel capturerait)")
        out.extend(_bloc_effet(c["effet"]["tout"]))
        out.extend(_bloc_effet(c["effet"]["dehors"]))

    w("CE QUE CETTE MESURE NE DIT PAS")
    w("  · Elle ne dit rien de la faisabilité : un écart positif et net n'est pas")
    w("    un ordre, et l'outil n'en pose aucun.")
    w("  · Le retour de dernier demi-heure est mesuré sur des barres de clôture :")
    w("    le prix d'exécution réel d'un ordre au marché n'est pas dans l'archive.")
    w("  · La séance est détectée par le volume, pas par un calendrier de place :")
    w("    les jours fériés partiels et les séances écourtées sont écartés par la")
    w("    porte de couverture, jamais complétés.")
    w("  · Un échantillon de séances ne couvre pas tous les régimes ; le nombre de")
    w("    tests est compté et le seuil corrigé, mais aucune conclusion ne dépasse")
    w("    les journées listées ci-dessus.")
    w("  · Aucun seuil n'est déplacé : le plafond de 12,5 %, la boucle et les")
    w("    plafonds de risque restent intacts.")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--symboles", nargs="*", default=list(SYMBOLES_DEFAUT))
    ap.add_argument("--timeframe", default="M5")
    ap.add_argument("--archive", default=str(RACINE / "results" / "barres"))
    ap.add_argument("--duree-seance", type=int, default=DUREE_SEANCE_MIN)
    ap.add_argument("--largeur-fenetre", type=int, default=LARGEUR_FENETRE_MIN)
    ap.add_argument("--part-hors-echantillon", type=float, default=PART_HORS_ECHANTILLON)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    archive = Path(a.archive)
    if not (archive / "_metadonnees.json").exists():
        print(f"archive de barres absente : {archive}")
        print("cet outil ne mesure que ce qui a été archivé ; il n'invente rien.")
        return 1

    resultats = []
    for symbole in a.symboles:
        try:
            resultats.append(
                mesurer_symbole(
                    symbole,
                    timeframe=a.timeframe,
                    archive=archive,
                    duree_min=a.duree_seance,
                    largeur_min=a.largeur_fenetre,
                )
            )
        except FileNotFoundError as exc:
            resultats.append(
                {
                    "symbole": symbole,
                    "seances": [],
                    "motif": str(exc),
                    "debut_mode_min": None,
                    "jours_ecartes": 0,
                }
            )

    if not any(res.get("seances") for res in resultats):
        print("aucune séance mesurable : rien à rapporter.")
        return 1

    texte = rapport(
        resultats,
        duree_min=a.duree_seance,
        largeur_min=a.largeur_fenetre,
        part_hors=a.part_hors_echantillon,
        tf=a.timeframe,
        archive=archive,
    )
    print(texte)
    if a.json:
        # Les mêmes chiffres que le rapport imprimé : `analyser` en est l'unique
        # propriétaire, donc les deux ne peuvent pas diverger.
        Path(a.json).write_text(
            json.dumps(
                {
                    "timeframe": a.timeframe,
                    "rapport": texte,
                    "seuil_t": seuil_corrige(),
                    "symboles": analyser(
                        resultats, part_hors=a.part_hors_echantillon, seuil_t=seuil_corrige()
                    ),
                },
                ensure_ascii=False,
                indent=2,
                default=float,
            ),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
