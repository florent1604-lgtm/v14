"""Qualification des trous runtime — complément à la tâche 454c1ec2.

`tools/analyse_pertes.py` reçoit ses `runtime_gaps` en paramètre explicite.
C'est le bon choix — ils sont scellés dans l'artefact et restent auditables —
mais tant que la liste est saisie à la main, l'exclusion vaut ce que vaut la
mémoire de celui qui l'a remplie. Le 13/08/2026 elle contenait un seul trou.

Ce que ces tests verrouillent :

1. un silence dans le journal d'observation est détecté ;
2. **le critère est une COUVERTURE, jamais un compte absolu de barres** — c'est
   la régression qui a fait passer deux arrêts en séance pour des nuits ;
3. un silence pendant lequel aucun marché ne cotait n'est pas un arrêt ;
4. la sortie a exactement la forme que `seal_excursions` attend.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tools import trous_runtime as tr


def _obs(dossier, horodatages):
    p = dossier / "shadow_prod.ndjson"
    p.write_text("\n".join(json.dumps({"ts": t}) for t in horodatages),
                 encoding="utf-8")
    return p


def _serie(debut: str, n: int, pas_min: int = 15):
    """Barres M15 régulières à partir de `debut`."""
    idx = pd.date_range(debut, periods=n, freq=f"{pas_min}min", tz="UTC")
    return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0},
                        index=idx)


def _lecteur(df):
    return lambda _sym, _tf, _n: df


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Détection
# ═══════════════════════════════════════════════════════════════════════════════

class TestSilences:
    def test_flux_regulier_aucun_silence(self, tmp_path):
        base = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
        p = _obs(tmp_path, [(base + timedelta(minutes=i)).isoformat()
                            for i in range(30)])
        assert tr.silences(p) == []

    def test_un_saut_est_detecte(self, tmp_path):
        p = _obs(tmp_path, ["2026-08-11T10:00:00+00:00",
                            "2026-08-11T10:40:00+00:00"])
        s = tr.silences(p)
        assert len(s) == 1
        assert s[0]["minutes"] == pytest.approx(40.0)

    def test_journal_absent_ne_leve_pas(self, tmp_path):
        assert tr.silences(tmp_path / "rien.ndjson") == []

    def test_lignes_illisibles_ignorees(self, tmp_path):
        p = tmp_path / "shadow_prod.ndjson"
        p.write_text('{"ts": "2026-08-11T10:00:00+00:00"}\npas du json\n'
                     '{"ts": "2026-08-11T10:40:00+00:00"}\n', encoding="utf-8")
        assert len(tr.silences(p)) == 1

    def test_horodatages_desordonnes_sont_tries(self, tmp_path):
        """Un journal concaténé peut ne pas être trié ; un faux saut en
        résulterait."""
        p = _obs(tmp_path, ["2026-08-11T10:40:00+00:00",
                            "2026-08-11T10:00:00+00:00",
                            "2026-08-11T10:01:00+00:00"])
        s = tr.silences(p)
        assert len(s) == 1
        assert s[0]["start"].startswith("2026-08-11T10:01")


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LE critère — couverture, pas compte absolu
# ═══════════════════════════════════════════════════════════════════════════════

class TestCouverture:
    """La première version exigeait « au moins 3 barres ». Un silence de 28
    minutes ne peut en contenir que deux : il était inclassable comme arrêt
    PAR CONSTRUCTION. Deux arrêts en pleine séance européenne — 28 et 38
    minutes, couverture réelle 106 % et 79 % — ont ainsi été étiquetés
    « silence normal »."""

    def test_silence_court_entierement_couvert_est_un_arret(self):
        """28 minutes, 2 barres attendues, 2 observées : le marché cotait."""
        trou = [{"start": "2026-08-12T13:09:00+00:00",
                 "end": "2026-08-12T13:37:00+00:00", "minutes": 28.0}]
        df = _serie("2026-08-12T13:10:00Z", 2)
        q = tr.qualifier(trou, _lecteur(df))[0]
        assert q["marche_cotait"] is True
        assert q["verdict"] == "ARRET"
        assert q["couverture"] >= 1.0

    def test_silence_long_sans_cotation_est_normal(self):
        """Neuf heures de nuit : aucune barre, rien à exclure."""
        trou = [{"start": "2026-08-10T19:00:00+00:00",
                 "end": "2026-08-11T04:00:00+00:00", "minutes": 540.0}]
        df = _serie("2026-08-11T05:00:00Z", 20)   # toutes APRÈS le trou
        q = tr.qualifier(trou, _lecteur(df))[0]
        assert q["marche_cotait"] is False
        assert q["verdict"] == "silence normal"

    def test_couverture_partielle_sous_le_seuil(self):
        """Le marché a rouvert à la fin : ce n'est pas un arrêt de la boucle."""
        trou = [{"start": "2026-08-10T19:00:00+00:00",
                 "end": "2026-08-11T04:00:00+00:00", "minutes": 540.0}]
        df = _serie("2026-08-11T03:30:00Z", 2)    # 2 barres pour 36 attendues
        q = tr.qualifier(trou, _lecteur(df))[0]
        assert q["couverture"] < tr.COUVERTURE_MIN
        assert q["marche_cotait"] is False

    def test_le_meilleur_temoin_decide(self):
        """Un seul marché ouvert suffit : la boucle aurait dû le balayer."""
        trou = [{"start": "2026-08-11T10:00:00+00:00",
                 "end": "2026-08-11T10:40:00+00:00", "minutes": 40.0}]

        def lecteur(sym, _tf, _n):
            # Seul BTCUSD cote — c'est le cas d'un week-end crypto.
            return _serie("2026-08-11T10:05:00Z", 3) if sym == "BTCUSD" \
                else _serie("2026-08-01T00:00:00Z", 3)

        assert tr.qualifier(trou, lecteur)[0]["marche_cotait"] is True

    def test_lecteur_en_echec_ne_leve_pas(self):
        trou = [{"start": "2026-08-11T10:00:00+00:00",
                 "end": "2026-08-11T10:40:00+00:00", "minutes": 40.0}]

        def lecteur(*_a):
            raise RuntimeError("MT5 muet")

        q = tr.qualifier(trou, lecteur)[0]
        assert q["marche_cotait"] is False
        assert q["barres_temoins"] == dict.fromkeys(tr.TEMOINS, 0)


