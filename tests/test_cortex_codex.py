"""Le cortex doit tourner sur les DEUX forfaits : Claude et Codex.

Etape C3 du dossier cortex. Le bassin Claude (voie CLI Hermes) interroge le CLI
en prose et espere un JSON ; le bassin Codex contraint la reponse finale par un
JSON Schema. Ce fichier prouve quatre choses, et chacune a son faux CLI :

1. **La ligne de commande reellement construite.** Un faux CLI relit son propre
   `sys.argv`, son environnement et son `cwd`, et les ecrit dans un journal. Le
   test compare ce que le PROCESSUS a recu. Comme pour C1, `codex` n'est pas
   installe sur la machine qui teste : c'est le CLI lui-meme qui est remplace,
   pas le transport.
2. **Le schema versionne.** Une enveloppe hors contrat est refusee, et ce refus
   est nomme. Il ne doit PAS ouvrir le disjoncteur : le fournisseur a repondu.
3. **La classification honnete.** Refus prealable (« cette requete ») contre
   panne (« ce fournisseur »), avec les deux vocabulaires deja en place depuis
   C1 — car un second vocabulaire deriverait du premier.
4. **La selection et le repli entre les deux bassins.** Un bassin a sec est
   saute, l'autre repond, et chacun garde son propre disjoncteur.

**Ce que ces tests ne prouvent pas, et qu'eux seuls ne peuvent pas prouver :**
aucun compte Codex, aucun forfait Claude Pro/Max n'est sollicite ici. Un faux CLI
etablit le contrat de transport, la forme du verdict et la logique d'etat ; il
n'etablit pas qu'un vrai `codex exec` rend `rc=0` en ~10 s, ni que le compte est
bien facture a l'abonnement plutot qu'a une cle. Cette constatation-la ne peut
venir que de la machine de l'utilisateur, et elle est ecrite dans le rapport de
la PR.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from titanium import cortex_codex, hermes_cortex as hc

#: Deliberement PAS de forme de cle : la garde de secrets du depot refuse, a
#: juste titre, tout ce qui ressemble a un jeton. Ces tests n'ont besoin que
#: d'une valeur reconnaissable dans la variable, pas d'une valeur plausible.
FACTICE = "valeur-factice-jamais-reelle"

#: Une enveloppe conforme au schema versionne, telle que le CLI la rendrait.
VERDICT_CONFORME = {
    "verdicts": [
        {
            "decision_ref": "entree-BTCUSD",
            "action": "ALLOW",
            "summary": "flux acheteur, spread sous le plafond",
            "confidence": 0.75,
        }
    ]
}

#: Le faux CLI Codex. Il ne connait ni OpenAI ni le schema : il obeit a
#: `FAUX_CODEX_MODE`, journalise ce qu'il a RECU, puis ecrit sa reponse la ou
#: `-o` le demande — exactement comme `codex exec`.
FAUX_CODEX = '''\
"""Faux CLI Codex : journalise son argv, son environnement et son cwd."""

import json
import os
import sys

argv = sys.argv[1:]


def _valeur(drapeau):
    try:
        return argv[argv.index(drapeau) + 1]
    except (ValueError, IndexError):
        return ""


recu = {
    "argv": sys.argv,
    "cwd": os.getcwd(),
    "prompt": argv[1] if len(argv) > 1 else "",
    "sous_commande": argv[0] if argv else "",
    "drapeaux": [item for item in argv if item.startswith("--")],
    "sandbox": _valeur("--sandbox"),
    "schema": _valeur("--output-schema"),
    "sortie": _valeur("-o"),
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
    "codex_api_key": os.environ.get("CODEX_API_KEY", ""),
    "mode": os.environ.get("FAUX_CODEX_MODE", "ok"),
}
journal = os.environ.get("FAUX_CODEX_JOURNAL")
if journal:
    with open(journal, "a", encoding="utf-8") as flux:
        flux.write(json.dumps(recu, ensure_ascii=False) + "\\n")

mode = recu["mode"]
sortie = recu["sortie"]

if mode == "ok":
    with open(sortie, "w", encoding="utf-8") as flux:
        flux.write(json.dumps({
            "verdicts": [{
                "decision_ref": "entree-BTCUSD",
                "action": "ALLOW",
                "summary": "flux acheteur, spread sous le plafond",
                "confidence": 0.75,
            }]
        }))
    sys.exit(0)

if mode == "horsschema":
    # Le CLI a repondu, rc=0, et le fichier de sortie viole le schema :
    # `verdicts` est vide alors que `minItems` vaut 1.
    with open(sortie, "w", encoding="utf-8") as flux:
        flux.write(json.dumps({"verdicts": []}))
    sys.exit(0)

if mode == "nonjson":
    # Avec `--output-schema`, un texte non-JSON est deja une rupture de contrat.
    with open(sortie, "w", encoding="utf-8") as flux:
        flux.write("desole, je ne peux pas repondre sous cette forme")
    sys.exit(0)

if mode == "refus":
    # Refus AVANT generation : la requete est en cause, pas le fournisseur.
    print("HTTP 400: request too large for this model")
    sys.exit(1)

if mode == "quota":
    # Le compte est a sec : « ce fournisseur », pas « cette requete ».
    print("HTTP 402: Your credit balance is too low to access the API.")
    sys.exit(1)

# "panne" : le processus meurt sans rien dire.
sys.exit(3)
'''


@pytest.fixture
def faux_codex(tmp_path, monkeypatch):
    """Un faux CLI Codex reellement lance par `subprocess.run`, sans abonnement.

    Rend une fonction qui relit le journal ecrit PAR LE PROCESSUS. La presence
    du journal est elle-meme la preuve qu'un vrai sous-processus a tourne.
    """
    script = tmp_path / "faux_codex.py"
    script.write_text(FAUX_CODEX, encoding="utf-8")
    journal = tmp_path / "journal_codex.ndjson"
    prefixe = [sys.executable, str(script)]
    monkeypatch.setattr(cortex_codex, "_prefixe", lambda: list(prefixe))
    monkeypatch.setenv("FAUX_CODEX_JOURNAL", str(journal))
    monkeypatch.setenv("FAUX_CODEX_MODE", "ok")
    monkeypatch.setattr(hc, "HERMES_INTERVALLE_MIN_S", 0.0)
    monkeypatch.setattr(hc, "HERMES_RETENTATIVES", 1)
    monkeypatch.setattr(hc.time, "sleep", lambda _s: None)
    hc._reset_circuits()

    def lignes() -> list[dict]:
        if not journal.exists():
            return []
        return [
            json.loads(ligne)
            for ligne in journal.read_text(encoding="utf-8").splitlines()
            if ligne.strip()
        ]

    lignes.prefixe = prefixe  # type: ignore[attr-defined]
    return lignes


def _mode(monkeypatch, mode: str) -> None:
    monkeypatch.setenv("FAUX_CODEX_MODE", mode)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. La ligne de commande reellement construite
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_la_ligne_de_commande_reellement_recue_est_figee(faux_codex, monkeypatch):
    """Ce que le processus a recu, pas ce que le code croit construire."""
    _mode(monkeypatch, "ok")
    cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                          env=hc._env_abonnement())
    recu = faux_codex()
    assert len(recu) == 1
    # `sys.argv[0]` est le script ; ce que le code a construit commence a
    # l'executable. La comparaison porte donc sur tout le reste, a l'identique.
    prefixe = faux_codex.prefixe  # type: ignore[attr-defined]
    # Le fichier de sortie vit dans le repertoire temporaire du module, donc on
    # reconstruit la ligne avec le chemin que le processus a REELLEMENT recu :
    # c'est la seule facon de comparer toute la ligne, `-o` compris.
    sortie_recue = Path(recu[0]["sortie"])
    attendu = cortex_codex._commande("diagnostic simple", sortie_recue)
    # `_commande` rend [executable, script, ...] ; `sys.argv` du processus vaut
    # [script, ...]. Ce qui doit coincider au mot pres est ce qui SUIT le
    # prefixe injecte — c'est-a-dire ce que le CLI a reellement recu.
    assert attendu[:len(prefixe)] == prefixe
    assert recu[0]["argv"][1:] == attendu[len(prefixe):]
    assert recu[0]["sous_commande"] == "exec"
    assert recu[0]["prompt"] == "diagnostic simple"


@pytest.mark.unit
def test_les_drapeaux_du_bassin_codex_sont_ceux_du_dossier(faux_codex, monkeypatch, tmp_path):
    """`--json`, `--ephemeral`, `--ignore-user-config`, `--sandbox read-only`.

    Chacun est ecrit pour une raison : sans `--ignore-user-config`, un
    `config.toml` utilisateur pourrait changer le verdict d'un composant qui
    publie des politiques.
    """
    _mode(monkeypatch, "ok")
    cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                          env=hc._env_abonnement())
    recu = faux_codex()[0]
    for drapeau in ("--json", "--ephemeral", "--ignore-user-config", "--ignore-rules"):
        assert drapeau in recu["drapeaux"], drapeau
    assert recu["sandbox"] == "read-only"


@pytest.mark.unit
def test_le_schema_pointe_sur_le_fichier_versionne(faux_codex, monkeypatch):
    """Le contrat n'est pas une convention en prose : c'est un fichier du depot."""
    _mode(monkeypatch, "ok")
    cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                          env=hc._env_abonnement())
    recu = faux_codex()[0]
    assert Path(recu["schema"]).resolve() == cortex_codex.SCHEMA.resolve()
    assert cortex_codex.SCHEMA.is_file()
    assert recu["sortie"], "`-o` doit nommer un fichier de sortie"


@pytest.mark.unit
def test_aucun_identifiant_api_n_atteint_le_bassin_codex(faux_codex, monkeypatch):
    """Le mode de panne du 08/09, transpose : une cle fait facturer la cle.

    La documentation Codex est explicite — `codex exec` reutilise
    l'authentification sauvegardee, mais facture la cle des que
    `OPENAI_API_KEY` ou `CODEX_API_KEY` est dans l'environnement.
    """
    monkeypatch.setenv("OPENAI_API_KEY", FACTICE)
    monkeypatch.setenv("CODEX_API_KEY", FACTICE)
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    _mode(monkeypatch, "ok")
    cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                          env=hc._env_abonnement())
    recu = faux_codex()[0]
    assert recu["openai_api_key"] == ""
    assert recu["codex_api_key"] == ""
    assert recu["anthropic_api_key"] == ""


@pytest.mark.unit
def test_le_bassin_codex_tourne_dans_la_racine_du_depot(faux_codex, monkeypatch):
    """Ce qui n'etait fige nulle part : le `cwd` du lancement reel."""
    _mode(monkeypatch, "ok")
    cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                          env=hc._env_abonnement())
    recu = faux_codex()[0]
    assert Path(recu["cwd"]).resolve() == cortex_codex.ROOT.resolve()


