"""L'IRM observe la boucle — elle ne doit jamais pouvoir agir sur elle.

Deux familles de tests :

1. **Garde-fous structurels.** L'IRM tourne à côté d'une boucle ARMÉE sur un
   compte réel-démo. Une régression qui lui donnerait un chemin d'ordre, un
   appel LLM ou une prise du verrou MT5 serait invisible à l'oeil et coûteuse.
   On l'interdit par analyse du module lui-même, pas par relecture.

2. **Robustesse de lecture.** Les journaux sont écrits *pendant* qu'on les lit.
   Ligne tronquée, fichier absent, JSON à moitié écrit, fichier qui rétrécit :
   aucun de ces cas normaux ne doit lever.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tools import irm

RACINE = Path(__file__).resolve().parent.parent
SOURCE = RACINE / "tools" / "irm.py"


# ── 1. Garde-fous structurels ──────────────────────────────────────────────

#: Tout ce qui passerait un ordre, bougerait un stop ou toucherait le terminal.
APPELS_INTERDITS = {
    "order_send", "positions_modify", "place_market_order", "initialize",
    "shutdown", "symbol_select", "copy_rates_from_pos", "market_book_add",
}

#: Modules qui, importés ici, mettraient l'observateur dans le chemin critique :
#: MetaTrader5 prendrait le verrou que la boucle armée utilise pour trader, et
#: un client LLM ferait payer un appel réseau à chaque rafraîchissement.
IMPORTS_INTERDITS = {
    "MetaTrader5", "titanium.execution", "titanium.execution.mt5_executor",
    "titanium.data.mt5_vendor", "titanium.deliberation",
    "tradingagents.llm_clients", "openai", "anthropic",
}


def _arbre() -> ast.Module:
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_aucun_appel_de_trading_ni_de_terminal():
    """Un ordre passé depuis une page d'observation serait un défaut muet."""
    noms = set()
    for noeud in ast.walk(_arbre()):
        if isinstance(noeud, ast.Call):
            cible = noeud.func
            if isinstance(cible, ast.Name):
                noms.add(cible.id)
            elif isinstance(cible, ast.Attribute):
                noms.add(cible.attr)

    interdits = noms & APPELS_INTERDITS
    assert not interdits, f"appels interdits dans irm.py : {sorted(interdits)}"


