"""Garde-fous de l'archive du carnet L2 Binance.

Quatre propriétés décident si cette archive vaudra quelque chose dans six mois :

  1. le module reste public et en lecture seule — aucune clé, aucun ordre ;
  2. une rupture de séquence est ECRITE, jamais avalée en silence ;
  3. le côté de l'agresseur est le bon — l'inverser retournerait tout
     déséquilibre de flux sans qu'aucun test de forme ne s'en aperçoive ;
  4. prix et quantités restent des chaînes, sans arrondi d'écriture.

La deuxième est celle qui distingue une archive d'un tas de lignes : un carnet
reconstruit sur un trou non signalé est faux et paraît sain.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from tools import enregistreur_carnet_binance as ecb

INTERDITS = frozenset({
    "order_send", "new_order", "create_order", "hmac", "sha256",
    "api_key", "apiKey", "secret", "signature",
})


# ─────────────────────────────────────────── sécurité

def test_le_module_ne_signe_ni_ne_passe_aucun_ordre():
    """Analysé à l'AST : la docstring cite les interdits pour les expliquer,
    un grep textuel s'y prendrait les pieds."""
    arbre = ast.parse(Path(ecb.__file__).read_text(encoding="utf-8"))
    utilises = (
        {n.attr for n in ast.walk(arbre) if isinstance(n, ast.Attribute)}
        | {n.id for n in ast.walk(arbre) if isinstance(n, ast.Name)}
        | {n.arg for n in ast.walk(arbre) if isinstance(n, ast.arg)}
    )
    fautifs = sorted(utilises & INTERDITS)
    assert not fautifs, f"appels interdits dans un collecteur public : {fautifs}"


def test_les_hotes_par_defaut_sont_ceux_des_donnees_de_marche():
    """`binance.vision` ne sert que des données publiques : aucun point d'entrée
    de trading n'y répond. L'absence d'ordre devient structurelle."""
    assert ecb.HOTE_REST == "https://data-api.binance.vision"
    assert ecb.HOTE_WS == "wss://data-stream.binance.vision"


def test_le_flux_demande_le_carnet_et_les_transactions_de_chaque_symbole():
    url = ecb.url_flux(("BTCUSDT", "ETHUSDT"))
    assert "btcusdt@depth@100ms" in url and "btcusdt@aggTrade" in url
    assert "ethusdt@depth@100ms" in url and "ethusdt@aggTrade" in url
    assert "api" not in url.split("?")[0].replace("data-stream", "")


# ─────────────────────────────────────────── séquence

def test_les_differentiels_anterieurs_a_linstantane_sont_ignores():
    """Rattrapage normal du protocole : ce n'est pas une anomalie, et les
    compter comme des trous déclencherait une resynchronisation sans fin."""
    sequence = ecb.SequenceCarnet()
    sequence.amorcer(100)
    assert sequence.verdict(90, 95) == "ignore"
    assert sequence.verdict(98, 100) == "ignore"


def test_le_premier_differentiel_retenu_doit_encadrer_linstantane():
    sequence = ecb.SequenceCarnet()
    sequence.amorcer(100)
    assert sequence.verdict(99, 105) == "applique"
    assert sequence.dernier_id == 105


def test_un_differentiel_qui_saute_lamorce_est_un_trou():
    """Si le premier différentiel commence après ``lastUpdateId + 1``, les mises
    à jour intermédiaires sont perdues : le carnet serait faux dès la première
    ligne."""
    sequence = ecb.SequenceCarnet()
    sequence.amorcer(100)
    assert sequence.verdict(103, 110) == "trou"


def test_la_chaine_doit_etre_continue_apres_lamorce():
    sequence = ecb.SequenceCarnet()
    sequence.amorcer(100)
    assert sequence.verdict(99, 105) == "applique"
    assert sequence.verdict(106, 110) == "applique"
    assert sequence.verdict(112, 115) == "trou", "un saut de numero doit etre vu"


