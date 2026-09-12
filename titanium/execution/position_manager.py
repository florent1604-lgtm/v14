"""Gestion dynamique des positions — breakeven puis trailing.

Porté de V12 (`execution/demo_position_manager.py`). **La logique de décision
est inchangée** : elle vient d'un post-mortem chiffré, pas d'une intuition.

    23 % des pertes de V12 avaient atteint +0.8 R en leur faveur AVANT de
    repartir au stop. Un SL figé les a laissées revenir en perte.

D'où deux mécanismes :

* **BREAKEVEN** — dès `+breakeven_r` en faveur, le SL remonte à l'entrée plus un
  tampon de coûts : une perte potentielle devient ~0.
* **TRAILING** — au-delà de `+trail_start_r`, le SL suit le plus-haut favorable
  à `trail_dist_r` derrière. On sécurise le gain même si un TP lointain n'est
  jamais touché.

CE QUI CHANGE PAR RAPPORT À V12
-------------------------------
V12 mêle décision et appels MT5 dans une boucle de 140 lignes : impossible à
tester sans terminal ouvert et sans position réelle. Ici la décision est une
**fonction pure** (`decide_new_sl`) et l'I/O est une coquille mince autour. Les
règles de sécurité se testent donc exhaustivement, hors ligne.

INVARIANTS DE SÉCURITÉ (conservés tels quels)
---------------------------------------------
* le SL ne bouge **jamais** dans le sens défavorable — on n'élargit pas le risque ;
* la distance de stop minimale du courtier est respectée (sinon rejet 10016) ;
* seules **nos** positions sont touchées (magic, ou commentaire) ;
* le TP n'est jamais modifié ;
* une position en erreur n'interrompt pas la boucle ;
* le mur démo↔réel s'applique, comme à l'exécution.
"""

from __future__ import annotations

import contextlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from titanium.data.mt5_vendor import decalage_serveur, heure_serveur_en_utc
from titanium.edge import PNL_R_MAX
from titanium.execution.micro_basket import (
    BasketMember,
    decide_basket_exit,
    load_basket_peaks,
    save_basket_peaks,
)
from titanium.execution.mt5_executor import (
    ExecutionPolicy,
    ExecutionRefused,
    _pick_filling_mode,
    assert_can_trade,
)
from titanium.execution.weekend_flat import (
    WeekendFlatDecision,
    WeekendFlatParams,
    decide_weekend_flat,
    heure_serveur_mt5,
    marche_cote,
)

PHASE_INIT = "init"
PHASE_BREAKEVEN = "breakeven"
PHASE_TRAILING = "trailing"

# Tampon de breakeven quand le spread est inconnu : 5 % du R initial. Placer le
# SL exactement à l'entrée le ferait toucher par le spread seul.
_BUFFER_R_FRACTION = 0.05


@dataclass(frozen=True)
class ManageParams:
    """Seuils de gestion, en multiples du R initial."""
    breakeven_r: float = 0.8
    trail_start_r: float = 1.2
    trail_dist_r: float = 0.8
    # Sortie active sans toucher au SL : le plancher est un cliquet propre a
    # chaque position, calcule depuis SON meilleur niveau observe.
    exit_arm_r: float = 0.8
    exit_min_lock_r: float = 0.15
    exit_max_giveback_r: float = 0.60
    exit_min_retention: float = 0.35

    @classmethod
    def from_config(cls, config: dict | None = None) -> ManageParams:
        if config is None:
            from tradingagents.default_config import DEFAULT_CONFIG
            config = DEFAULT_CONFIG

        def _f(cle, defaut):
            try:
                v = float(config.get(cle, defaut))
                return v if math.isfinite(v) else defaut
            except (TypeError, ValueError):
                return defaut

        return cls(
            breakeven_r=_f("manage_breakeven_r", 0.8),
            trail_start_r=_f("manage_trail_start_r", 1.2),
            trail_dist_r=_f("manage_trail_dist_r", 0.8),
            exit_arm_r=_f("manage_exit_arm_r", 0.8),
            exit_min_lock_r=_f("manage_exit_min_lock_r", 0.15),
            exit_max_giveback_r=_f("manage_exit_max_giveback_r", 0.60),
            exit_min_retention=_f("manage_exit_min_retention", 0.35),
        )


@dataclass(frozen=True)
class PositionSnapshot:
    """Ce qu'il faut savoir d'une position pour décider. Rien de plus."""
    ticket: str
    symbol: str
    side: int              # +1 long, −1 short
    entry: float
    current: float
    sl: float | None
    tp: float | None
    digits: int = 5
    min_stop_distance: float = 0.0
    spread: float = 0.0
    volume: float = 0.0


