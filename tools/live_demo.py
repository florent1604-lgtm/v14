"""Boucle armée — amorçage des premiers trades sur le compte DÉMO.

    .venv\\Scripts\\python.exe tools\\live_demo.py            # observation, aucun ordre
    .venv\\Scripts\\python.exe tools\\live_demo.py --armer    # envoie les ordres

CE QUE FAIT LA BOUCLE, À CHAQUE TOUR
-------------------------------------
1. relit l'equity et recalcule **l'univers traçable** — le compte décide seul
   des actifs qu'il peut porter (`titanium.sizing`) ;
2. balaie ces actifs : features → portes ET → RiskGate ;
3. sur un `ENTER`, dimensionne l'actif à son propre budget et envoie l'ordre ;
4. gère les positions ouvertes (breakeven puis trailing).

TROIS SÉCURITÉS QUI NE SE DÉSACTIVENT PAS
------------------------------------------
* le **mur démo↔réel** est revérifié à chaque ordre, jamais mis en cache ;
* `--armer` seul ne suffit pas : `TITANIUM_EXEC_ENABLED=1` doit aussi être dans
  le `.env`. Deux interrupteurs, deux gestes délibérés ;
* une **idempotence par barre** empêche de réenvoyer le même setup au tour
  suivant : la clé est `symbole:timeframe:horodatage de la barre`.

POURQUOI CETTE BOUCLE EXISTE
-----------------------------
La mesure d'edge exige 20 trades clos par contexte. Sans accumulation, le mode
PROD reste fermé pour toujours — par conception. C'est ici que commence
l'accumulation.
"""

from __future__ import annotations

import argparse
import json as _json
import math
import signal
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.execution.decision_registry import (  # noqa: E402
    append_decision_event,
    make_decision_id,
    prepare_decision_registry,
)
from titanium.execution.demo_cohort import (  # noqa: E402
    DEMO_COHORT_START_UTC,
    DEMO_COHORT_SYMBOLS,
)
from titanium.execution.execution_ledger import (  # noqa: E402
    execute_recorded,
    reconcile_recorded,
)
from titanium.execution.live_loss_guard import (  # noqa: E402
    evaluate_live_loss_guard,
    persist_live_loss_quarantine,
)
from titanium.execution.micro_basket import required_improvement_r  # noqa: E402
from titanium.execution.policy_identity import (  # noqa: E402
    build_policy_identity,
    snapshot_code_identity,
)
from titanium.organism import CentralMemory, DecisionIdentity  # noqa: E402
from titanium.organism.market_jepa import (  # noqa: E402
    MarketJepaRuntime,
    attach_market_jepa,
)
from tools.console_output import configure_console_output  # noqa: E402

#: Liste vide : le catalogue complet du courtier est parcouru par rotation.
UNIVERS: list[str] = []

#: Bascule temporaire : le moteur déterministe dimensionne seul les entrées.
ACTIVER_CORTEX = False

#: Repli si le catalogue est illisible.
UNIVERS_SECOURS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD",
    "EURGBP", "EURJPY", "GBPJPY", "ETHUSD", "BTCUSD",
    "US500", "GER40", "UK100", "USTECH", "XAUUSD", "XAGUSD", "USOIL",
]


def univers_complet() -> list:
    """Tous les symboles du catalogue, ou le repli. Ne lève jamais."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
        noms = [s.name for s in (mt5.symbols_get() or [])]
        return noms or list(UNIVERS_SECOURS)
    except Exception:  # noqa: BLE001
        return list(UNIVERS_SECOURS)
# Le chemin chaud ne contient aucun appel LLM. Sur la phase crypto du
# week-end, 30 actifs x six horizons ont ete mesures sous cinq secondes ; un
# passage toutes les dix secondes garde donc une marge nette sans empiler les
# appels MT5. Les decisions Hermès restent asynchrones et mises en cache.
INTERVALLE = 10.0        # s entre deux balayages

# Instruction opérateur du 28/08/2026 : rétablir le breakeven, sans réactiver
# le trailing. Le SL ne bouge qu'une fois vers l'entrée + coûts ; la protection
# dynamique des gains passe ensuite par une clôture active propre au ticket.
MODIFIER_STOPS_EXISTANTS = True
ACTIVER_TRAILING = False
GERER_SORTIES_ADAPTATIVES = True

#: Actifs examinés PAR TOUR. Le catalogue est parcouru par rotation.
#:
#: ⚠️ Sans rotation, balayer 148 actifs prend plus de deux heures — mesuré le
#: 07/08/2026 : un seul tour entre 11h39 et 13h45. Un « intervalle de 60 s »
#: devient alors une fiction, MT5 reste saturé en continu, et l'accumulation
#: RALENTIT au lieu d'accélérer. La rotation garde le tour court tout en
#: couvrant tout le catalogue en quelques minutes.
#: Passé de 24 à 60 : le balayage coûtait 392 ms/actif à cause du panel
#: d'indicateurs, qu'aucune porte ne lit. Sans lui, 30 ms — on examine donc
#: 2,5× plus d'actifs dans un tour PLUS court. Le catalogue entier est
#: couvert en 2-3 tours au lieu de 6.
LOT_PAR_TOUR = 60
_curseur = 0
LTF, HTF = "M15", "H4"
BARRES = 400
# Phase crypto week-end : toutes les unites operationnelles de V14 sont lues.
# D1 sert de contexte aux horizons H1/H4 ; W1/MN1 ne sont pas des horizons
# d'execution du moteur et ne sont donc pas presentes ici.
CRYPTO_TIMEFRAME_PAIRS = (
    ("M1", "M15"),
    ("M5", "H1"),
    ("M15", "H4"),
    ("M30", "H4"),
    ("H1", "D1"),
    ("H4", "D1"),
)
_TIMEFRAME_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440,
}
#: Positions simultanées, tous actifs confondus. **0 = illimité.**
#:
#: Porté de 8 à illimité le 17/08/2026, à la demande de Florent, pour lever le
#: goulot d'accumulation MESURÉ : `results/shadow_prod.ndjson` compte 3391
#: setups ENTER uniques du 10 au 17/08 pour 128 ordres envoyés — 3.8 %. Le
#: plafond de créneaux, pas la sélectivité, décidait de ce qui était joué, et
#: il servait les actifs les plus rapides de la rotation plutôt que les
#: meilleurs. La crypto n'a obtenu que 4 ordres pour 189 ENTER.
#:
#: ⚠️ Le compteur de positions ne borne PAS le risque : c'est
#: `MAX_RISQUE_CUMULE_PCT` qui devient le SEUL garde-fou global d'exposition,
#: avec `MAX_PAR_SYMBOLE` et l'arbre de corrélation. Retirer le plafond de
#: créneaux sans ce budget reviendrait à multiplier un pari unique.
MAX_POSITIONS = 0        # 0 = illimité (le budget de risque borne l'exposition)
# Une seule limite passive peut réserver du risque à la fois. Tant que le
# risque des ordres non exécutés n'est pas valorisé par le moteur global, ce
# verrou empêche plusieurs limites de se déclencher ensemble et de dépasser
# silencieusement MAX_RISQUE_CUMULE_PCT. Ne s'applique qu'en MODE_ENTREE
# "LIMITE" : au marché, aucun ordre ne reste en attente.
MAX_LIMITES_EN_ATTENTE = 1

#: Mode d'entrée : "MARCHE" prend le risque, "LIMITE" économise le spread.
#:
#: Repassé à "MARCHE" le 24/08/2026, à la demande explicite de Florent
#: (« je ne veux pas d'économie mais du risque »), sur une mesure sans
#: ambiguïté du régime passif installé le 12/08 par 5c5884e :
#:
#:   `results/limit_lifecycle.ndjson`, 12/08 → 24/08
#:     689 limites placées · 374 exécutées (54 %) · **315 expirées**
#:     économie réalisée moyenne +0.084 R par ordre rempli
#:     net_pnl_r cumulé **−19.7 R** sur 357 clôtures
#:   `results/refus_live.ndjson` : **194** refus LIMIT_PENDING_CAP
#:
#: Soit ~509 signaux déjà validés par toutes les portes, abandonnés pour
#: gagner 0.084 R sur ceux qui restaient. L'attrition ne se compense pas :
#: un signal non joué rapporte exactement zéro, et le verrou d'une seule
#: limite en attente coupait le reste du balayage du tour par un `break`.
#:
#: Ce drapeau ne relâche AUCUN garde-fou de risque : RiskGate, budget de
#: risque cumulé, MAX_PAR_SYMBOLE et l'arbre de corrélation restent devant
#: l'ordre. Il ne change que la FAÇON d'entrer une fois la décision prise.
#: Remettre "LIMITE" restaure exactement le régime passif.
MODE_ENTREE = "MARCHE"

#: Risque cumulé maximal sur l'ensemble des positions ouvertes, en % de
#: l'équité.
#:
#: ⚠️ Ce plafond est indissociable de la hausse de MAX_POSITIONS. Compter les
#: positions ne borne PAS le risque : à 1.75 % chacune, huit positions font
#: 14 % d'exposition. Pire, l'univers est saturé de paires corrélées —
#: EURUSD, GBPUSD, AUDUSD et NZDUSD longs, c'est quatre fois le même pari
#: contre le dollar. Sans budget global, augmenter le nombre de trades
#: multiplie une exposition unique au lieu de la diversifier.
MAX_RISQUE_CUMULE_PCT = 6.0

#: Dérive maximale tolérée entre le prix qui a produit la décision et le prix
#: au moment de l'envoi, en fraction de la distance de stop.
#:
#: ⚠️ Sans ce contrôle, un setup décidé à la clôture d'une barre M15 peut être
#: exécuté douze minutes plus tard — quand un créneau se libère à la clôture
#: d'une autre position. Le prix a déjà parcouru une partie du chemin : le R:R
#: réel n'est plus celui qui a été validé, et le TP est souvent frôlé puis
#: manqué. Constaté par Florent le 07/08/2026.
DERIVE_MAX_R = 0.35
#: Positions simultanées sur UN MÊME actif. Le plafond seul ne suffit pas :
#: chaque position supplémentaire doit aussi passer `_autoriser_empilement`.
#: Trois permet une entrée initiale, un renfort à meilleur prix et, si le
#: marché invalide le sens, une position de retournement explicitement classée
#: `reversal`. Le budget global et la grappe corrélée restent prioritaires.
MAX_PAR_SYMBOLE = 3
#: Un prix seulement meilleur de quelques ticks est du bruit, pas une nouvelle
#: opportunité. Le renfort doit améliorer le meilleur prix ouvert d'au moins
#: 0,10 R, R étant la distance de stop de la nouvelle décision.
AMELIORATION_ENTREE_MIN_R = 0.10
ESPACEMENT_ENTREE_MIN_ATR = 0.25
ESPACEMENT_ENTREE_MIN_SPREAD = 2.0
#: Risque total maximal des positions et ordres d'un même symbole.
MAX_RISQUE_PANIER_PCT = 3.0

_POLICY_CODE_SOURCES = (
    "tools/live_demo.py",
    # Sur-approximation volontaire : tout module Titanium peut devenir une
    # dépendance dynamique de la décision. Un faux nouvel epoch est sûr; un
    # changement de décision non détecté ne l'est pas.
    "titanium",
    "tradingagents/default_config.py",
)

try:
    # Snapshot UNE FOIS au chargement du processus. Une modification ultérieure
    # du working tree ne peut donc pas réétiqueter le bytecode déjà chargé.
    _BASE_CODE_SNAPSHOT = snapshot_code_identity(
        root=RACINE, code_sources=_POLICY_CODE_SOURCES,
    )
except (OSError, ValueError):
    _BASE_CODE_SNAPSHOT = None


def _decision_policy_identity(
    execution_mode: str,
    rr_ratio: float,
    *,
    ltf: str = LTF,
    htf: str = HTF,
) -> dict[str, str]:
    """Scelle la politique; une panne de télémétrie ne casse jamais l'ordre."""
    try:
        return build_policy_identity(
            entry_policy=MODE_ENTREE,
            execution_mode=execution_mode,
            config={
                "derive_max_r": DERIVE_MAX_R,
                "amelioration_entree_min_r": AMELIORATION_ENTREE_MIN_R,
                "espacement_entree_min_atr": ESPACEMENT_ENTREE_MIN_ATR,
                "espacement_entree_min_spread": ESPACEMENT_ENTREE_MIN_SPREAD,
                "htf": str(htf),
                "ltf": str(ltf),
                "max_limites_en_attente": MAX_LIMITES_EN_ATTENTE,
                "max_par_symbole": MAX_PAR_SYMBOLE,
                "max_positions": MAX_POSITIONS,
                "max_risque_cumule_pct": MAX_RISQUE_CUMULE_PCT,
                "max_risque_panier_pct": MAX_RISQUE_PANIER_PCT,
                "reserve_s3": RESERVE_S3,
                "rr_ratio": float(rr_ratio),
            },
            base_code_snapshot=_BASE_CODE_SNAPSHOT or {},
        )
    except (OSError, TypeError, ValueError):
        return {}


