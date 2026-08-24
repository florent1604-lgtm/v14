#!/usr/bin/env python
"""Evalue les contacts de prix passifs sur les quotes L1 Axi archivees.

Ce banc est strictement PAPER/DEMO et ne simule aucun service. Le sommet L1
permet d'observer un contact ou un franchissement de prix, mais jamais la
priorite dans la file, la quantite disponible devant l'ordre ni le cote
agresseur. Les champs de service restent donc ``null`` par construction.

La reproductibilite repose sur un cutoff UTC obligatoire, les prefixes exacts
des fichiers quotes (octets + SHA-256), les artefacts de rejeu scelles et
l'empreinte du code d'analyse. Une fenetre TTL n'est admise que si le flux est
borne apres l'expiration et qu'aucun intervalle entre observations ne depasse
``--max-gap-ms``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
if str(RACINE) not in sys.path:
    sys.path.insert(0, str(RACINE))

from titanium.execution.limit_pricing import plan_limite_entree  # noqa: E402
from tools import epoque_rejeu  # noqa: E402
from tools.rejeu_univers import artefact_brut_valide  # noqa: E402

QUOTES_DEFAUT = RACINE / "results" / "quotes"
BRUTS_DEFAUT = RACINE / "results" / "rejeu_univers_brut"
RESUMES_DEFAUT = RACINE / "results" / "rejeu_univers"
SPECIFICATIONS_DEFAUT = RACINE / "results" / "barres" / "_specifications.json"
SORTIE_DEFAUT = RACINE / "results" / "evaluation_l1_passif.json"
DETAILS_DEFAUT = RACINE / "results" / "evaluation_l1_passif_lignes.ndjson"

SCHEMA_VERSION = 1
TTL_SECONDS = (120, 300, 600)
POLITIQUES = ("best_passive", "v14_live")
ARTIFACT_TYPE = "v14.offline_replay.trades"
ARTIFACT_SCHEMA = 2


def _canonique(objet: object) -> bytes:
    return (json.dumps(
        objet, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _sha256(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _sha256_fichier(chemin: Path) -> str:
    hachage = hashlib.sha256()
    with Path(chemin).open("rb") as flux:
        for bloc in iter(lambda: flux.read(1024 * 1024), b""):
            hachage.update(bloc)
    return hachage.hexdigest()


def instant_utc_ms(valeur: str | datetime) -> int:
    if isinstance(valeur, datetime):
        instant = valeur
    else:
        instant = datetime.fromisoformat(str(valeur).strip().replace("Z", "+00:00"))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("horodatage sans fuseau")
    return int(round(instant.astimezone(timezone.utc).timestamp() * 1000.0))


def iso_utc_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class Quote:
    ts_ms: int
    bid: float
    ask: float


@dataclass
class Fenetre:
    ttl_seconds: int
    expiration_ms: int
    precedent_ms: int
    max_gap_ms: int
    observations: int = 0
    fermee: bool = False
    contact: bool = False
    franchissement: bool = False
    premier_contact_ms: int | None = None
    premier_franchissement_ms: int | None = None

    def observer(self, quote: Quote, *, side: int, limite: float) -> None:
        if self.fermee or quote.ts_ms <= self.precedent_ms:
            return
        ecart = quote.ts_ms - self.precedent_ms
        self.max_gap_ms = max(self.max_gap_ms, ecart)
        if quote.ts_ms >= self.expiration_ms:
            self.fermee = True
            return
        self.precedent_ms = quote.ts_ms
        self.observations += 1
        contact = quote.ask <= limite if side > 0 else quote.bid >= limite
        franchissement = quote.ask < limite if side > 0 else quote.bid > limite
        if contact and not self.contact:
            self.contact = True
            self.premier_contact_ms = quote.ts_ms
        if franchissement and not self.franchissement:
            self.franchissement = True
            self.premier_franchissement_ms = quote.ts_ms


@dataclass
class EvaluationDecision:
    decision: dict
    arrivee: Quote
    delai_arrivee_ms: int
    limites: dict[str, float]
    fenetres: dict[tuple[str, int], Fenetre] = field(default_factory=dict)


def quote_validee(objet: dict, symbole: str) -> Quote:
    try:
        ts_ms = int(round(float(objet["ts_ms"])))
        bid = float(objet["bid"])
        ask = float(objet["ask"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("quote numerique invalide") from exc
    if ts_ms <= 0 or not all(math.isfinite(v) and v > 0 for v in (bid, ask)):
        raise ValueError("quote non finie ou non positive")
    if ask < bid:
        raise ValueError("carnet inverse")
    if str(objet.get("symbole", "")).upper() != symbole.upper():
        raise ValueError("symbole quote incoherent")
    if objet.get("horloge") != "utc":
        raise ValueError("horloge quote non UTC")
    return Quote(ts_ms=ts_ms, bid=bid, ask=ask)


def limites_passives(arrivee: Quote, decision: dict, specification: dict) -> dict[str, float]:
    side = int(decision["side"])
    if side not in (-1, 1):
        raise ValueError("side invalide")
    point = float(specification.get("point") or 0.0)
    tick = float(specification.get("tick_size") or point)
    digits = int(specification.get("digits") or 0)
    if tick <= 0:
        raise ValueError("tick invalide")
    meilleur = arrivee.bid if side > 0 else arrivee.ask
    plan = plan_limite_entree(
        bid=arrivee.bid,
        ask=arrivee.ask,
        side=side,
        stop_distance=float(decision["r_unit"]),
        tick=tick,
        digits=digits,
    )
    return {"best_passive": meilleur, "v14_live": plan.price}


def _details_sans_arrivee(decision: dict, *, cutoff_ms: int, motif: str) -> list[dict]:
    sorties = []
    for politique in POLITIQUES:
        for ttl in TTL_SECONDS:
            sorties.append({
                "schema_version": SCHEMA_VERSION,
                "symbole": decision["symbol"],
                "decision_id": decision["decision_id"],
                "decision_at": decision["decision_at"],
                "side": decision["side"],
                "politique": politique,
                "ttl_seconds": ttl,
                "cutoff": iso_utc_ms(cutoff_ms),
                "coverage_ok": False,
                "coverage_reason": motif,
                "arrival_delay_ms": None,
                "max_gap_ms": None,
                "observations": 0,
                "prix_limite": None,
                "contact_prix": None,
                "franchissement_prix": None,
                "premier_contact_delay_ms": None,
                "premier_franchissement_delay_ms": None,
                "service_observable": False,
                "service": None,
                "service_reason": "L1_SANS_FILE_NI_AGRESSEUR",
            })
    return sorties


def finaliser_evaluation(
    evaluation: EvaluationDecision,
    *,
    cutoff_ms: int,
    seuil_gap_ms: int,
) -> list[dict]:
    decision = evaluation.decision
    sorties = []
    for politique, limite in evaluation.limites.items():
        for ttl in TTL_SECONDS:
            fenetre = evaluation.fenetres[(politique, ttl)]
            couverture = (
                fenetre.fermee
                and evaluation.delai_arrivee_ms <= seuil_gap_ms
                and fenetre.max_gap_ms <= seuil_gap_ms
            )
            raison = "OK" if couverture else (
                "CUTOFF_AVANT_FERMETURE" if not fenetre.fermee
                else "TROU_QUOTES_SUPERIEUR_AU_SEUIL"
            )
            sorties.append({
                "schema_version": SCHEMA_VERSION,
                "symbole": decision["symbol"],
                "decision_id": decision["decision_id"],
                "decision_at": decision["decision_at"],
                "side": decision["side"],
                "politique": politique,
                "ttl_seconds": ttl,
                "cutoff": iso_utc_ms(cutoff_ms),
                "coverage_ok": couverture,
                "coverage_reason": raison,
                "arrival_delay_ms": evaluation.delai_arrivee_ms,
                "max_gap_ms": fenetre.max_gap_ms,
                "observations": fenetre.observations,
                "prix_limite": limite,
                "contact_prix": fenetre.contact if couverture else None,
                "franchissement_prix": fenetre.franchissement if couverture else None,
                "premier_contact_delay_ms": (
                    fenetre.premier_contact_ms - evaluation.arrivee.ts_ms
                    if couverture and fenetre.premier_contact_ms is not None else None
                ),
                "premier_franchissement_delay_ms": (
                    fenetre.premier_franchissement_ms - evaluation.arrivee.ts_ms
                    if couverture and fenetre.premier_franchissement_ms is not None else None
                ),
                "service_observable": False,
                "service": None,
                "service_reason": "L1_SANS_FILE_NI_AGRESSEUR",
            })
    return sorties


def evaluer_flux_symbole(
    symbole: str,
    decisions: list[dict],
    quotes: Iterable[Quote],
    specification: dict,
    *,
    cutoff_ms: int,
    seuil_gap_ms: int,
) -> list[dict]:
    """Evalue un flux deja valide, sans charger les quotes en memoire."""
    ordonnees = sorted(decisions, key=lambda d: (d["decision_at_ms"], d["decision_id"]))
    prochain = 0
    actives: list[EvaluationDecision] = []
    sorties: list[dict] = []
    precedent_quote: int | None = None
    for quote in quotes:
        if quote.ts_ms >= cutoff_ms:
            break
        if precedent_quote is not None and quote.ts_ms < precedent_quote:
            raise ValueError(f"{symbole}: chronologie quotes invalide")
        precedent_quote = quote.ts_ms

        while prochain < len(ordonnees) and ordonnees[prochain]["decision_at_ms"] <= quote.ts_ms:
            decision = ordonnees[prochain]
            delai = quote.ts_ms - decision["decision_at_ms"]
            if delai > seuil_gap_ms:
                sorties.extend(_details_sans_arrivee(
                    decision, cutoff_ms=cutoff_ms, motif="ARRIVEE_HORS_SEUIL",
                ))
            else:
                try:
                    limites = limites_passives(quote, decision, specification)
                except (KeyError, TypeError, ValueError):
                    sorties.extend(_details_sans_arrivee(
                        decision, cutoff_ms=cutoff_ms, motif="PLAN_PRIX_INVALIDE",
                    ))
                else:
                    evaluation = EvaluationDecision(decision, quote, delai, limites)
                    for politique in POLITIQUES:
                        for ttl in TTL_SECONDS:
                            evaluation.fenetres[(politique, ttl)] = Fenetre(
                                ttl_seconds=ttl,
                                expiration_ms=quote.ts_ms + ttl * 1000,
                                precedent_ms=quote.ts_ms,
                                max_gap_ms=delai,
                            )
                    actives.append(evaluation)
            prochain += 1

        encore_actives = []
        for evaluation in actives:
            if quote.ts_ms > evaluation.arrivee.ts_ms:
                side = int(evaluation.decision["side"])
                for (politique, _ttl), fenetre in evaluation.fenetres.items():
                    fenetre.observer(quote, side=side, limite=evaluation.limites[politique])
            if all(fenetre.fermee for fenetre in evaluation.fenetres.values()):
                sorties.extend(finaliser_evaluation(
                    evaluation, cutoff_ms=cutoff_ms, seuil_gap_ms=seuil_gap_ms,
                ))
            else:
                encore_actives.append(evaluation)
        actives = encore_actives

    for evaluation in actives:
        sorties.extend(finaliser_evaluation(
            evaluation, cutoff_ms=cutoff_ms, seuil_gap_ms=seuil_gap_ms,
        ))
    for decision in ordonnees[prochain:]:
        sorties.extend(_details_sans_arrivee(
            decision, cutoff_ms=cutoff_ms, motif="AUCUNE_QUOTE_AVANT_CUTOFF",
        ))
    return sorties


def valider_artefact(
    symbole: str,
    *,
    bruts: Path,
    resumes: Path,
    empreinte_attendue: str,
) -> tuple[bool, str, dict]:
    manifeste = Path(bruts) / symbole / "manifest.json"
    try:
        meta = json.loads(manifeste.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, "MANIFESTE_ABSENT_OU_INVALIDE", {}
    if meta.get("artifact_type") != ARTIFACT_TYPE or meta.get("schema_version") != ARTIFACT_SCHEMA:
        return False, "CONTRAT_ARTEFACT_INCOMPATIBLE", {}
    if epoque_rejeu.empreinte_manifeste(meta) != empreinte_attendue:
        return False, "EPOQUE_REJEU_INCOMPATIBLE", {}
    resume = Path(resumes) / f"{symbole}.json"
    if not artefact_brut_valide(Path(bruts), symbole, resume_path=resume):
        return False, "SCEAU_ARTEFACT_INVALIDE", {}
    return True, "OK", meta


def charger_decisions(
    symbole: str,
    *,
    bruts: Path,
    debut_ms: int,
    cutoff_ms: int,
) -> list[dict]:
    sorties = []
    chemin = Path(bruts) / symbole / "trades.ndjson"
    with chemin.open("r", encoding="utf-8") as flux:
        for ligne in flux:
            if not ligne.strip():
                continue
            trade = json.loads(ligne)
            ts_ms = instant_utc_ms(trade["decision_at"])
            if ts_ms < debut_ms or ts_ms >= cutoff_ms:
                continue
            sorties.append({
                "symbol": symbole,
                "decision_id": str(trade["trade_id"]),
                "decision_at": iso_utc_ms(ts_ms),
                "decision_at_ms": ts_ms,
                "side": int(trade["side"]),
                "r_unit": float(trade["r_unit"]),
            })
    return sorties


def _date_fichier_ms(chemin: Path) -> int | None:
    try:
        return instant_utc_ms(f"{chemin.stem}T00:00:00+00:00")
    except ValueError:
        return None


def flux_quotes_scelle(
    symbole: str,
    fichiers: list[Path],
    *,
    cutoff_ms: int,
    snapshot: list[dict],
) -> Iterator[Quote]:
    precedent: int | None = None
    for chemin in sorted(fichiers):
        hachage = hashlib.sha256()
        octets = observations = 0
        premier = dernier = None
        with chemin.open("rb") as flux:
            for numero, ligne in enumerate(flux, start=1):
                if not ligne.strip():
                    continue
                try:
                    objet = json.loads(ligne)
                    quote = quote_validee(objet, symbole)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ValueError(f"{chemin}:{numero}: {exc}") from exc
                if quote.ts_ms >= cutoff_ms:
                    break
                if precedent is not None and quote.ts_ms < precedent:
                    raise ValueError(f"{chemin}:{numero}: chronologie")
                precedent = quote.ts_ms
                hachage.update(ligne)
                octets += len(ligne)
                observations += 1
                premier = quote.ts_ms if premier is None else premier
                dernier = quote.ts_ms
                yield quote
        if observations:
            snapshot.append({
                "symbol": symbole,
                "name": chemin.relative_to(RACINE).as_posix()
                if chemin.is_relative_to(RACINE) else str(chemin),
                "bytes_inclus": octets,
                "observations": observations,
                "first_ts": iso_utc_ms(premier),
                "last_ts": iso_utc_ms(dernier),
                "sha256_prefix": hachage.hexdigest(),
            })


def agreger(lignes: list[dict]) -> dict:
    groupes: dict[tuple[str, int], list[dict]] = {}
    for ligne in lignes:
        groupes.setdefault((ligne["politique"], ligne["ttl_seconds"]), []).append(ligne)
    sortie = {}
    for (politique, ttl), groupe in sorted(groupes.items()):
        couvertes = [ligne for ligne in groupe if ligne["coverage_ok"]]
        contacts = [ligne for ligne in couvertes if ligne["contact_prix"]]
        franchies = [ligne for ligne in couvertes if ligne["franchissement_prix"]]
        delais = [ligne["premier_contact_delay_ms"] for ligne in contacts
                  if ligne["premier_contact_delay_ms"] is not None]
        sortie[f"{politique}|{ttl}"] = {
            "politique": politique,
            "ttl_seconds": ttl,
            "decisions_candidates": len(groupe),
            "coverage_ok": len(couvertes),
            "coverage_rate": round(len(couvertes) / len(groupe), 6) if groupe else None,
            "contacts_prix": len(contacts),
            "taux_contact_prix": round(len(contacts) / len(couvertes), 6) if couvertes else None,
            "franchissements_prix": len(franchies),
            "taux_franchissement_prix": (
                round(len(franchies) / len(couvertes), 6) if couvertes else None
            ),
            "delai_contact_ms_median": int(statistics.median(delais)) if delais else None,
            "service_observable": False,
            "taux_service": None,
            "service_reason": "L1_SANS_FILE_NI_AGRESSEUR",
        }
    return sortie


def _ecrire_atomique(chemin: Path, contenu: bytes) -> None:
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(f"{chemin.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        temporaire.write_bytes(contenu)
        temporaire.replace(chemin)
    finally:
        temporaire.unlink(missing_ok=True)


def mesurer(
    *,
    quotes: Path,
    bruts: Path,
    resumes: Path,
    specifications_path: Path,
    cutoff_ms: int,
    seuil_gap_ms: int,
    symboles: list[str] | None = None,
) -> tuple[dict, list[dict]]:
    specifications_bytes = Path(specifications_path).read_bytes()
    specifications = json.loads(specifications_bytes)
    empreinte = epoque_rejeu.empreinte_courante()
    dossiers_quotes = {p.name: p for p in Path(quotes).iterdir() if p.is_dir()}
    univers = sorted(symboles or (p.name for p in Path(bruts).iterdir() if p.is_dir()))
    details: list[dict] = []
    sources_quotes: list[dict] = []
    sources_rejeu: list[dict] = []
    refuses: dict[str, str] = {}

    for symbole in univers:
        dossier_quotes = dossiers_quotes.get(symbole)
        if dossier_quotes is None:
            refuses[symbole] = "AUCUNE_ARCHIVE_QUOTES"
            continue
        fichiers = sorted(dossier_quotes.glob("*.ndjson"))
        dates = [date for date in (_date_fichier_ms(p) for p in fichiers) if date is not None]
        if not dates:
            refuses[symbole] = "AUCUN_FICHIER_QUOTES_DATE"
            continue
        valide, motif, meta = valider_artefact(
            symbole, bruts=bruts, resumes=resumes, empreinte_attendue=empreinte,
        )
        if not valide:
            refuses[symbole] = motif
            continue
        decisions = charger_decisions(
            symbole, bruts=bruts, debut_ms=min(dates), cutoff_ms=cutoff_ms,
        )
        if not decisions:
            continue
        dernier_besoin = min(
            cutoff_ms,
            max(d["decision_at_ms"] for d in decisions)
            + max(TTL_SECONDS) * 1000 + seuil_gap_ms,
        )
        fichiers_utiles = [
            p for p in fichiers
            if (_date_fichier_ms(p) or cutoff_ms) <= dernier_besoin
        ]
        snapshot_symbole: list[dict] = []
        flux = flux_quotes_scelle(
            symbole, fichiers_utiles, cutoff_ms=dernier_besoin,
            snapshot=snapshot_symbole,
        )
        try:
            details.extend(evaluer_flux_symbole(
                symbole, decisions, flux, specifications.get(symbole) or {},
                cutoff_ms=cutoff_ms, seuil_gap_ms=seuil_gap_ms,
            ))
        except ValueError as exc:
            refuses[symbole] = f"ARCHIVE_QUOTES_INVALIDE:{exc}"
            continue
        sources_quotes.extend(snapshot_symbole)
        sources_rejeu.append({
            "symbol": symbole,
            "manifest_sha256": meta["manifest_sha256"],
            "trades_sha256": meta["trades"]["sha256"],
            "summary_sha256": meta["summary"]["sha256"],
        })

    code = Path(__file__)
    pricing = RACINE / "titanium" / "execution" / "limit_pricing.py"
    source_snapshot = {
        "schema_version": SCHEMA_VERSION,
        "cutoff": iso_utc_ms(cutoff_ms),
        "max_gap_ms": seuil_gap_ms,
        "ttl_seconds": list(TTL_SECONDS),
        "policies": list(POLITIQUES),
        "epoque_rejeu": empreinte,
        "replay_artifacts": sorted(sources_rejeu, key=lambda x: x["symbol"]),
        "quote_prefixes": sorted(sources_quotes, key=lambda x: (x["symbol"], x["name"])),
        "specifications": {
            "name": str(Path(specifications_path)),
            "bytes": len(specifications_bytes),
            "sha256": _sha256(specifications_bytes),
        },
        "code": {
            "analysis_sha256": _sha256_fichier(code),
            "limit_pricing_sha256": _sha256_fichier(pricing),
        },
    }
    source_snapshot["snapshot_id"] = _sha256(_canonique(source_snapshot))
    rapport = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "v14.execution_l1.passive_price_path",
        "status": "MEASURED_PRICE_PATH_ONLY",
        "paper_demo_only": True,
        "cutoff": iso_utc_ms(cutoff_ms),
        "max_gap_ms": seuil_gap_ms,
        "ttl_seconds": list(TTL_SECONDS),
        "policies": list(POLITIQUES),
        "metric_contract": {
            "contact_prix": "BUY ask<=limite; SELL bid>=limite; fenetre [arrivee, expiration)",
            "franchissement_prix": "BUY ask<limite; SELL bid>limite",
            "service": None,
            "service_observable": False,
            "warning": "aucune profondeur, file, transaction ou cote agresseur; aucun fill infere",
        },
        "inventory": {
            "symbols_requested": len(univers),
            "symbols_measured": len({ligne["symbole"] for ligne in details}),
            "candidate_decisions": len({
                (ligne["symbole"], ligne["decision_id"]) for ligne in details
            }),
            "detail_lines": len(details),
            "refused_symbols": refuses,
        },
        "metrics": agreger(details),
        "source_snapshot": source_snapshot,
    }
    return rapport, details


def sceller_sorties(rapport: dict, details: list[dict], *, sortie: Path, details_path: Path) -> dict:
    contenu_details = b"".join(_canonique(ligne) for ligne in details)
    _ecrire_atomique(details_path, contenu_details)
    rapport = dict(rapport)
    rapport["details"] = {
        "name": str(details_path),
        "bytes": len(contenu_details),
        "lines": len(details),
        "sha256": _sha256(contenu_details),
    }
    rapport["manifest_sha256"] = _sha256(_canonique(rapport))
    _ecrire_atomique(sortie, json.dumps(
        rapport, ensure_ascii=False, indent=2, sort_keys=True,
    ).encode("utf-8"))
    return rapport


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cutoff", required=True, help="instant UTC ISO inclus dans le sceau")
    ap.add_argument("--max-gap-ms", type=int, default=5_000)
    ap.add_argument("--quotes", type=Path, default=QUOTES_DEFAUT)
    ap.add_argument("--bruts", type=Path, default=BRUTS_DEFAUT)
    ap.add_argument("--resumes", type=Path, default=RESUMES_DEFAUT)
    ap.add_argument("--specifications", type=Path, default=SPECIFICATIONS_DEFAUT)
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--sortie", type=Path, default=SORTIE_DEFAUT)
    ap.add_argument("--details", type=Path, default=DETAILS_DEFAUT)
    args = ap.parse_args()
    if args.max_gap_ms <= 0:
        ap.error("--max-gap-ms doit etre positif")
    cutoff_ms = instant_utc_ms(args.cutoff)
    rapport, details = mesurer(
        quotes=args.quotes,
        bruts=args.bruts,
        resumes=args.resumes,
        specifications_path=args.specifications,
        cutoff_ms=cutoff_ms,
        seuil_gap_ms=args.max_gap_ms,
        symboles=args.symboles,
    )
    rapport = sceller_sorties(
        rapport, details, sortie=args.sortie, details_path=args.details,
    )
    print(json.dumps({
        "status": rapport["status"],
        "inventory": rapport["inventory"],
        "metrics": rapport["metrics"],
        "snapshot_id": rapport["source_snapshot"]["snapshot_id"],
        "manifest_sha256": rapport["manifest_sha256"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
