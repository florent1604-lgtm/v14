"""Un classement de politiques d'execution sur des donnees mal alignees est
plus dangereux qu'une absence de classement : il a l'air serieux.

Ces tests fixent ce que la revue independante Codex/Hermes du 24/08/2026 a
trouve faux dans la premiere version (hub offsets 578, 582, 586, 588), plus
les quatre pieges deja rencontres en construisant l'audit :

* l'OHLC MetaTrader5 est le BID, jamais un milieu ;
* une VENTE se touche sur le haut de l'ASK ;
* on ne planifie que sur une barre CLOSE, on n'apparie que sur les suivantes ;
* une expiration qui tombe dans une barre n'est pas mesurable ;
* un artefact non scelle ou d'une autre epoque n'est pas lu ;
* les politiques non directionnelles et les decoupages intra-barre sortent du
  classement au lieu d'y figurer en silence.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

RACINE = Path(__file__).resolve().parent.parent


def _module():
    chemin = RACINE / "tools" / "politiques_execution_reel.py"
    spec = importlib.util.spec_from_file_location("politiques_execution_reel", chemin)
    module = importlib.util.module_from_spec(spec)
    sys.modules["politiques_execution_reel"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def module():
    return _module()


def _barres(debut: int = 1_000_000, n: int = 30, spread: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame({
        "time_utc": [debut + 300 * i for i in range(n)],
        "open": [100.0] * n, "high": [101.0] * n, "low": [99.0] * n,
        "close": [100.0] * n, "tick_volume": [500.0] * n,
        "spread": [spread] * n, "reconstruit": [False] * n,
    })


def _decision(instant: int, **changes) -> dict:
    base = {"decision_id": "bt:v2:X:1", "decision_at": instant, "side": 1,
            "split": "calibration", "r_unit": 1.0, "net_r": 0.5,
            "prix_entree": 100.0}
    base.update(changes)
    return base


# --------------------------------------------------------------------------
# P0-1 et P0-2 : l'OHLC est le BID, l'enveloppe d'une vente est le haut de l'ASK
# --------------------------------------------------------------------------

def test_le_close_mt5_est_le_bid_et_non_le_milieu(module):
    """close=100, spread=0.20 => bid=100 / ask=100.20, jamais 99.90/100.10.

    La premiere version deplacait tout achat passif d'un demi-spread en sa
    faveur : le classement mesurait ce decalage, pas les politiques.
    """
    barres = _barres(spread=20.0)
    snaps = module.snapshots_de_matching(barres, 0, "X", point=0.01, fenetre=3)
    assert snaps[0].bid == pytest.approx(100.0)
    assert snaps[0].ask == pytest.approx(100.20)
    assert snaps[0].close == pytest.approx(snaps[0].bid)


def test_l_enveloppe_porte_le_bas_du_bid_et_le_haut_de_l_ask(module):
    """`matching.py` compare `low` a un achat et `high` a une vente."""
    barres = _barres(spread=20.0)
    snaps = module.snapshots_de_matching(barres, 0, "X", point=0.01, fenetre=1)
    assert snaps[0].low == pytest.approx(99.0)          # plus bas du BID
    assert snaps[0].high == pytest.approx(101.0 + 0.20)  # plus haut de l'ASK


def test_une_vente_est_servie_sur_le_haut_de_l_ask(module):
    """Une limite de vente entre le haut du bid et le haut de l'ask DOIT etre
    servie ; avec l'enveloppe bid brute de la premiere version, elle ne
    l'etait jamais."""
    from titanium.execution_sim.matching import MatchingSimulator
    from titanium.execution_sim.models import Order, OrderType, Side
    from titanium.execution_sim.oms import OrderManager

    barres = _barres(spread=20.0)
    snap = module.snapshots_de_matching(barres, 0, "X", point=0.01, fenetre=1)[0]
    ordre = Order(client_order_id="v", symbol="X", side=Side.SELL, quantity=1.0,
                  order_type=OrderType.LIMIT, created_at=snap.timestamp,
                  limit_price=101.10)
    oms = OrderManager()
    oms.submit(ordre, snap.timestamp)
    MatchingSimulator(seed=1, maker_bps=0.0, taker_bps=0.0).match(ordre, snap, oms)
    assert ordre.filled_quantity == pytest.approx(1.0)
    assert ordre.avg_fill_price == pytest.approx(101.10)