@dataclass
class TrackedState:
    """Ce qu'on retient d'une position entre deux passages.

    ``r`` est figé à la PREMIÈRE observation, quand le SL est encore celui
    d'origine. Le recalculer après un déplacement de SL ferait dériver toutes
    les mesures en R — c'est l'erreur qui rend un journal d'excursions inutile.

    LES CHAMPS DE CONTEXTE NE SONT PAS DÉCORATIFS
    ----------------------------------------------
    ``entry``, ``context_key`` et ``indicators`` sont capturés à l'OUVERTURE
    parce que la clôture est le seul instant où résultat et contexte d'entrée
    coexistent — et à ce moment-là, MT5 ne voit déjà plus la position. Sans
    eux, un trade clos ne peut être rattaché à aucun contexte&nbsp;: la mesure
    d'edge et l'analyse discriminante restent aveugles pour toujours.
    L'historique de perception n'est pas reconstituable après coup.
    """
    r: float
    phase: str = PHASE_INIT
    peak_fav_r: float = 0.0
    symbol: str = ""
    side: int = 0

    # ── Contexte d'entrée, figé à l'ouverture.
    entry: float = 0.0
    sl_initial: float = 0.0
    tp_initial: float = 0.0
    context_key: str = ""
    # Verdict du RiskGate figé à l'entrée. Il ne se confond pas avec la
    # famille ``reversal`` : seul ce drapeau identifie exactement la cohorte
    # ouverte grâce à la levée de l'anti-fade.
    contre_tendance: bool = False
    indicators: dict = field(default_factory=dict)
    ts_open: str = ""
    mae_r: float = 0.0            # pire excursion observée (≤ 0)
    # Risque engagé en devise, figé à l'ouverture. Sert à convertir en R les
    # frais que MT5 rend en devise (commission, swap). Zéro = inconnu ; le
    # journal conserve alors cost_r=None plutôt que de prétendre « gratuit ».
    risque_devise: float = 0.0
    # Spread aller-retour rapporté au R, estimé juste avant l'ordre.
    # None = inconnu ; 0.0 = mesure réellement nulle.
    spread_r: float | None = None
    # True uniquement si le spread provient des fills réels. Le budget utilise
    # aujourd'hui une estimation pré-ordre : utile, mais pas « exacte ».
    spread_exact: bool = False
    # ── Stratification, figée à l'ouverture. Additifs : `r`, `phase`,
    #    `peak_fav_r` et `side` restent intacts — le moteur breakeven/trailing
    #    en dépend et les perdre le rendrait inopérant.
    mode: str = "explore"
    quorum: int = 0
    support_pillars: int = 0
    asset_class: str = ""
    account: str = ""
    timeframe: str = ""
    #: Qui a fourni le pilier G5 : "formes", "displacement" ou "" (aucun).
    #: Ajoute le 12/08/2026 en meme temps que le secours displacement. Sans ce
    #: champ ici, tools/live_demo.py le passait a un constructeur qui ne
    #: l acceptait pas : le TypeError etait avale par un `except` d observabilite
    #: et TOUT le contexte d ouverture etait perdu silencieusement.
    candle_source: str = ""
    # Identité de politique figée à la décision. Mesure seule : aucune porte,
    # aucun sizing et aucune gestion de stop ne lit ces champs.
    entry_policy: str = ""
    policy_epoch: str = ""
    config_sha256: str = ""
    code_sha256: str = ""
    decision_id: str = ""
    # Provenance d'une entree passive. Ces champs restent vides pour les
    # positions historiques ou ouvertes au marche. Ils rendent possible le
    # rapprochement causal ordre limite -> fill -> cloture, sans reconstruire
    # le prix de reference apres coup.
    limit_order_ticket: int = 0
    limit_planned_price: float = 0.0
    limit_market_reference_price: float = 0.0
    limit_target_saving_r: float | None = None
    limit_realized_saving_r: float | None = None
    limit_slippage_r: float | None = None
    # Une disparition de positions_get ne suffit pas à prouver la clôture.
    # Ces champs persistent le retry jusqu'à confirmation ou escalade bornée.
    history_missing_since: str = ""
    history_missing_attempts: int = 0
    # ── Instrumentation en avant (Prime, 18/08/2026), additive et neutre :
    #    aucune porte ni décision de sortie ne lit ces champs.
    #
    # Niveaux structurels vus par le builder À LA DÉCISION D'ENTRÉE — sr_level,
    # bornes de l'ote_zone, fvg_open, vpoc — plus la distance entrée→niveau en
    # R, calculée une fois pour toutes à l'ouverture. Sans cette capture, ces
    # niveaux ne sont visibles nulle part dans le trade clos : ils existent
    # dans `_trace` au moment du calcul mais ne survivent pas à la clôture, où
    # MT5 ne les recalculera jamais pour une barre déjà passée.
    entry_levels: dict = field(default_factory=dict)
    # ATR figé à l'ouverture, en unité de prix. Sert à exprimer l'excursion en
    # ATR plutôt qu'en R : R dépend du stop choisi, l'ATR non — les deux
    # mesures répondent à des questions différentes.
    entry_atr: float = 0.0
    # Excursion à horizon FIXE (barres écoulées depuis l'ouverture, pas le
    # nombre de tours de boucle) : {"1": {...}, "4": {...}, "12": {...}}.
    # Capturée une seule fois par horizon, dès qu'il est atteint ou dépassé —
    # jamais réécrite ensuite. Sans elle, la MAE terminale reste circulaire
    # (un stop touché vaut -1R par construction) et ne dit rien de l'entrée.
    horizon_excursions: dict = field(default_factory=dict)
    # Verdict cognitif asynchrone. Deux références GLM distinctes et fraîches
    # sont requises avant qu'une sortie de peur puisse être envisagée.
    sentiment_ref: str = ""
    sentiment_state: str = "UNKNOWN"
    sentiment_confidence: float = 0.0
    fear_streak: int = 0
    fear_exit_sent_ref: str = ""

    def to_dict(self) -> dict:
        return {"r": self.r, "phase": self.phase, "peak_fav_r": self.peak_fav_r,
                "symbol": self.symbol, "side": self.side,
                "entry": self.entry, "sl_initial": self.sl_initial,
                "tp_initial": self.tp_initial, "context_key": self.context_key,
                "contre_tendance": self.contre_tendance,
                "indicators": self.indicators, "ts_open": self.ts_open,
                "mae_r": self.mae_r, "risque_devise": self.risque_devise,
                "spread_r": self.spread_r, "spread_exact": self.spread_exact,
                "mode": self.mode, "quorum": self.quorum,
                "support_pillars": self.support_pillars,
                "asset_class": self.asset_class, "account": self.account,
                "timeframe": self.timeframe,
                "candle_source": self.candle_source,
                "entry_policy": self.entry_policy,
                "policy_epoch": self.policy_epoch,
                "config_sha256": self.config_sha256,
                "code_sha256": self.code_sha256,
                "decision_id": self.decision_id,
                "limit_order_ticket": self.limit_order_ticket,
                "limit_planned_price": self.limit_planned_price,
                "limit_market_reference_price": self.limit_market_reference_price,
                "limit_target_saving_r": self.limit_target_saving_r,
                "limit_realized_saving_r": self.limit_realized_saving_r,
                "limit_slippage_r": self.limit_slippage_r,
                "history_missing_since": self.history_missing_since,
                "history_missing_attempts": self.history_missing_attempts,
                "entry_levels": self.entry_levels, "entry_atr": self.entry_atr,
                "horizon_excursions": self.horizon_excursions,
                "sentiment_ref": self.sentiment_ref,
                "sentiment_state": self.sentiment_state,
                "sentiment_confidence": self.sentiment_confidence,
                "fear_streak": self.fear_streak,
                "fear_exit_sent_ref": self.fear_exit_sent_ref}

    @classmethod
    def from_dict(cls, d: dict) -> TrackedState:
        """Relit un état. **Tolérant aux états anciens** : un fichier écrit
        avant l'ajout du contexte se relit sans erreur, avec des champs vides —
        le gestionnaire doit survivre à une mise à jour du code alors que des
        positions sont ouvertes."""
        return cls(
            r=float(d["r"]), phase=str(d.get("phase", PHASE_INIT)),
            peak_fav_r=float(d.get("peak_fav_r", 0.0)),
            symbol=str(d.get("symbol", "")), side=int(d.get("side", 0)),
            entry=float(d.get("entry", 0.0) or 0.0),
            sl_initial=float(d.get("sl_initial", 0.0) or 0.0),
            tp_initial=float(d.get("tp_initial", 0.0) or 0.0),
            context_key=str(d.get("context_key", "")),
            contre_tendance=d.get("contre_tendance", False) is True,
            indicators=dict(d.get("indicators") or {}),
            ts_open=str(d.get("ts_open", "")),
            mae_r=float(d.get("mae_r", 0.0) or 0.0),
            risque_devise=float(d.get("risque_devise", 0.0) or 0.0),
            spread_r=(None if d.get("spread_r") is None
                      else float(d.get("spread_r"))),
            spread_exact=bool(d.get("spread_exact", False)),
            mode=str(d.get("mode", "explore")),
            quorum=int(d.get("quorum", 0) or 0),
            support_pillars=int(d.get("support_pillars", 0) or 0),
            asset_class=str(d.get("asset_class", "")),
            account=str(d.get("account", "")),
            timeframe=str(d.get("timeframe", "")),
            candle_source=str(d.get("candle_source", "") or ""),
            entry_policy=str(d.get("entry_policy", "") or ""),
            policy_epoch=str(d.get("policy_epoch", "") or ""),
            config_sha256=str(d.get("config_sha256", "") or ""),
            code_sha256=str(d.get("code_sha256", "") or ""),
            decision_id=str(d.get("decision_id", "") or ""),
            limit_order_ticket=int(d.get("limit_order_ticket", 0) or 0),
            limit_planned_price=float(d.get("limit_planned_price", 0.0) or 0.0),
            limit_market_reference_price=float(
                d.get("limit_market_reference_price", 0.0) or 0.0),
            limit_target_saving_r=(
                None if d.get("limit_target_saving_r") is None
                else float(d.get("limit_target_saving_r"))
            ),
            limit_realized_saving_r=(
                None if d.get("limit_realized_saving_r") is None
                else float(d.get("limit_realized_saving_r"))
            ),
            limit_slippage_r=(
                None if d.get("limit_slippage_r") is None
                else float(d.get("limit_slippage_r"))
            ),
            history_missing_since=str(d.get("history_missing_since", "") or ""),
            history_missing_attempts=int(d.get("history_missing_attempts", 0) or 0),
            entry_levels=dict(d.get("entry_levels") or {}),
            entry_atr=float(d.get("entry_atr", 0.0) or 0.0),
            horizon_excursions=dict(d.get("horizon_excursions") or {}),
            sentiment_ref=str(d.get("sentiment_ref", "") or ""),
            sentiment_state=str(d.get("sentiment_state", "UNKNOWN") or "UNKNOWN"),
            sentiment_confidence=float(d.get("sentiment_confidence", 0.0) or 0.0),
            fear_streak=int(d.get("fear_streak", 0) or 0),
            fear_exit_sent_ref=str(d.get("fear_exit_sent_ref", "") or ""),
        )


@dataclass
class SlDecision:
    """Verdict pour une position. ``new_sl is None`` = ne rien faire."""
    new_sl: float | None = None
    phase: str = PHASE_INIT
    fav_r: float = 0.0
    peak_fav_r: float = 0.0
    reason: str = ""
    checks: list[str] = field(default_factory=list)


@dataclass
class ExitDecision:
    """Verdict de sortie active. Aucun prix de sortie statique n'est stocke."""
    should_exit: bool = False
    fav_r: float = 0.0
    peak_fav_r: float = 0.0
    floor_r: float = 0.0
    giveback_r: float = 0.0
    reason: str = ""


#: Minutes par barre de la timeframe d'entrée — pour convertir un horizon EN
#: BARRES (+1, +4, +12) en un seuil de temps écoulé. Défaut M15 si la
#: timeframe est absente ou inconnue : c'est la valeur par défaut de la boucle
#: (``tools/live_demo.py:LTF``).
_MINUTES_PAR_TIMEFRAME = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440,
}

#: Horizons instrumentés, en barres de la timeframe d'entrée. Additif et figé
#: à ces trois valeurs : en ajouter ne casse rien, en retirer perdrait des
#: lignes déjà écrites — donc on n'y touche pas au fil de l'eau.
_HORIZONS_BARRES = (1, 4, 12)


def _maj_horizons(state: TrackedState, fav_r: float, now: datetime) -> None:
    """Capture MFE/MAE à horizon fixe, une seule fois par horizon.

    Additif et sans effet sur la décision de stop : appelé APRÈS que
    ``peak_fav_r``/``mae_r`` sont à jour, il ne fait que recopier leur valeur
    du moment dans ``horizon_excursions`` la première fois que l'horizon est
    atteint ou dépassé. Ne lève jamais — un ``ts_open`` illisible laisse
    simplement l'horizon non capturé pour ce tour, à retenter au suivant.
    """
    if not state.ts_open:
        return
    try:
        ouvert = datetime.fromisoformat(state.ts_open.replace("Z", "+00:00"))
        if ouvert.tzinfo is None:
            ouvert = ouvert.replace(tzinfo=timezone.utc)
        ecoule_min = (now - ouvert).total_seconds() / 60.0
    except (ValueError, TypeError):
        return
    if not math.isfinite(ecoule_min) or ecoule_min < 0:
        return
    minutes_barre = _MINUTES_PAR_TIMEFRAME.get(state.timeframe, 15)
    for barres in _HORIZONS_BARRES:
        cle = str(barres)
        if cle in state.horizon_excursions:
            continue
        if ecoule_min < barres * minutes_barre:
            continue
        capture = {
            "mfe_r": round(state.peak_fav_r, 4),
            "mae_r": round(state.mae_r, 4),
            "elapsed_min": round(ecoule_min, 2),
            "captured_at": now.isoformat(),
        }
        if state.entry_atr and math.isfinite(state.entry_atr) and state.entry_atr > 0:
            facteur = state.r / state.entry_atr
            capture["mfe_atr"] = round(state.peak_fav_r * facteur, 4)
            capture["mae_atr"] = round(state.mae_r * facteur, 4)
        else:
            capture["mfe_atr"] = None
            capture["mae_atr"] = None
        state.horizon_excursions[cle] = capture


