"""Pôle ÉMOTION — PORTÉ DE V12 le 14/09/2026, sur autorité de Florent.

Source : `v12/poles/emotion/emotion_engine.py`. **La logique de décision est
reprise sans modification** — c'est la méthode de Florent, éprouvée en réel sur
V12, et on ne la relitige pas. Seuls les imports changent.

POURQUOI CE PORT. V14 avait hérité du CONSOMMATEUR sans le PRODUCTEUR. La porte
de confluence lit un bloc `emotion`, et son `EMOTION_MIN_CONFIDENCE = 0.35` est
exactement le `MIN_ACTIONABLE_CONFIDENCE` de ce module — le contrat était donc
bien copié. Mais aucun appel ne fabriquait jamais ce bloc : les deux
`build_feats` de la boucle n'en passent pas, et chaque décision tournait sur
`NEUTRAL_EMOTION` (`filter_block=None`, `confidence=1.0`, `wait=False`).
Vérifié le 14/09 : `BLOCK_EMOTION_SIDE` et `WAIT_EMOTION_TIMING` ne se sont
JAMAIS déclenchés. L'organe existait, était testé, était documenté comme un
étage du verdict, et ne disait rien.

PIRE QUE MUET. V14 lisait l'absence de mesure comme une approbation. Quand
l'émotion est indisponible, V12 rend `stale=True, confidence=0.0, wait=True`
— la porte ATTEND. V14 rendait `confidence=1.0, wait=False` — la porte PASSE.
« Je n'ai rien ressenti » devenait « le marché est calme et j'en suis
certain ». C'est le même faux calme fabriqué que celui trouvé le matin même
dans le module macro, dans un autre organe : l'absence de donnée lue comme une
donnée favorable.

La docstring d'origine de V12 est conservée mot pour mot dans `_DOC_V12`
ci-dessous : elle porte le modèle circumplex de Russell et les arbitrages de
conception (pourquoi deux axes, pourquoi le RSI a été retiré, pourquoi on fade
la capitulation et l'euphorie mais pas la panique active).
"""

from __future__ import annotations

_DOC_V12 = """emotion/emotion_engine.py — Segment ÉMOTION v2 (v12, 13/07/2026).

Modèle CIRCUMPLEX (Russell) : l'émotion de marché sur DEUX axes —
  · VALENCE (direction)  : −100 peur … +100 avidité ;
  · AROUSAL (énergie)    :    0 épuisé/calme … 100 intense/actif.
Un score sur un seul axe confond des états opposés (remarque de Codex :
euphorie agressive vs confiance calme ; PANIQUE active vs CAPITULATION épuisée).
Le 2e axe (énergie) les sépare — et un axe optionnel de MOMENTUM (l'émotion
monte/retombe) fait émerger l'ESPOIR (peur qui reflue) vs l'ANXIÉTÉ.

Émotions de marché dérivées (cycle documenté : capitulation → espoir → optimisme
→ euphorie → complaisance → anxiété → peur → panique) :
  PANIQUE, PEUR, CAPITULATION, ESPOIR, NEUTRE, OPTIMISME, COMPLAISANCE,
  EUPHORIE, ANXIÉTÉ.

Décisions (nuance clé, remarque de Codex) : on ne fade PAS la panique active
(ça peut encore tomber) — on fade la CAPITULATION épuisée (souvent un plancher)
et l'EUPHORIE (souvent un sommet). Filtre : ne pas acheter l'euphorie, ne pas
vendre la capitulation.

⚠️ RSI RETIRÉ (directive Florent : c'est un indicateur MÉCANIQUE, pas une
émotion). L'émotion se lit dans la FOULE : pression, positionnement, énergie,
peur macro, chasse aux stops.

⚠️ Fidélité des données (remarque Florent : latences broker/Binance non
contrôlées) : `source_age_s`/`max_age_s` marquent l'état STALE → confiance
abaissée, jamais présenté comme frais.

Read-only. Aucun branchement dans une décision de trading sans protocole M2.
"""


import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# En-dessous de ce seuil de confiance (ou si données STALE), aucune sortie
# actionnable n'est émise : label/axes restent visibles, mais contrarian_bias et
# filter_block sont neutralisés (red-team Codex : ne jamais agir sur du fragile).
MIN_ACTIONABLE_CONFIDENCE = 0.35

