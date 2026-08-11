"""Hiérarchie de balayage : qui mérite d'être regardé souvent, et de près.

Le principe, qui n'est PAS un filtre
-------------------------------------
Aucun actif n'est exclu. Le catalogue entier reste balayé — un actif calme
aujourd'hui peut porter le meilleur setup de demain, et l'écarter
définitivement reviendrait à décider avant de mesurer.

Ce qui change, c'est la **fréquence** et la **finesse** :

    rang A   chaque tour, historique long, entrée affinée
    rang B   un tour sur trois
    rang C   un tour sur dix

Un actif liquide n'est pas « meilleur », il est **plus mesurable** : son
spread mange une part plus faible de la distance de stop, son carnet
absorbe le lot sans glissement, et ses barres portent assez de volume pour
que les détecteurs de structure ne travaillent pas sur du bruit. C'est
précisément là qu'affiner l'entrée rapporte quelque chose.

Ce qui compose le rang
----------------------
1. **Coût relatif** — spread rapporté à l'ATR. Le critère le plus dur :
   sur EURGBP, le spread mangeait 257 % de l'espérance en backtest.
2. **Activité** — volume de ticks. Une barre sans volume ne dit rien.
3. **Edge mesuré** — quand le journal en porte un. Il domine tout le reste
   dès qu'il existe : une mesure vaut mieux qu'un indice de liquidité.
4. **Avis TradingView** — 26 indicateurs agrégés hors de nos données. Un
   avis indépendant, donc informatif ; pondéré faiblement car il ne
   connaît ni notre horizon ni notre gestion.

`FORCES` entre toujours au rang A, quoi qu'en disent les mesures.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent

#: Actifs maintenus au rang A quels que soient les chiffres.
#: BTCUSD y figure parce que Florent le trade personnellement : le bot doit
#: le suivre en permanence pour que sa lecture reste comparable à la sienne.
FORCES = ("BTCUSD",)

RANG_A, RANG_B, RANG_C = "A", "B", "C"

#: Cadence de balayage par rang, en tours de boucle.
CADENCE = {RANG_A: 1, RANG_B: 3, RANG_C: 10}

#: Barres d'historique par rang. Plus d'histoire = structure plus fiable,
#: mais chaque barre coûte du temps MT5 ; on la dépense où elle sert.
BARRES = {RANG_A: 600, RANG_B: 400, RANG_C: 300}

#: Part du rang A dans le catalogue. Un tiers au rang A garde le tour court
#: tout en couvrant largement ce qui est réellement exploitable.
PART_A = 0.22
PART_B = 0.45


@dataclass
class Fiche:
    """Ce qu'on sait d'un actif, et le rang qu'on en tire."""

    symbol: str
    rang: str = RANG_C
    score: float = 0.0
    cout_relatif: float = 0.0     # spread / ATR — plus bas, mieux c'est
    activite: float = 0.0         # volume de ticks moyen
    edge_r: float | None = None   # espérance mesurée, si connue
    edge_n: int = 0
    avis_tv: str = ""             # recommandation TradingView
    classe: str = ""
    forcee: bool = False
    motif: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _cout_relatif(spread_prix: float, atr: float) -> float:
    """Part de l'ATR mangée par un aller-retour de spread.

    Rend une valeur haute (mauvaise) quand l'ATR est inconnu : sans mesure
    de volatilité, on ne peut pas affirmer qu'un actif est bon marché.
    """
    if atr <= 0:
        return 9.9
    return (spread_prix * 2.0) / atr


def _note_cout(c: float) -> float:
    """0..1 — un coût sous 5 % de l'ATR est excellent, au-delà de 60 % nul."""
    if c <= 0.05:
        return 1.0
    if c >= 0.60:
        return 0.0
    return 1.0 - (c - 0.05) / 0.55


def _note_avis(avis: str) -> float:
    """Un avis franc, dans un sens ou dans l'autre, vaut mieux qu'un neutre.

    On note la FRANCHISE, pas la direction : le sens vient de nos portes,
    jamais d'un service extérieur.
    """
    return {"STRONG_BUY": 1.0, "STRONG_SELL": 1.0,
            "BUY": 0.6, "SELL": 0.6, "NEUTRAL": 0.15}.get(avis.upper(), 0.0)