def _mettre_a_jour_excursion(
        pos: PositionSnapshot, state: TrackedState, *,
        now: datetime | None = None) -> tuple[float | None, str]:
    """Met à jour la mémoire de trajectoire d'un ticket, sans ordre MT5.

    Cette mesure tourne même lorsque le trailing est coupé. Le moteur connaît
    ainsi le meilleur niveau réellement atteint par chaque position et la part
    de cet avantage qui a ensuite été restituée.
    """
    if state.r <= 0 or not math.isfinite(state.r):
        return None, "R_INVALIDE"
    if pos.side not in (-1, 1):
        return None, "SIDE_INVALIDE"
    if not all(math.isfinite(v) for v in (pos.entry, pos.current)):
        return None, "PRIX_INVALIDE"

    fav_r = (pos.current - pos.entry) / state.r * pos.side
    state.peak_fav_r = max(state.peak_fav_r, fav_r)
    state.mae_r = min(state.mae_r, fav_r)
    with contextlib.suppress(Exception):
        _maj_horizons(state, fav_r, now or datetime.now(timezone.utc))
    return fav_r, "OK"


def decide_adaptive_exit(
        pos: PositionSnapshot, state: TrackedState, params: ManageParams, *,
        now: datetime | None = None) -> ExitDecision:
    """Décide une sortie active depuis la trajectoire propre à la position.

    Le plancher est le maximum de trois protections : gain minimal conservé,
    part du pic conservée, et restitution maximale depuis le pic. Il monte
    donc avec chaque nouveau sommet et ne dépend ni d'un TP fixe ni du prix
    nominal de l'actif.
    """
    d = ExitDecision()
    fav_r, reason = _mettre_a_jour_excursion(pos, state, now=now)
    if fav_r is None:
        d.reason = reason
        return d

    d.fav_r = fav_r
    d.peak_fav_r = state.peak_fav_r
    d.giveback_r = max(0.0, state.peak_fav_r - fav_r)

    valeurs = (
        params.exit_arm_r,
        params.exit_min_lock_r,
        params.exit_max_giveback_r,
        params.exit_min_retention,
    )
    if (not all(math.isfinite(v) for v in valeurs)
            or params.exit_arm_r < 0
            or params.exit_min_lock_r < 0
            or params.exit_max_giveback_r <= 0
            or not 0 <= params.exit_min_retention <= 1):
        d.reason = "PARAMS_SORTIE_INVALIDES"
        return d

    if state.peak_fav_r < params.exit_arm_r:
        d.reason = "ATTENTE_ARMEMENT"
        return d

    d.floor_r = max(
        params.exit_min_lock_r,
        state.peak_fav_r * params.exit_min_retention,
        state.peak_fav_r - params.exit_max_giveback_r,
    )
    if fav_r <= d.floor_r:
        d.should_exit = True
        d.reason = "AVANTAGE_RESTITUE"
    else:
        d.reason = "AVANTAGE_CONSERVE"
    return d


def decide_new_sl(pos: PositionSnapshot, state: TrackedState,
                  params: ManageParams, *,
                  now: datetime | None = None,
                  allow_trailing: bool = True) -> SlDecision:
    """Décide du nouveau stop. **Fonction pure** : aucun appel MT5, aucune I/O.

    Met à jour ``state.peak_fav_r`` et ``state.phase`` (le suivi du pic est un
    cliquet : il ne redescend jamais, sinon le trailing rendrait du gain).

    ``now`` — horloge injectable pour les tests ; défaut ``datetime.now(utc)``.
    Sert uniquement à l'instrumentation d'excursion à horizon fixe
    (``state.horizon_excursions``), jamais à la décision de stop elle-même :
    la logique de breakeven/trailing ci-dessous est inchangée bit à bit.
    """
    d = SlDecision(phase=state.phase)

    fav_r, reason = _mettre_a_jour_excursion(pos, state, now=now)
    if fav_r is None:
        d.reason = reason
        return d
    d.fav_r, d.peak_fav_r = fav_r, state.peak_fav_r

    candidat: float | None = None

    # 1) BREAKEVEN — une seule fois, tant qu'on n'est pas déjà plus loin.
    if state.phase == PHASE_INIT and fav_r >= params.breakeven_r:
        tampon = max(pos.spread, _BUFFER_R_FRACTION * state.r)
        candidat = pos.entry + pos.side * tampon
        state.phase = PHASE_BREAKEVEN
        d.checks.append(f"breakeven atteint (+{fav_r:.2f}R)")

    # 2) TRAILING — suit le PIC, pas le prix courant. Cliquet.
    if allow_trailing and state.peak_fav_r >= params.trail_start_r:
        trail = pos.entry + pos.side * (state.peak_fav_r - params.trail_dist_r) * state.r
        if candidat is None or pos.side * (trail - candidat) > 0:
            candidat = trail
        state.phase = PHASE_TRAILING
        d.checks.append(f"trailing actif (pic +{state.peak_fav_r:.2f}R)")

    d.phase = state.phase

    if candidat is None:
        d.reason = "RIEN_A_FAIRE"
        return d

    # ── Garde-fous. Aucun n'est négociable.
    # a) Ne JAMAIS élargir le risque.
    if pos.sl is not None and pos.side * (candidat - pos.sl) <= 0:
        d.reason = "PAS_D_AMELIORATION"
        d.checks.append(f"candidat {candidat:.5f} n'améliore pas le SL {pos.sl:.5f}")
        return d

    # b) Respecter la distance minimale du courtier, sinon rejet 10016.
    if pos.side * (pos.current - candidat) < pos.min_stop_distance:
        d.reason = "TROP_PRES_DU_PRIX"
        d.checks.append(f"distance {abs(pos.current - candidat):.5f} "
                        f"< minimum {pos.min_stop_distance:.5f}")
        return d

    # c) Un stop du mauvais côté du prix fermerait la position sur-le-champ.
    if pos.side * (pos.current - candidat) <= 0:
        d.reason = "SL_DU_MAUVAIS_COTE"
        return d

    d.new_sl = round(candidat, pos.digits)
    d.reason = "DEPLACER"
    return d


# ─────────────────────────── persistance de l'état ──────────────────────────

#: Incidents de lecture de l'état suivi, depuis le démarrage du processus.
#:
#: `load_state` ne peut pas lever — cinq appelants en dépendent et une position
#: vivante ne doit jamais être bloquée par un fichier abîmé. Le signal passe
#: donc PAR CE CANAL, hors du retour de la fonction : le battement le publie,
#: le tableau de bord le montre, et l'incident cesse d'être silencieux.
_INCIDENTS_ETAT: list[dict] = []

#: Nombre d'incidents conservés en mémoire. Au-delà, les plus anciens sortent :
#: un fichier durablement illisible ne doit pas faire enfler le processus.
_MAX_INCIDENTS = 50


def incidents_etat() -> list[dict]:
    """Copie des incidents de lecture d'état. Vide = tout va bien."""
    return [dict(i) for i in _INCIDENTS_ETAT]


