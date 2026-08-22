
import numpy as np, pandas as pd
from titanium.data.archive_barres import charger_barres
ltf = charger_barres("COFFEE.fs", "M15")
print(type(ltf.index), ltf.index.dtype, len(ltf))
t = ltf.index.asi8
print("premiers ns:", t[:5])
sec = t // 10**9
d = np.diff(sec)
import collections
print("top deltas:", collections.Counter(d.tolist()).most_common(5))
print(ltf.index[:6])
print(ltf.index[1288:1296])
