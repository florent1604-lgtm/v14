"""Parler à Hermès de ses décisions — en lecture, sans rien changer.

    .venv\\Scripts\\python.exe -X utf8 tools/demander_hermes.py BTCUSD
    .venv\\Scripts\\python.exe -X utf8 tools/demander_hermes.py XRPUSD "pourquoi BLOCK ?"
    .venv\\Scripts\\python.exe -X utf8 tools/demander_hermes.py --derniers

CE QUE C'EST
------------
Le cortex rend des verdicts ; l'IRM les affiche. Ce qui manquait, c'est de
pouvoir lui **demander pourquoi**, avec le contexte réel sous les yeux. Cet
outil charge ce que la machine a effectivement vu — verdict des portes, piliers,
mémoire d'edge, dernier avis rendu — et pose la question à Hermès.

CE QUE ÇA NE FAIT PAS
----------------------
Aucun ordre, aucune modification de stop, aucune écriture dans la file d'avis :
la réponse d'Hermès ici est une EXPLICATION pour toi, elle ne devient jamais
une politique et n'entre pas dans la mémoire centrale. Une explication produite
sur demande n'a pas été scellée au moment de la décision ; la promouvoir en
politique reviendrait à réécrire l'histoire après coup.

Un test structurel interdit à ce module tout appel d'ordre et tout import du
chemin d'exécution.
"""

from __future__ import annotations

import json
import sys
from contextlib import suppress
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

RESULTS = RACINE / "results"

#: Schéma de la réponse. Hermès n'a pas le mode `format` d'Ollama : la
#: contrainte passe par le texte, et on lit ce qui revient sans rien exiger de
#: plus — une explication mal formée reste lisible, contrairement à un verdict.
SCHEMA_EXPLICATION = {
    "type": "object",
    "properties": {
        "reponse": {"type": "string"},
        "facteurs": {"type": "array", "items": {"type": "string"}},
        "ce_qui_changerait_l_avis": {"type": "string"},
    },
    "required": ["reponse", "facteurs", "ce_qui_changerait_l_avis"],
}


def _queue(chemin: Path, octets: int = 128 * 1024) -> list[dict]:
    """Derniers enregistrements d'un NDJSON vivant, sans tout relire."""
    try:
        taille = chemin.stat().st_size
        depart = max(0, taille - octets)
        with chemin.open("rb") as flux:
            flux.seek(depart)
            brut = flux.read()
    except OSError:
        return []
    lignes = brut.split(b"\n")
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