def _autoriser_empilement(
    expositions: list[tuple[int, float]],
    *,
    side: int,
    prix: float,
    stop_distance: float,
    setup_family: str,
    atr: float = 0.0,
    spread: float = 0.0,
) -> tuple[bool, str]:
    """Autorise une position supplémentaire seulement si elle apporte un edge.

    * même sens : le prix doit améliorer le meilleur prix encore ouvert ;
    * sens opposé : la porte doit avoir classé le setup `reversal` ;
    * livre déjà mixte ou plafond atteint : refus fail-closed.

    Le SL n'est ni lu ni modifié ici. Le budget global et la grappe corrélée
    sont contrôlés plus loin, après le dimensionnement exact.
    """
    if not expositions:
        return True, "ACTIF_LIBRE"
    if len(expositions) >= MAX_PAR_SYMBOLE:
        return False, "PLAFOND_PAR_SYMBOLE"
    if side not in (-1, 1):
        return False, "SENS_INVALIDE"
    if not (math.isfinite(prix) and prix > 0.0):
        return False, "PRIX_INVALIDE"
    if not (math.isfinite(stop_distance) and stop_distance > 0.0):
        return False, "STOP_DISTANCE_INVALIDE"

    try:
        valides = [
            (int(s), float(p)) for s, p in expositions
            if int(s) in (-1, 1) and math.isfinite(float(p)) and float(p) > 0.0
        ]
    except (TypeError, ValueError):
        return False, "EXPOSITION_INVALIDE"
    if len(valides) != len(expositions):
        return False, "EXPOSITION_INVALIDE"

    memes = [p for s, p in valides if s == side]
    opposees = [p for s, p in valides if s == -side]
    if memes and opposees:
        return False, "EXPOSITION_DEJA_MIXTE"

    if opposees:
        if str(setup_family or "").strip().lower() != "reversal":
            return False, "SENS_OPPOSE_SANS_RETOURNEMENT"
        return True, "RETOURNEMENT_CONFIRME"

    seuil_r = required_improvement_r(
        stop_distance=stop_distance,
        atr=atr,
        spread=spread,
        base_r=AMELIORATION_ENTREE_MIN_R,
        atr_multiple=ESPACEMENT_ENTREE_MIN_ATR,
        spread_multiple=ESPACEMENT_ENTREE_MIN_SPREAD,
    )
    if seuil_r is None:
        return False, "ESPACEMENT_INVALIDE"

    meilleur = min(memes) if side > 0 else max(memes)
    amelioration_r = side * (meilleur - prix) / stop_distance
    if amelioration_r + 1e-12 < seuil_r:
        return False, (
            f"ENTREE_NON_AMELIOREE_{amelioration_r:.3f}R_MIN_{seuil_r:.3f}R"
        )
    suffixe = "" if abs(seuil_r - AMELIORATION_ENTREE_MIN_R) < 1e-12 else (
        f"_MIN_{seuil_r:.3f}R"
    )
    return True, f"ENTREE_AMELIOREE_{amelioration_r:.3f}R{suffixe}"


def _prix_execution_courant(symbole: str, side: int) -> float | None:
    """Prix exécutable courant (ask pour achat, bid pour vente), sinon None."""
    try:
        import MetaTrader5 as mt5  # noqa: N813

        tick = mt5.symbol_info_tick(symbole)
        if tick is None:
            return None
        prix = float(tick.ask if side > 0 else tick.bid)
        return prix if math.isfinite(prix) and prix > 0.0 else None
    except Exception:  # noqa: BLE001 -- une absence de prix refuse l'empilement
        return None

#: Créneaux réservés à la strate S≥3 parmi MAX_POSITIONS.
#:
#: Les S≥3 font ~10 % des ENTER : sans réserve, les S=2 remplissent les
#: huit créneaux avant qu'un S=3 se présente, et la strate qui nourrit la
#: promotion est censurée par sa propre rareté — le biais identifié dans la
#: conception du deadlock edge_ok.
RESERVE_S3 = 2

#: Suspension des ventes à découvert sur le FX, décidée le 17/08/2026 sur les
#: 128 trades clos du 10 au 17/08.
#:
#: 51 des 53 shorts du journal sont des shorts FX : −23.5 R pour 29 % de
#: réussite, intervalle de confiance bootstrap [−0.67 ; −0.23] R par trade —
#: il exclut zéro, la perte n'est pas du bruit. Les 14 shorts FX restés sur
#: les majeures après le filtre de liquidité perdent encore −0.36 R en
#: moyenne. Les longs, eux, sont à −0.04 R par trade sur l'univers filtré.
#:
#: ⚠️ Une semaine de mesure ne prouve pas qu'un short FX ne vaut jamais rien :
#: c'est une SUSPENSION, pas une loi. À rouvrir dès que l'échantillon long
#: montre une espérance positive stable, ou après 40 shadow-shorts FX
#: mesurés en observation (le verdict continue d'être journalisé).
FX_SHORTS_SUSPENDUS = True

#: Suspension du FX ENTIER, décidée par Florent le 24/08/2026 pour vérifier en
#: direct le gain mesuré hors échantillon.
#:
#: Le rejeu de l'univers (797 706 trades, 147 symboles, porte de coût 0,125
#: active) donne, sur le segment de vérification jamais utilisé pour choisir :
#:
#:     avec le FX      274 860 trades filtrés → +0,0948 R par trade
#:     sans le FX      dont 52 040 hors FX    → +0,1588 R par trade
#:
#: Le FX ne gagne que dans une seule cellule — seuil de coût 0,06, 5,6 % de ses
#: trades, +79 R au total sur 3 887 — c'est-à-dire rien de distinguable de zéro.
#: Aucun réglage de coût ne fabrique un avantage là où il n'y en a pas.
#: Même conclusion que le NO-GO FX de Codex, atteinte par un chemin indépendant.
#:
#: ⚠️ C'est une SUSPENSION de vérification, pas une loi. Le refus est journalisé
#: sous le code `FX_SUSPENDU`, donc le flux FX écarté reste comptable : on saura
#: combien d'occasions ont été laissées, et `tools/suivi_bascule.py` dira si le
#: R réalisé monte vraiment. Pour rouvrir : remettre False ici, redémarrer la
#: boucle. Aucune autre ligne à toucher.
FX_SUSPENDU = True

#: Hiérarchie de balayage écrite par tools/classement_backtest.py.
SELECTION_PATH = Path(__file__).resolve().parent.parent / "results" / "selection_actifs.json"
_TOUR = 0

_stop = False


def _arreter(*_):
    global _stop
    _stop = True
    print("\n  arrêt demandé…", flush=True)


#: Battement de cœur lu par le tableau de bord.
#: Un scan de processus serait plus direct, mais `wmic` n'existe pas sur cette
#: machine (Windows 11 l'a retiré) : la sonde rendait « arrêtée » alors que la
#: boucle tournait. Un fichier horodaté ne dépend d'aucun outil système, et il
#: distingue en plus une boucle **figée** d'une boucle arrêtée — ce qu'un scan
#: de processus ne sait pas faire.
BATTEMENT = Path(__file__).resolve().parent.parent / "results" / "loop_heartbeat.json"


def horodate() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def battre(stats: dict, *, armer: bool, equity: float = 0.0,
           portables: int = 0, intervalle: float = INTERVALLE) -> None:
    """Écrit le battement. Ne lève jamais : c'est de l'observabilité."""
    try:
        import json

        # Incidents de lecture de l'état suivi. Un fichier positions.json
        # abîmé rendait `{}` en silence : les positions vivantes étaient
        # réadoptées sans contexte et leurs clôtures partaient en quarantaine.
        # Le battement est le seul canal que le tableau de bord relit déjà.
        try:
            from titanium.execution.position_manager import incidents_etat
            incidents = incidents_etat()
        except Exception:  # noqa: BLE001
            incidents = []

        BATTEMENT.parent.mkdir(parents=True, exist_ok=True)
        tmp = BATTEMENT.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "intervalle": intervalle,
            "armed": armer,
            "manage_stops": MODIFIER_STOPS_EXISTANTS,
            "manage_trailing": ACTIVER_TRAILING,
            "manage_adaptive_exits": GERER_SORTIES_ADAPTATIVES,
            "equity": equity,
            "portables": portables,
            "cohort_symbols": list(DEMO_COHORT_SYMBOLS),
            "cohort_start_utc": DEMO_COHORT_START_UTC.isoformat(),
            "stats": dict(stats),
            "etat_incidents": incidents[-5:],
            "etat_incidents_total": len(incidents),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(BATTEMENT)
    except Exception:  # noqa: BLE001
        pass


def _journal_coverage(recovery: dict | None) -> dict:
    """Normalise la couverture MT5 -> journal pour le heartbeat."""
    recovery = recovery or {}
    return {
        "mt5_closed": int(recovery.get("mt5_closed", 0) or 0),
        "journal_edge": int(recovery.get("journal_edge", 0) or 0),
        "missing_in_edge": int(recovery.get("missing_in_edge", 0) or 0),
        "missing_in_edge_rate": float(
            recovery.get("missing_in_edge_rate", 0.0) or 0.0),
        "lookback_days": 7,
        "reason": str(recovery.get("reason", "") or ""),
    }


def _execution_detail(result, budget, *, equity: float, currency: str) -> tuple[str, float]:
    """Render the broker-sized fill and return its effective monetary risk."""
    lot_value = getattr(result, "filled_volume", None)
    if lot_value is None:
        lot_value = getattr(result, "lot", None)
    try:
        lot = float(lot_value)
    except (TypeError, ValueError):
        lot = float(budget.lot)
    risk_value = getattr(result, "risk_money_effective", None)
    try:
        risk_money = float(risk_value)
    except (TypeError, ValueError):
        risk_money = float(budget.risk_money)
    if not math.isfinite(lot) or lot <= 0:
        lot = float(budget.lot)
    if not math.isfinite(risk_money) or risk_money <= 0:
        risk_money = float(budget.risk_money)
    effective_pct = 100.0 * risk_money / equity if equity > 0 else 0.0
    detail = (
        f"lot {lot:g} · risque {risk_money:g} {currency} ({effective_pct:.2f} %)"
        + (" [lot min]" if budget.at_min_lot else "")
    )
    return detail, risk_money


def _compter_tunnel(stats: dict, etape: str, motif: str, nombre: int = 1) -> None:
    """Compte un passage/refus avec des cles stables et serialisables."""
    try:
        increment = int(nombre)
    except (TypeError, ValueError):
        increment = 0
    if increment <= 0:
        return
    tunnel = stats.setdefault("tunnel", {})
    seau = tunnel.setdefault(str(etape), {})
    cle = str(motif or "INCONNU")[:120]
    seau[cle] = int(seau.get(cle, 0) or 0) + increment


#: Journal des refus post-ENTER. La boucle armee tourne dans une console
#: `cmd /k` sans fichier de sortie : le 23/08/2026, 435 entrees refusees en
#: 28 h et pas une seule ligne de motif lisible apres coup. La cause a du etre
#: remontee par l'horodatage du cache de l'arbre de correlation.
REFUS_LIVE = RACINE / "results" / "refus_live.ndjson"


