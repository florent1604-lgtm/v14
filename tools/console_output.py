"""Sorties console robustes sur Windows, y compris hors console interactive.

Un processus Python lance avec ``Start-Process`` et des sorties redirigees peut
heriter de CP1252. Les glyphes utilises par les services V14 (cadres, fleches,
accents) levaient alors ``UnicodeEncodeError`` avant meme le premier tour.

Le lanceur officiel fixe aussi l'environnement UTF-8, mais cette fonction est
la protection locale : elle couvre les lancements directs, les taches Windows
et les superviseurs externes.
"""

from __future__ import annotations

import sys
from typing import TextIO


def _configure_stream(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # Flux capture, ferme ou non reconfigurable : l'affichage ne doit
        # jamais empecher le service de demarrer.
        pass


def configure_console_output(*, stdout: TextIO | None = None,
                             stderr: TextIO | None = None) -> None:
    """Force UTF-8 et une degradation sans crash sur stdout et stderr.

    Propage aussi PYTHONUTF8 et PYTHONIOENCODING aux processus enfants
    via ``os.environ.setdefault`` pour que les sous-processus heritent
    du meme encodage sans ecraser un reglage explicite du lanceur.
    """
    import os
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    _configure_stream(sys.stdout if stdout is None else stdout)
    _configure_stream(sys.stderr if stderr is None else stderr)
