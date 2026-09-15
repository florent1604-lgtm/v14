"""Fournisseurs de calendrier macro — une seule interface, plusieurs sources.

Chaque fournisseur sait UNE chose : rendre un ``MacroCalendar`` complet ou
lever. Il ne decide jamais de la prudence, ne met rien en cache et ne connait
pas la politique de risque : cette separation est ce qui permet de remplacer
une source par une autre sans retoucher une seule borne.

Trois fournisseurs livres :

* ``file``        — un PDF n'est pas un flux : le fichier JSON est la source
                    deterministe des tests, de la CI et d'un poste sans reseau ;
* ``http``        — un endpoint JSON generique, cle lue dans l'ENVIRONNEMENT
                    (jamais dans un fichier versionne) ;
* ``unavailable`` — echoue toujours. C'est le fournisseur par defaut quand rien
                    n'est configure, et c'est voulu : un systeme neuf doit dire
                    « je ne sais pas », pas « tout va bien ».

**Echec ferme.** Une seule ligne illisible invalide TOUT le calendrier. Ignorer
l'evenement mal forme serait un repli silencieux vers un calendrier plus vide —
donc vers moins d'alertes, et la publication perdue serait justement celle qu'un
changement de format vient de casser.

**Fraicheur.** Elle vient du PRODUCTEUR, jamais de l'instant de lecture : une
charge utile dont on ne sait pas lire l'horodatage est un echec, pas une donnee
fraiche. Sinon un producteur mort qui laisse son fichier lisible serait relu
« frais » a chaque poll, et l'etat STALE deviendrait inatteignable.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from titanium.macro.contracts import (
    MACRO_CONTRACT_VERSION,
    MacroCalendar,
    MacroEvent,
    MacroImpact,
)
from titanium.macro.policy import MacroPolicy


class MacroSource(Protocol):
    """Ce qu'un fournisseur doit exposer. Rien d'autre n'est utilise."""

    name: str

    def fetch(self) -> MacroCalendar: ...


def _fuseau(nom: str) -> timezone | ZoneInfo:
    try:
        return ZoneInfo(nom)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(
            f"fuseau '{nom}' inconnu — installer 'tzdata' pour les fuseaux non-UTC"
        ) from exc


def parse_instant(raw: Any, *, naive_tz: str) -> datetime:
    """Lit un horodatage ISO 8601 ou une epoch ; leve si le format est inconnu.

    Un horodatage naif n'est JAMAIS suppose UTC en dur : il est localise avec le
    fuseau declare par la politique. Lire « 14:30 » comme 14:30 UTC quand la
    source publie en heure de New York decale la fenetre de gel de quatre
    heures — exactement dans le sens qui laisse passer la publication.
    """
    if isinstance(raw, datetime):
        value = raw
    elif isinstance(raw, bool) or raw is None:
        raise ValueError("horodatage macro manquant")
    elif isinstance(raw, (int, float)):
        value = datetime.fromtimestamp(float(raw), tz=timezone.utc)
    else:
        texte = str(raw).strip()
        if not texte:
            raise ValueError("horodatage macro vide")
        try:
            value = datetime.fromisoformat(texte.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"horodatage macro illisible: {raw!r}") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=_fuseau(naive_tz))
    return value.astimezone(timezone.utc)


