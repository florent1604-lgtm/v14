"""Provider refusals remain identifiable without exposing raw CLI output."""

from types import SimpleNamespace

import pytest

from titanium import cortex_cli, hermes_cortex as cortex


@pytest.mark.parametrize("code,json_error", [(0, False), (1, False), (0, True)])
def test_refusal_behind_banner_preserves_reason_not_secrets(monkeypatch, code, json_error):
    message = 'HTTP 400: Your credit balance is too low. token=PRIVATE_TEST_SENTINEL'
    if json_error:
        message = '{"type":"error","error":{"message":"' + message + '"}}'
    output = 'banner ' * 100 + message
    monkeypatch.setattr(cortex, "_CIRCUITS", {})
    monkeypatch.setattr(cortex_cli, "executable", lambda _bassin: cortex.Path("hermes.exe"))
    monkeypatch.setattr(cortex, "HERMES_PROVIDER", "claude-cli")
    monkeypatch.setattr(cortex_cli.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=code, stdout=output, stderr=""))
    with pytest.raises(cortex.HermesCortexUnavailable) as exc:
        cortex._ask("diagnostic")
    assert 'HTTP 400' in str(exc.value)
    assert 'credit balance is too low' in str(exc.value)
    assert 'PRIVATE_TEST_SENTINEL' not in str(exc.value)
    assert 'PRIVATE_TEST_SENTINEL' not in cortex.circuit_status()['last_error']
    assert cortex.circuit_status()['retry_in_s'] > cortex.HERMES_BACKOFF_S


def test_unknown_output_does_not_leak(monkeypatch):
    assert cortex._safe_cli_error('password=PRIVATE_TEST_SENTINEL', '') == (
        'HERMES_CLI_ERROR_OR_INVALID_RESPONSE')


def test_stderr_is_classified_without_returning_raw_text():
    assert cortex._safe_cli_error('', 'HTTP 429: rate limit token=PRIVATE_TEST_SENTINEL') == (
        'HTTP 429: provider rate limit 429')
