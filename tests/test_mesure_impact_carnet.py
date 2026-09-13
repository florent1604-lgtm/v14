"""Mesure d'impact sur le carnet archivé — ce que ces tests verrouillent.

L'outil tourne en intégration continue, où l'archive de vingt gigaoctets n'existe
pas. Les tests fabriquent donc de petites archives au format exact de
l'enregistreur, et vérifient dans l'ordre d'importance :

1. **le sens de l'OFI.** Inverser l'agresseur retournerait tout déséquilibre de
   flux sans qu'aucun test de forme ne s'en aperçoive ;
2. **la convention de retrait.** Une quantité nulle SUPPRIME un niveau ; la lire
   comme « niveau à zéro » laisserait un carnet plein de fantômes et fausserait
   chaque mesure en aval ;
3. **la rupture de session.** Une fenêtre qui franchit un trou rejoue un carnet
   plausible et faux : elle doit être coupée, pas mesurée ;
4. **la borne de coût.** Un ordre qui tient dans le premier niveau paie la
   moitié du spread ; rien ne doit rendre un chiffre sur un carnet épuisé ;
5. **le seuil d'échantillon.** L'outil refuse de conclure sous `N_MINIMAL`, et
   les quantités comparées ne doivent pas mélanger deux symboles.
"""

from __future__ import annotations

import json

import pytest

from tools import mesure_impact_carnet as mic

# ═══════════════════════════════════════════════════════════════════════════════
# Fabrique d'archive
# ═══════════════════════════════════════════════════════════════════════════════


def _instantane(t: float, bids, asks, raison: str = "periodique", maj: int = 100) -> dict:
    return {
        "type": "instantane",
        "symbole": "XUSDT",
        "raison": raison,
        "last_update_id": maj,
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
        "recu_ms": t,
    }


def _diff(t: float, bids=(), asks=(), dernier: int = 101, premier: int = 100) -> dict:
    return {
        "type": "diff",
        "symbole": "XUSDT",
        "premier_id": premier,
        "dernier_id": dernier,
        "bids": [[str(p), str(q)] for p, q in bids],
        "asks": [[str(p), str(q)] for p, q in asks],
        "recu_ms": t,
    }


def _trade(t: float, prix: float, qte: float, cote: str) -> dict:
    return {
        "type": "trade",
        "symbole": "XUSDT",
        "agg_id": 1,
        "prix": str(prix),
        "quantite": str(qte),
        "trade_ms": t,
        "event_ms": t,
        "acheteur_est_maker": cote == "vendeur",
        "cote_agresseur": cote,
        "recu_ms": t,
        "latence_ms": 1.0,
        "horloge": "utc",
        "source": "binance-spot",
    }


def _ecrire(chemin, lignes: list[dict]) -> None:
    chemin.write_text("".join(json.dumps(x) + "\n" for x in lignes), encoding="utf-8")


# ═══════════════════════════════════════════════════════════════════════════════
# Relecture
# ═══════════════════════════════════════════════════════════════════════════════


class TestRelecture:
    def test_ligne_cassee_ne_tue_pas_la_lecture(self, tmp_path):
        p = tmp_path / "a.ndjson"
        p.write_text('{"type": "diff"}\n{ pas du json\n{"type": "diff"}\n', encoding="utf-8")
        assert len(list(mic.lire(p))) == 2

    def test_max_lignes_borne_la_lecture(self, tmp_path):
        p = tmp_path / "a.ndjson"
        _ecrire(p, [_diff(float(i)) for i in range(50)])
        assert len(list(mic.lire(p, max_lignes=10))) == 10

    def test_sessions_decoupe_sur_les_instantanes(self, tmp_path):
        lignes = [
            _instantane(1.0, [(10, 1)], [(11, 1)]),
            _diff(2.0, bids=[(10, 2)]),
            _instantane(3.0, [(10, 1)], [(11, 1)]),
            _diff(4.0, bids=[(10, 3)]),
        ]
        assert mic.sessions(lignes) == [(0, 2), (2, 4)]

    def test_un_instantane_periodique_ouvre_une_tranche_de_reprise(self, tmp_path):
        """Il est complet, donc rejouable : c'est le seul point de reprise
        légitime au milieu d'un fichier, et ce qui permet d'échantillonner."""
        lignes = [
            _instantane(1.0, [(10, 1)], [(11, 1)], raison="amorce"),
            _diff(2.0, bids=[(10, 2)]),
            _instantane(3.0, [(10, 5)], [(11, 5)], raison="periodique"),
        ]
        assert len(mic.sessions(lignes)) == 2


