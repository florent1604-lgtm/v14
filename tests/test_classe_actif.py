"""La classe d'actif est l'unite de promotion : elle ne doit jamais etre devinee.

Deux defauts constates le 12/08/2026, tous deux silencieux :

1. ``asset_class_of`` lisait le catalogue via un module MetaTrader5 simplement
   importe. Hors session initialisee, ``symbols_get()`` renvoie ``None`` et la
   classe devient "" -- donc jamais promouvable. Invisible depuis la boucle de
   trading, qui initialise MT5 pour d'autres raisons ; 66 % des candidats
   ressortaient « inconnue » dans un outil d'analyse lance separement.
2. Seule la racine du chemin MT5 etait lue. ``COPPER.fs`` tombait dans
   « indices », c'est-a-dire hors de la seule classe -- les metaux -- ou un
   avantage directionnel a ete mesure.
"""

from __future__ import annotations

import titanium.edge as edge
from titanium.edge import asset_class_of, classe_depuis_groupe


def test_le_petrole_au_comptant_n_est_pas_un_indice():
    assert classe_depuis_groupe("ROW_CASH\\CASH_OIL\\UKOIL", "UKOIL") == "energie"
    assert classe_depuis_groupe(
        "ROW_CASH\\CASH_INDICES_2\\US30", "US30") == "indices"


def test_les_futures_sont_tranches_par_sous_groupe_puis_par_nom():
    assert classe_depuis_groupe(
        "ROW_FUTURES\\FUT_INDICES2\\NAS100.fs", "NAS100.fs") == "indices"
    assert classe_depuis_groupe(
        "ROW_FUTURES\\FUT_COMMODITY1\\BRENT.fs", "BRENT.fs") == "energie"
    assert classe_depuis_groupe(
        "ROW_FUTURES\\FUT_COMMODITY2\\COPPER.fs", "COPPER.fs") == "metaux"
    assert classe_depuis_groupe(
        "ROW_FUTURES\\FUT_COMMODITY2\\COCOA.fs", "COCOA.fs") == "agricole"


def test_un_chemin_inconnu_reste_vide_plutot_que_devine():
    assert classe_depuis_groupe("ROW_MYSTERE\\X\\Y", "Y") == ""
    assert classe_depuis_groupe("", "Y") == ""


def test_le_catalogue_est_lu_dans_une_session_initialisee(monkeypatch):
    """Sans session, le terminal renvoie None : la classe serait "" en silence."""
    from contextlib import contextmanager

    class Terminal:
        def __init__(self, initialise):
            self.initialise = initialise

        def symbols_get(self):
            if not self.initialise:
                return None
            return [type("S", (), {"name": "GBPCAD", "path": "ROW_STANDARD_FX\\GBPCAD"})()]

    appels = {"sessions": 0}

    @contextmanager
    def session():
        appels["sessions"] += 1
        yield Terminal(initialise=True)

    monkeypatch.setattr(edge, "_CACHE_GROUPES", {}, raising=False)
    import titanium.data.mt5_vendor as vendor
    monkeypatch.setattr(vendor, "mt5_session", session)

    assert asset_class_of("GBPCAD") == "fx"
    assert appels["sessions"] == 1
    # Le catalogue est immuable : une seule lecture, puis le cache.
    assert asset_class_of("GBPCAD") == "fx"
    assert appels["sessions"] == 1


def test_la_liste_explicite_garde_la_main_hors_ligne(monkeypatch):
    monkeypatch.setattr(edge, "_CACHE_GROUPES", {}, raising=False)
    assert asset_class_of("EURUSD") == "fx"
    assert asset_class_of("XAUUSD") == "metaux"
