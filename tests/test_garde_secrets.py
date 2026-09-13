"""Garde de commit sur les accès personnels.

Une garde silencieuse est indistinguable d'une garde absente : ces tests
vérifient d'abord qu'elle **se déclenche**, ensuite qu'elle ne crie pas sur du
code honnête, et enfin qu'elle ne recopie jamais la valeur qu'elle dénonce.
"""

from __future__ import annotations

import pytest

from tools.garde_secrets import chemin_interdit, secrets_dans

# ── Fichiers d'accès : refusés sur leur nom, jamais ouverts ────────────────

@pytest.mark.parametrize("chemin", [
    ".env",
    ".env.local",
    ".env.production",
    "config/.env",
    "secrets/credentials.json",
    "deploy/auth.json",
    "cle.pem",
    "certificat.p12",
    "serveur.key",
    ".netrc",
    "id_rsa",
    "titanium/.ENV",                      # la casse ne doit pas sauver
    r"tools\config\.env",                 # séparateur Windows
])
def test_les_fichiers_d_acces_sont_refuses(chemin):
    assert chemin_interdit(chemin) is True


@pytest.mark.parametrize("chemin", [
    "titanium/execution/mt5_executor.py",
    "docs/ENVIRONNEMENT.md",
    "tools/environnement.py",
    "tests/test_env_config.py",
    "keyboard.py",                        # finit par « key » sans être une clé
    "monkey.py",
])
def test_le_code_honnete_passe(chemin):
    assert chemin_interdit(chemin) is False


# ── Le gabarit versionné n'est pas un fichier d'accès ─────────────────────

@pytest.mark.parametrize("chemin", [
    ".env.example",
    ".ENV.Example",                       # la casse ne doit pas le bannir
    "config/.env.example",
    r"deploy\config\.env.example",        # séparateur Windows
    ".env.sample",
    ".env.template",
])
def test_le_gabarit_versionne_passe(chemin):
    """`.env.example` est SUIVI et ne porte aucune valeur.

    Le prefixe `.env.` le refusait, donc le modele ne pouvait plus jamais etre
    complete — la garde bloquait le flux de travail que `AGENTS.md` prescrit
    (« mettre a jour `.env.example` »). Un modele committable est la seule
    facon de documenter une variable sans jamais versionner sa valeur.
    """
    assert chemin_interdit(chemin) is False


@pytest.mark.parametrize("chemin", [
    ".env.local",
    ".env.production",
    ".env.example.local",                 # un VRAI fichier derriere un nom de modele
    ".env.example.backup",
])
def test_un_vrai_fichier_d_acces_reste_refuse(chemin):
    """L'exception ne doit pas devenir une porte : elle nomme des modeles."""
    assert chemin_interdit(chemin) is True


# ── Valeurs de secrets ────────────────────────────────────────────────────

@pytest.mark.parametrize("echantillon", [
    "ANTHROPIC_API_KEY=sk-ant-api03-" + "A" * 80,
    "clef = 'AIza" + "B" * 35 + "'",
    "token: ghp_" + "C" * 36,
    "aws = AKIA" + "D" * 16,
    "slack = xoxb-" + "E" * 24,
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_les_secrets_sont_detectes(echantillon):
    assert secrets_dans(echantillon)


@pytest.mark.parametrize("echantillon", [
    'api_key = os.environ["ANTHROPIC_API_KEY"]',
    "password = getpass()",
    "# le token est lu depuis .env, jamais écrit ici",
    "TITANIUM_ALLOW_REAL_ACCOUNT=I_UNDERSTAND_THIS_IS_REAL_MONEY",
    "sk-court",
    "def token(self): return self._token",
])
def test_aucun_cri_sur_du_code_honnete(echantillon):
    """Une garde qui crie sur `api_key = os.environ[...]` finit désarmée.

    La ligne du compte réel est incluse à dessein : elle figure en prose dans
    CLAUDE.md et comme constante de comparaison dans `mt5_executor`. La
    bannir ici rendrait la documentation du mur incommittable.
    """
    assert secrets_dans(echantillon) == []


def test_la_position_est_rendue_sans_la_valeur():
    """Le contrat qui rend la garde publiable : elle ne recopie pas la clé."""
    texte = "ligne une\nligne deux\nclef = 'sk-ant-api03-" + "Z" * 80 + "'\n"
    trouves = secrets_dans(texte)
    assert len(trouves) == 1
    ligne, longueur = trouves[0]
    assert ligne == 3
    assert longueur > 80
    # Le retour est fait d'entiers : aucun fragment de secret ne peut en sortir.
    assert all(isinstance(v, int) for v in (ligne, longueur))


def test_plusieurs_secrets_sur_plusieurs_lignes():
    texte = "a = 'AIza" + "B" * 35 + "'\nb = 2\nc = 'ghp_" + "C" * 36 + "'\n"
    assert [ligne for ligne, _ in secrets_dans(texte)] == [1, 3]


def test_un_texte_vide_ne_casse_rien():
    assert secrets_dans("") == []