def _consigner_incident(chemin: Path, genre: str, detail: str, **extra) -> dict:
    """Enregistre un incident en mémoire ET sur disque. Ne lève jamais."""
    inc = {
        "at": datetime.now(timezone.utc).isoformat(),
        "genre": genre,
        "chemin": str(chemin),
        "detail": detail[:300],
        **extra,
    }
    _INCIDENTS_ETAT.append(inc)
    del _INCIDENTS_ETAT[:-_MAX_INCIDENTS]
    try:
        jrn = Path(chemin).parent / "etat_incidents.ndjson"
        jrn.parent.mkdir(parents=True, exist_ok=True)
        with jrn.open("a", encoding="utf-8") as f:
            f.write(json.dumps(inc, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — journaliser ne casse jamais la lecture
        pass
    return inc


def _sauvegarder_avant_ecrasement(chemin: Path) -> str:
    """Copie horodatée d'un fichier d'état abîmé. Rend le chemin, ou "".

    Sans cette copie, la séquence est fatale : le fichier est illisible, on
    repart d'un état vide, le tour suivant réécrit `positions.json` — et la
    seule trace du contexte des positions vivantes est écrasée. L'horodatage
    évite qu'un second incident détruise la preuve du premier.
    """
    src = Path(chemin)
    try:
        if not src.exists():
            return ""
        # Horodatage à la MILLISECONDE, et suffixe numérique en dernier
        # recours. Une marque à la seconde suffisait à ce que deux incidents
        # rapprochés portent le même nom : la seconde copie écrasait la
        # première, donc la sauvegarde ne sauvegardait rien dans le cas
        # exact — deux échecs coup sur coup — où elle sert le plus.
        marque = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")[:-3]
        dest = src.with_suffix(src.suffix + f".bak-{marque}")
        n = 1
        while dest.exists():
            dest = src.with_suffix(src.suffix + f".bak-{marque}-{n}")
            n += 1
        dest.write_bytes(src.read_bytes())
        return str(dest)
    except Exception:  # noqa: BLE001
        return ""


def load_state(chemin: Path) -> dict[str, TrackedState]:
    """Charge l'état suivi. Un fichier illisible n'empêche jamais de trader.

    CE QUI EST BRUYANT, ET POURQUOI
    --------------------------------
    Rendre `{}` en silence sur un fichier abîmé est le pire moment pour se
    taire : après un arrêt brutal, les positions vivantes sont réadoptées sans
    `context_key`, leurs clôtures partent en quarantaine `CONTEXTE_INCONNU`, et
    la donnée d'edge est perdue définitivement — l'historique de perception ne
    se reconstitue pas après coup.

    Trois cas, désormais distingués :

    * **fichier absent** — normal au premier démarrage, aucun incident ;
    * **fichier illisible** — anomalie : copie de sauvegarde, incident consigné,
      état vide rendu ;
    * **entrées partiellement illisibles** — on garde ce qui se lit. Perdre
      quatre positions parce que la cinquième est malformée serait une perte
      auto-infligée : `from_dict` exige `d["r"]`, et une seule `KeyError`
      emportait tout le dictionnaire.
    """
    p = Path(chemin)
    if not p.exists():
        return {}                      # premier démarrage : rien à signaler

    try:
        brut = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        _consigner_incident(
            p, "illisible", f"{type(exc).__name__}: {exc}",
            sauvegarde=_sauvegarder_avant_ecrasement(p),
            entrees_lues=0, entrees_perdues=0)
        return {}

    if not isinstance(brut, dict):
        _consigner_incident(
            p, "illisible", f"racine de type {type(brut).__name__}, dict attendu",
            sauvegarde=_sauvegarder_avant_ecrasement(p),
            entrees_lues=0, entrees_perdues=0)
        return {}

    etat: dict[str, TrackedState] = {}
    perdues: list[str] = []
    for cle, valeur in brut.items():
        try:
            etat[str(cle)] = TrackedState.from_dict(valeur)
        except Exception as exc:  # noqa: BLE001
            perdues.append(f"{cle} ({type(exc).__name__})")

    if perdues:
        _consigner_incident(
            p, "entrees_perdues", "tickets illisibles : " + ", ".join(perdues[:8]),
            sauvegarde=_sauvegarder_avant_ecrasement(p),
            entrees_lues=len(etat), entrees_perdues=len(perdues))
    return etat


def save_state(chemin: Path, state: dict[str, TrackedState]) -> None:
    """Écriture atomique : un plantage ne doit pas laisser un JSON tronqué."""
    p = Path(chemin)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps({k: v.to_dict() for k, v in state.items()}, indent=2),
                   encoding="utf-8")
    tmp.replace(p)


# ───────────────────────────── passage de gestion ───────────────────────────

#: Bornes de plausibilité du R, en fraction du prix d'entrée.
#:
#: Un stop à moins d'un cent-millième du prix n'existe pas chez un courtier
#: réel : c'est un résidu d'arrondi ou un stop normalisé sur le prix. Un stop
#: à plus de 50 % du prix n'est pas un stop non plus.
#: Hors de ces bornes, on REFUSE de journaliser plutôt que d'écrire un
#: chiffre faux — le registre d'edge n'a aucun moyen de s'en remettre.
R_MIN_RELATIF = 1e-5
R_MAX_RELATIF = 0.5

# Motifs qui ne peuvent pas disparaître lors d'une nouvelle tentative I/O.
# L'appelant les place en quarantaine au lieu de bloquer la boucle à vie.
MOTIF_REFUS_DEFINITIF = frozenset({
    "R_INVALIDE",
    "R_HORS_BORNES",
    "RISQUE_DEVISE_INCONNU",
    "CONTEXTE_INCONNU",
    "PNL_NET_INCONNU",
    "PNL_R_HORS_BORNES",
    "COUT_R_INVALIDE",
})

MOTIF_SORTIE_INTROUVABLE = "SORTIE_INTROUVABLE"
MAX_SORTIE_INTROUVABLE_SECONDES = 15 * 60

# Suffixes ajoutes par les courtiers a un instrument logique. La comparaison
# reste stricte sur l'identite de base : seul un suffixe connu est neutralise.
_BROKER_SYMBOL_SUFFIXES = (".cash", ".spot", ".pro", ".fs")

# Formes MT5 qui ferment au moins une partie d'une position. Cette convention
# doit rester identique a celle de `analysis.reconciliation`.
_DEAL_ENTRY_OUT = frozenset({1, 2, 3})
_MONETARY_FIELDS = ("profit", "commission", "swap", "fee")


def _symboles_mt5_equivalents(gauche: str, droite: str) -> bool:
    """Compare deux symboles MT5 en neutralisant un suffixe courtier connu."""
    def identites(value: str) -> set[str]:
        symbole = str(value or "").strip().upper()
        if not symbole:
            return set()
        valeurs = {symbole}
        for suffixe in _BROKER_SYMBOL_SUFFIXES:
            suffixe_haut = suffixe.upper()
            if symbole.endswith(suffixe_haut) and len(symbole) > len(suffixe_haut):
                valeurs.add(symbole[:-len(suffixe_haut)])
        return valeurs

    return bool(identites(gauche) & identites(droite))


def _cloture_depuis_historique(
        mt5, ticket: str, *,
        expected_symbol: str,
        diagnostic: dict | None = None) -> tuple[float | None, str, float, float]:
    """Prix, horodatage et FRAIS de clôture, lus dans l'historique des deals.

    Une position purgée n'est plus dans ``positions_get()`` — son prix de sortie
    n'existe plus que dans l'historique des deals. Sans cette lecture, on ne
    peut pas calculer le résultat réel et on serait réduit à l'estimer.

    Les frais sont sommés sur TOUS les deals de la position, pas seulement le
    dernier : la commission est prélevée à l'entrée comme à la sortie, et le
    swap s'accumule à chaque nuit de portage. Les ignorer gonflerait l'espérance
    journalisée — précisément le chiffre qui autorise le passage en argent réel.
    Le total est rendu en devise ; la conversion en R appartient à l'appelant,
    seul à connaître la valeur du risque engagé.
    """
    if diagnostic is not None:
        diagnostic["accounting_complete"] = False
    try:
        from datetime import timedelta

        fin = datetime.now(timezone.utc) + timedelta(days=1)
        debut = fin - timedelta(days=30)
        ticket_int = int(ticket)
        deals = mt5.history_deals_get(debut, fin, position=ticket_int)
        if not deals:
            return None, "", 0.0, 0.0

        # Ne jamais faire confiance au seul filtre `position=` du terminal.
        # Une ancienne version a recu tous les deals du compte lors d'une
        # fermeture groupee et a recopie le prix GER40 sur trois paires FX.
        # MT5 expose `position_id`; certains adaptateurs utilisent `position`.
        # Un deal sans position ou sans symbole est inutilisable et reste
        # fail-closed : pas de mesure vaut mieux qu'une mesure inventee.
        filtres = []
        symbole_attendu = str(expected_symbol or "").strip()
        if not symbole_attendu:
            return None, "", 0.0, 0.0
        for deal in deals:
            position_deal = getattr(deal, "position_id", None)
            if position_deal is None:
                position_deal = getattr(deal, "position", None)
            if position_deal is None:
                continue
            try:
                if int(position_deal) != ticket_int:
                    continue
            except (TypeError, ValueError):
                continue

            symbole_deal = str(getattr(deal, "symbol", "") or "").strip()
            if not symbole_deal:
                continue
            if not _symboles_mt5_equivalents(symbole_deal, symbole_attendu):
                continue
            filtres.append(deal)

        deals = sorted(
            filtres,
            key=lambda deal: (
                float(getattr(deal, "time_msc", 0) or 0)
                or float(getattr(deal, "time", 0) or 0)
            ),
        )
        if not deals:
            return None, "", 0.0, 0.0

        # La preuve de completude porte uniquement sur les deals deja filtres
        # par position ET symbole. Un adaptateur MT5 peut ignorer le filtre
        # `position=` et rendre tout le compte ; les volumes d'un autre actif
        # ne doivent jamais completer artificiellement cette position.
        entries = [
            deal for deal in deals
            if int(getattr(deal, "entry", -1)) == 0
        ]
        exits = [
            deal for deal in deals
            if int(getattr(deal, "entry", -1)) in _DEAL_ENTRY_OUT
        ]
        entry_volume = sum(
            abs(float(getattr(deal, "volume", 0.0) or 0.0))
            for deal in entries
        )
        exit_volume = sum(
            abs(float(getattr(deal, "volume", 0.0) or 0.0))
            for deal in exits
        )
        monetary_complete = True
        for deal in deals:
            for field in _MONETARY_FIELDS:
                if not hasattr(deal, field):
                    monetary_complete = False
                    break
                try:
                    if not math.isfinite(float(getattr(deal, field))):
                        monetary_complete = False
                        break
                except (TypeError, ValueError):
                    monetary_complete = False
                    break
            if not monetary_complete:
                break
        has_inout = any(int(getattr(deal, "entry", -1)) == 2 for deal in deals)
        accounting_complete = (
            bool(entries)
            and entry_volume > 0
            and not has_inout
            and monetary_complete
            and math.isclose(
                entry_volume, exit_volume, rel_tol=1e-6, abs_tol=1e-9,
            )
        )
        if diagnostic is not None:
            diagnostic.update(
                accounting_complete=accounting_complete,
                entry_volume=entry_volume,
                exit_volume=exit_volume,
                monetary_complete=monetary_complete,
                has_inout=has_inout,
            )

        frais = 0.0
        brut = 0.0
        sortie = None
        for d in deals:
            for champ in ("commission", "swap", "fee"):
                with contextlib.suppress(TypeError, ValueError):
                    frais += float(getattr(d, champ, 0.0) or 0.0)
            with contextlib.suppress(TypeError, ValueError):
                brut += float(getattr(d, "profit", 0.0) or 0.0)
            # DEAL_ENTRY_OUT == 1. Plus sûr que « le dernier deal » : une
            # position peut être clôturée en plusieurs fois, et un deal de
            # correction postérieur porterait un prix qui n'est pas la sortie.
            if int(getattr(d, "entry", -1) or -1) in _DEAL_ENTRY_OUT:
                sortie = d

        if sortie is None:
            # Une disparition de ``positions_get`` peut être transitoire. Le
            # deal d'entrée ne prouve jamais une clôture et ne doit surtout
            # pas devenir son propre prix de sortie. L'état reste alors
            # disponible pour un nouvel essai au tour suivant.
            return None, "", 0.0, 0.0

        # `deal.time` est en heure SERVEUR. L'etiqueter "+00:00" a rendu
        # fausses de trois heures les 35 premieres cloture du journal et a
        # gonfle d'autant toutes les durees de detention (7 minutes reelles
        # journalisees 187). Constate le 12/08/2026.
        decalage = decalage_serveur(mt5, (symbole_attendu,))
        quand = heure_serveur_en_utc(getattr(sortie, "time", 0) or 0, decalage)
        # `net` = ce que le compte a réellement gagné ou perdu, frais compris.
        # MT5 signe déjà `profit` selon le sens : pas de multiplication par
        # `side`, qui inverserait tous les shorts.
        return (float(getattr(sortie, "price", 0.0) or 0.0), quand, frais,
                brut + frais)
    except Exception:  # noqa: BLE001 — l'absence d'historique n'est pas fatale
        return None, "", 0.0, 0.0


def _classe_de(symbole: str) -> str:
    """Classe d'actif, résolue à la clôture si elle manque à l'état."""
    try:
        from titanium.edge import asset_class_of
        return asset_class_of(symbole)
    except Exception:  # noqa: BLE001
        return ""


def journaliser_cloture(st: TrackedState, ticket: str, *,
                        prix_sortie: float | None, ts_exit: str,
                        journal_path: Path, cost_r: float | None = None,
                        net_devise: float | None = None,
                        exact_net: bool = False,
                        diagnostic: dict | None = None) -> bool:
    """Écrit UNE ligne immuable pour une position qui vient de se fermer.

    C'est le maillon qui manquait : sans lui, rien de ce que fait le bot n'est
    mesurable, le registre d'edge reste vide, et le mode PROD ne s'ouvre jamais.

    Ne lève jamais — une erreur de journalisation ne doit pas remonter vers le
    trading. Rend True si une ligne a été écrite.
    """
    try:
        from titanium.edge import ClosedTrade, TradeJournal

        if st.r <= 0 or not math.isfinite(st.r):
            if diagnostic is not None:
                diagnostic.update(reason="R_INVALIDE", permanent=True)
            return False

        # Sans risque monetaire, le net du compte ne peut pas etre converti en
        # R. Une distance de prix seule n'a pas la meme unite et ne constitue
        # pas une preuve exploitable pour la promotion PROD.
        if not (st.risque_devise > 0 and math.isfinite(st.risque_devise)):
            if diagnostic is not None:
                diagnostic.update(reason="RISQUE_DEVISE_INCONNU", permanent=True)
            return False

        # Une position decouverte apres coup n'a plus son contexte d'entree.
        # La ranger dans un seau artificiel ferait tout de meme augmenter le
        # nombre d'echantillons. Une absence de preuve reste une absence.
        if not str(st.context_key or "").strip():
            if diagnostic is not None:
                diagnostic.update(reason="CONTEXTE_INCONNU", permanent=True)
            return False

        # ── Le R doit être une distance PLAUSIBLE au regard du prix d'entrée.
        #    Cette borne couvre les stops résiduels ou mal normalisés. Elle
        #    n'explique pas l'incident à +101 280 739 R du 07/08/2026 : sa
        #    cause était un prix de sortie GER40 attribué à une paire FX,
        #    désormais bloqué en amont par le filtre ticket + symbole.
        #    Une ligne fausse est bien pire qu'une ligne absente : elle se
        #    présente comme une mesure et rend le seuil des 20 trades inutile.
        if st.entry > 0:
            ratio = st.r / abs(st.entry)
            if not (R_MIN_RELATIF <= ratio <= R_MAX_RELATIF):
                if diagnostic is not None:
                    diagnostic.update(reason="R_HORS_BORNES", permanent=True)
                return False

        # `ClosedTrade.pnl_r` est une preuve comptable nette. Une excursion ou
        # une reconstruction par prix ne connait pas necessairement tous les
        # frais et ne doit donc jamais alimenter la promotion PROD.
        if net_devise is None:
            if diagnostic is not None:
                diagnostic.update(reason="PNL_NET_INCONNU", permanent=True)
            return False
        pnl_r = float(net_devise) / st.risque_devise

        cout_total = None if cost_r is None else abs(float(cost_r))
        if cout_total is not None and not math.isfinite(cout_total):
            if diagnostic is not None:
                diagnostic.update(reason="COUT_R_INVALIDE", permanent=True)
            return False

        # Dernier filet : même avec un `r` plausible, un prix de sortie
        # aberrant (donnée d'historique corrompue) doit être refusé.
        if not math.isfinite(pnl_r) or abs(pnl_r) > PNL_R_MAX:
            if diagnostic is not None:
                diagnostic.update(reason="PNL_R_HORS_BORNES", permanent=True)
            return False

        journal = TradeJournal(journal_path)

        # Idempotence : un ticket déjà journalisé ne l'est pas deux fois. Une
        # relance du gestionnaire relit un état où le ticket a disparu de MT5 —
        # sans ce garde-fou, chaque redémarrage dupliquerait la ligne.
        marque = f"live:{ticket}"
        if any(t.ticket == marque for t in journal.read_all()):
            if diagnostic is not None:
                diagnostic.update(reason="DEJA_JOURNALISE", permanent=False)
            return False

        # Le giveback n'a de sens QUE s'il y a eu un gain. Le compter sinon
        # double-compte la MAE — défaut relevé par un test dans V12.
        giveback = round(st.peak_fav_r - pnl_r, 4) if st.peak_fav_r > 0 else 0.0

        journal.record(ClosedTrade(
            context=st.context_key,
            source="live",
            account=st.account,
            mode=st.mode,
            quorum=st.quorum,
            support_pillars=st.support_pillars,
            candle_source=st.candle_source,
            contre_tendance=st.contre_tendance,
            # `closed_at` est desormais du vrai UTC. Le marqueur permet a un
            # outil de refuser les lignes anciennes, ecrites en heure serveur.
            horloge="utc",
            asset_class=st.asset_class or _classe_de(st.symbol),
            timeframe=st.timeframe,
            risk_money=st.risque_devise,
            # Exact seulement si le PnL comptable ET la decomposition complete
            # du cout (spread + frais MT5) sont tous deux mesures.
            exact_cost=(
                bool(exact_net)
                and cout_total is not None
                and st.spread_exact
            ),
            # `net_devise` est obligatoire plus haut : ce marqueur distingue
            # la preuve de rentabilite nette de sa ventilation de cout, dont
            # le spread reste aujourd'hui estime.
            exact_net=bool(exact_net),
            pnl_r=round(pnl_r, 4),
            closed_at=ts_exit or datetime.now(timezone.utc).isoformat(),
            ticket=marque,
            exit_reason=st.phase,
            cost_r=round(cout_total, 4) if cout_total is not None else None,
        ))

        # Détail d'excursion à côté du journal d'edge : il porte plus que ce que
        # `ClosedTrade` sait modéliser (MAE/MFE/panel), et c'est cette matière
        # qui nourrit l'analyse discriminante.
        detail = journal_path.parent / "excursions.ndjson"
        detail.parent.mkdir(parents=True, exist_ok=True)
        with detail.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ticket": marque, "symbol": st.symbol, "side": st.side,
                "ts_open": st.ts_open, "ts_exit": ts_exit,
                # Même convention que `ClosedTrade.horloge`, et plus nécessaire
                # encore ici : ce sont CES deux horodatages qu'un rejeu croise
                # avec des barres de marché. Sans le marqueur, un outil ne peut
                # pas distinguer une ligne en vrai UTC d'une ligne en heure
                # serveur étiquetée « +00:00 » — et croiser les deux produit un
                # résultat crédible et faux.
                "horloge": "utc",
                "entry": st.entry, "exit": prix_sortie,
                "r_unit": st.r, "sl_initial": st.sl_initial,
                "tp_initial": st.tp_initial,
                "pnl_r": round(pnl_r, 4),
                "mae_r": round(st.mae_r, 4), "mfe_r": round(st.peak_fav_r, 4),
                "giveback_r": giveback,
                "exit_reason": st.phase, "context": st.context_key,
                "contre_tendance": st.contre_tendance,
                "entry_policy": st.entry_policy,
                "execution_mode": st.mode,
                "policy_epoch": st.policy_epoch,
                "config_sha256": st.config_sha256,
                "code_sha256": st.code_sha256,
                # Vrai si la sortie a tronqué la MFE (stop touché) : sans ce
                # drapeau, toute statistique future de MFE est biaisée à la baisse.
                "censored": st.phase != PHASE_TRAILING and pnl_r <= 0,
                "indicators": st.indicators,
                # Niveaux structurels et distance entrée→niveau, figés à
                # l'ouverture (cf. TrackedState.entry_levels). Excursion à
                # horizon fixe en barres (cf. TrackedState.horizon_excursions).
                # Additifs, mesure seule.
                "entry_levels": st.entry_levels,
                "entry_atr": st.entry_atr,
                "horizon_excursions": st.horizon_excursions,
                "source": "live",
            }, ensure_ascii=False) + "\n")
        if st.decision_id:
            from titanium.execution.decision_registry import append_decision_event

            decision_written, decision_reason = append_decision_event(
                journal_path.parent / "decision_registry.ndjson",
                {
                    "event": "resolved",
                    "decision_id": st.decision_id,
                    "execution_ticket": int(ticket),
                    "symbol": st.symbol,
                    "closed_at": ts_exit,
                    "ts_exit": ts_exit,
                    "pnl_r": round(pnl_r, 4),
                    "mae_r": round(st.mae_r, 4),
                    "mfe_r": round(st.peak_fav_r, 4),
                    "giveback_r": giveback,
                    "exit_reason": st.phase,
                },
            )
            if diagnostic is not None:
                diagnostic.update(
                    decision_registry_written=decision_written,
                    decision_registry_reason=decision_reason,
                )
        if st.limit_order_ticket:
            # La fermeture complete le meme fil causal que le placement et le
            # fill. Le PnL reste le net comptable MT5 deja valide ci-dessus.
            from titanium.execution.pending_context import append_limit_event

            written, reason = append_limit_event(
                journal_path.parent / "limit_lifecycle.ndjson",
                {
                    "event": "closed",
                    "order_ticket": st.limit_order_ticket,
                    "position_ticket": int(ticket),
                    "symbol": st.symbol,
                    "side": st.side,
                    "planned_price": st.limit_planned_price,
                    "market_reference_price": st.limit_market_reference_price,
                    "fill_price": st.entry,
                    "exit_price": prix_sortie,
                    "target_saving_r": st.limit_target_saving_r,
                    "realized_saving_r": st.limit_realized_saving_r,
                    "slippage_r": st.limit_slippage_r,
                    "pnl_r": round(pnl_r, 4),
                    "cost_r": round(cout_total, 4) if cout_total is not None else None,
                    "context": st.context_key,
                    "contre_tendance": st.contre_tendance,
                    "regime": (
                        str(st.context_key).split("|")[2]
                        if len(str(st.context_key).split("|")) > 2 else "unknown"
                    ),
                    "asset_class": st.asset_class or _classe_de(st.symbol),
                    "mode": st.mode,
                    "entry_policy": st.entry_policy,
                    "execution_mode": st.mode,
                    "policy_epoch": st.policy_epoch,
                    "config_sha256": st.config_sha256,
                    "code_sha256": st.code_sha256,
                    "closed_at": ts_exit,
                },
            )
            if diagnostic is not None:
                diagnostic.update(
                    limit_lifecycle_written=written,
                    limit_lifecycle_reason=reason,
                )
        if diagnostic is not None:
            diagnostic.update(reason="JOURNALISE", permanent=False)
        return True
    except Exception as exc:  # noqa: BLE001 — journaliser ne casse jamais le trading
        if diagnostic is not None:
            diagnostic.update(
                reason=f"IO_{type(exc).__name__}",
                permanent=False,
            )
        return False


