"""L'unification du lancement ne change aucun verdict, et ferme le contournement.

Trois choses sont prouvees ici :

1. **L'equivalence des appelants de production.** `analyse_entries` et
   `analyse_positions` rendent les memes verdicts, sur la meme entree figee, que
   la tete d'AVANT l'unification (tete `d53b32e`). La reference est commitee dans
   `tests/fixtures/cortex_equivalence_reference.json` et le harnais qui la
   recalcule vit dans `tests/cortex_equivalence.py`.
2. **Le chemin de diagnostic ne contourne plus la politique.** Un bassin en
   quarantaine n'est pas relance par `tools/demander_hermes.py` : il est saute,
   exactement comme sur le chemin de trading.
3. **Il n'existe qu'un proprietaire du lancement.** Aucun module de production
   hors `titanium/cortex_cli.py` n'appelle `subprocess`. C'est la garde
   structurelle : sans elle, la divergence qui a produit un univers faux dans
   `sizing.py` peut revenir par une simple fonction ajoutee ailleurs.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from tests import cortex_equivalence as equiv
from titanium import cortex_cli, hermes_cortex as hc

RACINE = Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_les_appelants_de_production_rendent_les_memes_verdicts(tmp_path, monkeypatch):
    """Le test d'equivalence : memes entrees, memes verdicts qu'avant l'unification."""
    attendu = equiv.empreinte_du_fichier()
    obtenu = equiv.empreinte(tmp_path, monkeypatch)
    assert obtenu == attendu, (
        "les verdicts ou les requetes composees ont change depuis l'unification"
    )
    # Un temoin que la mesure n'est pas vide : une empreinte qui aurait perdu ses
    # entrees serait « egale » a une reference vide.
    assert [v["action"] for v in obtenu["entrees"]] == ["WAIT", "WAIT", "ALLOW"]
    # Les deux revues partent en lots distincts (`HERMES_LOT_MAX` vaut 1), donc
    # chacune arrive au rang 0 : le faux CLI rend CALM deux fois. Ecrire FEAR ici
    # aurait ete une attente imaginee — le genre d'assertion qui ne mesure rien.
    assert [v["state"] for v in obtenu["positions"]] == ["CALM", "CALM"]
    assert [v["request_ref"] for v in obtenu["positions"]] == [
        "revue-BTCUSD-1", "revue-ETHUSD-1",
    ]
    assert all(v["confidence"] == 0.8 for v in obtenu["positions"])
    assert len(obtenu["appels_entrees"]) == 3
    assert len(obtenu["appels_positions"]) == 2


@pytest.mark.unit
def test_le_chemin_de_diagnostic_ne_relance_pas_un_bassin_en_quarantaine(
    tmp_path, monkeypatch
):
    """Le defaut nomme par l'audit : le diagnostic s'adressait a `_ask` et relancait.

    Le bassin principal est mis en quarantaine AVANT l'appel. Sur l'ancien
    chemin, `demander()` appelait `_ask` directement : le CLI etait relance, ce
    qui consommait un sous-processus et ne laissait aucune trace. Ici, le
    lancement doit etre compte.
    """
    script = tmp_path / "faux_cli_diagnostic.py"
    script.write_text(equiv.FAUX_CLI, encoding="utf-8")
    journal = tmp_path / "diagnostic.ndjson"
    equiv.injecter(monkeypatch, script)
    monkeypatch.setenv("CORTEX_EQUIV_JOURNAL", str(journal))
    monkeypatch.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)
    monkeypatch.setattr(hc, "HERMES_RETENTATIVES", 1)
    monkeypatch.setattr(hc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    monkeypatch.delenv("TITANIUM_HERMES_PROVIDERS", raising=False)
    hc._reset_circuits()
    hc._trip("HTTP 402: credit balance is too low", provider="claude-cli")

    from tools import demander_hermes

    monkeypatch.setattr(demander_hermes, "dossier_du_symbole",
                        lambda _symbole: {"symbole": "BTCUSD"})
    resultat = demander_hermes.demander("BTCUSD", "pourquoi ?")

    assert resultat["ok"] is False
    assert "claude-cli" in resultat["erreur"]
    # La preuve : aucun sous-processus n'a ete lance pour un bassin deja a sec.
    assert not journal.exists() or not journal.read_text(encoding="utf-8").strip()


@pytest.mark.unit
def test_le_diagnostic_ne_choisit_pas_son_propre_chemin():
    """Garde de lecture : l'outil passe par la politique, il ne l'evite pas."""
    source = (RACINE / "tools" / "demander_hermes.py").read_text(encoding="utf-8")
    assert "interroger_bassins" in source
    assert "_ask(" not in source
    assert "import _ask" not in source


#: Les modules qui TOUCHENT au cortex : par leur nom, ou parce qu'ils importent
#: le lanceur. La garde ne juge pas le reste du depot, ou deux sous-processus
#: legitimes vivent deja — `metatester` lance le testeur MT5, `command_center`
#: interroge WMI. Les confondre avec un second lanceur de bassin rendrait la
#: garde fausse, donc inutile.
MODULES_HORS_CORTEX = ("titanium/cortex_cli.py",)
CHEMIN_DIAGNOSTIC = "tools/demander_hermes.py"


def _modules_du_cortex() -> list[tuple[str, str]]:
    trouves: list[tuple[str, str]] = []
    for chemin in sorted((RACINE / "titanium").rglob("*.py")):
        texte = chemin.read_text(encoding="utf-8")
        if "cortex" in chemin.name or "cortex_cli" in texte:
            trouves.append((chemin.relative_to(RACINE).as_posix(), texte))
    diagnostic = RACINE / CHEMIN_DIAGNOSTIC
    trouves.append((CHEMIN_DIAGNOSTIC, diagnostic.read_text(encoding="utf-8")))
    return trouves


@pytest.mark.unit
def test_un_seul_proprietaire_lance_un_sous_processus():
    """Sans cette garde, une seconde copie du lancement peut rentrer sans bruit."""
    fautifs = []
    for relatif, texte in _modules_du_cortex():
        if relatif in MODULES_HORS_CORTEX:
            continue
        arbre = ast.parse(texte)
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Attribute) or noeud.attr not in {"run", "Popen", "call", "check_output"}:
                continue
            valeur = noeud.value
            if isinstance(valeur, ast.Name) and valeur.id == "subprocess":
                fautifs.append(f"{relatif}:{noeud.lineno}")
    assert fautifs == [], (
        "un second proprietaire lance un sous-processus : " + ", ".join(fautifs)
    )