def test_le_spread_de_chaque_barre_est_celui_du_courtier(module):
    barres = _barres(spread=20.0)
    barres.loc[1, "spread"] = 200.0
    snaps = module.snapshots_de_matching(barres, 0, "X", point=0.01, fenetre=3)
    assert snaps[0].spread == pytest.approx(20.0 * 0.01)
    assert snaps[1].spread == pytest.approx(200.0 * 0.01)


# --------------------------------------------------------------------------
# P0-4 : causalite. On ne planifie pas sur une barre qu'on n'a pas encore vue.
# --------------------------------------------------------------------------

def test_le_snapshot_de_decision_n_a_aucune_enveloppe_future(module):
    """A l'instant de la decision, seul le sommet du carnet existe.

    Sans cela un ordre pose a t serait servi grace au parcours de la barre qui
    COMMENCE a t, c'est-a-dire grace a l'avenir.
    """
    barres = _barres(spread=20.0)
    snap = module.snapshot_de_decision(barres.iloc[0], "X", point=0.01,
                                       instant=1_000_300)
    assert (snap.low, snap.high) == (snap.bid, snap.ask)
    assert snap.timestamp == datetime.fromtimestamp(1_000_300, tz=timezone.utc)


def test_la_decision_est_planifiee_sur_la_barre_close_et_appariee_sur_la_suivante(module):
    barres = _barres()
    gardees = module.couvertes([_decision(1_000_600)], barres, fenetre=3)
    assert len(gardees) == 1
    assert gardees[0]["barre_decision"] == 1     # barre [1_000_300, 1_000_600)
    assert gardees[0]["barre"] == 2              # barre [1_000_600, 1_000_900)


def test_une_decision_hors_archive_est_ecartee(module):
    """Le piege le plus grave : une decision de 2023 executee sur une barre de
    2025 rendrait un classement faux avec un air parfaitement serieux."""
    barres = _barres()
    gardees = module.couvertes(
        [_decision(1), _decision(1_000_600), _decision(9_999_999_999)],
        barres, fenetre=3)
    assert [d["decision_at"] for d in gardees] == [1_000_600]


def test_une_decision_hors_grille_m5_est_ecartee(module):
    """Hors grille, l'intervalle entre l'ordre et la premiere barre n'est pas
    observe : ni remplissage ni absence de remplissage ne s'y affirment."""
    assert module.couvertes([_decision(1_000_450)], _barres(), fenetre=3) == []


def test_la_premiere_barre_ne_peut_pas_servir_de_decision(module):
    """Sans barre precedente, il n'y a aucune information deja acquise."""
    assert module.couvertes([_decision(1_000_000)], _barres(), fenetre=3) == []


def test_un_trou_de_seance_ecarte_la_decision(module):
    """Un trou est du temps non observe, pas un temps sans transaction."""
    barres = _barres(n=10)
    troue = barres.drop(index=4).reset_index(drop=True)
    assert module.couvertes([_decision(1_000_600)], troue, fenetre=3) == []
    # La meme decision passe quand la fenetre s'arrete avant le trou.
    assert len(module.couvertes([_decision(1_000_600)], troue, fenetre=2)) == 1


def test_une_decision_trop_pres_du_bord_est_ecartee(module):
    barres = _barres(n=10)
    tardive = _decision(int(barres["time_utc"].iloc[-2]))
    assert module.couvertes([tardive], barres, fenetre=5) == []


# --------------------------------------------------------------------------
# P0-4 (suite) : une expiration intra-barre n'est pas mesurable
# --------------------------------------------------------------------------

class _OrdreFactice:
    def __init__(self, ttl_s, *, hedge=False, offset_ms=0, suffixe=""):
        self.created_at = datetime(2026, 8, 24, tzinfo=timezone.utc)
        self.expires_at = (None if ttl_s is None
                           else self.created_at + timedelta(seconds=ttl_s))
        self.metadata = {"hedge_leg": True} if hedge else {}
        self.scheduled_offset_ms = offset_ms
        self.client_order_id = f"x{suffixe}"


