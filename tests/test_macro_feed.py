"""Tests du flux macro externe — contrats, cache, sources, risque, integration.

Ce que ces tests protegent, dans l'ordre d'importance :

1. **Non-regression.** Flux eteint, le vecteur de features et le verdict de la
   porte sont EXACTEMENT ceux d'avant l'arrivee du macro. Si ce test tombe, la
   matrice historique et ses 864 scenarios ne sont plus comparables.
2. **Echec ferme.** Absence, peremption, bloc incomplet : jamais de laissez-passer.
3. **Hors chemin critique.** La lecture reseau tourne dans un autre fil, donc un
   fournisseur lent ne retarde pas une decision.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

import pytest

from titanium.execution_sim.adaptive_features import build_features
from titanium.execution_sim.models import BookLevel, ExecutionIntent, MarketSnapshot, Side
from titanium.execution_sim.policies import PolicyContext
from titanium.features.builder import build_feats
from titanium.gates.confluence_gate import evaluate
from titanium.macro import (
    MACRO_BLOCK_KEYS,
    FileMacroSource,
    MacroCache,
    MacroCalendar,
    MacroFeed,
    MacroImpact,
    MacroPolicy,
    MacroRisk,
    MacroState,
    UnavailableMacroSource,
    build_source,
    currencies_of,
    macro_features,
    macro_risk,
    parse_events,
)
from titanium.macro.contracts import MacroEvent
from titanium.macro.gate import MACRO_POSTURE_KEY, macro_block
from titanium.macro.telemetry import macro_telemetry

MAINTENANT = datetime(2026, 9, 17, 17, 30, tzinfo=timezone.utc)
FOMC = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)


def politique(**surcharges) -> MacroPolicy:
    base = {
        "enabled": True, "provider": "file", "file": "peu-importe.json",
        "blackout_before_s": 900.0, "blackout_after_s": 300.0,
        "elevated_within_s": 3600.0, "ttl_s": 900.0,
    }
    base.update(surcharges)
    return MacroPolicy.from_mapping(base)


def evenement(titre: str = "FOMC", devise: str = "USD", quand: datetime = FOMC,
              impact: MacroImpact = MacroImpact.HIGH) -> MacroEvent:
    return MacroEvent(event_id=f"{titre}-{devise}", title=titre, currency=devise,
                      scheduled_at=quand, impact=impact, source="test")


def cache_avec(events: tuple[MacroEvent, ...], *, lu_a: datetime = MAINTENANT) -> MacroCache:
    # `lu_a` porte l'horodatage du PRODUCTEUR (`MacroCalendar.fetched_at`) : c'est
    # lui qui vieillit. L'instant de notre propre lecture vit dans le cache.
    cache = MacroCache()
    cache.publish(MacroCalendar(provider="test", fetched_at=lu_a, events=events))
    return cache


def risque(cache: MacroCache, *, quand: datetime = MAINTENANT, symbole=None,
           policy: MacroPolicy | None = None) -> MacroRisk:
    return macro_risk(now=quand, symbols=symbole, policy=policy or politique(), cache=cache)


# ═══════════════════════════ 1. non-regression ══════════════════════════════

def test_flux_eteint_aucun_bloc_macro_dans_les_features():
    """Le defaut est eteint : le dict de features est celui d'avant, sans cle."""
    flux = macro_features("EURUSD", now=MAINTENANT, policy=MacroPolicy.from_mapping({}))
    assert flux is None


def test_flux_eteint_la_porte_rend_la_decision_d_avant():
    """Sans bloc macro, la porte ignore le macro — au bit pres."""
    sans = evaluate(features_parfaites())
    assert sans.verdict == "ENTER"
    assert sans.code == "ENTER_CONFLUENCE"
    assert "macro" not in features_parfaites()


def test_flux_eteint_le_calendrier_perime_n_a_aucun_effet():
    """Un cache perime ne doit RIEN bloquer quand le flux est eteint."""
    cache = cache_avec((evenement(),), lu_a=MAINTENANT - timedelta(days=3))
    verdict = risque(cache, policy=MacroPolicy.from_mapping({}))
    assert verdict.state is MacroState.CLEAR
    assert verdict.allows_new_risk is True


# ═══════════════════════════ 2. contrats geles ══════════════════════════════

def test_un_evenement_gele_ne_se_modifie_pas():
    eve = evenement()
    with pytest.raises(FrozenInstanceError):
        eve.title = "autre"  # type: ignore[misc]


def test_impact_inconnu_leve_au_lieu_de_devenir_faible():
    """Sous-estimer une publication est precisement la faute a eviter."""
    with pytest.raises(ValueError, match="impact macro inconnu"):
        MacroImpact.parse("catastrophique")
    assert MacroImpact.parse("High") is MacroImpact.HIGH
    assert MacroImpact.parse("ÉLEVÉ") is MacroImpact.HIGH


def test_horodatage_naif_refuse():
    with pytest.raises(ValueError, match="fuseau"):
        MacroEvent(event_id="x", title="x", currency="USD",
                   scheduled_at=datetime(2026, 9, 17, 18, 0), impact=MacroImpact.HIGH)


