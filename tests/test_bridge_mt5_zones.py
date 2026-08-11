"""Tests du pont V14 → MetaTrader 5.

Deux exigences dominent, et aucune ne concerne l'esthétique :

1. **Le graphique doit montrer ce qui a décidé.** Les zones tracées viennent des
   valeurs numériques de la trace, pas d'une reconstruction faite pour
   l'affichage. Si les deux divergeaient, le graphique mentirait.

2. **L'affichage ne casse jamais le trading.** Dossier introuvable, disque plein,
   objet corrompu : `ecrire()` rend None et la boucle continue.

Aucun test ne touche au vrai terminal — tout passe par un dossier temporaire.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from titanium.bridge.mt5_zones import (
    NOM_FICHIER,
    PREFIXE,
    SCHEMA,
    Plan,
    Zone,
    ZonePayload,
    ecrire,
    effacer,
    zones_depuis_features,
)
from titanium.features.builder import build_feats


def bougies(n=400, freq="15min") -> pd.DataFrame:
    idx = pd.date_range(end=pd.Timestamp("2026-08-07 04:30", tz="UTC"),
                        periods=n, freq=freq)
    rng = np.random.default_rng(5)
    base = np.cumsum(rng.normal(0, 0.3, n)) + 100
    return pd.DataFrame({
        "open": base, "high": base + 0.4, "low": base - 0.4,
        "close": base + 0.05, "tick_volume": rng.integers(80, 400, n).astype(float),
    }, index=idx)


def charge(**over) -> ZonePayload:
    base = dict(symbol="EURUSD", timeframe="M15", verdict="ENTER",
                code="ENTER_CONFLUENCE",
                pillars=[{"name": f"g{i}", "passed": i < 4} for i in range(6)],
                zones=[Zone("sr", 1.1495, 1.1509, "support", 1.0)],
                plan=Plan(side=1, entry=1.1523, sl=1.1495, tp=1.1579))
    base.update(over)
    return ZonePayload(**base)


# ══════════════ le R:R est calculé, jamais déclaré ═══════════════════════════

def test_rr_calcule():
    """Risque 28 pips, gain 56 → R:R 2.0. Un R:R déclaré à la main finirait
    par mentir dès qu'un stop bouge."""
    p = Plan(side=1, entry=1.1523, sl=1.1495, tp=1.1579)
    assert p.rr == pytest.approx(2.0, abs=0.01)


def test_rr_short_symetrique():
    p = Plan(side=-1, entry=1.1523, sl=1.1551, tp=1.1467)
    assert p.rr == pytest.approx(2.0, abs=0.01)


@pytest.mark.parametrize("plan", [
    Plan(), Plan(side=1, entry=1.15), Plan(side=1, entry=1.15, sl=1.15),
])
def test_rr_absent_si_plan_incomplet(plan):
    assert plan.rr is None


# ══════════════ écriture : atomique et sans exception ════════════════════════

def test_ecriture_produit_un_json_lisible(tmp_path):
    f = ecrire(charge(), tmp_path)
    assert f is not None and f.name == NOM_FICHIER
    d = json.loads(f.read_text(encoding="utf-8"))
    assert d["symbol"] == "EURUSD"
    assert d["schema"] == SCHEMA
    assert d["plan"]["rr"] == pytest.approx(2.0, abs=.01)


def test_pas_de_fichier_temporaire_laisse(tmp_path):
    """L'indicateur lit à chaque tick : un .tmp traînant serait lu un jour."""
    ecrire(charge(), tmp_path)
    assert not list(tmp_path.glob("*.tmp"))