# Labels (émotions de marché dérivées des 2 axes ; cycle complet)
PANIC, FEAR, CAPITULATION, HOPE, NEUTRAL = "PANIQUE", "PEUR", "CAPITULATION", "ESPOIR", "NEUTRE"
OPTIMISM, COMPLACENCY, EUPHORIA, ANXIETY = "OPTIMISME", "COMPLAISANCE", "EUPHORIE", "ANXIÉTÉ"
DESPAIR, THRILL = "DÉSESPOIR", "EXALTATION"   # fond numb (au-delà de la capitulation) / excitation avant l'euphorie


def _clamp(x: float, lo: float, hi: float) -> float:
    if x != x:          # NaN : on le PROPAGE (le rejet des valeurs non finies se
        return x        # fait au point d'agrégation) plutôt que de le borner à hi
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class EmotionSignal:
    """Un « ressenti » de foule. `axis` = 'valence' (−1..+1, peur→avidité) ou
    'arousal' (0..1, énergie). fn(context) → contribution, ou None si donnée absente."""
    name: str
    axis: str
    weight: float
    fn: Callable[[Dict], Optional[float]]
    description: str = ""


@dataclass
class EmotionState:
    available: bool
    valence: float               # −100..+100 (peur↔avidité)
    arousal: float               # 0..100 (énergie/intensité)
    momentum: Optional[float]    # −1..+1 (émotion qui retombe/monte) ou None
    label: str                   # émotion de marché
    confidence: float            # 0..1
    stale: bool                  # données trop vieilles (fidélité marché)
    contrarian_bias: Optional[str]  # "long"|"short"|None (fade des EXTRÊMES épuisés)
    filter_block: Optional[str]  # côté d'entrée à ÉVITER : "long"|"short"|None
    breakdown: Dict[str, float] = field(default_factory=dict)
    reasons: List[str] = field(default_factory=list)


# ── Ressentis de FOULE (aucun indicateur mécanique — pas de RSI) ─────────────

def _v_delta_volume(ctx):        # pression acheteur(+)/vendeur(−)
    dv = ctx.get("delta_volume")
    return None if dv is None else _clamp(float(dv), -1, 1)

def _v_funding(ctx):             # longs surpayés = foule avide (crypto)
    f = ctx.get("funding_rate")
    return None if f is None else _clamp(float(f) / 0.0005, -1, 1)

def _v_long_short(ctx):          # positionnement de la foule
    r = ctx.get("long_short_ratio")
    if r is None or r <= 0:
        return None
    import math
    return _clamp(math.log(float(r)) / math.log(3.0), -1, 1)

def _v_macro_fear(ctx):          # risque macro/news (haut = peur)
    risk = ctx.get("macro_risk")
    return None if risk is None else _clamp(-((float(risk) - 50.0) / 50.0), -1, 1)

def _v_sweep(ctx):               # sweep haut = avidité, sweep bas = peur
    s = ctx.get("liquidity_sweep")
    return None if s is None else _clamp(float(s), -1, 1)

def _a_volatility(ctx):          # pic de volatilité = forte énergie émotionnelle
    z = ctx.get("atr_zscore")
    return None if z is None else _clamp(abs(float(z)) / 3.0, 0, 1)

def _a_volume_surge(ctx):        # afflux de volume = foule active
    v = ctx.get("volume_surge")  # 0..1 (volume vs moyenne, normalisé)
    return None if v is None else _clamp(float(v), 0, 1)

def _a_sweep_energy(ctx):        # une chasse aux stops est un événement à haute énergie
    s = ctx.get("liquidity_sweep")
    return None if s is None else _clamp(abs(float(s)), 0, 1)