@pytest.mark.unit
def test_la_charge_du_faux_cli_est_bien_lue():
    """La sonde se prouve elle-meme : le faux CLI voit ce qu'il pretend voir."""
    journal = []
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as actif:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="equiv-sonde-") as dossier:
            actif.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)
            actif.setattr(hc, "HERMES_RETENTATIVES", 1)
            actif.setattr(hc.time, "sleep", lambda _s: None)
            actif.setattr(hc, "HERMES_PROVIDER", "claude-cli")
            actif.setattr(hc, "_evidence_by_symbol", lambda symboles: dict(equiv.FAITS))
            actif.delenv("TITANIUM_HERMES_PROVIDERS", raising=False)
            obtenu = equiv.empreinte(Path(dossier), actif)
            journal = obtenu["appels_entrees"]
    assert [appel["refs"] for appel in journal] == [
        ["entree-BTCUSD-1"], ["entree-BTCUSD-2"], ["entree-ETHUSD-1"],
    ]
    assert {appel["provider"] for appel in journal} == {"claude-cli"}
    assert all(len(appel["sha256_prompt"]) == 64 for appel in journal)


@pytest.mark.unit
def test_le_faux_cli_refuse_au_dela_de_deux_candidats(monkeypatch):
    """Le mecanisme de scission que la reference exerce doit rester exercable."""
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory(prefix="equiv-refus-") as dossier:
        script = Path(dossier) / "faux.py"
        script.write_text(equiv.FAUX_CLI, encoding="utf-8")
        charge = json.dumps({"candidates": [
            {"decision_ref": f"r{n}"} for n in range(3)
        ]})
        termine = subprocess.run(
            [sys.executable, str(script), "-z", "entete\n" + charge],
            capture_output=True, text=True, check=False,
        )
    assert termine.returncode == 1
    assert "HTTP 400" in termine.stdout


@pytest.mark.unit
def test_le_proprietaire_declare_ses_bassins(monkeypatch):
    """Le contrat du proprietaire unique : un bassin se DECLARE, il ne se bricole pas."""
    assert set(cortex_cli.REGLES) >= {"claude-cli", "codex-cli"}
    monkeypatch.setattr(cortex_cli.shutil, "which", lambda _nom: None)
    with pytest.raises(cortex_cli.BassinIntrouvable):
        cortex_cli.executable("bassin-jamais-declare")


@pytest.mark.unit
def test_un_bassin_non_declare_est_une_erreur_nommee(monkeypatch):
    """Un nom inconnu echoue en le disant, au lieu de lancer un autre CLI."""
    monkeypatch.setattr(hc, "_CIRCUITS", {})
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-lci")  # faute de frappe
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("peu importe")
    message = str(leve.value)
    assert "claude-lci" in message, "l'erreur doit nommer le bassin fautif"
    assert "inconnu" in message, "et dire qu'il n'est pas declare"


@pytest.mark.unit
def test_le_chemin_de_diagnostic_est_le_meme_que_la_production(monkeypatch):
    """`demander` et `analyse_entries` traversent la meme porte."""
    appels = []

    def _faux(prompt, **kwargs):
        appels.append(kwargs)
        return {"verdicts": []}

    monkeypatch.setattr(hc, "interroger_bassins", _faux)
    from tools import demander_hermes

    monkeypatch.setattr(demander_hermes, "dossier_du_symbole",
                        lambda _symbole: {"symbole": "BTCUSD"})
    demander_hermes.demander("BTCUSD", "pourquoi ?")
    assert len(appels) == 1, "le diagnostic doit passer par la porte unique"
    # `scindable` vaut False par defaut dans `interroger_bassins` : le diagnostic
    # ne scinde pas, mais il ne contourne plus la politique.
    assert appels[0].get("scindable", False) is False


@pytest.mark.unit
def test_la_reference_couvre_les_quatre_plans_compares():
    """La reference doit couvrir les verdicts ET les requetes, des deux appelants."""
    charge = json.loads(equiv.FIXTURE.read_text(encoding="utf-8"))
    assert set(charge) == {"entrees", "appels_entrees", "positions", "appels_positions"}
    assert charge["entrees"] and charge["positions"]
    assert charge["appels_entrees"] and charge["appels_positions"]