def test_le_digest_ignore_l_instant_de_lecture():
    """Deux lectures du meme contenu partagent leur empreinte.

    Inclure `fetched_at` ferait diverger le digest a chaque rafraichissement et
    interdirait toute deduplication aval — le defaut `jepa_latency_ms` a nouveau.
    """
    a = MacroCalendar(provider="p", fetched_at=MAINTENANT, events=(evenement(),))
    b = MacroCalendar(provider="p", fetched_at=MAINTENANT + timedelta(hours=2),
                      events=(evenement(),))
    assert a.digest() == b.digest()
    c = MacroCalendar(provider="p", fetched_at=MAINTENANT,
                      events=(evenement(titre="CPI"),))
    assert a.digest() != c.digest()


def test_devises_deduite_du_symbole():
    assert currencies_of("EURUSD") == frozenset({"EUR", "USD"})
    assert currencies_of("XAUUSD") == frozenset({"XAU", "USD"})
    assert currencies_of("US500") == frozenset()


# ═══════════════════════════ 3. politique ═══════════════════════════════════

def test_les_cles_inconnues_levent_et_les_commentaires_sont_ignores():
    with pytest.raises(ValueError, match="cles macro inconnues"):
        MacroPolicy.from_mapping({"blackout_befor_s": 60})
    assert MacroPolicy.from_mapping({"_note": "lisible"}).ttl_s == 900.0


def test_bornes_invalides_refusees():
    with pytest.raises(ValueError, match="ttl_s"):
        MacroPolicy.from_mapping({"ttl_s": 0})
    with pytest.raises(ValueError, match="score_blackout"):
        MacroPolicy.from_mapping({"score_blackout": 1.5})
    with pytest.raises(ValueError, match="negatif"):
        MacroPolicy.from_mapping({"blackout_before_s": -1})


def test_empreinte_de_politique_stable_et_sensible():
    a = politique().fingerprint()
    assert a == politique().fingerprint()
    assert a != politique(blackout_before_s=60.0).fingerprint()


def test_exemple_de_configuration_est_chargeable():
    """L'exemple livre doit rester copiable tel quel vers config/macro.json."""
    from pathlib import Path

    racine = Path(__file__).resolve().parent.parent
    donnees = json.loads((racine / "config" / "macro.example.json").read_text("utf-8"))
    assert MacroPolicy.from_mapping(donnees).enabled is False


# ═══════════════════════════ 4. cache ═══════════════════════════════════════

def test_un_echec_conserve_la_derniere_lecture():
    """On ne remplace jamais une lecture connue par une absence silencieuse."""
    cache = cache_avec((evenement(),))
    cache.record_failure("test", ValueError("reseau"))
    vue = cache.view()
    assert vue.calendar is not None
    assert vue.consecutive_failures == 1
    assert "reseau" in vue.last_error


def test_un_succes_remet_le_compteur_d_echecs_a_zero():
    cache = cache_avec((evenement(),))
    cache.record_failure("test", ValueError("1"))
    cache.record_failure("test", ValueError("2"))
    cache.publish(MacroCalendar(provider="test", fetched_at=MAINTENANT, events=()))
    vue = cache.view()
    assert vue.consecutive_failures == 0
    assert vue.total_failures == 2 and vue.total_successes == 2