def test_un_differentiel_deja_vu_est_ignore_et_ne_casse_pas_la_chaine():
    """Une reconnexion peut renvoyer des différentiels déjà écrits ; les traiter
    comme des trous ferait boucler la resynchronisation."""
    sequence = ecb.SequenceCarnet()
    sequence.amorcer(100)
    sequence.verdict(99, 105)
    assert sequence.verdict(101, 104) == "ignore"
    assert sequence.verdict(106, 108) == "applique"


# ─────────────────────────────────────────── mise en ligne

def test_le_cote_agresseur_suit_le_drapeau_maker():
    """``m`` vrai = l'acheteur tenait le marché, donc l'agresseur est le vendeur.

    C'est le seul champ dérivé de l'archive, et le seul dont l'inversion serait
    invisible : les volumes resteraient justes, le déséquilibre serait retourné.
    """
    brut = {"a": 1, "p": "63000.10", "q": "0.5", "T": 1000.0, "E": 1000.0, "m": True}
    vendeur = ecb.ligne_trade("BTCUSDT", brut, 1050.0)
    assert vendeur["cote_agresseur"] == "vendeur"

    acheteur = ecb.ligne_trade("BTCUSDT", {**brut, "m": False}, 1050.0)
    assert acheteur["cote_agresseur"] == "acheteur"


def test_prix_et_quantites_restent_des_chaines():
    """Un ``float`` à l'écriture fige un arrondi qu'aucun retraitement ne défait.
    L'archive doit rendre exactement ce que la place a émis."""
    ligne = ecb.ligne_trade(
        "BTCUSDT",
        {"a": 1, "p": "63000.12345678", "q": "0.00000001",
         "T": 1.0, "E": 1.0, "m": False},
        2.0)
    assert ligne["prix"] == "63000.12345678"
    assert ligne["quantite"] == "0.00000001"
    assert isinstance(ligne["prix"], str)


def test_la_latence_mesuree_est_lecart_entre_emission_et_reception():
    """La seule mesure de latence réelle dont V14 dispose. Elle ne coûte rien à
    conserver et ne se reconstitue pas après coup."""
    ligne = ecb.ligne_diff(
        "BTCUSDT",
        {"U": 1, "u": 2, "E": 1000.0, "b": [], "a": []},
        1042.0)
    assert ligne["latence_ms"] == 42.0
    assert ligne["horloge"] == "utc"


# ─────────────────────────────────────────── boucle complète

def _flux(messages):
    async def generateur():
        for message in messages:
            yield json.dumps(message)
    return lambda: generateur()


def _depth(symbole, premier, dernier, event_ms=1000.0):
    return {"stream": f"{symbole.lower()}@depth@100ms",
            "data": {"e": "depthUpdate", "s": symbole, "U": premier, "u": dernier,
                     "E": event_ms, "b": [["1.0", "2.0"]], "a": [["1.1", "3.0"]]}}


def _trade(symbole, agg_id, maker=False):
    return {"stream": f"{symbole.lower()}@aggTrade",
            "data": {"e": "aggTrade", "s": symbole, "a": agg_id, "p": "63000.00",
                     "q": "0.25", "T": 1000.0, "E": 1000.0, "m": maker}}


def _lancer(tmp_path, messages, instantanes):
    archive = ecb.Archive(tmp_path)
    appels = []

    def instantane(symbole):
        appels.append(symbole)
        return instantanes[min(len(appels) - 1, len(instantanes) - 1)]

    compteurs = asyncio.run(ecb.enregistrer(
        ("BTCUSDT",), archive=archive, instantane_s=10_000.0,
        messages=_flux(messages), instantane=instantane,
        horloge=lambda: 1_786_800_000_000.0))
    archive.fermer()
    return compteurs, appels, tmp_path


def _lignes(dossier, nature):
    chemin = next((dossier / "BTCUSDT").glob(f"*.{nature}.ndjson"))
    return [json.loads(x) for x in chemin.read_text(encoding="utf-8").splitlines()]


def test_un_flux_sain_archive_instantane_puis_differentiels(tmp_path):
    instantane = {"lastUpdateId": 100, "bids": [["1.0", "2.0"]], "asks": [["1.1", "3.0"]]}
    compteurs, appels, dossier = _lancer(
        tmp_path,
        [_depth("BTCUSDT", 99, 105), _depth("BTCUSDT", 106, 110)],
        [instantane])

    assert appels == ["BTCUSDT"], "aucune resynchronisation ne devait etre necessaire"
    assert compteurs == {"instantane": 1, "diff": 2}
    types = [ligne["type"] for ligne in _lignes(dossier, "depth")]
    assert types == ["instantane", "diff", "diff"]


