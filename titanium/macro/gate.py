"""Pont entre le verdict macro et le vecteur de features qui alimente la porte.

Proprietaire unique de la question « sous quelle forme le macro entre-t-il dans
une decision ? ». Le bloc produit ne contient AUCUN texte libre : deux booleens
que la porte peut lire, plus l'etat et le score pour la trace. C'est ce qui
permet a ``titanium.gates.confluence_gate`` de rester pure — elle ne connait
pas ``titanium.macro``, elle lit deux cles comme elle lit ``emotion`` et
``cost``.

**Ce que vaut ``None``.** ``macro_features`` rend ``None`` quand le flux est
eteint, et ``build_feats`` n'ajoute alors AUCUNE cle. C'est la garantie de
non-regression : une installation qui n'active pas le macro produit exactement
le dict d'avant, donc le meme verdict, au bit pres.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from titanium.macro.cache import MacroCache
from titanium.macro.contracts import MacroRisk
from titanium.macro.policy import MacroPolicy

#: Cles que la porte exige quand le bloc est present. Un bloc incomplet fait
#: BLOCK : mieux vaut refuser une entree que lire ``None`` comme « tout va
#: bien », ce qui est exactement l'inverse de l'intention.
MACRO_BLOCK_KEYS = frozenset({"allows_new_risk", "conservative"})


def macro_block(risk: MacroRisk) -> dict[str, Any]:
    """Forme normale du verdict macro telle qu'elle entre dans les features."""
    return {
        "allows_new_risk": bool(risk.allows_new_risk),
        "conservative": bool(risk.conservative),
        # Champs de trace. La porte ne les lit pas pour DECIDER, mais elle les
        # cite dans son motif : un WAIT qui ne nomme pas la publication qui l'a
        # cause oblige a relire le calendrier a la main, donc a ne pas le faire.
        "state": risk.state.value,
        "score": round(float(risk.score), 4),
        "next_event": risk.next_event_title,
        "source_digest": risk.source_digest,
    }


def macro_features(
    symbol: str = "",
    *,
    now: datetime | None = None,
    policy: MacroPolicy | None = None,
    cache: MacroCache | None = None,
) -> dict[str, Any] | None:
    """Bloc macro pour ``symbol``, ou ``None`` quand le flux est eteint.

    Ne rattrape PAS une configuration illisible : un ``config/macro.json``
    malforme doit echouer bruyamment au premier balayage, pas rendre
    discretement un calendrier plus vide que prevu.
    """
    from titanium.macro import load_policy, macro_risk

    politique = policy or load_policy()
    if not politique.enabled:
        return None
    return macro_block(
        macro_risk(now=now, symbols=symbol or None, policy=politique, cache=cache)
    )
