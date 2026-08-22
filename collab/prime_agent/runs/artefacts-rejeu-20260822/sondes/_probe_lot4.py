
import json, sys
sys.path.insert(0, ".")
from titanium.data.archive_barres import inventaire
syms = sorted(inventaire("M15"))
lot4 = [s for i, s in enumerate(syms) if i % 8 == 4]
print(json.dumps({"lot4": lot4, "sans_usdusc": [s for s in lot4 if s != "USDUSC"]}, indent=1))