# ═══════════════════════════════════════════════════════════════════════════════
# 3. La sortie doit être consommable telle quelle
# ═══════════════════════════════════════════════════════════════════════════════

class TestSortie:
    def test_le_rapport_propose_les_trous_au_format_attendu(self):
        """`seal_excursions` attend des dicts {start, end} — et rien d'autre."""
        trou = [{"start": "2026-08-12T13:09:00+00:00",
                 "end": "2026-08-12T13:37:00+00:00", "minutes": 28.0}]
        q = tr.qualifier(trou, _lecteur(_serie("2026-08-12T13:10:00Z", 2)))
        txt = tr.rapport(q)
        assert "À SCELLER DANS L'ARTEFACT" in txt
        propose = json.loads(txt.split("À SCELLER DANS L'ARTEFACT")[1]
                             .split("\n", 1)[1])
        assert propose == [{"start": trou[0]["start"], "end": trou[0]["end"]}]

    def test_les_trous_proposes_sont_acceptes_par_le_sceau(self, tmp_path):
        """Contrat entre les deux outils, vérifié et non supposé."""
        from tools.analyse_pertes import seal_excursions

        trou = [{"start": "2026-08-12T13:09:00+00:00",
                 "end": "2026-08-12T13:37:00+00:00", "minutes": 28.0}]
        q = tr.qualifier(trou, _lecteur(_serie("2026-08-12T13:10:00Z", 2)))
        gaps = [{"start": t["start"], "end": t["end"]}
                for t in q if t["marche_cotait"]]

        src = tmp_path / "excursions.ndjson"
        src.write_text(json.dumps({
            "ticket": "1", "exit_reason": "init", "pnl_r": -1.0, "mfe_r": 0.0,
            "ts_open": "2026-08-12T13:00:00+00:00",
            "ts_exit": "2026-08-12T14:00:00+00:00"}) + "\n", encoding="utf-8")

        art = seal_excursions(src, runtime_gaps=gaps)
        assert art["counts"]["runtime_gap_censored"] == 1

    def test_rapport_sans_arret_le_dit(self):
        trou = [{"start": "2026-08-10T19:00:00+00:00",
                 "end": "2026-08-11T04:00:00+00:00", "minutes": 540.0}]
        q = tr.qualifier(trou, _lecteur(_serie("2026-08-11T05:00:00Z", 5)))
        assert "aucun" in tr.rapport(q)
