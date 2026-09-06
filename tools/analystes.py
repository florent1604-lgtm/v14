"""Travailleur d'analyse — fait délibérer les analystes hors du chemin critique.

    .venv\\Scripts\\python.exe tools\\analystes.py            # boucle continue
    .venv\\Scripts\\python.exe tools\\analystes.py --une-fois # un passage

Il consomme les candidats déposés par `tools/live_demo.py`, les soumet à
Hermès, puis publie sa décision scellée. La boucle de trading relit cette
décision sans jamais attendre.

Pourquoi un processus séparé
-----------------------------
Une délibération distante prend des secondes ; la boucle tourne en 10 secondes. Les
mettre dans le même fil ferait attendre le trading derrière un service
externe. Séparés, une panne des analystes — quota épuisé, fournisseur
saturé, réseau coupé — bloque les nouvelles entrées sans bloquer la boucle.
La protection déterministe des positions ouvertes continue normalement.

C'est aussi ce qui permet de couper les coûts d'un geste : arrêter ce
processus n'arrête rien d'autre.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.avis import (  # noqa: E402
    NEUTRE,
    Avis,
    demandes_en_attente,
    enregistrer,
    purger,
)
from titanium.organism.contracts import digest  # noqa: E402
from titanium.organism.cortex import (  # noqa: E402
    build_cortex_policy,
    market_observed_at,
    policy_ttl_s,
)
from titanium.organism.memory import CentralMemory  # noqa: E402
from titanium.organism.trading_knowledge import compact_observations  # noqa: E402
from tools.console_output import configure_console_output  # noqa: E402

DEMANDES = RACINE / "results" / "avis_demandes.ndjson"
AVIS = RACINE / "results" / "avis_rendus.ndjson"
POSITION_REQUESTS = RACINE / "results" / "position_review_requests.ndjson"
POSITION_VERDICTS = RACINE / "results" / "position_review_verdicts.ndjson"
CENTRAL_MEMORY = CentralMemory(
    RACINE / "results" / "organism_memory.sqlite3",
    RACINE / "results" / "organism_alerts.ndjson",
)

INTERVALLE = 5.0

#: Analystes convoqués selon la classe d'actif.
#:
#: Choisis sur ce qu'ils peuvent réellement savoir. `fundamentals` lit des
#: bilans d'entreprise : il n'a rien à dire d'une paire de devises, et son
#: avis vide compte pourtant dans le débat. `social` mesure un sentiment de
#: foule — pertinent sur la crypto, du bruit sur EURSGD.
#:
#: Effet mesuré : ~116 s à quatre analystes, ~35 s à deux.
ANALYSTES_PAR_CLASSE = {
    "fx":      ("market", "news"),
    "metaux":  ("market", "news"),
    "energie": ("market", "news"),
    "indices": ("market", "news", "fundamentals"),
    "crypto":  ("market", "social", "news"),
}
ANALYSTES_DEFAUT = ("market", "news")

#: Deux candidats par appel bornent le temps de réponse mesuré d'Opus tout en
#: lui laissant un arbitrage transversal. Ce plafond est une taille de lot,
#: jamais un top-N de l'univers : toute la file est consommée par vagues.
ENTRY_BATCH_SIZE = 2
_GLM_LOCK = threading.Lock()

# Bornes propres au travailleur asynchrone. Elles ne touchent pas au moteur de
# trading et toute valeur explicite de configuration reste prioritaire.
# Un appel fournisseur bloque ne doit pas faire expirer toute la file.
LLM_CALL_TIMEOUT_S = 90.0
LLM_MAX_RETRIES = 0

#: Coupe-circuit de quota. Sans lui, quatre travailleurs parallèles
#: continuent de marteler un fournisseur épuisé, des dizaines d'appels par
#: délibération, pendant des heures — pour ne produire que du neutre.
_QUOTA: dict = {"epuise_a": 0.0}
REPOS_QUOTA_S = 600.0


def quota_epuise() -> float:
    """Secondes restantes avant nouvelle tentative. 0 si la voie est libre."""
    reste = REPOS_QUOTA_S - (time.time() - _QUOTA["epuise_a"])
    return max(0.0, reste)


#: Journal de coût des délibérations. Une ligne par appel, append-only.
#:
#: Séparé de `avis_rendus.ndjson` À DESSEIN : ajouter un champ au dataclass
#: `Avis` changerait un schéma déjà persisté, et les lignes anciennes
#: deviendraient des lignes « sans coût » indiscernables d'un coût nul — le
#: piège du 0.0 ambigu qui a déjà coûté une conclusion fausse sur `cost_r`.
#: Un journal distinct n'a aucun passé à réinterpréter.
COUT_LLM = RACINE / "results" / "cout_llm.ndjson"


def journaliser_cout(symbole: str, classe: str, analystes, duree_s: float,
                     rating: str, source: str) -> bool:
    """Écrit le coût d'UNE délibération. Ne lève jamais.

    Ce qui manquait : la durée était mesurée puis jetée à l'écran, et le
    quota vivait en mémoire — redémarrer le service effaçait toute trace de
    ce qui avait été dépensé. Impossible de répondre à « combien coûte la
    délibération » autrement qu'à l'estime.

    Les jetons ne sont pas ici : ils ne sont pas exposés à cette couture,
    `propagate` ne rend que l'avis. Les obtenir demande d'instrumenter le
    client LLM dans le socle forké — décision d'architecture, pas de mesure.
    La durée et le nombre d'analystes sont les deux grandeurs disponibles, et
    elles suffisent à comparer les classes d'actifs entre elles.
    """
    try:
        ligne = {
            "at": datetime.now(timezone.utc).isoformat(),
            "symbol": symbole,
            "classe": classe,
            "analystes": list(analystes or ()),
            "n_analystes": len(analystes or ()),
            "duree_s": round(float(duree_s), 2),
            "rating": rating or "",
            "source": source or "",
        }
        COUT_LLM.parent.mkdir(parents=True, exist_ok=True)
        with COUT_LLM.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return True
    except Exception:  # noqa: BLE001 — une mesure ne bloque jamais le service
        return False

_stop = False


def _arreter(*_):
    global _stop
    _stop = True
    print("\n  arrêt demandé…", flush=True)


def deliberer(demande, deliberateur) -> Avis:
    """Soumet la lecture de Titanium et convertit la réponse en avis.

    Toute erreur devient un avis **neutre** plutôt qu'une exception : un
    analyste muet ne doit pas peser sur la taille, ni dans un sens ni dans
    l'autre. C'est ce qui permet au trading de survivre à une panne du
    fournisseur sans rien changer à son comportement.
    """
    # Les analystes interrogent des fournisseurs PUBLICS : ils ne
    # connaissent pas les noms du courtier. Sans traduction, Yahoo répond
    # 404 sur `NK225.FS` et l'avis se rend sans la moindre donnée.
    from titanium.data.mt5_dataflows import ticker_public
    from titanium.deliberation import conviction_from_rating
    public = ticker_public(demande.symbol) or demande.symbol

    try:
        note = deliberateur.rating_for(public, demande.resume())
    except Exception as exc:                   # noqa: BLE001
        # ⚠️ Distinguer un QUOTA d'une erreur quelconque. Les deux rendaient
        # « sans note · conviction 0.50 », indiscernable d'un vrai Hold : le
        # travailleur a brûlé des heures sur un quota mort sans que rien ne
        # le signale. Constaté le 07/08/2026 — palier gratuit Gemini à
        # 20 requêtes/jour, épuisé dès le premier setup.
        txt = str(exc)
        epuise = ("RESOURCE_EXHAUSTED" in txt or "429" in txt
                  or "quota" in txt.lower() or "credit" in txt.lower())
        if epuise:
            _QUOTA["epuise_a"] = time.time()
        return Avis(demande.symbol, demande.side, NEUTRE, "",
                    None, ("QUOTA ÉPUISÉ — " if epuise else "échec — ")
                    + txt[:130],
                    demande.bar_time, "",
                    "quota" if epuise else "erreur")

    try:
        conviction = float(conviction_from_rating(str(note), demande.side))
    except Exception:                          # noqa: BLE001
        conviction = NEUTRE

    # Le sens que les analystes privilégient, déduit de la note. Sert
    # uniquement à repérer un désaccord de direction — lequel réduit la
    # taille, jamais la décision.
    bas = str(note).lower()
    cote = 1 if bas in ("buy", "overweight") else (
        -1 if bas in ("sell", "underweight") else 0)

    trace = getattr(deliberateur, "last", {}) or {}
    return Avis(
        symbol=demande.symbol, side=cote or demande.side,
        conviction=conviction, rating=str(note),
        accord=(cote == demande.side) if cote else None,
        resume=str(trace.get("summary", ""))[:500],
        bar_time=demande.bar_time, source="graphe",
    )


def construire_deliberateur(analystes=ANALYSTES_DEFAUT):
    """Instancie un délibérateur pour un jeu d'analystes donné."""
    try:
        from datetime import date

        from titanium.deliberation import GraphDeliberator
        from tradingagents.default_config import DEFAULT_CONFIG
        # La date de trade borne le cache du graphe : une note du jour ne
        # doit pas resservir demain.
        cfg = DEFAULT_CONFIG.copy()
        if cfg.get("llm_timeout") in (None, ""):
            cfg["llm_timeout"] = LLM_CALL_TIMEOUT_S
        if cfg.get("llm_max_retries") in (None, ""):
            cfg["llm_max_retries"] = LLM_MAX_RETRIES
        return GraphDeliberator(trade_date=date.today().isoformat(),
                                analystes=tuple(analystes), config=cfg)
    except Exception as exc:                   # noqa: BLE001
        print(f"  ! délibérateur indisponible : {str(exc)[:160]}")
        print("    le travailleur tourne à vide ; la boucle de trading")
        print("    continue normalement avec une conviction neutre.")
        return None


