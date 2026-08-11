"""Tests de portabilite de l'interface en ligne de commande V14."""

from __future__ import annotations

import io

import scan_v14
from tools import verifier_accumulation


def test_console_cp1252_ne_plante_pas_sur_les_symboles_unicode(monkeypatch):
    """La console Windows historique doit degrader l'affichage, pas le scan."""
    sortie_brute = io.BytesIO()
    sortie_cp1252 = io.TextIOWrapper(
        sortie_brute,
        encoding="cp1252",
        errors="strict",
    )
    monkeypatch.setattr(scan_v14.sys, "stdout", sortie_cp1252)

    scan_v14._configure_console_output()
    print("✓ ▶ —", file=sortie_cp1252)
    sortie_cp1252.flush()

    assert sortie_cp1252.errors == "replace"


def test_verificateur_cp1252_ne_plante_pas_sur_les_symboles_unicode(monkeypatch):
    sortie_brute = io.BytesIO()
    sortie_cp1252 = io.TextIOWrapper(
        sortie_brute,
        encoding="cp1252",
        errors="strict",
    )
    monkeypatch.setattr(verifier_accumulation.sys, "stdout", sortie_cp1252)

    verifier_accumulation._configure_console_output()
    print("✓ ✗ ⚠ —", file=sortie_cp1252)
    sortie_cp1252.flush()

    assert sortie_cp1252.errors == "replace"
