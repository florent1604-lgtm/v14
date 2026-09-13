"""Momentum intraday H4 — ce que ces tests verrouillent.

L'outil tourne en intégration continue, où il n'y a ni terminal MT5 ni archive de
55 millions de barres. Les tests construisent donc des barres synthétiques et
**injectent** le chargeur, ce qui permet de vérifier la chaîne de mesure sans
dépendre du module d'archive. Dans l'ordre d'importance :

1. **la référence du retour d'ouverture est la clôture de la veille**, pas la
   barre collée à la séance : c'est la définition du papier, et c'est elle qui
   contient le gap overnight ;
2. **la référence du retour de clôture est la barre qui précède la fenêtre**,
   pas la première barre de la fenêtre — cinq minutes de trop, sinon ;
3. **la séance est détectée au volume**, et une journée décalée d'une heure est
   absorbée par la tolérance, ce qui tient lieu de gestion du changement d'heure ;
4. **une journée mal couverte est écartée**, jamais complétée ;
5. **le coût vient de l'archive du symbole**, converti avec son point, et
   l'absence de spécification ne fabrique pas un coût ;
6. **le seuil annoncé est le seuil appliqué** : la famille de tests corrige le
   seuil, et un `t` sous ce seuil ne conclut pas.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tools import mesure_momentum_intraday as mmi

JOUR = 86400
BASE = pd.Timestamp("2026-01-05 00:00:00", tz="UTC")  # un lundi


# ═══════════════════════════════════════════════════════════════════════════════
# Fabrique de barres
# ═══════════════════════════════════════════════════════════════════════════════


def _jour_synthetique(
    jour: int,
    debut_util: float,
    r_ouverture: float,
    r_cloture: float,
    *,
    decalage_min: int = 0,
    barres_avant: int = 6,
    barres_apres: int = 6,
    volume_creux: float = 1.0,
    volume_plein: float = 100.0,
):
    """Barres d'une séance : 78 barres M5, mise en forme par morceaux.

    Les prix sont posés pour que les références attendues se lisent **exactement** :
    la clôture de la première demi-heure vaut `debut_util × (1 + r_ouverture)` et
    la clôture de séance `ouverture × (1 + r_cloture)`. La barre 71 — celle qui
    précède la dernière fenêtre — reste au prix d'ouverture, et la barre 72 qui
    ouvre la fenêtre en diffère : c'est ce qui permet de distinguer les deux
    conventions de référence du retour de clôture.
    """
    debut = (BASE + pd.Timedelta(days=jour)).timestamp() + (13 * 60 + 30 + decalage_min) * 60
    horodatages = [debut + i * 300 for i in range(-barres_avant, 78 + barres_apres)]
    ouverture_prix = debut_util * (1.0 + r_ouverture)
    cloture_prix = ouverture_prix * (1.0 + r_cloture)
    prix, volumes = [], []
    for i in range(-barres_avant, 78 + barres_apres):
        if i < 0:
            # Avant séance : la place est au prix de clôture de la veille.
            prix.append(debut_util)
            volumes.append(volume_creux)
        elif i <= 5:
            # Première demi-heure : six pas égaux, de la veille à l'ouverture.
            prix.append(debut_util + (ouverture_prix - debut_util) * (i + 1) / 6.0)
            volumes.append(volume_plein)
        elif i <= 71:
            # Cœur de séance immobile : seule la fenêtre de clôture porte le retour.
            prix.append(ouverture_prix)
            volumes.append(volume_plein)
        elif i <= 77:
            # Dernière demi-heure : six pas égaux, de la barre 72 à la clôture.
            prix.append(ouverture_prix + (cloture_prix - ouverture_prix) * (i - 71) / 6.0)
            volumes.append(volume_plein)
        else:
            prix.append(cloture_prix)
            volumes.append(volume_plein)
    index = pd.to_datetime(horodatages, unit="s", utc=True)
    return pd.DataFrame(
        {
            "open": prix,
            "high": prix,
            "low": prix,
            "close": prix,
            "tick_volume": volumes,
            "spread": [30.0] * len(prix),
            "real_volume": [0.0] * len(prix),
        },
        index=index,
    )


def _archives(
    n_jours: int = 6, *, decalages: dict[int, int] | None = None, r_ouverture=None, r_cloture=None
) -> pd.DataFrame:
    decalages = decalages or {}
    morceaux = []
    base = 100.0
    for j in range(n_jours):
        # Aucun retour nul : quand le prix d'ouverture égale la clôture de la veille,
        # les deux conventions de référence coïncident et les tests cessent de
        # distinguer ce qu'ils prétendent distinguer.
        ro = r_ouverture(j) if r_ouverture else ((j - 2) * 2 + 1) * 0.001
        rc = r_cloture(ro) if r_cloture else 0.5 * ro
        jour = _jour_synthetique(j, base, ro, rc, decalage_min=decalages.get(j, 0))
        morceaux.append(jour)
        # Chaîne : la clôture de ce jour est la veille du lendemain. C'est la
        # barre 77 (dernière de séance), et les barres d'après-séance la répètent.
        base = float(jour["close"].iloc[-1])
    return pd.concat(morceaux)


def _barres_du_jour(donnees: pd.DataFrame, jour: int) -> pd.DataFrame:
    """Les 90 barres du jour `jour` de la fabrique : 6 avant, 78 de séance, 6 après."""
    return donnees.iloc[jour * 90 : (jour + 1) * 90]

def _chargeur(donnees: pd.DataFrame):
    def charger(symbole, timeframe):  # signature de ``charger_barres``
        return donnees.copy()

    return charger


def _seance(jour: int, r_ouverture: float, r_cloture: float, *, cout_bps: float = 0.5):
    """Une séance posée à la main, pour éprouver le verdict sans archive."""
    return mmi.Seance(
        jour=f"2026-01-{jour:02d}",
        debut_min=810,
        r_ouverture=r_ouverture,
        r_ouverture_session=r_ouverture,
        r_cloture=r_cloture,
        r_milieu=0.0,
        r_cloture_veille=0.0,
        amplitude_ouverture=abs(r_ouverture),
        volatilite_passee=0.0,
        spread_bps=cout_bps,
        prix_cloture=100.0,
        barres=78,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Statistiques
# ═══════════════════════════════════════════════════════════════════════════════


class TestStatistiques:
    def test_quantile_bilateral_retrouve_les_valeurs_usuelles(self):
        assert mmi.quantile_bilateral(0.05) == pytest.approx(1.960, abs=1e-3)
        assert mmi.quantile_bilateral(0.01) == pytest.approx(2.576, abs=1e-3)

    def test_le_seuil_de_famille_est_plus_strict_que_le_seuil_simple(self):
        assert mmi.quantile_bilateral(0.05 / 12) > mmi.quantile_bilateral(0.05)

    def test_regression_parfaite(self):
        r = mmi.regression([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 6.0, 8.0])
        assert r["pente"] == pytest.approx(2.0)
        assert r["r2"] == pytest.approx(1.0)

    def test_regression_sans_variance_ne_divise_pas_par_zero(self):
        assert mmi.regression([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])["pente"] == 0.0

    def test_ecart_conditionnel_a_le_bon_signe(self):
        valeurs = [0.011, 0.010, 0.009, -0.009, -0.010, -0.011]
        pred = [1.0, 1.0, 1.0, -1.0, -1.0, -1.0]
        e = mmi.ecart_conditionnel(valeurs, pred)
        assert e["ecart_bps"] == pytest.approx(200.0, rel=0.02)
        assert e["t"] > 0

    def test_ecart_conditionnel_refuse_un_groupe_vide(self):
        e = mmi.ecart_conditionnel([0.01, -0.01], [1.0, -1.0])
        assert e["ecart_bps"] == 0.0 and e["t"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Détection de séance
# ═══════════════════════════════════════════════════════════════════════════════


class TestDetectionSeance:
    def test_le_debut_modal_est_la_fenetre_de_volume(self):
        donnees = _archives(4)
        mode, par_jour = mmi.detecter_debuts(donnees, duree_min=390, pas_min=5)
        assert mmi._hhmm(mode) == "13:30"
        assert len(par_jour) == 4

    def test_un_decalage_d_une_heure_est_absorbe(self):
        """C'est la gestion du changement d'heure : pas de fuseaux, une tolérance."""
        donnees = _archives(5, decalages={3: 60, 4: 60})
        mode, par_jour = mmi.detecter_debuts(donnees, duree_min=390, pas_min=5)
        debuts = dict(par_jour)
        assert mmi._hhmm(mode) == "13:30"
        assert sorted(debuts.values())[-1] - sorted(debuts.values())[0] == 60


# ═══════════════════════════════════════════════════════════════════════════════
# Chaîne de mesure
# ═══════════════════════════════════════════════════════════════════════════════


class TestMesure:
    def test_la_pente_retrouve_l_effet_pose(self, tmp_path):
        """Effet posé à 0,5 : la chaîne complète doit le retrouver."""
        donnees = _archives(40)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        seances = res["seances"]
        assert len(seances) == 39  # la première journée sert de référence
        r = mmi.regression([s.r_ouverture for s in seances], [s.r_cloture for s in seances])
        assert r["pente"] == pytest.approx(0.5, abs=0.02)

    def test_le_retour_d_ouverture_part_de_la_cloture_de_la_veille(self, tmp_path):
        """Le gap overnight doit compter : sinon le prédicteur change de nature."""
        donnees = _archives(6)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        seances = res["seances"]
        # Le retour d'ouverture part de la clôture de la veille, donc il contient
        # le gap overnight ; celui mesuré depuis la première barre de séance non.
        # La fabrique les rend délibérément différents.
        assert all(abs(s.r_ouverture - s.r_ouverture_session) > 1e-6 for s in seances)

    def test_le_retour_de_cloture_part_de_la_barre_qui_precede_la_fenetre(self, tmp_path):
        """La référence est la barre 71, pas la barre 72 qui ouvre la fenêtre.

        Les deux lectures sont recalculées depuis les barres synthétiques : la
        bonne rend `r_cloture`, la fautive n'en rend que les 5/6. Le test ne se
        contente donc pas de comparer l'outil à lui-même.
        """
        donnees = _archives(40)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        seances = res["seances"]
        assert len(seances) == 39
        for jour, seance in enumerate(seances, start=1):
            clotures = _barres_du_jour(donnees, jour)["close"].to_numpy(dtype=float)
            correcte = clotures[83] / clotures[6 + 71] - 1.0
            fautive = clotures[83] / clotures[6 + 72] - 1.0
            assert seance.r_cloture == pytest.approx(correcte, abs=1e-12)
            # Les deux conventions diffèrent d'un sixième : le test discrimine.
            assert abs(fautive - correcte) > abs(correcte) * 0.1

    def test_une_journee_trop_courte_est_ecartee(self, tmp_path):
        donnees = _archives(6)
        tronque = donnees[donnees.index < donnees.index[0] + pd.Timedelta(days=5)]
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(tronque),
        )
        assert res["jours_ecartes"] >= 1
        assert len(res["seances"]) < 5

    def test_le_cout_vient_du_spread_de_l_archive(self, tmp_path):
        (tmp_path / "_specifications.json").write_text(
            json.dumps({"X": {"point": 0.01}}), encoding="utf-8"
        )
        donnees = _archives(6)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        # spread 30 points x 0,01 = 0,30 sur un prix proche de 100 : ~30 bp.
        assert res["seances"][0].spread_bps == pytest.approx(30.0, rel=0.5)

    def test_sans_specification_le_cout_n_est_pas_invente(self, tmp_path):
        donnees = _archives(6)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        assert res["seances"][0].spread_bps != res["seances"][0].spread_bps  # NaN

    def test_archive_vide_est_dite_telle_quelle(self, tmp_path):
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(pd.DataFrame()),
        )
        assert res["seances"] == [] and res["motif"] == "archive vide"


class TestSpecifications:
    def test_le_point_est_lu(self, tmp_path):
        (tmp_path / "_specifications.json").write_text(
            json.dumps({"X": {"point": 0.001}}), encoding="utf-8"
        )
        assert mmi.lire_point(tmp_path, "X") == 0.001

    def test_specification_absente_rend_none(self, tmp_path):
        assert mmi.lire_point(tmp_path, "X") is None

    def test_point_nul_est_refuse(self, tmp_path):
        (tmp_path / "_specifications.json").write_text(
            json.dumps({"X": {"point": 0}}), encoding="utf-8"
        )
        assert mmi.lire_point(tmp_path, "X") is None

    def test_specification_illisible_ne_tue_pas(self, tmp_path):
        (tmp_path / "_specifications.json").write_text("{ pas du json", encoding="utf-8")
        assert mmi.lire_point(tmp_path, "X") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Rapport
# ═══════════════════════════════════════════════════════════════════════════════


class TestChiffres:
    """L'artefact structuré : le seuil, le verdict, et le lien avec le rapport.

    `analyser` est le propriétaire unique des chiffres — le rapport imprime ce
    qu'elle rend et l'artefact JSON écrit ce qu'elle rend. Ces tests protègent
    cette invariante et les trois verdicts possibles.
    """

    def test_le_seuil_annonce_est_le_seuil_applique(self):
        assert mmi.seuil_corrige() == mmi.quantile_bilateral(0.05 / mmi.TESTS_FAMILLE)

    def test_un_effet_large_et_net_est_declare_exploitable(self):
        seances = [_seance(i + 1, (i - 20) * 0.00002, (i - 20) * 0.00001) for i in range(40)]
        d = mmi._effet(seances, cout_bps=0.0, seuil_t=mmi.seuil_corrige())
        assert d["t"] > mmi.seuil_corrige()
        assert d["borne_basse_bps"] > 0
        assert "EXPLOITABLE sur cet échantillon" in d["verdict"]

    def test_le_cout_entre_dans_le_verdict(self):
        """Le même effet, facturé à sa propre valeur, cesse d'être exploitable."""
        seances = [_seance(i + 1, (i - 20) * 0.00002, (i - 20) * 0.00001) for i in range(40)]
        gratuit = mmi._effet(seances, cout_bps=0.0, seuil_t=mmi.seuil_corrige())
        cher = mmi._effet(seances, cout_bps=gratuit["ecart_bps"], seuil_t=mmi.seuil_corrige())
        assert gratuit["net_bps"] == pytest.approx(gratuit["ecart_bps"])
        assert cher["borne_basse_bps"] < 0
        assert "NON PROUVÉ" in cher["verdict"]

    def test_un_effet_bruite_est_declare_indecis(self):
        # L'ouverture change de signe, la clôture est constante : les deux
        # groupes ont la même moyenne, donc l'écart est nul par construction.
        seances = [_seance(i + 1, 0.002 if i % 2 else -0.002, 0.01) for i in range(40)]
        d = mmi._effet(seances, cout_bps=0.1, seuil_t=mmi.seuil_corrige())
        assert d["ecart_bps"] == pytest.approx(0.0)
        assert d["verdict"].startswith("INDÉCIS")

    def test_l_artefact_rend_un_chiffre_par_symbole_et_le_compte_des_coupes(self, tmp_path):
        (tmp_path / "_specifications.json").write_text(
            json.dumps({"X": {"point": 0.01}}), encoding="utf-8"
        )
        donnees = _archives(40)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        c = mmi.analyser([res], part_hors=0.3, seuil_t=mmi.seuil_corrige())[0]
        assert c["symbole"] == "X"
        assert c["seances"] == 39
        assert c["coupe"]["dedans"] == int(39 * 0.7)
        assert c["coupe"]["dehors"] == 39 - c["coupe"]["dedans"]
        assert c["premiere_seance"] == res["seances"][0].jour
        # Le coût du verdict est celui que la porte facture : le spread mesuré.
        assert c["effet"]["tout"]["cout_bps"] == pytest.approx(c["cout"]["median_bps"])

    def test_le_rapport_imprime_les_chiffres_de_l_artefact(self, tmp_path):
        """La couture : le rapport ne recalcule rien, il rend ce qu'`analyser` donne."""
        donnees = _archives(40)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        c = mmi.analyser([res], part_hors=0.3, seuil_t=mmi.seuil_corrige())[0]
        texte = mmi.rapport(
            [res], duree_min=390, largeur_min=30, part_hors=0.3, tf="M5", archive=tmp_path
        )
        assert f"séances mesurables : {c['seances']}" in texte
        assert f"médiane {c['cout']['median_bps']:.3f} bp" in texte
        assert f"{c['effet']['tout']['ecart_bps']:+8.3f} bp" in texte

    def test_les_chiffres_sont_serialisables_tels_quels(self, tmp_path):
        """L'artefact part en JSON sans conversion : un ajout non sérialisable casse ici."""
        donnees = _archives(12)
        res = mmi.mesurer_symbole(
            "X",
            timeframe="M5",
            archive=tmp_path,
            duree_min=390,
            largeur_min=30,
            charger=_chargeur(donnees),
        )
        c = mmi.analyser([res], part_hors=0.3, seuil_t=mmi.seuil_corrige())
        texte = json.dumps(c, ensure_ascii=False, default=float)
        assert "borne_basse_bps" in texte and "verdict" in texte


