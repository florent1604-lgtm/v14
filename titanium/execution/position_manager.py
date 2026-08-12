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

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from titanium.edge import PNL_R_MAX
from titanium.execution.mt5_executor import ExecutionPolicy, ExecutionRefused, assert_can_trade

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

    @classmethod
    def from_config(cls, config: dict | None = None) -> "ManageParams":
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

    def to_dict(self) -> dict:
        return {"r": self.r, "phase": self.phase, "peak_fav_r": self.peak_fav_r,
                "symbol": self.symbol, "side": self.side,
                "entry": self.entry, "sl_initial": self.sl_initial,
                "tp_initial": self.tp_initial, "context_key": self.context_key,
                "indicators": self.indicators, "ts_open": self.ts_open,
                "mae_r": self.mae_r, "risque_devise": self.risque_devise,
                "spread_r": self.spread_r, "spread_exact": self.spread_exact,
                "mode": self.mode, "quorum": self.quorum,
                "support_pillars": self.support_pillars,
                "asset_class": self.asset_class, "account": self.account,
                "timeframe": self.timeframe,
                "candle_source": self.candle_source}

    @classmethod
    def from_dict(cls, d: dict) -> "TrackedState":
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
            indicators=dict(d.get("indicators") or {}),
            ts_open=str(d.get("ts_open", "")),
            mae_r=float(d.get("mae_r", 0.0) or 0.0),
            risque_devise=float(d.get("risque_devise", 0.0) or 0.0),
            spread_r=(None if d.get("spread_r", None) is None
                      else float(d.get("spread_r"))),
            spread_exact=bool(d.get("spread_exact", False)),
            mode=str(d.get("mode", "explore")),
            quorum=int(d.get("quorum", 0) or 0),
            support_pillars=int(d.get("support_pillars", 0) or 0),
            asset_class=str(d.get("asset_class", "")),
            account=str(d.get("account", "")),
            timeframe=str(d.get("timeframe", "")),
            candle_source=str(d.get("candle_source", "") or ""),
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


def decide_new_sl(pos: PositionSnapshot, state: TrackedState,
                  params: ManageParams) -> SlDecision:
    """Décide du nouveau stop. **Fonction pure** : aucun appel MT5, aucune I/O.

    Met à jour ``state.peak_fav_r`` et ``state.phase`` (le suivi du pic est un
    cliquet : il ne redescend jamais, sinon le trailing rendrait du gain).
    """
    d = SlDecision(phase=state.phase)

    if state.r <= 0 or not math.isfinite(state.r):
        d.reason = "R_INVALIDE"
        return d
    if pos.side not in (-1, 1):
        d.reason = "SIDE_INVALIDE"
        return d
    for valeur in (pos.entry, pos.current):
        if not math.isfinite(valeur):
            d.reason = "PRIX_INVALIDE"
            return d

    fav_r = (pos.current - pos.entry) / state.r * pos.side
    state.peak_fav_r = max(state.peak_fav_r, fav_r)
    # La pire excursion se suit ici, au fil de l'eau : à la clôture, MT5 ne
    # montre plus rien et il serait trop tard pour la mesurer.
    state.mae_r = min(state.mae_r, fav_r)
    d.fav_r, d.peak_fav_r = fav_r, state.peak_fav_r

    candidat: float | None = None

    # 1) BREAKEVEN — une seule fois, tant qu'on n'est pas déjà plus loin.
    if state.phase == PHASE_INIT and fav_r >= params.breakeven_r:
        tampon = max(pos.spread, _BUFFER_R_FRACTION * state.r)
        candidat = pos.entry + pos.side * tampon
        state.phase = PHASE_BREAKEVEN
        d.checks.append(f"breakeven atteint (+{fav_r:.2f}R)")

    # 2) TRAILING — suit le PIC, pas le prix courant. Cliquet.
    if state.peak_fav_r >= params.trail_start_r:
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

def load_state(chemin: Path) -> dict[str, TrackedState]:
    """Charge l'état suivi. Un fichier illisible n'empêche jamais de trader."""
    try:
        brut = json.loads(Path(chemin).read_text(encoding="utf-8"))
        return {k: TrackedState.from_dict(v) for k, v in brut.items()}
    except Exception:  # noqa: BLE001
        return {}


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