def _ligne(politique="p", segment="choix", *, service=True,
           indetermine=False, intra_barre=False, part=1.0,
           effet=1.0, contact=True, franchi=True, symbole="X",
           decision_id="d"):
    return {
        "symbole": symbole, "decision_id": decision_id,
        "politique": politique, "split": segment,
        "prix_touche_inclusif": contact,
        "prix_franchi": franchi,
        "service_synthetique_scenario": service,
        "part_service_synthetique": part,
        "gain_entree_r": 0.0, "net_r": 1.0,
        "effet_r_service_synthetique": effet,
        "indetermine": indetermine, "intra_barre": intra_barre,
    }


@pytest.mark.parametrize("ttl,attendu", [
    (120, True),    # TTL de la boucle quand le spread pese : INDETERMINE en M5
    (300, False),   # convention explicite [t, t+300)
    (600, False),
    (None, False),  # un ordre sans expiration ne promet aucune duree
    (450, True),
])
def test_une_expiration_hors_grille_est_indeterminee(module, ttl, attendu):
    assert module.expiration_indeterminee([_OrdreFactice(ttl)]) is attendu


def test_l_indetermination_est_decidee_avant_le_resultat(module):
    """Le verdict ne depend QUE du plan, jamais du remplissage.

    Exclure sur le resultat ne retirerait que les touches et biaiserait le
    taux de remplissage vers le bas : la politique paraitrait plus prudente
    qu'elle ne l'est.
    """
    rempli, vide = _OrdreFactice(120), _OrdreFactice(120)
    rempli.filled_quantity, rempli.avg_fill_price = 1.0, 100.0
    vide.filled_quantity, vide.avg_fill_price = 0.0, 0.0
    assert module.expiration_indeterminee([rempli]) is True
    assert module.expiration_indeterminee([vide]) is True


def test_une_ligne_indeterminee_sort_du_numerateur_et_du_denominateur(module):
    lignes = [
        _ligne(),
        _ligne(indetermine=True, decision_id="e"),
    ]
    cellule = module.agreger(lignes, effectif_min=1)["p"]["choix"]
    assert cellule["n"] == 1
    assert cellule["n_indetermine"] == 1


# --------------------------------------------------------------------------
# P0-5 : le classement ne retient que le mesurable et le directionnel
# --------------------------------------------------------------------------

def test_les_politiques_non_directionnelles_ne_sont_pas_mesurees_par_defaut(module):
    for nom in ("market_making", "multi_leg_simultaneous", "maker_then_hedge_taker"):
        assert nom in module.NON_DIRECTIONNELLES
        assert nom not in module.POLITIQUES
    assert "v14_live" in module.POLITIQUES


def test_une_politique_non_directionnelle_demandee_est_mesuree_mais_pas_classee(module):
    lignes = [_ligne("market_making", segment)
              for segment in ("choix", "jugement")]
    agrege = module.agreger(lignes, effectif_min=1)
    assert agrege["market_making"]["choix"]["n"] == 1
    assert agrege["market_making"]["exclue_du_classement"] == module.MOTIF_NON_DIRECTIONNELLE
    assert module.classer(agrege) == []


@pytest.mark.parametrize("nom", ["cancel_replace", "pegged", "vwap", "pov"])
def test_une_politique_non_fidele_au_runner_m5_sort_du_classement(module, nom):
    lignes = [_ligne(nom, segment) for segment in ("choix", "jugement")]
    agrege = module.agreger(lignes, effectif_min=1)
    assert agrege[nom]["exclue_du_classement"] == module.MOTIF_NON_FIDELE_M5
    assert module.classer(agrege) == []


def test_une_cohorte_partiellement_indeterminee_sort_du_classement_global(module):
    lignes = [_ligne("v14_live", "choix"),
              _ligne("v14_live", "jugement", indetermine=True,
                     decision_id="e")]
    agrege = module.agreger(lignes, effectif_min=1)
    assert agrege["v14_live"]["exclue_du_classement"] == \
        module.MOTIF_COHORTE_PARTIELLE
    assert module.classer(agrege) == []