REGISTRY: List[EmotionSignal] = [
    EmotionSignal("delta_volume", "valence", 1.3, _v_delta_volume, "Pression acheteur/vendeur"),
    EmotionSignal("funding_bias", "valence", 1.2, _v_funding, "Longs surpayés (crypto)"),
    EmotionSignal("long_short_ratio", "valence", 1.0, _v_long_short, "Positionnement foule"),
    EmotionSignal("macro_fear", "valence", 1.1, _v_macro_fear, "Peur macro/news"),
    EmotionSignal("sweep_valence", "valence", 0.9, _v_sweep, "Direction chasse aux stops"),
    EmotionSignal("volatility", "arousal", 1.2, _a_volatility, "Pic de volatilité"),
    EmotionSignal("volume_surge", "arousal", 1.1, _a_volume_surge, "Afflux de volume"),
    EmotionSignal("sweep_energy", "arousal", 0.9, _a_sweep_energy, "Énergie chasse aux stops"),
]


def register_signal(signal: EmotionSignal) -> None:
    """Ajoute un ressenti (enrichissement au fil des tests). Validé : axe connu,
    poids fini > 0, nom non vide et unique (red-team Codex : pas de doublon/axe
    ni poids invalide qui fausserait l'agrégation)."""
    if not isinstance(signal, EmotionSignal):
        raise TypeError("register_signal attend un EmotionSignal")
    if signal.axis not in ("valence", "arousal"):
        raise ValueError(f"axe invalide: {signal.axis!r}")
    if not (isinstance(signal.weight, (int, float)) and math.isfinite(signal.weight) and signal.weight > 0):
        raise ValueError(f"poids invalide: {signal.weight!r}")
    if not signal.name or any(s.name == signal.name for s in REGISTRY):
        raise ValueError(f"nom manquant ou dupliqué: {signal.name!r}")
    REGISTRY.append(signal)


def _market_emotion(valence: float, arousal: float, momentum: Optional[float]) -> str:
    """Position (valence, arousal[, momentum]) → émotion de marché nommée.
    Cycle complet : DÉSESPOIR → CAPITULATION → ESPOIR → OPTIMISME → EXALTATION →
    EUPHORIE → COMPLAISANCE → ANXIÉTÉ → PEUR → PANIQUE."""
    rising = momentum is not None and momentum > 0.15    # peur qui reflue / avidité qui monte
    falling = momentum is not None and momentum < -0.15
    # ── zone PEUR (valence négative) ────────────────────────────────────────
    if valence <= -40:
        if valence <= -70 and arousal <= 25:
            return DESPAIR                               # fond NUMB, épuisé (au-delà de la capitulation)
        if arousal >= 60:
            return PANIC                                 # peur ACTIVE (peut encore tomber)
        if arousal <= 30:
            return HOPE if rising else CAPITULATION      # épuisé : plancher (ou espoir si reflue)
        return HOPE if rising else FEAR
    # ── zone NEUTRE ─────────────────────────────────────────────────────────
    if valence < 20:
        if rising:
            return HOPE
        if falling:
            return ANXIETY
        return NEUTRAL
    # ── zone AVIDITÉ ────────────────────────────────────────────────────────
    if valence >= 80 and arousal >= 55:
        return EUPHORIA                                  # sommet extrême
    if valence >= 55 and arousal >= 70:
        return THRILL                                    # excitation qui monte (juste avant l'euphorie)
    if valence >= 55:
        return COMPLACENCY if arousal < 40 else OPTIMISM  # confiance calme vs optimisme
    # avidité modérée (20..55)
    if falling:
        return ANXIETY
    return OPTIMISM if arousal >= 40 else COMPLACENCY