def _quarantiner_rejet(st: TrackedState, ticket: str, *, reason: str,
                       journal_path: Path, ts_exit: str,
                       age_seconds: float | None = None) -> bool:
    """Conserve un rejet définitif sans le faire passer pour une mesure d'edge."""
    try:
        path = journal_path.parent / "journal_rejets.ndjson"
        marque = f"live:{ticket}"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if json.loads(line).get("ticket") == marque:
                        return True
                except (json.JSONDecodeError, AttributeError):
                    continue
        path.parent.mkdir(parents=True, exist_ok=True)
        recoverable = reason.startswith(MOTIF_SORTIE_INTROUVABLE)
        payload = {
                "ticket": marque,
                "symbol": st.symbol,
                "reason": reason,
                "ts_open": st.ts_open,
                "ts_exit": ts_exit,
                "entry": st.entry,
                "r_unit": st.r,
                "risk_money": st.risque_devise,
                "context": st.context_key,
                "source": "live",
                "history_missing_since": st.history_missing_since,
                "history_missing_attempts": st.history_missing_attempts,
        }
        if recoverable:
            payload.update(
                quarantine_recoverable=True,
                tracked_state=st.to_dict(),
                history_missing_age_seconds=max(0, int(age_seconds or 0)),
            )
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 — l'appelant conserve l'état pour réessai
        return False


