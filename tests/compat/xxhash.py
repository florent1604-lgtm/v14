"""Substitut pur Python de `xxhash`, pour les tests seulement.

POURQUOI CE MODULE EXISTE
--------------------------
Smart App Control refuse de charger `_xxhash.cp312-win_amd64.pyd` : la roue
PyPI n'est pas signee Authenticode et sa prevalence est trop faible pour que
l'Intelligent Security Graph lui accorde une reputation. Mesure du 07/09/2026,
journal `Microsoft-Windows-CodeIntegrity/Operational`, evenements 3033/3077 :

    Code Integrity determined that a process ... attempted to load
    ...\\site-packages\\xxhash\\_xxhash.cp312-win_amd64.pyd that did not meet
    the Enterprise signing level requirements (Policy ID:{0283ac0f-...})

Verifie : recreer le venv depuis un Python **signe** ne change rien — le
blocage porte sur le module charge, pas sur le processus qui le charge.
`pip install --force-reinstall` reinstalle le meme binaire, bit pour bit.
Aucune version de `langsmith` depuis la 0.9 ne se passe de cette dependance.

Consequence : 30 fichiers de tests devenaient incollectables, par la chaine
`tests -> tradingagents -> langchain -> langsmith._internal._uuid -> xxhash`.

CE QUE CE SUBSTITUT COUVRE, ET RIEN DE PLUS
--------------------------------------------
`langsmith` n'appelle `xxhash` qu'a UN endroit — `uuid7_deterministic`, qui
derive un identifiant de replique LangSmith a partir de `xxh3_128(...).digest()`.
V14 n'utilise pas les repliques LangSmith : cette valeur n'a besoin d'etre que
**deterministe**, jamais identique aux digests officiels de xxHash.

Seuls `xxh3_128` et `xxh3_128_hexdigest` sont fournis, car les dependances
installees ne consomment que ces deux points. LangGraph les utilise aussi pour
ses identifiants de taches et checkpoints : leur compatibilite avec des donnees
produites par le backend natif N'EST PAS validee par ce substitut.
Tout le reste de la surface de `xxhash` leve `AttributeError`. C'est
volontaire et c'est le principe fail-closed du depot : un futur appelant qui
aurait besoin de vrais digests xxHash doit echouer bruyamment, jamais recevoir
en silence une valeur qui ressemble a un hash sans en etre un.

PORTEE
------
Charge uniquement par `tests/conftest.py`, jamais par la boucle de trading —
qui n'importe pas `langchain` et tourne donc sans ce substitut.

⚠️ Ce module traite un symptome. La cause est structurelle : les 419 extensions
natives du venv sont TOUTES non signees, numpy, pandas, scipy et sklearn
comprises. Elles ne passent aujourd'hui que par reputation. Le correctif de
fond est d'executer la suite hors du perimetre Smart App Control (WSL2 ou
conteneur), pas de multiplier les substituts.
"""

from __future__ import annotations

import hashlib

VERSION = "0.0.0-substitut-v14"
XXHASH_VERSION = VERSION


class _Digest:
    """Resultat deterministe, de la meme forme que celui de `xxhash`."""

    __slots__ = ("_octets",)

    def __init__(self, donnees: bytes, taille: int) -> None:
        # Bibliotheque standard, sans la roue tierce xxhash. hashlib peut lui
        # aussi utiliser une extension native : ce n'est pas un remede a SAC.
        self._octets = hashlib.blake2b(donnees, digest_size=taille).digest()

    def digest(self) -> bytes:
        return self._octets

    def hexdigest(self) -> str:
        return self._octets.hex()

    def intdigest(self) -> int:
        return int.from_bytes(self._octets, "big")


def _fabrique(taille: int):
    def _hacheur(donnees: bytes = b"", seed: int = 0) -> _Digest:
        graine = seed.to_bytes(8, "little") if seed else b""
        return _Digest(graine + bytes(donnees), taille)
    return _hacheur


# API objet. `xxh3_128` est celle qu'appelle `langsmith._internal._uuid`.
xxh3_128 = _fabrique(16)


def _oneshot(taille: int, hexa: bool):
    """Variantes en un appel. `langgraph.types` importe xxh3_128_hexdigest."""
    def _calcule(donnees: bytes = b"", seed: int = 0):
        d = _fabrique(taille)(donnees, seed)
        return d.hexdigest() if hexa else d.digest()
    return _calcule


xxh3_128_hexdigest = _oneshot(16, True)


def __getattr__(nom: str):
    """Tout le reste echoue bruyamment, jamais en silence.

    Un appelant qui aurait besoin de vrais digests xxHash — comparaison avec
    un systeme tiers, checksum de fichier — doit le voir immediatement, pas
    recevoir une valeur qui ressemble a un hash sans en etre un.
    """
    raise AttributeError(
        f"xxhash.{nom} n'est pas fourni par le substitut de tests V14 "
        f"({__file__}). Il ne couvre que des digests DETERMINISTES, adosses a "
        "blake2b, et non l'algorithme xxHash. Si un vrai digest est requis, "
        "executer la suite hors du perimetre Smart App Control."
    )
