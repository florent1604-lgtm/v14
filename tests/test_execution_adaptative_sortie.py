"""Le harnais d'arene ne peut pas detruire sa propre reference.

`results/execution_adaptative/execution_adaptative.ndjson` — 30 Mo, non
versionnes, `sha256 18b2740f…` — est l'artefact sur lequel tout le dossier
repose. Le defaut precedent y ecrivait : une passe de tension lancee sans
`--output` remplacait la mesure, et les comparaisons suivantes l'auraient
comparee a elle-meme.

Le comportement EXPLICITE n'a pas bouge : `--output` ecrit exactement ou on le
demande. C'est l'ecriture IMPLICITE qui change de cible, et qui ne peut donc
plus rien detruire.

Sans MT5 : les passes sont `--quick`, et `ROOT` est detourne vers un dossier
temporaire, donc ces tests ne peuvent rien casser meme quand la garde manque.
"""

from __future__ import annotations

from pathlib import Path

from tools import execution_adaptative as harnais

REFERENCE = ("results", "execution_adaptative", "execution_adaptative.ndjson")


def _config_reelle() -> Path:
    """La config livree, meme quand `ROOT` est detourne."""
    return Path(harnais.__file__).resolve().parent.parent / "config" / "execution_backtest.json"


def test_sans_output_la_passe_n_ecrit_pas_sur_la_reference(tmp_path, monkeypatch):
    """La garde : l'ecriture implicite va dans un dossier date.

    Ce test TOMBE si quelqu'un redonne une valeur par defaut a `--output` : le
    fichier de reference est alors cree sous `ROOT`, ce qui est exactement le
    defaut ferme. Comme `ROOT` pointe sur `tmp_path`, la garde manquante ne
    detruit rien — elle se contente d'echouer ici.
    """
    monkeypatch.setattr(harnais, "ROOT", tmp_path)
    assert harnais.main(["--quick", "--config", str(_config_reelle())]) == 0

    reference = tmp_path.joinpath(*REFERENCE)
    assert not reference.exists(), (
        "une passe sans --output a ecrit sur l'emplacement de l'artefact de reference"
    )
    ecrites = sorted((tmp_path / "results").glob("arene_*/execution_adaptative.ndjson"))
    assert len(ecrites) == 1, "l'ecriture implicite doit atterrir dans un dossier date"


def test_le_harnais_publie_la_posture_qu_il_a_mesuree(tmp_path, monkeypatch):
    """`bloc_posture` et `posture_echelle_s` ont ici leur lecteur.

    Sans ce test, le bloc fabrique par le harnais serait verifie seulement par les
    17 techniques, et l'echelle enregistree a cote du rapport serait un champ que
    personne ne lit : une echelle codee en dur passerait inapercue.
    """
    import json

    import pytest

    from titanium.macro import load_policy
    from titanium.macro.gate import MACRO_POSTURE_KEY

    assert harnais.bloc_posture(0.0, echelle_s=3600.0) is None
    for demande in (0.25, 0.5, 1.0):
        bloc = harnais.bloc_posture(demande, echelle_s=3600.0)
        assert bloc is not None
        assert bloc[MACRO_POSTURE_KEY] == pytest.approx(demande, abs=1e-6)

    monkeypatch.setattr(harnais, "ROOT", tmp_path)
    dossier = tmp_path / "mesure"
    assert harnais.main(
        ["--quick", "--config", str(_config_reelle()), "--output", str(dossier)]
    ) == 0
    rapport = json.loads((dossier / "execution_adaptative.json").read_text(encoding="utf-8"))
    assert rapport["posture_echelle_s"] == load_policy().elevated_within_s


def test_le_chemin_explicite_ecrit_toujours_ou_on_le_demande(tmp_path, monkeypatch):
    """Le comportement d'aujourd'hui, inchange : `--output` est la cible."""
    monkeypatch.setattr(harnais, "ROOT", tmp_path)
    dossier = tmp_path / "ailleurs"
    assert harnais.main(
        ["--quick", "--config", str(_config_reelle()), "--output", str(dossier)]
    ) == 0
    assert (dossier / "execution_adaptative.ndjson").is_file()
    assert (dossier / "execution_adaptative.json").is_file()
    assert (dossier / "execution_adaptative.md").is_file()
    assert not (tmp_path / "results").exists(), "aucune ecriture implicite en plus"
