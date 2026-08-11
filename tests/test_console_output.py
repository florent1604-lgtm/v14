"""Non-regression du demarrage des services V14 sous Windows CP1252."""

from __future__ import annotations

import io
import os
from pathlib import Path

from tools.console_output import configure_console_output


ROOT = Path(__file__).resolve().parent.parent


def _cp1252_strict() -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", errors="strict")


def test_service_output_survit_aux_glyphes_unicode_windows() -> None:
    raw_out, stdout = _cp1252_strict()
    raw_err, stderr = _cp1252_strict()
    try:
        configure_console_output(stdout=stdout, stderr=stderr)

        print("═ → ✓ démo", file=stdout)
        print("⚠ erreur contrôlée", file=stderr)
        stdout.flush()
        stderr.flush()

        assert stdout.encoding == "utf-8"
        assert stderr.encoding == "utf-8"
        assert stdout.errors == "replace"
        assert stderr.errors == "replace"
        assert "démo" in raw_out.getvalue().decode("utf-8")
        assert "contrôlée" in raw_err.getvalue().decode("utf-8")
    finally:
        stdout.detach()
        stderr.detach()


def test_lanceur_impose_utf8_aux_trois_services() -> None:
    source = (ROOT / "DEMARRER_V14.bat").read_text(encoding="ascii")

    assert 'set "PYTHONUTF8=1"' in source
    assert 'set "PYTHONIOENCODING=utf-8"' in source
    assert source.count("-X utf8 tools\\") == 3


def test_services_activent_leur_filet_console() -> None:
    for relatif in (
        "tools/live_demo.py",
        "tools/analystes.py",
        "tools/dashboard.py",
    ):
        source = (ROOT / relatif).read_text(encoding="utf-8")
        assert "configure_console_output()" in source, relatif


def test_configure_propage_env_aux_enfants(monkeypatch) -> None:
    """setdefault pose PYTHONUTF8 et PYTHONIOENCODING sans ecraser."""
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)

    raw, stream = _cp1252_strict()
    try:
        configure_console_output(stdout=stream, stderr=stream)
        assert os.environ["PYTHONUTF8"] == "1"
        assert os.environ["PYTHONIOENCODING"] == "utf-8"
    finally:
        stream.detach()


def test_configure_ne_ecrase_pas_env_existant(monkeypatch) -> None:
    """Si le lanceur a deja fixe une valeur, setdefault la preserve."""
    monkeypatch.setenv("PYTHONUTF8", "0")
    monkeypatch.setenv("PYTHONIOENCODING", "latin-1")

    raw, stream = _cp1252_strict()
    try:
        configure_console_output(stdout=stream, stderr=stream)
        assert os.environ["PYTHONUTF8"] == "0"
        assert os.environ["PYTHONIOENCODING"] == "latin-1"
    finally:
        stream.detach()