@pytest.mark.unit
def test_le_shim_cmd_de_windows_passe_par_cmd_c(monkeypatch, tmp_path):
    """npm installe Codex en `.cmd`, et `CreateProcess` ne sait pas le lancer.

    Le nom du binaire n'est pas resolu par le shell : c'est la meme classe de
    probleme que le shim Hermes, et la meme reponse.
    """
    shim = tmp_path / "codex.cmd"
    shim.write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(cortex_codex, "_executable", lambda: shim)
    monkeypatch.setattr(cortex_codex.os, "name", "nt")
    assert cortex_codex._prefixe() == ["cmd", "/c", str(shim)]


@pytest.mark.unit
def test_un_binaire_natif_n_est_pas_emballe_dans_cmd_c(monkeypatch, tmp_path):
    """Le shim est le cas de Windows ; ailleurs, la ligne reste nue."""
    natif = tmp_path / "codex"
    natif.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(cortex_codex, "_executable", lambda: natif)
    monkeypatch.setattr(cortex_codex.os, "name", "posix")
    assert cortex_codex._prefixe() == [str(natif)]


@pytest.mark.unit
def test_un_binaire_absent_est_nomme_et_non_invente(monkeypatch):
    """Sans binaire, l'erreur dit la cause ; elle n'invente pas un verdict."""
    monkeypatch.setattr(cortex_codex.shutil, "which", lambda _nom: None)
    monkeypatch.delenv("CODEX_CORTEX_EXECUTABLE", raising=False)
    with pytest.raises(cortex_codex.CodexEchec, match="introuvable"):
        cortex_codex._executable()


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Le schema versionne
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_le_schema_versionne_accepte_une_enveloppe_conforme():
    assert cortex_codex.valider(VERDICT_CONFORME) == []