def test_un_trou_est_ecrit_puis_resynchronise(tmp_path):
    """Le comportement qui décide de la valeur de l'archive : la rupture est
    consignée avec les numéros attendu et reçu, et un nouvel instantané repart."""
    compteurs, appels, dossier = _lancer(
        tmp_path,
        [_depth("BTCUSDT", 99, 105), _depth("BTCUSDT", 112, 115),
         _depth("BTCUSDT", 201, 205)],
        [{"lastUpdateId": 100, "bids": [], "asks": []},
         {"lastUpdateId": 200, "bids": [], "asks": []}])

    assert appels == ["BTCUSDT", "BTCUSDT"], "le trou doit declencher un instantane"
    trous = [ligne for ligne in _lignes(dossier, "depth") if ligne["type"] == "trou"]
    assert len(trous) == 1
    assert trous[0]["attendu_premier_id"] == 106
    assert trous[0]["recu_premier_id"] == 112
    assert compteurs["diff"] == 3, (
        "les trois differentiels doivent etre ecrits, y compris celui qui a "
        "revele le trou : la sequence annote, elle ne filtre pas")


def test_le_trou_porte_la_raison_sur_linstantane_de_reprise(tmp_path):
    _, _, dossier = _lancer(
        tmp_path,
        [_depth("BTCUSDT", 99, 105), _depth("BTCUSDT", 112, 115)],
        [{"lastUpdateId": 100, "bids": [], "asks": []},
         {"lastUpdateId": 200, "bids": [], "asks": []}])
    raisons = [ligne["raison"] for ligne in _lignes(dossier, "depth")
               if ligne["type"] == "instantane"]
    assert raisons == ["amorce", "trou"]


def test_carnet_et_transactions_vont_dans_des_fichiers_separes(tmp_path):
    """Les deux flux n'ont ni le même volume ni les mêmes consommateurs ; les
    mélanger obligerait à lire tout le carnet pour compter des trades."""
    _lancer(tmp_path,
            [_depth("BTCUSDT", 99, 105), _trade("BTCUSDT", 1),
             _trade("BTCUSDT", 2, maker=True)],
            [{"lastUpdateId": 100, "bids": [], "asks": []}])

    assert len(_lignes(tmp_path, "trades")) == 2
    assert all(ligne["type"] == "trade" for ligne in _lignes(tmp_path, "trades"))
    assert all(ligne["type"] != "trade" for ligne in _lignes(tmp_path, "depth"))


def test_larchive_est_append_only_entre_deux_executions(tmp_path):
    """Relancer le collecteur ne doit rien écraser : une archive qui se tronque
    à chaque redémarrage perd exactement ce qu'elle prétend garder."""
    messages = [_depth("BTCUSDT", 99, 105)]
    instantane = [{"lastUpdateId": 100, "bids": [], "asks": []}]
    _lancer(tmp_path, messages, instantane)
    premier = len(_lignes(tmp_path, "depth"))
    _lancer(tmp_path, messages, instantane)
    assert len(_lignes(tmp_path, "depth")) == 2 * premier


def test_chaque_ligne_porte_le_marqueur_dhorloge(tmp_path):
    """Même contrat que l'archive L1 : sans marqueur, un consommateur ne peut
    pas distinguer un horodatage corrigé d'un horodatage brut, et doit refuser
    le fichier."""
    _lancer(tmp_path,
            [_depth("BTCUSDT", 99, 105), _trade("BTCUSDT", 1)],
            [{"lastUpdateId": 100, "bids": [], "asks": []}])
    lignes = _lignes(tmp_path, "depth") + _lignes(tmp_path, "trades")
    assert lignes and all(ligne["horloge"] == "utc" for ligne in lignes)
    assert all(ligne["source"] == "binance-spot" for ligne in lignes)