#: Un délibérateur par jeu d'analystes — le graphe est construit une fois
#: puis réutilisé, sa mise en place coûtant plusieurs secondes.
_DELIBERATEURS: dict = {}


def _classe_pour(symbole: str) -> str:
    """Classe d'actif, ou chaîne vide. Ne lève jamais : sert au journal."""
    try:
        from titanium.edge import asset_class_of
        return asset_class_of(symbole) or ""
    except Exception:  # noqa: BLE001
        return ""


def deliberateur_pour(classe: str):
    """Délibérateur adapté à la classe d'actif, mis en cache."""
    from titanium.edge import asset_class_of  # noqa: F401

    analystes = ANALYSTES_PAR_CLASSE.get(classe or "", ANALYSTES_DEFAUT)
    cle = tuple(analystes)
    if cle not in _DELIBERATEURS:
        _DELIBERATEURS[cle] = construire_deliberateur(analystes)
    return _DELIBERATEURS[cle], analystes


def _publier_cortex(demande, identity, avis_local: Avis) -> None:
    """Publie l'avis exact puis sa politique de contexte a courte duree.

    La politique est la voie rapide d'Hermes : la boucle suivante peut la
    relire localement sans attendre un nouvel appel LLM. Elle ne contient
    volontairement aucun prix, lot, stop ou ordre.
    """
    proposal = {
        **identity.to_dict(),
        "evidence_digest": avis_local.evidence_digest,
        "action": avis_local.action,
        "confidence": avis_local.conviction,
        "summary": avis_local.resume,
        "sources": avis_local.sources,
        "rendered_at": avis_local.rendu_a,
        "decision_model_version": avis_local.model_version,
        "producer": avis_local.source,
    }
    try:
        CENTRAL_MEMORY.record_proposal(identity, proposal)
        context_key = str(demande.engine_context)
        ttl_s = policy_ttl_s(context_key)
        observed = market_observed_at(demande.bar_time, context_key, demande.demande_a)
        policy = build_cortex_policy(
            identity,
            context_key=context_key,
            action=avis_local.action,
            confidence=avis_local.conviction,
            summary=avis_local.resume,
            evidence_digest=avis_local.evidence_digest,
            producer=avis_local.source or "cortex-inconnu",
            source_observed_at=observed.isoformat(),
            ttl_s=ttl_s,
            decision_model_version=avis_local.model_version,
        )
        CENTRAL_MEMORY.record_policy(policy)
    except Exception as exc:  # noqa: BLE001 - le moteur restera en WAIT
        CENTRAL_MEMORY.alert(
            "BRAIN_PROPOSAL_WRITE_FAILED", identity, type(exc).__name__,
        )


