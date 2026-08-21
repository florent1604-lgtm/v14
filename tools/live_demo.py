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
import math
import signal
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.console_output import configure_console_output  # noqa: E402

#: Univers candidat. Chaque tour filtre selon ce que l'equity peut porter.
#: Univers candidat. Vide ⇒ **tout le catalogue MT5 tradable**.
#:
#: L'élargissement est le seul levier d'accélération qui ne change pas la
#: stratégie. Le capital reste à 5000 EUR ; ce qui limitait l'accumulation
#: n'était pas lui mais la surface de balayage : 24 actifs pour ~2 setups
#: S≥3 simultanés sur tout le catalogue.
#:
#: L'exposition reste bornée par MAX_POSITIONS et MAX_RISQUE_CUMULE_PCT —
#: balayer large ne fait pas trader plus, cela fait *choisir* mieux.
UNIVERS: list = []

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
INTERVALLE = 60.0        # s entre deux balayages

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
# silencieusement MAX_RISQUE_CUMULE_PCT.
MAX_LIMITES_EN_ATTENTE = 1

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
#: Positions simultanées sur UN MÊME actif. Second garde-fou, indépendant de
#: l'idempotence : si la clé de barre échoue pour une raison quelconque, ce
#: plafond empêche encore d'empiler trois fois le même risque corrélé.
MAX_PAR_SYMBOLE = 1

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
            "equity": equity,
            "portables": portables,
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


def _compter_refus_execution(stats: dict, resultat) -> None:
    """Ventile un refus d'ordre sans perdre le compteur historique EXECUTION."""
    _compter_tunnel(stats, "post_enter_refusal", "EXECUTION")
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


#: Charges de zones du tour courant, une par symbole. Vidé à chaque tour
#: pour qu'un symbole sorti de l'univers cesse d'être tracé.
_ZONES: dict = {}


def _tracer_zones(sym: str, feats: dict, out, cfg, conf=None, budget=None) -> None:
    """Exporte les zones vers MT5. Ne lève jamais — l'affichage n'est pas critique."""
    try:
        from titanium.bridge.mt5_zones import Plan, ecrire, zones_depuis_features
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


#: Actifs jouables vus depuis le démarrage. La rotation n'en montre que 24
#: par tour ; l'arbre a besoin d'un échantillon large pour être fidèle.
_JOUABLES: set = set()


def _tradables_connus(courants) -> list:
    """Cumul des actifs jouables rencontrés, pour nourrir l'arbre."""
    _JOUABLES.update(courants)
    return sorted(_JOUABLES)


def rafraichir_grappes(catalogue) -> None:
    """(Re)calcule l'arbre de correlation. A appeler HORS d'un envoi d'ordre."""
    global _GRAPPES, _GRAPPES_A
    try:
        import time as _t

        from titanium.correlation import TTL_GRAPPES_S, charger
        if _GRAPPES is not None and _t.time() - _GRAPPES_A < TTL_GRAPPES_S:
            return
        # Sous 20 actifs, les familles ne veulent rien dire : on attend que
        # la rotation en ait montré assez.
        if len(catalogue) < 20:
            return
        g = charger(catalogue)
        if g.par_actif:
            _GRAPPES = g
            _GRAPPES_A = _t.time()
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