def test_reecriture_remplace_sans_doubler(tmp_path):
    ecrire(charge(), tmp_path)
    ecrire(charge(symbol="XAUUSD"), tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert json.loads((tmp_path / NOM_FICHIER).read_text(encoding="utf-8"))["symbol"] == "XAUUSD"


def test_dossier_introuvable_ne_leve_pas(monkeypatch):
    import titanium.bridge.mt5_zones as m
    monkeypatch.setattr(m, "dossier_mql5_files", lambda *a, **k: None)
    assert ecrire(charge()) is None


def test_charge_incoherente_ne_leve_pas(tmp_path):
    """Un objet non sérialisable doit rendre None, pas remonter vers la boucle."""
    p = charge()
    p.zones = [object()]                     # volontairement invalide
    assert ecrire(p, tmp_path) is None


def test_effacer_ecrit_une_charge_vide(tmp_path):
    ecrire(charge(), tmp_path)
    assert effacer(tmp_path) is True
    d = json.loads((tmp_path / NOM_FICHIER).read_text(encoding="utf-8"))
    assert d["symbol"] == ""
    assert d["zones"] == []


def test_horodatage_pose_a_lecriture(tmp_path):
    f = ecrire(charge(), tmp_path)
    assert json.loads(f.read_text(encoding="utf-8"))["written_at"]


# ══════════════ la ligne de piliers, lisible d'un coup d'œil ═════════════════

def test_ligne_de_piliers():
    """'+' passé, '.' manquant — l'indicateur l'affiche tel quel sur le graphique."""
    d = charge().to_dict()
    assert d["pillars_line"] == "++++.."


def test_ligne_de_piliers_vide_si_aucun():
    assert charge(pillars=[]).to_dict()["pillars_line"] == ""


# ══════════ les zones viennent de la trace, pas d'une reconstruction ═════════

def test_zones_construites_depuis_les_valeurs_numeriques():
    f = build_feats(bougies(), bougies(freq="4h"))
    p = zones_depuis_features("EURUSD", f)
    t = f["_trace"]
    kinds = {z.kind for z in p.zones}
    if t.get("sr_level"):
        assert "sr" in kinds, "le niveau S/R regardé doit être tracé"
    if t.get("vpoc"):
        assert "vpoc" in kinds
    assert p.price == t["price"]
    assert p.bar_time == t["bar_time"]


def test_la_trace_expose_bien_les_valeurs_numeriques():
    """Sans elles, le pont devrait analyser des phrases — et finirait par
    tracer autre chose que ce qui a décidé."""
    t = build_feats(bougies(), bougies(freq="4h"))["_trace"]
    for cle in ("sr_level", "sr_strength", "vpoc", "ote_zone", "fvg_open"):
        assert cle in t, f"{cle} manquant : le graphique divergerait de la décision"


def test_zone_sr_centree_sur_le_niveau():
    f = build_feats(bougies(), bougies(freq="4h"))
    niveau = (f["_trace"]).get("sr_level")
    if not niveau:
        pytest.skip("aucun niveau S/R sur ce jeu de données")
    sr = next(z for z in zones_depuis_features("EURUSD", f).zones if z.kind == "sr")
    assert sr.bas < niveau < sr.haut


def test_fvg_bornees():
    """Au-delà de six zones le graphique devient illisible."""
    t = build_feats(bougies(), bougies(freq="4h"))["_trace"]
    assert len(t.get("fvg_open") or []) <= 6


def test_features_sans_trace_ne_levent_pas():
    p = zones_depuis_features("EURUSD", {})
    assert p.symbol == "EURUSD"
    assert p.zones == []


# ══════════════ contrat avec l'indicateur MQL5 ═══════════════════════════════

def test_le_prefixe_est_reserve():
    """L'indicateur ne supprime que les objets TITANIUM_* : c'est ce qui
    garantit que les tracés manuels de l'utilisateur survivent."""
    assert PREFIXE == "TITANIUM_"


def test_le_mq5_et_le_python_partagent_le_meme_contrat():
    """Un renommage d'un seul côté casserait le pont en silence."""
    from pathlib import Path
    src = Path("titanium/bridge/titanium_zones.mq5").read_text(encoding="utf-8")
    assert f'#define PREFIXE   "{PREFIXE}"' in src
    assert f'#define FICHIER   "{NOM_FICHIER}"' in src
    assert f"#define SCHEMA_OK {SCHEMA}" in src


def test_le_mq5_ne_passe_aucun_ordre():
    """Garde-fou : l'indicateur est un afficheur. S'il gagne un jour la
    capacité de trader, ce test doit hurler."""
    from pathlib import Path
    src = Path("titanium/bridge/titanium_zones.mq5").read_text(encoding="utf-8")
    for interdit in ("OrderSend", "trade.Buy", "trade.Sell", "CTrade",
                     "PositionOpen", "OrderClose"):
        assert interdit not in src, f"appel de trading dans un indicateur : {interdit}"


def test_ordre_des_champs_json_est_un_contrat():
    """Le parseur MQL5 balaie les objets APRÈS la clé "zones" et s'arrête au
    premier qui n'a pas de "kind". Il dépend donc de l'ORDRE des champs :

        … pillars … zones … plan …

    Réordonner le dataclass casserait le tracé **en silence** — zéro zone
    dessinée, aucune erreur nulle part. D'où ce test.
    """
    cles = list(charge().to_dict())
    assert cles.index("pillars") < cles.index("zones") < cles.index("plan")


def test_les_objets_pillars_nont_pas_de_cle_kind():
    """C'est ce qui permet au parseur de savoir où le tableau `zones` s'arrête."""
    for p in charge().to_dict()["pillars"]:
        assert "kind" not in p


def test_toutes_les_zones_portent_kind():
    """Une zone sans `kind` ferait croire au parseur que le tableau est fini,
    et toutes les suivantes seraient perdues."""
    d = charge(zones=[Zone("sr", 1, 2), Zone("ote", 3, 4), Zone("fvg", 5, 6)]).to_dict()
    assert all(z.get("kind") for z in d["zones"])


def test_le_mq5_ne_supprime_que_nos_objets():
    """`ObjectsDeleteAll` sans filtre effacerait les tracés de l'utilisateur."""
    from pathlib import Path
    src = Path("titanium/bridge/titanium_zones.mq5").read_text(encoding="utf-8")
    assert "ObjectsDeleteAll" not in src
    assert "StringFind(nom, PREFIXE) == 0" in src