def _traiter(d):
    from titanium.fundamental_intelligence import analyse

    t0 = time.time()
    identity = d.sceller()
    # Le pool conserve la publication "le plus rapide d'abord" et sa
    # tolerance aux futures sources reseau. Le modele local CPU, lui, reste
    # serialise pour eviter quatre generations concurrentes qui se bloquent.
    with _GLM_LOCK:
        result = analyse(
            d.symbol, d.side, d.resume(),
            decision_ref=identity.decision_ref,
            context_digest=identity.context_digest,
            model_version=identity.model_version,
            prompt_version=identity.prompt_version,
        )
    action = str(result.get("action", "WAIT")).upper()
    confidence = float(result.get("confidence", 0.0) or 0.0)
    avis_local = Avis(
        symbol=d.symbol, side=d.side,
        conviction=max(0.0, min(1.0, confidence)),
        rating=action, accord=(action == "ALLOW"),
        resume=str(result.get("summary", ""))[:500],
        bar_time=d.bar_time, source="cortex-local-multisource",
        action=action, sources=list(result.get("sources", ())),
        decision_ref=identity.decision_ref,
        context_digest=identity.context_digest,
        evidence_digest=str(result.get("evidence_digest", "")),
        model_version=str(result.get("model_version", identity.model_version)),
        prompt_version=str(result.get("prompt_version", identity.prompt_version)),
    )
    avis_local.rendu_a = datetime.now(timezone.utc).isoformat()
    _publier_cortex(d, identity, avis_local)
    return d, avis_local, time.time() - t0, ("cortex-local",)