def _is_ours(pos, magic: int) -> bool:
    if int(getattr(pos, "magic", 0) or 0) == magic:
        return True
    return str(getattr(pos, "comment", "") or "").startswith("titanium")


def _snapshot(mt5, pos) -> PositionSnapshot:
    """Traduit une position MT5 en instantané pur."""
    si = mt5.symbol_info(pos.symbol)
    tick = mt5.symbol_info_tick(pos.symbol)
    point = float(getattr(si, "point", 0) or 0)
    stops_lvl = float(getattr(si, "trade_stops_level", 0) or 0)
    spread = float(tick.ask - tick.bid) if tick else 0.0
    return PositionSnapshot(
        ticket=str(pos.ticket),
        symbol=str(pos.symbol),
        side=1 if int(pos.type) == 0 else -1,   # 0=buy → long, 1=sell → short
        entry=float(pos.price_open),
        current=float(pos.price_current),
        sl=float(pos.sl) if pos.sl else None,
        tp=float(pos.tp) if pos.tp else None,
        digits=int(getattr(si, "digits", 5)),
        # ×1.2 : marge sur un spread qui bouge entre la décision et l'envoi.
        min_stop_distance=max(stops_lvl * point, spread) * 1.2,
        spread=spread,
        volume=float(getattr(pos, "volume", 0.0) or 0.0),
    )


