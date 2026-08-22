
"""Sonde 2: chaine complete resume+brut sur une tranche bornee, dans un dossier
jetable. Ne touche a aucun artefact publie."""
import json, sys, tempfile
from pathlib import Path
RACINE = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(RACINE))

import tools.rejeu_univers as ru

SYMBOLE = sys.argv[1] if len(sys.argv) > 1 else "AAVE-USD"
BARRES = int(sys.argv[2]) if len(sys.argv) > 2 else 6000
bac = Path(tempfile.mkdtemp(prefix="sonde_rejeu_"))
ru.DEST = bac / "rejeu_univers"
ru.DEST_BRUT = bac / "rejeu_univers_brut"
ru.ECHEC_SENTINEL = ru.DEST_BRUT / "_RUN_FAILED.json"
ru.DEST.mkdir(parents=True, exist_ok=True)

sortie = ru.traiter_symbole(SYMBOLE, "M15", "H4", BARRES, 1)
print("n_enter", sortie["n_enter"], "global", sortie["global"]["n"],
      "calib", sortie["calibration"]["n"], "verif", sortie["verification"]["n"])

trades_path = ru.DEST_BRUT / SYMBOLE / "trades.ndjson"
lignes = [json.loads(x) for x in trades_path.read_text(encoding="utf-8").splitlines() if x]
print("lignes brutes:", len(lignes), "octets:", trades_path.stat().st_size)
requis = {"decision_at", "asset_class", "quantity", "quantity_unit", "side", "trade_id"}
manquants = requis - set(lignes[0])
print("champs A/B manquants:", manquants or "aucun")
print("exemple:", {k: lignes[0][k] for k in sorted(requis)})
print("manifeste valide:", ru.artefact_brut_valide(
    ru.DEST_BRUT, SYMBOLE,
    json.loads((ru.DEST_BRUT / SYMBOLE / "manifest.json").read_text(
        encoding="utf-8"))["snapshot"]["snapshot_id"],
    resume_path=ru.DEST / f"{SYMBOLE}.json"))
print("BAC", bac)