def test_aucun_differentiel_recu_nest_jete(tmp_path):
    """La règle qui protège l'archive : la séquence annote, elle ne filtre pas.

    Un différentiel antérieur à l'instantané est un doublon pour qui reconstruit,
    pas un déchet pour qui archive. Le jour où le filtre se trompe de côté, la
    perte est définitive et muette — alors qu'un doublon se retire à la lecture.
    """
    _, _, dossier = _lancer(
        tmp_path,
        [_depth("BTCUSDT", 90, 95),    # anterieur a l'instantane : "ignore"
         _depth("BTCUSDT", 96, 100),   # se termine sur lastUpdateId
         _depth("BTCUSDT", 101, 105)],
        [{"lastUpdateId": 100, "bids": [], "asks": []}])

    diffs = [ligne for ligne in _lignes(dossier, "depth") if ligne["type"] == "diff"]
    assert [d["premier_id"] for d in diffs] == [90, 96, 101]


def test_linstantane_periodique_ne_recasse_pas_la_chaine(tmp_path):
    """Un point de reprise ne doit pas se comporter comme une resynchronisation.

    Réamorcer la séquence sur un instantané périodique ferait passer pour
    « déjà vus » les différentiels encore en file, et le trou suivant serait
    inventé par le collecteur lui-même.
    """
    archive = ecb.Archive(tmp_path)
    appels = []

    def instantane(symbole):
        appels.append(symbole)
        return {"lastUpdateId": 100 if len(appels) == 1 else 10_000,
                "bids": [], "asks": []}

    asyncio.run(ecb.enregistrer(
        ("BTCUSDT",), archive=archive,
        instantane_s=0.0,  # un instantane periodique apres chaque message
        messages=_flux([_depth("BTCUSDT", 99, 105), _depth("BTCUSDT", 106, 110),
                        _depth("BTCUSDT", 111, 115)]),
        instantane=instantane, horloge=lambda: 1_786_800_000_000.0))
    archive.fermer()

    lignes = _lignes(tmp_path, "depth")
    assert not [x for x in lignes if x["type"] == "trou"], (
        "un instantane periodique a fabrique un faux trou")
    raisons = [x["raison"] for x in lignes if x["type"] == "instantane"]
    assert raisons[0] == "amorce" and set(raisons[1:]) == {"periodique"}


def test_le_dossier_par_defaut_reste_sous_results(tmp_path):
    """Un collecteur qui écrit hors de `results/` polluerait le dépôt ; le
    chemin par défaut est une propriété, pas un détail."""
    assert ecb.DOSSIER.parent.name == "results"
    assert ecb.DOSSIER.name == "carnet_binance"


@pytest.mark.parametrize("symbole", ecb.SYMBOLES)
def test_les_symboles_par_defaut_sont_des_paires_binance(symbole):
    assert symbole.isupper() and symbole.endswith("USDT")


def test_les_symboles_par_defaut_ont_un_equivalent_L1_archive():
    """Sans quote Axi en face, un carnet Binance ne se compare à rien : il
    mesurerait une place où V14 n'exécute pas, sans point de reference."""
    assert set(ecb.SYMBOLES) == {"BTCUSDT", "ETHUSDT"}


# ─────────────────────────────────────────── compaction

def test_la_compaction_epargne_le_jour_en_cours(tmp_path):
    """Un collecteur ecrit dans le fichier du jour ; le compresser sous ses
    pieds perdrait la fin de la journee."""
    dossier = tmp_path / "BTCUSDT"
    dossier.mkdir(parents=True)
    (dossier / "2026-08-16.depth.ndjson").write_text('{"a": 1}\n', encoding="utf-8")
    (dossier / "2026-08-15.depth.ndjson").write_text('{"a": 1}\n', encoding="utf-8")

    faits = ecb.compacter(tmp_path, aujourdhui="2026-08-16")

    assert [c.name for c, _ in faits] == ["2026-08-15.depth.ndjson.gz"]
    assert (dossier / "2026-08-16.depth.ndjson").exists()
    assert not (dossier / "2026-08-15.depth.ndjson").exists()


