"""Coupe-circuit de pertes pour les nouvelles entrées DEMO.

Le journal live est la preuve comptable de référence. Une preuve absente ou
illisible interdit une nouvelle entrée, sans empêcher la gestion protectrice
des positions déjà ouvertes.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from titanium.edge import PNL_R_MAX

DAILY_LOSS_LIMIT_R = 2.0
ROLLING_7D_LOSS_LIMIT_R = 6.0
# Un horodatage peut se lire differemment selon le fuseau qu'on lui suppose.
# Le constat d'horloge serveur (Axi, UTC+3, 35 clotures datees trois heures
# dans le futur) montre que l'ecart est reel. L'amplitude entre les deux
# lectures extremes d'un meme horodatage est celle des fuseaux existants.
AMBIGUITE_HORAIRE_MAX = timedelta(hours=26)


@dataclass(frozen=True)
class LiveLossVerdict:
    """Décision reproductible du coupe-circuit de pertes live."""

    action: str
    reason: str
    daily_trades: int = 0
    daily_net_r: float = 0.0
    rolling_trades: int = 0
    rolling_net_r: float = 0.0
    # Motif et date du declencheur, portes par le verrou. Sans eux un verdict
    # bloque affiche des chiffres revenus dans les limites et ne dit pas
    # pourquoi il bloque.
    quarantine_reason: str = ""
    quarantine_since: str = ""

    def to_dict(self) -> dict[str, str | int | float]:
        return asdict(self)


def _invalid() -> LiveLossVerdict:
    return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_INVALID")


def _parse_closed_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _bornes_horodatage(value: object) -> tuple[datetime, datetime] | None:
    """Lecture la plus ancienne et la plus recente d'un horodatage.

    Un horodatage sans fuseau n'est pas situe : il est lu aux deux extremes
    plutot que de condamner le verdict a lui seul. `None` quand il n'est pas
    lisible du tout -- ce qui ne prouve rien et reste donc invalide.
    """

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed - AMBIGUITE_HORAIRE_MAX, parsed + AMBIGUITE_HORAIRE_MAX


def _quarantine_verdict(
    verdict: LiveLossVerdict,
    *,
    action: str = "BLOCK",
    reason: str = "PERSISTENT_LOSS_QUARANTINE",
    quarantine_reason: str = "",
    quarantine_since: str = "",
) -> LiveLossVerdict:
    return LiveLossVerdict(
        action=action,
        reason=reason,
        daily_trades=verdict.daily_trades,
        daily_net_r=verdict.daily_net_r,
        rolling_trades=verdict.rolling_trades,
        rolling_net_r=verdict.rolling_net_r,
        quarantine_reason=quarantine_reason,
        quarantine_since=quarantine_since,
    )


def _verrou_en_place(
    verdict: LiveLossVerdict,
    *,
    path: Path,
    account: str,
) -> LiveLossVerdict | None:
    """Verdict impose par un verrou existant et valide, sinon `None`.

    La lecture du verrou est ici et nulle part ailleurs : un consommateur qui
    doit rendre le meme verdict que le moteur la partage au lieu de la
    recopier. Le motif du declencheur est porte par le verdict, pour que
    « bloque » se lise avec sa cause et non avec un chiffre revenu dans les
    limites. Un compte non nomme ne peut rien revendiquer : le verdict
    attend, et aucun verrou n'est pose.
    """

    if not account:
        return _quarantine_verdict(
            verdict, action="WAIT", reason="LIVE_LOSS_QUARANTINE_INVALID",
        )
    if not path.exists():
        return None
    if not path.is_file():
        return _quarantine_verdict(
            verdict, action="WAIT", reason="LIVE_LOSS_QUARANTINE_INVALID",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        trigger = payload.get("trigger") if isinstance(payload, dict) else None
        valid = (
            payload.get("schema") == 1
            and str(payload.get("account", "")) == account
            and _parse_closed_at(payload.get("latched_at_utc")) is not None
            and isinstance(trigger, dict)
            and trigger.get("action") == "BLOCK"
            and isinstance(trigger.get("reason"), str)
            and bool(trigger["reason"])
        )
    except (OSError, json.JSONDecodeError, UnicodeError, AttributeError):
        valid = False
    if not valid:
        return _quarantine_verdict(
            verdict, action="WAIT", reason="LIVE_LOSS_QUARANTINE_INVALID",
        )
    return _quarantine_verdict(
        verdict,
        quarantine_reason=str(trigger["reason"]),
        quarantine_since=str(payload.get("latched_at_utc", "")),
    )


def persist_live_loss_quarantine(
    verdict: LiveLossVerdict,
    *,
    path: str | Path,
    account: str,
    now: datetime | None = None,
) -> LiveLossVerdict:
    """Verrouille un BLOCK jusqu'a un acquittement operateur explicite.

    Une limite glissante finirait sinon par repasser automatiquement a ALLOW.
    Le fichier est lie au compte et ecrit atomiquement. Toute preuve existante
    illisible ou incoherente maintient le moteur en WAIT.
    """

    quarantine_path = Path(path)
    expected_account = str(account).strip()
    en_place = _verrou_en_place(
        verdict, path=quarantine_path, account=expected_account,
    )
    if en_place is not None:
        return en_place

    if verdict.action != "BLOCK":
        return verdict

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return _quarantine_verdict(
            verdict, action="WAIT", reason="LIVE_LOSS_QUARANTINE_INVALID",
        )
    payload = {
        "schema": 1,
        "account": expected_account,
        "latched_at_utc": current.astimezone(timezone.utc).isoformat(),
        "trigger": verdict.to_dict(),
    }
    temporary: Path | None = None
    try:
        quarantine_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, raw_temporary = tempfile.mkstemp(
            prefix=quarantine_path.name + ".",
            suffix=".tmp",
            dir=quarantine_path.parent,
        )
        temporary = Path(raw_temporary)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=True, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, quarantine_path)
    except OSError:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        return _quarantine_verdict(
            verdict, action="WAIT", reason="LIVE_LOSS_QUARANTINE_WRITE_FAILED",
        )
    return _quarantine_verdict(
        verdict,
        quarantine_reason=str(verdict.reason),
        quarantine_since=current.astimezone(timezone.utc).isoformat(),
    )


def live_loss_guard_path(root: str | Path, account: str) -> Path:
    """Emplacement unique du verrou de quarantaine pour un compte."""

    return (Path(root) / "data" / "runtime" / "live_loss_quarantine"
            / f"{str(account).strip()}.json")


def read_live_loss_guard(
    journal_path: str | Path,
    *,
    account: str,
    quarantine_path: str | Path,
    now: datetime | None = None,
    not_before: datetime | None = None,
) -> LiveLossVerdict:
    """Verdict unique du coupe-circuit, verrou inclus, sans jamais ecrire.

    Le coupe-circuit a un seul proprietaire : un lecteur qui doit rendre le
    meme verdict que le moteur ne recalcule pas la regle, et ne pose ni ne
    leve le verrou de l'operateur.
    """

    verdict = evaluate_live_loss_guard(
        journal_path, account=account, now=now, not_before=not_before,
    )
    expected_account = str(account).strip()
    en_place = _verrou_en_place(
        verdict, path=Path(quarantine_path), account=expected_account,
    )
    return verdict if en_place is None else en_place


def evaluate_live_loss_guard(
    journal_path: str | Path,
    *,
    account: str,
    now: datetime | None = None,
    not_before: datetime | None = None,
) -> LiveLossVerdict:
    """Autorise ou bloque les nouvelles entrées selon le PnL live en R.

    Les limites sont de -2 R sur le jour UTC et -6 R sur sept jours glissants.
    Les doublons sont résolus par la date de clôture la plus récente. Une
    ligne dont l'horodatage tombe hors fenêtre sous toutes ses lectures est
    ignorée, comme les autres contrôles hors fenêtre ; une ligne pertinente
    qui n'est pas une preuve UTC nette fait attendre.
    """

    path = Path(journal_path)
    if not path.is_file():
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_MISSING")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return _invalid()
    current = current.astimezone(timezone.utc)
    cutoff = current - timedelta(days=7)
    if not_before is not None:
        if not isinstance(not_before, datetime) or not_before.tzinfo is None:
            return _invalid()
        cohort_start = not_before.astimezone(timezone.utc)
        if cohort_start > current:
            return _invalid()
        cutoff = max(cutoff, cohort_start)
    expected_account = str(account)
    relevant_account_seen = False
    by_ticket: dict[str, tuple[datetime, float]] = {}

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return _invalid()
    if not lines:
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_JOURNAL_MISSING")

    for raw in lines:
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError):
            return _invalid()
        if not isinstance(row, dict):
            return _invalid()
        if row.get("source") != "live" or str(row.get("account", "")) != expected_account:
            continue

        relevant_account_seen = True
        closed_at = _parse_closed_at(row.get("closed_at"))
        bornes = _bornes_horodatage(row.get("closed_at"))
        # Une ligne hors fenetre sous toutes les lectures de son horodatage ne
        # doit pas condamner le verdict a elle seule : c'est le regime des
        # quatre controles ci-dessous. Un horodatage illisible ne prouve rien
        # et reste, lui, invalide plutot que d'etre ignore en silence.
        if bornes is not None and (bornes[1] < cutoff or bornes[0] > current):
            continue
        if closed_at is None or closed_at > current:
            return _invalid()
        if closed_at < cutoff:
            continue

        ticket = row.get("ticket")
        try:
            pnl_r = float(row.get("pnl_r"))
        except (TypeError, ValueError):
            return _invalid()
        if (
            row.get("horloge") != "utc"
            or row.get("exact_net") is not True
            or not isinstance(ticket, str)
            or not ticket.strip()
            or not math.isfinite(pnl_r)
            or abs(pnl_r) > PNL_R_MAX
        ):
            return _invalid()
        cle_ticket = ticket.strip()
        # « L'etat le plus recent du journal » se lit sur la date de cloture :
        # un journal reordonne (reparation, rejeu) ne doit pas changer le
        # verdict. A date egale, la derniere ligne du fichier gagne.
        precedent = by_ticket.get(cle_ticket)
        if precedent is None or closed_at >= precedent[0]:
            by_ticket[cle_ticket] = (closed_at, pnl_r)

    if not relevant_account_seen:
        return LiveLossVerdict(action="WAIT", reason="LIVE_LOSS_ACCOUNT_HISTORY_MISSING")

    rolling = [item for item in by_ticket.values() if cutoff <= item[0] <= current]
    daily = [item for item in rolling if item[0].date() == current.date()]
    daily_net_r = round(sum(pnl_r for _, pnl_r in daily), 4)
    rolling_net_r = round(sum(pnl_r for _, pnl_r in rolling), 4)
    fields = {
        "daily_trades": len(daily),
        "daily_net_r": daily_net_r,
        "rolling_trades": len(rolling),
        "rolling_net_r": rolling_net_r,
    }
    if daily_net_r <= -DAILY_LOSS_LIMIT_R:
        return LiveLossVerdict(action="BLOCK", reason="DAILY_LOSS_LIMIT", **fields)
    if rolling_net_r <= -ROLLING_7D_LOSS_LIMIT_R:
        return LiveLossVerdict(action="BLOCK", reason="ROLLING_7D_LOSS_LIMIT", **fields)
    return LiveLossVerdict(action="ALLOW", reason="WITHIN_LOSS_LIMITS", **fields)
