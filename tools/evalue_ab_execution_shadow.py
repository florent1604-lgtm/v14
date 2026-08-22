"""Audite les prerequis d'un A/B SHADOW d'execution, sans simuler de fills.

Le validateur est volontairement fail-closed. Une quote L1 ne prouve ni la
priorite dans la file ni l'agresseur d'une transaction. Tant que ces donnees
et l'instant causal exact de l'intention manquent, il produit un NO-GO scelle
et laisse toutes les metriques a ``null``.
"""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
BRUTS_DEFAUT = RACINE / "results" / "rejeu_univers_brut"
QUOTES_DEFAUT = RACINE / "results" / "quotes"
RESUMES_DEFAUT = RACINE / "results" / "rejeu_univers"
SCHEMA_VERSION = 2
POLITIQUES = ("market", "limit_passive", "adaptive")
FALLBACK_AUTORISES = {
    "market": {"immediate_or_expire"},
    "limit_passive": {"expire_unfilled"},
    "adaptive": {"cross_at_expiry", "expire_unfilled"},
}
METRIQUES = (
    "fill_rate",
    "delay_ms",
    "slippage_bps",
    "saving_bps",
    "markout_bps",
    "opportunity_cost_r",
    "net_intention_to_trade_r",
)
CHAMPS_INTENTION = {
    "decision_at",
    "asset_class",
    "quantity",
    "quantity_unit",
    "side",
    "trade_id",
}
CHAMPS_PASSIF = {
    "bid_size",
    "ask_size",
    "trade_price",
    "trade_size",
    "aggressor_side",
    "sequence",
}


