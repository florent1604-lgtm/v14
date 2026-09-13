"""Cycle de vie du flux macro, et sa traversee de la frontiere de processus.

Trois proprietes, et chacune repond a un defaut mesure :

1. **Il demarre.** La brique savait relire, personne ne la lancait : le
   processus arme restait sur ``UNKNOWN``, donc sur un refus, alors que sa
   configuration disait ``enabled=true``.
2. **Il s'arrete vite, et toujours.** Un arret qui attend le prochain
   rafraichissement ferait traîner la fermeture du processus jusqu'a une heure ;
   un arret arrive avant que le fil n'arme son evenement le laissait carrement
   vivant — 40 essais sur 40. Les deux sont mesures ici, et le delai d'arret
   couvre la lecture que la politique autorise.
3. **Son verdict traverse le processus.** La boucle le publie dans le battement
   qu'elle ecrit deja, la sonde du tableau de bord le relit — sans quoi la jauge
   afficherait un verdict local a la place de celui qui decide.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from titanium.macro import (
    FileMacroSource,
    MacroCache,
    MacroCalendar,
    MacroImpact,
    MacroPolicy,
    MacroService,
    MacroState,
    UnavailableMacroSource,
    build_source,
    macro_publication,
    macro_risk,
)
from titanium.macro.contracts import MacroEvent

pytestmark = pytest.mark.unit

JAUGES = ("score_pct", "freshness_pct", "imminence_pct")


def politique(*, file: str = "peu-importe.json", **surcharges) -> MacroPolicy:
    base = {
        "enabled": True, "provider": "file", "file": file,
        "ttl_s": 900.0, "refresh_s": 0.2, "max_backoff_s": 0.4,
        "blackout_before_s": 900.0, "blackout_after_s": 300.0,
        "elevated_within_s": 3600.0,
    }
    base.update(surcharges)
    return MacroPolicy.from_mapping(base)


def ecrire_calendrier(chemin: Path, *, dans_minutes: int = 10) -> Path:
    """Un calendrier reel sur disque : le fournisseur `file` le lit tel quel."""
    quand = datetime.now(timezone.utc) + timedelta(minutes=dans_minutes)
    chemin.write_text(json.dumps({"events": [
        {"title": "FOMC", "currency": "USD", "impact": "High",
         "scheduled_at": quand.isoformat()},
    ]}), encoding="utf-8")
    return chemin


def attendre(predicat, *, limite_s: float = 8.0) -> bool:
    """Attend qu'un fil ait publie. Rend False au lieu de bloquer le test."""
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        if predicat():
            return True
        time.sleep(0.02)
    return False


def test_flux_eteint_ne_demarre_aucun_fil():
    """`enabled=false` est le defaut du depot : rien ne doit bouger."""
    policy = MacroPolicy()
    assert policy.enabled is False
    assert isinstance(build_source(policy), UnavailableMacroSource)

    service = MacroService(policy=policy)
    assert service.start() is False
    assert service.running is False
    assert service.etat()["running"] is False
    # `stop` sur un service jamais demarre n'attend rien et ne leve pas.
    assert service.stop() is True


def test_le_service_sert_le_calendrier_puis_s_arrete(tmp_path):
    calendrier = ecrire_calendrier(tmp_path / "calendrier.json")
    policy = politique(file=str(calendrier))
    cache = MacroCache()
    service = MacroService(policy=policy, cache=cache,
                           source=FileMacroSource(calendrier))
    try:
        assert service.start() is True
        assert service.running is True
        assert attendre(lambda: cache.view().has_data), "aucune lecture publiee"
        verdict = macro_risk(policy=policy, cache=cache, symbols="EURUSD")
        assert verdict.state is MacroState.BLACKOUT
        assert verdict.allows_new_risk is False
        assert "FOMC" in verdict.reasons[0]
        assert service.etat()["error"] == ""
    finally:
        assert service.stop() is True
    assert service.running is False


def test_l_arret_n_attend_pas_le_prochain_rafraichissement(tmp_path):
    """`refresh_s` d'une heure : l'arret doit rester immediat, pas horaire.

    C'est le defaut que ce test protege : une boucle de service qui dort sans
    etre reveillable fait traîner la fermeture du processus arme d'un cycle
    complet — une heure avec les bornes par defaut.
    """
    calendrier = ecrire_calendrier(tmp_path / "calendrier.json")
    policy = politique(file=str(calendrier), refresh_s=3600.0, max_backoff_s=3600.0)
    cache = MacroCache()
    service = MacroService(policy=policy, cache=cache,
                           source=FileMacroSource(calendrier))
    assert service.start() is True
    assert attendre(lambda: cache.view().has_data), "aucune lecture publiee"

    depart = time.monotonic()
    assert service.stop() is True
    ecoule = time.monotonic() - depart
    assert ecoule < 2.0, f"arret trop lent ({ecoule:.2f} s) — le sommeil n'est pas reveillable"


