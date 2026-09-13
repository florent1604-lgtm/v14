"""Le cortex doit tourner sur les CLI d'abonnement, et survivre a un fournisseur a sec.

Etape C1 du dossier cortex. Trois choses sont prouvees ici, et chacune a son
propre faux CLI :

1. **La ligne de commande qui garde l'abonnement.** Un faux CLI relit son propre
   `sys.argv` et son propre environnement, et les ecrit dans un journal. Le test
   compare ce que le PROCESSUS a recu, pas ce que le code croit construire.
2. **Le disjoncteur par fournisseur.** Un fournisseur a sec n'ouvre plus que son
   propre disjoncteur : le suivant repond sur la meme requete.
3. **La distinction honnete entre refus prealable et panne.** Un refus dit
   « cette requete » et ne ferme aucun disjoncteur ; une panne dit « ce
   fournisseur » et ferme le sien, sans toucher aux autres.

Le second fournisseur porte ici le label generique `repli-cli` : le faux CLI
l'accepte sans connaitre ce nom. `codex-cli` est un nom RESERVE depuis l'etape
C3 — il designe le bassin `titanium.cortex_codex`, et un test de C1 qui
l'emploierait n'exercerait plus la voie CLI qu'il pretend mesurer.

**Ce que ces tests ne prouvent pas, et qu'eux seuls ne peuvent pas prouver :**
aucun abonnement Claude Pro/Max ni compte Codex n'est sollicite ici. Un faux CLI
etablit le contrat de transport et la logique d'etat ; il n'etablit pas qu'un
vrai CLI rend rc=0 en ~10 s, ni que le compte est bien facture a l'abonnement.
Cette constatation-la ne peut venir que de la machine de l'utilisateur, et c'est
ecrit dans le rapport de la PR.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from titanium import cortex_cli, hermes_cortex as hc

#: Deliberement PAS de forme de cle : la garde de secrets du depot refuse,
#: a juste titre, tout ce qui ressemble a un jeton. Ce test n'a besoin que d'une
#: valeur reconnaissable dans la variable ANTHROPIC_API_KEY, pas d'une valeur
#: plausible — et lui donner une forme de cle ferait crier la garde sans rien
#: prouver de plus.
FACTICE = "valeur-factice-jamais-reelle"

#: Le faux CLI. Il ne connait ni Anthropic ni Codex : il obeit a `FAUX_CLI_MODES`,
#: un dictionnaire `fournisseur -> mode`, et journalise ce qu'il a RECU.
FAUX_CLI = '''\
"""Faux CLI de test : journalise son argv et son environnement, puis obeit."""

import json
import os
import re
import sys

argv = sys.argv[1:]


def _valeur(drapeau):
    try:
        return argv[argv.index(drapeau) + 1]
    except (ValueError, IndexError):
        return ""


fournisseur = _valeur("--provider")
modes = json.loads(os.environ.get("FAUX_CLI_MODES", "{}"))
recu = {
    "argv": argv,
    "fournisseur": fournisseur,
    "modele": _valeur("--model"),
    "cwd": os.getcwd(),
    "anthropic_api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
    "anthropic_auth_token": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
    "mode": modes.get(fournisseur, "ok"),
}
journal = os.environ.get("FAUX_CLI_JOURNAL")
if journal:
    with open(journal, "a", encoding="utf-8") as flux:
        flux.write(json.dumps(recu, ensure_ascii=False) + "\\n")

if recu["mode"] == "ok":
    # Un vrai modele rend un verdict par reference demandee. Le faux CLI fait
    # pareil quand la requete en porte, et rend une enveloppe vide sinon : les
    # tests qui n'envoient qu'une consigne en prose gardent leur attente.
    prompt = argv[1] if len(argv) > 1 else ""
    refs = re.findall(r'"(?:decision_ref|request_ref)":[ ]*"([^"]+)"', prompt)
    cle = "decision_ref" if "decision_ref" in prompt else "request_ref"
    print(json.dumps({"verdicts": [
        {cle: ref, "confidence": 0.5} for ref in dict.fromkeys(refs)
    ]}))
    sys.exit(0)

if recu["mode"] == "refus":
    # Refus AVANT generation : la requete est en cause, pas le fournisseur.
    print("HTTP 400: request too large")
    sys.exit(1)

if recu["mode"] == "quota":
    # Le CLI rend 0 meme quand l'API refuse : le motif est sur stdout.
    print("HTTP 402: Your credit balance is too low to access the API.")
    sys.exit(0)

# "panne" : le processus meurt sans rien dire.
sys.exit(3)
'''


@pytest.fixture
def faux_cli(tmp_path, monkeypatch):
    """Un faux CLI reellement lance par `subprocess.run`, sans abonnement.

    L'injection passe par le PROPRIETAIRE du lancement : on remplace le binaire,
    jamais le chemin. `repli-cli` se DECLARE dans le registre — il n'existe pas
    de second chemin de lancement, meme pour un bassin de test.
    """
    script = tmp_path / "faux_cli_hermes.py"
    script.write_text(FAUX_CLI, encoding="utf-8")
    journal = tmp_path / "journal.ndjson"
    monkeypatch.setitem(cortex_cli.REGLES, "repli-cli", cortex_cli.Regle(
        nom="repli-cli", variable="REPLI_CORTEX_EXECUTABLE",
        binaires=("repli",), shim="module",
    ))
    monkeypatch.setattr(
        cortex_cli, "prefixe", lambda _bassin: [sys.executable, str(script)]
    )
    monkeypatch.setenv("FAUX_CLI_JOURNAL", str(journal))
    monkeypatch.setenv("FAUX_CLI_MODES", "{}")
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

    return lignes


def _modes(monkeypatch, **modes: str) -> None:
    monkeypatch.setenv("FAUX_CLI_MODES", json.dumps(modes))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. La ligne de commande qui garde l'abonnement
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_la_ligne_de_commande_reellement_recue_est_figee(faux_cli, monkeypatch):
    """Ce que le processus a recu, pas ce que le code croit construire."""
    _modes(monkeypatch, **{"claude-cli": "ok"})
    assert hc._ask("diagnostic simple", provider="claude-cli") == {"verdicts": []}
    recu = faux_cli()
    assert len(recu) == 1
    assert recu[0]["argv"] == [
        "-z", "diagnostic simple",
        "--provider", "claude-cli",
        "--model", hc.HERMES_MODEL,
        "--ignore-rules",
        "-t", "todo",
    ]


@pytest.mark.unit
def test_le_cli_est_lance_sans_aucun_identifiant_api(faux_cli, monkeypatch):
    """La cause racine mesuree : avec la cle, le CLI abandonne l'abonnement."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", FACTICE)
    _modes(monkeypatch, **{"claude-cli": "ok"})
    hc._ask("diagnostic simple", provider="claude-cli")
    recu = faux_cli()[0]
    assert recu["anthropic_api_key"] == ""
    assert recu["anthropic_auth_token"] == ""


