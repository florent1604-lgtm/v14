"""Le cortex appelle Hermes sous ABONNEMENT, jamais sous cle API.

Regression du 08/09/2026. Le CLI Hermes, s'il trouve `ANTHROPIC_API_KEY` dans
son environnement, abandonne l'abonnement Claude Pro/Max et facture une cle API
au solde vide ; le fournisseur repond « HTTP 400: credit balance is too low ».
`PRIME_V14.bat` exporte cette cle depuis `.env`, et `cmd /k` la propageait au
worker — d'ou un cortex muet, par intermittence, selon le shell de depart.

Ces tests n'emploient que des valeurs factices : aucun secret reel n'est lu.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from titanium import hermes_cortex as hc

FACTICE = "sk-ant-valeur-factice-de-test"
PURGEES = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")


@pytest.mark.unit
@pytest.mark.parametrize("cle", PURGEES)
def test_les_identifiants_api_sont_retires(monkeypatch, cle):
    monkeypatch.setenv(cle, FACTICE)
    assert cle not in hc._env_abonnement()


@pytest.mark.unit
def test_le_reste_de_l_environnement_est_preserve(monkeypatch):
    """Purge chirurgicale : le CLI a besoin de PATH, HOME et consorts."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    monkeypatch.setenv("TITANIUM_MARQUEUR_TEST", "conserve")
    env = hc._env_abonnement()
    assert env["TITANIUM_MARQUEUR_TEST"] == "conserve"
    assert "PATH" in env or "Path" in env


@pytest.mark.unit
def test_environnement_deja_propre_reste_inchange(monkeypatch):
    for cle in PURGEES:
        monkeypatch.delenv(cle, raising=False)
    assert not set(PURGEES) & set(hc._env_abonnement())


@pytest.mark.unit
def test_ask_lance_le_cli_sans_cle_api(monkeypatch, tmp_path):
    """Le contrat qui compte : ce que `_ask` passe REELLEMENT au processus."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    monkeypatch.setattr(hc, "_hermes_executable", lambda: tmp_path / "hermes.exe")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "anthropic")
    monkeypatch.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)
    monkeypatch.setitem(hc._DERNIER_APPEL, "at", 0.0)
    hc._CIRCUIT.update(retry_at=0.0, error="")
    vus = {}

    def faux_run(cmd, **kwargs):
        vus.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, '{"verdicts":[]}', "")

    monkeypatch.setattr(hc.subprocess, "run", faux_run)
    assert hc._ask("peu importe") == {"verdicts": []}
    env = vus["env"]
    assert "ANTHROPIC_API_KEY" not in env
    # Et la valeur factice n'a fui par aucune autre variable.
    assert FACTICE not in "".join(env.values())


@pytest.mark.unit
@pytest.mark.skipif(
    sys.platform != "win32",
    reason="comportement Windows : le candidat LOCALAPPDATA et le prefixe "
    "`-c` sont conditionnes a `os.name == 'nt'` dans `hermes_cortex`",
)
def test_windows_uses_python_entrypoint_instead_of_blocked_shim(monkeypatch, tmp_path):
    scripts = tmp_path / "hermes" / "hermes-agent" / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("HERMES_CORTEX_EXECUTABLE", raising=False)
    monkeypatch.setattr(hc.shutil, "which", lambda name: None)
    command = hc._hermes_command_prefix()
    assert Path(command[0]) == python
    assert command[1:] == ["-c", "from hermes_cli.main import main; main()"]


@pytest.mark.unit
def test_aucun_secret_n_est_journalise(monkeypatch):
    """`_safe_cli_error` ne doit jamais recopier une cle dans un message."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    detail = hc._safe_cli_error(f"HTTP 401 avec {FACTICE}", "")
    assert FACTICE not in detail