def test_la_compaction_relit_avant_de_supprimer(tmp_path):
    """Le fichier clair ne disparait qu'apres relecture integrale du .gz.
    Une compaction qui perd des lignes serait pire que pas de compaction."""
    import gzip

    dossier = tmp_path / "BTCUSDT"
    dossier.mkdir(parents=True)
    contenu = "\n".join(json.dumps({"i": i, "type": "diff"}) for i in range(500)) + "\n"
    (dossier / "2026-08-15.depth.ndjson").write_text(contenu, encoding="utf-8")

    (cible, taux), = ecb.compacter(tmp_path, aujourdhui="2026-08-16")

    with gzip.open(cible, "rt", encoding="utf-8") as flux:
        assert flux.read() == contenu
    assert taux < 0.5, "du JSON repetitif doit se comprimer nettement"


def test_la_compaction_ne_refait_pas_un_travail_deja_fait(tmp_path):
    dossier = tmp_path / "BTCUSDT"
    dossier.mkdir(parents=True)
    (dossier / "2026-08-15.depth.ndjson").write_text('{"a": 1}\n', encoding="utf-8")
    assert len(ecb.compacter(tmp_path, aujourdhui="2026-08-16")) == 1
    assert ecb.compacter(tmp_path, aujourdhui="2026-08-16") == []


# ─────────────────────────────────────────── relecture

def test_larchive_se_rejoue_en_carnet_exact():
    """Le contrat de l'archive. Si ce test tombe, le format est faux et aucun
    volume de collecte ne le rattrapera."""
    lignes = [
        {"type": "instantane", "symbole": "BTCUSDT", "raison": "amorce",
         "last_update_id": 100,
         "bids": [["10.0", "1.0"], ["9.0", "2.0"]],
         "asks": [["11.0", "1.5"]], "recu_ms": 1.0, "horloge": "utc"},
        {"type": "diff", "symbole": "BTCUSDT", "premier_id": 90, "dernier_id": 95,
         "bids": [["9.0", "999.0"]], "asks": [], "recu_ms": 2.0},   # anterieur
        {"type": "diff", "symbole": "BTCUSDT", "premier_id": 101, "dernier_id": 105,
         "bids": [["10.0", "5.0"], ["9.0", "0"]], "asks": [["12.0", "4.0"]],
         "recu_ms": 3.0},
    ]
    bids, asks, appliques = ecb.reconstruire_carnet(lignes)

    assert appliques == 1, "le differentiel anterieur a l instantane ne doit pas s appliquer"
    assert bids == {"10.0": "5.0"}, "une quantite nulle SUPPRIME le niveau"
    assert asks == {"11.0": "1.5", "12.0": "4.0"}


def test_une_quantite_nulle_supprime_le_niveau_au_lieu_de_le_mettre_a_zero():
    """Lire « 0 » comme un niveau à zéro laisserait un carnet plein de fantômes,
    et gonflerait toute mesure de profondeur."""
    lignes = [
        {"type": "instantane", "last_update_id": 1, "symbole": "X", "raison": "amorce",
         "bids": [["10.0", "1.0"]], "asks": [], "recu_ms": 1.0},
        {"type": "diff", "premier_id": 2, "dernier_id": 3, "symbole": "X",
         "bids": [["10.0", "0.00000000"]], "asks": [], "recu_ms": 2.0},
    ]
    bids, _, _ = ecb.reconstruire_carnet(lignes)
    assert bids == {}


def test_la_verification_declare_reconstruit_sur_une_archive_saine(tmp_path):
    chemin = tmp_path / "sain.depth.ndjson"
    chemin.write_text("\n".join(json.dumps(x) for x in [
        {"type": "instantane", "symbole": "X", "raison": "amorce", "last_update_id": 100,
         "bids": [["10.0", "1.0"]], "asks": [["11.0", "1.0"]], "recu_ms": 1.0},
        {"type": "diff", "symbole": "X", "premier_id": 101, "dernier_id": 105,
         "bids": [["10.0", "4.0"]], "asks": [], "recu_ms": 2.0},
        {"type": "instantane", "symbole": "X", "raison": "periodique",
         "last_update_id": 105,
         "bids": [["10.0", "4.0"]], "asks": [["11.0", "1.0"]], "recu_ms": 3.0},
    ]) + "\n", encoding="utf-8")

    rapport = ecb.verifier_archive(chemin)
    assert rapport["verdict"] == "reconstruit"
    assert rapport["sessions_verifiees"] == 1