def test_cache_thread_safe():
    cache = MacroCache()
    barriere = threading.Barrier(8)
    erreurs: list[BaseException] = []

    def travaille() -> None:
        try:
            barriere.wait()
            for _ in range(50):
                cache.publish(MacroCalendar(provider="t", fetched_at=MAINTENANT, events=()))
                cache.view()
        except BaseException as exc:  # noqa: BLE001
            erreurs.append(exc)

    fils = [threading.Thread(target=travaille) for _ in range(8)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    assert not erreurs
    assert cache.view().total_successes == 400


# ═══════════════════════════ 5. verdict de risque ═══════════════════════════

def test_aucune_donnee_refuse_le_risque_neuf():
    verdict = risque(MacroCache(), symbole="EURUSD")
    assert verdict.state is MacroState.UNKNOWN
    assert verdict.allows_new_risk is False
    assert verdict.score == 1.0


def test_un_calendrier_sans_aucun_evenement_est_inconnu_pas_serein():
    """CHANGEMENT VOLONTAIRE du 14/09 — ce test figeait l'inverse.

    Il s'appelait `test_calendrier_vide_et_frais_autorise` et affirmait qu'un
    calendrier frais mais VIDE rend CLEAR, donc `allows_new_risk=True`. C'etait
    un repli fail-OPEN : un flux qui publie zero evenement ne dit pas « rien a
    signaler », il dit « je n'ai rien lu ». La cle renommee, le schema muet et le
    producteur casse y ressemblent tous, a s'y meprendre, a une journee sereine.

    Zero evenement est donc UNKNOWN, exactement ce que `risk.py` et la matrice
    d'echec ferme du document promettaient deja (« un calendrier qu'on ne peut pas
    lire n'est pas un calendrier vide »).
    """
    verdict = risque(cache_avec(()), symbole="EURUSD")
    assert verdict.state is MacroState.UNKNOWN
    assert verdict.allows_new_risk is False
    assert verdict.score == 1.0
    assert "aucun evenement" in verdict.reasons[0]


def test_publication_imminente_autorise_mais_conservateur():
    verdict = risque(cache_avec((evenement(),)), symbole="EURUSD")
    assert verdict.state is MacroState.ELEVATED
    assert verdict.allows_new_risk is True
    assert verdict.conservative is True
    assert verdict.seconds_to_next == pytest.approx(1800.0)
    assert 0.35 <= verdict.score <= 1.0


def test_fenetre_de_gel_refuse_avant_et_apres():
    # TTL large : ces cas testent la FENETRE, pas la peremption, et une lecture
    # d'une heure pour juger une fenetre de 15 minutes n'aurait pas de sens.
    large = politique(ttl_s=86_400.0)
    cache = cache_avec((evenement(),))
    avant = risque(cache, quand=FOMC - timedelta(seconds=600), symbole="EURUSD", policy=large)
    assert avant.state is MacroState.BLACKOUT and avant.allows_new_risk is False
    apres = risque(cache, quand=FOMC + timedelta(seconds=120), symbole="EURUSD", policy=large)
    assert apres.state is MacroState.BLACKOUT
    hors = risque(cache, quand=FOMC + timedelta(seconds=400), symbole="EURUSD", policy=large)
    assert hors.state is MacroState.CLEAR


def test_lecture_perimee_refusee_et_distinguee_de_l_inconnu():
    cache = cache_avec((), lu_a=MAINTENANT - timedelta(hours=1))
    verdict = risque(cache, symbole="EURUSD")
    assert verdict.state is MacroState.STALE
    assert verdict.allows_new_risk is False
    assert "perime" in verdict.reasons[0]


def test_horodatage_futur_traite_comme_non_fiable():
    cache = cache_avec((), lu_a=MAINTENANT + timedelta(hours=2))
    verdict = risque(cache, symbole="EURUSD")
    assert verdict.state is MacroState.STALE


def test_seule_la_devise_du_symbole_compte():
    """Une publication EUR ne gele pas un symbole qui ne porte pas l'EUR."""
    cache = cache_avec((evenement("CPI", "EUR"),))
    assert risque(cache, symbole="USDJPY").state is MacroState.CLEAR
    assert risque(cache, symbole="EURUSD").state is MacroState.ELEVATED


def test_symbole_non_devinable_conserve_toutes_les_publications():
    """Fail-closed : ne pas savoir filtrer ne doit pas fabriquer un faux calme."""
    cache = cache_avec((evenement("CPI", "EUR"),))
    verdict = risque(cache, symbole="US500")
    assert verdict.state is MacroState.ELEVATED


def test_impact_sous_le_seuil_ignore():
    basse = evenement("Discours", impact=MacroImpact.LOW)
    cache = cache_avec((basse,))
    assert risque(cache, symbole="EURUSD").state is MacroState.CLEAR
    stricte = politique(min_impact=MacroImpact.LOW)
    assert risque(cache, symbole="EURUSD", policy=stricte).state is MacroState.ELEVATED


def test_la_fenetre_retient_l_evenement_le_plus_proche():
    proche = evenement("NFP", quand=FOMC + timedelta(seconds=60))
    loin = evenement("FOMC", quand=FOMC + timedelta(minutes=5))
    cache = cache_avec((loin, proche))
    verdict = risque(cache, quand=FOMC, symbole="USDJPY", policy=politique(ttl_s=86_400.0))
    assert "NFP" in verdict.reasons[0]


# ═══════════════════════════ 6. sources ═════════════════════════════════════

def test_lecture_de_fichier_deterministe(tmp_path):
    fichier = tmp_path / "calendrier.json"
    fichier.write_text(json.dumps({
        "retrieved_at": "2026-09-17T17:30:00+00:00",
        "events": [
            {"title": "FOMC", "currency": "USD",
             "scheduled_at": "2026-09-17T18:00:00+00:00", "impact": "High"},
        ],
    }), encoding="utf-8")
    source = FileMacroSource(fichier)
    premier, deuxieme = source.fetch(), source.fetch()
    assert len(premier.events) == 1
    assert premier.digest() == deuxieme.digest()


def test_la_fraicheur_vient_du_producteur_pas_de_l_instant_de_lecture(tmp_path):
    """A. Un producteur mort qui laisse son fichier lisible doit rendre STALE.

    Le fichier est ecrit une fois puis plus jamais touche : si la source
    estampait `fetched_at` avec `now()`, ce calendrier serait relu « frais » a
    chaque poll et l'etat STALE ne se declencherait jamais.
    """
    fige = datetime.now(timezone.utc) - timedelta(hours=2)
    fichier = tmp_path / "fige.json"
    fichier.write_text(json.dumps({
        "retrieved_at": fige.isoformat(),
        "events": [{"title": "FOMC", "currency": "USD", "impact": "High",
                    "scheduled_at": (fige + timedelta(days=1)).isoformat()}],
    }), encoding="utf-8")

    calendrier = FileMacroSource(fichier).fetch()
    assert calendrier.fetched_at == fige, "horodatage du producteur, pas de la lecture"

    cache = MacroCache()
    cache.publish(calendrier)
    verdict = risque(cache, quand=datetime.now(timezone.utc))
    assert verdict.state is MacroState.STALE
    assert verdict.allows_new_risk is False
    assert verdict.data_age_s is not None and verdict.data_age_s > 3600.0


def test_l_horodatage_du_producteur_livre_est_lu(tmp_path):
    """A. Le producteur livre publie `calendarRisk.evaluated_at` (v14.macro.snapshot/1)."""
    quand = datetime.now(timezone.utc) - timedelta(minutes=30)
    fichier = tmp_path / "snapshot.json"
    fichier.write_text(json.dumps({
        "schema": "v14.macro.snapshot/1",
        "calendarRisk": {"state": "CLEAR", "reason": "NO_NEARBY_EVENT", "score": 12,
                         "next_event_id": None, "next_event_at": None,
                         "evaluated_at": quand.isoformat()},
        "events": [],
    }), encoding="utf-8")
    assert FileMacroSource(fichier).fetch().fetched_at == quand


def test_une_charge_utile_sans_horodatage_producteur_est_un_echec(tmp_path):
    """A. Un age inconnu n'est pas une fraicheur : on leve au lieu de supposer."""
    fichier = tmp_path / "muet.json"
    fichier.write_text(json.dumps({"events": [
        {"title": "FOMC", "currency": "USD", "impact": "High",
         "scheduled_at": "2026-09-17T18:00:00+00:00"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="horodatage producteur"):
        FileMacroSource(fichier).fetch()
    # Le cache n'a rien recu : le risque neuf reste refuse, aucun laissez-passer.
    assert risque(MacroCache()).allows_new_risk is False


def test_le_calendrier_vide_ne_se_confond_pas_avec_le_perime():
    """B + l'ancien contrat. « Je n'ai rien lu » et « c'est vieux » restent distincts."""
    frais = risque(cache_avec(())).state
    perime = risque(cache_avec((), lu_a=MAINTENANT - timedelta(hours=1))).state
    assert frais is MacroState.UNKNOWN
    assert perime is MacroState.STALE


def test_une_ligne_illisible_invalide_tout_le_calendrier():
    """Ignorer la ligne cassee produirait un calendrier plus vide, donc moins d'alertes."""
    with pytest.raises(ValueError, match="evénement macro #1|evenement macro #1"):
        parse_events([
            {"title": "OK", "currency": "USD",
             "scheduled_at": "2026-09-17T18:00:00+00:00", "impact": "High"},
            {"title": "CASSE", "currency": "", "scheduled_at": "n'importe quoi"},
        ], provider="test")


def test_impact_absent_refuse():
    with pytest.raises(ValueError, match="impact"):
        parse_events([{"title": "x", "currency": "USD",
                       "scheduled_at": "2026-09-17T18:00:00+00:00"}], provider="test")


def test_horodatage_naif_localise_par_la_politique():
    """« 14:30 » sans zone n'est pas 14:30 UTC : c'est le fuseau declare."""
    evenements = parse_events(
        [{"title": "x", "currency": "USD", "scheduled_at": "2026-09-17T14:00:00",
          "impact": "High"}],
        provider="test", naive_tz="UTC",
    )
    assert evenements[0].scheduled_at == datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


def test_fuseau_inconnu_leve():
    with pytest.raises(ValueError, match="fuseau"):
        parse_events([{"title": "x", "currency": "USD",
                       "scheduled_at": "2026-09-17T14:00:00", "impact": "High"}],
                     provider="test", naive_tz="Mars/Olympus")


def test_fichier_absent_leve(tmp_path):
    with pytest.raises(ValueError, match="introuvable"):
        FileMacroSource(tmp_path / "absent.json").fetch()


def test_fournisseur_par_defaut_echoue_ferme():
    """Un systeme neuf doit dire « je ne sais pas », pas « tout va bien »."""
    source = build_source(MacroPolicy.from_mapping({}))
    assert isinstance(source, UnavailableMacroSource)
    with pytest.raises(ValueError):
        source.fetch()
    with pytest.raises(ValueError, match="fournisseur macro inconnu"):
        build_source(politique(provider="magique"))


# ═══════════════════════ 6bis. fournisseur HTTP ════════════════════════════
#
# Le chemin reseau etait livre mais jamais parcouru : cinquante-trois tests
# couvraient le fichier, l'horloge et le cache, et AUCUN ne construisait
# `HttpMacroSource`. Trois pannes y sont donc exercees ici — cle absente, delai
# depasse, charge utile illisible — et chacune doit finir en refus.

class FauxRequests:
    """Double minimal de `requests` : on n'exerce QUE ce que le code utilise."""

    def __init__(self, reponse=None, panne: Exception | None = None) -> None:
        self.appels: list[tuple[str, dict]] = []
        self.reponse = reponse
        self.panne = panne

    def get(self, url: str, **kwargs):
        self.appels.append((url, kwargs))
        if self.panne is not None:
            raise self.panne
        return self.reponse


class Reponse:
    def __init__(self, payload, *, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class DelaiDepasse(Exception):
    """Meme nom et meme role que `requests.exceptions.Timeout`."""


def source_http(monkeypatch, faux: FauxRequests, *,
                api_key_env: str = "V14_MACRO_API_KEY", timeout_s: float = 8.0):
    from titanium.macro.sources import HttpMacroSource

    monkeypatch.setitem(sys.modules, "requests", faux)
    return HttpMacroSource("https://exemple.test/calendrier", api_key_env=api_key_env,
                           timeout_s=timeout_s)


def payload_valide(*, dans_minutes: int = 10) -> dict:
    quand = datetime.now(timezone.utc) + timedelta(minutes=dans_minutes)
    return {"retrieved_at": datetime.now(timezone.utc).isoformat(),
            "events": [{"title": "FOMC", "currency": "USD",
                        "scheduled_at": quand.isoformat(), "impact": "High"}]}


def test_http_cle_absente_echoue_sans_toucher_le_reseau(monkeypatch):
    """Le nom de la variable manquante suffit a reparer ; l'appel, lui, n'a pas lieu."""
    monkeypatch.delenv("V14_MACRO_API_KEY", raising=False)
    faux = FauxRequests(Reponse(payload_valide()))
    source = source_http(monkeypatch, faux)

    with pytest.raises(ValueError, match="V14_MACRO_API_KEY"):
        source.fetch()
    assert faux.appels == [], "aucune requete ne doit partir sans cle"

    # Echec ferme : le cache n'a rien recu, donc le risque neuf est refuse.
    cache = MacroCache()
    flux = MacroFeed(source, cache, policy=politique())
    assert asyncio.run(flux.refresh_once()) is False
    assert cache.view().has_data is False
    verdict = risque(cache)
    assert verdict.state is MacroState.UNKNOWN
    assert verdict.allows_new_risk is False
    porte = evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0)))
    assert porte.verdict == "BLOCK"
    assert porte.code == "BLOCK_MACRO_BLACKOUT"


def test_http_delai_depasse_devient_une_panne_de_source(monkeypatch):
    """Un fournisseur muet ne doit ni bloquer la boucle ni effacer la lecture connue."""
    monkeypatch.setenv("V14_MACRO_API_KEY", "cle-de-test")
    faux = FauxRequests(panne=DelaiDepasse("read timeout"))
    source = source_http(monkeypatch, faux, timeout_s=2.5)
    cache = cache_avec((evenement(),))
    flux = MacroFeed(source, cache, policy=politique())

    assert asyncio.run(flux.refresh_once()) is False
    assert len(faux.appels) == 1
    assert faux.appels[0][1]["timeout"] == 2.5, "le delai doit etre celui de la politique"
    vue = cache.view()
    assert vue.has_data is True, "la derniere lecture connue survit a la panne"
    assert vue.total_failures == 1
    assert "DelaiDepasse" in vue.last_error

    # La lecture survit, mais c'est la FRAICHEUR qui la juge : perimee, elle refuse.
    verdict = risque(cache, quand=MAINTENANT + timedelta(seconds=3600),
                     policy=politique(ttl_s=900.0))
    assert verdict.state is MacroState.STALE
    assert verdict.allows_new_risk is False
    assert evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0))).verdict == "BLOCK"


