"""Shared pytest fixtures that prevent CI hangs when API keys are absent."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Repli `xxhash`, uniquement si le paquet natif est inutilisable.
#
# Smart App Control (etat 1, enforcement) refuse de charger `_xxhash...pyd`
# (non signe, sans reputation ISG) et rendait 30 fichiers de tests
# incollectables via la chaine tradingagents -> langchain -> langsmith.
# Verifie le 08/09/2026 : la version 4.0.1 est bloquee de la meme facon, ce
# n'est pas une question de version.
#
# Le repli de `tests/compat/` n'est place devant `site-packages` QUE si le vrai
# paquet echoue : sur une machine saine, rien ne change.
#
# Depuis le 08/09/2026 ce repli calcule le VRAI XXH3-128, verifie contre les
# vecteurs officiels de xxHash v0.8.2 (cf. tests/test_xxh3_pur_python.py).
# Les empreintes sont identiques a celles du binaire natif, donc les
# identifiants LangGraph/LangSmith restent compatibles et la suite n'est plus
# degradee. Seule la vitesse change, sur des identifiants courts hors chemin
# critique.
XXHASH_TEST_FALLBACK = False
try:  # pragma: no cover - depend de la machine, pas du code
    import xxhash  # noqa: F401
except (ImportError, OSError):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "compat"))
    XXHASH_TEST_FALLBACK = True


def pytest_terminal_summary(terminalreporter):
    if XXHASH_TEST_FALLBACK:
        terminalreporter.write_line(
            "xxHash backend: pure-Python XXH3-128 (native module blocked by "
            "Smart App Control). Digests are exact; verified against official "
            "xxHash v0.8.2 vectors."
        )
    else:
        terminalreporter.write_line("xxHash backend: native.")


def pytest_configure(config):
    for marker in ("unit", "integration", "smoke"):
        config.addinivalue_line("markers", f"{marker}: {marker}-level tests")


_API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
    "XAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "DASHSCOPE_API_KEY",
    "DASHSCOPE_CN_API_KEY",
    "ZHIPU_API_KEY",
    "ZHIPU_CN_API_KEY",
    "MINIMAX_API_KEY",
    "MINIMAX_CN_API_KEY",
    "OPENROUTER_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "ALPHA_VANTAGE_API_KEY",
)


@pytest.fixture(autouse=True)
def _dummy_api_keys(monkeypatch):
    for env_var in _API_KEY_ENV_VARS:
        # `or` not a .get default: an env var present but empty (e.g. a key left
        # blank in a .env copied from .env.example) must still get the placeholder.
        monkeypatch.setenv(env_var, os.environ.get(env_var) or "placeholder")


@pytest.fixture(autouse=True)
def _reset_hermes_throttle():
    """Neutralise l'espacement de débit d'Hermès pendant les tests.

    `HERMES_INTERVALLE_MIN_S` protège la fenêtre d'usage de l'abonnement en
    production. Appliqué aux tests, il ajoutait 30 s à chaque appel après le
    premier du processus — 121 s pour cinq fichiers, sans rien vérifier de
    plus. Le compteur est remis à zéro avant chaque test : le premier appel
    n'attend jamais, et le débit reste borné là où il compte, en vol.
    """
    # Seulement si le module est deja charge : ne pas l'importer ici evite
    # d'imposer ses dependances aux 2 700 tests qui ne le touchent pas, et
    # qu'une panne d'import du cortex fasse tomber toute la suite.
    def _remise_a_zero():
        module = sys.modules.get("titanium.hermes_cortex")
        if module is not None:
            module._DERNIER_APPEL["at"] = 0.0
            module._CIRCUIT.update(retry_at=0.0, error="")

    _remise_a_zero()
    yield
    _remise_a_zero()


@pytest.fixture(autouse=True)
def _isolate_config():
    """Reset the global dataflows config before and after each test.

    ``set_config`` merges (it never clears keys absent from the override), so a
    test that sets e.g. ``tool_vendors`` would otherwise leak into later tests
    and make routing behavior order-dependent. Replace the global outright so
    every test starts from a clean DEFAULT_CONFIG.
    """
    import copy

    import tradingagents.dataflows.config as config_module
    import tradingagents.default_config as default_config

    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)
    yield
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


@pytest.fixture()
def mock_llm_client():
    client = MagicMock()
    client.get_llm.return_value = MagicMock()
    with patch(
        "tradingagents.llm_clients.factory.create_llm_client",
        return_value=client,
    ):
        yield client