def test_la_verification_detecte_une_divergence(tmp_path):
    """Un contrôle qui ne sait dire que « tout va bien » ne contrôle rien."""
    chemin = tmp_path / "faux.depth.ndjson"
    chemin.write_text("\n".join(json.dumps(x) for x in [
        {"type": "instantane", "symbole": "X", "raison": "amorce", "last_update_id": 100,
         "bids": [["10.0", "1.0"]], "asks": [["11.0", "1.0"]], "recu_ms": 1.0},
        {"type": "instantane", "symbole": "X", "raison": "periodique",
         "last_update_id": 200,
         "bids": [["10.0", "7.0"]], "asks": [["11.0", "1.0"]], "recu_ms": 2.0},
    ]) + "\n", encoding="utf-8")

    assert ecb.verifier_archive(chemin)["verdict"] == "divergent"


# ─────────────────────────────────────────── sessions

def test_un_redemarrage_ouvre_une_session_et_le_rejeu_ne_la_franchit_pas():
    """Le defaut trouve en production le 16/08/2026.

    Le collecteur avait ete redemarre : 273 mises a jour manquaient entre la
    fin d une session et le debut de la suivante, et la reconstruction les
    enjambait sans rien signaler. Un carnet plausible et faux est pire qu une
    erreur : rien ne le distingue d un carnet juste.
    """
    lignes = [
        {"type": "instantane", "symbole": "X", "raison": "amorce", "last_update_id": 100,
         "bids": [["10.0", "1.0"]], "asks": [], "recu_ms": 1.0},
        {"type": "diff", "premier_id": 101, "dernier_id": 105, "symbole": "X",
         "bids": [["10.0", "2.0"]], "asks": [], "recu_ms": 2.0},
        # --- coupure : les mises a jour 106 a 199 n existent nulle part ---
        {"type": "instantane", "symbole": "X", "raison": "amorce", "last_update_id": 200,
         "bids": [["10.0", "9.0"]], "asks": [], "recu_ms": 3.0},
        {"type": "diff", "premier_id": 201, "dernier_id": 205, "symbole": "X",
         "bids": [["10.0", "8.0"]], "asks": [], "recu_ms": 4.0},
    ]
    assert ecb.sessions(lignes) == [(0, 2), (2, 4)]

    bids, _, appliques = ecb.reconstruire_carnet(lignes, depuis=0)
    assert appliques == 1, "le rejeu a franchi la rupture"
    assert bids == {"10.0": "2.0"}

    bids, _, _ = ecb.reconstruire_carnet(lignes, depuis=1)
    assert bids == {"10.0": "8.0"}


def test_un_trou_ouvre_aussi_une_session():
    """Une rupture de sequence est une discontinuite au meme titre qu un
    redemarrage : le carnet d avant et celui d apres ne se chainent pas."""
    lignes = [
        {"type": "instantane", "raison": "amorce", "last_update_id": 1,
         "bids": [], "asks": [], "symbole": "X", "recu_ms": 1.0},
        {"type": "instantane", "raison": "periodique", "last_update_id": 2,
         "bids": [], "asks": [], "symbole": "X", "recu_ms": 2.0},
        {"type": "instantane", "raison": "trou", "last_update_id": 3,
         "bids": [], "asks": [], "symbole": "X", "recu_ms": 3.0},
    ]
    assert ecb.sessions(lignes) == [(0, 2), (2, 3)]


def test_une_archive_sans_point_de_reprise_est_indecidable(tmp_path):
    """Ne pas confondre « non verifie » et « verifie bon ». Une archive de
    moins d un quart d heure n a pas encore de quoi se controler."""
    chemin = tmp_path / "jeune.depth.ndjson"
    chemin.write_text(json.dumps(
        {"type": "instantane", "symbole": "X", "raison": "amorce",
         "last_update_id": 1, "bids": [], "asks": [], "recu_ms": 1.0}) + "\n",
        encoding="utf-8")
    assert ecb.verifier_archive(chemin)["verdict"] == "indecidable"