@pytest.mark.unit
def test_le_fournisseur_nomme_est_celui_que_le_cli_recoit(faux_cli, monkeypatch):
    """Sans cela, le repli interrogerait toujours le fournisseur par defaut."""
    _modes(monkeypatch, **{"repli-cli": "ok"})
    hc._ask("diagnostic simple", provider="repli-cli")
    assert faux_cli()[0]["fournisseur"] == "repli-cli"
    assert hc.circuit_status("repli-cli")["provider"] == "repli-cli"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Le disjoncteur par fournisseur
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_un_402_sur_un_fournisseur_laisse_l_autre_disponible(monkeypatch):
    """Le test que le dossier demande pour l'etape C1, mot pour mot."""
    hc._reset_circuits()
    hc._trip("HTTP 402: credit balance is too low", provider="deepseek-api")
    assert hc.circuit_status("deepseek-api")["available"] is False
    assert hc.circuit_status("claude-cli")["available"] is True
    assert hc.circuit_status("claude-cli")["last_error"] == ""


@pytest.mark.unit
def test_une_panne_ouvre_le_disjoncteur_du_fournisseur_qui_est_tombe(faux_cli, monkeypatch):
    hc._reset_circuits()
    _modes(monkeypatch, **{"claude-cli": "panne"})
    with pytest.raises(hc.HermesCortexUnavailable):
        hc._ask("diagnostic simple", provider="claude-cli")
    assert hc.circuit_status("claude-cli")["available"] is False
    assert hc.circuit_status("repli-cli")["available"] is True