def test_http_charge_utile_illisible_n_enregistre_rien(monkeypatch):
    """Une ligne cassee invalide tout le calendrier : rien n'est publie, tout est refuse."""
    monkeypatch.setenv("V14_MACRO_API_KEY", "cle-de-test")
    casse = {"retrieved_at": datetime.now(timezone.utc).isoformat(),
             "events": [
        {"title": "OK", "currency": "USD",
         "scheduled_at": "2026-09-17T18:00:00+00:00", "impact": "High"},
        {"title": "CASSE", "currency": "", "impact": "High"},
    ]}
    faux = FauxRequests(Reponse(casse))
    source = source_http(monkeypatch, faux)
    cache = MacroCache()
    flux = MacroFeed(source, cache, policy=politique())

    assert asyncio.run(flux.refresh_once()) is False
    assert cache.view().has_data is False
    assert "CASSE" in cache.view().last_error
    verdict = risque(cache)
    assert verdict.allows_new_risk is False
    assert evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0))).verdict == "BLOCK"


def test_http_calendrier_valide_traverse_toute_la_chaine(monkeypatch):
    """Le chemin nominal existe, et va jusqu'a la porte : CLEAR autorise, gel refuse."""
    monkeypatch.setenv("V14_MACRO_API_KEY", "cle-de-test")
    faux = FauxRequests(Reponse(payload_valide(dans_minutes=10)))
    source = source_http(monkeypatch, faux)
    cache = MacroCache()
    flux = MacroFeed(source, cache, policy=politique())

    assert asyncio.run(flux.refresh_once()) is True
    assert cache.view().has_data is True

    # Le fournisseur HTTP horodate `fetched_at` avec l'horloge REELLE : on juge
    # donc a l'instant reel, pas a `MAINTENANT` qui est une date de laboratoire.
    maintenant = datetime.now(timezone.utc)
    verdict = risque(cache, quand=maintenant, symbole="EURUSD")
    assert verdict.state is MacroState.BLACKOUT
    assert verdict.allows_new_risk is False
    assert "FOMC" in verdict.reasons[0]
    porte = evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0)))
    assert porte.verdict == "BLOCK"
    assert porte.code == "BLOCK_MACRO_BLACKOUT"

    # Le meme fournisseur, une publication lointaine : le risque neuf repasse.
    faux.reponse = Reponse({"retrieved_at": datetime.now(timezone.utc).isoformat(),
                            "events": [{
        "title": "FOMC", "currency": "USD", "impact": "High",
        "scheduled_at": (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
    }]})
    assert asyncio.run(flux.refresh_once()) is True
    verdict = risque(cache, quand=datetime.now(timezone.utc), symbole="EURUSD")
    assert verdict.state is MacroState.CLEAR
    assert evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0))).verdict == "ENTER"


