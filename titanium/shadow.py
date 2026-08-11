"""PROD fantôme : évalue la porte en mode strict sans jamais exécuter.

Le problème que ça résout
-------------------------
Le mode PROD exige `edge_ok=True`, qui exige ≥20 trades clos par contexte,
qui exigent que le bot trade — en EXPLORE, avec un quorum de 2 piliers sur 4
au lieu de 3. On mesure donc sur une population (EXPLORE) pour autoriser une
autre (PROD), qui en est un sous-ensemble strict.

Avant même de trancher si c'est valide, une question plus élémentaire n'a
aucune réponse : **à quelle fréquence le quorum 3 se déclenche-t-il ?** Sans
ce chiffre, tout calendrier d'ouverture du mode PROD est une invention.

Ce module y répond au coût exact de zéro : à chaque évaluation de la boucle,
la porte est rejouée en mode strict et le verdict est journalisé. Rien n'est
exécuté, aucune décision ne change. C'est une observation, pas un second
moteur — et c'est pourquoi elle ne peut rien casser.

Ce qu'il ne fait PAS
--------------------
Il n'injecte pas `edge_ok`. Le registre affiche aujourd'hui une espérance
négative sur la plupart des contextes ; injecter le verdict ferait tomber
`BLOCK_EDGE_NEGATIVE`, qui s'applique **aussi en EXPLORE** — et éteindrait
la boucle d'accumulation qu'on cherche justement à alimenter. Le câblage
d'`edge_ok` est une décision distincte, à prendre par strate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# Le quorum strict, celui du mode PROD. Repris de `confluence_gate` plutôt
# que redéfini : deux constantes qui divergent finiraient par mentir.
from titanium.gates.confluence_gate import QUORUM_PROD


def observer(feats: dict, symbol: str, verdict_reel: str,
             journal: Path) -> dict | None:
    """Rejoue la porte en mode strict et journalise l'écart.

    ``verdict_reel`` est le verdict qui a effectivement piloté la boucle.
    On ne journalise que les évaluations où les deux modes *divergent* ou
    bien où le mode strict dit ENTER — le reste est du bruit qui noierait
    le signal utile.

    Ne lève jamais : une observation ne doit pas pouvoir arrêter le trading.
    Rend la ligne écrite, ou ``None``.
    """
    try:
        from titanium.gates import confluence_gate

        strict = confluence_gate.evaluate(
            feats, require_edge=True, quorum=QUORUM_PROD)
        verdict_strict = getattr(strict, "verdict", None) or "?"

        interessant = verdict_strict == "ENTER" or verdict_strict != verdict_reel
        if not interessant:
            return None

        trace = feats.get("_trace") or {}
        ligne = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "bar_time": trace.get("bar_time", ""),
            "verdict_explore": verdict_reel,
            "verdict_prod": verdict_strict,
            # `code` est le reason-code stable ; `reasons` est la liste
            # lisible. On garde le code pour agréger, la première raison
            # pour comprendre.
            "code_prod": getattr(strict, "code", "") or "",
            "motif_prod": (getattr(strict, "reasons", None) or [""])[0],
            # Seulement les quatre piliers de support. Compter toutes les
            # portes passées (données, tendance obligatoire, etc.) affichait
            # par exemple 4 alors que le motif disait correctement 2/4.
            "piliers": int(getattr(strict, "support_passed", 0) or 0),
            "famille": getattr(strict, "setup_family", ""),
            "side": getattr(strict, "side", 0),
            # `edge_ok` n'est injecté par personne aujourd'hui : on note ce
            # que la porte a réellement vu, pas ce qu'on suppose.
            "edge_ok_vu": (feats.get("cost") or {}).get("edge_ok"),
        }

        journal = Path(journal)
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")
        return ligne
    except Exception:  # noqa: BLE001 — l'observation ne casse jamais le trading
        return None


def resume(journal: Path) -> dict:
    """Compte ce que le mode strict aurait fait, sur le journal accumulé."""
    journal = Path(journal)
    if not journal.exists():
        return {"lignes": 0}

    total = prod_enter = explore_seul = quorum_atteint = edge_non_prouve = 0
    motifs: dict[str, int] = {}
    par_symbole: dict[str, int] = {}

    for ligne in journal.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne:
            continue
        try:
            d = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        total += 1
        if d.get("verdict_prod") == "ENTER":
            prod_enter += 1
            quorum_atteint += 1
            s = d.get("symbol", "?")
            par_symbole[s] = par_symbole.get(s, 0) + 1
        elif d.get("verdict_explore") == "ENTER":
            # Le cas qui compte : EXPLORE a tradé, PROD aurait refusé.
            explore_seul += 1
            m = d.get("code_prod") or d.get("motif_prod", "?")
            motifs[m] = motifs.get(m, 0) + 1
            # Avant ce correctif, `piliers` comptait toutes les portes. Le
            # reason-code reste donc la preuve rétrocompatible que le quorum
            # avait été franchi avant le blocage d'edge.
            if m in {"BLOCK_EDGE_UNPROVEN", "BLOCK_EDGE_NEGATIVE"}:
                quorum_atteint += 1
            if m == "BLOCK_EDGE_UNPROVEN":
                edge_non_prouve += 1

    return {
        "lignes": total,
        "prod_aurait_entre": prod_enter,
        "quorum_atteint": quorum_atteint,
        "edge_non_prouve": edge_non_prouve,
        "explore_seul": explore_seul,
        "motifs_de_refus": dict(sorted(motifs.items(),
                                       key=lambda kv: -kv[1])[:8]),
        "par_symbole": dict(sorted(par_symbole.items(),
                                   key=lambda kv: -kv[1])[:10]),
    }