def compute_emotion(context: Dict, *, registry: Optional[List[EmotionSignal]] = None) -> EmotionState:
    """Agrège les ressentis disponibles sur 2 axes → EmotionState. Fail-safe
    (signal absent/erreur ignoré ; confiance = part pondérée disponible).
    Fidélité : `source_age_s` > `max_age_s` (défaut 15 s) ⇒ stale, confiance /2."""
    reg = registry if registry is not None else REGISTRY
    momentum = context.get("valence_momentum")
    if momentum is not None:
        try:
            momentum = float(momentum)
        except (TypeError, ValueError):
            momentum = None
        else:
            momentum = _clamp(momentum, -1, 1) if math.isfinite(momentum) else None

    breakdown: Dict[str, float] = {}
    v_num = v_w = a_num = a_w = 0.0
    v_wtot = sum(s.weight for s in reg if s.axis == "valence") or 1.0
    a_wtot = sum(s.weight for s in reg if s.axis == "arousal") or 1.0
    for s in reg:
        try:
            val = s.fn(context)
        except Exception:
            val = None
        # Une contribution non convertible en nombre fini est une donnée ABSENTE,
        # pas une panne : le contrat fail-safe du module d'origine l'omet, elle
        # ne doit pas tuer l'agrégation (sonde du 15/09 : une fn rendant "abc"
        # levait ValueError hors du try et crashait compute_emotion — donc,
        # en production, un signal mal nourri tuait tout l'organe émotion).
        try:
            val = float(val) if val is not None else None
        except (TypeError, ValueError):
            val = None
        # Rejet des valeurs non finies (red-team Codex : NaN → _clamp → +1 = fausse euphorie).
        if val is None or not math.isfinite(val):
            continue
        breakdown[s.name] = round(float(val), 3)
        if s.axis == "valence":
            v_num += s.weight * val
            v_w += s.weight
        else:
            a_num += s.weight * val
            a_w += s.weight

    if v_w == 0.0 and a_w == 0.0:
        return EmotionState(False, 0.0, 0.0, momentum, NEUTRAL, 0.0, False,
                            None, None, {}, ["Aucune donnée d'émotion disponible."])

    valence = round((v_num / v_w) * 100.0, 1) if v_w else 0.0
    arousal = round((a_num / a_w) * 100.0, 1) if a_w else 0.0
    # Un poids négatif est impossible par construction (register_signal valide
    # weight > 0) ; si un registre injecté l'autorisait quand même, une
    # confiance négative lirait « pire qu'aucune donnée » comme un passant au
    # seuil EMOTION_MIN_CONFIDENCE — on borne à 0 (fail-closed).
    confidence = max(0.0, round(((v_w / v_wtot) + (a_w / a_wtot)) / 2.0, 2))

    # Fidélité des données (latences broker/Binance) : stale → confiance abaissée.
    # Âge/max_age non finis ⇒ traités comme STALE (red-team Codex : entrée invalide).
    age = context.get("source_age_s")
    max_age = context.get("max_age_s", 15.0)
    try:
        max_age = float(max_age)
        if not (math.isfinite(max_age) and max_age > 0):
            max_age = 0.0
    except (TypeError, ValueError):
        max_age = 0.0
    if age is None:
        # Pas d'horodatage du producteur = fraîcheur INCONNUE, pas fraîche :
        # l'absence de mesure n'est pas une donnée favorable (le même motif
        # fail-open payé dans macro/risk.py et dans le garde-fou de pertes).
        # Un producteur qui n'estampe pas doit rendre la porte ATTENDANTE,
        # pas passante — cohérent avec tests/test_emotion_contexte.py.
        stale = True
    else:
        try:
            age = float(age)
            stale = (not math.isfinite(age)) or age > max_age
        except (TypeError, ValueError):
            stale = True
    if stale:
        confidence = round(confidence * 0.5, 2)

    label = _market_emotion(valence, arousal, momentum)

    # Décisions : fade UNIQUEMENT les extrêmes ÉPUISÉS (pas la panique active).
    contrarian = None
    if label == EUPHORIA:
        contrarian = "short"          # fade le sommet euphorique
    elif label in (CAPITULATION, DESPAIR):
        contrarian = "long"           # fade le plancher épuisé / le fond de désespoir
    filt = None
    if label in (EUPHORIA, THRILL):
        filt = "long"                 # ne pas ACHETER l'euphorie/l'exaltation (leçon EURUSD)
    elif label in (PANIC, CAPITULATION, DESPAIR):
        filt = "short"                # ne pas VENDRE la panique / le fond

    # Neutralisation des sorties ACTIONNABLES sur données fragiles (red-team Codex :
    # stale/faible confiance abaissaient la confiance mais laissaient un biais exploitable).
    reasons = [f"{k}={v:+.2f}" for k, v in sorted(breakdown.items(), key=lambda kv: -abs(kv[1]))]
    if stale or confidence < MIN_ACTIONABLE_CONFIDENCE:
        contrarian = filt = None
        reasons.append("⚠ non actionnable (stale ou confiance basse)")

    return EmotionState(True, valence, arousal, momentum, label, confidence, stale,
                        contrarian, filt, breakdown, reasons)