def test_un_decoupage_intra_barre_sort_du_classement(module):
    lignes = [_ligne("twap", segment, intra_barre=True)
              for segment in ("choix", "jugement")]
    agrege = module.agreger(lignes, effectif_min=1)
    assert agrege["twap"]["exclue_du_classement"] == module.MOTIF_INTRA_BARRE
    assert module.classer(agrege) == []


def test_deux_tranches_dans_la_meme_barre_sont_indistinguables(module):
    assert module.sequence_intra_barre(
        [_OrdreFactice(None, offset_ms=0), _OrdreFactice(None, offset_ms=30_000)])
    assert not module.sequence_intra_barre(
        [_OrdreFactice(None, offset_ms=0), _OrdreFactice(None, offset_ms=600_000)])
    # Le remplacement de cancel_replace est pose une heure plus tard : son
    # decalage programme vaut zero, il ne prouve aucun decoupage intra-barre.
    assert not module.sequence_intra_barre(
        [_OrdreFactice(None), _OrdreFactice(None, suffixe=":replacement")])


def test_une_jambe_opposee_ne_deplace_pas_le_prix_d_entree(module):
    """Un achat n'est pas mieux servi parce qu'une vente a ete remplie."""
    from titanium.execution_sim.models import Order, OrderType, Side

    horodatage = datetime(2026, 8, 24, tzinfo=timezone.utc)

    def ordre(side, prix):
        o = Order(client_order_id=f"o{side}", symbol="X", side=side, quantity=1.0,
                  order_type=OrderType.LIMIT, created_at=horodatage, limit_price=prix)
        o.filled_quantity, o.avg_fill_price = 1.0, prix
        return o

    quantite, prix = module._prix_moyen(
        [ordre(Side.BUY, 100.0), ordre(Side.SELL, 90.0)], Side.BUY)
    assert (quantite, prix) == (1.0, 100.0)


def test_contact_inclusif_et_franchissement_strict_sont_distincts(module):
    from titanium.execution_sim.models import Order, OrderType, Side

    barres = _barres(n=3)
    barres.loc[1, "low"] = 99.0
    decision = module.snapshot_de_decision(
        barres.iloc[0], "X", point=0.01, instant=int(barres.loc[1, "time_utc"]))
    futurs = module.snapshots_de_matching(barres, 1, "X", point=0.01, fenetre=1)
    ordre = Order(client_order_id="contact", symbol="X", side=Side.BUY,
                  quantity=1.0, order_type=OrderType.LIMIT,
                  created_at=decision.timestamp, limit_price=99.0)
    assert module._premier_contact_prix(
        [ordre], [decision, *futurs], Side.BUY, inclusif=True) == futurs[0].timestamp
    assert module._premier_contact_prix(
        [ordre], [decision, *futurs], Side.BUY, inclusif=False) is None


def test_comparaison_v14_live_market_utilise_exactement_la_meme_cohorte(module):
    lignes = [
        _ligne("v14_live", "jugement", effet=0.2, decision_id="a"),
        _ligne("market", "jugement", effet=0.1, decision_id="a"),
        _ligne("v14_live", "jugement", effet=-9.0, indetermine=True,
               decision_id="b"),
        _ligne("market", "jugement", effet=-2.0, decision_id="b"),
    ]
    juge = module.comparer_cohorte_commune(lignes)["jugement"]
    assert juge["n"] == 1
    assert juge["n_total_politique"] == 2
    assert juge["taux_resolution"] == pytest.approx(0.5)
    assert juge["attrition_non_aleatoire_possible"] is True
    assert juge["effet_r_politique"] == pytest.approx(0.2)
    assert juge["effet_r_market"] == pytest.approx(0.1)
    assert juge["uplift_r_vs_market"] == pytest.approx(0.1)
    assert juge["bootstrap_cluster_symbole"]["valide"] is False