class TestRuptures:
    def test_un_trou_est_une_rupture(self):
        assert mic._est_rupture({"type": "trou"}) is True

    def test_un_instantane_d_ouverture_est_une_rupture(self):
        assert mic._est_rupture({"type": "instantane", "raison": "amorce"}) is True
        assert mic._est_rupture({"type": "instantane", "raison": "trou"}) is True

    def test_un_instantane_periodique_n_est_pas_une_rupture(self):
        """Sinon on ne pourrait jamais échantillonner au milieu d'un fichier."""
        assert mic._est_rupture({"type": "instantane", "raison": "periodique"}) is False

    def test_le_franchissement_d_un_trou_est_indecis(self):
        """Une quantité nulle retire le niveau : deux niveaux retirés d'un coup
        ne sont pas « quatre niveaux à zéro »."""
        carnet = mic.Carnet.depuis_instantane(
            _instantane(0.0, [(10, 1), (9, 1)], [(11, 1), (12, 1)])
        )
        carnet.appliquer(_diff(1.0, bids=[(10, 0)], asks=[(11, 0)]))
        assert 10 not in carnet.bids
        assert 11 not in carnet.asks
        assert carnet._sommet.prix_bid == 9
        assert carnet._sommet.prix_ask == 12


# ═══════════════════════════════════════════════════════════════════════════════
# Carnet
# ═══════════════════════════════════════════════════════════════════════════════


class TestCarnet:
    def test_sommet_et_mid(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(10, 2)], [(11, 3)]))
        assert c._sommet.prix_bid == 10 and c._sommet.prix_ask == 11
        assert c._sommet.mid == 10.5
        assert c._sommet.spread == 1.0
        assert c._sommet.profondeur_sommet == 5.0

    def test_un_carnet_croise_ne_rend_pas_de_sommet(self):
        """Un mid calculé sur un carnet croisé contaminerait toutes les mesures."""
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(12, 1)], [(11, 1)]))
        assert c._sommet is None
        assert c.couts_execution([100.0])[100.0] is None

    def test_la_modification_du_sommet_met_a_jour_sa_taille(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(10, 2)], [(11, 2)]))
        c.appliquer(_diff(1.0, bids=[(10, 7)]))
        assert c._sommet.qte_bid == 7.0

    def test_un_niveau_meilleur_est_pris_meme_hors_du_sommet(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(10, 1)], [(11, 1)]))
        c.appliquer(_diff(1.0, bids=[(10.5, 1)]))
        assert c._sommet.prix_bid == 10.5

    def test_profondeur_bande_cumule_par_distance_au_mid(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(100, 1), (99.9, 5)], [(100.1, 2)]))
        # ±5 bp du mid (100.05) vaut [99.999975, 100.100025] : le niveau à 99.9
        # est hors bande, celui à 100.1 dedans.
        assert c.profondeur_bande(5.0) == pytest.approx(3.0, rel=1e-3)


class TestCout:
    def test_un_ordre_dans_le_premier_niveau_paie_la_moitie_du_spread(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(100.0, 10)], [(100.2, 10)]))
        cout = c.couts_execution([500.0])[500.0]
        # mid 100,10 ; servir 500 à 100,2 coûte 0,1/100,1 = 9,99 bp ≈ demi-spread.
        assert cout == pytest.approx(9.99, abs=0.02)

    def test_un_ordre_qui_mord_le_second_niveau_coute_plus_cher(self):
        c = mic.Carnet.depuis_instantane(
            _instantane(0.0, [(100.0, 10)], [(100.2, 1), (101.0, 100)])
        )
        petit = c.couts_execution([50.0])[50.0]
        grand = c.couts_execution([5000.0])[5000.0]
        assert grand > petit

    def test_un_carnet_trop_mince_ne_rend_pas_de_chiffre(self):
        """Inventer une mesure sur un carnet épuisé serait le pire des résultats."""
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(100.0, 1)], [(100.2, 1)]))
        assert c.couts_execution([1_000_000.0])[1_000_000.0] is None

    def test_le_plus_grand_notionnel_ne_contamine_pas_le_plus_petit(self):
        c = mic.Carnet.depuis_instantane(_instantane(0.0, [(100.0, 10)], [(100.2, 10)]))
        out = c.couts_execution([500.0, 1_000_000.0])
        assert out[500.0] is not None
        assert out[1_000_000.0] is None