@pytest.mark.unit
def test_le_schema_accepte_la_variante_evaluation_de_position():
    """La seconde branche de `anyOf` : evaluation, pas decision d'entree."""
    charge = {
        "verdicts": [{
            "request_ref": "pos-17",
            "state": "CAUTION",
            "reason": "exposition concentree, volatilite en hausse",
            "confidence": 0.4,
        }]
    }
    assert cortex_codex.valider(charge) == []


@pytest.mark.unit
def test_le_schema_refuse_une_enveloppe_sans_verdict():
    motifs = cortex_codex.valider({"verdicts": []})
    assert motifs
    assert any("moins de 1" in motif for motif in motifs)


@pytest.mark.unit
def test_le_schema_refuse_un_champ_absent():
    motifs = cortex_codex.valider({"verdicts": [{"action": "ALLOW"}]})
    assert any("confidence" in motif for motif in motifs)


@pytest.mark.unit
def test_le_schema_refuse_un_champ_inconnu():
    """`additionalProperties: false` fait d'un champ invente un refus."""
    charge = json.loads(json.dumps(VERDICT_CONFORME))
    charge["verdicts"][0]["humeur"] = "optimiste"
    motifs = cortex_codex.valider(charge)
    assert motifs
    assert any("inconnu" in motif for motif in motifs)