def _canonique(objet: dict) -> bytes:
    return (json.dumps(
        objet, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n").encode("utf-8")


def _sha256(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _timestamp_ms_utc(valeur) -> float:
    texte = str(valeur).strip().replace("Z", "+00:00")
    instant = datetime.fromisoformat(texte)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("horodatage sans fuseau")
    return instant.astimezone(timezone.utc).timestamp() * 1000.0


def _nombre_fini(valeur, *, strictement_positif: bool = False) -> float:
    nombre = float(valeur)
    if not math.isfinite(nombre):
        raise ValueError("nombre non fini")
    if strictement_positif and nombre <= 0:
        raise ValueError("nombre non positif")
    return nombre


def _nombre_non_negatif(valeur) -> float:
    nombre = _nombre_fini(valeur)
    if nombre < 0:
        raise ValueError("nombre negatif")
    return nombre


def _valider_hypotheses(hypotheses: dict | None) -> dict:
    if not isinstance(hypotheses, dict):
        raise ValueError("hypotheses absentes")
    if hypotheses.get("schema_version") != 1:
        raise ValueError("schema hypotheses inconnu")
    latences = hypotheses.get("latency_ms")
    expirations = hypotheses.get("expiry_ms")
    frais = hypotheses.get("fees_bps")
    fallback = hypotheses.get("fallback")
    horizons = hypotheses.get("markout_horizons_ms")
    if not isinstance(latences, dict) or set(POLITIQUES) - set(latences):
        raise ValueError("latences incompletes")
    if not isinstance(expirations, dict) or set(POLITIQUES) - set(expirations):
        raise ValueError("expirations incompletes")
    if not isinstance(frais, dict) or not {"maker", "taker"}.issubset(frais):
        raise ValueError("frais incomplets")
    if not isinstance(fallback, dict) or not all(
        fallback.get(politique) in FALLBACK_AUTORISES[politique]
        for politique in POLITIQUES
    ):
        raise ValueError("fallback incomplet")
    if not isinstance(horizons, list) or not horizons:
        raise ValueError("horizons markout absents")
    normalise = {
        "schema_version": 1,
        "latency_ms": {
            politique: _nombre_fini(latences[politique])
            for politique in POLITIQUES
        },
        "expiry_ms": {
            politique: _nombre_fini(
                expirations[politique], strictement_positif=True,
            )
            for politique in POLITIQUES
        },
        "fees_bps": {
            role: _nombre_fini(frais[role]) for role in ("maker", "taker")
        },
        "fallback": {
            politique: fallback[politique].strip() for politique in POLITIQUES
        },
        "markout_horizons_ms": sorted({
            _nombre_fini(horizon, strictement_positif=True)
            for horizon in horizons
        }),
        "max_quote_gap_ms": _nombre_fini(
            hypotheses.get("max_quote_gap_ms"), strictement_positif=True,
        ),
    }
    if any(latence < 0 for latence in normalise["latency_ms"].values()):
        raise ValueError("latence negative")
    if any(
        normalise["latency_ms"][politique]
        >= normalise["expiry_ms"][politique]
        for politique in POLITIQUES
    ):
        raise ValueError("latence doit etre inferieure a expiration")
    return normalise


def _scanner_quotes(fichiers: list[Path], symbole: str) -> dict:
    """Valide chaque observation et rend un snapshot sans inventer de fill."""
    snapshot: list[dict] = []
    premiere_ts = derniere_ts = precedente_ts = None
    observations = 0
    passif_observable = True
    precedente_sequence = None
    timestamps_ms: list[float] = []
    for chemin in fichiers:
        hachage = hashlib.sha256()
        taille = 0
        with chemin.open("rb") as fichier:
            for numero, ligne in enumerate(fichier, start=1):
                hachage.update(ligne)
                taille += len(ligne)
                if not ligne.strip():
                    continue
                quote = json.loads(ligne)
                if not isinstance(quote, dict):
                    raise ValueError(f"{chemin.name}:{numero}: quote non objet")
                ts_ms = _nombre_fini(quote["ts_ms"], strictement_positif=True)
                bid = _nombre_fini(quote["bid"], strictement_positif=True)
                ask = _nombre_fini(quote["ask"], strictement_positif=True)
                if ask < bid or quote.get("horloge") != "utc":
                    raise ValueError(f"{chemin.name}:{numero}: quote ou horloge")
                if str(quote.get("symbole", "")).upper() != symbole.upper():
                    raise ValueError(f"{chemin.name}:{numero}: symbole")
                if precedente_ts is not None and ts_ms < precedente_ts:
                    raise ValueError(f"{chemin.name}:{numero}: chronologie")
                precedente_ts = ts_ms
                timestamps_ms.append(ts_ms)
                premiere_ts = ts_ms if premiere_ts is None else premiere_ts
                derniere_ts = ts_ms
                observations += 1

                if not CHAMPS_PASSIF.issubset(quote):
                    passif_observable = False
                    continue
                _nombre_non_negatif(quote["bid_size"])
                _nombre_non_negatif(quote["ask_size"])
                _nombre_fini(quote["trade_price"], strictement_positif=True)
                _nombre_fini(quote["trade_size"], strictement_positif=True)
                sequence = _nombre_fini(
                    quote["sequence"], strictement_positif=True,
                )
                if not sequence.is_integer():
                    raise ValueError(
                        f"{chemin.name}:{numero}: sequence non entiere"
                    )
                if (precedente_sequence is not None
                        and sequence <= precedente_sequence):
                    raise ValueError(
                        f"{chemin.name}:{numero}: sequence non croissante"
                    )
                precedente_sequence = sequence
                if str(quote["aggressor_side"]).lower() not in {"buy", "sell"}:
                    raise ValueError(f"{chemin.name}:{numero}: cote agresseur")
        snapshot.append({
            "symbol": symbole,
            "name": chemin.name,
            "bytes": taille,
            "sha256": hachage.hexdigest(),
        })
    if observations == 0:
        raise ValueError("archives quotes vides")
    return {
        "snapshot": snapshot,
        "first_ts_ms": premiere_ts,
        "last_ts_ms": derniere_ts,
        "observations": observations,
        "passive_observable": passif_observable,
        "timestamps_ms": timestamps_ms,
    }


def _artefact_scelle(dossier: Path, resumes: Path) -> tuple[dict, list[dict]]:
    symbole = dossier.name
    chemin_manifeste = dossier / "manifest.json"
    chemin_trades = dossier / "trades.ndjson"
    manifeste = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
    corps = dict(manifeste)
    sceau = corps.pop("manifest_sha256")
    if sceau != _sha256(_canonique(corps)):
        raise ValueError("sceau manifeste invalide")
    if manifeste.get("schema_version") != 2:
        raise ValueError("schema brut incompatible")
    if manifeste.get("symbol") != symbole:
        raise ValueError("symbole manifeste incoherent")
    brut = chemin_trades.read_bytes()
    fichier = manifeste["trades"]
    if len(brut) != fichier["bytes"] or _sha256(brut) != fichier["sha256"]:
        raise ValueError("trades non scelles")
    fichier_resume = manifeste["summary"]
    chemin_resume = resumes / fichier_resume["name"]
    resume = chemin_resume.read_bytes()
    if (len(resume) != fichier_resume["bytes"]
            or _sha256(resume) != fichier_resume["sha256"]):
        raise ValueError("resume non scelle")
    lignes = [json.loads(ligne) for ligne in brut.splitlines() if ligne.strip()]
    if len(lignes) != manifeste["counts"]["trades"]:
        raise ValueError("compteur trades incoherent")
    if any(not isinstance(ligne, dict) for ligne in lignes):
        raise ValueError("trade brut non objet")
    if any(ligne.get("symbol") != symbole for ligne in lignes):
        raise ValueError("symbole trade incoherent")
    if any(ligne.get("schema_version") != 2 for ligne in lignes):
        raise ValueError("schema trade incompatible")
    identifiants = [ligne.get("trade_id") for ligne in lignes]
    if any(not isinstance(identifiant, str) or not identifiant for identifiant in identifiants):
        raise ValueError("trade_id absent")
    if len(identifiants) != len(set(identifiants)):
        raise ValueError("trade_id duplique")
    return manifeste, lignes


def _blocage(code: str, scope: str, detail: str) -> dict:
    return {"code": code, "scope": scope, "detail": detail}


def _fenetre_intentions(intentions: list[dict]) -> tuple[float, float]:
    decisions: list[float] = []
    for intention in intentions:
        decision = _timestamp_ms_utc(intention["decision_at"])
        side = _nombre_fini(intention["side"])
        if side not in {-1.0, 1.0}:
            raise ValueError("side doit valoir -1 ou 1")
        _nombre_fini(intention["quantity"], strictement_positif=True)
        if intention["quantity_unit"] != "risk_unit":
            raise ValueError("quantity_unit doit valoir risk_unit")
        if not str(intention["asset_class"]).strip():
            raise ValueError("classe d'actif vide")
        decisions.append(decision)
    return min(decisions), max(decisions)


def _valider_couverture_par_bras(
    intentions: list[dict],
    timestamps: list[float],
    hypotheses: dict,
) -> None:
    """Exige une trace dense dans chaque fenêtre causale et à chaque markout."""
    gap_max = hypotheses["max_quote_gap_ms"]
    for intention in intentions:
        decision = _timestamp_ms_utc(intention["decision_at"])
        for politique in POLITIQUES:
            debut = decision + hypotheses["latency_ms"][politique]
            fin = debut + hypotheses["expiry_ms"][politique]
            gauche = bisect.bisect_left(timestamps, debut)
            droite = bisect.bisect_right(timestamps, fin)
            fenetre = timestamps[gauche:droite]
            if not fenetre:
                raise ValueError(
                    f"{politique}: aucune quote dans la fenetre executable"
                )
            if fenetre[0] - debut > gap_max or fin - fenetre[-1] > gap_max:
                raise ValueError(f"{politique}: bornes de fenetre non couvertes")
            if any(
                courant - precedent > gap_max
                for precedent, courant in zip(
                    fenetre, fenetre[1:], strict=False,
                )
            ):
                raise ValueError(f"{politique}: trou de quotes dans la fenetre")
            for horizon in hypotheses["markout_horizons_ms"]:
                cible = fin + horizon
                index = bisect.bisect_left(timestamps, cible)
                if index >= len(timestamps) or timestamps[index] - cible > gap_max:
                    raise ValueError(
                        f"{politique}: horizon markout non couvert"
                    )


def auditer_disponibilite(
    bruts: Path,
    quotes: Path,
    resumes: Path,
    hypotheses: dict | None = None,
) -> dict:
    """Rend un inventaire deterministe et scelle; n'infere jamais un fill."""
    bruts, quotes, resumes = Path(bruts), Path(quotes), Path(resumes)
    dossiers_bruts = sorted(
        (p for p in bruts.iterdir() if p.is_dir()), key=lambda p: p.name
    ) if bruts.is_dir() else []
    dossiers_quotes = sorted(
        (p for p in quotes.iterdir() if p.is_dir()), key=lambda p: p.name
    ) if quotes.is_dir() else []
    fichiers_quotes_total = sum(
        1 for dossier in dossiers_quotes for _ in dossier.glob("*.ndjson")
    )
    blocages: list[dict] = []
    symboles: list[dict] = []
    snapshot_bruts: list[dict] = []
    snapshot_quotes: list[dict] = []
    intentions_total = 0
    observations_quotes = 0
    scelles = 0

    try:
        hypotheses_normalisees = _valider_hypotheses(hypotheses)
    except (KeyError, TypeError, ValueError) as exc:
        hypotheses_normalisees = None
        blocages.append(_blocage(
            "EXECUTION_ASSUMPTIONS_INVALID", "global", str(exc),
        ))

    if not dossiers_bruts:
        blocages.append(_blocage(
            "NO_RAW_ARTIFACTS", "global",
            "aucun artefact de trades bruts scelle n'est disponible",
        ))

    for dossier in dossiers_bruts:
        symbole = dossier.name
        entree = {
            "symbol": symbole,
            "asset_class": None,
            "intentions": 0,
            "quote_files": 0,
            "sealed": False,
        }
        try:
            manifeste, intentions = _artefact_scelle(dossier, resumes)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            blocages.append(_blocage(
                "RAW_ARTIFACT_INVALID", symbole, type(exc).__name__,
            ))
            symboles.append(entree)
            continue

        scelles += 1
        intentions_total += len(intentions)
        entree["sealed"] = True
        entree["intentions"] = len(intentions)
        classes = sorted({
            str(i.get("asset_class")) for i in intentions if i.get("asset_class")
        })
        entree["asset_class"] = classes[0] if len(classes) == 1 else None
        snapshot_bruts.append({
            "symbol": symbole,
            "manifest_sha256": manifeste["manifest_sha256"],
            "trades_sha256": manifeste["trades"]["sha256"],
            "summary_sha256": manifeste["summary"]["sha256"],
        })

        champs_absents = sorted({
            champ for intention in intentions for champ in CHAMPS_INTENTION
            if intention.get(champ) in (None, "")
        })
        if "decision_at" in champs_absents:
            blocages.append(_blocage(
                "INTENT_TIMESTAMP_UNOBSERVABLE", symbole,
                "bar_entree est un index de barre, pas l'instant exact de decision",
            ))
        if "asset_class" in champs_absents:
            blocages.append(_blocage(
                "ASSET_CLASS_UNOBSERVABLE", symbole,
                "dimension par classe absente des intentions scellees",
            ))
        if "quantity" in champs_absents:
            blocages.append(_blocage(
                "INTENT_QUANTITY_UNOBSERVABLE", symbole,
                "quantite intention-to-trade absente",
            ))
        if "quantity_unit" in champs_absents:
            blocages.append(_blocage(
                "INTENT_QUANTITY_UNIT_UNOBSERVABLE", symbole,
                "unite de quantite normalisee absente",
            ))
        fenetre_intentions = None
        if not intentions:
            blocages.append(_blocage(
                "NO_EXECUTION_INTENTIONS", symbole,
                "artefact scelle sans aucune intention executable",
            ))
        elif not champs_absents:
            try:
                fenetre_intentions = _fenetre_intentions(intentions)
            except (KeyError, TypeError, ValueError) as exc:
                blocages.append(_blocage(
                    "INTENT_VALUES_INVALID", symbole, str(exc),
                ))

        fichiers = sorted((quotes / symbole).glob("*.ndjson"))
        entree["quote_files"] = len(fichiers)
        if not fichiers:
            blocages.append(_blocage(
                "NO_BROKER_QUOTES", symbole, "aucune quote broker archivee",
            ))
            symboles.append(entree)
            continue
        try:
            audit_quotes = _scanner_quotes(fichiers, symbole)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            blocages.append(_blocage(
                "BROKER_QUOTES_INVALID", symbole, type(exc).__name__,
            ))
            symboles.append(entree)
            continue

        observations_quotes += audit_quotes["observations"]
        snapshot_quotes.extend(audit_quotes["snapshot"])
        if not audit_quotes["passive_observable"]:
            blocages.append(_blocage(
                "PASSIVE_FILL_UNOBSERVABLE", symbole,
                "L1 sans profondeur/file, transactions et cote agresseur",
            ))
        if fenetre_intentions is not None and hypotheses_normalisees is not None:
            try:
                _valider_couverture_par_bras(
                    intentions,
                    audit_quotes["timestamps_ms"],
                    hypotheses_normalisees,
                )
            except ValueError as exc:
                blocages.append(_blocage(
                    "QUOTE_COVERAGE_INCOMPLETE", symbole,
                    str(exc),
                ))
        symboles.append(entree)

    blocages.sort(key=lambda item: (item["code"], item["scope"], item["detail"]))
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "raw_artifacts": snapshot_bruts,
        "quote_files": snapshot_quotes,
        "execution_assumptions": hypotheses_normalisees,
    }
    snapshot["snapshot_id"] = _sha256(_canonique(snapshot))
    rapport = {
        "schema_version": SCHEMA_VERSION,
        "report_type": "v14.execution_ab.availability",
        "status": "BLOCKED" if blocages else "READY_FOR_EVALUATOR",
        "simulation_performed": False,
        "policies": list(POLITIQUES),
        "dimensions": ["symbol", "asset_class"],
        "metrics": dict.fromkeys(METRIQUES),
        "inventory": {
            "raw_symbols": len(dossiers_bruts),
            "sealed_raw_symbols": scelles,
            "intentions": intentions_total,
            "quote_symbols": len(dossiers_quotes),
            "quote_files": fichiers_quotes_total,
            "quote_observations_validated": observations_quotes,
        },
        "per_symbol": symboles,
        "blockers": blocages,
        "snapshot": snapshot,
    }
    rapport["manifest_sha256"] = _sha256(_canonique(rapport))
    return rapport


def _ecrire_atomique(chemin: Path, contenu: bytes) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(
        f"{chemin.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}"
    )
    try:
        temporaire.write_bytes(contenu)
        temporaire.replace(chemin)
    finally:
        temporaire.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bruts", type=Path, default=BRUTS_DEFAUT)
    ap.add_argument("--quotes", type=Path, default=QUOTES_DEFAUT)
    ap.add_argument("--resumes", type=Path, default=RESUMES_DEFAUT)
    ap.add_argument(
        "--hypotheses", type=Path, required=True,
        help="JSON scelle dans le snapshot: latences, frais, expirations, fallback",
    )
    ap.add_argument("--sortie", type=Path, default=None)
    args = ap.parse_args()
    try:
        hypotheses = json.loads(args.hypotheses.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ap.error(f"--hypotheses illisible: {exc}")
    rapport = auditer_disponibilite(
        args.bruts, args.quotes, args.resumes, hypotheses,
    )
    contenu = json.dumps(rapport, ensure_ascii=False, indent=2).encode("utf-8")
    if args.sortie is not None:
        _ecrire_atomique(args.sortie, contenu)
    print(contenu.decode("utf-8"))
    return 0 if rapport["status"] == "READY_FOR_EVALUATOR" else 2


if __name__ == "__main__":
    raise SystemExit(main())