def _traiter_lot(demandes):
    """Fait arbitrer un lot par Hermès; toute panne publie uniquement WAIT."""
    from titanium.hermes_cortex import (
        HERMES_SOURCE,
        HermesCortexUnavailable,
        analyse_entries,
    )

    demandes = list(demandes)
    if not demandes:
        return []
    t0 = time.time()
    identities = [demande.sceller() for demande in demandes]
    payloads = [
        {
            "symbol": demande.symbol,
            "side": demande.side,
            "mechanical_summary": demande.resume(),
            "decision_ref": identity.decision_ref,
            "context_digest": identity.context_digest,
            "model_version": identity.model_version,
            "prompt_version": identity.prompt_version,
            "context_key": demande.engine_context,
            "bar_time": demande.bar_time,
            "requested_at": demande.demande_a,
            "piliers": demande.piliers,
            "total_piliers": demande.total_piliers,
            "observations": compact_observations(demande.indicateurs),
            "asset_class": _classe_pour(demande.symbol),
        }
        for demande, identity in zip(demandes, identities, strict=True)
    ]
    results = []
    active_indices = []
    now = datetime.now(timezone.utc)
    for index, demande in enumerate(demandes):
        try:
            observed = market_observed_at(
                demande.bar_time, demande.engine_context, demande.demande_a,
            )
            fresh = now <= observed + timedelta(seconds=policy_ttl_s(demande.engine_context))
        except (TypeError, ValueError):
            fresh = False
        results.append({
            "action": "WAIT", "confidence": 0.0,
            "summary": "CORTEX_REQUEST_STALE: source ou timeframe inexploitable",
            "sources": [], "model_version": "none", "source": "cortex-request-guard",
            "evidence_digest": digest({"decision_ref": identities[index].decision_ref,
                                       "state": "CORTEX_REQUEST_STALE"}),
            "prompt_version": identities[index].prompt_version,
        })
        if fresh:
            active_indices.append(index)
    selected_payloads = [payloads[index] for index in active_indices]
    with _GLM_LOCK:
        try:
            active_results = analyse_entries(selected_payloads) if selected_payloads else []
        except HermesCortexUnavailable as exc:
            print(f"  Hermes indisponible ({exc}); nouvelles entrees en WAIT", flush=True)
            active_results = [
                {
                    "action": "WAIT",
                    "confidence": 0.0,
                    "summary": f"Hermes indisponible: {type(exc).__name__}",
                    "sources": [],
                    "evidence_digest": digest({
                        "decision_ref": payload["decision_ref"],
                        "state": "HERMES_UNAVAILABLE",
                    }),
                    "model_version": "none",
                    "prompt_version": payload["prompt_version"],
                    "source": "hermes-unavailable",
                }
                for payload in selected_payloads
            ]
    for index, result in zip(active_indices, active_results, strict=True):
        results[index] = result
    elapsed = time.time() - t0
    out = []
    for demande, identity, result in zip(demandes, identities, results, strict=True):
        action = str(result.get("action", "WAIT")).upper()
        confidence = float(result.get("confidence", 0.0) or 0.0)
        avis_local = Avis(
            symbol=demande.symbol,
            side=demande.side,
            conviction=max(0.0, min(1.0, confidence)),
            rating=action,
            accord=(action == "ALLOW"),
            resume=str(result.get("summary", ""))[:500],
            bar_time=demande.bar_time,
            source=str(result.get("source", HERMES_SOURCE)),
            action=action,
            sources=list(result.get("sources", ())),
            decision_ref=identity.decision_ref,
            context_digest=identity.context_digest,
            evidence_digest=str(result.get("evidence_digest", "")),
            model_version=str(result.get("model_version", identity.model_version)),
            prompt_version=str(result.get("prompt_version", identity.prompt_version)),
        )
        avis_local.rendu_a = datetime.now(timezone.utc).isoformat()
        _publier_cortex(demande, identity, avis_local)
        out.append((demande, avis_local, elapsed, (avis_local.source,)))
    return out