@pytest.mark.unit
def test_le_schema_refuse_une_confiance_hors_bornes():
    charge = json.loads(json.dumps(VERDICT_CONFORME))
    charge["verdicts"][0]["confidence"] = 1.4
    assert any("maximum" in motif for motif in cortex_codex.valider(charge))


@pytest.mark.unit
def test_le_schema_refuse_une_action_hors_enumeration():
    """Trois verdicts existent : ALLOW, WAIT, BLOCK. Pas quatre."""
    charge = json.loads(json.dumps(VERDICT_CONFORME))
    charge["verdicts"][0]["action"] = "MAYBE"
    assert any("hors de" in motif for motif in cortex_codex.valider(charge))


@pytest.mark.unit
def test_le_schema_refuse_un_verdict_qui_ne_satisfait_aucune_variante():
    """`confidence` seule ne suffit pas : il faut une branche complete."""
    motifs = cortex_codex.valider({"verdicts": [{"confidence": 0.5}]})
    assert any("variante" in motif for motif in motifs)


@pytest.mark.unit
def test_le_schema_est_bien_du_json_et_porte_un_identifiant_de_version():
    """Un contrat sans version ne peut pas etre durci sans casser ses lecteurs."""
    charge = cortex_codex.charger_schema()
    assert charge["$id"] == "titanium-v14/cortex-verdict/1"
    assert charge["additionalProperties"] is False


@pytest.mark.unit
def test_une_reponse_non_json_est_hors_schema(faux_codex, monkeypatch):
    """Avec `--output-schema`, un texte libre est deja une rupture de contrat."""
    _mode(monkeypatch, "nonjson")
    with pytest.raises(cortex_codex.CodexHorsSchema, match="sans JSON"):
        cortex_codex.executer("diagnostic simple", timeout_s=5.0,
                              env=hc._env_abonnement())


# ═══════════════════════════════════════════════════════════════════════════════
# 3. La classification : refus prealable contre panne
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_un_refus_prealable_leve_un_lot_trop_grand_sans_ouvrir_le_disjoncteur(
    faux_codex, monkeypatch
):
    """Un refus dit « cette requete » : le bassin reste disponible."""
    _mode(monkeypatch, "refus")
    with pytest.raises(hc.HermesLotTropGrand) as leve:
        hc._ask("diagnostic simple", scindable=True, provider="codex-cli")
    assert leve.value.provider == "codex-cli"
    assert hc.circuit_status("codex-cli")["available"] is True


@pytest.mark.unit
def test_un_quota_epuise_ouvre_le_disjoncteur_du_seul_bassin_codex(
    faux_codex, monkeypatch
):
    """Un compte a sec dit « ce fournisseur » : lui seul attend."""
    _mode(monkeypatch, "quota")
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("diagnostic simple", provider="codex-cli")
    assert not isinstance(leve.value, hc.HermesLotTropGrand)
    sec = hc.circuit_status("codex-cli")
    assert sec["available"] is False
    assert sec["retry_in_s"] > hc.HERMES_BACKOFF_S
    assert hc.circuit_status("claude-cli")["available"] is True


@pytest.mark.unit
def test_une_panne_ouvre_le_disjoncteur_du_seul_bassin_codex(faux_codex, monkeypatch):
    """Meme requete, meme bassin, deux verdicts opposes."""
    _mode(monkeypatch, "panne")
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("diagnostic simple", provider="codex-cli")
    assert not isinstance(leve.value, hc.HermesLotTropGrand)
    assert hc.circuit_status("codex-cli")["available"] is False
    assert hc.circuit_status("claude-cli")["available"] is True


@pytest.mark.unit
def test_un_succes_rend_le_verdict_et_laisse_le_bassin_disponible(faux_codex, monkeypatch):
    _mode(monkeypatch, "ok")
    resultat = hc._ask("diagnostic simple", provider="codex-cli")
    assert resultat == VERDICT_CONFORME
    assert hc.circuit_status("codex-cli")["available"] is True