def test_bootstrap_apparie_est_clusterise_par_symbole_et_deterministe(module):
    lignes = []
    for symbole, effets in {"A": (0.6, 0.2), "B": (-0.1, 0.1)}.items():
        for index, (cible, marche) in enumerate(zip(effets, (0.0, 0.0), strict=True)):
            decision = f"{symbole}-{index}"
            lignes.extend([
                _ligne("v14_live", "jugement", effet=cible,
                       symbole=symbole, decision_id=decision),
                _ligne("market", "jugement", effet=marche,
                       symbole=symbole, decision_id=decision),
            ])
    premier = module.comparer_cohorte_commune(lignes)["jugement"]
    second = module.comparer_cohorte_commune(lignes)["jugement"]
    assert premier == second
    bootstrap = premier["bootstrap_cluster_symbole"]
    assert bootstrap["valide"] is True
    assert bootstrap["n_symboles"] == 2
    assert bootstrap["repetitions"] == 5_000
    assert bootstrap["decision_weighted"]["moyenne"] == pytest.approx(0.2)
    assert bootstrap["symbol_equal"]["moyenne"] == pytest.approx(0.2)
    assert bootstrap["decision_weighted"]["ic95"] == pytest.approx([0.0, 0.4])


def test_bootstrap_distingue_ponderation_decisions_et_symboles(module):
    deltas = {"LIQUIDE": [1.0] * 9, "RARE": [-1.0]}
    resultat = module._ic_bootstrap_par_symbole(deltas, repetitions=100, seed=7)
    assert resultat["decision_weighted"]["moyenne"] == pytest.approx(0.8)
    assert resultat["symbol_equal"]["moyenne"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# P0-6 : un artefact non scelle, hors contrat ou d'une autre epoque n'est pas lu
# --------------------------------------------------------------------------

def _artefact(tmp_path: Path, symbole: str = "X", **surcharges) -> tuple[Path, Path]:
    brut, resumes = tmp_path / "brut", tmp_path / "resumes"
    (brut / symbole).mkdir(parents=True)
    resumes.mkdir()
    (resumes / f"{symbole}.json").write_text("{}", encoding="utf-8")
    manifeste = {"artifact_type": "v14.offline_replay.trades", "schema_version": 2,
                 "symbol": symbole, "snapshot": {"engine": [{"name": "a", "sha256": "b"}]}}
    manifeste.update(surcharges)
    (brut / symbole / "manifest.json").write_text(
        json.dumps(manifeste), encoding="utf-8")
    (brut / symbole / "trades.ndjson").write_text("", encoding="utf-8")
    return brut, resumes


@pytest.mark.parametrize("surcharges,motif", [
    ({"artifact_type": "autre"}, "artifact_type"),
    ({"schema_version": 4}, "schema_version"),
    ({"schema_version": 1}, "schema_version"),
])
def test_un_artefact_hors_contrat_est_refuse(module, tmp_path, surcharges, motif):
    """« v4 » designe la generation du backfill, pas le schema : le contrat
    machine reste 2 (rectification Codex, hub offset 588)."""
    brut, resumes = _artefact(tmp_path, **surcharges)
    valide, raison = module.valider_artefact("X", brut=brut, resumes=resumes,
                                             empreinte_attendue="")
    assert not valide and motif in raison


def test_un_artefact_d_une_autre_epoque_est_refuse(module, tmp_path):
    """Deux generations melangees classent l'historique des commits, pas les
    politiques."""
    brut, resumes = _artefact(tmp_path)
    valide, raison = module.valider_artefact(
        "X", brut=brut, resumes=resumes, empreinte_attendue="e" * 64)
    assert not valide and "epoque_moteur" in raison


def test_un_manifeste_absent_est_refuse(module, tmp_path):
    (tmp_path / "brut" / "X").mkdir(parents=True)
    (tmp_path / "resumes").mkdir()
    valide, raison = module.valider_artefact(
        "X", brut=tmp_path / "brut", resumes=tmp_path / "resumes",
        empreinte_attendue="")
    assert not valide and raison == "manifeste_absent"


def test_un_sceau_faux_est_refuse(module, tmp_path):
    """Le sceau du manifeste est verifie par le validateur du rejeu lui-meme."""
    brut, resumes = _artefact(tmp_path)
    valide, raison = module.valider_artefact("X", brut=brut, resumes=resumes,
                                             empreinte_attendue="")
    assert not valide and raison == "sceaux_ou_compteurs_invalides"


def test_aucune_decision_n_est_lue_sans_artefact_valide(module, tmp_path):
    brut, resumes = _artefact(tmp_path)
    (brut / "X" / "trades.ndjson").write_text(
        json.dumps({"trade_id": "t", "decision_at": "2026-01-01T00:00:00+00:00",
                    "side": 1, "r_unit": 1.0, "net_r": 0.1,
                    "prix_entree": 1.0}) + "\n", encoding="utf-8")
    assert module.decisions("X", brut=brut, empreinte_attendue="") == []


# --------------------------------------------------------------------------
# Reauditabilite et pieges deja connus
# --------------------------------------------------------------------------

def test_l_artefact_de_sortie_permet_de_refaire_l_appariement(module):
    """Sans identifiant, instant et sens, personne ne peut rejouer la mesure."""
    import inspect

    source = inspect.getsource(module.evaluer_symbole)
    for champ in ('"decision_id"', '"decision_at"', '"side"'):
        assert champ in source


def test_la_coupe_est_chronologique_et_aux_deux_tiers(module):
    decisions = [{"decision_at": t, "split": "verification"} for t in range(9)]
    marquees = module.marquer_segments(decisions)
    assert [d["split"] for d in marquees] == ["choix"] * 6 + ["jugement"] * 3
    # Le segment du rejeu est conserve, pas ecrase.
    assert {d["split_rejeu"] for d in marquees} == {"verification"}


def test_les_barres_reconstruites_sont_exclues(module, tmp_path):
    barres = _barres(n=6)
    barres.loc[2, "reconstruit"] = True
    (tmp_path / "M5").mkdir()
    barres.to_parquet(tmp_path / "M5" / "X.parquet", index=False)
    lues = module.charger_barres("X", racine=tmp_path)
    assert len(lues) == 5


def test_les_plafonds_de_risque_suivent_le_prix(module):
    from titanium.execution_sim.config import load_config

    petit = module._config_a_l_echelle(load_config(), 100.0)
    grand = module._config_a_l_echelle(load_config(), 112_000.0)
    assert grand["risk"]["max_gross_exposure"] > petit["risk"]["max_gross_exposure"]
    # Sans cette mise a l'echelle, un ordre unitaire sur BTC est rejete en
    # silence et la politique disparait du classement.
    assert grand["risk"]["max_gross_exposure"] >= 112_000.0


def test_un_remplissage_partiel_ne_compte_qu_une_part_du_R(module):
    lignes = [
        _ligne(part=0.4, effet=0.4),
        _ligne(service=False, part=0.0, effet=0.0, decision_id="e"),
    ]
    agrege = module.agreger(lignes, effectif_min=1)
    cellule = agrege["p"]["choix"]
    assert cellule["effet_r_service_synthetique"] == pytest.approx(0.2)
    assert cellule["taux_service_synthetique_scenario"] == pytest.approx(0.5)
    assert cellule["effet_r_par_service_synthetique"] == pytest.approx(0.4)


def test_une_politique_non_concluante_ne_figure_pas_au_classement(module):
    lignes = [_ligne()]
    assert module.classer(module.agreger(lignes, effectif_min=60)) == []
    assert module.classer(module.agreger(lignes, effectif_min=1))[0]["politique"] == "p"


def test_le_rapport_dit_que_le_rang_seul_ne_promeut_rien(module):
    rapport = module.mesurer([], politiques=("market",), limite=1)
    assert "NEGATIVE" in rapport["avertissement"]
    assert rapport["schema_version"] == 3
    assert "AVERTISSEMENT" in module.resumer(rapport)


def test_aucune_cle_publiee_n_annonce_un_remplissage(module):
    """Contrat impose par la revue Hermes H2 : `touch_rate_upper_bound`,
    jamais `fill_rate`. Une barre M5 dit qu'un prix a ete touche ; elle ne dit
    ni la file d'attente, ni la profondeur, ni la priorite. Nommer cela un
    taux de remplissage transformerait une borne superieure en resultat."""
    lignes = [_ligne("p", segment)
              for segment in ("choix", "jugement")]
    agrege = module.agreger(lignes, effectif_min=1)
    cellule = agrege["p"]["choix"]
    assert not any("rempli" in cle or "fill" in cle for cle in cellule)
    assert cellule["taux_contact_inclusif"] == pytest.approx(1.0)
    for rang in module.classer(agrege):
        assert not any("rempli" in cle or "fill" in cle for cle in rang)


# --------------------------------------------------------------------------
# Epoque d'analyse declaree (P0 du 25/08/2026) : la generation mesuree est
# celle du corpus, pas celle de l'arbre de travail.
# --------------------------------------------------------------------------

def test_le_classement_suit_l_epoque_du_corpus_et_publie_l_ecart(
        module, tmp_path, monkeypatch):
    """Regression : un commit sur un fichier moteur ne doit plus perimer un
    corpus scelle intact. L'ecart avec l'arbre de travail est publie, pas
    interdit."""
    brut, resumes = _artefact(tmp_path)
    from tools import epoque_rejeu

    corpus = epoque_rejeu.empreinte([{"name": "a", "sha256": "b"}])
    monkeypatch.setattr(module, "evaluer_symbole",
                        lambda *a, **k: [_ligne(decision_id="d1")])
    monkeypatch.setattr(module, "valider_artefact",
                        lambda symbole, **k: (True, "ok"))
    rapport = module.mesurer(["X"], politiques=("market",), limite=1,
                             brut=brut, resumes=resumes)
    assert rapport["statut"] == module.STATUT_MESURE
    assert rapport["epoque"]["corpus_epoch"] == corpus
    assert rapport["epoque_rejeu"] == corpus
    assert rapport["epoque"]["workspace_engine_epoch"] == (
        epoque_rejeu.empreinte_courante())
    assert rapport["epoque"]["workspace_matches_corpus"] is False
    assert [m["symbol"] for m in rapport["epoque"]["manifests"]] == ["X"]
    assert len(rapport["code"]["analyse_sha256"]) == 64


def test_un_corpus_a_deux_generations_refuse_le_classement(module, tmp_path):
    from tools import epoque_rejeu

    brut, resumes = _artefact(tmp_path, "X")
    (brut / "Y").mkdir(parents=True)
    (resumes / "Y.json").write_text("{}", encoding="utf-8")
    (brut / "Y" / "manifest.json").write_text(json.dumps({
        "artifact_type": "v14.offline_replay.trades", "schema_version": 2,
        "symbol": "Y", "snapshot": {"engine": [{"name": "a", "sha256": "z"}]},
    }), encoding="utf-8")
    (brut / "Y" / "trades.ndjson").write_text("", encoding="utf-8")
    with pytest.raises(epoque_rejeu.EpoqueCorpusError) as erreur:
        module.mesurer(["X", "Y"], politiques=("market",), limite=1,
                       brut=brut, resumes=resumes)
    assert erreur.value.motif == "GENERATIONS_MIXTES"


def test_un_pin_faux_refuse_et_un_pin_juste_passe(module, tmp_path, monkeypatch):
    from tools import epoque_rejeu

    brut, resumes = _artefact(tmp_path)
    corpus = epoque_rejeu.empreinte([{"name": "a", "sha256": "b"}])
    monkeypatch.setattr(module, "evaluer_symbole",
                        lambda *a, **k: [_ligne(decision_id="d1")])
    monkeypatch.setattr(module, "valider_artefact",
                        lambda symbole, **k: (True, "ok"))
    rapport = module.mesurer(["X"], politiques=("market",), limite=1,
                             brut=brut, resumes=resumes, pin_epoque=corpus)
    assert rapport["epoque"]["pin"] == corpus
    with pytest.raises(epoque_rejeu.EpoqueCorpusError) as erreur:
        module.mesurer(["X"], politiques=("market",), limite=1,
                       brut=brut, resumes=resumes, pin_epoque="a" * 64)
    assert erreur.value.motif == "PIN_DIFFERENT_DU_CORPUS"


def test_zero_artefact_valide_bloque_l_analyse(module, tmp_path):
    """Un classement vide n'est pas un classement : il doit se declarer bloque."""
    brut, resumes = _artefact(tmp_path)
    rapport = module.mesurer(["X"], politiques=("market",), limite=1,
                             brut=brut, resumes=resumes)
    assert rapport["statut"] == module.STATUT_BLOQUE
    assert "sceaux_ou_compteurs_invalides" in rapport["motif_bloquant"]
    assert rapport["artefacts"]["valides"] == 0


def test_une_demande_vide_n_est_pas_une_mesure(module):
    rapport = module.mesurer([], politiques=("market",), limite=1)
    assert rapport["statut"] == module.STATUT_BLOQUE
    assert rapport["motif_bloquant"] == "CORPUS_VIDE"


def test_le_classement_bloque_publie_son_blocage_sans_ecraser_le_dernier(
        module, tmp_path, monkeypatch, capsys):
    """Durcissement Claude/Hermes : ANALYSIS_BLOCKED doit figurer DANS UN
    RAPPORT PUBLIE, pas seulement dans un code de retour. C'est ce mode de
    panne muet qui a dure du 25/08 08:08 au lendemain matin."""
    from tools import epoque_rejeu

    brut, resumes = _artefact(tmp_path, "X")
    sortie = tmp_path / "classement.json"
    sortie.write_text('{"statut": "MESURE"}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "politiques_execution_reel.py", "--symboles", "X",
        "--limite", "1", "--politiques", "market",
        "--brut", str(brut), "--resumes", str(resumes),
        "--sortie", str(sortie),
    ])
    assert module.main() == 2
    rendu = json.loads(capsys.readouterr().out)
    assert rendu["statut"] == module.STATUT_BLOQUE
    # Le corpus est lisible et homogene : le blocage vient du SCEAU, pas de
    # l'epoque. Les deux causes ne doivent jamais se confondre.
    assert "sceaux_ou_compteurs_invalides" in rendu["motif_bloquant"]
    assert rendu["epoque"]["corpus_epoch"]
    assert rendu["ecrit"] is False
    assert json.loads(sortie.read_text(encoding="utf-8"))["statut"] == "MESURE"
    blocage = json.loads(
        epoque_rejeu.chemin_blocage(sortie).read_text(encoding="utf-8"))
    assert blocage["status"] == "ANALYSIS_BLOCKED"
    assert blocage["banc"] == "politiques_execution_reel"
    assert blocage["report_not_written"] == str(sortie)