class TestRapport:
    def _seances(self, n: int = 60):
        donnees = _archives(n)
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            res = mmi.mesurer_symbole(
                "X",
                timeframe="M5",
                archive=Path(tmp),
                duree_min=390,
                largeur_min=30,
                charger=_chargeur(donnees),
            )
        return res

    def test_le_rapport_annonce_le_seuil_de_famille_et_les_limites(self, tmp_path):
        res = self._seances()
        texte = mmi.rapport(
            [res], duree_min=390, largeur_min=30, part_hors=0.3, tf="M5", archive=tmp_path
        )
        assert "seuil corrigé" in texte
        assert "CE QUE CETTE MESURE NE DIT PAS" in texte
        assert "12,5" not in texte or "plafond de 12,5" in texte

    def test_le_rapport_nomme_la_seance_et_le_nombre_de_seances(self, tmp_path):
        res = self._seances()
        texte = mmi.rapport(
            [res], duree_min=390, largeur_min=30, part_hors=0.3, tf="M5", archive=tmp_path
        )
        assert "13:30 UTC" in texte
        assert "séances mesurables" in texte

    def test_une_absence_de_donnees_est_ecrite_pas_maquillee(self, tmp_path):
        res = {
            "symbole": "X",
            "seances": [],
            "motif": "archive absente",
            "debut_mode_min": None,
            "jours_ecartes": 0,
        }
        texte = mmi.rapport(
            [res], duree_min=390, largeur_min=30, part_hors=0.3, tf="M5", archive=tmp_path
        )
        assert "AUCUNE MESURE" in texte

    def test_main_sort_en_erreur_sans_archive(self, tmp_path, capsys):
        code = mmi.main(["--symboles", "X", "--archive", str(tmp_path / "vide")])
        assert code == 1
        assert "n'invente rien" in capsys.readouterr().out