def _demander_fenetres(candidats) -> None:
    """Demande a l'EA compagnon d'ouvrir les graphiques utiles.

    L'API Python ne sait pas ouvrir un graphique — ChartOpen n'existe que
    cote MQL5. V14 ecrit donc la liste voulue, et titanium_charts.mq5 fait
    converger le terminal. Ne leve jamais : l'affichage n'est pas critique.
    """
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.bridge.mt5_charts import (
            demander_fenetres, symboles_actifs,
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


def _avis_pour(sym: str, side: int) -> tuple[float, str]:
    """Relit l'avis des analystes. NE BLOQUE JAMAIS, ne declenche aucun
    appel : si le travailleur est arrete ou en panne, la boucle continue
    exactement comme si le pont n'existait pas."""
    try:
        from titanium.avis import conviction_pour
        return conviction_pour(sym, side, AVIS_RENDUS)
    except Exception:  # noqa: BLE001
        return 0.5, "avis indisponible"


def _sante_resumee() -> str:
    """Resume d'auscultation, pour que l'analyste sache sur quoi il juge."""
    try:
        from titanium.web.medecin import bilan
        b = bilan()
        return f"{b.get('verdict','?')} — {b.get('resume','')}"
    except Exception:  # noqa: BLE001
        return ""


def _demander_avis(sym: str, feats: dict, out, decision, cfg) -> None:
    """Depose la lecture deterministe pour les analystes. Une ecriture,
    puis on continue — la deliberation se fait dans un autre processus."""
    try:
        from titanium.avis import Demande, deposer
        trace = feats.get("_trace") or {}
        deposer(Demande(
            symbol=sym, side=out.side,
            verdict=decision.verdict, code=decision.code,
            piliers=sum(1 for g in (decision.gates or []) if g.passed),
            famille=getattr(decision, "setup_family", ""),
            prix=float(trace.get("price") or 0.0),
            stop_distance=float(out.stop_distance or 0.0),
            rr=cfg.rr_ratio,
            bar_time=str(trace.get("bar_time") or ""),
            indicateurs=dict(trace.get("indicators") or {}),
            sante=_sante_resumee(),
        ), AVIS_DEMANDES)
    except Exception:  # noqa: BLE001
        pass


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
                       spread_r: float | None = None) -> None:
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
            TrackedState, load_state, save_state,
        )

        chemin = RACINE / "results" / "positions.json"
        etat = load_state(chemin)
        r = abs((res.price or 0.0) - (res.sl or 0.0))
        r_eff = r if r > 0 else (out.stop_distance or 0.0)
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
            indicators=dict((feats.get("_trace") or {}).get("indicators") or {}),
            ts_open=datetime.now(timezone.utc).isoformat(),
            # Sert a convertir en R la commission et le swap que MT5 rend en
            # devise. Sans lui, ces frais seraient journalises a zero.
            risque_devise=float(risque_devise or 0.0),
            spread_r=(None if spread_r is None else float(spread_r)),
            spread_exact=False,
            entry_levels=_niveaux_entree(
                feats, entry=res.price or 0.0, side=out.side, r=r_eff),
            entry_atr=float((feats.get("_trace") or {}).get("atr") or 0.0),
            **_stratification(sym, feats, out.side),
        )
        save_state(chemin, etat)
    except Exception:  # noqa: BLE001 — l'observabilite ne casse jamais le trading
        pass