def test_un_seul_sceau_casse_bloque_tout_le_classement(module, tmp_path,
                                                       monkeypatch):
    """Bloqueur 1 de Codex : un classement partiel a l'air d'un classement
    complet. Un artefact demande dont l'integrite tombe bloque l'analyse."""
    brut, resumes = _artefact(tmp_path, "X")
    (brut / "Y").mkdir()
    (brut / "Y" / "trades.ndjson").write_text("", encoding="utf-8")
    (brut / "Y" / "manifest.json").write_text(
        (brut / "X" / "manifest.json").read_text(encoding="utf-8").replace(
            '"X"', '"Y"'), encoding="utf-8")
    (resumes / "Y.json").write_text("{}", encoding="utf-8")
    appels = {"n": 0}

    def valider(symbole, **_):
        appels["n"] += 1
        return (True, "ok") if symbole == "X" else (
            False, "sceaux_ou_compteurs_invalides")

    monkeypatch.setattr(module, "valider_artefact", valider)
    monkeypatch.setattr(module, "evaluer_symbole",
                        lambda *a, **k: [_ligne(decision_id="d1")])
    rapport = module.mesurer(["X", "Y"], politiques=("market",), limite=1,
                             brut=brut, resumes=resumes)
    assert rapport["statut"] == module.STATUT_BLOQUE
    assert rapport["motif_bloquant"] == "sceaux_ou_compteurs_invalides"
    assert rapport["artefacts"]["valides"] == 1
    assert rapport["artefacts"]["refuses"] == {
        "Y": "sceaux_ou_compteurs_invalides"}