def test_un_arret_immediat_ne_laisse_aucun_fil(tmp_path):
    """`start()` puis `stop()` sans le moindre delai.

    Defaut mesure : entre `Thread.start()` et l'instant ou le fil cree le sien,
    la demande d'arret tombait dans le vide. `stop()` attendait alors son delai
    entier, rendait `False`, et le fil survivait — 40 essais sur 40. Le nom de
    fil est distinct pour qu'aucun autre test ne puisse faire passer ce controle
    a tort, et le cycle est repete parce que le defaut dependait de l'instant.
    """
    import threading

    calendrier = ecrire_calendrier(tmp_path / "calendrier.json")
    nom = "macro-feed-arret-immediat"
    for _ in range(25):
        service = MacroService(
            policy=politique(file=str(calendrier)), cache=MacroCache(),
            source=FileMacroSource(calendrier), thread_name=nom,
        )
        assert service.start() is True
        assert service.stop() is True, "l'arret immediat a echoue"
        assert service.running is False
    assert not [f for f in threading.enumerate() if f.name == nom], (
        "un fil du service a survecu a son arret"
    )


def test_le_delai_d_arret_couvre_le_budget_de_lecture(tmp_path):
    """Un fil bloque dans un `fetch` ne peut pas etre tue : le delai doit le couvrir.

    La lecture dure 6 s et la politique declare un budget de 8 s : le contrat
    d'arret doit donc valoir au moins ce budget. C'est le cas que la constante
    fixe (5 s) rendait impossible a tenir — l'arret rendait `False` et laissait
    le fil vivant. Le test coute les 6 s de la lecture, et c'est le prix de la
    seule version qui echoue sur l'ancien contrat : un budget court passerait
    des deux cotes.
    """
    calendrier = ecrire_calendrier(tmp_path / "calendrier.json")
    reel = FileMacroSource(calendrier)

    class SourceLente:
        name = "lente"

        def fetch(self):
            time.sleep(6.0)
            return reel.fetch()

    service = MacroService(
        policy=politique(file=str(calendrier), timeout_s=8.0),
        cache=MacroCache(), source=SourceLente(),
    )
    assert service.start() is True
    time.sleep(0.2)  # le fil est entre dans la lecture
    depart = time.monotonic()
    assert service.stop() is True, "le delai ne couvre pas la lecture autorisee"
    ecoule = time.monotonic() - depart
    assert 4.0 < ecoule < 9.0, f"arret hors contrat ({ecoule:.2f} s)"
    assert service.running is False


def test_une_source_en_panne_laisse_le_verdict_ferme(tmp_path):
    """Fichier absent : le service tourne, echoue, et le risque neuf reste refuse."""
    absente = tmp_path / "jamais-ecrit.json"
    policy = politique(file=str(absente))
    cache = MacroCache()
    service = MacroService(policy=policy, cache=cache, source=FileMacroSource(absente))
    try:
        assert service.start() is True
        assert attendre(lambda: cache.view().total_failures >= 1), "aucun echec enregistre"
        assert cache.view().has_data is False
        assert service.running is True, "une source en panne ne doit pas tuer le service"
        assert service.etat()["error"] == "", "la panne de source est absorbee par le flux"
        verdict = macro_risk(policy=policy, cache=cache)
        assert verdict.state is MacroState.UNKNOWN
        assert verdict.allows_new_risk is False
    finally:
        assert service.stop() is True


def test_la_publication_porte_les_jauges():
    """Le bloc publie est deja normalise : pourcentages bornes, severite connue."""
    maintenant = datetime.now(timezone.utc)
    cache = MacroCache()
    cache.publish(MacroCalendar(provider="file", fetched_at=maintenant, events=(
        MacroEvent(event_id="fomc", title="FOMC", currency="USD",
                   impact=MacroImpact.HIGH,
                   scheduled_at=maintenant + timedelta(minutes=10), source="file"),
    )))
    bloc = macro_publication(policy=politique(), cache=cache)

    for cle in ("state", "label", "severity", "allows_new_risk", "service", *JAUGES):
        assert cle in bloc, f"cle manquante dans le bloc publie : {cle}"
    for cle in JAUGES:
        assert isinstance(bloc[cle], int)
        assert 0 <= bloc[cle] <= 100, f"{cle} hors bornes : {bloc[cle]}"
    assert bloc["severity"] in {"ok", "warn", "crit"}
    assert bloc["enabled"] is True
    assert bloc["state"] == MacroState.BLACKOUT.value
    assert bloc["severity"] == "crit"
    assert bloc["next_event"]["title"] == "FOMC"
    assert bloc["service"]["enabled"] is True