def test_http_erreur_serveur_devient_une_panne(monkeypatch):
    """Un 500 ne doit pas remonter en exception brute dans la boucle."""
    monkeypatch.setenv("V14_MACRO_API_KEY", "cle-de-test")
    faux = FauxRequests(Reponse(None, status=500))
    source = source_http(monkeypatch, faux)
    cache = MacroCache()
    assert asyncio.run(MacroFeed(source, cache, policy=politique()).refresh_once()) is False
    assert cache.view().has_data is False
    assert risque(cache).allows_new_risk is False


# ═══════════════════════════ 7. service de rafraichissement ════════════════

class _SourceEspionne:
    name = "espion"

    def __init__(self, panne: Exception | None = None) -> None:
        self.fils: list[int] = []
        self.panne = panne

    def fetch(self) -> MacroCalendar:
        self.fils.append(threading.get_ident())
        if self.panne is not None:
            raise self.panne
        return MacroCalendar(provider=self.name, fetched_at=datetime.now(timezone.utc),
                             events=(evenement(),))


def test_la_lecture_ne_bloque_pas_la_boucle_evenements():
    """Un `requests` bloquant doit tourner dans un fil, pas dans la boucle."""
    import asyncio

    source = _SourceEspionne()
    cache = MacroCache()
    flux = MacroFeed(source, cache, policy=politique())
    boucle_fil = threading.get_ident()
    assert asyncio.run(flux.refresh_once()) is True
    assert source.fils and source.fils[0] != boucle_fil
    assert cache.view().has_data is True