def dossier_du_symbole(symbole: str) -> dict:
    """Rassemble ce que la machine a réellement vu sur cet actif.

    On ne recalcule rien : on relit les journaux. Recalculer donnerait un
    contexte plus frais que celui qui a produit le verdict, et Hermès
    expliquerait alors une décision qu'il n'a pas prise.
    """
    sym = symbole.upper()

    avis = [a for a in _queue(RESULTS / "avis_rendus.ndjson")
            if str(a.get("symbol", "")).upper() == sym]
    memoire = [m for m in _queue(RESULTS / "live_memory.ndjson")
               if str(m.get("symbol", "")).upper() == sym]
    refus = [r for r in _queue(RESULTS / "refus_live.ndjson")
             if str(r.get("symbole", "")).upper() == sym]
    revues = [v for v in _queue(RESULTS / "position_review_verdicts.ndjson")
              if str(v.get("symbol", "")).upper() == sym]

    positions = {}
    try:
        positions = json.loads(
            (RESULTS / "positions.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        positions = {}
    ouvertes = [{"ticket": t, **p} for t, p in positions.items()
                if isinstance(p, dict)
                and str(p.get("symbol", "")).upper() == sym]

    return {
        "symbole": sym,
        "dernier_avis": avis[-1] if avis else None,
        "avis_recents": avis[-5:],
        "memoire": memoire[-1] if memoire else None,
        "refus_recents": refus[-6:],
        "derniere_revue": revues[-1] if revues else None,
        "positions_ouvertes": ouvertes,
    }


def _brief(dossier: dict, question: str) -> str:
    """Rédige la question. Factuel, sans conclusion suggérée.

    Un brief qui vend une réponse obtient un accord poli et sans valeur — même
    raison que pour le brief des analystes (`titanium/avis.py`).
    """
    lignes = [
        "Tu es le cortex Hermes de Titanium V14. On te demande d'EXPLIQUER une",
        "decision passee, pas d'en prendre une nouvelle. Sois factuel et bref.",
        "Si les elements ne suffisent pas a conclure, dis-le franchement.",
        "",
        f"ACTIF {dossier['symbole']}",
    ]

    avis = dossier.get("dernier_avis")
    if avis:
        lignes.append(
            f"TON DERNIER VERDICT {avis.get('rating')} conviction "
            f"{avis.get('conviction')} rendu {avis.get('rendu_a')} "
            f"par {avis.get('source')}")
        if avis.get("resume"):
            lignes.append(f"TON RESUME {avis['resume']}")
        if avis.get("sources"):
            lignes.append(f"SOURCES CITEES {', '.join(avis['sources'])}")
    else:
        lignes.append("AUCUN VERDICT rendu sur cet actif dans l'historique recent")

    mem = dossier.get("memoire")
    if mem:
        lignes.append(
            f"MEMOIRE contexte {mem.get('context')} action {mem.get('action')} "
            f"n={mem.get('samples')} esperance={mem.get('expectancy_r')}R "
            f"PF={mem.get('profit_factor')}")

    for r in dossier.get("refus_recents") or []:
        lignes.append(f"REFUS {r.get('code')} {str(r.get('detail'))[:110]}")

    for p in dossier.get("positions_ouvertes") or []:
        lignes.append(
            f"POSITION OUVERTE sens={p.get('side')} entree={p.get('entry')} "
            f"SL={p.get('sl_initial')} pic={p.get('peak_fav_r')}R "
            f"phase={p.get('phase')}")

    revue = dossier.get("derniere_revue")
    if revue:
        lignes.append(f"TA DERNIERE REVUE {revue.get('state')} "
                      f"{str(revue.get('reason'))[:150]}")

    lignes += ["", f"QUESTION DE FLORENT : {question}"]
    return "\n".join(lignes)


def demander(symbole: str, question: str) -> dict:
    """Pose la question a Hermes et rend sa reponse.

    Passe par `interroger_bassins`, comme les appelants de production. Ce n'est
    pas un detail : en s'adressant a `_ask` directement, cet outil ignorait la
    quarantaine et relancait un bassin a sec que la liste savait deja sec.
    """
    from titanium.hermes_cortex import HermesCortexUnavailable, interroger_bassins

    dossier = dossier_du_symbole(symbole)
    prompt = _brief(dossier, question)
    consigne = (
        f"{prompt}\n\n"
        "Reponds UNIQUEMENT par un objet JSON conforme a ce schema, sans texte "
        "autour et sans cloture Markdown :\n"
        f"{json.dumps(SCHEMA_EXPLICATION, ensure_ascii=False)}"
    )
    try:
        return {"ok": True, "dossier": dossier,
                "reponse": interroger_bassins(consigne)}
    except HermesCortexUnavailable as exc:
        return {"ok": False, "dossier": dossier, "erreur": str(exc)}


def _afficher(resultat: dict) -> None:
    d = resultat["dossier"]
    print(f"\n{'=' * 72}")
    print(f"  {d['symbole']}")
    print(f"{'=' * 72}")

    avis = d.get("dernier_avis")
    if avis:
        print(f"  dernier verdict : {avis.get('rating')} "
              f"· conviction {avis.get('conviction')} "
              f"· {avis.get('source')} · {str(avis.get('rendu_a'))[11:19]}")
        print(f"  son resume      : {avis.get('resume')}")
    mem = d.get("memoire")
    if mem:
        print(f"  memoire         : {mem.get('action')} "
              f"n={mem.get('samples')} E={mem.get('expectancy_r')}R "
              f"PF={mem.get('profit_factor')}")
    for p in d.get("positions_ouvertes") or []:
        print(f"  position        : sens {p.get('side')} "
              f"entree {p.get('entry')} pic {p.get('peak_fav_r')}R")

    print(f"\n{'-' * 72}")
    if not resultat["ok"]:
        print(f"  HERMES INDISPONIBLE : {resultat['erreur']}")
        print("  (le repli local ne repond pas aux questions ouvertes)")
        return

    r = resultat["reponse"]
    print("  REPONSE D'HERMES\n")
    print(f"  {r.get('reponse', '(vide)')}\n")
    facteurs = r.get("facteurs") or []
    if facteurs:
        print("  facteurs retenus :")
        for f in facteurs:
            print(f"    - {f}")
    if r.get("ce_qui_changerait_l_avis"):
        print(f"\n  ce qui changerait son avis :\n    {r['ce_qui_changerait_l_avis']}")
    print()


def _derniers(n: int = 10) -> None:
    """Les derniers verdicts, tous actifs confondus."""
    avis = _queue(RESULTS / "avis_rendus.ndjson")[-n:][::-1]
    if not avis:
        print("  aucun verdict dans l'historique recent")
        return
    print(f"\n  {n} derniers verdicts du cortex\n")
    for a in avis:
        print(f"  {str(a.get('rendu_a'))[11:19]}  {str(a.get('symbol')):10} "
              f"{str(a.get('rating')):6} conv {a.get('conviction')}  "
              f"{str(a.get('source'))}")
        print(f"      {str(a.get('resume'))[:150]}")
    print()


def main() -> int:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        with suppress(OSError, ValueError):
            reconfigure(encoding="utf-8", errors="replace")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    options = {a for a in sys.argv[1:] if a.startswith("--")}

    if "--derniers" in options or not args:
        _derniers()
        if not args:
            print("  usage : demander_hermes.py SYMBOLE [question]")
            return 0
        return 0

    symbole = args[0]
    question = " ".join(args[1:]) or (
        "Explique ta derniere decision sur cet actif : sur quoi t'es-tu appuye, "
        "et qu'est-ce qui te ferait changer d'avis ?")
    _afficher(demander(symbole, question))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
