"""Bornes numeriques du flux macro — un seul endroit a relire pour un seuil.

Ces valeurs ne sont pas des constantes cachees dans le code de decision : elles
sont dans ``DEFAULT_CONFIG['execution']['macro']`` (donc versionnees, donc
comparables) et materialisees ici sous forme gelee. Un balayage de seuils est un
fichier de configuration, pas une modification du moteur.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from titanium.macro.contracts import MacroImpact

#: Cles acceptees par ``from_mapping``. Une cle inconnue est une FAUTE, pas un
#: detail : une faute de frappe dans ``blackout_befor_s`` laisserait la fenetre
#: a sa valeur par defaut en donnant l'illusion d'avoir ete reglee.
KNOWN_FIELDS = frozenset(
    {
        "enabled", "provider", "file", "url", "api_key_env",
        "naive_tz", "timeout_s", "ttl_s", "refresh_s", "max_backoff_s",
        "blackout_before_s", "blackout_after_s", "elevated_within_s",
        "min_impact", "score_elevated", "score_blackout", "score_unavailable",
    }
)


@dataclass(frozen=True)
class MacroPolicy:
    """Regles de lecture et fenetres de prudence autour des publications.

    ``enabled=False`` par DEFAUT, et c'est un choix : un poste neuf n'a pas de
    calendrier, et activer d'office un flux absent produirait un etat
    ``UNKNOWN`` qui refuserait tout. Le flux s'allume par configuration, une
    fois la source renseignee.

    Il n'y a PAS de troisieme booleen « obligatoire » : la semantique est deja
    portee par l'etat. Flux eteint -> ``CLEAR`` ; flux allume mais sans donnee
    fraiche -> ``STALE``/``UNKNOWN``, donc refus. Vouloir le flux sans son veto
    serait une politique qui se contredit.
    """

    enabled: bool = False
    provider: str = "file"
    file: str = ""
    url: str = ""
    api_key_env: str = "V14_MACRO_API_KEY"
    #: Fuseau applique a un horodatage SANS zone. Ce n'est pas un detail : lire
    #: « 14:30 » comme 14:30 UTC quand le fournisseur publie en heure de New York
    #: decale la fenetre de gel de quatre heures, dans le sens qui ne protege pas.
    #: Un fuseau inconnu fait echouer la lecture, il ne retombe sur rien.
    naive_tz: str = "UTC"
    timeout_s: float = 8.0
    #: Age maximal d'une lecture pour qu'elle soit encore crue. Au-dela : STALE.
    ttl_s: float = 900.0
    refresh_s: float = 300.0
    max_backoff_s: float = 3600.0
    #: Fenetre de gel autour d'une publication a fort impact.
    blackout_before_s: float = 900.0
    blackout_after_s: float = 300.0
    #: Horizon dans lequel une publication imminente rend l'attitude conservatrice.
    elevated_within_s: float = 3600.0
    #: Impact minimal pris en compte. LOW = aucune publication n'est ignoree.
    min_impact: MacroImpact = MacroImpact.HIGH
    score_elevated: float = 0.35
    score_blackout: float = 1.0
    #: Score d'un etat STALE/UNKNOWN. Jamais 0 : on ne sait pas n'est pas serein.
    score_unavailable: float = 1.0

    def __post_init__(self) -> None:
        if not isinstance(self.min_impact, MacroImpact):
            object.__setattr__(self, "min_impact", MacroImpact.parse(self.min_impact))
        for champ in ("ttl_s", "refresh_s", "timeout_s"):
            if float(getattr(self, champ)) <= 0:
                raise ValueError(f"macro.{champ} doit etre strictement positif")
        for champ in (
            "max_backoff_s", "blackout_before_s", "blackout_after_s", "elevated_within_s",
        ):
            if float(getattr(self, champ)) < 0:
                raise ValueError(f"macro.{champ} ne peut pas etre negatif")
        for champ in ("score_elevated", "score_blackout", "score_unavailable"):
            valeur = float(getattr(self, champ))
            if not 0.0 <= valeur <= 1.0:
                raise ValueError(f"macro.{champ} doit etre dans [0, 1]")
        object.__setattr__(self, "provider", str(self.provider or "").strip().lower())
        if not self.provider:
            raise ValueError("macro.provider manquant")
        object.__setattr__(self, "naive_tz", str(self.naive_tz or "").strip())
        if not self.naive_tz:
            raise ValueError("macro.naive_tz manquant")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> MacroPolicy:
        """Construit une politique depuis la configuration, en refusant les inconnues.

        Une cle prefixee par ``_`` est un commentaire et est ignoree : JSON
        n'offre pas de commentaire, et un exemple de configuration muet ne
        serait pas relisible. Toute AUTRE cle inconnue leve.
        """
        donnees = {
            cle: valeur
            for cle, valeur in dict(raw or {}).items()
            if not str(cle).startswith("_")
        }
        inconnues = sorted(set(donnees) - KNOWN_FIELDS)
        if inconnues:
            raise ValueError(f"cles macro inconnues: {', '.join(inconnues)}")
        return cls(**donnees)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "file": self.file,
            "url": self.url,
            # Le NOM de la variable, jamais sa valeur : un artefact scelle ne
            # doit pas pouvoir porter un secret.
            "api_key_env": self.api_key_env,
            "naive_tz": self.naive_tz,
            "timeout_s": self.timeout_s,
            "ttl_s": self.ttl_s,
            "refresh_s": self.refresh_s,
            "max_backoff_s": self.max_backoff_s,
            "blackout_before_s": self.blackout_before_s,
            "blackout_after_s": self.blackout_after_s,
            "elevated_within_s": self.elevated_within_s,
            "min_impact": self.min_impact.name,
            "score_elevated": self.score_elevated,
            "score_blackout": self.score_blackout,
            "score_unavailable": self.score_unavailable,
        }

    def fingerprint(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