def test_une_panne_de_source_ne_leve_pas_et_est_enregistree():
    import asyncio

    cache = MacroCache()
    flux = MacroFeed(_SourceEspionne(ValueError("500")), cache, policy=politique())
    assert asyncio.run(flux.refresh_once()) is False
    assert "500" in cache.view().last_error


def test_le_delai_double_apres_echec_et_plafonne():
    cache = MacroCache()
    flux = MacroFeed(_SourceEspionne(), cache, policy=politique(refresh_s=10.0,
                                                              max_backoff_s=60.0))
    assert flux._delai_suivant() == 10.0
    for _ in range(3):
        cache.record_failure("x", ValueError("y"))
    assert flux._delai_suivant() == 60.0


# ═══════════════════════════ 8. integration porte ═══════════════════════════

def features_parfaites(**surcharges) -> dict:
    """Le setup LONG continuation parfait, comme celui des tests de la porte."""
    base = {
        "data_valid": True, "trend": 1, "setup_side": 1, "setup_family": "continuation",
        "on_sr_level": True, "fair_value": True, "liquidity": 1, "ote": 1, "candle": 1,
        "strengths": {"trend_sr": 1.0, "fair_value": 0.8, "liquidity": 0.9,
                      "ote_ob": 0.7, "candle_confirmed": 0.6},
        "emotion": {"filter_block": None, "stale": False, "confidence": 0.8,
                    "wait": False},
        "cost": {"edge_ok": True, "weekend_block": False},
    }
    base.update(surcharges)
    return base


def bloc(**surcharges) -> dict:
    base = {"allows_new_risk": True, "conservative": False, "state": "CLEAR", "score": 0.0}
    base.update(surcharges)
    return base


def test_le_gel_macro_bloque_l_entree():
    d = evaluate(features_parfaites(macro=bloc(allows_new_risk=False, state="BLACKOUT",
                                               score=1.0, conservative=True)))
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_MACRO_BLACKOUT"


def test_publication_imminente_fait_attendre():
    d = evaluate(features_parfaites(macro=bloc(conservative=True, state="ELEVATED",
                                               score=0.6)))
    assert d.verdict == "WAIT"
    assert d.code == "WAIT_MACRO_IMMINENT"


def test_bloc_macro_incomplet_bloque():
    d = evaluate(features_parfaites(macro={"conservative": False}))
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_MACRO_UNAVAILABLE"


def test_bloc_macro_nul_bloque():
    """Cle presente et nulle : le macro s'applique, et il est illisible."""
    d = evaluate(features_parfaites(macro=None))
    assert d.code == "BLOCK_MACRO_UNAVAILABLE"