def _refus(stats: dict, code: str, symbole: str = "", detail: str = "",
           **contexte) -> None:
    """Compte le refus ET l'ecrit sur disque. Ne leve jamais."""
    _compter_tunnel(stats, "post_enter_refusal", code)
    try:
        ligne = {"at": datetime.now(timezone.utc).isoformat(),
                 "code": str(code), "symbole": str(symbole),
                 "detail": str(detail)[:300]}
        for cle, valeur in contexte.items():
            ligne[cle] = valeur
        REFUS_LIVE.parent.mkdir(parents=True, exist_ok=True)
        with REFUS_LIVE.open("a", encoding="utf-8") as flux:
            flux.write(_json.dumps(ligne, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — journaliser ne doit jamais bloquer
        pass


def _compter_refus_execution(stats: dict, resultat) -> None:
    """Ventile un refus d'ordre sans perdre le compteur historique EXECUTION."""
    _refus(stats, "EXECUTION", getattr(resultat, "symbol", "") or "",
           getattr(resultat, "reason", "") or "INCONNU")
    _compter_tunnel(
        stats,
        "execution_refusal",
        getattr(resultat, "reason", "") or "INCONNU",
    )
    portes = []
    for check in getattr(resultat, "checks", ()) or ():
        if isinstance(check, dict) and check.get("passed") is False:
            portes.append(check.get("gate") or "INCONNU")
    if not portes:
        portes.append("NON_DETAILLE")
    for porte in portes:
        _compter_tunnel(stats, "execution_gate_failed", porte)


def _code_portabilite(motif: str) -> str:
    """Normalise un texte de dimensionnement en code stable de tunnel."""
    texte = str(motif or "").lower()
    correspondances = (
        ("march", "MARCHE_FERME"),
        ("atr", "ATR_INDISPONIBLE"),
        ("tick", "SPECS_TICK"),
        ("volume", "SPECS_VOLUME"),
        ("hors de port", "LOT_MIN_HORS_PORTEE"),
        ("instrument", "INSTRUMENT_ETEINT"),
        ("injouable", "COUT_SPREAD"),
        ("coût", "COUT_SPREAD"),
        ("cout", "COUT_SPREAD"),
        ("spread", "COUT_SPREAD"),
        ("volatil", "COUT_OU_VOLATILITE"),
    )
    for fragment, code in correspondances:
        if fragment in texte:
            return code
    return "AUTRE"


def cle_barre(symbole: str, feats: dict) -> str:
    """Idempotence : une entrée par setup et par barre, jamais deux.

    ⚠️ Ancrée sur ``bar_time`` — l'horodatage de la dernière barre CLÔTURÉE — et
    surtout pas sur ``decided_at``, qui vaut l'instant du calcul et change à
    chaque balayage. Le défaut a été constaté en réel le 07/08/2026 : la clé
    étant toujours neuve, trois positions se sont ouvertes sur AUDUSD en
    quelques minutes, triplant le risque prévu sur un seul actif.

    Sans ``bar_time`` exploitable, on rend une clé vide : l'exécuteur traite
    l'absence de clé comme « pas de déduplication », et c'est le garde-fou par
    symbole ci-dessous qui prend le relais.
    """
    trace = feats.get("_trace") or {}
    barre = trace.get("bar_time") or ""
    # L'unité fait partie de la clé : le même instant en M15 et en H1 sont
    # deux barres différentes, et deux décisions différentes.
    unite = trace.get("timeframe") or LTF
    return f"{symbole}:{unite}:{barre}" if barre else ""


def _marquer_echelle(feats: dict, timeframe: str, higher_timeframe: str) -> None:
    """Attache l'échelle réellement analysée à toutes les preuves aval.

    Sans ce marquage, une décision H1/H4 était affichée et journalisée comme
    M15. Il devenait impossible de mesurer l'échelle adaptative séparément.
    """
    trace = feats.setdefault("_trace", {})
    trace["timeframe"] = str(timeframe)
    trace["higher_timeframe"] = str(higher_timeframe)


def _echelles_a_balayer(
    symbole: str, unite: str, haute: str, *, crypto_weekend: bool = False,
) -> tuple:
    """Horizons a evaluer sans modifier le comportement des marches ouverts.

    La phase multi-horizon est reservee a la crypto lorsque les autres marches
    sont fermes. En semaine, le dimensionnement adaptatif conserve exactement
    son couple historique afin de ne pas changer simultanement deux regimes.
    """
    from titanium.edge import asset_class_of

    if crypto_weekend and asset_class_of(symbole) == "crypto":
        return CRYPTO_TIMEFRAME_PAIRS
    return ((str(unite), str(haute)),)


def _resoudre_candidats_multitimeframe(candidats: list[dict]) -> tuple[list[dict], list[str]]:
    """Garde au plus une these coherente par actif.

    Hermès peut arbitrer la qualite d'une these, mais ne doit pas recevoir
    deux instructions opposees pour le meme actif au meme instant. Toute
    contradiction directionnelle est donc bloquee avant le cortex. Quand les
    horizons convergent, on retient d'abord le plus de piliers, puis le rang,
    le cout et enfin l'horizon le plus long.
    """
    groupes: dict[str, list[dict]] = {}
    for candidat in candidats:
        groupes.setdefault(str(candidat.get("sym", "")), []).append(candidat)

    retenus: list[dict] = []
    conflits: list[str] = []
    for symbole, groupe in groupes.items():
        directions = {
            int(getattr(c.get("out"), "side", 0) or 0) for c in groupe
        } - {0}
        if len(directions) != 1:
            conflits.append(symbole)
            continue
        retenus.append(max(
            groupe,
            key=lambda c: (
                int(c.get("support", 0) or 0),
                float(c.get("rank", 0.0) or 0.0),
                -float(c.get("cost", math.inf) or math.inf),
                _TIMEFRAME_MINUTES.get(str(c.get("timeframe", "")), 0),
            ),
        ))
    return retenus, conflits


def _journaliser_selection_multitimeframe(
    candidats: list[dict], retenus: list[dict], conflits: list[str], stats: dict,
) -> None:
    """One terminal outcome per discarded raw ENTER, without changing selection."""
    identites = {id(c) for c in retenus}
    symboles_en_conflit = set(conflits)
    for c in candidats:
        if id(c) in identites:
            continue
        conflit = str(c.get("sym", "")) in symboles_en_conflit
        _refus(
            stats, "MULTITIMEFRAME_CONFLICT" if conflit else "MULTITIMEFRAME_COALESCED",
            c.get("sym", ""),
            "directions opposees" if conflit else "autre horizon retenu; pas un rejet courtier",
            timeframe=c.get("timeframe"), stage="multitimeframe",
        )


#: Charges de zones du tour courant, une par symbole. Vidé à chaque tour
#: pour qu'un symbole sorti de l'univers cesse d'être tracé.
_ZONES: dict = {}


def _tracer_zones(sym: str, feats: dict, out, cfg, conf=None, budget=None) -> None:
    """Exporte les zones vers MT5. Ne lève jamais — l'affichage n'est pas critique."""
    try:
        from titanium.bridge.mt5_zones import Plan, zones_depuis_features
        from titanium.gates import confluence_gate

        d = confluence_gate.evaluate(feats, require_edge=cfg.require_edge)
        trace = feats.get("_trace") or {}
        indics = dict(trace.get("indicators") or {})
        risque = {}
        if conf is not None:
            risque = {"pct": conf.pct, "motif": conf.motif,
                      "lot": getattr(budget, "lot", 0.0) if budget else 0.0}
        prix = (feats.get("_trace") or {}).get("price")
        stop = out.stop_distance
        sens = out.side or 0
        plan = Plan(side=sens)
        if prix and stop and sens:
            plan = Plan(side=sens, entry=prix,
                        sl=prix - sens * stop,
                        tp=prix + sens * stop * cfg.rr_ratio)
        charge = zones_depuis_features(
            sym, feats, timeframe=str(trace.get("timeframe") or LTF),
            verdict=d.verdict, code=d.code,
            pillars=[{"name": g.name, "passed": g.passed} for g in d.gates],
            plan=plan)
        charge.indicators = indics
        charge.risk = risque
        # Accumule au lieu d'ecraser : le fichier porte un bloc par symbole,
        # et chaque graphique extrait le sien. Ecraser ne laisserait dessiner
        # qu'UN graphique — les autres resteraient vides par construction.
        _ZONES[sym] = charge
    except Exception:  # noqa: BLE001
        pass


def _publier_zones() -> None:
    """Ecrit toutes les charges accumulees, en une ecriture atomique.

    Un seul fichier plutot qu'un par symbole : l'ecriture atomique garantit
    alors que tous les graphiques voient le meme instant.
    """
    try:
        from titanium.bridge.mt5_zones import ecrire_multi
        if _ZONES:
            ecrire_multi(list(_ZONES.values()))
    except Exception:  # noqa: BLE001
        pass


#: Arbre de correlation, calcule au demarrage et rafraichi hors du chemin
#: critique. `None` = pas encore disponible, le garde-fou refuse l'entree.
_GRAPPES = None
_GRAPPES_A = 0.0
_GRAPPES_CATALOGUE: set = set()


#: Actifs jouables vus depuis le démarrage. La rotation n'en montre que 24
#: par tour ; l'arbre a besoin d'un échantillon large pour être fidèle.
_JOUABLES: set = set()


def _tradables_connus(courants) -> list:
    """Cumul des actifs jouables rencontrés, pour nourrir l'arbre."""
    _JOUABLES.update(courants)
    if UNIVERS and len(_JOUABLES) < 20:
        # A small explicit cohort cannot rebuild a meaningful tree alone. Seed
        # it from the last broad cache and include temporarily non-portable
        # cohort members so they are covered when market costs improve.
        try:
            from titanium.correlation import charger_cache

            cache = charger_cache()
            if cache is not None:
                _JOUABLES.update(cache.par_actif)
        except Exception:  # noqa: BLE001 - the cluster gate remains fail-closed
            pass
        _JOUABLES.update(UNIVERS)
    return sorted(_JOUABLES)


def _entry_universe(open_symbols, scanned_symbols) -> list:
    """Return new-entry candidates without reinforcing outside the cohort."""
    candidates = list(dict.fromkeys([*open_symbols, *scanned_symbols]))
    if not UNIVERS:
        return candidates
    allowed = {str(symbol).upper() for symbol in UNIVERS}
    return [symbol for symbol in candidates if str(symbol).upper() in allowed]


def rafraichir_grappes(catalogue) -> None:
    """(Re)calcule l'arbre de correlation. A appeler HORS d'un envoi d'ordre.

    Deux besoins distincts, longtemps confondus dans un seul garde-fou :

    * **calculer** un arbre demande un echantillon large — sous 20 actifs les
      familles ne veulent rien dire, et on attend que la rotation en ait
      montre assez ;
    * **lire** un arbre deja calcule ne demande rien du tout.

    Le seuil de 20 gardait les deux. Consequence mesuree le 22/08/2026 : la
    boucle redemarre un samedi, seule la crypto est ouverte, 17 actifs
    portables — l'arbre n'est jamais charge, meme avec un cache frais sur
    disque, et la porte de risque correle refuse 435 entrees d'affilee.
    Le seuil ne garde donc plus que le recalcul.
    """
    global _GRAPPES, _GRAPPES_A, _GRAPPES_CATALOGUE
    try:
        import time as _t

        from titanium.correlation import (
            CATALOGUE_REFRESH_MIN_S,
            TTL_GRAPPES_S,
            age_grappes,
            charger,
            charger_cache,
        )
        age = _t.time() - _GRAPPES_A
        connus = _GRAPPES_CATALOGUE | (set(_GRAPPES.par_actif) if _GRAPPES else set())
        if (_GRAPPES is not None and 0 <= age < TTL_GRAPPES_S
                and (set(catalogue) <= connus or age < CATALOGUE_REFRESH_MIN_S)):
            return

        if len(catalogue) < 20:
            # Pas de quoi recalculer. Un arbre deja sur disque reste une
            # photographie utilisable : les familles se deplacent en semaines,
            # pas en heures. Son age est trace a chaque adoption.
            if _GRAPPES is not None:
                return
            g = charger_cache()
            if g is None:
                print(f"  grappes : {len(catalogue)} actifs ouverts, trop peu "
                      "pour calculer, et aucun arbre sur disque", flush=True)
                return
            _GRAPPES = g
            _GRAPPES_A = _t.time()
            print(f"  grappes de correlation : {len(g.membres)} familles "
                  f"({g.methode}) — CACHE de "
                  f"{age_grappes(g) / 3600:.1f} h, recalcul impossible sous "
                  f"20 actifs ({len(catalogue)} ouverts)", flush=True)
            return

        g = charger(catalogue)
        if g.par_actif:
            _GRAPPES = g
            _GRAPPES_A = _t.time()
            _GRAPPES_CATALOGUE = set(catalogue)
            print(f"  grappes de correlation : {len(g.membres)} familles "
                  f"({g.methode})", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  grappes indisponibles : {type(exc).__name__}", flush=True)


def _place_dans_la_grappe(sym: str, risque_pct: float) -> tuple:
    """La grappe de correlation de cet actif accepte-t-elle ce risque ?

    Ne leve jamais et refuse par defaut si la photographie de risque n'est
    pas disponible. Cette porte est reservee au chemin PAPER/DEMO.
    """
    # Le calcul de l'arbre reste hors du chemin critique. Son absence est
    # toutefois une exposition inconnue, donc une entree doit attendre.
    if _GRAPPES is None:
        return False, "GRAPPES_INDISPONIBLES"
    symbole_normalise = str(sym).upper()
    try:
        if symbole_normalise not in _GRAPPES.par_actif:
            return False, f"GRAPPE_SYMBOLE_ABSENT: {symbole_normalise}"
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.correlation import place_disponible
        from titanium.data.mt5_vendor import account_snapshot

        # ⚠️ On lit l'arbre DEJA calcule. Appeler `charger()` ici
        # declencherait le calcul sur le chemin critique — 300 barres sur
        # 149 actifs, plusieurs minutes, juste avant d'envoyer un ordre.
        return place_disponible(sym, risque_pct, mt5, _GRAPPES,
                                account_snapshot().equity)
    except Exception as exc:  # noqa: BLE001
        return False, f"ERREUR_RISQUE_CORRELE: {type(exc).__name__}: {exc}"


def _revalider_grappe_apres_sizing(sym: str, budget) -> tuple:
    """Valide le risque reellement tradable, jamais l'intention de risque.

    Le lot minimum peut porter ``effective_pct`` au-dessus du ``target_pct``.
    Une valeur absente, nulle ou non finie signale un contrat de sizing casse
    et ferme la porte PAPER/DEMO.
    """
    try:
        if not bool(budget.tradable):
            return False, "BUDGET_NON_TRADABLE"
        risque_effectif = float(budget.effective_pct)
        if not math.isfinite(risque_effectif) or risque_effectif <= 0:
            return False, "RISQUE_EFFECTIF_INVALIDE"
    except Exception as exc:  # noqa: BLE001
        return False, f"RISQUE_EFFECTIF_ILLISIBLE: {type(exc).__name__}: {exc}"
    try:
        return _place_dans_la_grappe(sym, risque_effectif)
    except Exception as exc:  # noqa: BLE001
        return False, f"ERREUR_REVALIDATION_GRAPPE: {type(exc).__name__}: {exc}"


CANDIDATS_GRAPPE = RACINE / "results" / "candidats_grappe.ndjson"
_CANDIDATS_GRAPPE_VUS: set[str] = set()


def _journaliser_grappes(candidats, equity: float) -> int:
    """Journalise une fois par barre la grappe et son risque deja engage.

    Mesure additive uniquement : elle ne change ni l'ordre des candidats, ni
    les garde-fous, ni l'execution. Le risque propose est celui du RiskGate
    deterministe, avant l'avis asynchrone des analystes.
    """
    if not candidats:
        return 0
    try:
        import json

        from titanium.correlation import risque_par_grappe

        # L'absence de MetaTrader5 ne doit PAS effacer la mesure. L'import
        # etait inconditionnel et son ImportError etait avalee par le `except`
        # de fin : sur une machine sans le paquet, tout le journal de grappes
        # disparaissait en silence, y compris les champs qui n'ont besoin
        # d'aucun courtier. Constate le 18/08/2026 sur le runner Linux de la
        # CI, ou la fonction rendait 0 au lieu de 1.
        #
        # On degrade au lieu de disparaitre : sans terminal, le risque engage
        # par grappe est simplement inconnu -- `risque_par_grappe` ne leve
        # jamais et rend {} -- mais la ligne est ecrite quand meme. Une mesure
        # qui ne bloque jamais la boucle ne doit pas non plus s'eteindre sans
        # le dire.
        risques = {}
        if _GRAPPES is not None:
            try:
                import MetaTrader5 as mt5  # noqa: N813
            except ImportError:
                mt5 = None
            risques = risque_par_grappe(mt5, _GRAPPES, equity)
        lignes = []
        for candidat in candidats:
            sym = candidat["sym"]
            feats = candidat["feats"]
            cle = cle_barre(sym, feats)
            if not cle or cle in _CANDIDATS_GRAPPE_VUS:
                continue
            _CANDIDATS_GRAPPE_VUS.add(cle)
            dec, out = candidat["dec"], candidat["out"]
            grappe = (_GRAPPES.grappe_de(sym)
                      if _GRAPPES is not None else "indisponible")
            membres = (_GRAPPES.membres.get(grappe, [])
                       if _GRAPPES is not None else [])
            risque_propose = ((float(out.risk_money or 0.0) / equity * 100.0)
                              if equity > 0 else 0.0)
            lignes.append({
                "at": datetime.now(timezone.utc).isoformat(),
                "candidate_key": cle,
                "symbol": sym,
                "side": int(out.side or 0),
                "setup_family": str(getattr(dec, "setup_family", "") or ""),
                "cluster": grappe,
                "cluster_members": list(membres),
                "cluster_risk_engaged_pct": round(float(risques.get(grappe, 0.0)), 6),
                "proposed_risk_pct": round(risque_propose, 6),
                "support_pillars": int(candidat.get("support", 0) or 0),
                "rank": round(float(candidat.get("rank", 0.0) or 0.0), 6),
            })
        if not lignes:
            return 0
        CANDIDATS_GRAPPE.parent.mkdir(parents=True, exist_ok=True)
        with CANDIDATS_GRAPPE.open("a", encoding="utf-8") as flux:
            for ligne in lignes:
                flux.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return len(lignes)
    except Exception:  # noqa: BLE001 -- une mesure ne bloque jamais la boucle
        return 0


def _derive_depuis_decision(sym: str, feats: dict, out) -> float | None:
    """Ecart entre le prix de decision et le prix courant, en R.

    Rend None si la mesure est impossible — on n'invente pas un refus sur
    une donnee absente.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813

        stop = float(out.stop_distance or 0.0)
        decision = float((feats.get("_trace") or {}).get("price") or 0.0)
        if stop <= 0 or decision <= 0:
            return None
        t = mt5.symbol_info_tick(sym)
        if t is None:
            return None
        courant = float(t.ask if (out.side or 0) > 0 else t.bid)
        if courant <= 0:
            return None
        # Valeur absolue : une derive FAVORABLE est tout aussi disqualifiante.
        # Le prix parti dans notre sens a mange le gain, celui parti contre a
        # invalide le niveau. Dans les deux cas ce n'est plus le meme trade.
        return abs(courant - decision) / stop
    except Exception:  # noqa: BLE001
        return None


def _risque_engage_pct(mt5, equity: float) -> float:
    """Risque cumule des positions ouvertes, en % de l'equite.

    Mesure la perte si TOUTES les positions touchaient leur stop en meme
    temps. C'est le seul chiffre qui borne reellement l'exposition : compter
    les positions ne dit rien tant que leurs tailles different.

    Une position sans stop compte pour son plafond nominal — sans quoi elle
    passerait pour gratuite alors qu'elle est la plus dangereuse.
    """
    if equity <= 0:
        return 0.0
    total = 0.0
    try:
        for p in (mt5.positions_get() or []):
            spec = mt5.symbol_info(p.symbol)
            if spec is None or not spec.trade_tick_size:
                continue
            if not p.sl:
                total += 2.0          # inconnu : on suppose le plafond dur
                continue
            dist = abs(p.price_open - p.sl)
            perte = (dist / spec.trade_tick_size) * spec.trade_tick_value * p.volume
            total += perte / equity * 100.0
    except Exception:  # noqa: BLE001
        return 0.0
    return total


def _risque_exposition_pct(mt5, exposition, equity: float, *, side: int) -> float | None:
    """Risque restant d'une position/limite pour le budget de son panier.

    Un SL déjà au breakeven ou en gain vaut zéro risque restant. Une donnée
    manquante rend ``None`` afin que l'appelant bloque tout nouveau renfort.
    """
    try:
        if equity <= 0 or side not in (-1, 1):
            return None
        entry = float(getattr(exposition, "price_open", 0.0) or 0.0)
        sl = float(getattr(exposition, "sl", 0.0) or 0.0)
        volume = float(
            getattr(exposition, "volume", 0.0)
            or getattr(exposition, "volume_current", 0.0)
            or getattr(exposition, "volume_initial", 0.0)
            or 0.0
        )
        if min(entry, sl, volume) <= 0:
            return None
        spec = mt5.symbol_info(exposition.symbol)
        tick_size = float(getattr(spec, "trade_tick_size", 0.0) or 0.0)
        tick_value = float(
            getattr(spec, "trade_tick_value_loss", 0.0)
            or getattr(spec, "trade_tick_value", 0.0)
            or 0.0
        )
        if tick_size <= 0 or tick_value <= 0:
            return None
        distance_risque = max(0.0, side * (entry - sl))
        perte = distance_risque / tick_size * tick_value * volume
        return perte / equity * 100.0
    except (AttributeError, TypeError, ValueError):
        return None


def _demander_fenetres(candidats) -> None:
    """Demande a l'EA compagnon d'ouvrir les graphiques utiles.

    L'API Python ne sait pas ouvrir un graphique — ChartOpen n'existe que
    cote MQL5. V14 ecrit donc la liste voulue, et titanium_charts.mq5 fait
    converger le terminal. Ne leve jamais : l'affichage n'est pas critique.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.bridge.mt5_charts import (
            demander_fenetres,
            symboles_actifs,
        )
        from titanium.bridge.mt5_zones import dossier_mql5_files

        dossier = dossier_mql5_files()
        if dossier is None:
            return
        # Seules les positions OUVERTES méritent une fenêtre. Un graphique
        # d'actif clos occupe une place que la position suivante réclamera —
        # l'EA ferme donc ce qui disparaît de la liste. On ne complète PLUS
        # avec les candidats : sinon les huit places restaient prises par des
        # actifs simplement surveillés, et une nouvelle position n'obtenait
        # jamais son graphique.
        demander_fenetres(symboles_actifs(mt5, []), dossier)
    except Exception:  # noqa: BLE001
        pass


AVIS_DEMANDES = RACINE / "results" / "avis_demandes.ndjson"
AVIS_RENDUS = RACINE / "results" / "avis_rendus.ndjson"
POSITION_REVIEW_REQUESTS = RACINE / "results" / "position_review_requests.ndjson"
POSITION_REVIEW_VERDICTS = RACINE / "results" / "position_review_verdicts.ndjson"
NOYAU_CENTRAL = CentralMemory(
    RACINE / "results" / "organism_memory.sqlite3",
    RACINE / "results" / "organism_alerts.ndjson",
)
MARKET_JEPA = MarketJepaRuntime(
    RACINE / "results" / "market_jepa" / "model.json",
)

try:
    from titanium.live_memory import ReplayEdgeMemory
    _MEMOIRE_LIVE = ReplayEdgeMemory(RACINE)
except Exception:  # noqa: BLE001
    _MEMOIRE_LIVE = None


def _avis_pour(sym: str, side: int,
               identity: DecisionIdentity,
               context_key: str = "") -> tuple[float, str]:
    """Relit uniquement une politique Hermes fraiche, sans appel reseau."""
    from titanium.organism.contracts import (
        CORTEX_DECISION_MODEL_VERSION,
        CORTEX_DECISION_PRODUCER,
    )

    if not context_key:
        return 0.5, "CORTEX_CONTEXT_MISSING"
    try:
        proposal, code = NOYAU_CENTRAL.policy_for(
            identity, context_key,
            expected_decision_model=CORTEX_DECISION_MODEL_VERSION,
            expected_producer=CORTEX_DECISION_PRODUCER,
        )
        if proposal is None:
            return 0.5, code
        if int(proposal.get("side", 0) or 0) != int(side):
            return 0.2, "BRAIN_SIDE_MISMATCH"
        return float(proposal.get("confidence", 0.5)), code
    except Exception:  # noqa: BLE001
        return 0.5, "avis indisponible"


def _contexte_cortex(sym: str, feats: dict, side: int) -> str:
    """Contexte de politique Hermès, borné à l'horizon réellement analysé."""
    base = _contexte_exact(sym, feats, side)
    trace = feats.get("_trace") or {}
    timeframe = str(trace.get("timeframe") or "").upper()
    if not timeframe:
        return ""
    higher = str(trace.get("higher_timeframe") or HTF).upper()
    return f"{base}|tf={timeframe}>{higher}"


def _garde_intelligente(sym: str, side: int, feats: dict,
                        identity: DecisionIdentity) -> tuple[bool, str]:
    """Autorite Hermes locale et scellee, puis gardes de mesure V4.

    Aucun appel LLM n'est effectue ici. Seule une
    politique Hermès fraîche pour le même contexte permet de poursuivre.
    ``WAIT``, ``BLOCK``, absence, panne ou incohérence restent fail-closed.
    """
    from titanium.organism.contracts import (
        CORTEX_DECISION_MODEL_VERSION,
        CORTEX_DECISION_PRODUCER,
    )

    contexte = _contexte_exact(sym, feats, side)
    contexte_cortex = _contexte_cortex(sym, feats, side)
    if not contexte_cortex:
        return False, "CORTEX_CONTEXT_MISSING"
    if _MEMOIRE_LIVE is None:
        return False, "memoire live indisponible"
    verdict = _MEMOIRE_LIVE.verdict(sym, contexte)
    _MEMOIRE_LIVE.record(sym, contexte, verdict)
    if verdict.action != "ALLOW":
        return False, (f"memoire {verdict.action}: {verdict.reason}; "
                       f"n={verdict.samples}, E={verdict.expectancy_r:+.3f}R, "
                       f"PF={verdict.profit_factor:.2f}")
    proposal, code = NOYAU_CENTRAL.policy_for(
        identity, contexte_cortex,
        expected_decision_model=CORTEX_DECISION_MODEL_VERSION,
        expected_producer=CORTEX_DECISION_PRODUCER,
    )
    gate_source = "politique Hermes"
    if proposal is None:
        NOYAU_CENTRAL.alert(code, identity, "aucune politique Hermes fraiche")
        return False, f"noyau {code}: autorisation Hermes absente"
    action = str(proposal.get("action", "WAIT")).upper()
    if action not in {"ALLOW", "WAIT", "BLOCK"}:
        NOYAU_CENTRAL.alert("BRAIN_ACTION_INVALID", identity, action)
        return False, "noyau BRAIN_ACTION_INVALID"
    reason = str(proposal.get("summary", ""))[:240]
    if action != "ALLOW":
        NOYAU_CENTRAL.alert(f"BRAIN_{action}", identity, reason)
        return False, f"fondamental {action}: {reason}"
    try:
        NOYAU_CENTRAL.append("engine.gate", identity.decision_ref, sym, {
            **identity.to_dict(), "action": "ALLOW",
            "evidence_digest": proposal.get("evidence_digest", ""),
            "gate_source": gate_source,
            "policy_ref": proposal.get("policy_ref", ""),
        })
    except Exception as exc:  # noqa: BLE001 - aucune execution sans trace
        NOYAU_CENTRAL.alert("CENTRAL_GATE_WRITE_FAILED", identity,
                            type(exc).__name__)
        return False, "noyau CENTRAL_GATE_WRITE_FAILED"
    return True, (f"memoire ALLOW: n={verdict.samples}, "
                  f"E={verdict.expectancy_r:+.3f}R, PF={verdict.profit_factor:.2f}; "
                  f"cortex {code} ALLOW: {reason}")


def _autorisation_et_conviction(sym: str, feats: dict, out, decision, cfg,
                                ltf=None) -> tuple[bool, float, str]:
    """Autorise le cortex ou applique le dimensionnement déterministe."""
    if not ACTIVER_CORTEX:
        return True, 0.5, "CORTEX_DESACTIVE"

    identity = _demander_avis(sym, feats, out, decision, cfg, ltf=ltf)
    if identity is None:
        return False, 0.5, "CENTRAL_MEMORY"
    intelligence_ok, motif_intelligence = _garde_intelligente(
        sym, out.side, feats, identity)
    if not intelligence_ok:
        return False, 0.5, motif_intelligence
    conviction, motif_avis = _avis_pour(
        sym, out.side, identity, _contexte_cortex(sym, feats, out.side))
    return True, conviction, motif_avis


def _sante_resumee() -> str:
    """Resume d'auscultation, pour que l'analyste sache sur quoi il juge."""
    try:
        from titanium.web.medecin import bilan
        b = bilan()
        return f"{b.get('verdict','?')} — {b.get('resume','')}"
    except Exception:  # noqa: BLE001
        return ""


def _demander_avis(sym: str, feats: dict, out, decision,
                   cfg, ltf=None) -> DecisionIdentity | None:
    """Depose la lecture deterministe pour les analystes. Une ecriture,
    puis on continue — la deliberation se fait dans un autre processus."""
    try:
        from titanium.avis import Demande, deposer
        if ltf is not None:
            attach_market_jepa(sym, feats, ltf, MARKET_JEPA)
        trace = feats.get("_trace") or {}
        context_key = _contexte_cortex(sym, feats, out.side)
        if not context_key:
            return None
        indicators = dict(trace.get("indicators") or {})
        if _MEMOIRE_LIVE is not None:
            edge = _MEMOIRE_LIVE.verdict(sym, _contexte_exact(sym, feats, out.side))
            indicators.update(edge_samples=edge.samples, edge_expectancy_r=edge.expectancy_r,
                              edge_profit_factor=edge.profit_factor)
        demande = Demande(
            symbol=sym, side=out.side,
            verdict=decision.verdict, code=decision.code,
            piliers=int(getattr(decision, "support_passed", 0)),
            famille=getattr(decision, "setup_family", ""),
            prix=float(trace.get("price") or 0.0),
            stop_distance=float(out.stop_distance or 0.0),
            rr=cfg.rr_ratio,
            bar_time=str(trace.get("bar_time") or ""),
            engine_context=context_key,
            indicateurs=indicators,
            sante=_sante_resumee(),
            demande_a=datetime.now(timezone.utc).isoformat(),
        )
        identity = demande.sceller()
        payload = demande.to_dict()
        if not deposer(demande, AVIS_DEMANDES):
            NOYAU_CENTRAL.alert("BRAIN_REQUEST_FILE_FAILED", identity)
        NOYAU_CENTRAL.record_request(identity, payload)
        return identity
    except Exception:  # noqa: BLE001
        return None


def _contexte_exact(sym: str, feats: dict, side: int) -> str:
    """Cle de contexte batie sur les portes. Ne leve jamais."""
    try:
        from titanium.edge import context_from_decision
        from titanium.gates import confluence_gate
        d = confluence_gate.evaluate(feats, side=side)
        return context_from_decision(sym, d).key()
    except Exception:  # noqa: BLE001
        return f"{sym}|?|?|0p"


def _stratification(sym: str, feats: dict, side: int) -> dict:
    """Champs que le protocole de promotion lira a la cloture.

    Figes a l'OUVERTURE : a la cloture, la porte ne verrait plus le meme
    marche et le mode aurait pu changer entre-temps.
    """
    try:
        from titanium.edge import asset_class_of
        from titanium.gates import confluence_gate
        d = confluence_gate.evaluate(feats, side=side)
        compte = ""
        try:
            import MetaTrader5 as mt5  # noqa: N813
            i = mt5.account_info()
            compte = str(i.login) if i else ""
        except Exception:  # noqa: BLE001
            pass
        return {
            "mode": getattr(d, "mode", "explore"),
            "quorum": int(getattr(d, "quorum", 0) or 0),
            "support_pillars": int(getattr(d, "support_passed", 0) or 0),
            "asset_class": asset_class_of(sym),
            "account": compte,
            "timeframe": str((feats.get("_trace") or {}).get("timeframe") or LTF),
            # Qui a fourni G5 : "formes" (motif de chandelier) ou
            # "displacement" (amplitude ICT, secours ajoute le 12/08/2026).
            # Sans ce champ au journal, on ne pourra jamais comparer
            # l'esperance des deux sources sur des RESULTATS -- seulement sur
            # des taux de passage, qui ne disent rien de la rentabilite.
            "candle_source": str(
                (feats.get("_trace") or {}).get("candle_source") or ""),
        }
    except Exception:  # noqa: BLE001
        return {}


def _niveaux_entree(feats: dict, *, entry: float, side: int, r: float) -> dict:
    """Niveaux structurels vus par le builder A LA DECISION, plus la distance
    entree->niveau en R -- instrumentation en avant demandee par Prime le
    18/08/2026 (hub, echange avec Claude sur le point d'entree).

    Fige a l'OUVERTURE, comme `_stratification` : a la cloture MT5 ne
    recalculera jamais des niveaux pour une barre deja passee, et le builder
    a pu changer de version entre-temps. Additif et sans effet sur la
    decision : aucune porte ne lit ce dict, il n'est journalise qu'avec le
    trade clos. Convention de signe alignee sur `fav_r` : positif = le niveau
    est du cote favorable de l'entree.
    """
    trace = (feats.get("_trace") or {}) if isinstance(feats, dict) else {}
    if r <= 0 or not math.isfinite(r) or side not in (-1, 1):
        return {}

    def _dist(niveau) -> float | None:
        try:
            v = float(niveau)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):
            return None
        return round((v - entry) / r * side, 4)

    sr_level = trace.get("sr_level")
    vpoc = trace.get("vpoc")
    ote_zone = trace.get("ote_zone")
    fvg_open = trace.get("fvg_open") or []

    dist_ote_r = None
    if isinstance(ote_zone, (list, tuple)) and len(ote_zone) == 2:
        bornes = [d for d in (_dist(ote_zone[0]), _dist(ote_zone[1])) if d is not None]
        if bornes:
            dist_ote_r = min(bornes, key=abs)

    dist_fvg_r = None
    fvg_distances = []
    for zone in fvg_open:
        if isinstance(zone, (list, tuple)) and len(zone) == 2:
            fvg_distances.extend(
                d for d in (_dist(zone[0]), _dist(zone[1])) if d is not None)
    if fvg_distances:
        dist_fvg_r = min(fvg_distances, key=abs)

    return {
        "sr_level": sr_level, "vpoc": vpoc,
        "ote_zone": list(ote_zone) if isinstance(ote_zone, (list, tuple)) else None,
        "fvg_open": [list(z) for z in fvg_open
                     if isinstance(z, (list, tuple)) and len(z) == 2],
        "dist_sr_r": _dist(sr_level),
        "dist_vpoc_r": _dist(vpoc),
        "dist_ote_r": dist_ote_r,
        "dist_fvg_r": dist_fvg_r,
    }


def _observer_prod(sym: str, feats: dict, verdict: str) -> None:
    """Observation seule. Ne leve jamais, ne change aucune decision."""
    try:
        from titanium.shadow import observer
        observer(feats, sym, verdict, RACINE / "results" / "shadow_prod.ndjson")
    except Exception:  # noqa: BLE001
        pass


def _attacher_contexte(ticket, sym: str, feats: dict, out, res,
                       risque_devise: float = 0.0,
                       spread_r: float | None = None,
                       policy_identity: dict[str, str] | None = None,
                       decision_id: str = "", decision_at: str = "") -> None:
    """Ecrit le contexte d'entree dans l'etat suivi, des l'envoi de l'ordre.

    Sans cela, `position_manager` decouvrira le ticket au tour suivant et ne
    pourra reconstituer ni la cle de contexte ni le panel d'indicateurs — la
    mesure d'edge resterait aveugle. Ne leve jamais.
    """
    if not ticket:
        return
    try:
        from datetime import datetime, timezone

        from titanium.execution.position_manager import (
            TrackedState,
            load_state,
            save_state,
        )

        chemin = RACINE / "results" / "positions.json"
        etat = load_state(chemin)
        r = abs((res.price or 0.0) - (res.sl or 0.0))
        r_eff = r if r > 0 else (out.stop_distance or 0.0)
        identity = policy_identity or {}
        etat[str(ticket)] = TrackedState(
            r=r_eff,
            symbol=sym, side=out.side,
            entry=res.price or 0.0,
            sl_initial=res.sl or 0.0, tp_initial=res.tp or 0.0,
            # Contexte EXACT, construit depuis la décision de porte et non
            # depuis `strengths` : ce dernier mesure la non-nullité, la porte
            # mesure l'alignement. Le sur-comptage rangeait un setup à
            # 2 piliers parmi les « 4p ».
            context_key=_contexte_exact(sym, feats, out.side),
            contre_tendance=bool(getattr(out, "contre_tendance", False)),
            indicators=dict((feats.get("_trace") or {}).get("indicators") or {}),
            ts_open=decision_at or datetime.now(timezone.utc).isoformat(),
            # Sert a convertir en R la commission et le swap que MT5 rend en
            # devise. Sans lui, ces frais seraient journalises a zero.
            risque_devise=float(risque_devise or 0.0),
            spread_r=(None if spread_r is None else float(spread_r)),
            spread_exact=False,
            entry_levels=_niveaux_entree(
                feats, entry=res.price or 0.0, side=out.side, r=r_eff),
            entry_atr=float((feats.get("_trace") or {}).get("atr") or 0.0),
            entry_policy=str(identity.get("entry_policy", "")),
            policy_epoch=str(identity.get("policy_epoch", "")),
            config_sha256=str(identity.get("config_sha256", "")),
            code_sha256=str(identity.get("code_sha256", "")),
            decision_id=decision_id,
            **_stratification(sym, feats, out.side),
        )
        save_state(chemin, etat)
    except Exception:  # noqa: BLE001 — l'observabilite ne casse jamais le trading
        pass


def _memoriser_contexte_limit(
    ticket, sym: str, feats: dict, out, res,
    *,
    risque_devise: float = 0.0,
    spread_r: float | None = None,
    policy_identity: dict[str, str] | None = None,
    decision_id: str = "",
    decision_at: str = "",
) -> tuple[bool, str]:
    """Conserve le contexte jusqu'au fill et rend une preuve exploitable."""
    if not ticket or not getattr(res, "expires_at", ""):
        return False, "TICKET_OU_EXPIRATION_ABSENT"
    try:
        from datetime import datetime, timezone

        from titanium.execution.pending_context import save_pending_context
        from titanium.execution.position_manager import TrackedState

        r = abs((res.price or 0.0) - (res.sl or 0.0))
        r_eff = r if r > 0 else (out.stop_distance or 0.0)
        identity = policy_identity or {}
        template = TrackedState(
            r=r_eff,
            symbol=sym, side=out.side,
            entry=res.price or 0.0,
            sl_initial=res.sl or 0.0, tp_initial=res.tp or 0.0,
            context_key=_contexte_exact(sym, feats, out.side),
            contre_tendance=bool(getattr(out, "contre_tendance", False)),
            indicators=dict((feats.get("_trace") or {}).get("indicators") or {}),
            ts_open=decision_at or datetime.now(timezone.utc).isoformat(),
            risque_devise=float(risque_devise or 0.0),
            spread_r=(None if spread_r is None else float(spread_r)),
            spread_exact=False,
            entry_levels=_niveaux_entree(
                feats, entry=res.price or 0.0, side=out.side, r=r_eff),
            entry_atr=float((feats.get("_trace") or {}).get("atr") or 0.0),
            limit_order_ticket=int(ticket),
            limit_planned_price=float(res.price or 0.0),
            limit_market_reference_price=float(
                getattr(res, "market_reference_price", 0.0) or 0.0),
            limit_target_saving_r=(
                float(getattr(res, "spread_saved_price", 0.0) or 0.0) / r
                if r > 0 else None
            ),
            entry_policy=str(identity.get("entry_policy", "")),
            policy_epoch=str(identity.get("policy_epoch", "")),
            config_sha256=str(identity.get("config_sha256", "")),
            code_sha256=str(identity.get("code_sha256", "")),
            decision_id=decision_id,
            **_stratification(sym, feats, out.side),
        )
        save_pending_context(
            RACINE / "results" / "pending_limits.json",
            order_ticket=int(ticket), symbol=sym, side=out.side,
            expires_at=res.expires_at, state=template,
        )
        return True, "SAVED"
    except Exception as exc:  # noqa: BLE001
        return False, f"ERROR_{type(exc).__name__.upper()}"


def _envoi_entree(mode: str | None = None):
    """Rend la fonction d'envoi d'ordre correspondant au mode d'entrée.

    Une seule ligne décide entre le marché et la limite passive. Elle vit ici,
    hors de ``tour``, pour être vérifiable sans MT5 ni balayage : le choix du
    type d'ordre est une décision de risque, elle ne doit pas dépendre d'un
    chemin de 400 lignes pour être prouvée.
    """
    from titanium.execution.limit_orders import place_limit_order
    from titanium.execution.mt5_executor import place_market_order

    return (place_limit_order if (mode or MODE_ENTREE) == "LIMITE"
            else place_market_order)


def tour(*, armer: bool, stats: dict, tracer: bool = True,
         tracer_defaut: str = "EURUSD") -> None:
    from titanium.data.mt5_vendor import (
        account_snapshot,
        ensure_symbol,
        get_rates,
        get_rates_cache,
    )
    from titanium.execution.mt5_executor import ExecutionPolicy
    from titanium.execution.pending_context import (
        append_limit_event,
        limit_lifecycle_summary,
        reconcile_pending_contexts,
    )
    from titanium.execution.position_manager import ManageParams, manage_once
    from titanium.features.builder import build_feats, risk_context_from
    from titanium.orchestrator import OrchestratorConfig, run_once
    from titanium.sizing import (
        MAX_COUT_SPREAD_PCT,
        budget_for,
        tradable_universe,
    )

    politique = ExecutionPolicy.from_config()
    compte = account_snapshot()

    # ── 1. L'univers suit le compte, pas une liste figée.
    #     `UNIVERS` vide ⇒ tout le catalogue. Le filtre de dimensionnement
    #     écarte ensuite ce que l'équité ne peut pas porter, donc élargir ne
    #     revient pas à prendre plus de risque : cela offre plus de choix
    #     à un nombre de créneaux inchangé.
    global _curseur
    catalogue = UNIVERS or univers_complet()
    catalogue_initial = len(catalogue)
    _compter_tunnel(stats, "flow", "catalogue", catalogue_initial)

    # ── Filtre de liquidité FX. Élargir l'univers à tout le catalogue a fait
    #    entrer 42 trades sur croisements et exotiques : −21.3 R, soit 73 %
    #    de la perte des 128 trades clos du 10 au 17/08/2026, pour un taux de
    #    réussite de 26 % et un coût moyen 37 % plus élevé que sur les
    #    majeures. Le même échantillon privé de ces actifs passe de −29.3 R
    #    à −8.0 R et de 43 % à 52 % de réussite, avec un tiers de trades en
    #    moins. Ce n'est PAS un réglage de stratégie : c'est la surface de
    #    balayage qu'on ramène aux marchés dont le spread laisse une chance
    #    au stop. Voir docs/RECALIBRAGE_20260817.md.
    from titanium.edge import fx_illiquide as _fx_illiquide

    ecartes = [s for s in catalogue if _fx_illiquide(s)]
    if ecartes:
        catalogue = [s for s in catalogue if s not in set(ecartes)]
        _compter_tunnel(stats, "flow", "fx_illiquides_ecartes", len(ecartes))

    # Rotation : on examine une tranche par tour, en repartant là où le tour
    # précédent s'était arrêté. Les positions ouvertes sont TOUJOURS incluses —
    # leur gestion ne doit pas attendre son tour de rotation.
    portees = []
    try:
        import MetaTrader5 as mt5  # noqa: N813
        portees = [p.symbol for p in (mt5.positions_get() or [])]
    except Exception:  # noqa: BLE001
        pass

    # ── Concentration crypto quand le reste dort.
    #     Le week-end, seule la crypto cote. Continuer à faire tourner la
    #     rotation sur 149 actifs ferait balayer 120 marchés morts pour
    #     n'examiner qu'une poignée de vivants — la crypto ne passerait
    #     qu'un tour sur six alors qu'elle est la seule à bouger.
    from titanium.edge import asset_class_of
    from titanium.sizing import marches_ouverts

    vivants = marches_ouverts(catalogue)
    hors_crypto = [s for s in catalogue
                   if asset_class_of(s) != "crypto" and vivants.get(s, True)]
    phase_crypto_weekend = False
    # Une cohorte explicite peut contenir volontairement moins de dix marches
    # non crypto. Ne pas la remplacer silencieusement par les seuls cryptos.
    if not UNIVERS and len(hors_crypto) < 10:
        cryptos = [s for s in catalogue if asset_class_of(s) == "crypto"]
        if cryptos:
            catalogue = cryptos
            phase_crypto_weekend = True
            print(f"    marchés fermés hors crypto — balayage concentré sur "
                  f"{len(cryptos)} actifs", flush=True)

    # ── Rotation pilotée par la hiérarchie mesurée quand elle existe :
    #    rang A chaque tour (espérance backtest positive + BTCUSD forcé),
    #    B un tour sur trois, C un tour sur dix. Sans fichier de sélection,
    #    rotation uniforme — le défaut sûr est de regarder plus, pas moins.
    global _TOUR
    _TOUR += 1
    from titanium.selection import a_balayer, lire as lire_selection
    sel = lire_selection(SELECTION_PATH)
    if sel.get("actifs"):
        base = a_balayer(sel, _TOUR, catalogue)
        rang_de = {a["symbol"]: a.get("rang", "C") for a in sel["actifs"]}
        # A d'abord : si le tour doit être coupé, ce sont les C qui sautent.
        base.sort(key=lambda x: {"A": 0, "B": 1, "C": 2}.get(rang_de.get(x, "C"), 2))
        tranche = base[:LOT_PAR_TOUR + 12]
    elif len(catalogue) > LOT_PAR_TOUR:
        deb = _curseur % len(catalogue)
        tranche = (catalogue + catalogue)[deb:deb + LOT_PAR_TOUR]
        _curseur = (deb + LOT_PAR_TOUR) % len(catalogue)
    else:
        tranche = list(catalogue)

    univers = _entry_universe(portees, tranche)
    _compter_tunnel(stats, "flow", "selectionnes", len(univers))
    budgets = tradable_universe(univers, compte.equity, timeframe=LTF)
    tradables = [s for s, b in budgets.items() if b.tradable]
    _compter_tunnel(stats, "flow", "portables", len(tradables))
    _compter_tunnel(stats, "flow", "non_portables", len(univers) - len(tradables))
    for budget in budgets.values():
        if not budget.tradable:
            _compter_tunnel(
                stats, "portability_refusal", _code_portabilite(budget.reason))

    print(f"[{horodate()}] equity {compte.equity:.2f} {compte.currency} · "
          f"{len(tradables)}/{len(univers)} portables "
          f"(tranche {_curseur}/{len(catalogue)}) · "
          f"exécution {'ARMÉE' if (armer and politique.enabled) else 'désarmée'}",
          flush=True)
    battre(stats, armer=armer and politique.enabled, equity=compte.equity,
           portables=len(tradables))
    _demander_fenetres(tradables)
    _ZONES.clear()

    # ── Arbre de corrélation, calculé ici et JAMAIS pendant un envoi.
    #    Sur les actifs RÉELLEMENT jouables : passer le catalogue entier y
    #    injecte des instruments éteints dont les séries ne s'alignent pas,
    #    l'intersection des dates s'effondre, et le résultat était
    #    148 familles pour 149 actifs — un garde-fou qui ne borne rien.
    rafraichir_grappes(_tradables_connus(tradables))

    # ── 2. Gestion des positions déjà ouvertes, avant d'en ouvrir d'autres.
    ouvertes = 0
    limites_en_attente = 0
    par_symbole: dict[str, int] = {}
    expositions_par_symbole: dict[str, list[tuple[int, float]]] = {}
    risque_par_symbole: dict[str, float] = {}
    gestion_saine = True
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.data.mt5_vendor import mt5_lock, mt5_session

        etat = Path(RACINE / "results" / "positions.json")
        with mt5_lock, mt5_session():
            positions_courantes = mt5.positions_get()
            if positions_courantes is None:
                raise RuntimeError(f"positions MT5 indisponibles: {mt5.last_error()}")
            for p in positions_courantes:
                if int(getattr(p, "magic", 0) or 0) != politique.magic:
                    continue
                ouvertes += 1
                par_symbole[p.symbol] = par_symbole.get(p.symbol, 0) + 1
                side = (1 if int(getattr(p, "type", -1))
                        == mt5.POSITION_TYPE_BUY else -1)
                expositions_par_symbole.setdefault(p.symbol, []).append(
                    (side, float(getattr(p, "price_open", 0.0) or 0.0))
                )
                mesure_risque = _risque_exposition_pct(
                    mt5, p, compte.equity, side=side,
                )
                risque_par_symbole[p.symbol] = (
                    MAX_RISQUE_PANIER_PCT
                    if mesure_risque is None
                    else risque_par_symbole.get(p.symbol, 0.0) + mesure_risque
                )
            # Une limite en attente réserve déjà un créneau et du risque.
            # L'ignorer permettrait d'empiler des ordres qui se
            # transformeraient tous en positions au même mouvement.
            ordres_courants = mt5.orders_get()
            if ordres_courants is None and armer and politique.enabled:
                raise RuntimeError(f"ordres MT5 indisponibles: {mt5.last_error()}")
            for ordre in (ordres_courants or []):
                if int(getattr(ordre, "magic", 0) or 0) != politique.magic:
                    continue
                ouvertes += 1
                limites_en_attente += 1
                par_symbole[ordre.symbol] = par_symbole.get(ordre.symbol, 0) + 1
                achats = {
                    mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP,
                    mt5.ORDER_TYPE_BUY_STOP_LIMIT,
                }
                ventes = {
                    mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP,
                    mt5.ORDER_TYPE_SELL_STOP_LIMIT,
                }
                type_ordre = int(getattr(ordre, "type", -1))
                side = (1 if type_ordre in achats
                        else (-1 if type_ordre in ventes else 0))
                expositions_par_symbole.setdefault(ordre.symbol, []).append(
                    (side, float(getattr(ordre, "price_open", 0.0) or 0.0))
                )
                mesure_risque = _risque_exposition_pct(
                    mt5, ordre, compte.equity, side=side,
                )
                risque_par_symbole[ordre.symbol] = (
                    MAX_RISQUE_PANIER_PCT
                    if mesure_risque is None
                    else risque_par_symbole.get(ordre.symbol, 0.0) + mesure_risque
                )
            adoption = reconcile_pending_contexts(
                mt5, magic=politique.magic, state_path=etat,
                pending_path=RACINE / "results" / "pending_limits.json",
                lifecycle_path=RACINE / "results" / "limit_lifecycle.ndjson",
                positions=positions_courantes,
            )
            if adoption.get("adopted"):
                stats["limites_executees"] = int(
                    stats.get("limites_executees", 0) or 0
                ) + int(adoption["adopted"])
                print(f"    limites exécutées : {adoption['adopted']} contexte(s) "
                      "rattaché(s)", flush=True)
            if adoption.get("expired"):
                stats["limites_expirees"] = int(
                    stats.get("limites_expirees", 0) or 0
                ) + int(adoption["expired"])
                print(f"    limites expirées : {adoption['expired']} contexte(s) "
                      "purgé(s)", flush=True)
            if adoption.get("canceled"):
                stats["limites_annulees"] = int(
                    stats.get("limites_annulees", 0) or 0
                ) + int(adoption["canceled"])
                print(f"    limites annulees : {adoption['canceled']} contexte(s) "
                      "purge(s)", flush=True)
            if adoption.get("unknown"):
                stats["limites_issue_inconnue"] = int(
                    stats.get("limites_issue_inconnue", 0) or 0
                ) + int(adoption["unknown"])
                _compter_tunnel(
                    stats, "limit_lifecycle_failure", "ISSUE_INCONNUE")
            if adoption.get("events_written"):
                stats["limit_lifecycle_events"] = int(
                    stats.get("limit_lifecycle_events", 0) or 0
                ) + int(adoption["events_written"])
            if adoption.get("event_failures"):
                _compter_tunnel(
                    stats, "limit_lifecycle_failure", "RECONCILIATION")
            r = manage_once(
                mt5,
                policy=politique,
                params=ManageParams.from_config(),
                state_path=etat,
                account=compte,
                journal_path=RACINE / "results" / "trades.ndjson",
                manage_stops=MODIFIER_STOPS_EXISTANTS,
                manage_trailing=ACTIVER_TRAILING,
                manage_exits=GERER_SORTIES_ADAPTATIVES,
                sentiment_request_path=POSITION_REVIEW_REQUESTS,
                sentiment_verdict_path=POSITION_REVIEW_VERDICTS,
            )
            stats["journal_coverage"] = _journal_coverage(
                r.get("history_recovery"),
            )
            deplaces = int(r.get("moved", 0) or 0)
            sorties = int(r.get("exit_sent", 0) or 0)
            stats["breakeven_deplaces"] = int(
                stats.get("breakeven_deplaces", 0) or 0
            ) + deplaces
            stats["sorties_adaptatives"] = int(
                stats.get("sorties_adaptatives", 0) or 0
            ) + sorties
            stats["sorties_peur_glm"] = int(
                stats.get("sorties_peur_glm", 0) or 0
            ) + int(r.get("fear_exit_sent", 0) or 0)
            stats["sorties_micro_panier"] = int(
                stats.get("sorties_micro_panier", 0) or 0
            ) + int(r.get("basket_exit_sent", 0) or 0)
            stats["sentiment_positions"] = dict(r.get("sentiment") or {})
            if deplaces or sorties:
                print(
                    f"    gestion : {deplaces} BE déplacé(s), "
                    f"{sorties} sortie(s) adaptative(s) demandée(s)",
                    flush=True,
                )
                for d in r.get("details", []):
                    print(f"      {d}", flush=True)
            if r.get("reason"):
                gestion_saine = False
                print(f"    gestion fail-closed : {r['reason']}", flush=True)
                for d in r.get("details", []):
                    print(f"      {d}", flush=True)
            # After protective management: evidence failure must never disable exits.
            try:
                trace = reconcile_recorded(
                    mt5, account=compte, path=RACINE / "results" / "execution_ledger.sqlite3",
                )
            except Exception as exc:  # keep protections active, but no new entry
                trace = {"status": "WAIT", "reason": type(exc).__name__}
            stats["execution_trace"] = trace
            if trace["status"] == "WAIT":
                gestion_saine = False
                print("    execution incertaine : reconciliation requise, nouvelles entrees WAIT",
                      flush=True)
    except Exception as exc:  # noqa: BLE001
        gestion_saine = False
        print(f"    gestion indisponible : {type(exc).__name__}", flush=True)

    if not gestion_saine:
        print("    mesure ou positions indisponibles — aucun nouvel ordre", flush=True)
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))
        return

    # Protective management above remains active even when new entries are
    # cut. Missing, invalid, or excessive live losses fail closed here.
    loss_guard = evaluate_live_loss_guard(
        RACINE / "results" / "trades.ndjson",
        account=str(compte.login),
        not_before=DEMO_COHORT_START_UTC,
    )
    loss_guard = persist_live_loss_quarantine(
        loss_guard,
        path=(RACINE / "data" / "runtime" / "live_loss_quarantine"
              / f"{compte.login}.json"),
        account=str(compte.login),
    )
    stats["live_loss_guard"] = loss_guard.to_dict()
    entry_blocked = loss_guard.action != "ALLOW"
    entry_block_reason = (
        f"{loss_guard.action}/{loss_guard.reason}" if entry_blocked else ""
    )
    if entry_blocked:
        print(
            "    coupe-circuit pertes "
            f"{loss_guard.action}/{loss_guard.reason} "
            f"(jour {loss_guard.daily_net_r:+.2f} R, "
            f"7j {loss_guard.rolling_net_r:+.2f} R) - aucune nouvelle entree",
            flush=True,
        )
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))

    risque_engage = 0.0
    try:
        import MetaTrader5 as mt5  # noqa: N813
        risque_engage = _risque_engage_pct(mt5, compte.equity)
    except Exception:  # noqa: BLE001
        pass

    plafond_atteint = MAX_POSITIONS > 0 and ouvertes >= MAX_POSITIONS
    if plafond_atteint or risque_engage >= MAX_RISQUE_CUMULE_PCT:
        motif = ("plafond de positions" if plafond_atteint
                 else f"budget de risque ({risque_engage:.1f} %)")
        print(f"    {ouvertes} positions · risque engagé {risque_engage:.1f} % — "
              f"{motif} atteint, aucun nouvel ordre", flush=True)
        entry_blocked = True
        if not entry_block_reason:
            entry_block_reason = motif
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))

    # ── 3. Balayage.
    # R:R porté de 2.0 à 3.0 — seul réglage que le testeur natif ait validé
    # HORS échantillon, et sur deux actifs indépendants : XAUUSD +0.294 →
    # +0.407 R, ETHUSD +0.098 → +0.192 R. On s'arrête à 3.0 plutôt qu'au 3.5
    # mesuré comme optimal : l'écart 3.0→3.5 tient sur moins de trades, et
    # 4.0 décroche déjà nettement sur ETHUSD. Marge volontaire du côté sûr.
    # ⚠️ R:R ramené de 3.0 à 2.0. Le 3.0 venait d'un forward sur DEUX actifs
    #    (XAUUSD, ETHUSD) et je l'ai généralisé à 149 — la faute même que le
    #    forward sert à éviter. Sur une journée de catalogue complet :
    #    17 sorties sur 18 au stop, un seul TP touché. Avec un objectif à
    #    4.5×ATR et un stop à 1.5×ATR, le prix touche le stop bien avant.
    #    On revient au réglage mesuré sur l'univers réel, et on ne le
    #    rebougera qu'après mesure SUR CET univers.
    cfg = OrchestratorConfig(require_edge=False, deliberate=False,
                             execute=False, rr_ratio=2.0)

    # ── PHASE 1 — tout évaluer, ne rien envoyer.
    #    Envoyer au fil de la rotation donnait les créneaux aux premiers
    #    venus : un S=2 médiocre prenait la place qu'un S=3 aurait réclamée
    #    deux évaluations plus tard. Huit créneaux pour ~150 actifs : ils
    #    doivent aller aux setups les plus FORTS du tour, pas aux plus
    #    rapides. C'est le même capital, mieux placé.
    from titanium.echelle import cout_relatif_stop
    from titanium.gates import confluence_gate as _cg
    from titanium.selection import barres_pour

    candidats = []
    for sym in tradables:
        if _stop:
            return
        # La profondeur d'historique suit le rang : un actif de rang A
        # obtient plus de barres, donc des niveaux de structure calculés
        # sur une histoire plus longue et moins sensibles au bruit.
        n_barres = barres_pour(sel, sym, BARRES) if sel else BARRES
        unite_budget = getattr(budgets.get(sym), "timeframe", LTF) or LTF
        haute_budget = "D1" if unite_budget == "H4" else HTF
        paires = _echelles_a_balayer(
            sym, unite_budget, haute_budget,
            crypto_weekend=phase_crypto_weekend,
        )
        for unite, haute in paires:
            if _stop:
                return
            try:
                ltf_rates = get_rates(sym, unite, n_barres)
                htf_rates = get_rates_cache(sym, haute, BARRES)
                # ⚠️ SANS le panel d'indicateurs. Mesuré : 392 ms avec, 30 ms
                # sans — le panel pèse 92 % du coût d'un actif. Or AUCUNE
                # porte ne le lit : il ne sert qu'à l'affichage MT5, au brief
                # des analystes et au contexte figé, tous sur le chemin ENTER.
                # On le recalcule donc seulement pour les setups qui entrent.
                # La crypto cote en continu : pas de blocage week-end.
                feats = build_feats(
                    ltf_rates, htf_rates, with_indicators=False,
                    marche_continu=asset_class_of(sym) == "crypto",
                )
                _marquer_echelle(feats, unite, haute)
            except Exception as exc:  # noqa: BLE001
                # ⚠️ NE JAMAIS avaler en silence. Un `continue` muet a masqué
                # un arrêt TOTAL du balayage pendant une heure le 07/08/2026.
                stats["illisibles"] = stats.get("illisibles", 0) + 1
                if stats["illisibles"] <= 3 or stats["illisibles"] % 25 == 0:
                    print(f"    {sym:10} {unite}/{haute} illisible : "
                          f"{type(exc).__name__}: {str(exc)[:70]}", flush=True)
                _compter_tunnel(stats, "features", "ILLISIBLE")
                continue

            _compter_tunnel(stats, "features", "LISIBLE")
            ctx = risk_context_from(feats, equity=compte.equity, risk_pct=1.0)
            out = run_once(sym, feats, ctx, config=cfg)
            stats["evalues"] += 1
            dec = _cg.evaluate(feats, require_edge=cfg.require_edge)
            _compter_tunnel(stats, "support_passed", f"S{dec.support_passed}")
            for gate in dec.gates:
                if not gate.passed:
                    _compter_tunnel(stats, "pillar_missing", gate.code or gate.name)
            _compter_tunnel(stats, "gate_verdict", out.gate_verdict)
            _compter_tunnel(stats, "gate_code", out.gate_code or out.reason)
            _observer_prod(sym, feats, out.gate_verdict)

            if tracer and (not phase_crypto_weekend or unite == unite_budget):
                _tracer_zones(sym, feats, out, cfg)
            # Le flux Shadow n'est plus alimente. La memoire V4 live est
            # consultee uniquement pour les entrees candidates.
            if out.gate_verdict != "ENTER":
                continue
            stats["enter"] += 1

            # Un signal sur M1/M5 peut etre techniquement propre tout en etant
            # mathematiquement detruit par le spread. On l'ecarte avant le
            # cortex : Hermès ne doit pas depenser du temps sur l'injouable.
            try:
                spec = ensure_symbol(sym)
                cost = cout_relatif_stop(spec, out.stop_distance or 0.0)
            except Exception:  # noqa: BLE001
                cost = math.inf
            if cost > MAX_COUT_SPREAD_PCT:
                _compter_tunnel(stats, "multitimeframe", "COUT_SPREAD")
                _refus(stats, "COUT_SPREAD", sym, "cout excessif avant cortex",
                       stage="multitimeframe", timeframe=unite)
                continue

            # Le setup entre : MAINTENANT le panel vaut son coût.
            try:
                feats = build_feats(
                    ltf_rates, htf_rates, with_indicators=True,
                    marche_continu=asset_class_of(sym) == "crypto",
                )
                _marquer_echelle(feats, unite, haute)
            except Exception:  # noqa: BLE001 — sans panel, on trade quand même
                pass

            feats.setdefault("_trace", {}).setdefault("indicators", {})[
                "execution_spread_stop_pct"
            ] = float(cost)
            candidats.append({
                "sym": sym, "feats": feats, "out": out, "dec": dec,
                "support": int(getattr(dec, "support_passed", 0) or 0),
                "rank": float(getattr(dec, "rank", 0.0) or 0.0),
                "cost": float(cost), "timeframe": unite,
                "higher_timeframe": haute, "ltf_rates": ltf_rates,
            })

    candidats_bruts = candidats
    candidats, conflits = _resoudre_candidats_multitimeframe(candidats_bruts)
    _journaliser_selection_multitimeframe(candidats_bruts, candidats, conflits, stats)
    for sym in conflits:
        _compter_tunnel(stats, "multitimeframe", "CONFLICT")
        print(f"    {sym:8} bloque — directions opposees entre horizons", flush=True)

    # Les plus forts d'abord : piliers alignés, puis score de classement.
    candidats.sort(key=lambda c: (-c["support"], -c["rank"]))
    if len(candidats) > 1:
        print("    candidats : " + " > ".join(
            f"{c['sym']}[{c['timeframe']}]({c['support']}/4)"
            for c in candidats[:6]), flush=True)

    if entry_blocked:
        print(
            f"    observation terminee - entrees bloquees ({entry_block_reason})",
            flush=True,
        )
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))
        return

    # ── PHASE 2 — envoyer, sous tous les garde-fous, par ordre de mérite.
    _journaliser_grappes(candidats, compte.equity)

    for indice_candidat, c in enumerate(candidats):
        if _stop:
            return
        sym, feats, out, _dec = c["sym"], c["feats"], c["out"], c["dec"]

        # ── Suspension du FX entier (voir FX_SUSPENDU). Placée AVANT la
        #    suspension des shorts : quand tout le FX est écarté, le motif
        #    rendu doit être le vrai, sinon le journal raconte une décision qui
        #    n'a pas eu lieu.
        if FX_SUSPENDU:
            from titanium.edge import asset_class_of as _classe

            if _classe(sym) == "fx":
                _refus(stats, "FX_SUSPENDU", sym,
                       "FX suspendu pour verification (24/08/2026)",
                       side=int(getattr(out, "side", 0) or 0))
                print(f"    {sym:8} ENTER ignoré — FX suspendu pour "
                      f"vérification (24/08/2026)", flush=True)
                continue

        # ── Suspension des shorts FX (voir FX_SHORTS_SUSPENDUS).
        if FX_SHORTS_SUSPENDUS and int(getattr(out, "side", 0) or 0) < 0:
            from titanium.edge import asset_class_of as _classe

            if _classe(sym) == "fx":
                _refus(stats, "FX_SHORT_SUSPENDU", sym,
                       "shorts FX suspendus (recalibrage 17/08/2026)")
                print(f"    {sym:8} ENTER ignoré — shorts FX suspendus "
                      f"(recalibrage 17/08/2026)", flush=True)
                continue

        if out.risk_verdict == "DENY":
            _refus(stats, "RISKGATE_DENY", sym, out.reason,
                   piliers=c.get("support"), side=int(getattr(out, "side", 0) or 0))
            print(f"    {sym:8} ENTER mais RiskGate refuse : {out.reason}", flush=True)
            continue

        # ── Levee de l'anti-fade du 24/08/2026 : compter ce qui passe grace a
        #    elle. Sans ce compteur, la calibration ne saurait pas dire quelle
        #    part du flux vient de la levee, et on jugerait la levee sur une
        #    performance globale ou elle est noyee. La famille reste par
        #    ailleurs inscrite dans la cle de contexte (`...|reversal|...`).
        if bool(getattr(out, "contre_tendance", False)):
            _compter_tunnel(stats, "anti_fade", "CONTRE_TENDANCE_AUTORISE")
            print(f"    {sym:8} contre-tendance autorisé (calibration 24/08) — "
                  "drapeau RiskGate persisté", flush=True)

        # ── Réserve S≥3. Les derniers créneaux appartiennent à la strate qui
        #    nourrit la promotion : rare (~10 % des ENTER), elle serait sinon
        #    censurée par les S=2, plus nombreux et plus rapides à remplir.
        # Sans plafond de créneaux, il n'y a plus de dernière place à
        # réserver : la strate S≥3 n'est plus censurée par les S=2.
        if (MAX_POSITIONS > 0 and ouvertes >= MAX_POSITIONS - RESERVE_S3
                and c["support"] < 3):
            _refus(stats, "RESERVE_S3", sym,
                   f"creneaux reserves a S>=3 (setup {c['support']}/4)",
                   ouvertes=ouvertes)
            print(f"    {sym:8} ENTER différé — créneaux restants réservés à la "
                  f"strate S>=3 (setup {c['support']}/4)", flush=True)
            continue

        # Microstructure crypto multi-place, lue sur disque en quelques
        # millisecondes : aucun appel reseau ne se trouve dans la boucle.
        # Elle est placee en tete des indicateurs AVANT le scellement pour que
        # Qwen juge exactement le carnet qui accompagne cette decision.
        try:
            from titanium.microstructure import (
                attach_live_microstructure,
                microstructure_gate,
            )

            micro = attach_live_microstructure(sym, feats, root=RACINE)
            micro_gate = microstructure_gate(micro, side=int(out.side or 0))
            _compter_tunnel(stats, "microstructure", micro_gate.action)
            from titanium.microstructure import entry_microstructure_guard

            required_micro_gate = entry_microstructure_guard(sym, side=int(out.side or 0), root=RACINE)
            if micro_gate.action == "BLOCK" or required_micro_gate.action != "ALLOW":
                _refus(stats, "MICROSTRUCTURE", sym, micro_gate.reason,
                       piliers=c.get("support"), side=int(out.side or 0))
                print(f"    {sym:8} ENTER refuse - {micro_gate.reason}", flush=True)
                continue
        except Exception as exc:  # noqa: BLE001 - organe additif fail-soft
            _compter_tunnel(
                stats, "microstructure", f"ERROR_{type(exc).__name__.upper()}",
            )

        # ── 4. Dimensionnement PAR ACTIF, puis ordre.
        try:
            spec = ensure_symbol(sym)
            from titanium.confiance import (
                evaluer as evaluer_confiance,
                piliers_de,
                total_piliers,
            )
            intelligence_ok, conv, motif_intelligence = _autorisation_et_conviction(
                sym, feats, out, _dec, cfg, ltf=c.get("ltf_rates"),
            )
            if not intelligence_ok:
                _refus(stats, "INTELLIGENCE_GATE", sym, motif_intelligence,
                       piliers=c.get("support"), side=out.side)
                print(f"    {sym:8} ENTER differe/refuse - {motif_intelligence}",
                      flush=True)
                continue
            if ACTIVER_CORTEX:
                print(f"    {sym:8} intelligence live valide - {motif_intelligence}",
                      flush=True)

            conf = evaluer_confiance(
                piliers_de(_dec),
                total_piliers=total_piliers(),
                conviction=conv,
                quorum=(_cg.QUORUM_PROD if cfg.require_edge
                        else _cg.QUORUM_EXPLORE))

            # ── Le setup est-il encore valable MAINTENANT ?
            derive = _derive_depuis_decision(sym, feats, out)
            if derive is not None and derive > DERIVE_MAX_R:
                _refus(stats, "DERIVE", sym,
                       f"prix derive de {derive:.2f} R depuis la decision")
                print(f"    {sym:8} ENTER périmé — le prix a dérivé de "
                      f"{derive:.2f} R depuis la décision, setup abandonné",
                      flush=True)
                continue

            # Une position supplémentaire sur le même actif n'est pas un
            # doublon toléré : elle doit être un retournement explicite ou
            # améliorer réellement le meilleur prix de la position existante.
            expositions = expositions_par_symbole.get(sym, [])
            prix_courant = _prix_execution_courant(sym, int(out.side or 0))
            autorise, motif_empilement = _autoriser_empilement(
                expositions,
                side=int(out.side or 0),
                prix=float(prix_courant or 0.0),
                stop_distance=float(out.stop_distance or 0.0),
                setup_family=str(getattr(_dec, "setup_family", "") or ""),
                atr=float(feats.get("atr", 0.0) or 0.0),
                spread=(
                    float(getattr(spec, "spread", 0.0) or 0.0)
                    * float(getattr(spec, "point", 0.0) or 0.0)
                ),
            )
            if not autorise:
                _refus(
                    stats, "MULTIPOSITION", sym, motif_empilement,
                    deja=len(expositions), side=int(out.side or 0),
                )
                print(f"    {sym:8} ENTER ignoré — multi-position refusée : "
                      f"{motif_empilement}", flush=True)
                continue
            if expositions:
                _compter_tunnel(stats, "multiposition", motif_empilement)
                print(f"    {sym:8} multi-position autorisée — "
                      f"{motif_empilement}", flush=True)

            # Le filtre initial travaille sur un ATR estimé. RiskGate vient de
            # produire le stop exact : on recontrôle le coût sur CETTE distance
            # avant de dimensionner. Sinon un écart entre les deux ATR pouvait
            # contourner le plafond de 25 %.
            from titanium.echelle import cout_relatif_stop

            cout_actuel = cout_relatif_stop(spec, out.stop_distance or 0.0)
            if cout_actuel > MAX_COUT_SPREAD_PCT:
                _refus(stats, "COUT_SPREAD", sym,
                       f"spread {cout_actuel:.0%} du stop reel "
                       f"(plafond {MAX_COUT_SPREAD_PCT:.0%})")
                print(f"    {sym:8} ENTER refusé — spread {cout_actuel:.0%} "
                      f"du stop réel (plafond {MAX_COUT_SPREAD_PCT:.0%})",
                      flush=True)
                continue

            budget = budget_for(spec, out.stop_distance or 0.0, compte.equity,
                                target_pct=conf.pct)
            budget = replace(
                budget,
                timeframe=str(c.get("timeframe") or LTF),
                cout_spread=round(cout_actuel, 4),
            )

            # Second tracé, maintenant que la taille est connue : le panneau
            # MT5 doit montrer le risque réellement engagé, pas une intention.
            if tracer:
                _tracer_zones(sym, feats, out, cfg, conf=conf, budget=budget)
        except Exception as exc:  # noqa: BLE001
            _refus(stats, "SIZING_ERROR", sym, type(exc).__name__,
                   timeframe=c.get("timeframe"))
            print(f"    {sym:8} dimensionnement impossible : {type(exc).__name__}",
                  flush=True)
            continue

        if not budget.tradable:
            _refus(stats, "BUDGET", sym, budget.reason)
            print(f"    {sym:8} ENTER mais {budget.reason}", flush=True)
            continue

        risque_panier = float(risque_par_symbole.get(sym, 0.0) or 0.0)
        risque_panier_apres = risque_panier + float(budget.effective_pct)
        if (
            not math.isfinite(risque_panier_apres)
            or risque_panier_apres > MAX_RISQUE_PANIER_PCT + 1e-12
        ):
            motif_panier = (
                f"risque panier {risque_panier_apres:.2f} % > "
                f"{MAX_RISQUE_PANIER_PCT:.2f} %"
            )
            _refus(
                stats,
                "MICRO_PANIER_RISK",
                sym,
                motif_panier,
                risque_actuel_pct=round(risque_panier, 4),
                risque_propose_pct=round(float(budget.effective_pct), 4),
            )
            print(f"    {sym:8} ENTER refusé — {motif_panier}", flush=True)
            continue

        # Le lot minimum et l'arrondi courtier peuvent eloigner le risque de
        # l'intention ``conf.pct``. Une seule porte correlee fait autorite :
        # celle-ci, apres sizing, avec le risque effectivement tradable.
        ok_grappe, motif_grappe = _revalider_grappe_apres_sizing(sym, budget)
        if not ok_grappe:
            _refus(stats, "GRAPPE", sym, motif_grappe,
                   risque_pct=getattr(budget, "effective_pct", None))
            print(f"    {sym:8} ENTER refuse — risque effectif : "
                  f"{motif_grappe}", flush=True)
            continue

        sens = "LONG" if out.side > 0 else "SHORT"
        detail = (f"lot {budget.lot} · risque {budget.risk_money} {compte.currency} "
                  f"({budget.effective_pct:.2f} %)"
                  + (" [lot min]" if budget.at_min_lot else ""))

        if not (armer and politique.enabled):
            print(f"    {sym:8} ENTER {sens} — {detail}  [observation, aucun ordre]",
                  flush=True)
            stats["simules"] += 1
            continue

        if MODE_ENTREE == "LIMITE" and limites_en_attente >= MAX_LIMITES_EN_ATTENTE:
            for restant in candidats[indice_candidat:]:
                _refus(stats, "LIMIT_PENDING_CAP", restant["sym"],
                       f"{MAX_LIMITES_EN_ATTENTE} limites deja en attente",
                       timeframe=restant.get("timeframe"))
            print(f"    limite en attente déjà présente "
                  f"({MAX_LIMITES_EN_ATTENTE}) — aucun risque passif supplémentaire",
                  flush=True)
            break

        # Une seule décision d'entrée, deux façons de la poser. Le mur
        # d'armement, le lot et le SL/TP sont identiques des deux côtés :
        # seul le type d'ordre change.
        decision_stratification = _stratification(sym, feats, out.side)
        decision_at = datetime.now(timezone.utc).isoformat()
        policy_identity = _decision_policy_identity(
            str(decision_stratification.get("mode", "")), cfg.rr_ratio,
            ltf=str(c.get("timeframe") or LTF),
            htf=str(c.get("higher_timeframe") or HTF),
        )
        # The deliberation/sizing may outlive the quote. Re-read local data now;
        # a previously fresh snapshot is not an authorization to trade later.
        from titanium.microstructure import entry_microstructure_guard

        final_micro = entry_microstructure_guard(sym, side=int(out.side or 0), root=RACINE)
        if final_micro.action != "ALLOW":
            _refus(stats, "MICROSTRUCTURE_FINAL", sym, final_micro.reason)
            continue
        final_loss_guard = evaluate_live_loss_guard(
            RACINE / "results" / "trades.ndjson",
            account=str(compte.login),
            not_before=DEMO_COHORT_START_UTC,
        )
        final_loss_guard = persist_live_loss_quarantine(
            final_loss_guard,
            path=(RACINE / "data" / "runtime" / "live_loss_quarantine"
                  / f"{compte.login}.json"),
            account=str(compte.login),
        )
        stats["live_loss_guard"] = final_loss_guard.to_dict()
        if final_loss_guard.action != "ALLOW":
            _refus(stats, "LIVE_LOSS_GUARD_FINAL", sym, final_loss_guard.reason)
            print(
                "    coupe-circuit pertes actualise avant envoi - "
                f"{final_loss_guard.action}/{final_loss_guard.reason}",
                flush=True,
            )
            battre(stats, armer=armer and politique.enabled, equity=compte.equity,
                   portables=len(tradables))
            return
        res = execute_recorded(
            _envoi_entree(),
            sym, out.side, budget.risk_money, out.stop_distance or 0.0,
            policy=politique,
            account=compte, identity=policy_identity,
            path=RACINE / "results" / "execution_ledger.sqlite3",
            tp_distance=(out.stop_distance or 0.0) * cfg.rr_ratio,
            idempotency_key=cle_barre(sym, feats),
        )
        execution_detail, execution_risk_money = _execution_detail(
            res, budget, equity=compte.equity, currency=compte.currency,
        )
        execution_risk_pct = (
            100.0 * execution_risk_money / compte.equity if compte.equity > 0 else 0.0
        )
        risque_panier_execute = risque_panier + execution_risk_pct

        if res.sent and not res.ticket:
            # A broker acknowledgement without a usable ticket is not safely adoptable.
            _refus(stats, "EXECUTION", sym, "TRACE_MISSING_ORDER_TICKET")
            continue

        decision_id = ""
        if res.sent:
            try:
                decision_id = make_decision_id(
                    policy_identity.get("policy_epoch", ""), res.ticket,
                )
                ctxk = _contexte_exact(sym, feats, out.side)
                unite_decision = str(c.get("timeframe") or LTF)
                written, reason = append_decision_event(
                    RACINE / "results" / "decision_registry.ndjson",
                    {
                        "event": "decided",
                        "decision_id": decision_id,
                        "decision_at": decision_at,
                        "execution_ticket": int(res.ticket),
                        "symbol": sym,
                        "side": int(out.side),
                        "asset_class": decision_stratification.get("asset_class", ""),
                        "context": ctxk,
                        "timeframe": unite_decision,
                        "quorum": decision_stratification.get("quorum", 0),
                        "support_pillars": decision_stratification.get(
                            "support_pillars", 0,
                        ),
                        **policy_identity,
                    },
                )
                stats["decision_registry_events"] = int(
                    stats.get("decision_registry_events", 0) or 0,
                ) + int(written)
                if not written and reason != "DUPLICATE":
                    _compter_tunnel(stats, "decision_registry_failure", reason)
            except Exception as exc:  # noqa: BLE001 - télémétrie fail-soft
                _compter_tunnel(
                    stats, "decision_registry_failure",
                    f"ERROR_{type(exc).__name__.upper()}",
                )

        if res.sent and MODE_ENTREE != "LIMITE":
            stats["envoyes"] += 1
            ouvertes += 1
            par_symbole[sym] = par_symbole.get(sym, 0) + 1
            risque_par_symbole[sym] = risque_panier_execute
            expositions_par_symbole.setdefault(sym, []).append(
                (int(out.side), float(res.price))
            )
            # Le contexte doit être attaché MAINTENANT : à la clôture, MT5 ne
            # montrera plus la position et l'information serait perdue.
            _attacher_contexte(res.ticket, sym, feats, out, res,
                               risque_devise=execution_risk_money,
                               spread_r=budget.cout_spread,
                               policy_identity=policy_identity,
                               decision_id=decision_id,
                               decision_at=decision_at)
            unite_b = str(c.get("timeframe") or LTF)
            marque = "" if unite_b == LTF else f" [{unite_b}]"
            ctxk = _contexte_exact(sym, feats, out.side)
            print(f"    {sym:8} ORDRE ENVOYÉ {sens}{marque} #{res.ticket} "
                  f"@ {res.price} — {execution_detail} · SL {res.sl} TP {res.tp}",
                  flush=True)
            print(f"             contexte : {ctxk}", flush=True)
        elif res.sent:
            stats["envoyes"] += 1
            stats["limites_placees"] = int(stats.get("limites_placees", 0) or 0) + 1
            ouvertes += 1
            limites_en_attente += 1
            par_symbole[sym] = par_symbole.get(sym, 0) + 1
            risque_par_symbole[sym] = risque_panier_execute
            expositions_par_symbole.setdefault(sym, []).append(
                (int(out.side), float(res.price))
            )
            # Le contexte doit être attaché MAINTENANT : à la clôture, MT5 ne
            # montrera plus la position et l'information serait perdue.
            contexte_sauve, motif_contexte = _memoriser_contexte_limit(
                res.ticket, sym, feats, out, res,
                risque_devise=execution_risk_money,
                spread_r=budget.cout_spread,
                policy_identity=policy_identity,
                decision_id=decision_id,
                decision_at=decision_at,
            )
            if contexte_sauve:
                stats["pending_context_saved"] = int(
                    stats.get("pending_context_saved", 0) or 0
                ) + 1
            else:
                _compter_tunnel(
                    stats, "pending_context_save_failure", motif_contexte)
                print(f"    ALERTE contexte limite non sauvegarde : "
                      f"{motif_contexte}", flush=True)
            unite_b = str(c.get("timeframe") or LTF)
            marque = "" if unite_b == LTF else f" [{unite_b}]"
            economie_r = res.spread_saved_price / (out.stop_distance or 1.0)
            ctxk = _contexte_exact(sym, feats, out.side)
            stratification = _stratification(sym, feats, out.side)
            regime = str(ctxk).split("|")[2:3]
            evenement_ecrit, motif_evenement = append_limit_event(
                RACINE / "results" / "limit_lifecycle.ndjson",
                {
                    "event": "placed", "order_ticket": int(res.ticket),
                    "symbol": sym, "side": int(out.side),
                    "planned_price": float(res.price or 0.0),
                    "market_reference_price": float(
                        getattr(res, "market_reference_price", 0.0) or 0.0),
                    "r_unit": float(out.stop_distance or 0.0),
                    "target_saving_price": float(res.spread_saved_price or 0.0),
                    "target_saving_r": economie_r,
                    "spread_r": float(res.spread_r or 0.0),
                    "expires_at": res.expires_at,
                    "lot": float(res.lot or 0.0),
                    "risk_money": execution_risk_money,
                    "context": ctxk,
                    "regime": regime[0] if regime else "unknown",
                    "asset_class": stratification.get("asset_class", ""),
                    "mode": stratification.get("mode", ""),
                    "timeframe": unite_b,
                    "candle_source": stratification.get("candle_source", ""),
                    **policy_identity,
                },
            )
            if evenement_ecrit:
                stats["limit_lifecycle_events"] = int(
                    stats.get("limit_lifecycle_events", 0) or 0
                ) + 1
            elif motif_evenement != "DUPLICATE":
                _compter_tunnel(
                    stats, "limit_lifecycle_failure", motif_evenement)
                print(f"    ALERTE cycle limite non journalise : "
                      f"{motif_evenement}", flush=True)
            print(f"    {sym:8} LIMIT PLACÉE {sens}{marque} #{res.ticket} "
                  f"@ {res.price} — économie visée {economie_r:.1%}R · "
                  f"expire {res.expires_at} — {execution_detail} · SL {res.sl} TP {res.tp}",
                  flush=True)
            print(f"             contexte : {ctxk}", flush=True)
        elif res.reason != "DEJA_ENVOYE":
            _compter_refus_execution(stats, res)
            print(f"    {sym:8} ordre refusé : {res.reason}", flush=True)
            for chk in res.checks:
                if not chk["passed"]:
                    print(f"             {chk['gate']} — {chk['detail']}", flush=True)

        if MAX_POSITIONS > 0 and ouvertes >= MAX_POSITIONS:
            break
        # Le budget se recalcule après chaque envoi : huit positions à 1.75 %
        # feraient 14 % d'exposition, dont une bonne part sur le même pari.
        try:
            import MetaTrader5 as mt5  # noqa: N813
            if _risque_engage_pct(mt5, compte.equity) >= MAX_RISQUE_CUMULE_PCT:
                print(f"    budget de risque atteint "
                      f"({MAX_RISQUE_CUMULE_PCT:.1f} %) — arrêt des envois",
                      flush=True)
                break
        except Exception:  # noqa: BLE001
            pass

    # Second battement, en FIN de tour : celui du début prouve la vitalité
    # (utile si un balayage se bloque), celui-ci porte des statistiques à jour.
    stats["limit_lifecycle"] = limit_lifecycle_summary(
        RACINE / "results" / "limit_lifecycle.ndjson")
    _publier_zones()
    battre(stats, armer=armer and politique.enabled, equity=compte.equity,
           portables=len(tradables))
    _demander_fenetres(tradables)