# Suffixes ajoutes par les courtiers a un instrument logique. La comparaison
# reste stricte sur l'identite de base : seul un suffixe connu est neutralise.
_BROKER_SYMBOL_SUFFIXES = (".cash", ".spot", ".pro", ".fs")

# Formes MT5 qui ferment au moins une partie d'une position. Cette convention
# doit rester identique a celle de `analysis.reconciliation`.
_DEAL_ENTRY_OUT = frozenset({1, 2, 3})


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
        expected_symbol: str) -> tuple[float | None, str, float, float]:
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

        deals = filtres
        if not deals:
            return None, "", 0.0, 0.0

        frais = 0.0
        brut = 0.0
        sortie = None
        for d in deals:
            for champ in ("commission", "swap", "fee"):
                try:
                    frais += float(getattr(d, champ, 0.0) or 0.0)
                except (TypeError, ValueError):
                    pass
            try:
                brut += float(getattr(d, "profit", 0.0) or 0.0)
            except (TypeError, ValueError):
                pass
            # DEAL_ENTRY_OUT == 1. Plus sûr que « le dernier deal » : une
            # position peut être clôturée en plusieurs fois, et un deal de
            # correction postérieur porterait un prix qui n'est pas la sortie.
            if int(getattr(d, "entry", -1) or -1) in _DEAL_ENTRY_OUT:
                sortie = d

        if sortie is None:
            sortie = max(deals, key=lambda d: getattr(d, "time", 0))

        quand = datetime.fromtimestamp(
            float(getattr(sortie, "time", 0) or 0), tz=timezone.utc).isoformat()
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
            asset_class=st.asset_class or _classe_de(st.symbol),
            timeframe=st.timeframe,
            risk_money=st.risque_devise,
            # Exact seulement si le PnL comptable ET la decomposition complete
            # du cout (spread + frais MT5) sont tous deux mesures.
            exact_cost=cout_total is not None and st.spread_exact,
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
                "entry": st.entry, "exit": prix_sortie,
                "r_unit": st.r, "sl_initial": st.sl_initial,
                "tp_initial": st.tp_initial,
                "pnl_r": round(pnl_r, 4),
                "mae_r": round(st.mae_r, 4), "mfe_r": round(st.peak_fav_r, 4),
                "giveback_r": giveback,
                "exit_reason": st.phase, "context": st.context_key,
                # Vrai si la sortie a tronqué la MFE (stop touché) : sans ce
                # drapeau, toute statistique future de MFE est biaisée à la baisse.
                "censored": st.phase != PHASE_TRAILING and pnl_r <= 0,
                "indicators": st.indicators,
                "source": "live",
            }, ensure_ascii=False) + "\n")
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
                       journal_path: Path, ts_exit: str) -> bool:
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
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
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
            }, ensure_ascii=False) + "\n")
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


def manage_once(mt5, *, policy: ExecutionPolicy, params: ManageParams,
                state_path: Path, account=None,
                journal_path: Path | None = None,
                manage_stops: bool = True) -> dict:
    """Un passage sur toutes NOS positions. Ne lève jamais.

    Returns:
        ``{"managed": n, "moved": n, "reason": str, "details": [...]}``
    """
    rapport = {"managed": 0, "moved": 0, "reason": "", "details": []}

    # Le mur complet ne protège que la branche qui MODIFIE les stops. Le mode
    # observation reste actif quand l'exécution est désarmée : il lit les
    # positions et journalise les clôtures, sans jamais appeler order_send.
    try:
        if account is None:
            from titanium.data.mt5_vendor import account_snapshot
            account = account_snapshot()
        if manage_stops:
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

    for pos in positions:
        if not _is_ours(pos, policy.magic):
            continue
        rapport["managed"] += 1
        try:
            snap = _snapshot(mt5, pos)
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

            if not manage_stops:
                continue

            d = decide_new_sl(snap, st, params)
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
        prix, quand, frais, net = _cloture_depuis_historique(
            mt5, tk, expected_symbol=st.symbol)
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

    return rapport