def _risque_devise_position(mt5, pos, snap: PositionSnapshot) -> float:
    """Reconstitue le risque monetaire d'une position adoptee, sans ecriture.

    `order_calc_profit` est la source courtier : elle tient compte de la devise
    du compte et des specifications du contrat. Le calcul par ticks n'est qu'un
    repli pour les doublures de test ou les terminaux qui ne l'exposent pas.
    """
    if snap.sl is None:
        return 0.0
    try:
        volume = float(getattr(pos, "volume", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not (volume > 0 and math.isfinite(volume)):
        return 0.0

    calcul = getattr(mt5, "order_calc_profit", None)
    if callable(calcul):
        try:
            ordre = (getattr(mt5, "ORDER_TYPE_BUY", 0) if snap.side > 0
                     else getattr(mt5, "ORDER_TYPE_SELL", 1))
            perte = float(calcul(
                ordre, snap.symbol, volume, snap.entry, float(snap.sl)))
            if math.isfinite(perte) and perte != 0:
                return abs(perte)
        except (TypeError, ValueError, AttributeError):
            pass

    try:
        info = mt5.symbol_info(snap.symbol)
        tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
        tick_value = float(
            getattr(info, "trade_tick_value_loss", 0.0)
            or getattr(info, "trade_tick_value", 0.0)
            or 0.0
        )
        if tick_size > 0 and tick_value > 0:
            perte = abs(snap.entry - float(snap.sl)) / tick_size * tick_value * volume
            return perte if math.isfinite(perte) else 0.0
    except (TypeError, ValueError, AttributeError):
        pass
    return 0.0


def _ouverture_iso(pos) -> str:
    """Horodatage d'ouverture MT5, ou vide si le terminal ne le fournit pas."""
    try:
        epoch = float(getattr(pos, "time", 0.0) or 0.0)
        if epoch > 0 and math.isfinite(epoch):
            return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        pass
    return ""


def _envoyer_sortie_adaptative(mt5, pos, snap: PositionSnapshot,
                               policy: ExecutionPolicy, *,
                               comment: str = "titanium-v14-adaptive-exit"):
    """Demande la clôture complète du ticket au marché, sans toucher SL/TP."""
    if not (snap.volume > 0 and math.isfinite(snap.volume)):
        raise ValueError("VOLUME_INVALIDE")
    tick = mt5.symbol_info_tick(snap.symbol)
    if tick is None:
        raise ValueError("PAS_DE_PRIX")

    if snap.side > 0:
        ordre = mt5.ORDER_TYPE_SELL
        prix = float(tick.bid)
    else:
        ordre = mt5.ORDER_TYPE_BUY
        prix = float(tick.ask)
    if not (prix > 0 and math.isfinite(prix)):
        raise ValueError("PRIX_SORTIE_INVALIDE")

    requete = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": pos.ticket,
        "symbol": snap.symbol,
        "volume": snap.volume,
        "type": ordre,
        "price": prix,
        "deviation": policy.deviation_points,
        "magic": policy.magic,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": _pick_filling_mode(mt5, snap.symbol),
    }
    return mt5.order_send(requete)


def manage_once(mt5, *, policy: ExecutionPolicy, params: ManageParams,
                state_path: Path, account=None,
                journal_path: Path | None = None,
                manage_stops: bool = True,
                manage_trailing: bool = True,
                manage_exits: bool = False,
                sentiment_request_path: Path | None = None,
                sentiment_verdict_path: Path | None = None,
                weekend_flat: WeekendFlatParams | None = None) -> dict:
    """Un passage sur toutes NOS positions. Ne lève jamais.

    Returns:
        ``{"managed": n, "moved": n, "reason": str, "details": [...]}``
    """
    rapport = {"managed": 0, "moved": 0, "exit_sent": 0,
               "fear_exit_sent": 0, "basket_exit_sent": 0,
               "weekend_exit_sent": 0, "sentiment": {},
               "reason": "", "details": []}

    # Le mur complet ne protège que la branche qui MODIFIE les stops. Le mode
    # observation reste actif quand l'exécution est désarmée : il lit les
    # positions et journalise les clôtures, sans jamais appeler order_send.
    try:
        if account is None:
            from titanium.data.mt5_vendor import account_snapshot
            account = account_snapshot()
        if manage_stops or manage_exits:
            assert_can_trade(policy, account)
        else:
            if not bool(getattr(account, "is_demo", False)):
                rapport["reason"] = "OBSERVE_NOT_DEMO"
                return rapport
            expected = int(getattr(policy, "expected_demo_login", 0) or 0)
            if expected and int(getattr(account, "login", 0) or 0) != expected:
                rapport["reason"] = "OBSERVE_ACCOUNT_MISMATCH"
                return rapport
    except ExecutionRefused as exc:
        rapport["reason"] = exc.code
        return rapport
    except Exception as exc:  # noqa: BLE001 — fail-closed
        rapport["reason"] = f"MUR_ERREUR: {type(exc).__name__}"
        return rapport

    try:
        positions = mt5.positions_get()
        # MT5 rend None en cas d'erreur terminal. Le confondre avec une liste
        # vide ferait croire que toutes les positions suivies sont clôturées,
        # puis purgerait leur contexte de mesure.
        if positions is None:
            last_error = getattr(mt5, "last_error", lambda: "")()
            raise RuntimeError(f"MT5 positions_get: {last_error}")
    except Exception as exc:  # noqa: BLE001
        rapport["reason"] = f"POSITIONS_INDISPONIBLES: {type(exc).__name__}"
        return rapport

    etat = load_state(state_path)
    vivants: set[str] = set()

    # Une photographie commune permet au panier de raisonner sur toutes ses
    # tranches au même tick. Les positions nouvellement adoptées entrent dans
    # ce calcul au passage suivant, après création de leur R initial scellé.
    snapshots: dict[str, PositionSnapshot] = {}
    membres_par_symbole: dict[str, list[BasketMember]] = {}
    for position in positions:
        if not _is_ours(position, policy.magic):
            continue
        try:
            snap = _snapshot(mt5, position)
            snapshots[snap.ticket] = snap
            suivi = etat.get(snap.ticket)
            if suivi is None or suivi.r <= 0 or not math.isfinite(suivi.r):
                continue
            fav_r = (snap.current - snap.entry) / suivi.r * snap.side
            membres_par_symbole.setdefault(snap.symbol, []).append(
                BasketMember(
                    ticket=snap.ticket,
                    fav_r=fav_r,
                    risk_money=float(suivi.risque_devise or 0.0),
                )
            )
        except Exception:  # noqa: BLE001 -- la boucle détaillera ensuite
            continue

    basket_state_path = state_path.with_name("micro_baskets.json")
    anciens_pics = load_basket_peaks(basket_state_path)
    decisions_panier = {}
    nouveaux_pics: dict[str, float] = {}
    for symbole, membres in membres_par_symbole.items():
        decision_panier = decide_basket_exit(
            membres,
            previous_peak_r=anciens_pics.get(symbole, 0.0),
        )
        if len(membres) >= 2:
            decisions_panier[symbole] = decision_panier
            nouveaux_pics[symbole] = decision_panier.peak_r

    # Horloge serveur lue UNE seule fois par passage. La relire position par
    # position coûterait un appel terminal chacune et, si un tick tombait
    # entre deux lectures, deux positions du même passage pourraient se voir
    # de part et d'autre de la frontière du vendredi soir.
    params_weekend = weekend_flat or WeekendFlatParams()
    serveur_maintenant = (
        heure_serveur_mt5(mt5)
        if (manage_exits and params_weekend.actif) else None
    )

    for pos in positions:
        if not _is_ours(pos, policy.magic):
            continue
        rapport["managed"] += 1
        try:
            snap = snapshots.get(str(pos.ticket)) or _snapshot(mt5, pos)
            vivants.add(snap.ticket)

            st = etat.get(snap.ticket)
            if st is None:
                # R figé à la première observation, SL encore d'origine.
                if snap.sl is None or abs(snap.entry - snap.sl) <= 0:
                    rapport["details"].append(f"{snap.symbol} #{snap.ticket}: pas de SL initial")
                    continue
                st = TrackedState(
                    r=abs(snap.entry - snap.sl), symbol=snap.symbol,
                    side=snap.side,
                    # Contexte fige des la premiere observation : c'est la
                    # derniere occasion de le saisir avant la cloture.
                    entry=snap.entry, sl_initial=snap.sl or 0.0,
                    tp_initial=snap.tp or 0.0,
                    # Une adoption ne peut pas reconstituer la perception de
                    # la porte. Le contexte reste donc vide et sera refuse a
                    # la cloture plutot que range dans un faux seau `?|?|0p`.
                    context_key="",
                    ts_open=(_ouverture_iso(pos)
                             or datetime.now(timezone.utc).isoformat()),
                    risque_devise=_risque_devise_position(mt5, pos, snap),
                    spread_r=None,
                    spread_exact=False,
                    mode="unknown",
                    asset_class=_classe_de(snap.symbol),
                    account=str(getattr(account, "login", "") or ""),
                    timeframe="")
                etat[snap.ticket] = st
            elif st.history_missing_attempts:
                # La position a réapparu : la disparition précédente était un
                # snapshot transitoire, pas une clôture.
                st.history_missing_since = ""
                st.history_missing_attempts = 0

            sortie = decide_adaptive_exit(snap, st, params)

            # Mise à plat hors crypto avant la fermeture hebdomadaire : une
            # position d'indice ou de FX portée jusqu'au dimanche soir ne
            # travaille pas, son stop ne peut pas être géré, et le swap court.
            from titanium.edge import asset_class_of
            sortie_weekend = decide_weekend_flat(
                asset_class_of(snap.symbol), serveur_maintenant, params_weekend,
            )
            # Un marché endormi n'accepte aucun ordre : insister ferait partir
            # une demande refusée à chaque tour jusqu'à la réouverture.
            if sortie_weekend.should_exit and not marche_cote(
                    mt5, snap.symbol, serveur_maintenant):
                sortie_weekend = WeekendFlatDecision(False, "MARCHE_FERME")

            peur_confirmee = False
            peur_ref = ""
            if sentiment_request_path is not None:
                from titanium.position_sentiment import (
                    append_record,
                    build_review,
                    confirm_fear,
                    latest_verdict,
                )

                review = build_review(
                    ticket=snap.ticket,
                    symbol=snap.symbol,
                    side=snap.side,
                    entry=snap.entry,
                    current=snap.current,
                    sl=snap.sl,
                    tp=snap.tp,
                    r_unit=st.r,
                    fav_r=sortie.fav_r,
                    peak_fav_r=st.peak_fav_r,
                    mae_r=st.mae_r,
                    opened_at=st.ts_open,
                    context={
                        "asset_class": st.asset_class,
                        "mode": st.mode,
                        "quorum": st.quorum,
                        "support_pillars": st.support_pillars,
                        "context_key": st.context_key,
                    },
                )
                append_record(sentiment_request_path, review)
                verdict = (
                    latest_verdict(sentiment_verdict_path, snap.ticket)
                    if sentiment_verdict_path is not None else None
                )
                confirmation = confirm_fear(
                    verdict,
                    last_ref=st.sentiment_ref,
                    previous_streak=st.fear_streak,
                )
                st.sentiment_ref = confirmation.last_ref
                st.sentiment_state = confirmation.state
                st.sentiment_confidence = confirmation.confidence
                st.fear_streak = confirmation.streak
                rapport["sentiment"][confirmation.state] = (
                    int(rapport["sentiment"].get(confirmation.state, 0)) + 1
                )
                peur_ref = confirmation.last_ref
                peur_confirmee = (
                    confirmation.should_exit
                    and confirmation.last_ref != st.fear_exit_sent_ref
                )
                if confirmation.state in {"FEAR", "PANIC"}:
                    rapport["details"].append(
                        f"{snap.symbol} #{snap.ticket}: GLM {confirmation.state} "
                        f"{confirmation.confidence:.2f}, serie "
                        f"{confirmation.streak}/2 ({confirmation.reason})"
                    )

            panier = decisions_panier.get(snap.symbol)
            sortie_panier = bool(panier is not None and panier.should_exit)
            demande_sortie = (sortie.should_exit or peur_confirmee
                              or sortie_panier or sortie_weekend.should_exit)
            if manage_exits and demande_sortie:
                # Le week-end prime : c'est le seul motif qui ne dépend pas de
                # la trajectoire de la position et qu'attendre n'améliore pas.
                motif = (
                    "weekend" if sortie_weekend.should_exit
                    else ("fear" if peur_confirmee
                          else ("basket" if sortie_panier else "adaptive"))
                )
                res = _envoyer_sortie_adaptative(
                    mt5,
                    pos,
                    snap,
                    policy,
                    comment=f"titanium-v14-{motif}-exit",
                )
                done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
                done_partial = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)
                if res is not None and getattr(res, "retcode", None) in {
                        done, done_partial}:
                    rapport["exit_sent"] += 1
                    if sortie_weekend.should_exit:
                        rapport["weekend_exit_sent"] += 1
                        classe = asset_class_of(snap.symbol) or "classe inconnue"
                        rapport["details"].append(
                            f"{snap.symbol} #{snap.ticket}: mise à plat week-end "
                            f"demandée ({classe}, serveur "
                            f"{serveur_maintenant:%a %d/%m %H:%M})"
                        )
                    elif peur_confirmee:
                        st.fear_exit_sent_ref = peur_ref
                        rapport["fear_exit_sent"] += 1
                        rapport["details"].append(
                            f"{snap.symbol} #{snap.ticket}: sortie peur GLM demandée "
                            f"({st.sentiment_state} {st.sentiment_confidence:.2f})"
                        )
                    elif sortie_panier and panier is not None:
                        rapport["basket_exit_sent"] += 1
                        rapport["details"].append(
                            f"{snap.symbol} #{snap.ticket}: sortie micro-panier "
                            f"demandée (tranches={panier.members} "
                            f"actuel={panier.current_r:.2f}R "
                            f"pic={panier.peak_r:.2f}R "
                            f"plancher={panier.floor_r:.2f}R)"
                        )
                    else:
                        rapport["details"].append(
                            f"{snap.symbol} #{snap.ticket}: sortie adaptative demandée "
                            f"(actuel={sortie.fav_r:.2f}R "
                            f"pic={sortie.peak_fav_r:.2f}R "
                            f"plancher={sortie.floor_r:.2f}R "
                            f"restitution={sortie.giveback_r:.2f}R)"
                        )
                else:
                    rapport["details"].append(
                        f"{snap.symbol} #{snap.ticket}: sortie {motif} refusée "
                        f"retcode={getattr(res, 'retcode', None)}")
                # Ne jamais envoyer une modification de SL dans le même tour
                # qu'une demande de clôture, même si le courtier la refuse.
                continue

            if not manage_stops:
                continue

            d = decide_new_sl(
                snap, st, params, allow_trailing=manage_trailing,
            )
            if d.new_sl is None:
                continue

            req = {
                "action": mt5.TRADE_ACTION_SLTP,
                "position": pos.ticket,
                "symbol": snap.symbol,
                "sl": d.new_sl,
                "tp": round(snap.tp, snap.digits) if snap.tp else 0.0,  # jamais modifié
                "magic": policy.magic,
            }
            res = mt5.order_send(req)
            done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
            if res is not None and getattr(res, "retcode", None) == done:
                rapport["moved"] += 1
                rapport["details"].append(
                    f"{snap.symbol} #{snap.ticket}: SL → {d.new_sl} ({d.phase}, "
                    f"fav={d.fav_r:.2f}R pic={d.peak_fav_r:.2f}R)")
            else:
                rapport["details"].append(
                    f"{snap.symbol} #{snap.ticket}: refusé "
                    f"retcode={getattr(res, 'retcode', None)}")
        except Exception as exc:  # noqa: BLE001 — une position ne casse pas la boucle
            rapport["details"].append(f"{getattr(pos, 'symbol', '?')}: {type(exc).__name__}")
            continue

    # Purge des tickets fermés — sinon l'état gonfle indéfiniment.
    # ⚠️ Un ticket qui disparaît = une position qui vient de SE FERMER. C'est le
    # SEUL instant où l'on connaît encore son contexte d'entrée ET son résultat.
    # Si on ne journalise pas ici, la conséquence est perdue pour toujours et
    # l'edge ne pourra jamais être mesuré.
    cible_journal = journal_path or (state_path.parent / "trades.ndjson")
    for tk in [t for t in etat if t not in vivants]:
        st = etat[tk]
        diagnostic_historique: dict = {}
        prix, quand, frais, net = _cloture_depuis_historique(
            mt5, tk, expected_symbol=st.symbol,
            diagnostic=diagnostic_historique)
        if prix is None or not quand:
            # L'absence de position dans un snapshot n'est pas une preuve de
            # clôture. Sans deal OUT explicite, conserver le contexte et
            # réessayer évite à la fois une fausse ligne et une quarantaine
            # définitive injustifiée.
            maintenant = datetime.now(timezone.utc)
            st.history_missing_attempts += 1
            if not st.history_missing_since:
                st.history_missing_since = maintenant.isoformat()
            try:
                debut_manquant = datetime.fromisoformat(
                    st.history_missing_since.replace("Z", "+00:00"))
                if debut_manquant.tzinfo is None:
                    debut_manquant = debut_manquant.replace(tzinfo=timezone.utc)
                age_manquant = (maintenant - debut_manquant).total_seconds()
            except (TypeError, ValueError):
                age_manquant = 0.0

            escalade = age_manquant >= MAX_SORTIE_INTROUVABLE_SECONDES
            if escalade and _quarantiner_rejet(
                st,
                tk,
                reason=(
                    f"{MOTIF_SORTIE_INTROUVABLE}_APRES_"
                    f"{int(age_manquant)}_SECONDES"
                ),
                journal_path=cible_journal,
                ts_exit="",
                age_seconds=age_manquant,
            ):
                etat.pop(tk, None)
                rapport["journal_rejected"] = rapport.get("journal_rejected", 0) + 1
                rapport["details"].append(
                    f"#{tk} sortie introuvable après "
                    f"{st.history_missing_attempts} essais — quarantaine")
            else:
                rapport["journal_failures"] = rapport.get("journal_failures", 0) + 1
                rapport["reason"] = "JOURNAL_GAP"
                rapport["history_missing"] = rapport.get("history_missing", 0) + 1
                rapport["details"].append(
                    f"#{tk} {MOTIF_SORTIE_INTROUVABLE} — essai "
                    f"{st.history_missing_attempts}, age={int(age_manquant)} s")
            continue
        # Convention unique live/backtest : cost_r est une decomposition
        # complete (spread + commission + swap + fee), jamais un ajustement
        # implicite. None signifie inconnu ; zero signifie mesure nulle.
        historique_exact = prix is not None and bool(quand)
        frais_r = None
        if historique_exact and st.risque_devise > 0:
            frais_r = abs(frais) / st.risque_devise
        cout_r = None
        if st.spread_r is not None and frais_r is not None:
            cout_r = abs(float(st.spread_r)) + frais_r
        diagnostic: dict = {}
        ecrit = journaliser_cloture(
            st, tk, prix_sortie=prix, ts_exit=quand,
            journal_path=cible_journal, cost_r=cout_r,
            net_devise=net if historique_exact else None,
            exact_net=bool(diagnostic_historique.get("accounting_complete")),
            diagnostic=diagnostic,
        )
        deja_vu = False
        if not ecrit:
            try:
                from titanium.edge import TradeJournal
                marque = f"live:{tk}"
                deja_vu = any(
                    trade.ticket == marque
                    for trade in TradeJournal(cible_journal).read_all()
                )
            except Exception:  # noqa: BLE001
                deja_vu = False
        rapport["details"].append(
            f"#{tk} clôturé — "
            f"{'journalisé' if ecrit else ('déjà vu' if deja_vu else 'ÉCHEC journal')}")
        rapport["journalises"] = rapport.get("journalises", 0) + int(ecrit)
        permanent = (
            bool(diagnostic.get("permanent"))
            and diagnostic.get("reason") in MOTIF_REFUS_DEFINITIF
        )
        quarantined = False
        if permanent:
            quarantined = _quarantiner_rejet(
                st,
                tk,
                reason=str(diagnostic.get("reason")),
                journal_path=cible_journal,
                ts_exit=quand,
            )
        if ecrit or deja_vu or quarantined:
            etat.pop(tk, None)
            if quarantined:
                rapport["journal_rejected"] = rapport.get("journal_rejected", 0) + 1
                rapport["details"].append(
                    f"#{tk} rejet définitif mis en quarantaine: "
                    f"{diagnostic.get('reason')}")
        else:
            # Garder le contexte permet une nouvelle tentative au passage
            # suivant. Le perdre rendrait l'écart MT5/journal irréparable.
            rapport["journal_failures"] = rapport.get("journal_failures", 0) + 1
            rapport["reason"] = "JOURNAL_GAP"
    try:
        save_state(state_path, etat)
    except Exception:  # noqa: BLE001 — perdre l'état ne doit pas casser la gestion
        rapport["details"].append("sauvegarde de l'état impossible")
    try:
        save_basket_peaks(basket_state_path, nouveaux_pics)
    except Exception:  # noqa: BLE001 — observabilité fail-soft
        rapport["details"].append("sauvegarde des pics micro-panier impossible")

    # Filet de couverture : une position peut naître et mourir entre deux
    # tours. Son contexte est alors inconnaissable ; on conserve la preuve
    # comptable hors edge, sans inventer de R ni de piliers.
    try:
        from titanium.execution.history_recovery import recover_unobserved_closures

        recovery = recover_unobserved_closures(
            mt5,
            magic=policy.magic,
            journal_path=cible_journal,
            open_position_ids=vivants,
            protected_position_ids=etat,
        )
        rapport["history_recovery"] = recovery
        if recovery["recovered"]:
            rapport["details"].append(
                f"{recovery['recovered']} clôture(s) MT5 récupérée(s) hors edge")
    except Exception as exc:  # noqa: BLE001 - jamais casser la boucle
        rapport["history_recovery"] = {
            "recovered": 0,
            "scanned": 0,
            "mt5_closed": 0,
            "journal_edge": 0,
            "missing_in_edge": 0,
            "missing_in_edge_rate": 0.0,
            "reason": f"RECOVERY_ERROR:{type(exc).__name__}",
        }

    return rapport