@pytest.mark.unit
def test_un_fournisseur_a_sec_est_saute_et_le_suivant_repond(faux_cli, monkeypatch):
    """Sans liste, nommer les disjoncteurs ne changerait rien d'observable.

    `scindable=True` : les drapeaux de la PRODUCTION. `_ask_par_lots` ne
    l'appelle jamais autrement, et c'est precisement sous ce drapeau qu'un
    quota devient un `HermesLotTropGrand` au lieu d'ouvrir le disjoncteur.
    """
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "quota", "repli-cli": "ok"})
    assert hc.interroger_bassins("diagnostic simple", scindable=True) == {"verdicts": []}
    # Le fournisseur a sec est mis en quarantaine, et lui seul. La duree est
    # EPINGLEE : c'est celle que `_ask_par_lots` applique deja au meme cas, donc
    # une seule duree pour une seule situation. La borne large d'avant
    # (`> HERMES_BACKOFF_S`) aurait laisse passer un retour au backoff de 600 s
    # sans que le test le dise.
    sec = hc.circuit_status("claude-cli")
    assert sec["available"] is False
    assert sec["retry_in_s"] == pytest.approx(hc.HERMES_BACKOFF_S, abs=1.0)
    assert hc.circuit_status("repli-cli")["available"] is True
    assert [ligne["fournisseur"] for ligne in faux_cli()] == ["claude-cli", "repli-cli"]


@pytest.mark.unit
def test_le_quota_expire_et_le_fournisseur_principal_est_reessaye(faux_cli, monkeypatch):
    """« Expiration de quota » : la quarantaine se termine et le principal revient."""
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "quota", "repli-cli": "ok"})
    hc.interroger_bassins("diagnostic simple", scindable=True)
    attente = hc.circuit_status("claude-cli")["retry_in_s"]
    assert attente == pytest.approx(hc.HERMES_BACKOFF_S, abs=1.0)

    maintenant = hc.time.time()
    monkeypatch.setattr(hc.time, "time", lambda: maintenant + attente + 1.0)
    assert hc.circuit_status("claude-cli")["available"] is True
    # Et il est bien reinterroge : son quota recharge reprend la main.
    _modes(monkeypatch, **{"claude-cli": "ok", "repli-cli": "ok"})
    assert hc.interroger_bassins("diagnostic simple", scindable=True) == {"verdicts": []}
    assert [ligne["fournisseur"] for ligne in faux_cli()] == ["claude-cli", "repli-cli", "claude-cli"]


@pytest.mark.unit
def test_tous_les_disjoncteurs_ouverts_echouent_et_le_disent(faux_cli, monkeypatch):
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    for nom in ("claude-cli", "repli-cli"):
        hc._trip("HTTP 402: credit balance is too low", provider=nom)
    with pytest.raises(hc.HermesCortexUnavailable, match="tous les fournisseurs"):
        hc.interroger_bassins("diagnostic simple")
    assert faux_cli() == []  # aucun sous-processus lance pour rien


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Refus prealable contre panne
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.unit
def test_un_refus_prealable_ne_ferme_aucun_disjoncteur(faux_cli, monkeypatch):
    """Un refus dit « cette requete », pas « ce fournisseur »."""
    hc._reset_circuits()
    _modes(monkeypatch, **{"claude-cli": "refus"})
    with pytest.raises(hc.HermesLotTropGrand) as leve:
        hc._ask("diagnostic simple", scindable=True, provider="claude-cli")
    assert leve.value.provider == "claude-cli"
    assert hc.circuit_status("claude-cli")["available"] is True


@pytest.mark.unit
def test_une_panne_ne_se_confond_pas_avec_un_refus(faux_cli, monkeypatch):
    """Meme requete, meme fournisseur, deux verdicts opposes."""
    hc._reset_circuits()
    _modes(monkeypatch, **{"claude-cli": "panne"})
    with pytest.raises(hc.HermesCortexUnavailable) as panne:
        hc._ask("diagnostic simple", scindable=True, provider="claude-cli")
    assert not isinstance(panne.value, hc.HermesLotTropGrand)
    assert hc.circuit_status("claude-cli")["available"] is False


@pytest.mark.unit
def test_un_fournisseur_a_sec_ne_bloque_pas_la_scission_du_lot(faux_cli, monkeypatch):
    """Le repli doit laisser passer le refus quand PERSONNE n'a repondu.

    C'est ce qui rend a `_ask_par_lots` son role : scinder le lot. Un repli qui
    avalerait le refus priverait le decoupage de sa raison d'etre.
    """
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "refus", "repli-cli": "refus"})
    with pytest.raises(hc.HermesLotTropGrand):
        hc.interroger_bassins("diagnostic simple", scindable=True)
    assert hc.circuit_status("claude-cli")["available"] is True
    assert hc.circuit_status("repli-cli")["available"] is True