def main() -> None:
    configure_console_output()
    ap = argparse.ArgumentParser(description="Boucle d'amorçage démo Titanium V14")
    ap.add_argument("--armer", action="store_true",
                    help="envoie réellement les ordres (exige aussi TITANIUM_EXEC_ENABLED=1)")
    ap.add_argument("--intervalle", type=float, default=INTERVALLE)
    ap.add_argument("--tours", type=int, default=0, help="0 = sans fin")
    ap.add_argument("--tracer", default="EURUSD",
                    help="symbole trace sur MT5 par defaut (vide = desactive)")
    args = ap.parse_args()

    signal.signal(signal.SIGINT, _arreter)

    registry_ready, registry_reason = prepare_decision_registry(
        RACINE / "results" / "decision_registry.ndjson",
    )
    if not registry_ready:
        print(
            f"  ALERTE registre de décisions non préchargé : {registry_reason}",
            flush=True,
        )

    from titanium.data.mt5_vendor import account_snapshot, shutdown
    from titanium.execution.mt5_executor import ExecutionPolicy

    politique = ExecutionPolicy.from_config()
    compte = account_snapshot()

    print("═" * 78)
    print("  TITANIUM V14 — boucle d'amorçage")
    print("═" * 78)
    print(f"  compte     : {compte.login} {compte.server} "
          f"({'DÉMO' if compte.is_demo else 'RÉEL'}) · "
          f"{compte.equity:.2f} {compte.currency}")
    print(f"  mur        : armé={politique.enabled} · "
          f"login attendu={politique.expected_demo_login} · "
          f"réel={'AUTORISÉ' if politique.allow_real_account else 'interdit'}")
    print(f"  intervalle : {args.intervalle:.0f} s · "
          f"{'positions illimitées' if MAX_POSITIONS <= 0 else f'max {MAX_POSITIONS} positions'} "
          f"· budget de risque {MAX_RISQUE_CUMULE_PCT:.0f} %")

    if args.armer and not politique.enabled:
        print("\n  ⚠️  --armer demandé mais TITANIUM_EXEC_ENABLED≠1 dans le .env.")
        print("      Les deux sont requis. La boucle tourne en OBSERVATION.")
    elif args.armer:
        print("\n  ⚠️  ORDRES RÉELS ACTIVÉS sur le compte démo.")
    else:
        print("\n  Mode observation : aucun ordre ne sera envoyé.")
    print("\n  Ctrl+C pour arrêter.\n")

    stats = {"tours": 0, "evalues": 0, "enter": 0, "simules": 0,
             "envoyes": 0, "tunnel": {}}
    try:
        while not _stop:
            stats["tours"] += 1
            try:
                tour(armer=args.armer, stats=stats,
                     tracer=bool(args.tracer), tracer_defaut=args.tracer)
            except Exception as exc:  # noqa: BLE001 — la boucle survit à tout
                print(f"    tour en erreur : {type(exc).__name__}: {exc}", flush=True)
            if args.tours and stats["tours"] >= args.tours:
                break
            for _ in range(int(args.intervalle * 2)):
                if _stop:
                    break
                time.sleep(0.5)
    finally:
        shutdown()
        print(f"\n  {stats['tours']} tour(s) · {stats['evalues']} évaluations · "
              f"{stats['enter']} ENTER · {stats['simules']} simulés · "
              f"{stats['envoyes']} ordre(s) envoyé(s)")


if __name__ == "__main__":
    main()
