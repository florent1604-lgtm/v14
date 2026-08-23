"""Audit de conformite de l'archive de barres.

Ce que ces tests protegent : la distinction entre un defaut qui compte et un
defaut qui n'en est pas un. Les deux pannes du 22/08/2026 venaient de la
source, et l'audit doit les voir sans crier au loup sur des phenomenes connus.

Deux pieges specifiques sont figes ici :

- **La bascule de printemps n'est pas une corruption.** Sur les actifs cotes
  24 h/24, deux etiquettes serveur consecutives retombent sur le meme instant
  UTC au passage a l'heure d'ete. Trois occurrences par serie et par decennie,
  jamais sur le FX. `charger_barres` les absorbe deja ; l'audit doit les
  compter pour information, pas les traiter comme un OHLC invalide.

- **Un jour ferie isole n'est pas une barre journaliere.** Le critere de
  granularite exige DEUX ecarts journaliers consecutifs, sinon chaque pont de
  mai ferait passer un fichier H4 pour du journalier.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from tools.audit_archive_barres import _defauts  # noqa: E402

H4 = 14400
JOUR = 86400


def _serie(temps, *, open_=None, high=None, low=None, close=None,
           spread=None, reconstruit=None) -> dict:
    n = len(temps)
    un = [1.0] * n
    return {
        "time": np.asarray(temps, dtype=np.int64),
        "open": np.asarray(open_ if open_ is not None else un, dtype=float),
        "high": np.asarray(high if high is not None else [1.5] * n, dtype=float),
        "low": np.asarray(low if low is not None else [0.5] * n, dtype=float),
        "close": np.asarray(close if close is not None else un, dtype=float),
        "spread": np.asarray(spread if spread is not None else [10.0] * n, dtype=float),
        "reconstruit": np.asarray(reconstruit if reconstruit is not None
                                  else [False] * n, dtype=bool),
    }


def _reguliere(n: int, pas: int = H4, debut: int = 1_600_000_000):
    return [debut + pas * i for i in range(n)]


# ── Une serie saine ne doit rien declencher ────────────────────────────────

def test_serie_saine_ne_signale_rien():
    d = _defauts(_serie(_reguliere(50)), 0, H4)
    assert d["barres"] == 50
    assert d["vide"] is False
    for k in ("ohlc_invalides", "horodatage_duplique", "horodatage_en_recul",
              "barres_grossieres", "spread_negatif"):
        assert d[k] == 0


def test_serie_vide_est_signalee():
    d = _defauts(_serie([]), 0, H4)
    assert d["vide"] is True
    assert d["barres"] == 0


def test_la_borne_reduit_bien_la_portee():
    """C'est tout l'objet de l'outil : un defaut hors fenetre ne compte pas."""
    t = _reguliere(50)
    low = [0.5] * 50
    low[3] = 0.0                     # defaut au tout debut
    serie = _serie(t, low=low)
    assert _defauts(serie, 0, H4)["ohlc_invalides"] == 1
    assert _defauts(serie, 10, H4)["ohlc_invalides"] == 0


# ── OHLC ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("champ", "valeur"), [
    ("low", 0.0),        # le cas DJ30.fs du 23/11/2009
    ("low", -1.0),
    ("high", 0.4),       # haut sous le bas
    ("open", 9.0),       # ouverture hors plage
    ("close", 9.0),
])
def test_ohlc_incoherent_est_compte(champ, valeur):
    t = _reguliere(20)
    kw = {"open_": [1.0] * 20, "high": [1.5] * 20,
          "low": [0.5] * 20, "close": [1.0] * 20}
    cle = {"open": "open_", "high": "high", "low": "low", "close": "close"}[champ]
    kw[cle] = list(kw[cle])
    kw[cle][7] = valeur
    assert _defauts(_serie(t, **kw), 0, H4)["ohlc_invalides"] == 1


def test_valeur_non_finie_est_comptee():
    t = _reguliere(20)
    high = [1.5] * 20
    high[5] = float("nan")
    assert _defauts(_serie(t, high=high), 0, H4)["ohlc_invalides"] == 1


# ── Horodatage ─────────────────────────────────────────────────────────────

def test_bascule_de_printemps_comptee_sans_etre_une_corruption():
    """Doublon signale, mais aucun OHLC invalide : ce sont deux choses."""
    t = _reguliere(20)
    t[10] = t[9]                     # meme instant UTC, cas crypto en mars
    d = _defauts(_serie(t), 0, H4)
    assert d["horodatage_duplique"] == 1
    assert d["ohlc_invalides"] == 0


def test_horodatage_en_recul_est_distingue_du_doublon():
    t = _reguliere(20)
    t[10] = t[9] - H4
    d = _defauts(_serie(t), 0, H4)
    assert d["horodatage_en_recul"] == 1
    assert d["horodatage_duplique"] == 0


# ── Granularite ────────────────────────────────────────────────────────────

def test_journalier_etiquete_h4_est_detecte():
    """Le cas DJ30.fs : la portion ancienne du fichier H4 est journaliere."""
    t = [1_600_000_000 + JOUR * i for i in range(10)]
    assert _defauts(_serie(t), 0, H4)["barres_grossieres"] > 0


def test_un_jour_ferie_isole_n_est_pas_du_journalier():
    """Un seul grand ecart ne suffit pas : il en faut deux consecutifs."""
    t = _reguliere(30)
    t = [x if i < 15 else x + JOUR for i, x in enumerate(t)]   # un trou unique
    assert _defauts(_serie(t), 0, H4)["barres_grossieres"] == 0


def test_pas_de_detection_de_granularite_sur_le_journalier():
    """Sur du D1, un ecart d'un jour est la norme, pas un defaut."""
    t = [1_600_000_000 + JOUR * i for i in range(30)]
    assert _defauts(_serie(t), 0, JOUR)["barres_grossieres"] == 0


# ── Spread et barres fabriquees ────────────────────────────────────────────

def test_spread_negatif_est_compte():
    sp = [10.0] * 20
    sp[4] = -1.0
    assert _defauts(_serie(_reguliere(20), spread=sp), 0, H4)["spread_negatif"] == 1


def test_barres_reconstruites_sont_comptees():
    rec = [False] * 20
    rec[:6] = [True] * 6
    assert _defauts(_serie(_reguliere(20), reconstruit=rec), 0, H4)["reconstruites"] == 6