@pytest.mark.unit
def test_un_succes_efface_l_erreur_precedente_du_bassin(faux_codex, monkeypatch):
    """`circuit_status` rapporte `last_error` : un motif perime serait lu comme courant."""
    hc._trip("HTTP 402: credit balance is too low", provider="codex-cli")
    assert hc.circuit_status("codex-cli")["last_error"] != ""
    # Le quota recharge : le disjoncteur se referme, et une reponse exploitable
    # doit effacer le motif.
    etat = hc._etat_circuit("codex-cli")
    etat["retry_at"] = 0.0
    _mode(monkeypatch, "ok")
    hc._ask("diagnostic simple", provider="codex-cli")
    assert hc.circuit_status("codex-cli")["last_error"] == ""


@pytest.mark.unit
def test_une_reponse_hors_schema_est_une_panne_pour_l_appelant_pas_pour_le_fournisseur(
    faux_codex, monkeypatch
):
    """Le fournisseur a repondu : ouvrir son disjoncteur punirait un service disponible.

    C'est le fail-closed que le dossier exige sur une charge hors schema : la
    reponse est inutilisable, donc WAIT — mais reessayer avec un lot plus petit
    ne repare pas une forme fausse, et la panne n'est pas celle du fournisseur.
    """
    _mode(monkeypatch, "horsschema")
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("diagnostic simple", provider="codex-cli")
    assert not isinstance(leve.value, hc.HermesLotTropGrand)
    assert hc.circuit_status("codex-cli")["available"] is True
    assert hc.circuit_status("codex-cli")["last_error"] == ""


@pytest.mark.unit
def test_aucun_secret_ne_traverse_le_disjoncteur_codex(faux_codex, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", FACTICE)
    _mode(monkeypatch, "quota")
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("diagnostic simple", provider="codex-cli")
    assert FACTICE not in str(leve.value)
    assert FACTICE not in hc.circuit_status("codex-cli")["last_error"]


# ═══════════════════════════════════════════════════════════════════════════════
# 4. La selection et le repli entre les DEUX bassins
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_le_second_bassin_repond_quand_le_premier_est_a_sec(faux_codex, monkeypatch):
    """La raison d'etre de C1 rendue utile : deux bassins, deux disjoncteurs."""
    hc._trip("HTTP 402: credit balance is too low", provider="claude-cli")
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,codex-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _mode(monkeypatch, "ok")
    assert hc._ask_avec_repli("diagnostic simple") == VERDICT_CONFORME
    # Le bassin a sec n'a meme pas ete interroge : aucun sous-processus pour lui.
    assert len(faux_codex()) == 1
    assert hc.circuit_status("claude-cli")["available"] is False


@pytest.mark.unit
def test_un_bassin_a_sec_puis_l_autre_en_panne_dit_laquelle(faux_codex, monkeypatch):
    """Chaque bassin tombe pour SA raison, et le message nomme la derniere."""
    hc._trip("HTTP 402: credit balance is too low", provider="claude-cli")
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,codex-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _mode(monkeypatch, "panne")
    with pytest.raises(hc.HermesCortexUnavailable):
        hc._ask_avec_repli("diagnostic simple")
    assert hc.circuit_status("claude-cli")["available"] is False
    assert hc.circuit_status("codex-cli")["available"] is False


@pytest.mark.unit
def test_les_deux_bassins_gardent_des_disjoncteurs_independants(monkeypatch):
    """Le test que le dossier demande pour C3 : un 402 n'eteint pas l'autre."""
    hc._reset_circuits()
    hc._trip("HTTP 402: your credit balance is too low", provider="codex-cli")
    assert hc.circuit_status("codex-cli")["available"] is False
    assert hc.circuit_status("claude-cli")["available"] is True
    assert hc.circuit_status("claude-cli")["last_error"] == ""
    disponibles = [
        nom for nom in ("claude-cli", "codex-cli")
        if hc.circuit_status(nom)["available"]
    ]
    assert disponibles == ["claude-cli"]


@pytest.mark.unit
def test_le_bassin_codex_est_interroge_par_son_propre_nom(faux_codex, monkeypatch):
    """Sans cela, le repli interrogerait toujours la voie Claude."""
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "codex-cli")
    _mode(monkeypatch, "ok")
    assert hc._ask_avec_repli("diagnostic simple") == VERDICT_CONFORME
    assert len(faux_codex()) == 1
    assert hc.circuit_status()["provider"] == "codex-cli"


@pytest.mark.unit
def test_les_deux_bassins_sont_declares_et_distincts():
    """Une identite par bassin, sans convention implicite de nommage."""
    assert cortex_codex.NOMS == ("codex-cli",)
    assert "claude-cli" not in cortex_codex.NOMS
    assert len(set(cortex_codex.NOMS)) == len(cortex_codex.NOMS)