def test_aucun_import_du_chemin_critique():
    """L'IRM ne doit disputer ni le verrou MT5 ni un budget LLM à la boucle."""
    importes = set()
    for noeud in ast.walk(_arbre()):
        if isinstance(noeud, ast.Import):
            importes.update(alias.name for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module:
            importes.add(noeud.module)

    interdits = {m for m in importes
                 if any(m == i or m.startswith(i + ".") for i in IMPORTS_INTERDITS)}
    assert not interdits, f"imports interdits dans irm.py : {sorted(interdits)}"


def test_le_serveur_n_expose_que_des_lectures():
    """Aucun verbe d'écriture : pas de do_POST, do_PUT, do_DELETE, do_PATCH."""
    methodes = {n.name for n in ast.walk(_arbre())
                if isinstance(n, ast.FunctionDef)}
    ecritures = {m for m in methodes
                 if m in ("do_POST", "do_PUT", "do_DELETE", "do_PATCH")}
    assert not ecritures, f"verbes d'ecriture exposes : {sorted(ecritures)}"


def test_le_port_ne_heurte_aucun_service_connu():
    """8090 V12, 8095 tableau de bord, 8097 terminal collab, 8766/8770 hub."""
    assert irm.PORT not in (3000, 8080, 8090, 8095, 8097, 8765, 8766, 8770, 4750)


# ── 2. Robustesse de lecture ───────────────────────────────────────────────

def test_journal_absent_ne_leve_pas(tmp_path):
    journal = irm.Journal(tmp_path / "jamais_ecrit.ndjson")
    assert journal.nouveautes() == []


def test_journal_ne_rend_pas_de_ligne_tronquee(tmp_path):
    """Une écriture en cours coupe la dernière ligne : elle est rendue entière
    au passage suivant, jamais en deux moitiés."""
    chemin = tmp_path / "vivant.ndjson"
    chemin.write_bytes(b'{"at":"1","v":1}\n{"at":"2","v":2}\n{"at":"3","v"')

    journal = irm.Journal(chemin)
    journal.decalage = 0
    premier = journal.nouveautes()
    assert [e["v"] for e in premier] == [1, 2]

    # La ligne se complète : elle apparaît alors, et une seule fois.
    with chemin.open("ab") as flux:
        flux.write(b':3}\n')
    assert [e["v"] for e in journal.nouveautes()] == [3]


def test_journal_qui_retrecit_repart_du_debut(tmp_path):
    """Une purge ou une rotation ne doit pas rendre des octets pris au milieu
    d'une ligne — ce qui produirait du JSON invalide en boucle."""
    chemin = tmp_path / "tourne.ndjson"
    chemin.write_bytes(b'{"at":"1","v":1}\n' * 50)
    journal = irm.Journal(chemin)
    journal.decalage = 0
    journal.nouveautes()

    chemin.write_bytes(b'{"at":"9","v":9}\n')
    assert [e["v"] for e in journal.nouveautes()] == [9]


def test_ligne_illisible_est_sautee_sans_tuer_le_lot(tmp_path):
    chemin = tmp_path / "abime.ndjson"
    chemin.write_bytes(b'{"at":"1","v":1}\n<<not json>>\n{"at":"2","v":2}\n')
    journal = irm.Journal(chemin)
    journal.decalage = 0
    assert [e["v"] for e in journal.nouveautes()] == [1, 2]


def test_json_tronque_rend_un_dict_vide(tmp_path):
    """Le battement est réécrit pendant qu'on le lit : la course est normale."""
    chemin = tmp_path / "battement.json"
    chemin.write_text('{"at": "2026-09-05T08:00:00+00:00", "equ', encoding="utf-8")
    assert irm._lire_json(chemin) == {}


def test_json_liste_rend_un_dict_vide(tmp_path):
    """Un JSON valide mais du mauvais type ne doit pas casser les appelants."""
    chemin = tmp_path / "liste.json"
    chemin.write_text("[1, 2, 3]", encoding="utf-8")
    assert irm._lire_json(chemin) == {}


# ── 3. Lecture de l'état ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("age_s", "attendu"),
    [(10.0, "en_cours"), (59.0, "en_cours"), (181.0, "figee"), (3600.0, "figee")],
)
def test_etat_de_la_boucle_selon_l_age_du_battement(monkeypatch, tmp_path,
                                                    age_s, attendu):
    """Trois états, pas deux : `figee` est la valeur qu'un scan de processus ne
    sait pas produire, et c'est celle qui compte."""
    from datetime import datetime, timedelta, timezone

    instant = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    (tmp_path / "loop_heartbeat.json").write_text(json.dumps({
        "at": instant.isoformat(), "intervalle": 60.0, "armed": True,
        "equity": 1000.0, "stats": {"tours": 5},
    }), encoding="utf-8")
    (tmp_path / "positions.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    assert irm._pouls()["etat"] == attendu


def test_battement_absent_dit_arretee_et_non_en_cours(monkeypatch, tmp_path):
    """Le sens sûr de l'erreur : un observateur muet annonce l'arrêt, jamais
    une activité qu'il n'a pas constatée."""
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    pouls = irm._pouls()
    assert pouls["etat"] == "arretee"
    assert pouls["positions"] == []


def test_les_organes_relevent_l_entonnoir_sans_le_recalculer(monkeypatch, tmp_path):
    (tmp_path / "loop_heartbeat.json").write_text(json.dumps({
        "at": "2026-09-05T08:00:00+00:00", "intervalle": 60.0,
        "stats": {"tunnel": {
            "flow": {"catalogue": 1000, "selectionnes": 600, "portables": 400,
                     "fx_illiquides_ecartes": 400},
            "portability_refusal": {"COUT_SPREAD": 200},
            "features": {"LISIBLE": 380},
            "gate_verdict": {"ENTER": 40, "BLOCK": 340},
            "pillar_missing": {"G4_OTE_OB_MISSING": 300},
            "post_enter_refusal": {"INTELLIGENCE_GATE": 25, "EXECUTION": 5},
            "support_passed": {"S0": 10, "S2": 5},
        }},
    }), encoding="utf-8")
    monkeypatch.setattr(irm, "RESULTS", tmp_path)

    chaine = {b["cle"]: b for b in irm._organes()["chaine"]}

    assert chaine["catalogue"]["entre"] == 1000
    assert chaine["catalogue"]["sort"] == 600
    assert chaine["catalogue"]["perdus"] == 400
    assert chaine["portes"]["entre"] == 380
    assert chaine["portes"]["sort"] == 40
    # La mémoire retire ses propres refus, et eux seuls.
    assert chaine["memoire"]["sort"] == 15
    # Sans compteur d'envois, les dix candidats restants ne sont pas des ordres.
    assert chaine["execution"]["sort"] is None
    assert irm._organes()["entry_accounting"]["status"] == "UNAVAILABLE"


def test_un_etage_non_compte_rend_none_et_non_zero(monkeypatch, tmp_path):
    """Zéro dirait « cet organe n'a rien laissé passer ». None dit « la boucle
    ne compte pas cet étage ». Les confondre ferait conclure faux."""
    (tmp_path / "loop_heartbeat.json").write_text(
        json.dumps({"at": "2026-09-05T08:00:00+00:00", "stats": {"tunnel": {}}}),
        encoding="utf-8")
    monkeypatch.setattr(irm, "RESULTS", tmp_path)

    chaine = {b["cle"]: b for b in irm._organes()["chaine"]}
    assert chaine["politique"]["entre"] is None
    assert chaine["politique"]["sort"] is None
    assert chaine["politique"]["perdus"] is None


def test_chaque_code_de_refus_connu_est_rattache_a_un_organe():
    """Un refus non attribué disparaîtrait du flux au lieu d'être visible."""
    organes = {cle for cle, _, _ in irm.ORGANES}
    for code, organe in irm.ORGANE_DU_REFUS.items():
        assert organe in organes, f"{code} pointe vers un organe inconnu : {organe}"


# ── 4. Le tampon commun ────────────────────────────────────────────────────

def test_deux_lecteurs_voient_le_meme_flux(monkeypatch, tmp_path):
    """Les décalages d'un Journal sont un état partagé.

    Si chaque client SSE draînait les journaux lui-même, le premier
    consommerait les octets et le second afficherait une page vide — deux
    onglets de la même page montreraient deux vérités différentes. Le
    collecteur lit une fois, tout le monde rend le même tampon.
    """
    irm._TAMPON.clear()
    (tmp_path / "refus_live.ndjson").write_text(
        '{"at":"2026-09-05T08:00:01+00:00","code":"COUT_SPREAD",'
        '"symbole":"EURUSD","detail":"","side":1,"piliers":2}\n',
        encoding="utf-8")

    monkeypatch.setattr(irm, "JOURNAUX", {
        "refus": irm.Journal(tmp_path / "refus_live.ndjson", "at"),
    })
    irm.JOURNAUX["refus"].decalage = 0

    # Un seul drainage, comme le fait le collecteur.
    irm._TAMPON.extend(irm._lire_journaux())

    premier = irm._evenements()
    second = irm._evenements()
    assert premier == second
    assert len(premier) == 1
    assert premier[0]["symbole"] == "EURUSD"
    assert premier[0]["organe"] == "portabilite"


def test_le_tampon_est_borne(monkeypatch):
    """Un flux vivant sans plafond ferait grossir le serveur sans fin."""
    irm._TAMPON.clear()
    irm._TAMPON.extend({"a": str(i)} for i in range(5000))
    assert len(irm._TAMPON) == irm._TAMPON.maxlen


# ── 5. Les vues cognitives ─────────────────────────────────────────────────

def test_le_cortex_est_un_organe_de_la_chaine():
    """Il était fondu dans « Mémoire d'edge », alors que c'est lui qui raisonne."""
    cles = [cle for cle, _, _ in irm.ORGANES]
    assert "cortex" in cles
    assert cles.index("memoire") < cles.index("cortex") < cles.index("execution")


def test_la_sante_du_cortex_est_deduite_des_verdicts(monkeypatch, tmp_path):
    """Le disjoncteur vit dans la mémoire du travailleur, pas sur disque.

    Lire la source du dernier verdict est la seule mesure honnête depuis
    l'extérieur — elle dit la vérité même si le travailleur se trompe.
    """
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    chemin = tmp_path / "avis_rendus.ndjson"

    chemin.write_text(json.dumps({
        "symbol": "BTCUSD", "rendu_a": "2026-09-05T12:00:00+00:00",
        "source": "hermes-cortex/claude-opus-5", "rating": "ALLOW",
    }) + "\n", encoding="utf-8")
    assert irm._cortex()["sante"] == "hermes"
    assert irm._cortex()["detail"] == "claude-opus-5"

    with chemin.open("a", encoding="utf-8") as flux:
        flux.write(json.dumps({
            "symbol": "ETHUSD", "rendu_a": "2026-09-05T12:01:00+00:00",
            "source": "cortex-local-fallback", "rating": "WAIT",
        }) + "\n")
    assert irm._cortex()["sante"] == "repli"


def test_aucun_verdict_ne_se_lit_pas_comme_un_cortex_sain(monkeypatch, tmp_path):
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    assert irm._cortex()["sante"] == "muet"


def test_la_memoire_montre_le_rentable_en_premier(monkeypatch, tmp_path):
    """C'est la question posée : ce contexte a-t-il déjà gagné ?"""
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    (tmp_path / "live_memory.ndjson").write_text("\n".join(json.dumps(x) for x in [
        {"symbol": "A", "context": "A|long|c|3p", "action": "BLOCK",
         "samples": 200, "expectancy_r": -0.4, "profit_factor": 0.4, "at": "t1"},
        {"symbol": "B", "context": "B|short|r|3p", "action": "ALLOW",
         "samples": 186, "expectancy_r": 0.261, "profit_factor": 1.77, "at": "t2"},
    ]) + "\n", encoding="utf-8")

    vue = irm._memoire()
    assert vue["contextes"][0]["contexte"] == "B|short|r|3p"
    assert vue["n_rentables"] == 1
    assert vue["n_total"] == 2


def test_un_contexte_ne_figure_qu_une_fois(monkeypatch, tmp_path):
    """Il est relu à chaque tour ; la table répéterait quinze fois le même actif."""
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    ligne = {"symbol": "A", "context": "A|long|c|3p", "action": "BLOCK",
             "samples": 10, "expectancy_r": -0.1, "profit_factor": 0.9, "at": "t"}
    (tmp_path / "live_memory.ndjson").write_text(
        "\n".join(json.dumps(ligne) for _ in range(15)) + "\n", encoding="utf-8")
    assert irm._memoire()["n_total"] == 1


def test_une_source_tombee_reste_visible_a_zero(monkeypatch, tmp_path):
    """Une source omise disparaît en silence ; une source à zéro se voit.

    C'est la seule façon de constater qu'un fournisseur de faits ne répond
    plus — sinon la liste rétrécit sans que personne le remarque.
    """
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    (tmp_path / "avis_rendus.ndjson").write_text(json.dumps({
        "symbol": "A", "rendu_a": "t", "sources": ["ECB", "CoinGecko"],
    }) + "\n", encoding="utf-8")

    par_nom = {s["nom"]: s["n"] for s in irm._injections()}
    for connue in irm.SOURCES_CONNUES:
        assert connue in par_nom, f"{connue} a disparu de la liste"
    assert par_nom["ECB"] == 1
    assert par_nom["FRED"] == 0


def test_une_revue_par_ticket_et_la_plus_recente(monkeypatch, tmp_path):
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    (tmp_path / "position_review_verdicts.ndjson").write_text(
        "\n".join(json.dumps(x) for x in [
            {"symbol": "A", "ticket": "1", "state": "CALM",
             "rendered_at": "2026-09-05T10:00:00+00:00"},
            {"symbol": "A", "ticket": "1", "state": "FEAR",
             "rendered_at": "2026-09-05T11:00:00+00:00"},
        ]) + "\n", encoding="utf-8")

    revues = irm._revues()
    assert len(revues) == 1
    assert revues[0]["etat"] == "FEAR", "la revue la plus recente doit gagner"


def test_les_vues_cognitives_ne_levent_jamais_sans_fichier(monkeypatch, tmp_path):
    """Journaux absents = système jeune, pas erreur."""
    monkeypatch.setattr(irm, "RESULTS", tmp_path)
    assert irm._cortex()["verdicts"] == []
    assert irm._memoire()["contextes"] == []
    assert irm._revues() == []
    assert all(s["n"] == 0 for s in irm._injections())
