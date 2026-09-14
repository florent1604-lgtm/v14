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


#: Cle de la POSTURE D'EXECUTION graduee. Distincte des cles de veto, et c'est
#: le point : elle n'interdit rien et ne remplace aucun seuil. Son consommateur
#: est le contexte d'arrivee des techniques d'execution (``build_features``),
#: qui s'en sert pour doser l'agressivite. Un producteur qui ne la publie pas
#: laisse le comportement d'avant : elle est ABSENTE, pas fausse.
MACRO_POSTURE_KEY = "tension"


def macro_posture(risk: MacroRisk, *, scale_s: float) -> float:
    """Posture graduee dans [0, 1] : plus la publication approche, plus elle
    demande de la patience. Aucune publication connue rend une posture nulle.

    **Pourquoi une decroissance et pas un seuil.** Un seuil pose sur l'horizon
    du veto (``elevated_within_s``) vaudrait zero partout ou l'execution a lieu :
    ``CLEAR`` signifie precisement que la publication est PLUS LOIN que cet
    horizon. Mesure du 14/09 : tout etat qui execute rend ``score = 0`` et
    ``conservative = False`` par construction, donc une posture batie sur ces
    deux-la, ou sur une coupure au meme horizon, ne module jamais rien. La duree
    du veto sert ici d'ECHELLE DE TEMPS, pas de coupure : la tension decroit
    continument et reste non nulle sur le chemin qui execute.

    Aucune borne nouvelle : la fonction ne lit que des champs deja figes par
    ``risk.py``, et l'echelle est un parametre de politique existant.
    """
    if scale_s <= 0.0:
        return 0.0
    ecart = risk.seconds_to_next
    if ecart is None:
        return 0.0
    ecart = max(0.0, float(ecart))
    return scale_s / (scale_s + ecart)


def macro_block(risk: MacroRisk, *, posture_scale_s: float) -> dict[str, Any]:
    """Forme normale du verdict macro telle qu'elle entre dans les features."""
    return {
        "allows_new_risk": bool(risk.allows_new_risk),
        "conservative": bool(risk.conservative),
        # Posture d'execution graduee. Elle accompagne le verdict, elle ne le
        # remplace pas : le veto reste porte par les deux booleens ci-dessus.
        MACRO_POSTURE_KEY: round(macro_posture(risk, scale_s=posture_scale_s), 6),
        # Champs de trace. La porte ne les lit pas pour DECIDER, mais elle les
        # cite dans son motif : un WAIT qui ne nomme pas la publication qui l'a
        # cause oblige a relire le calendrier a la main, donc a ne pas le faire.
        # `source_digest` a ete retire : personne ne le citait -- ni la porte,
        # ni la telemetrie (qui publie le sien), ni un journal. Etre ecrit n'est
        # pas etre lu.
        "state": risk.state.value,
        "score": round(float(risk.score), 4),
        "next_event": risk.next_event_title,
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
        macro_risk(now=now, symbols=symbol or None, policy=politique, cache=cache),
        posture_scale_s=politique.elevated_within_s,
    )
