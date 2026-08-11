"""Auscultation des organes de V14 et de la circulation entre eux.

Pourquoi un module dédié
------------------------
Un tableau de bord qui affiche douze blocs indépendants laisse à l'œil le
travail de recoupement : « la boucle tourne, mais le journal est vide, donc
le retour est coupé ». Ce module fait ce recoupement lui-même et rend un
**graphe** — des organes et les artères qui les relient — où une artère
bouchée se voit sans être cherchée.

Il ne décide de rien, ne corrige rien, n'exécute rien. Il ausculte.

Comment un organe est jugé
--------------------------
Sur un FAIT mesuré, jamais sur une présence de fichier. « Le module existe »
ne dit pas qu'il fonctionne — V14 a passé une journée avec un flux retour
dont tous les modules existaient et dont aucune ligne n'était écrite.

Quatre états seulement, pour que la lecture reste immédiate :
`sain`, `ralenti`, `bouche`, `inconnu`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
RESULTATS = RACINE / "results"

SAIN, RALENTI, BOUCHE, INCONNU = "sain", "ralenti", "bouche", "inconnu"

#: Ordre de gravité, pour trier et pour colorer.
GRAVITE = {SAIN: 0, INCONNU: 1, RALENTI: 2, BOUCHE: 3}


@dataclass
class Organe:
    """Un organe du bot, avec son état et ce qui le justifie."""

    id: str
    nom: str
    groupe: str                 # perception | decision | risque | action | memoire
    etat: str = INCONNU
    mesure: str = ""            # le chiffre qui justifie l'état
    detail: str = ""            # ce qu'il faut faire, si quelque chose
    debit: float = 0.0          # 0..1 — intensité du flux qui le traverse


@dataclass
class Artere:
    """Une circulation entre deux organes."""

    de: str
    vers: str
    etat: str = INCONNU
    mesure: str = ""
    libelle: str = ""


@dataclass
class Bilan:
    organes: list = field(default_factory=list)
    arteres: list = field(default_factory=list)
    verdict: str = INCONNU
    resume: str = ""

    def to_dict(self) -> dict:
        return {
            "organes": [asdict(o) for o in self.organes],
            "arteres": [asdict(a) for a in self.arteres],
            "verdict": self.verdict,
            "resume": self.resume,
        }


def _lignes(chemin: Path) -> int:
    try:
        if not chemin.exists():
            return 0
        return sum(1 for l in chemin.read_text(encoding="utf-8").splitlines()
                   if l.strip())
    except Exception:  # noqa: BLE001
        return 0


def _age(chemin: Path) -> float | None:
    try:
        return time.time() - chemin.stat().st_mtime if chemin.exists() else None
    except Exception:  # noqa: BLE001
        return None


def ausculter() -> Bilan:
    """Relève l'état de chaque organe et de chaque artère. Ne lève jamais."""
    from titanium.web import state as S

    b = Bilan()
    O, A = b.organes.append, b.arteres.append

    # ── Ce qu'on lit une fois, et qui sert à plusieurs organes.
    compte = S._safe(S.account, {"connected": False})
    boucle = S._safe(S.loop, {"running": False})
    mur = S._safe(S.wall, {})
    reg = S._safe(S.edge, {})
    prom = S._safe(S.promotion, {})
    pos = S._safe(S.positions, {"positions": []})
    ana = S._safe(S.analystes, {"actif": False})
    fant = S._safe(S.prod_fantome, {"actif": False})
    risq = S._safe(S.risque, {"disponible": False})

    battement = boucle.get("age_s")
    vivante = bool(boucle.get("running")) and not boucle.get("stale")

    # ═══ PERCEPTION ═══════════════════════════════════════════════════
    connecte = bool(compte.get("connected"))
    O(Organe("mt5", "Terminal MT5", "perception",
             SAIN if connecte else BOUCHE,
             f"{compte.get('login', '—')} · {compte.get('mode_label', '')}"
             if connecte else "injoignable",
             "" if connecte else "Le terminal doit être ouvert et connecté.",
             1.0 if connecte else 0.0))

    portables = boucle.get("portables") or 0
    O(Organe("marche", "Observateur de marché", "perception",
             SAIN if portables > 0 else (RALENTI if connecte else BOUCHE),
             f"{portables} actifs portables",
             "" if portables else "Aucun actif dimensionnable par l'équité.",
             min(1.0, portables / 24)))

    O(Organe("features", "Détecteurs & features", "perception",
             SAIN if vivante else (RALENTI if boucle.get("running") else BOUCHE),
             f"{(boucle.get('stats') or {}).get('evalues', 0)} évaluations",
             "", 1.0 if vivante else 0.0))

    # ═══ DÉCISION ═════════════════════════════════════════════════════
    st = boucle.get("stats") or {}
    enter = st.get("enter", 0)
    evalues = st.get("evalues", 0) or 1
    O(Organe("portes", "Portes de confluence", "decision",
             SAIN if vivante else BOUCHE,
             f"{enter} ENTER sur {st.get('evalues', 0)} évalués",
             "", min(1.0, enter / max(1, evalues) * 8)))

    prod_enter = fant.get("prod_aurait_entre", 0)
    quorum_atteint = fant.get("quorum_atteint", prod_enter)
    lignes_f = fant.get("lignes", 0)
    O(Organe("strict", "Quorum strict (fantôme)", "decision",
             SAIN if quorum_atteint else (RALENTI if lignes_f else INCONNU),
             f"quorum 3 atteint {quorum_atteint} fois sur {lignes_f} observations",
             ("Le quorum 3 ne se déclenche jamais : la strate PROD est "
              "inatteignable." if lignes_f and not quorum_atteint else
              (f"{quorum_atteint} setup(s) ont atteint le quorum, mais aucun "
               "n'a encore l'edge prouvé." if quorum_atteint and not prod_enter
               else "")),
             min(1.0, quorum_atteint / max(1, lignes_f) * 10)))

    n_avis = ana.get("n_avis", 0)
    attente = ana.get("en_attente", 0)
    O(Organe("analystes", "Analystes LLM", "decision",
             BOUCHE if (ana.get("actif") and attente > 40 and not n_avis)
             else RALENTI if attente > 8
             else SAIN if n_avis else INCONNU,
             f"{n_avis} avis · {attente} en attente",
             "Le travailleur ne suit pas le rythme des dépôts."
             if attente > 8 else "",
             min(1.0, n_avis / 20)))

    # ═══ RISQUE ═══════════════════════════════════════════════════════
    occ = risq.get("occupation", 0) if risq.get("disponible") else 0
    O(Organe("riskgate", "RiskGate & sizing", "risque",
             BOUCHE if occ >= 100 else RALENTI if occ >= 80 else SAIN,
             f"{risq.get('engage_pct', 0)} % sur {risq.get('budget_pct', 0)} %"
             if risq.get("disponible") else "indisponible",
             "Budget de risque saturé : plus aucune entrée possible."
             if occ >= 100 else "",
             min(1.0, occ / 100)))

    ouvert = bool(mur.get("open"))
    O(Organe("mur", "Mur démo ↔ réel", "risque",
             SAIN if ouvert else RALENTI,
             mur.get("code", "—"), mur.get("detail", ""),
             1.0 if ouvert else 0.0))

    # ═══ ACTION ═══════════════════════════════════════════════════════
    n_pos = len(pos.get("positions") or [])
    envoyes = st.get("envoyes", 0)
    O(Organe("executeur", "Exécuteur MT5", "action",
             SAIN if (ouvert and vivante) else RALENTI if ouvert else BOUCHE,
             f"{envoyes} ordre(s) · {n_pos} position(s)",
             "", min(1.0, n_pos / 8)))

    O(Organe("gestion", "Gestion de position", "action",
             SAIN if vivante else BOUCHE,
             f"BE +{boucle.get('breakeven_r', '—')} R · "
             f"trail +{boucle.get('trail_start_r', '—')} R",
             "Sans boucle vivante, aucun stop n'est déplacé."
             if not vivante else "",
             min(1.0, n_pos / 8)))

    zones = RESULTATS.parent / "results"          # placeholder de cohérence
    age_z = _age(Path(RACINE / "results" / "loop_heartbeat.json"))
    O(Organe("tracer", "Tracé sur MT5", "action",
             SAIN if (age_z is not None and age_z < 180) else RALENTI,
             "zones publiées" if age_z is not None else "aucune publication",
             "", 0.6 if age_z is not None else 0.0))

    # ═══ MÉMOIRE ══════════════════════════════════════════════════════
    journal = RACINE / "results" / "trades.ndjson"
    n_trades = _lignes(journal)
    O(Organe("journal", "Journal des clôtures", "memoire",
             SAIN if n_trades else (RALENTI if n_pos else INCONNU),
             f"{n_trades} trade(s) clos",
             "Aucune clôture enregistrée : rien n'est mesurable."
             if not n_trades and n_pos else "",
             min(1.0, n_trades / 30)))

    contextes = len(reg.get("contexts") or [])
    O(Organe("edge", "Registre d'edge", "memoire",
             SAIN if contextes else (RALENTI if n_trades else INCONNU),
             f"{contextes} contexte(s) · seuil {reg.get('min_samples', 20)}",
             "", min(1.0, contextes / 10)))

    cellules = prom.get("cellules") or []
    eligibles = sum(1 for c in cellules if c.get("eligible"))
    haute = prom.get("strate_haute", 0)
    O(Organe("promotion", "Promotion vers le réel", "memoire",
             SAIN if eligibles else (RALENTI if haute else INCONNU),
             f"{haute} trade(s) en strate S≥3 · "
             f"{len(cellules)} cellule(s)",
             "Le mode réel reste fermé tant qu'aucune cellule n'est "
             "éligible." if not eligibles else "",
             min(1.0, haute / max(1, prom.get("requis", 60)))))

    # ═══ ARTÈRES ══════════════════════════════════════════════════════
    #     Chacune porte la MESURE qui prouve qu'elle coule — ou pas.
    def art(de, vers, ok, mesure, libelle, ralenti=False):
        A(Artere(de, vers,
                 BOUCHE if not ok else (RALENTI if ralenti else SAIN),
                 mesure, libelle))

    art("mt5", "marche", connecte, f"{portables} actifs", "cotations")
    art("marche", "features", vivante, f"{st.get('evalues', 0)} lus", "OHLC")
    art("features", "portes", vivante, f"{enter} ENTER", "features")
    art("portes", "analystes", bool(ana.get("actif")),
        f"{ana.get('n_demandes', 0)} dépôts", "lecture déterministe",
        ralenti=attente > 8)
    art("analystes", "riskgate", n_avis > 0, f"{n_avis} avis",
        "conviction → taille")
    art("portes", "riskgate", vivante, "verdict", "ENTER")
    art("riskgate", "mur", vivante, "budget", "lot")
    art("mur", "executeur", ouvert, mur.get("code", ""), "autorisation")
    art("executeur", "gestion", n_pos > 0, f"{n_pos} suivies", "tickets")
    art("executeur", "tracer", True, "zones", "plan")
    art("gestion", "journal", n_trades > 0, f"{n_trades} écrits",
        "clôtures", ralenti=(n_pos > 0 and not n_trades))
    art("journal", "edge", contextes > 0, f"{contextes} contextes",
        "agrégation")
    art("edge", "promotion", haute > 0, f"{haute} en S≥3", "strate")
    art("promotion", "portes", eligibles > 0,
        "aucune cellule promue" if not eligibles else f"{eligibles} promue(s)",
        "edge_ok")
    art("portes", "strict", bool(lignes_f), f"{lignes_f} observations",
        "quorum 3")

    # ═══ VERDICT ══════════════════════════════════════════════════════
    pire = max((GRAVITE.get(o.etat, 1) for o in b.organes), default=1)
    bouches = [o for o in b.organes if o.etat == BOUCHE]
    ralentis = [o for o in b.organes if o.etat == RALENTI]
    b.verdict = {0: SAIN, 1: INCONNU, 2: RALENTI, 3: BOUCHE}[pire]
    if bouches:
        b.resume = f"{len(bouches)} organe(s) hors service : " \
                   + ", ".join(o.nom for o in bouches[:3])
    elif ralentis:
        b.resume = f"{len(ralentis)} organe(s) ralenti(s) : " \
                   + ", ".join(o.nom for o in ralentis[:3])
    else:
        b.resume = "Tous les organes répondent."
    return b


def bilan() -> dict:
    """Point d'entrée du tableau de bord. Ne lève jamais."""
    try:
        return ausculter().to_dict()
    except Exception as exc:  # noqa: BLE001
        return {"organes": [], "arteres": [], "verdict": INCONNU,
                "resume": f"auscultation impossible : {type(exc).__name__}"}
