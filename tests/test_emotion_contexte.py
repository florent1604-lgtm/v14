from __future__ import annotations

import pandas as pd

from titanium.emotion.contexte import RawInputs, assemble_context
from titanium.emotion.engine import compute_emotion


def test_source_sans_horodatage_est_perimee_malgre_les_bougies():
    rows = [
        {
            "open": 100.0 + index,
            "high": 102.0 + index,
            "low": 99.0 + index,
            "close": 101.0 + index,
            "v": 100.0 + index,
        }
        for index in range(60)
    ]

    context = assemble_context(
        RawInputs(delta_pct=0.4, candles=pd.DataFrame(rows), now=1_000.0),
        max_age_s=60.0,
    )
    state = compute_emotion(context)

    assert state.available
    assert state.stale