def _nombre(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _identifiant(row: Mapping[str, Any], *, title: str, currency: str, moment: datetime) -> str:
    """Identifiant stable, ou ``event_id`` declare s'il existe.

    L'empreinte de repli ne contient que le contenu de l'evenement, jamais
    l'instant de lecture : deux lectures du meme calendrier doivent produire les
    memes identifiants, sinon les decisions aval ne sont plus dedupliquables.
    """
    declare = row.get("event_id") or row.get("id")
    if declare:
        return str(declare).strip()
    base = f"{MACRO_CONTRACT_VERSION}|{currency}|{moment.isoformat()}|{title}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _lignes(payload: Any) -> Any:
    """Lignes d'evenements d'une charge utile, quelles que soient ses variantes de nom."""
    if isinstance(payload, Mapping):
        return payload.get("events") or payload.get("calendar") or payload.get("data") or []
    return payload


def _instant_producteur(payload: Any, *, naive_tz: str) -> datetime:
    """Quand le PRODUCTEUR a produit cette charge utile — jamais l'instant de lecture.

    Estamper `datetime.now()` a la lecture declare la fraicheur au nom du
    LECTEUR : un producteur mort qui laisse son fichier lisible serait relu
    « frais » a chaque poll, et STALE deviendrait inatteignable. On lit donc
    l'horodatage du producteur, dans l'ordre ou le contrat le porte. Absent ou
    illisible, on LEVE : une donnee dont on ignore l'age n'est pas une donnee
    fraiche, et un repli silencieux serait un laissez-passer.
    """
    declare: Any = None
    if isinstance(payload, Mapping):
        declare = payload.get("retrieved_at") or payload.get("generated_at")
        if declare is None:
            bloc = payload.get("calendarRisk")
            if isinstance(bloc, Mapping):
                declare = bloc.get("evaluated_at")
    if declare is None:
        instants = [
            parse_instant(row["retrieved_at"], naive_tz=naive_tz)
            for row in _lignes(payload)
            if isinstance(row, Mapping) and row.get("retrieved_at")
        ]
        if instants:
            return max(instants)
    if declare is None:
        raise ValueError(
            "charge utile macro sans horodatage producteur "
            "(retrieved_at, generated_at, calendarRisk.evaluated_at)"
        )
    return parse_instant(declare, naive_tz=naive_tz)


def parse_events(payload: Any, *, provider: str, naive_tz: str = "UTC") -> tuple[MacroEvent, ...]:
    """Convertit une charge utile JSON en evenements ; leve si une ligne est douteuse."""
    lignes = _lignes(payload)
    if not isinstance(lignes, Sequence) or isinstance(lignes, (str, bytes)):
        raise ValueError("charge utile macro: liste d'evenements attendue")
    events: list[MacroEvent] = []
    for index, row in enumerate(lignes):
        if not isinstance(row, Mapping):
            raise ValueError(f"evenement macro #{index}: objet attendu")
        try:
            title = str(row.get("title") or row.get("name") or row.get("event") or "").strip()
            currency = str(
                row.get("currency") or row.get("devise") or row.get("country") or ""
            ).strip()
            if not currency:
                raise ValueError("devise manquante")
            moment = parse_instant(
                row.get("scheduled_at") or row.get("date") or row.get("time"),
                naive_tz=naive_tz,
            )
            impact = MacroImpact.parse(
                row.get("impact") or row.get("importance") or row.get("priority")
            )
        except ValueError as exc:
            raise ValueError(f"evenement macro #{index} ({row.get('title', '')!r}): {exc}") from exc
        events.append(
            MacroEvent(
                event_id=_identifiant(row, title=title, currency=currency, moment=moment),
                title=title or "(sans titre)",
                currency=currency,
                scheduled_at=moment,
                impact=impact,
                source=provider,
                forecast=_nombre(row.get("forecast")),
                previous=_nombre(row.get("previous")),
                actual=_nombre(row.get("actual")),
            )
        )
    return tuple(events)


class FileMacroSource:
    """Lit un calendrier JSON local. Deterministe, sans reseau ni secret."""

    name = "file"

    def __init__(self, path: str | Path, *, naive_tz: str = "UTC") -> None:
        self.path = Path(path)
        self.naive_tz = naive_tz

    def fetch(self) -> MacroCalendar:
        if not self.path.is_file():
            raise ValueError(f"calendrier macro introuvable: {self.path}")
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"calendrier macro illisible (JSON): {exc}") from exc
        return MacroCalendar(
            provider=self.name,
            fetched_at=_instant_producteur(payload, naive_tz=self.naive_tz),
            events=parse_events(payload, provider=self.name, naive_tz=self.naive_tz),
        )


class HttpMacroSource:
    """Interroge un endpoint JSON. La cle vient de l'environnement, jamais du disque."""

    name = "http"

    def __init__(
        self,
        url: str,
        *,
        provider: str = "http",
        api_key_env: str = "",
        timeout_s: float = 8.0,
        naive_tz: str = "UTC",
    ) -> None:
        if not str(url or "").strip():
            raise ValueError("macro.url manquant pour le fournisseur http")
        self.url = str(url).strip()
        self.provider = provider
        self.api_key_env = api_key_env
        self.timeout_s = float(timeout_s)
        self.naive_tz = naive_tz

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key_env:
            cle = os.environ.get(self.api_key_env, "").strip()
            if not cle:
                # Ne jamais journaliser la valeur ; dire quelle variable manque
                # suffit a reparer, et le nom n'est pas un secret.
                raise ValueError(f"variable d'environnement '{self.api_key_env}' absente")
            headers["Authorization"] = f"Bearer {cle}"
        return headers

    def fetch(self) -> MacroCalendar:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - requests est une dependance de base
            raise ValueError("le fournisseur http exige 'requests'") from exc
        try:
            reponse = requests.get(
                self.url, headers=self._headers(), timeout=self.timeout_s
            )
            reponse.raise_for_status()
            payload = reponse.json()
        except ValueError:
            raise
        except Exception as exc:  # noqa: BLE001 — toute panne reseau est un echec de source
            raise ValueError(f"{type(exc).__name__}: {exc}") from exc
        return MacroCalendar(
            provider=self.provider,
            fetched_at=_instant_producteur(payload, naive_tz=self.naive_tz),
            events=parse_events(payload, provider=self.provider, naive_tz=self.naive_tz),
        )


class UnavailableMacroSource:
    """Echoue toujours, avec un motif lisible. Le defaut d'un systeme non configure."""

    def __init__(self, reason: str = "aucun fournisseur macro configure") -> None:
        self.name = "unavailable"
        self.reason = reason

    def fetch(self) -> MacroCalendar:
        raise ValueError(self.reason)


def build_source(policy: MacroPolicy) -> MacroSource:
    """Fabrique le fournisseur decrit par la politique. Un nom inconnu leve."""
    if not policy.enabled:
        return UnavailableMacroSource("flux macro desactive")
    if policy.provider == "file":
        if not policy.file:
            return UnavailableMacroSource("macro.file non renseigne")
        return FileMacroSource(policy.file, naive_tz=policy.naive_tz)
    if policy.provider == "http":
        return HttpMacroSource(
            policy.url,
            api_key_env=policy.api_key_env,
            timeout_s=policy.timeout_s,
            naive_tz=policy.naive_tz,
        )
    if policy.provider == "unavailable":
        return UnavailableMacroSource()
    raise ValueError(f"fournisseur macro inconnu: {policy.provider}")
