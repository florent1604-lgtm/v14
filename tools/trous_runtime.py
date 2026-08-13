"""Propose les trous runtime, avec la preuve que le marché cotait.

    python tools/trous_runtime.py
    python tools/trous_runtime.py --json results/trous_runtime.json

Complément à `tools/analyse_pertes.py`, qui reçoit ses `runtime_gaps` en
paramètre explicite — un choix sain : les trous sont scellés dans l'artefact et
restent auditables. Mais tant que la liste est saisie à la main, l'exclusion
dépend de ce que quelqu'un a pensé à y mettre. Au 13/08/2026 elle contenait un
seul trou, celui qu'Hermes avait nommé.

CE QUE CET OUTIL FAIT, ET LA DISTINCTION QUI COMPTE
----------------------------------------------------
`shadow_prod.ndjson` reçoit une ligne par setup évalué, à chaque tour. Un
silence prolongé y signale qu'aucun tour n'a laissé de trace — mais **silence
n'est pas panne** : la nuit et le week-end, il n'y a rien à balayer, et la
boucle se tait légitimement.

Le discriminant est le marché lui-même. Si des barres M15 existent sur des
symboles liquides PENDANT le silence, le marché cotait et la boucle aurait dû
écrire : c'est un arrêt. Si aucune barre n'existe, le silence est normal.

L'outil ne décide pas à la place de l'analyste : il propose une liste avec sa
preuve, à sceller explicitement dans l'artefact.

Lecture seule. Aucun ordre, aucun seuil.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

#: Au-delà de ce silence, on regarde de plus près. La boucle tourne en 60 s.
SILENCE_MIN_S = 300.0

#: Symboles témoins. Trois classes indépendantes : si aucun des trois ne cote,
#: aucun marché ne cotait. Le crypto est là exprès — il cote la nuit et le
#: week-end, donc son silence est une information forte.
TEMOINS = ("EURUSD", "BTCUSD", "US500")

#: Barres témoins minimales pour déclarer que le marché cotait. Une seule
#: barre pourrait être un résidu d'ouverture ; trois marquent une séance.
BARRES_MIN = 3


def silences(chemin: Path, seuil_s: float = SILENCE_MIN_S) -> list[dict]:
    """Périodes sans aucune ligne d'observation."""
    ts = []
    if not chemin.exists():
        return []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip():
            continue
        try:
            v = json.loads(ligne).get("ts")
        except json.JSONDecodeError:
            continue
        try:
            d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        ts.append(d if d.tzinfo else d.replace(tzinfo=timezone.utc))
    ts.sort()
    out = []
    for a, b in zip(ts, ts[1:]):
        if (b - a).total_seconds() > seuil_s:
            out.append({"start": a.isoformat(), "end": b.isoformat(),
                        "minutes": round((b - a).total_seconds() / 60.0, 1)})
    return out


def barres_pendant(symbole: str, debut: datetime, fin: datetime, lecteur) -> int:
    """Barres M15 réellement cotées dans l'intervalle. 0 si illisible."""
    try:
        df = lecteur(symbole, "M15", 5000)
    except Exception:  # noqa: BLE001
        return 0
    if df is None or len(df) == 0:
        return 0
    try:
        return int(((df.index > debut) & (df.index < fin)).sum())
    except TypeError:
        return 0


def qualifier(trous: list[dict], lecteur) -> list[dict]:
    """Ajoute à chaque silence la preuve que le marché cotait — ou non."""
    out = []
    for t in trous:
        debut = datetime.fromisoformat(t["start"])
        fin = datetime.fromisoformat(t["end"])
        preuve = {s: barres_pendant(s, debut, fin, lecteur) for s in TEMOINS}
        cotait = max(preuve.values(), default=0) >= BARRES_MIN
        out.append({**t, "barres_temoins": preuve,
                    "marche_cotait": cotait,
                    "verdict": "ARRET" if cotait else "silence normal"})
    return out


def rapport(qualifies: list[dict]) -> str:
    out: list[str] = []
    w = out.append
    arrets = [t for t in qualifies if t["marche_cotait"]]

    w(f"SILENCES > {int(SILENCE_MIN_S // 60)} min : {len(qualifies)}")
    w(f"  dont ARRÊTS avérés (le marché cotait) : {len(arrets)}")
    w("")
    w(f"  {'début':<17} {'fin':<17} {'durée':>8}  "
      + "  ".join(f"{s:>7}" for s in TEMOINS) + "   verdict")
    w("  " + "-" * 86)
    for t in qualifies:
        w(f"  {t['start'][5:16]:<17} {t['end'][5:16]:<17} "
          f"{t['minutes']:>6.0f} m  "
          + "  ".join(f"{t['barres_temoins'][s]:>7}" for s in TEMOINS)
          + f"   {t['verdict']}")
    w("")
    w("LECTURE")
    w("  Des barres M15 pendant le silence ⇒ le marché cotait ⇒ la boucle")
    w("  aurait dû écrire ⇒ c'est un arrêt. Aucune barre ⇒ nuit ou week-end,")
    w("  la boucle se tait légitimement et rien n'est à exclure.")
    w("")
    w("  BTCUSD est le témoin décisif : il cote la nuit et le week-end. Un")
    w("  silence pendant que BTCUSD cotait ne s'explique pas par l'horaire.")
    w("")
    w("À SCELLER DANS L'ARTEFACT (paramètre `runtime_gaps`)")
    if arrets:
        w(json.dumps([{"start": t["start"], "end": t["end"]} for t in arrets],
                     indent=1))
    else:
        w("  aucun — tous les silences s'expliquent par l'horaire de marché")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--observations",
                    default=str(RACINE / "results" / "shadow_prod.ndjson"))
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    from titanium.data.mt5_vendor import get_rates, mt5_session

    bruts = silences(Path(a.observations))
    if not bruts:
        print("aucun silence détecté")
        return 0
    print(f"{len(bruts)} silence(s) à qualifier · lecture des barres témoins…\n")

    with mt5_session():
        qualifies = qualifier(bruts, get_rates)

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(qualifies, ensure_ascii=False, indent=1),
                                encoding="utf-8")
    print(rapport(qualifies))
    if a.json:
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
