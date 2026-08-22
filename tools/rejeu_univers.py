"""Rejeu hors ligne de la porte d'entree sur tout l'univers archive.

Protocole PREENREGISTRE avant le balayage, opposable a l'auteur
--------------------------------------------------------------
Chercher en boucle jusqu'a depasser un seuil sur un meme jeu de donnees est une
machine a sur-ajustement : 149 actifs font 149 occasions de trouver un gagnant
par hasard. Les regles ci-dessous sont donc fixees AVANT de lancer, et le script
ne connait aucun moyen de les contourner.

1. La metrique de decision est l'**esperance en R apres couts**, pas le winrate.
   A R:R 2, l'equilibre est a 33 % de winrate ; un winrate flatteur ne paie rien.
2. **Plancher de 60 clotures** par cellule. Sous ce seuil, la cellule est
   rapportee comme non concluante, meme gagnante.
3. **Hors-echantillon strict** : la coupure temporelle est fixee ici a 2/3 de la
   periode. Le dernier tiers ne sert a aucune selection ; il ne fait que
   confirmer ou infirmer.
4. La correction de multiplicite (Benjamini-Hochberg) s'applique a l'analyse de
   la sortie, sur l'ensemble des actifs testes.
5. Une decouverte reste une hypothese : PAPER ONLY.

Ce que le script fait
---------------------
Il LIT l'archive de barres v2 et les specifications figees. Il n'ouvre pas MT5,
ne passe aucun ordre, n'ecrit rien hors de ``results/rejeu_univers``.

Le spread applique est le **spread median archive de la periode**, converti en
prix par le ``point`` du symbole — et non le spread du jour du backtest, qui
etait la simplification du pilote.

Usage
-----
    python tools/rejeu_univers.py --part 0 --sur 6
    python tools/rejeu_univers.py --symboles XAUUSD BTCUSD
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from titanium.data.archive_barres import (  # noqa: E402
    charger_barres,
    chemin as chemin_archive,
    inventaire,
)
from titanium.edge import asset_class_of  # noqa: E402

DEST = RACINE / "results" / "rejeu_univers"
DEST_BRUT = RACINE / "results" / "rejeu_univers_brut"
SPECIFICATIONS = RACINE / "results" / "barres" / "_specifications.json"
FICHIERS_MOTEUR = [
    Path(__file__).resolve(),
    RACINE / "titanium" / "backtest.py",
    RACINE / "titanium" / "data" / "archive_barres.py",
    RACINE / "titanium" / "edge.py",
    RACINE / "titanium" / "features" / "builder.py",
    RACINE / "titanium" / "features" / "candlesticks.py",
    RACINE / "titanium" / "features" / "indicators.py",
    RACINE / "titanium" / "features" / "smc.py",
    RACINE / "titanium" / "features" / "structure.py",
    RACINE / "titanium" / "features" / "ict_structure.py",
    RACINE / "titanium" / "gates" / "confluence_gate.py",
]

#: Plancher de clotures sous lequel une cellule n'est pas concluante.
CLOTURES_MIN = 60

#: Part de la periode reservee a la calibration. Le reste ne sert qu'a verifier.
PART_CALIBRATION = 2.0 / 3.0

#: Schema des artefacts bruts, independant du schema des resumes historiques.
SCHEMA_BRUT = 2

# Un index de barre MT5 designe son ouverture. La decision du backtest utilise
# son close; l'instant causal est donc l'ouverture + la duree fixe du TF.
# MN1 est volontairement absent: sa duree varie avec le calendrier.
DUREE_TIMEFRAME_S = {
    "M1": 60,
    "M5": 5 * 60,
    "M15": 15 * 60,
    "M30": 30 * 60,
    "H1": 60 * 60,
    "H4": 4 * 60 * 60,
    "D1": 24 * 60 * 60,
    "W1": 7 * 24 * 60 * 60,
}


def _json_canonique(valeur) -> bytes:
    return (json.dumps(
        valeur, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def _sha256(contenu: bytes) -> str:
    return hashlib.sha256(contenu).hexdigest()


def _sha256_fichier(chemin: Path) -> str:
    digest = hashlib.sha256()
    with chemin.open("rb") as fichier:
        for bloc in iter(lambda: fichier.read(1024 * 1024), b""):
            digest.update(bloc)
    return digest.hexdigest()


def _nom_stable(chemin: Path) -> str:
    try:
        return chemin.resolve().relative_to(RACINE.resolve()).as_posix()
    except ValueError:
        return chemin.name


def construire_snapshot_rejeu(*, symbole: str, ltf_tf: str, htf_tf: str,
                               asset_class: str,
                               barres: int | None, pas: int, spec: dict,
                               qualite: dict | None = None,
                               fichiers_entree: dict[str, Path],
                               fichiers_moteur: list[Path]) -> dict:
    """Scelle les donnees, le protocole et le code qui determinent un rejeu."""
    classe = str(asset_class or "").strip().lower()
    if not classe:
        raise ValueError("classe d'actif absente du snapshot")
    snapshot = {
        "schema_version": SCHEMA_BRUT,
        "symbol": symbole,
        "asset_class": classe,
        "sources": {
            nom: {
                "name": _nom_stable(chemin),
                "bytes": chemin.stat().st_size,
                "sha256": _sha256_fichier(chemin),
            }
            for nom, chemin in sorted(fichiers_entree.items())
        },
        "engine": [
            {
                "name": _nom_stable(chemin),
                "bytes": chemin.stat().st_size,
                "sha256": _sha256_fichier(chemin),
            }
            for chemin in sorted(fichiers_moteur, key=lambda p: _nom_stable(p))
        ],
        "specification": spec,
        "protocol": {
            "ltf": ltf_tf.upper(),
            "htf": htf_tf.upper(),
            "barres": barres,
            "pas": pas,
            "part_calibration": PART_CALIBRATION,
            "spread_policy": "median_archive_points_x_symbol_point",
            "min_clotures": CLOTURES_MIN,
            "quality_gates": qualite or {},
        },
    }
    snapshot["snapshot_id"] = _sha256(_json_canonique(snapshot))
    return snapshot


def _instant(valeur: str) -> datetime:
    return datetime.fromisoformat(str(valeur).replace("Z", "+00:00"))


def _decision_at(bar_entree: str, snapshot: dict) -> str:
    """Rend la cloture UTC de la barre qui a effectivement produit le signal."""
    timeframe = str(snapshot.get("protocol", {}).get("ltf", "")).upper()
    duree = DUREE_TIMEFRAME_S.get(timeframe)
    if duree is None:
        raise ValueError(f"timeframe LTF sans duree fixe: {timeframe or '?'}")
    instant = _instant(bar_entree)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("barre d'entree sans fuseau")
    decision = instant.astimezone(timezone.utc) + timedelta(seconds=duree)
    return decision.isoformat()


def separer_trades(trades: list, coupure: str) -> tuple[list, list]:
    """Separe chronologiquement sans comparer les representations ISO en texte."""
    tries = sorted(trades, key=lambda trade: _instant(trade.bar_entree))
    instant_coupure = _instant(coupure)
    return (
        [trade for trade in tries if _instant(trade.bar_entree) < instant_coupure],
        [trade for trade in tries if _instant(trade.bar_entree) >= instant_coupure],
    )


def construire_artefact_brut(symbole: str, trades: list, *, coupure: str,
                             snapshot: dict) -> tuple[bytes, dict]:
    """Construit les lignes et leur manifeste, sans horloge ni alea runtime."""
    lignes = []
    classe = str(snapshot.get("asset_class", "")).strip().lower()
    if not classe:
        raise ValueError("classe d'actif absente du snapshot")
    # Valider les horloges avant le split garantit une erreur explicite, plutot
    # qu'une comparaison datetime naive/aware accidentelle.
    for trade in trades:
        _decision_at(trade.bar_entree, snapshot)
    calibration, verification = separer_trades(trades, coupure)
    for ordinal, trade in enumerate(calibration + verification):
        split = "calibration" if ordinal < len(calibration) else "verification"
        decision_at = _decision_at(trade.bar_entree, snapshot)
        identite = {
            "snapshot_id": snapshot["snapshot_id"],
            "ordinal": ordinal,
            "symbol": symbole,
            "decision_at": decision_at,
            "bar_sortie": trade.bar_sortie,
            "side": trade.side,
            "quantity": 1.0,
            "quantity_unit": "risk_unit",
            "asset_class": classe,
        }
        lignes.append({
            "schema_version": SCHEMA_BRUT,
            "trade_id": f"bt:v2:{_sha256(_json_canonique(identite))}",
            "ordinal": ordinal,
            "symbol": symbole,
            "split": split,
            "side": trade.side,
            "decision_at": decision_at,
            "quantity": 1.0,
            "quantity_unit": "risk_unit",
            "asset_class": classe,
            "bar_entree": trade.bar_entree,
            "bar_sortie": trade.bar_sortie,
            "prix_entree": trade.prix_entree,
            "prix_sortie": trade.prix_sortie,
            "sl": trade.sl,
            "tp": trade.tp,
            "r_unit": trade.r_unit,
            "gross_r": round(trade.pnl_r + trade.cost_r, 4),
            "net_r": trade.pnl_r,
            "cost_r": trade.cost_r,
            "mae_r": trade.mae_r,
            "mfe_r": trade.mfe_r,
            "barres": trade.barres,
            "exit_reason": trade.motif,
            "context": trade.contexte,
            "pillars": trade.pillars,
            "family": trade.family,
            "indicators": trade.indicators,
        })
    brut = b"".join(_json_canonique(ligne) for ligne in lignes)
    manifeste = {
        "schema_version": SCHEMA_BRUT,
        "artifact_type": "v14.offline_replay.trades",
        "symbol": symbole,
        "snapshot": snapshot,
        "split": {"coupure": coupure, "calibration_fraction": PART_CALIBRATION},
        "counts": {
            "trades": len(lignes),
            "calibration": sum(ligne["split"] == "calibration" for ligne in lignes),
            "verification": sum(ligne["split"] == "verification" for ligne in lignes),
        },
        "trades": {
            "name": "trades.ndjson",
            "sha256": _sha256(brut),
            "bytes": len(brut),
        },
    }
    manifeste["manifest_sha256"] = _sha256(_json_canonique(manifeste))
    return brut, manifeste


def lier_resume_au_manifeste(manifeste: dict, symbole: str,
                             resume: bytes) -> dict:
    """Scelle le resume dans une copie du manifeste avant publication."""
    lie = dict(manifeste)
    lie.pop("manifest_sha256", None)
    lie["summary"] = {
        "name": f"{symbole}.json",
        "sha256": _sha256(resume),
        "bytes": len(resume),
    }
    lie["manifest_sha256"] = _sha256(_json_canonique(lie))
    return lie


def _ecrire_atomique(chemin: Path, contenu: bytes) -> None:
    chemin.parent.mkdir(parents=True, exist_ok=True)
    temporaire = chemin.with_name(
        f"{chemin.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        temporaire.write_bytes(contenu)
        temporaire.replace(chemin)
    finally:
        temporaire.unlink(missing_ok=True)


def persister_artefact_brut(destination: Path, symbole: str, brut: bytes,
                            manifeste: dict, *, resume_path: Path | None = None,
                            resume: bytes | None = None) -> None:
    """Publie les donnees puis le manifeste, commit atomique de la paire."""
    corps = dict(manifeste)
    sceau = corps.pop("manifest_sha256", None)
    fichier = manifeste.get("trades", {})
    if (sceau != _sha256(_json_canonique(corps))
            or manifeste.get("symbol") != symbole
            or fichier.get("bytes") != len(brut)
            or fichier.get("sha256") != _sha256(brut)):
        raise ValueError("brut et manifeste incoherents")
    fichier_resume = manifeste.get("summary")
    if fichier_resume is not None:
        if (resume_path is None or resume is None
                or fichier_resume.get("name") != resume_path.name
                or fichier_resume.get("bytes") != len(resume)
                or fichier_resume.get("sha256") != _sha256(resume)):
            raise ValueError("resume et manifeste incoherents")
    elif resume_path is not None or resume is not None:
        raise ValueError("resume non scelle dans le manifeste")
    dossier = destination / symbole
    _ecrire_atomique(dossier / "trades.ndjson", brut)
    if resume_path is not None and resume is not None:
        _ecrire_atomique(resume_path, resume)
    _ecrire_atomique(dossier / "manifest.json", _json_canonique(manifeste))


def artefact_brut_valide(destination: Path, symbole: str,
                         snapshot_id: str | None = None,
                         resume_path: Path | None = None) -> bool:
    """Valide sceaux, compteurs, identifiants et arithmetique pour la reprise."""
    dossier = destination / symbole
    chemin_brut = dossier / "trades.ndjson"
    chemin_manifeste = dossier / "manifest.json"
    try:
        manifeste = json.loads(chemin_manifeste.read_text(encoding="utf-8"))
        sceau = manifeste.pop("manifest_sha256")
        if sceau != _sha256(_json_canonique(manifeste)):
            return False
        manifeste["manifest_sha256"] = sceau
        if manifeste.get("schema_version") != SCHEMA_BRUT:
            return False
        if manifeste.get("symbol") != symbole:
            return False
        if (snapshot_id is not None
                and manifeste.get("snapshot", {}).get("snapshot_id") != snapshot_id):
            return False
        brut = chemin_brut.read_bytes()
        fichier = manifeste["trades"]
        if len(brut) != fichier["bytes"] or _sha256(brut) != fichier["sha256"]:
            return False
        fichier_resume = manifeste.get("summary")
        if resume_path is not None and fichier_resume is None:
            return False
        if fichier_resume is not None:
            if resume_path is None or fichier_resume.get("name") != resume_path.name:
                return False
            resume = resume_path.read_bytes()
            if (len(resume) != fichier_resume.get("bytes")
                    or _sha256(resume) != fichier_resume.get("sha256")):
                return False
        lignes = [json.loads(ligne) for ligne in brut.splitlines() if ligne.strip()]
        if len(lignes) != manifeste["counts"]["trades"]:
            return False
        if len({ligne["trade_id"] for ligne in lignes}) != len(lignes):
            return False
        if sum(ligne["split"] == "calibration" for ligne in lignes) != (
                manifeste["counts"]["calibration"]):
            return False
        if sum(ligne["split"] == "verification" for ligne in lignes) != (
                manifeste["counts"]["verification"]):
            return False
        return all(
            ligne["schema_version"] == SCHEMA_BRUT
            and ligne["symbol"] == symbole
            and str(ligne["trade_id"]).startswith("bt:v2:")
            and ligne["ordinal"] == ordinal
            and ligne["decision_at"] == _decision_at(
                ligne["bar_entree"], manifeste["snapshot"])
            and ligne["quantity"] == 1.0
            and ligne["quantity_unit"] == "risk_unit"
            and ligne["asset_class"] == manifeste["snapshot"]["asset_class"]
            and abs((ligne["gross_r"] - ligne["cost_r"]) - ligne["net_r"]) < 1e-8
            for ordinal, ligne in enumerate(lignes)
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def specifications() -> dict:
    if not SPECIFICATIONS.is_file():
        return {}
    return json.loads(SPECIFICATIONS.read_text(encoding="utf-8"))


def snapshot_rejeu_courant(*, symbole: str, ltf_tf: str, htf_tf: str,
                           barres: int | None, pas: int,
                           fraicheur_max_s: float | None = None,
                           ratio_reconstruit_max: float | None = None,
                           tolerance_future_s: float = 0.0) -> dict:
    return construire_snapshot_rejeu(
        symbole=symbole,
        ltf_tf=ltf_tf,
        htf_tf=htf_tf,
        asset_class=asset_class_of(symbole),
        barres=barres,
        pas=pas,
        spec=specifications().get(symbole, {}),
        qualite={
            "fraicheur_max_s": fraicheur_max_s,
            "ratio_reconstruit_max": ratio_reconstruit_max,
            "tolerance_future_s": tolerance_future_s,
        },
        fichiers_entree={
            "ltf": chemin_archive(symbole, ltf_tf),
            "htf": chemin_archive(symbole, htf_tf),
        },
        fichiers_moteur=FICHIERS_MOTEUR,
    )


def spread_median_prix(ltf, spec: dict) -> float:
    """Spread median de la periode, en unites de prix.

    L'archive porte le spread en points, barre par barre. Utiliser le spread du
    jour du backtest — ce que faisait le pilote — flatte les periodes calmes et
    punit les periodes tendues, dans les deux cas a tort.
    """
    point = float(spec.get("point") or 0.0)
    if not point or "spread" not in ltf.columns or ltf.empty:
        return 0.0
    return float(ltf["spread"].median()) * point


def _stats(trades: list) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "esperance_r": 0.0, "winrate": 0.0,
                "profit_factor": None, "concluant": False}
    pnls = [t.pnl_r for t in trades]
    gains = sum(p for p in pnls if p > 0)
    pertes = -sum(p for p in pnls if p < 0)
    return {
        "n": n,
        "esperance_r": round(sum(pnls) / n, 6),
        "ecart_type_r": round((sum((p - sum(pnls) / n) ** 2 for p in pnls)
                               / max(1, n - 1)) ** 0.5, 6),
        "winrate": round(sum(1 for p in pnls if p > 0) / n, 6),
        "profit_factor": round(gains / pertes, 4) if pertes else None,
        "somme_r": round(sum(pnls), 4),
        "concluant": n >= CLOTURES_MIN,
    }


def rejouer_symbole_brut(symbole: str, ltf_tf: str, htf_tf: str,
                         barres: int | None, pas: int, *,
                         fraicheur_max_s: float | None = None,
                         ratio_reconstruit_max: float | None = None,
                         tolerance_future_s: float = 0.0,
                         maintenant_utc=None) -> tuple[dict, list]:
    from titanium.backtest import rejouer

    t0 = time.time()
    portes_qualite = {
        "fraicheur_max_s": fraicheur_max_s,
        "ratio_reconstruit_max": ratio_reconstruit_max,
        "tolerance_future_s": tolerance_future_s,
        "maintenant_utc": maintenant_utc,
    }
    ltf = charger_barres(symbole, ltf_tf, barres, **portes_qualite)
    htf = charger_barres(symbole, htf_tf, **portes_qualite)
    spec = specifications().get(symbole, {})
    spread = spread_median_prix(ltf, spec)

    res = rejouer(symbole, ltf, htf, spread=spread, pas=pas)
    trades = sorted(res.trades, key=lambda t: t.bar_entree)

    # Coupure TEMPORELLE, pas par nombre de trades : une periode agitee ne doit
    # pas voler du temps a la periode de verification.
    debut, fin = ltf.index[0], ltf.index[-1]
    coupure = debut + (fin - debut) * PART_CALIBRATION
    coupure_txt = coupure.isoformat()
    calibration, verification = separer_trades(trades, coupure_txt)

    sortie = {
        "symbole": symbole,
        "ltf": ltf_tf,
        "htf": htf_tf,
        "barres_ltf": int(len(ltf)),
        "debut": str(debut),
        "fin": str(fin),
        "coupure": coupure_txt,
        "spread_points": (float(ltf["spread"].median())
                          if "spread" in ltf.columns else None),
        "spread_prix": spread,
        "n_enter": int(res.n_enter),
        "barres_evaluees": int(res.barres_evaluees),
        "erreurs": int(res.erreurs),
        "qualite_archive": {
            "ltf": dict(ltf.attrs.get("archive_quality", {})),
            "htf": dict(htf.attrs.get("archive_quality", {})),
            "seuils": {
                "fraicheur_max_s": (float(fraicheur_max_s)
                                     if fraicheur_max_s is not None else None),
                "ratio_reconstruit_max": (
                    float(ratio_reconstruit_max)
                    if ratio_reconstruit_max is not None else None
                ),
                "tolerance_future_s": float(tolerance_future_s),
            },
        },
        "global": _stats(trades),
        "calibration": _stats(calibration),
        "verification": _stats(verification),
        "secondes": round(time.time() - t0, 1),
        "ecrit_le": datetime.now(timezone.utc).isoformat(),
    }
    return sortie, trades


def rejouer_symbole(symbole: str, ltf_tf: str, htf_tf: str, barres: int | None,
                    pas: int, *, fraicheur_max_s: float | None = None,
                    ratio_reconstruit_max: float | None = None,
                    tolerance_future_s: float = 0.0,
                    maintenant_utc=None) -> dict:
    """Compatibilite historique : le resume conserve exactement son contrat dict."""
    sortie, _ = rejouer_symbole_brut(
        symbole, ltf_tf, htf_tf, barres, pas,
        fraicheur_max_s=fraicheur_max_s,
        ratio_reconstruit_max=ratio_reconstruit_max,
        tolerance_future_s=tolerance_future_s,
        maintenant_utc=maintenant_utc,
    )
    return sortie


def traiter_symbole(symbole: str, ltf_tf: str, htf_tf: str, barres: int | None,
                    pas: int, *, refaire: bool = False,
                    fraicheur_max_s: float | None = None,
                    ratio_reconstruit_max: float | None = None,
                    tolerance_future_s: float = 0.0,
                    maintenant_utc=None, snapshot: dict | None = None) -> dict | None:
    """Rejoue et publie un couple resume+brut; ``None`` signifie reprise valide."""
    snapshot = snapshot or snapshot_rejeu_courant(
        symbole=symbole, ltf_tf=ltf_tf, htf_tf=htf_tf,
        barres=barres, pas=pas,
        fraicheur_max_s=fraicheur_max_s,
        ratio_reconstruit_max=ratio_reconstruit_max,
        tolerance_future_s=tolerance_future_s,
    )
    cible = DEST / f"{symbole}.json"
    if (not refaire and cible.is_file()
            and artefact_brut_valide(
                DEST_BRUT, symbole, snapshot["snapshot_id"], resume_path=cible)):
        return None

    sortie, trades = rejouer_symbole_brut(
        symbole, ltf_tf, htf_tf, barres, pas,
        fraicheur_max_s=fraicheur_max_s,
        ratio_reconstruit_max=ratio_reconstruit_max,
        tolerance_future_s=tolerance_future_s,
        maintenant_utc=maintenant_utc,
    )
    brut, manifeste = construire_artefact_brut(
        symbole, trades, coupure=sortie["coupure"], snapshot=snapshot)
    resume = json.dumps(sortie, indent=1).encode("utf-8")
    manifeste = lier_resume_au_manifeste(manifeste, symbole, resume)
    # Le manifeste est le commit final. Une interruption avant sa publication
    # laisse forcement un couple brut/resume non validable au prochain passage.
    persister_artefact_brut(
        DEST_BRUT, symbole, brut, manifeste, resume_path=cible, resume=resume)
    return sortie


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--symboles", nargs="*", default=None)
    ap.add_argument("--ltf", default="M15")
    ap.add_argument("--htf", default="H4")
    ap.add_argument("--barres", type=int, default=0, help="0 = toute la profondeur")
    ap.add_argument("--pas", type=int, default=1)
    ap.add_argument(
        "--fraicheur-max-heures", type=float, default=0.0,
        help="age maximal de la derniere barre; 0 desactive la porte",
    )
    ap.add_argument(
        "--ratio-reconstruit-max", type=float, default=None,
        help="ratio maximal post-borne entre 0 et 1; absent = porte desactivee",
    )
    ap.add_argument(
        "--tolerance-future-secondes", type=float, default=0.0,
        help="avance maximale acceptee de la derniere barre; defaut 0",
    )
    ap.add_argument("--part", type=int, default=0, help="index du lot")
    ap.add_argument("--sur", type=int, default=1, help="nombre de lots")
    ap.add_argument("--refaire", action="store_true",
                    help="rejoue meme si la sortie existe deja")
    args = ap.parse_args()
    if args.fraicheur_max_heures < 0:
        ap.error("--fraicheur-max-heures doit etre >= 0")
    if (args.ratio_reconstruit_max is not None
            and not 0 <= args.ratio_reconstruit_max <= 1):
        ap.error("--ratio-reconstruit-max doit etre compris entre 0 et 1")
    if args.tolerance_future_secondes < 0:
        ap.error("--tolerance-future-secondes doit etre >= 0")
    fraicheur_max_s = (args.fraicheur_max_heures * 3600.0
                       if args.fraicheur_max_heures else None)

    symboles = args.symboles or sorted(inventaire(args.ltf))
    lot = [s for i, s in enumerate(symboles) if i % args.sur == args.part]
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"lot {args.part + 1}/{args.sur} : {len(lot)} symboles", flush=True)

    for symbole in lot:
        # Trace d'entree : un rejeu a pleine profondeur dure environ une heure
        # par symbole. Sans cette ligne, un lot parait fige pendant tout ce
        # temps et on ne peut pas distinguer un travail en cours d'un blocage.
        print(f"{symbole:12} en cours...", flush=True)
        try:
            sortie = traiter_symbole(
                symbole, args.ltf, args.htf, args.barres or None, args.pas,
                refaire=args.refaire,
                fraicheur_max_s=fraicheur_max_s,
                ratio_reconstruit_max=args.ratio_reconstruit_max,
                tolerance_future_s=args.tolerance_future_secondes,
            )
        except Exception as e:  # un symbole qui casse n'arrete pas le lot
            print(f"{symbole:12} ECHEC {type(e).__name__}: {e}", flush=True)
            continue
        if sortie is None:
            print(f"{symbole:12} deja fait (resume + brut valides)", flush=True)
            continue
        g, v = sortie["global"], sortie["verification"]
        print(f"{symbole:12} n={g['n']:5} esp {g['esperance_r']:+.4f} R  "
              f"win {g['winrate'] * 100:4.1f}%  | verif n={v['n']:4} "
              f"esp {v['esperance_r']:+.4f} R  [{sortie['secondes']}s]",
              flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