@pytest.mark.unit
def test_un_fournisseur_qui_refuse_laisse_la_main_au_suivant(faux_cli, monkeypatch):
    """Un compte a sec refuse tout ce qu'on lui envoie : le suivant accepte."""
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "refus", "repli-cli": "ok"})
    assert hc.interroger_bassins("diagnostic simple", scindable=True) == {"verdicts": []}
    assert [ligne["fournisseur"] for ligne in faux_cli()] == ["claude-cli", "repli-cli"]
    # Le refus n'est pas une panne : le premier fournisseur reste disponible.
    assert hc.circuit_status("claude-cli")["available"] is True


@pytest.mark.unit
def test_la_liste_garde_le_fournisseur_principal_en_tete(monkeypatch):
    """Une liste qui l'oublie ne doit pas changer silencieusement de cortex."""
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "repli-cli,ollama-local")
    assert hc._fournisseurs() == ["claude-cli", "repli-cli", "ollama-local"]
    monkeypatch.delenv("TITANIUM_HERMES_PROVIDERS")
    assert hc._fournisseurs() == ["claude-cli"]


@pytest.mark.unit
def test_aucun_secret_ne_traverse_le_disjoncteur(faux_cli, monkeypatch):
    """Le message d'etat reste classifie, meme sur un faux CLI bavard."""
    hc._reset_circuits()
    monkeypatch.setenv("ANTHROPIC_API_KEY", FACTICE)
    _modes(monkeypatch, **{"claude-cli": "quota"})
    with pytest.raises(hc.HermesCortexUnavailable) as leve:
        hc._ask("diagnostic simple", provider="claude-cli")
    assert FACTICE not in str(leve.value)
    assert FACTICE not in hc.circuit_status("claude-cli")["last_error"]


@pytest.mark.unit
def test_les_circuits_par_defaut_sont_ceux_du_fournisseur_actif(faux_cli, monkeypatch):
    """`circuit_status()` sans argument reste le fournisseur actif, comme avant."""
    hc._reset_circuits()
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "repli-cli")
    _modes(monkeypatch, **{"repli-cli": "panne"})
    with pytest.raises(hc.HermesCortexUnavailable):
        hc._ask("diagnostic simple")
    assert hc.circuit_status()["provider"] == "repli-cli"
    assert hc.circuit_status()["available"] is False
    assert hc.circuit_status("claude-cli")["available"] is True


@pytest.mark.unit
def test_le_cli_tourne_dans_la_racine_du_depot(faux_cli, monkeypatch):
    """Ce qui n'etait fige nulle part : `cwd` et `--model` du lancement reel.

    Le journal est ecrit par le processus lui-meme : sa presence suffit a
    prouver qu'un vrai sous-processus a tourne, et non une doublure.
    """
    _modes(monkeypatch, **{"claude-cli": "ok"})
    hc._ask("diagnostic simple", provider="claude-cli")
    recu = faux_cli()[0]
    assert Path(recu["cwd"]).resolve() == Path(hc.ROOT).resolve()
    assert recu["modele"] == hc.HERMES_MODEL


# ═══════════════════════════════════════════════════════════════════════════════
# 4. La quarantaine sur le chemin de PRODUCTION
#
# `interroger_bassins` n'est appelee qu'une fois dans tout le code de production —
# `_ask_par_lots`, toujours avec `scindable=True`. Les tests de la section 2
# l'appelaient avec le drapeau par defaut, donc la quarantaine qu'ils
# mesuraient n'etait PAS celle que la production obtient. Cette section
# n'utilise que le chemin et les drapeaux reels.
# ═══════════════════════════════════════════════════════════════════════════════


def _lot(*refs: str) -> list[dict]:
    """Des candidats minimaux : la seule chose que `_ask_par_lots` exige."""
    return [{"decision_ref": ref, "symbole": "US500"} for ref in refs]


@pytest.mark.unit
def test_le_refus_de_compte_n_est_pas_un_refus_de_requete():
    """Le discriminant, sur le vocabulaire que le classificateur produit lui-meme.

    Sans lui, soit on quarantaine toute refusal — et un lot trop gros ferme un
    compte disponible —, soit on n'en quarantaine aucune, et un compte a sec
    est relance a chaque lot.
    """
    compte = (
        "HTTP 402: provider refused: credit balance is too low",
        "HTTP 429: provider rate limit 429",
        "provider usage/quota refusal",
    )
    requete = (
        "HTTP 400: request too large for this model",
        # Mesure du 07/09 : ce libelle recouvrait une fenetre d'usage qui se
        # recharge, et le lot plus petit passait. La quarantaine aurait ferme
        # un compte disponible.
        "HTTP 400: provider refused: credit balance is too low",
        "HTTP 413: HERMES_CLI_ERROR_OR_INVALID_RESPONSE",
    )
    for detail in compte:
        assert hc._refus_du_fournisseur(detail) is True, detail
    for detail in requete:
        assert hc._refus_du_fournisseur(detail) is False, detail
        # Et la scission de lot garde exactement sa semantique : ce sont
        # toujours des refus prealables, donc `_ask_par_lots` scinde.
        assert hc._refus_prealable(detail) is True, detail


