
import json, sys
sys.path.insert(0, ".")
from pathlib import Path
import importlib.util
spec = importlib.util.spec_from_file_location("rj", "tools/rejeu_univers.py")
rj = importlib.util.module_from_spec(spec); spec.loader.exec_module(rj)
for sym in ("DOTUSD", "BTCUSD", "COFFEE.fs"):
    snap = rj.snapshot_rejeu_courant(symbole=sym, ltf_tf="M15", htf_tf="H4", barres=None, pas=1,
                                     fraicheur_max_s=None, ratio_reconstruit_max=None, tolerance_future_s=0.0)
    man = Path(f"results/rejeu_univers_brut/{sym}/manifest.json")
    ancien = json.loads(man.read_text(encoding="utf-8"))["snapshot"]["snapshot_id"] if man.is_file() else None
    print(sym, "courant", snap["snapshot_id"][:16], "| artefact", (ancien or "ABSENT")[:16],
          "| identique:", ancien == snap["snapshot_id"])
    print("   valide selon le moteur:", rj.artefact_brut_valide(rj.DEST_BRUT, sym, snap["snapshot_id"],
          resume_path=rj.DEST / f"{sym}.json"))