def test_une_configuration_illisible_devient_un_rouge_lisible(tmp_path, monkeypatch):
    """Une faute de configuration doit s'afficher, jamais casser la sonde."""
    from titanium.web import state as ws

    monkeypatch.setattr(ws, "RACINE", tmp_path)
    (tmp_path / "results").mkdir(exist_ok=True)
    monkeypatch.setattr(
        "titanium.macro.load_policy",
        lambda: (_ for _ in ()).throw(ValueError("cles macro inconnues: ttl")),
    )

    bloc = ws.macro()
    assert bloc["disponible"] is False
    assert bloc["allows_new_risk"] is False
    assert bloc["severity"] == "crit"
    assert "illisible" in bloc["reasons"][0]


def test_le_battement_publie_le_verdict_de_la_boucle(tmp_path, monkeypatch):
    """Le verdict de la BOUCLE traverse le processus par le battement.

    C'est la propriete qui manquait : la jauge doit montrer ce qui decide. On
    publie donc depuis `live_demo.battre` — le canal reel — et on relit par la
    sonde reelle, sans passer par un dictionnaire fabrique a la main.
    """
    import titanium.macro as paquet_macro
    from titanium.web import state as ws
    from tools import live_demo

    maintenant = datetime.now(timezone.utc)
    cache = MacroCache()
    cache.publish(MacroCalendar(provider="file", fetched_at=maintenant, events=(
        MacroEvent(event_id="fomc", title="FOMC", currency="USD",
                   impact=MacroImpact.HIGH,
                   scheduled_at=maintenant + timedelta(minutes=10), source="file"),
    )))
    monkeypatch.setattr(paquet_macro, "get_cache", lambda: cache)
    monkeypatch.setattr(paquet_macro, "load_policy", lambda: politique())

    monkeypatch.setattr(ws, "RACINE", tmp_path)
    battement = tmp_path / "results" / "loop_heartbeat.json"
    monkeypatch.setattr(live_demo, "BATTEMENT", battement)

    live_demo.battre({"tours": 1}, armer=False, equity=0.0)

    publie = json.loads(battement.read_text(encoding="utf-8"))["macro"]
    assert publie["state"] == MacroState.BLACKOUT.value
    assert publie["allows_new_risk"] is False

    relu = ws.macro()
    assert relu["source"] == "boucle", "la sonde doit preferer le verdict de la boucle"
    assert relu["state"] == MacroState.BLACKOUT.value
    assert relu["allows_new_risk"] is False
    assert relu["perime"] is False
    assert relu["publie_age_s"] is not None and relu["publie_age_s"] < 60
    assert relu["disponible"] is True


def test_la_sonde_marque_un_verdict_de_boucle_perime(tmp_path, monkeypatch):
    """Une boucle muette ne doit pas presenter son dernier verdict comme actuel."""
    from titanium.web import state as ws

    monkeypatch.setattr(ws, "RACINE", tmp_path)
    (tmp_path / "results").mkdir(exist_ok=True)
    vieux = datetime.now(timezone.utc) - timedelta(minutes=10)
    (tmp_path / "results" / "loop_heartbeat.json").write_text(json.dumps({
        "at": vieux.isoformat(), "intervalle": 60, "armed": True, "stats": {},
        "macro": {"state": "CLEAR", "label": "Calme", "severity": "ok",
                  "allows_new_risk": True, "score_pct": 0, "freshness_pct": 80,
                  "imminence_pct": 0, "reasons": ["rien dans l'horizon"],
                  "enabled": True, "service": {"enabled": True, "running": True}},
    }), encoding="utf-8")

    relu = ws.macro()
    assert relu["source"] == "boucle"
    assert relu["perime"] is True, "un verdict vieux de dix minutes doit etre signale"
    assert relu["publie_age_s"] > 500


def test_la_sonde_calcule_en_local_quand_la_boucle_se_tait(tmp_path, monkeypatch):
    """Sans battement, le tableau de bord reste lisible — et le DIT."""
    from titanium.web import state as ws

    monkeypatch.setattr(ws, "RACINE", tmp_path)

    relu = ws.macro()
    assert relu["disponible"] is True
    assert relu["source"] == "processus"
    assert relu["state"] == MacroState.CLEAR.value
    assert relu["enabled"] is False, "sans config/macro.json, le flux est eteint"
