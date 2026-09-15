from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import titanium.hermes_cortex as cortex


def test_ask_serializes_provider_calls(monkeypatch):
    first_entered = Event()
    release_first = Event()
    second_started = Event()
    second_entered = Event()

    def run(command, **_kwargs):
        if command[command.index("-z") + 1] == "first":
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return SimpleNamespace(returncode=0, stdout='{"verdicts": []}', stderr="")

    def second():
        second_started.set()
        return cortex._ask("second")

    monkeypatch.setattr(cortex, "_hermes_executable", lambda: cortex.Path("hermes.exe"))
    monkeypatch.setattr(cortex, "HERMES_PROVIDER", "anthropic")
    monkeypatch.setattr(cortex, "HERMES_INTERVALLE_MIN_S", 0)
    monkeypatch.setattr(cortex.subprocess, "run", run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cortex._ask, "first")
        try:
            assert first_entered.wait(2)
            other = pool.submit(second)
            assert second_started.wait(2)
            assert not second_entered.wait(0.1)
        finally:
            release_first.set()
        assert first.result(timeout=2) == {"verdicts": []}
        assert other.result(timeout=2) == {"verdicts": []}
