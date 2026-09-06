from __future__ import annotations

from types import SimpleNamespace

from tools import arreter_services, etat_services


def test_state_and_stop_scripts_use_replace_decode_for_process_scans(monkeypatch):
    calls: list[dict] = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(etat_services.subprocess, "run", fake_run)
    assert etat_services._processus() == []
    assert calls[-1]["encoding"] == "utf-8"
    assert calls[-1]["errors"] == "replace"

    monkeypatch.setattr(arreter_services.subprocess, "run", fake_run)
    assert arreter_services._scanner() == []
    assert calls[-1]["encoding"] == "utf-8"
    assert calls[-1]["errors"] == "replace"