# ═══════════════════════════════════════════════════════════════════════════════
# OFI — le sens du signal
# ═══════════════════════════════════════════════════════════════════════════════


class TestFlux:
    def _fenetre(self, bids, asks, diff_bids=(), diff_asks=()) -> mic.Fenetre:
        """Rejoue un seul différentiel et rend la fenêtre mesurée."""
        lignes = [
            _instantane(0.0, bids, asks),
            _diff(1.0, bids=diff_bids, asks=diff_asks),
        ]
        mesure = mic.mesurer_fenetre(lignes, 0, 2, horizons=(1.0,), notionnels=(100.0,))
        return mesure["fenetres"][0]

    def test_une_entree_de_taille_cote_achat_pousse_l_OFI_positif(self):
        # Prix inchangés, taille au bid qui monte de 1 à 5 : OFI = +4.
        f = self._fenetre([(10, 1)], [(11, 1)], diff_bids=[(10, 5)])
        assert f.ofi == pytest.approx(4.0)

    def test_une_entree_de_taille_cote_vente_le_pousse_negatif(self):
        f = self._fenetre([(10, 1)], [(11, 1)], diff_asks=[(11, 6)])
        assert f.ofi == pytest.approx(-5.0)

    def test_le_retrait_du_meilleur_bid_pousse_l_OFI_negatif(self):
        """Retirer le premier niveau décale le sommet : c'est une soustraction de
        l'ANCIENNE taille, jamais celle du nouveau meilleur niveau."""
        f = self._fenetre([(10, 4), (9, 2)], [(11, 1)], diff_bids=[(10, 0)])
        assert f.ofi == pytest.approx(-4.0)

    def test_un_prix_qui_monte_avec_la_meme_taille_est_positif(self):
        """Le prix qui s'éloigne compte autant que la taille : c'est tout l'écart
        entre l'OFI et le simple déséquilibre de volume, qui vaut zéro ici."""
        f = self._fenetre([(10, 2)], [(11, 2)], diff_bids=[(10.5, 2)])
        assert f.ofi > 0
        assert f.naif == pytest.approx(0.0)

    def test_le_flux_agressif_est_signe_par_la_place(self):
        """Le côté agresseur vient de la source, pas d'une règle de tick."""
        trades = [_trade(1.0, 10, 3, "acheteur"), _trade(2.0, 10, 7, "vendeur")]
        assert mic.flux_agressif(trades, 0.0, float("inf")) == pytest.approx(-4.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Statistiques
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistiques:
    def test_regression_parfaite(self):
        r = mic.regression([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
        assert r["pente"] == pytest.approx(2.0)
        assert r["r2"] == pytest.approx(1.0)

    def test_regression_sans_variance_ne_divise_pas_par_zero(self):
        assert mic.regression([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])["pente"] == 0.0

    def test_terciles_ordonnes(self):
        bornes = mic.terciles([float(i) for i in range(9)])
        assert bornes[0] < bornes[1]

    def test_echantillon_trop_petit_ne_rend_pas_de_terciles(self):
        assert mic.terciles([1.0, 2.0]) == []

    def test_la_profondeur_de_reference_replie_sur_le_sommet(self):
        """Sans échantillon de coût dans la fenêtre, la bande vaut zéro : le
        repli doit être explicite plutôt que de rendre une pente infinie."""
        f = mic.Fenetre(1.0, 100.0, 100.0, 1.0, 0.0, 2.0, 0.1, profondeur_bande=0.0)
        assert mic._profondeur(f) == 2.0
        f2 = mic.Fenetre(1.0, 100.0, 100.0, 1.0, 0.0, 2.0, 0.1, profondeur_bande=50.0)
        assert mic._profondeur(f2) == 50.0


class TestSpreadExterne:
    def test_analyse_des_couples(self):
        assert mic.analyser_spread_externe("BTCUSD=1.56;ETHUSD=5.03") == {
            "BTCUSD": 1.56,
            "ETHUSD": 5.03,
        }

    def test_une_entree_illisible_est_ignoree(self):
        assert mic.analyser_spread_externe("BTCUSD=abc;;ETHUSD=5") == {"ETHUSD": 5.0}


# ═══════════════════════════════════════════════════════════════════════════════
# Bout en bout, sur archive synthétique
# ═══════════════════════════════════════════════════════════════════════════════


class TestBoutEnBout:
    def _archive(self, tmp_path, avec_trou: bool = False):
        dossier = tmp_path / "carnet" / "XUSDT"
        dossier.mkdir(parents=True)
        t0 = 1_700_000_000_000.0
        lignes = [_instantane(t0, [(100, 10)], [(100.2, 10)], raison="amorce")]
        for i in range(1, 40):
            prix = 100.0 + (i % 5) * 0.01
            lignes.append(_diff(t0 + i * 100.0, bids=[(prix, 1 + (i % 3))]))
        if avec_trou:
            lignes.append({"type": "trou", "recu_ms": t0 + 4000.0})
            lignes.append(_instantane(t0 + 4000.0, [(100, 1)], [(100.2, 1)], raison="amorce"))
            for i in range(1, 40):
                lignes.append(_diff(t0 + 4000.0 + i * 100.0, bids=[(100.0, 1)]))
        _ecrire(dossier / "2026-01-01.depth.ndjson", lignes)
        return tmp_path / "carnet"

    def test_le_rapport_nomme_ses_fichiers_et_sa_fenetre(self, tmp_path, capsys):
        dossier = self._archive(tmp_path)
        code = mic.main(
            [
                "--symbole",
                "XUSDT",
                "--dossier",
                str(dossier),
                "--jours",
                "2026-01-01",
                "--minutes",
                "10",
                "--fenetres",
                "1",
                "--horizons",
                "1",
                "--notionnels",
                "100",
            ]
        )
        assert code == 0
        sortie = capsys.readouterr().out
        assert "MESURE D'IMPACT" in sortie
        assert "2026-01-01.depth.ndjson" in sortie
        assert "CE QUE CETTE MESURE NE DIT PAS" in sortie
        assert "Binance" in sortie

    def test_une_fenetre_ne_franchit_pas_un_trou(self, tmp_path):
        dossier = self._archive(tmp_path, avec_trou=True)
        chemin = dossier / "XUSDT" / "2026-01-01.depth.ndjson"
        vues = list(mic._fenetres_lecture(chemin, minutes=10, max_lignes=100_000, fenetres=1))
        assert vues, "une fenêtre doit être trouvée avant le trou"
        _, fin, lignes, _, _ = vues[0]
        assert not any(ligne.get("type") == "trou" for ligne in lignes[:fin])

    def test_archive_absente_sort_en_erreur_sans_inventer(self, tmp_path, capsys):
        code = mic.main(["--symbole", "RIEN", "--dossier", str(tmp_path / "vide")])
        assert code == 1
        assert "n'invente rien" in capsys.readouterr().out

    def test_le_rapport_refuse_de_comparer_deux_symboles(self, tmp_path, capsys):
        """Le spread de BTCUSD face au marché mesuré d'ETHUSDT serait un rapport
        sans signification : il doit être dit hors comparaison."""
        dossier = self._archive(tmp_path)
        code = mic.main(
            [
                "--symbole",
                "XUSDT",
                "--code-mt5",
                "XUSD",
                "--dossier",
                str(dossier),
                "--jours",
                "2026-01-01",
                "--minutes",
                "10",
                "--fenetres",
                "1",
                "--horizons",
                "1",
                "--notionnels",
                "100",
                "--spread-externe",
                "XUSD=2.0;AUTRE=9.0",
            ]
        )
        assert code == 0
        sortie = capsys.readouterr().out
        assert "hors comparaison" in sortie
        assert "XUSD" in sortie
