"""Garde-fous de l'archive de quotes L1.

Trois propriétés comptent et sont vérifiées ici :
  1. le module reste en lecture seule — aucun passage d'ordre, jamais ;
  2. l'horodatage archivé est en VRAI UTC, décalage serveur retiré ;
  3. la reprise ne duplique pas et ne saute pas.

La deuxième est celle qui a déjà coûté un rejeu complet le 12/08/2026 : des
barres en heure serveur étiquetées UTC décalaient toutes les fenêtres de trois
heures, et 12 trades sur 37 disparaissaient faute d'intersection.
"""

from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import enregistreur_quotes as eq

INTERDITS = frozenset({
    "order_send", "order_check", "order_calc_margin",
    "positions_get", "positions_total", "position_modify",
})


def test_le_module_ne_passe_aucun_ordre():
    """Ce que la docstring du module promet doit être vrai structurellement.

    On analyse l'AST et non le texte : la docstring *cite* l'interdit pour
    l'expliquer, et un grep naïf s'y prendrait les pieds.
    """
    arbre = ast.parse(Path(eq.__file__).read_text(encoding="utf-8"))
    utilises = (
        {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        | {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
    )
    fautifs = sorted(utilises & INTERDITS)
    assert not fautifs, f"appels interdits dans un module lecture seule : {fautifs}"


# ─────────────────────────────────────────── horloge

class _TickFactice(dict):
    """Un tick MT5 se lit par clés, comme un enregistrement numpy."""


def _faux_mt5(ticks):
    class M:
        COPY_TICKS_INFO = 1

        @staticmethod
        def copy_ticks_range(symbole, debut, fin, flags):
            return ticks

    return M()


@pytest.fixture()
def archive(tmp_path, monkeypatch):
    monkeypatch.setattr(eq, "DOSSIER", tmp_path)
    return tmp_path


def _brancher(monkeypatch, ticks):
    from contextlib import contextmanager

    @contextmanager
    def session():
        yield _faux_mt5(ticks)

    monkeypatch.setattr(eq, "mt5_session", session)


def test_le_decalage_serveur_est_retire_de_lhorodatage(archive, monkeypatch):
    """Un tick à 12:00:00 heure serveur avec GMT+3 doit s'archiver à 09:00:00Z.

    C'est l'invariant central : archiver l'heure serveur en la nommant UTC
    produirait un fichier qui ment sans jamais lever d'erreur.
    """
    serveur = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    _brancher(monkeypatch, [_TickFactice(
        time_msc=serveur * 1000.0, bid=1.0, ask=1.2,
        last=float("nan"), volume=0, volume_real=0.0, flags=2,
    )])

    n = eq.collecter("EURUSD", decalage_s=10800)  # Axi = GMT+3

    assert n == 1
    ligne = json.loads(next(archive.rglob("*.ndjson")).read_text(encoding="utf-8").strip())
    archive_utc = datetime.fromtimestamp(ligne["ts_ms"] / 1000.0, tz=timezone.utc)
    assert archive_utc.hour == 9, "le decalage serveur n a pas ete retire"
    assert ligne["horloge"] == "utc"
    assert ligne["decalage_serveur_s"] == 10800


def test_le_marqueur_dhorloge_est_present_sur_chaque_ligne(archive, monkeypatch):
    """Sans marqueur, un consommateur ne peut pas distinguer UTC de serveur.

    Le rejeu de breakeven refuse déjà les lignes sans marqueur ; l'archive doit
    donc le porter partout, pas seulement sur la première ligne.
    """
    base = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    _brancher(monkeypatch, [
        _TickFactice(time_msc=(base + i) * 1000.0, bid=1.0, ask=1.2,
                     last=float("nan"), volume=0, volume_real=0.0, flags=2)
        for i in range(5)
    ])

    eq.collecter("EURUSD", decalage_s=10800)

    lignes = next(archive.rglob("*.ndjson")).read_text(encoding="utf-8").splitlines()
    assert len(lignes) == 5
    assert all(json.loads(x)["horloge"] == "utc" for x in lignes)


# ─────────────────────────────────────────── reprise

def test_la_reprise_ne_duplique_pas(archive, monkeypatch):
    """Rejouer la même fenêtre ne doit rien réécrire.

    Le recouvrement d'une seconde existe pour ne pas perdre un tick arrivé en
    retard ; il ne doit pas se payer en doublons, sinon toute statistique de
    volume ou de spread est fausse.
    """
    base = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    ticks = [
        _TickFactice(time_msc=(base + i) * 1000.0, bid=1.0, ask=1.2,
                     last=float("nan"), volume=0, volume_real=0.0, flags=2)
        for i in range(3)
    ]
    _brancher(monkeypatch, ticks)

    assert eq.collecter("EURUSD", 10800) == 3
    assert eq.collecter("EURUSD", 10800) == 0, "la seconde passe a duplique"

    lignes = next(archive.rglob("*.ndjson")).read_text(encoding="utf-8").splitlines()
    horodatages = [json.loads(x)["ts_ms"] for x in lignes]
    assert len(horodatages) == len(set(horodatages)) == 3


def test_un_tick_neuf_est_bien_ajoute_apres_reprise(archive, monkeypatch):
    """La déduplication ne doit pas devenir un filtre qui bloque tout.

    Un test qui ne vérifie que l'absence de doublons passerait aussi si la
    collecte n'écrivait plus jamais rien.
    """
    base = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    _brancher(monkeypatch, [_TickFactice(
        time_msc=base * 1000.0, bid=1.0, ask=1.2,
        last=float("nan"), volume=0, volume_real=0.0, flags=2)])
    assert eq.collecter("EURUSD", 10800) == 1

    _brancher(monkeypatch, [_TickFactice(
        time_msc=(base + 10) * 1000.0, bid=1.1, ask=1.3,
        last=float("nan"), volume=0, volume_real=0.0, flags=2)])
    assert eq.collecter("EURUSD", 10800) == 1


def test_un_tick_sans_les_deux_cotes_est_ecarte(archive, monkeypatch):
    """Un tick à bid ou ask nul n'est pas une quote L1 exploitable.

    L'archiver donnerait un spread aberrant, et c'est précisément le spread
    que les politiques d'exécution passives cherchent à capter.
    """
    base = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    _brancher(monkeypatch, [
        _TickFactice(time_msc=base * 1000.0, bid=0.0, ask=1.2,
                     last=float("nan"), volume=0, volume_real=0.0, flags=2),
        _TickFactice(time_msc=(base + 1) * 1000.0, bid=1.0, ask=0.0,
                     last=float("nan"), volume=0, volume_real=0.0, flags=2),
        _TickFactice(time_msc=(base + 2) * 1000.0, bid=1.0, ask=1.2,
                     last=float("nan"), volume=0, volume_real=0.0, flags=2),
    ])

    assert eq.collecter("EURUSD", 10800) == 1


def test_une_ligne_tronquee_ne_fait_pas_repartir_de_zero(archive, monkeypatch):
    """Un arrêt brutal laisse une ligne incomplète : elle doit être ignorée,
    pas interprétée, et surtout pas faire perdre l'horodatage de reprise."""
    dossier = archive / "EURUSD"
    dossier.mkdir(parents=True)
    bon = {"symbole": "EURUSD", "ts_ms": 1_000_000.0, "horloge": "utc"}
    (dossier / "2026-08-15.ndjson").write_text(
        json.dumps(bon) + "\n" + '{"symbole": "EURUSD", "ts_m',
        encoding="utf-8")

    assert eq.dernier_horodatage("EURUSD") == 1_000_000.0


def test_aucune_archive_rend_none(archive):
    assert eq.dernier_horodatage("INEXISTANT") is None