def classer(fiches: list) -> list:
    """Attribue un rang à chaque fiche. Modifie et rend la liste."""
    for f in fiches:
        if f.symbol.upper() in FORCES:
            f.forcee = True

        note_c = _note_cout(f.cout_relatif)
        note_a = min(1.0, f.activite / 4000.0) if f.activite > 0 else 0.0
        note_tv = _note_avis(f.avis_tv)

        # L'edge mesuré DOMINE dès qu'il existe : un indice de liquidité est
        # une conjecture, une espérance sur 20 trades est un fait.
        if f.edge_r is not None and f.edge_n >= 20:
            f.score = 0.60 * max(0.0, min(1.0, (f.edge_r + 0.3) / 0.8)) \
                + 0.25 * note_c + 0.15 * note_a
            f.motif = f"edge mesuré {f.edge_r:+.3f} R sur {f.edge_n}"
        else:
            f.score = 0.55 * note_c + 0.30 * note_a + 0.15 * note_tv
            f.motif = (f"coût {f.cout_relatif:.0%} de l'ATR"
                       + (f" · TV {f.avis_tv}" if f.avis_tv else ""))

    ordonnees = sorted(fiches, key=lambda x: (-x.forcee, -x.score))
    n = len(ordonnees)
    fin_a = max(1, int(n * PART_A))
    fin_b = max(fin_a + 1, int(n * PART_B))

    for i, f in enumerate(ordonnees):
        if f.forcee:
            f.rang = RANG_A
            f.motif = "suivi permanent (traité manuellement)"
        elif i < fin_a:
            f.rang = RANG_A
        elif i < fin_b:
            f.rang = RANG_B
        else:
            f.rang = RANG_C
    return ordonnees


def ecrire(fiches: list, chemin: Path) -> Path:
    """Écrit la sélection, de façon atomique."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    charge = {
        "etabli_le": datetime.now(timezone.utc).isoformat(),
        "forces": list(FORCES),
        "cadence": CADENCE,
        "barres": BARRES,
        "rangs": {r: sum(1 for f in fiches if f.rang == r)
                  for r in (RANG_A, RANG_B, RANG_C)},
        "actifs": [f.to_dict() for f in fiches],
    }
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(charge, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(chemin)
    return chemin


def lire(chemin: Path) -> dict:
    """Sélection en vigueur, ou {} si absente. Ne lève jamais."""
    try:
        p = Path(chemin)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def a_balayer(selection: dict, tour: int, catalogue: list) -> list:
    """Actifs à examiner à ce tour, selon leur rang.

    Sans sélection, tout le catalogue est rendu : l'absence de hiérarchie ne
    doit pas se traduire par une absence de balayage. Le défaut sûr, c'est
    de regarder plus, pas moins.
    """
    if not selection or not selection.get("actifs"):
        return list(catalogue)

    cadence = selection.get("cadence") or CADENCE
    rang_de = {a["symbol"]: a.get("rang", RANG_C)
               for a in selection["actifs"]}

    out = []
    for sym in catalogue:
        r = rang_de.get(sym, RANG_C)
        if tour % int(cadence.get(r, 10) or 10) == 0:
            out.append(sym)
    # Les forcés ne manquent jamais un tour, même si l'arithmétique les
    # écarterait : c'est le sens du mot « forcé ».
    for sym in selection.get("forces", []):
        if sym in catalogue and sym not in out:
            out.insert(0, sym)
    return out


def barres_pour(selection: dict, symbole: str, defaut: int = 400) -> int:
    """Profondeur d'historique à lire pour cet actif.

    C'est ici que « affiner la précision » se concrétise : un actif de
    rang A obtient 600 barres au lieu de 300, donc des niveaux de structure
    calculés sur une histoire plus longue et moins sensibles au bruit.
    """
    if not selection:
        return defaut
    barres = selection.get("barres") or BARRES
    for a in selection.get("actifs", []):
        if a["symbol"] == symbole:
            return int(barres.get(a.get("rang", RANG_C), defaut))
    return defaut