@pytest.mark.unit
def test_un_quota_met_le_bassin_en_quarantaine_des_le_premier_lot(faux_cli, monkeypatch):
    """Le chemin reel : `_ask_par_lots`, jamais `_ask` nu."""
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "quota", "repli-cli": "ok"})

    verdicts = hc._ask_par_lots(
        _lot("c1", "c2"), ["ENTETE"], "candidates", "decision_ref"
    )
    assert sorted(verdicts) == ["c1", "c2"]

    etat = hc.etat_bassins()
    assert etat["mesure"] is True
    assert etat["a_sec"] == ["claude-cli"]
    sec = next(b for b in etat["bassins"] if b["provider"] == "claude-cli")
    assert sec["en_quarantaine"] is True
    assert sec["retry_in_s"] == pytest.approx(hc.HERMES_BACKOFF_S, abs=1.0)
    # Le CLI rend 0 avec la prose sur stdout : le motif porte donc aussi la
    # raison du parsing. Ce qui compte pour l'operateur est la queue classee.
    assert sec["motif"].endswith(
        "HTTP 402: provider refused: credit balance is too low"
    )

    # `HERMES_LOT_MAX = 1` : deux lots. Le premier interroge le bassin a sec et
    # l'y laisse ; le second ne le relance plus du tout.
    assert [ligne["fournisseur"] for ligne in faux_cli()] == [
        "claude-cli", "repli-cli", "repli-cli"
    ]


@pytest.mark.unit
def test_le_lot_suivant_ne_relance_pas_le_bassin_a_sec(faux_cli, monkeypatch):
    """« Saute » veut dire : aucun sous-processus. Le journal le prouve."""
    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "quota", "repli-cli": "ok"})

    hc._ask_par_lots(_lot("c1"), ["ENTETE"], "candidates", "decision_ref")
    avant = len([l for l in faux_cli() if l["fournisseur"] == "claude-cli"])
    assert avant == 1, "le bassin a sec a bien ete interroge une premiere fois"

    verdicts = hc._ask_par_lots(_lot("c2"), ["ENTETE"], "candidates", "decision_ref")
    assert sorted(verdicts) == ["c2"]
    apres = len([l for l in faux_cli() if l["fournisseur"] == "claude-cli"])
    assert apres == avant, "un bassin en quarantaine ne doit plus etre relance"


@pytest.mark.unit
def test_la_sonde_nommee_lit_l_etat_des_bassins(faux_cli, monkeypatch, tmp_path):
    """L'etat doit etre LU, pas seulement ecrit — et par un lecteur nomme."""
    from titanium.web import cortex_status

    hc._reset_circuits()
    monkeypatch.setenv("TITANIUM_HERMES_PROVIDERS", "claude-cli,repli-cli")
    monkeypatch.setattr(hc, "HERMES_PROVIDER", "claude-cli")
    _modes(monkeypatch, **{"claude-cli": "quota", "repli-cli": "ok"})
    hc._ask_par_lots(_lot("c1"), ["ENTETE"], "candidates", "decision_ref")

    bloc = cortex_status.snapshot(root=tmp_path)["bassins"]
    assert bloc["source"] == "processus"
    assert bloc["mesure"] is True
    assert bloc["a_sec"] == ["claude-cli"]
    sec = next(b for b in bloc["bassins"] if b["provider"] == "claude-cli")
    assert sec["en_quarantaine"] is True
    assert sec["motif"].endswith(
        "HTTP 402: provider refused: credit balance is too low"
    )


@pytest.mark.unit
def test_la_sonde_avoue_ne_pas_savoir_quand_rien_n_a_ete_decide(tmp_path):
    """Un processus qui n'a rien decide n'affiche pas « disponible ».

    L'etat des disjoncteurs vit dans le processus qui trade. La sonde servie
    ailleurs ne le voit pas : elle doit le DIRE, sinon elle affiche un bassin
    sain sur un processus qui n'en a jamais rien su.
    """
    from titanium.web import cortex_status

    hc._reset_circuits()
    bloc = cortex_status.snapshot(root=tmp_path)["bassins"]
    assert bloc["mesure"] is False
    assert bloc["source"] == "aucune_decision"
    assert bloc["a_sec"] == []