def _memoriser_contexte_limit(ticket, sym: str, feats: dict, out, res,
                              *, risque_devise: float = 0.0,
                              spread_r: float | None = None) -> tuple[bool, str]:
    """Conserve le contexte jusqu'au fill et rend une preuve exploitable."""
    if not ticket or not getattr(res, "expires_at", ""):
        return False, "TICKET_OU_EXPIRATION_ABSENT"
    try:
        from datetime import datetime, timezone

        from titanium.execution.pending_context import save_pending_context
        from titanium.execution.position_manager import TrackedState

        r = abs((res.price or 0.0) - (res.sl or 0.0))
        r_eff = r if r > 0 else (out.stop_distance or 0.0)
        template = TrackedState(
            r=r_eff,
            symbol=sym, side=out.side,
            entry=res.price or 0.0,
            sl_initial=res.sl or 0.0, tp_initial=res.tp or 0.0,
            context_key=_contexte_exact(sym, feats, out.side),
            indicators=dict((feats.get("_trace") or {}).get("indicators") or {}),
            ts_open=datetime.now(timezone.utc).isoformat(),
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


def tour(*, armer: bool, stats: dict, tracer: bool = True,
         tracer_defaut: str = "EURUSD") -> None:
    from titanium.data.mt5_vendor import (
        account_snapshot,
        ensure_symbol,
        get_rates,
        get_rates_cache,
    )
    from titanium.execution.limit_orders import place_limit_order
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
    if len(hors_crypto) < 10:
        cryptos = [s for s in catalogue if asset_class_of(s) == "crypto"]
        if cryptos:
            catalogue = cryptos
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

    univers = list(dict.fromkeys(portees + tranche))
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
    gestion_saine = True
    try:
        import MetaTrader5 as mt5  # noqa: N813

        from titanium.data.mt5_vendor import mt5_lock, mt5_session

        etat = Path(RACINE / "results" / "positions.json")
        with mt5_lock:
            with mt5_session():
                positions_courantes = mt5.positions_get()
                if positions_courantes is None:
                    raise RuntimeError(f"positions MT5 indisponibles: {mt5.last_error()}")
                for p in positions_courantes:
                    if int(getattr(p, "magic", 0) or 0) != politique.magic:
                        continue
                    ouvertes += 1
                    par_symbole[p.symbol] = par_symbole.get(p.symbol, 0) + 1
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
                    manage_stops=armer and politique.enabled,
                )
                stats["journal_coverage"] = _journal_coverage(
                    r.get("history_recovery"),
                )
                if r.get("moved"):
                    print(f"    gestion : {r['moved']} stop(s) déplacé(s)", flush=True)
                    for d in r.get("details", []):
                        print(f"      {d}", flush=True)
                if r.get("reason"):
                    gestion_saine = False
                    print(f"    gestion fail-closed : {r['reason']}", flush=True)
                    for d in r.get("details", []):
                        print(f"      {d}", flush=True)
    except Exception as exc:  # noqa: BLE001
        gestion_saine = False
        print(f"    gestion indisponible : {type(exc).__name__}", flush=True)

    if not gestion_saine:
        print("    mesure ou positions indisponibles — aucun nouvel ordre", flush=True)
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))
        return

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
        battre(stats, armer=armer and politique.enabled, equity=compte.equity,
               portables=len(tradables))
        return

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
    from titanium.gates import confluence_gate as _cg
    from titanium.selection import barres_pour

    candidats = []
    for sym in tradables:
        if _stop:
            return
        try:
            # La profondeur d'historique suit le rang : un actif de rang A
            # obtient plus de barres, donc des niveaux de structure calculés
            # sur une histoire plus longue et moins sensibles au bruit.
            n_barres = barres_pour(sel, sym, BARRES) if sel else BARRES
            # ── L'unité de temps vient du DIMENSIONNEMENT, pas d'une
            #    constante. Le week-end, la volatilité M15 s'effondre à 58 %
            #    de la semaine : le stop devient plus petit que le spread.
            #    `tradable_universe` a déjà choisi la plus petite unité où
            #    l'actif redevient jouable — analyser M15 pendant que le
            #    stop est calibré en H1 ferait décider sur des bougies qui
            #    n'ont aucun rapport avec le risque pris.
            unite = getattr(budgets.get(sym), "timeframe", LTF) or LTF
            haute = "D1" if unite == "H4" else HTF
            ltf = get_rates(sym, unite, n_barres)
            htf = get_rates_cache(sym, haute, BARRES)
            # ⚠️ SANS le panel d'indicateurs. Mesuré : 392 ms avec, 30 ms
            # sans — le panel pèse 92 % du coût d'un actif. Or AUCUNE porte
            # ne le lit : il ne sert qu'à l'affichage MT5, au brief des
            # analystes et au contexte figé, tous sur le chemin ENTER. On le
            # recalcule donc pour les ~10 % qui entrent, pas pour les 90 %
            # qui sont écartés. Balayage 6× plus rapide, décision identique.
            # La crypto cote en continu : le blocage week-end ne la vise pas
            # (voir titanium.features.builder._weekend_block).
            feats = build_feats(ltf, htf, with_indicators=False,
                                marche_continu=asset_class_of(sym) == "crypto")
            _marquer_echelle(feats, unite, haute)
        except Exception as exc:  # noqa: BLE001
            # ⚠️ NE JAMAIS avaler en silence. Un `continue` muet a masqué un
            # arrêt TOTAL du balayage pendant une heure le 07/08/2026.
            stats["illisibles"] = stats.get("illisibles", 0) + 1
            if stats["illisibles"] <= 3 or stats["illisibles"] % 25 == 0:
                print(f"    {sym:10} illisible : {type(exc).__name__}: "
                      f"{str(exc)[:70]}", flush=True)
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

        if tracer:
            _tracer_zones(sym, feats, out, cfg)
        _observer_prod(sym, feats, out.gate_verdict)

        if out.gate_verdict != "ENTER":
            continue
        stats["enter"] += 1

        # Le setup entre : MAINTENANT le panel vaut son coût.
        try:
            feats = build_feats(ltf, htf, with_indicators=True,
                                marche_continu=asset_class_of(sym) == "crypto")
            _marquer_echelle(feats, unite, haute)
        except Exception:  # noqa: BLE001 — sans panel, on trade quand même
            pass

        candidats.append({
            "sym": sym, "feats": feats, "out": out, "dec": dec,
            "support": int(getattr(dec, "support_passed", 0) or 0),
            "rank": float(getattr(dec, "rank", 0.0) or 0.0),
        })

    # Les plus forts d'abord : piliers alignés, puis score de classement.
    candidats.sort(key=lambda c: (-c["support"], -c["rank"]))
    if len(candidats) > 1:
        print("    candidats : " + " > ".join(
            f"{c['sym']}({c['support']}/4)" for c in candidats[:6]), flush=True)

    # ── PHASE 2 — envoyer, sous tous les garde-fous, par ordre de mérite.
    _journaliser_grappes(candidats, compte.equity)

    for c in candidats:
        if _stop:
            return
        sym, feats, out, _dec = c["sym"], c["feats"], c["out"], c["dec"]

        # Garde-fou indépendant de l'idempotence : ne jamais empiler le même
        # risque corrélé sur un actif déjà en position.
        if par_symbole.get(sym, 0) >= MAX_PAR_SYMBOLE:
            _compter_tunnel(stats, "post_enter_refusal", "MAX_PAR_SYMBOLE")
            print(f"    {sym:8} ENTER ignoré — déjà {par_symbole[sym]} position(s) "
                  f"sur cet actif", flush=True)
            continue

        # ── Suspension des shorts FX (voir FX_SHORTS_SUSPENDUS).
        if FX_SHORTS_SUSPENDUS and int(getattr(out, "side", 0) or 0) < 0:
            from titanium.edge import asset_class_of as _classe

            if _classe(sym) == "fx":
                _compter_tunnel(stats, "post_enter_refusal", "FX_SHORT_SUSPENDU")
                print(f"    {sym:8} ENTER ignoré — shorts FX suspendus "
                      f"(recalibrage 17/08/2026)", flush=True)
                continue

        if out.risk_verdict == "DENY":
            _compter_tunnel(stats, "post_enter_refusal", "RISKGATE_DENY")
            print(f"    {sym:8} ENTER mais RiskGate refuse : {out.reason}", flush=True)
            continue

        # ── Réserve S≥3. Les derniers créneaux appartiennent à la strate qui
        #    nourrit la promotion : rare (~10 % des ENTER), elle serait sinon
        #    censurée par les S=2, plus nombreux et plus rapides à remplir.
        # Sans plafond de créneaux, il n'y a plus de dernière place à
        # réserver : la strate S≥3 n'est plus censurée par les S=2.
        if (MAX_POSITIONS > 0 and ouvertes >= MAX_POSITIONS - RESERVE_S3
                and c["support"] < 3):
            _compter_tunnel(stats, "post_enter_refusal", "RESERVE_S3")
            print(f"    {sym:8} ENTER différé — créneaux restants réservés à la "
                  f"strate S>=3 (setup {c['support']}/4)", flush=True)
            continue

        # ── 4. Dimensionnement PAR ACTIF, puis ordre.
        try:
            spec = ensure_symbol(sym)
            from titanium.confiance import (
                evaluer as evaluer_confiance, piliers_de, total_piliers,
            )
            # Avis des analystes : relu SANS attendre. Absent, périmé ou
            # travailleur arrêté -> conviction neutre, la boucle continue.
            conv, motif_avis = _avis_pour(sym, out.side)
            _demander_avis(sym, feats, out, _dec, cfg)

            conf = evaluer_confiance(
                piliers_de(_dec),
                total_piliers=total_piliers(),
                conviction=conv,
                quorum=(_cg.QUORUM_PROD if cfg.require_edge
                        else _cg.QUORUM_EXPLORE))

            # ── Le setup est-il encore valable MAINTENANT ?
            derive = _derive_depuis_decision(sym, feats, out)
            if derive is not None and derive > DERIVE_MAX_R:
                _compter_tunnel(stats, "post_enter_refusal", "DERIVE")
                print(f"    {sym:8} ENTER périmé — le prix a dérivé de "
                      f"{derive:.2f} R depuis la décision, setup abandonné",
                      flush=True)
                continue

            # Le filtre initial travaille sur un ATR estimé. RiskGate vient de
            # produire le stop exact : on recontrôle le coût sur CETTE distance
            # avant de dimensionner. Sinon un écart entre les deux ATR pouvait
            # contourner le plafond de 25 %.
            from titanium.echelle import cout_relatif_stop

            cout_actuel = cout_relatif_stop(spec, out.stop_distance or 0.0)
            if cout_actuel > MAX_COUT_SPREAD_PCT:
                _compter_tunnel(stats, "post_enter_refusal", "COUT_SPREAD")
                print(f"    {sym:8} ENTER refusé — spread {cout_actuel:.0%} "
                      f"du stop réel (plafond {MAX_COUT_SPREAD_PCT:.0%})",
                      flush=True)
                continue

            budget = budget_for(spec, out.stop_distance or 0.0, compte.equity,
                                target_pct=conf.pct)
            budget = replace(
                budget,
                timeframe=getattr(budgets.get(sym), "timeframe", LTF),
                cout_spread=round(cout_actuel, 4),
            )

            # Second tracé, maintenant que la taille est connue : le panneau
            # MT5 doit montrer le risque réellement engagé, pas une intention.
            if tracer:
                _tracer_zones(sym, feats, out, cfg, conf=conf, budget=budget)
        except Exception as exc:  # noqa: BLE001
            print(f"    {sym:8} dimensionnement impossible : {type(exc).__name__}",
                  flush=True)
            continue

        if not budget.tradable:
            _compter_tunnel(stats, "post_enter_refusal", "BUDGET")
            print(f"    {sym:8} ENTER mais {budget.reason}", flush=True)
            continue

        # Le lot minimum et l'arrondi courtier peuvent eloigner le risque de
        # l'intention ``conf.pct``. Une seule porte correlee fait autorite :
        # celle-ci, apres sizing, avec le risque effectivement tradable.
        ok_grappe, motif_grappe = _revalider_grappe_apres_sizing(sym, budget)
        if not ok_grappe:
            _compter_tunnel(stats, "post_enter_refusal", "GRAPPE")
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

        if limites_en_attente >= MAX_LIMITES_EN_ATTENTE:
            _compter_tunnel(stats, "post_enter_refusal", "LIMIT_PENDING_CAP")
            print(f"    limite en attente déjà présente "
                  f"({MAX_LIMITES_EN_ATTENTE}) — aucun risque passif supplémentaire",
                  flush=True)
            break

        res = place_limit_order(
            sym, out.side, budget.risk_money, out.stop_distance or 0.0,
            policy=politique,
            tp_distance=(out.stop_distance or 0.0) * cfg.rr_ratio,
            idempotency_key=cle_barre(sym, feats),
        )
        if res.sent:
            stats["envoyes"] += 1
            stats["limites_placees"] = int(stats.get("limites_placees", 0) or 0) + 1
            ouvertes += 1
            limites_en_attente += 1
            par_symbole[sym] = par_symbole.get(sym, 0) + 1
            # Le contexte doit être attaché MAINTENANT : à la clôture, MT5 ne
            # montrera plus la position et l'information serait perdue.
            contexte_sauve, motif_contexte = _memoriser_contexte_limit(
                res.ticket, sym, feats, out, res,
                risque_devise=budget.risk_money,
                spread_r=budget.cout_spread,
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
            unite_b = getattr(budgets.get(sym), "timeframe", LTF)
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
                    "risk_money": float(budget.risk_money or 0.0),
                    "context": ctxk,
                    "regime": regime[0] if regime else "unknown",
                    "asset_class": stratification.get("asset_class", ""),
                    "mode": stratification.get("mode", ""),
                    "timeframe": unite_b,
                    "candle_source": stratification.get("candle_source", ""),
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
                  f"expire {res.expires_at} — {detail} · SL {res.sl} TP {res.tp}",
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

    from titanium.execution.mt5_executor import ExecutionPolicy
    from titanium.data.mt5_vendor import account_snapshot, shutdown

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