def test_macro_peut_etre_force_dans_les_deux_sens():
    assert evaluate(features_parfaites(), require_macro=True).code == "BLOCK_MACRO_UNAVAILABLE"
    force_hors = features_parfaites(macro=bloc(allows_new_risk=False))
    assert evaluate(force_hors, require_macro=False).verdict == "ENTER"


def test_macro_ne_compense_jamais_un_pilier_absent():
    """Le macro est un moderateur : il ne peut pas faire entrer un setup incomplet.

    G1 est obligatoire quel que soit le quorum — c'est le pilier a retirer pour
    que le cas soit reellement incomplet.
    """
    d = evaluate(features_parfaites(on_sr_level=False, macro=bloc()))
    assert d.verdict == "BLOCK"
    assert d.code == "BLOCK_PILLAR_MISSING"


def test_le_verdict_macro_porte_ses_motifs():
    verdict = risque(cache_avec((evenement(),)), symbole="EURUSD")
    d = evaluate(features_parfaites(macro=macro_block(verdict, posture_scale_s=3600.0)))
    assert d.verdict == "WAIT"
    assert any("FOMC" in motif for motif in d.reasons)


# ═══════════════════════════ 9. integration execution ══════════════════════

def contexte_macro(bloc_macro: dict | None) -> PolicyContext:
    return PolicyContext(
        snapshot=MarketSnapshot(
            timestamp=MAINTENANT, symbol="EURUSD", bid=1.1000, ask=1.1002,
            bid_levels=(BookLevel(1.1000, 500.0),),
            ask_levels=(BookLevel(1.1002, 500.0),), volume=1000.0, volatility_bps=8.0,
        ),
        tick_size=0.0001, macro=bloc_macro,
    )


def intention() -> ExecutionIntent:
    return ExecutionIntent("m1", "EURUSD", Side.BUY, 1.0)


def test_le_contexte_d_arrivee_reste_neutre_sans_macro():
    """Sans bloc macro, le vecteur est EXACTEMENT celui d'avant le macro.

    La comparaison porte sur le contexte construit sans le champ `macro` du tout
    (le `replace` ci-dessous) : c'est la garantie de non-regression, et elle est
    verifiee sur le vecteur entier — pas sur trois champs recopies, qui ne
    prouvaient que leur propre presence.
    """
    avec_champ = build_features(intention(), contexte_macro(None))
    sans_champ = build_features(intention(), replace(contexte_macro(None), macro=None))
    assert avec_champ is not None
    assert avec_champ == sans_champ


def test_le_contexte_d_arrivee_refuse_de_planifier_en_gel():
    assert build_features(intention(), contexte_macro(bloc(allows_new_risk=False))) is None


def test_le_contexte_d_arrivee_refuse_un_bloc_incomplet():
    assert build_features(intention(), contexte_macro({"conservative": True})) is None


def test_l_attitude_conservatrice_laisse_planifier_et_c_est_la_porte_qui_attend():
    """`conservative` n'interdit pas de planifier : la porte en fait un WAIT.

    Le vecteur ne recopie pas l'attitude — il n'en ferait rien. Ce qui compte
    est verifie la ou elle decide : `evaluate` ci-dessus (WAIT_MACRO_IMMINENT,
    publication nommee).
    """
    f = build_features(intention(), contexte_macro(bloc(conservative=True, score=0.6)))
    assert f is not None
    verdict = evaluate(features_parfaites(macro=bloc(conservative=True, score=0.6)))
    assert verdict.verdict == "WAIT"
    assert verdict.code == "WAIT_MACRO_IMMINENT"


def test_le_bloc_de_la_porte_est_le_meme_objet_que_celui_du_contexte():
    """Une seule regle de completude, `MACRO_BLOCK_KEYS`, pour les deux lecteurs."""
    # Une publication lointaine : CLEAR, le seul etat qui execute. Le cas vide
    # n'en est plus un (voir `test_un_calendrier_sans_aucun_evenement_...`).
    conforme = macro_block(
        risque(cache_avec((evenement(quand=MAINTENANT + timedelta(hours=4)),)),
               symbole="EURUSD"),
        posture_scale_s=3600.0,
    )
    assert MACRO_BLOCK_KEYS.issubset(conforme)
    assert build_features(intention(), contexte_macro(conforme)) is not None


def test_le_builder_n_ajoute_rien_sans_symbole():
    import numpy as np
    import pandas as pd

    def bougies(n: int = 80) -> pd.DataFrame:
        index = pd.date_range("2026-03-02", periods=n, freq="1h", tz="UTC")
        base = 1.10 + np.cumsum(np.random.default_rng(7).normal(0, 0.0005, n))
        return pd.DataFrame({"open": base, "high": base + 0.0004, "low": base - 0.0004,
                             "close": base + 0.0001, "volume": np.full(n, 100.0)},
                            index=index)

    assert "macro" not in build_feats(bougies(), bougies())
    assert "macro" not in build_feats(bougies(), bougies(), symbol="EURUSD")


