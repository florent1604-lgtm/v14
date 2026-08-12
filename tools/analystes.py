"""Travailleur d'analyse — fait délibérer les analystes hors du chemin critique.

    .venv\\Scripts\\python.exe tools\\analystes.py            # boucle continue
    .venv\\Scripts\\python.exe tools\\analystes.py --une-fois # un passage

Il consomme les demandes déposées par `tools/live_demo.py`, soumet la
lecture déterministe de Titanium aux analystes de V13, et écrit leur avis.
La boucle de trading relit ces avis sans jamais attendre.

Pourquoi un processus séparé
-----------------------------
Une délibération prend des minutes ; la boucle tourne en 60 secondes. Les
mettre dans le même fil ferait attendre le trading derrière un service
externe. Séparés, une panne des analystes — quota épuisé, fournisseur
saturé, réseau coupé — laisse le trading intact : il retombe simplement sur
une conviction neutre.

C'est aussi ce qui permet de couper les coûts d'un geste : arrêter ce
processus n'arrête rien d'autre.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.console_output import configure_console_output  # noqa: E402

from titanium.avis import (  # noqa: E402
    NEUTRE, Avis, demandes_en_attente, enregistrer, purger,
)

DEMANDES = RACINE / "results" / "avis_demandes.ndjson"
AVIS = RACINE / "results" / "avis_rendus.ndjson"

INTERVALLE = 90.0

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

#: Délibérations menées de front. Un appel LLM est de l'attente réseau, pas
#: du calcul : les paralléliser multiplie le débit sans coûter de CPU.
#: Quatre suffisent à passer de 31 à ~120 avis/heure, au-dessus des
#: 54 dépôts/heure mesurés.
PARALLELE = 4

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
    from titanium.deliberation import conviction_from_rating

    # Les analystes interrogent des fournisseurs PUBLICS : ils ne
    # connaissent pas les noms du courtier. Sans traduction, Yahoo répond
    # 404 sur `NK225.FS` et l'avis se rend sans la moindre donnée.
    from titanium.data.mt5_dataflows import ticker_public
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

        from tradingagents.default_config import DEFAULT_CONFIG
        from titanium.deliberation import GraphDeliberator
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


def _traiter(d):
    """Une délibération complète. Rend (demande, avis, durée)."""
    from titanium.edge import asset_class_of

    classe = asset_class_of(d.symbol)
    delib, analystes = deliberateur_pour(classe)
    t0 = time.time()
    avis = deliberer(d, delib) if delib else Avis(
        d.symbol, d.side, NEUTRE, "", None,
        "aucun délibérateur configuré", d.bar_time, "", "absent")
    return d, avis, time.time() - t0, analystes


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
        return 0

    en_attente = demandes_en_attente(DEMANDES, AVIS)
    if not en_attente:
        return 0

    from concurrent.futures import ThreadPoolExecutor, as_completed

    n = 0
    # Les délibérations sont menées de front : ce sont des attentes réseau.
    # En séquentiel, le débit plafonnait à 31/heure pour 54 dépôts.
    with ThreadPoolExecutor(max_workers=PARALLELE) as pool:
        futurs = {pool.submit(_traiter, d): d for d in en_attente}
        for futur in as_completed(futurs):
            if _stop:
                for restant in futurs:
                    restant.cancel()
                break
            d, avis, duree, analystes = futur.result()
            enregistrer(avis, AVIS)
            journaliser_cout(d.symbol, _classe_pour(d.symbol), analystes,
                             duree, avis.rating, avis.source)
            n += 1
            accord = {True: "d'accord", False: "EN DÉSACCORD",
                      None: "sans direction"}[avis.accord]
            print(f"  {d.symbol:10} {d.piliers}/{d.total_piliers} piliers · "
                  f"{'+'.join(analystes)} → {avis.rating or 'sans note'} · "
                  f"conviction {avis.conviction:.2f} · {accord} "
                  f"({duree:.0f} s)", flush=True)
    return n


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
    print("\n  Les avis modulent la TAILLE de ±25 % au plus.")
    print("  Ils n'ouvrent, ne ferment et n'empêchent aucune position.\n")

    deliberateur = construire_deliberateur()

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