def _traiter_positions() -> int:
    """Traite toutes les positions prioritaires via Hermès en un seul appel."""
    from titanium.hermes_cortex import (
        HermesCortexUnavailable,
        analyse_positions,
    )
    from titanium.position_sentiment import append_record, pending_reviews

    requests = pending_reviews(POSITION_REQUESTS, POSITION_VERDICTS, limit=8)
    if not requests:
        return 0
    with _GLM_LOCK:
        try:
            verdicts = analyse_positions(requests)
        except HermesCortexUnavailable as exc:
            print(f"  Hermes positions indisponible ({exc}); protections locales actives", flush=True)
            verdicts = [{
                "request_ref": request["request_ref"],
                "ticket": request["ticket"], "symbol": request["symbol"],
                "state": "UNKNOWN", "confidence": 0.0,
                "reason": "HERMES_UNAVAILABLE", "model_version": "none",
                "source": "hermes-unavailable",
                "rendered_at": datetime.now(timezone.utc).isoformat(),
            } for request in requests]
    written = 0
    for verdict in verdicts:
        written += int(append_record(POSITION_VERDICTS, verdict))
        print(
            f"  position {verdict.get('symbol', '?'):10} "
            f"#{verdict.get('ticket', '?')} -> {verdict.get('state', 'UNKNOWN')} "
            f"({float(verdict.get('confidence', 0.0) or 0.0):.2f})",
            flush=True,
        )
    return written


def passage(_inutilise=None) -> int:
    # Purge d'abord : relire à chaque passage un historique qu'on vient de
    # décider d'ignorer coûte du temps et masque la charge réelle.
    jetees = purger(DEMANDES)
    if jetees:
        print(f"  {jetees} demande(s) périmée(s) écartée(s)", flush=True)

    reste = quota_epuise()
    if reste:
        print(f"  quota épuisé — reprise dans {reste:.0f} s. Les demandes "
              "restent en file, aucune n'est perdue.", flush=True)
        return _traiter_positions()

    en_attente = demandes_en_attente(
        DEMANDES, AVIS, maxi=ENTRY_BATCH_SIZE,
    )
    n = 0
    if en_attente:
        # Une entree crypto M1 devient vite obsolete. Elle passe avant les
        # revues periodiques de positions, sans jamais bloquer la boucle MT5
        # qui tourne dans un autre processus.
        for d, avis, duree, analystes in _traiter_lot(en_attente):
            if _stop:
                break
            enregistrer(avis, AVIS)
            journaliser_cout(d.symbol, _classe_pour(d.symbol), analystes,
                             duree, avis.rating, avis.source)
            n += 1
            accord = {True: "d'accord", False: "EN DÉSACCORD",
                      None: "sans direction"}[avis.accord]
            print(f"  {d.symbol:10} {d.piliers}/{d.total_piliers} piliers · "
                  f"{'+'.join(analystes)} → {avis.rating or 'sans note'} · "
                  f"conviction {avis.conviction:.2f} · {accord} "
                  f"({duree:.0f} s pour {len(en_attente)} avis)", flush=True)

    # Les positions restent revues a chaque passage, mais ne peuvent plus
    # retarder une impulsion d'entree qui attend deja dans la file.
    n_positions = _traiter_positions()
    return n + n_positions


def main() -> int:
    configure_console_output()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--une-fois", action="store_true")
    ap.add_argument("--intervalle", type=float, default=INTERVALLE)
    a = ap.parse_args()

    signal.signal(signal.SIGINT, _arreter)

    print("═" * 70)
    print("  TITANIUM V14 — analystes")
    print("═" * 70)
    print(f"  demandes : {DEMANDES}")
    print(f"  avis     : {AVIS}")
    print("\n  Hermes/Claude Code est le cortex principal asynchrone.")
    print("  Hermes decide; indisponibilite = WAIT/UNKNOWN; protections DEMO actives.\n")

    deliberateur = None

    if a.une_fois:
        print(f"\n{passage(deliberateur)} demande(s) traitée(s)")
        return 0

    while not _stop:
        try:
            n = passage()
            if n == 0:
                print(f"  [{time.strftime('%H:%M:%S')}] rien en attente",
                      flush=True)
        except Exception as exc:               # noqa: BLE001
            # Le travailleur ne meurt jamais sur une erreur : il réessaiera
            # au tour suivant. C'est ce qui rend le flux « sans coupure ».
            print(f"  ! passage en échec : {str(exc)[:160]}", flush=True)
        for _ in range(int(a.intervalle)):
            if _stop:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