def test_le_builder_refuse_un_calendrier_illisible_sans_rien_avaler():
    import numpy as np
    import pandas as pd

    index = pd.date_range("2026-03-02", periods=80, freq="1h", tz="UTC")
    base = 1.10 + np.cumsum(np.random.default_rng(7).normal(0, 0.0005, 80))
    df = pd.DataFrame({"open": base, "high": base + 0.0004, "low": base - 0.0004,
                       "close": base + 0.0001, "volume": np.full(80, 100.0)}, index=index)
    feats = build_feats(df, df, macro=bloc(allows_new_risk=False, state="BLACKOUT"))
    assert feats["macro"]["allows_new_risk"] is False
    assert evaluate(feats).verdict == "BLOCK"


# ═══════════════════════════ 10. telemetrie ═════════════════════════════════

def test_les_jauges_sont_bornees_et_serialisables():
    policy = politique()
    cache = cache_avec((evenement(),))
    bloc = macro_telemetry(risque(cache, symbole="EURUSD"), policy=policy,
                           view=cache.view())
    assert 0 <= bloc["score_pct"] <= 100
    assert 0 <= bloc["freshness_pct"] <= 100
    assert 0 <= bloc["imminence_pct"] <= 100
    assert bloc["severity"] == "warn"
    assert bloc["label"] == "Prudence"
    assert json.loads(json.dumps(bloc))  # aucun objet non serialisable


def test_la_severite_distingue_autorise_de_refuse():
    policy = politique()
    clair = macro_telemetry(
        risque(cache_avec((evenement(quand=MAINTENANT + timedelta(hours=4)),)),
               symbole="EURUSD"),
        policy=policy,
    )
    gel = macro_telemetry(risque(cache_avec((evenement(),), lu_a=MAINTENANT - timedelta(days=1)),
                                 symbole="EURUSD"), policy=policy)
    assert clair["severity"] == "ok" and clair["allows_new_risk"] is True
    assert gel["severity"] == "crit" and gel["allows_new_risk"] is False
    assert gel["label"] in ("Perime", "Inconnu")


# ═══════════════════════════ 11. sonde tableau de bord ══════════════════════

def test_la_sonde_du_tableau_de_bord_ne_leve_jamais():
    from titanium.web import state

    bloc = state.macro()
    assert bloc["disponible"] is True
    assert "allows_new_risk" in bloc
    assert "severity" in bloc
    assert bloc["enabled"] is False  # flux eteint par defaut
    assert "macro" in state.state()


# ───────────── Posture d'execution : non degeneree la ou l'execution a lieu ─


def test_la_posture_publiee_est_non_degeneree_sur_le_chemin_qui_execute():
    """Le seul etat qui execute est CLEAR : la posture doit y vivre.

    Mesure du 14/09 : en CLEAR, ``score = 0`` et ``conservative = False`` par
    construction, et ELEVATED fait WAIT. Une posture tiree du score, ou coupee a
    l'horizon du veto, serait identiquement nulle partout ou l'execution a lieu.
    """
    regle = politique()
    lointain = risque(cache_avec((evenement(quand=MAINTENANT + timedelta(hours=4)),)))
    assert lointain.state is MacroState.CLEAR
    assert lointain.allows_new_risk is True
    assert lointain.conservative is False
    bloc_lointain = macro_block(lointain, posture_scale_s=regle.elevated_within_s)
    assert bloc_lointain[MACRO_POSTURE_KEY] > 0.0

    proche = risque(cache_avec((evenement(quand=MAINTENANT + timedelta(hours=1, minutes=30)),)))
    assert proche.state is MacroState.CLEAR
    bloc_proche = macro_block(proche, posture_scale_s=regle.elevated_within_s)
    assert bloc_proche[MACRO_POSTURE_KEY] > bloc_lointain[MACRO_POSTURE_KEY]
    assert bloc_proche[MACRO_POSTURE_KEY] < 1.0

    # CHANGEMENT VOLONTAIRE du 14/09 : `vide` n'est plus un CLEAR serein mais un
    # UNKNOWN. La posture reste nulle, mais parce que le risque est REFUSE, pas
    # parce que le calendrier est calme — la nuance est desormais portee par
    # l'etat, verifie ici, au lieu d'etre invisible.
    vide = risque(cache_avec(()))
    assert vide.state is MacroState.UNKNOWN
    assert macro_block(vide, posture_scale_s=regle.elevated_within_s)[MACRO_POSTURE_KEY] == 0.0


def test_la_posture_ne_deplace_ni_le_veto_ni_ses_cles():
    """L'echelle de posture ne touche aucun des deux booleens du veto."""
    gele = risque(cache_avec((evenement(quand=MAINTENANT + timedelta(minutes=5)),)))
    reference = macro_block(gele, posture_scale_s=3600.0)
    autre = macro_block(gele, posture_scale_s=1.0)
    assert MACRO_BLOCK_KEYS.issubset(reference)
    for cle in ("allows_new_risk", "conservative", "state", "score"):
        assert reference[cle] == autre[cle]
    assert reference["allows_new_risk"] is False
    assert reference["conservative"] is True
