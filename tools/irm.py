"""IRM de Titanium V14 — le flux vivant de la chaîne, organe par organe.

    .venv\\Scripts\\python.exe -X utf8 tools/irm.py        # http://localhost:8099

CE QUE C'EST
------------
Une coupe en temps réel de ce que la boucle fait *pendant* qu'elle le fait :
quelles données entrent dans chaque organe, ce que l'organe en calcule, et ce
qu'il laisse passer. Le tableau de bord (8095) répond « où en est le système » ;
l'IRM répond « qu'est-il en train de faire, là, maintenant ».

POURQUOI C'EST UN LECTEUR, JAMAIS UN RÉ-EXÉCUTEUR
--------------------------------------------------
Rejouer la chaîne pour l'observer demanderait le verrou MT5 — celui-là même que
la boucle armée utilise pour décider et pour passer ses ordres. Un observateur
qui affame l'observé ne mesure plus rien (leçon V12 sur le catalogue courtier).
L'IRM lit donc EXCLUSIVEMENT les journaux que la boucle écrit déjà à chaque
tour. Conséquence assumée : elle ne montre rien que la boucle n'ait produit.
Boucle arrêtée ⇒ l'IRM le dit, et n'invente pas une activité.

LES ORGANES, DANS L'ORDRE DU FLUX
----------------------------------
Ils viennent de l'entonnoir réel du battement (`stats.tunnel`) et des codes de
refus journalisés, pas d'un schéma dessiné à la main.

    catalogue → portabilité → détecteurs → portes ET → mémoire d'edge
    → politique → grappes → microstructure → avis LLM → exécution

LECTURE SEULE. Aucune route ne passe d'ordre, ne déplace un stop, n'appelle un
LLM ni ne redémarre quoi que ce soit. Un test le vérifie.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

PORT = 8099
UI = Path(__file__).resolve().parent / "ui"
RESULTS = RACINE / "results"

#: Octets relus au premier attachement d'un journal. Les journaux vivants font
#: plusieurs Mo : repartir de zéro noierait l'affichage sous des heures
#: d'historique et coûterait une seconde de CPU à chaque démarrage.
AMORCE_OCTETS = 256 * 1024

#: Au-delà, le battement est déclaré FIGÉ. Trois tours manqués — même seuil que
#: le tableau de bord, pour que les deux pages ne se contredisent jamais.
FACTEUR_FIGE = 3.0

TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".js": "text/javascript; charset=utf-8"}


# ── Les organes ────────────────────────────────────────────────────────────
#
# (clé, libellé, sous-titre). L'ordre est celui du flux.
ORGANES = [
    ("catalogue",      "Vendeur MT5",     "catalogue courtier balayé"),
    ("portabilite",    "Portabilité",     "coût, marché ouvert, lot minimum"),
    ("detecteurs",     "Détecteurs",      "ATR, S/R, FVG, OTE, VPOC, bougies"),
    ("portes",         "Portes ET",       "6 piliers, véto déterministe"),
    ("memoire",        "Mémoire d'edge",  "espérance mesurée par contexte"),
    ("cortex",         "Cortex Hermès",   "raisonnement Opus 5, hors chemin chaud"),
    ("politique",      "Politique",       "suspensions et dérive"),
    ("grappes",        "Grappes",         "budget de risque corrélé"),
    ("microstructure", "Microstructure",  "carnet L1, coût instantané"),
    ("avis",           "Avis LLM",        "conviction, hors chemin critique"),
    ("execution",      "Exécution",       "mur démo↔réel, ordre"),
]

_LIBELLES = {cle: (lib, sous) for cle, lib, sous in ORGANES}

#: Code de refus → organe qui l'a prononcé. Un code inconnu tombe dans
#: « execution » plutôt que d'être perdu : un refus non attribué reste visible.
ORGANE_DU_REFUS = {
    "COUT_SPREAD": "portabilite",
    "MARCHE_FERME": "portabilite",
    "LOT_MIN_HORS_PORTEE": "portabilite",
    "INTELLIGENCE_GATE": "memoire",
    "FX_SUSPENDU": "politique",
    "DERIVE": "politique",
    "GRAPPE": "grappes",
    "MULTIPOSITION": "grappes",
    "MICROSTRUCTURE": "microstructure",
    "EXECUTION": "execution",
}


def _maintenant() -> datetime:
    return datetime.now(timezone.utc)


def _age(iso: str | None) -> float | None:
    """Âge en secondes d'un horodatage ISO, ou None s'il est illisible."""
    if not iso:
        return None
    try:
        instant = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    return (_maintenant() - instant).total_seconds()


class Journal:
    """Lecteur incrémental d'un NDJSON vivant.

    Retient un décalage en octets et ne relit que ce qui s'est ajouté. Sans
    cela, suivre ``refus_live.ndjson`` (6 Mo, grossissant chaque minute)
    relirait l'intégralité du fichier à chaque rafraîchissement.

    Un fichier qui rétrécit (rotation, purge) fait repartir le décalage à zéro
    plutôt que de rendre des octets pris au milieu d'une ligne.
    """

    def __init__(self, chemin: Path, horodatage: str = "at") -> None:
        self.chemin = chemin
        self.horodatage = horodatage
        self.decalage = -1          # -1 = jamais attaché
        self._verrou = threading.Lock()

    def nouveautes(self, maximum: int = 400) -> list[dict]:
        with self._verrou:
            try:
                taille = self.chemin.stat().st_size
            except OSError:
                return []

            if self.decalage < 0:
                # Premier attachement : on démarre près de la fin.
                self.decalage = max(0, taille - AMORCE_OCTETS)
            elif taille < self.decalage:
                self.decalage = 0

            if taille == self.decalage:
                return []

            try:
                with self.chemin.open("rb") as flux:
                    flux.seek(self.decalage)
                    brut = flux.read()
                    self.decalage = flux.tell()
            except OSError:
                return []

            lignes = brut.split(b"\n")
            # La dernière ligne peut être tronquée par une écriture en cours :
            # on recule le décalage d'autant pour la relire entière au tour
            # suivant, au lieu de la jeter ou d'en rendre une moitié.
            if lignes and lignes[-1]:
                self.decalage -= len(lignes[-1])
            lignes = lignes[:-1] if lignes else []

        sortie = []
        for ligne in lignes[-maximum:]:
            if not ligne.strip():
                continue
            try:
                sortie.append(json.loads(ligne.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
        return sortie


#: Journaux suivis, avec l'organe auquel rattacher leurs évènements.
JOURNAUX = {
    "refus":   Journal(RESULTS / "refus_live.ndjson", "at"),
    "memoire": Journal(RESULTS / "live_memory.ndjson", "at"),
    "avis":    Journal(RESULTS / "avis_rendus.ndjson", "rendu_a"),
    "revue":   Journal(RESULTS / "position_review_verdicts.ndjson", "rendered_at"),
    "grappe":  Journal(RESULTS / "candidats_grappe.ndjson", "at"),
}


def _lire_json(chemin: Path) -> dict:
    """Lit un JSON, rend {} sur toute anomalie.

    Ces fichiers sont réécrits par la boucle pendant qu'on les lit : une
    lecture peut tomber sur un fichier tronqué. Ce n'est pas une erreur, c'est
    une course normale — au tour suivant il sera complet.
    """
    try:
        charge = json.loads(chemin.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return charge if isinstance(charge, dict) else {}


def _pouls() -> dict:
    """Signes vitaux : battement, armement, capital, positions ouvertes."""
    battement = _lire_json(RESULTS / "loop_heartbeat.json")
    intervalle = float(battement.get("intervalle") or 60.0)
    age = _age(battement.get("at"))

    if age is None:
        etat = "arretee"
    elif age > intervalle * FACTEUR_FIGE:
        etat = "figee"
    else:
        etat = "en_cours"

    positions = _lire_json(RESULTS / "positions.json")
    ouvertes = []
    for ticket, p in positions.items():
        if not isinstance(p, dict):
            continue
        ouvertes.append({
            "ticket": ticket,
            "symbole": p.get("symbol", "?"),
            "sens": p.get("side", 0),
            # `r` du journal est le multiplicateur R→prix (|entrée − SL|), PAS
            # un résultat : position_manager.py:514 s'en sert pour convertir un
            # pic exprimé en R vers un niveau de stop en prix. L'appeler
            # « résultat » ferait lire +34.36 R sur UK100 là où le pic réel est
            # +0.17 R — et sommer ces valeurs additionnerait des dollars
            # d'argent avec des points d'indice.
            "unite_r": round(float(p.get("r") or 0.0), 5),
            # Le seul chiffre en R vraiment porté par l'état : l'excursion
            # favorable maximale. C'est un cliquet, il ne redescend jamais.
            "pic_r": round(float(p.get("peak_fav_r") or 0.0), 3),
            "phase": p.get("phase", ""),
            "contexte": p.get("context_key", ""),
            "entree": p.get("entry"),
            "sl": p.get("sl_initial"),
            "tp": p.get("tp_initial"),
        })
    ouvertes.sort(key=lambda x: -x["pic_r"])

    stats = battement.get("stats") or {}
    return {
        "etat": etat,
        "age_s": None if age is None else round(age, 1),
        "intervalle_s": intervalle,
        "battement_a": battement.get("at"),
        "arme": bool(battement.get("armed")),
        "capital": battement.get("equity"),
        "portables": battement.get("portables"),
        "tours": stats.get("tours"),
        "evalues": stats.get("evalues"),
        "enter": stats.get("enter"),
        "envoyes": stats.get("envoyes"),
        "positions": ouvertes,
    }


def _bloc(cle: str, entre, sort, detail: dict) -> dict:
    libelle, soustitre = _LIBELLES[cle]
    perdus = None
    if isinstance(entre, int) and isinstance(sort, int):
        perdus = max(0, entre - sort)
    return {
        "cle": cle, "libelle": libelle, "soustitre": soustitre,
        "entre": entre, "sort": sort, "perdus": perdus,
        "detail": [{"code": k, "n": v}
                   for k, v in sorted(detail.items(), key=lambda x: -x[1]) if v],
    }


def _organes() -> dict:
    """La chaîne, avec les débits relevés dans l'entonnoir du battement.

    Aucun chiffre n'est calculé ici : tous sont relevés dans ce que la boucle a
    compté. Une case vide signifie « la boucle ne compte pas cet étage », pas
    « cet étage n'a rien fait ».
    """
    from titanium.analysis.entry_accounting import entry_balance
    from titanium.analysis.execution_trace import ledger_summary

    stats = _lire_json(RESULTS / "loop_heartbeat.json").get("stats") or {}
    tunnel = stats.get("tunnel") or {}
    flow = tunnel.get("flow") or {}
    verdicts = tunnel.get("gate_verdict") or {}
    post = tunnel.get("post_enter_refusal") or {}
    features = tunnel.get("features") or {}

    enter = verdicts.get("ENTER", 0)
    # ENTER, refus et envois sont tous cumulatifs depuis le même démarrage.
    # Un candidat sans refus journalisé n'est PAS une preuve d'ordre envoyé.
    balance = entry_balance(stats)

    chaine = [
        _bloc("catalogue", flow.get("catalogue"), flow.get("selectionnes"),
              {"fx_illiquides_ecartes": flow.get("fx_illiquides_ecartes", 0)}),
        _bloc("portabilite", flow.get("selectionnes"), flow.get("portables"),
              dict(tunnel.get("portability_refusal") or {})),
        _bloc("detecteurs", flow.get("portables"), features.get("LISIBLE"), {}),
        _bloc("portes", features.get("LISIBLE"), enter,
              {**(tunnel.get("pillar_missing") or {}),
               **{f"verdict {k}": v for k, v in verdicts.items() if k != "ENTER"}}),
        _bloc("memoire", enter, enter - post.get("INTELLIGENCE_GATE", 0),
              {"INTELLIGENCE_GATE": post.get("INTELLIGENCE_GATE", 0)}),
        _bloc("cortex", None, None, {}),
        _bloc("politique", None, None,
              {k: post.get(k, 0) for k in ("FX_SUSPENDU", "DERIVE")}),
        _bloc("grappes", None, None,
              {k: post.get(k, 0) for k in ("GRAPPE", "MULTIPOSITION")}),
        _bloc("microstructure", None, None, dict(tunnel.get("microstructure") or {})),
        _bloc("avis", None, None, {}),
        _bloc("execution", enter, stats.get("envoyes"),
              {"EXECUTION": post.get("EXECUTION", 0)}),
    ]
    return {"chaine": chaine, "supports": tunnel.get("support_passed") or {},
            "entry_accounting": balance,
            "execution_ledger": ledger_summary(RESULTS / "execution_ledger.sqlite3"),
            "execution_trace": stats.get("execution_trace", {"status": "NOT_STARTED"})}


#: Tampon commun des évènements récents, alimenté par UN seul lecteur.
#:
#: Les décalages d'un `Journal` sont un état partagé : si chaque client SSE
#: appelait la lecture, deux onglets se voleraient les évènements (le premier
#: consomme les octets, le second ne voit plus rien) et un simple
#: rechargement afficherait une page vide. Le collecteur lit une fois, tout le
#: monde rend le même tampon.
_TAMPON: deque[dict] = deque(maxlen=600)
_VERROU_TAMPON = threading.Lock()


def _collecter() -> None:
    """Boucle de fond : draine les journaux dans le tampon commun."""
    while True:
        try:
            neufs = _lire_journaux()
        except Exception:  # noqa: BLE001 — un collecteur ne meurt jamais
            neufs = []
        if neufs:
            with _VERROU_TAMPON:
                _TAMPON.extend(neufs)
        time.sleep(2.0)


def _evenements(maximum: int = 140) -> list[dict]:
    """Instantané du tampon commun, du plus ancien au plus récent."""
    with _VERROU_TAMPON:
        return list(_TAMPON)[-maximum:]


def _lire_journaux() -> list[dict]:
    """Fusionne les journaux vivants en un flux unique, trié par instant."""
    flux: list[dict] = []

    for nom, journal in JOURNAUX.items():
        for enr in journal.nouveautes():
            instant = enr.get(journal.horodatage)

            if nom == "refus":
                code = enr.get("code", "?")
                flux.append({
                    "a": instant, "organe": ORGANE_DU_REFUS.get(code, "execution"),
                    "type": "refus", "symbole": enr.get("symbole", "?"),
                    "titre": code, "detail": enr.get("detail", ""),
                    "sens": enr.get("side", 0), "piliers": enr.get("piliers"),
                })
            elif nom == "memoire":
                flux.append({
                    "a": instant, "organe": "memoire", "type": "memoire",
                    "symbole": enr.get("symbol", "?"),
                    "titre": f"{enr.get('action', '?')} · {enr.get('context', '')}",
                    "detail": (f"n={enr.get('samples')} · "
                               f"E={enr.get('expectancy_r')} R · "
                               f"PF={enr.get('profit_factor')}"),
                    "sens": 0, "piliers": None,
                })
            elif nom == "avis":
                flux.append({
                    "a": instant, "organe": "cortex", "type": "avis",
                    "symbole": enr.get("symbol", "?"),
                    "titre": f"{enr.get('rating', '?')} · conviction {enr.get('conviction', '?')}",
                    "detail": (enr.get("resume") or "")[:220],
                    "sens": enr.get("side", 0), "piliers": None,
                    "modele": enr.get("source") or enr.get("model_version"),
                })
            elif nom == "revue":
                flux.append({
                    "a": instant, "organe": "avis", "type": "revue",
                    "symbole": enr.get("symbol", "?"),
                    "titre": f"revue position · {enr.get('state', '?')}",
                    "detail": (enr.get("reason") or "")[:220],
                    "sens": 0, "piliers": None,
                    "modele": enr.get("model_version"),
                })
            elif nom == "grappe":
                flux.append({
                    "a": instant, "organe": "grappes", "type": "grappe",
                    "symbole": enr.get("symbol", "?"),
                    "titre": f"grappe {enr.get('cluster', '?')} · {enr.get('setup_family', '')}",
                    "detail": (f"risque proposé {enr.get('proposed_risk_pct')} % · "
                               f"engagé sur la grappe {enr.get('cluster_risk_engaged_pct')} %"),
                    "sens": enr.get("side", 0),
                    "piliers": enr.get("support_pillars"),
                })

    flux.sort(key=lambda e: str(e.get("a") or ""))
    return flux


#: Les sept sources de faits interrogees par le collecteur fondamental
#: (`titanium/fundamental_intelligence.collect`). Nommees ici pour qu'une
#: source qui cesse d'alimenter soit VISIBLE a zero, au lieu de disparaitre
#: silencieusement de la liste.
SOURCES_CONNUES = ("FRED", "EIA", "ECB", "CoinGecko", "CFTC-COT", "FederalReserve")


def _fin_de_journal(chemin: Path, octets: int = 96 * 1024) -> list[dict]:
    """Derniers enregistrements d'un NDJSON, sans relire tout le fichier.

    Ces vues sont des ETATS, pas des flux : on veut « les derniers avis », pas
    « les avis apparus depuis le dernier passage ». On lit donc une queue
    bornee a chaque appel, plutot que de passer par le tampon incremental.
    """
    try:
        taille = chemin.stat().st_size
        depart = max(0, taille - octets)
        with chemin.open("rb") as flux:
            flux.seek(depart)
            brut = flux.read()
    except OSError:
        return []
    lignes = brut.split(b"\n")
    # La premiere ligne n'est coupee QUE si on a saute du debut du fichier.
    # La jeter systematiquement perdait un enregistrement dans tout journal
    # plus court que la fenetre de lecture — invisible en production, ou les
    # fichiers la depassent tous. Attrape par les tests.
    if depart > 0 and len(lignes) > 1:
        lignes = lignes[1:]
    sortie = []
    for ligne in lignes:
        if not ligne.strip():
            continue
        try:
            enr = json.loads(ligne.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(enr, dict):
            sortie.append(enr)
    return sortie


def _cortex(maximum: int = 12) -> dict:
    """La reflexion d'Hermes : ses verdicts, leurs preuves, son etat de sante.

    L'etat de sante est DEDUIT des verdicts eux-memes, jamais importe du module
    du cortex : le disjoncteur vit dans la memoire du travailleur, pas sur
    disque. Lire la source des derniers verdicts est la seule mesure honnete
    depuis l'exterieur.
    """
    avis = _fin_de_journal(RESULTS / "avis_rendus.ndjson")
    recents = avis[-maximum:][::-1]

    par_source: dict[str, int] = {}
    for enr in avis[-120:]:
        nom = str(enr.get("source") or "?")
        par_source[nom] = par_source.get(nom, 0) + 1

    dernier = avis[-1] if avis else {}
    source = str(dernier.get("source") or "")
    if source.startswith("hermes-cortex/"):
        sante, detail = "hermes", source.split("/", 1)[1]
    elif source:
        sante, detail = "repli", source
    else:
        sante, detail = "muet", "aucun verdict"

    verdicts = [{
        "a": enr.get("rendu_a"),
        "symbole": enr.get("symbol", "?"),
        "sens": enr.get("side", 0),
        "verdict": enr.get("rating") or enr.get("action") or "?",
        "conviction": enr.get("conviction"),
        "accord": enr.get("accord"),
        "raisonnement": str(enr.get("resume") or ""),
        "preuves": list(enr.get("sources") or []),
        "modele": enr.get("model_version") or "",
        "producteur": enr.get("source") or "",
    } for enr in recents]

    return {"sante": sante, "detail": detail, "verdicts": verdicts,
            "par_source": [{"nom": k, "n": v} for k, v in
                           sorted(par_source.items(), key=lambda x: -x[1])],
            "age_s": _age(dernier.get("rendu_a"))}


def _memoire(maximum: int = 14) -> dict:
    """Ce que l'histoire dit du contexte en cours d'evaluation.

    C'est l'organe qui repond a « ce setup a-t-il deja gagne ? ». Chaque ligne
    porte le nombre de trades clos qui la fondent, l'esperance en R et le
    facteur de profit — les trois chiffres sans lesquels un verdict de memoire
    n'est qu'une opinion.
    """
    lignes = _fin_de_journal(RESULTS / "live_memory.ndjson")

    # Un contexte revient a chaque tour : on ne garde que sa derniere lecture,
    # sinon la table repete quinze fois le meme actif.
    dernier_par_contexte: dict[str, dict] = {}
    for enr in lignes:
        cle = str(enr.get("context") or enr.get("symbol") or "?")
        dernier_par_contexte[cle] = enr

    vues = []
    for cle, enr in dernier_par_contexte.items():
        try:
            esperance = float(enr.get("expectancy_r") or 0.0)
        except (TypeError, ValueError):
            esperance = 0.0
        vues.append({
            "a": enr.get("at"),
            "symbole": enr.get("symbol", "?"),
            "contexte": cle,
            "action": str(enr.get("action") or "?"),
            "echantillons": enr.get("samples"),
            "esperance_r": esperance,
            "profit_factor": enr.get("profit_factor"),
            "motif": str(enr.get("reason") or ""),
        })
    # Le rentable d'abord : c'est ce qu'on cherche a voir.
    vues.sort(key=lambda v: -v["esperance_r"])
    rentables = [v for v in vues if v["action"] == "ALLOW"]
    return {"contextes": vues[:maximum], "n_rentables": len(rentables),
            "n_total": len(vues)}


def _revues(maximum: int = 8) -> list[dict]:
    """Reevaluation des positions DEJA ouvertes, une par ticket."""
    lignes = _fin_de_journal(RESULTS / "position_review_verdicts.ndjson")
    par_ticket: dict[str, dict] = {}
    for enr in lignes:
        par_ticket[str(enr.get("ticket") or enr.get("symbol") or "?")] = enr
    sortie = [{
        "a": enr.get("rendered_at"),
        "symbole": enr.get("symbol", "?"),
        "ticket": enr.get("ticket", ""),
        "etat": str(enr.get("state") or "?"),
        "confiance": enr.get("confidence"),
        "raisonnement": str(enr.get("reason") or ""),
        "preuves": list(enr.get("sources") or []),
        "modele": enr.get("model_version") or "",
    } for enr in par_ticket.values()]
    sortie.sort(key=lambda v: str(v.get("a") or ""), reverse=True)
    return sortie[:maximum]


def _injections() -> list[dict]:
    """Quelles sources de faits alimentent reellement les verdicts.

    Une source connue mais absente est rendue a ZERO plutot qu'omise : c'est la
    seule facon de voir qu'un fournisseur est tombe.
    """
    compte = dict.fromkeys(SOURCES_CONNUES, 0)
    total = 0
    for enr in _fin_de_journal(RESULTS / "avis_rendus.ndjson")[-120:]:
        total += 1
        for nom in (enr.get("sources") or []):
            compte[str(nom)] = compte.get(str(nom), 0) + 1
    for enr in _fin_de_journal(RESULTS / "position_review_verdicts.ndjson")[-60:]:
        for nom in (enr.get("sources") or []):
            compte[str(nom)] = compte.get(str(nom), 0) + 1
    return [{"nom": k, "n": v, "part": (v / total if total else 0.0)}
            for k, v in sorted(compte.items(), key=lambda x: -x[1])]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence : le flux SSE inonderait la console
        pass

    def _envoyer(self, corps: bytes, mime: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(corps)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corps)

    def _json(self, charge: object, code: int = 200) -> None:
        self._envoyer(json.dumps(charge, ensure_ascii=False).encode("utf-8"),
                      "application/json; charset=utf-8", code)

    def _fichier(self, chemin: Path) -> None:
        if not chemin.is_file():
            return self._json({"erreur": f"absent : {chemin.name}"}, 404)
        self._envoyer(chemin.read_bytes(),
                      TYPES.get(chemin.suffix, "application/octet-stream"))

    def do_GET(self):  # noqa: N802
        chemin = urlparse(self.path).path.rstrip("/") or "/"

        if chemin == "/":
            return self._fichier(UI / "irm.html")
        if chemin in ("/irm.css", "/irm.js"):
            return self._fichier(UI / chemin.lstrip("/"))
        if chemin == "/api/pouls":
            return self._json(_pouls())
        if chemin == "/api/organes":
            return self._json(_organes())
        if chemin == "/api/flux":
            return self._json({"evenements": _evenements()})
        if chemin == "/api/cortex":
            return self._json(_cortex())
        if chemin == "/api/memoire":
            return self._json(_memoire())
        if chemin == "/api/revues":
            return self._json({"revues": _revues()})
        if chemin == "/api/injections":
            return self._json({"injections": _injections()})
        if chemin == "/api/direct":
            return self._sse()

        self._json({"erreur": "route inconnue"}, 404)

    def _sse(self) -> None:
        """Flux poussé. EventSource se reconnecte seul si la boucle coupe."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                charge = {"pouls": _pouls(), "organes": _organes(),
                          "evenements": _evenements(), "cortex": _cortex(),
                          "memoire": _memoire(), "revues": _revues(),
                          "injections": _injections()}
                bloc = f"data: {json.dumps(charge, ensure_ascii=False)}\n\n"
                self.wfile.write(bloc.encode("utf-8"))
                self.wfile.flush()
                time.sleep(2.0)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Onglet fermé ou rechargé : fin normale d'un flux SSE.
            return


def main() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")

    # ThreadingHTTPServer est obligatoire : un serveur mono-thread serait
    # entièrement bloqué par le premier client SSE, qui ne raccroche jamais.
    serveur = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    serveur.daemon_threads = True

    # Le collecteur remplit le tampon avant le premier client : une page
    # ouverte plus tard montre l'historique récent au lieu d'un écran vide.
    threading.Thread(target=_collecter, daemon=True, name="irm-collecteur").start()
    print(f"\n  IRM V14 — http://localhost:{PORT}")
    print(f"  lecture seule · journaux de {RESULTS}\n")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("\n  arret\n")
    finally:
        serveur.server_close()


if __name__ == "__main__":
    main()
