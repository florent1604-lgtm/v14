"""Le calibrateur de fenetre de blackout mesure-t-il ce qu'il pretend ?

La seule preuve qui vaille : injecter un choc dont on connait les bornes et
verifier que l'outil les retrouve. Puis retirer le choc et verifier qu'il ne
trouve plus rien — sans cette seconde mesure, un outil qui rend toujours la meme
fenetre passerait le premier test.

Aucun acces MT5, aucun reseau : l'archive est fabriquee en fixtures, et le
calendrier est lu par le meme lecteur que la production.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tools import calibrer_fenetre_blackout as cal

pytestmark = pytest.mark.unit

#: Le pas de la barre fabriquee.
BARRE_S = 300
#: Le spread hors publication.
BASE = 10.0
#: Le spread au pic.
PIC = 30.0
#: Bornes du choc injecte, en secondes autour de la publication.
CHOC_AVANT_S = 600
CHOC_APRES_S = 1800
#: La publication de reference, un mardi a 13:30 UTC (heure des publications US).
PUBLICATION = "2026-06-02T13:30:00+00:00"
SYMBOLES = ("EURUSD", "US500", "BTCUSD", "ETHUSD", "XAUUSD", "GER40")


def _barres(
    symbole: str,
    *,
    choc: tuple[int, int] | None = None,
    chocs: list[tuple[int, int]] | None = None,
    debut: str = "2026-05-04",
    jours: int = 40,
) -> pd.DataFrame:
    """Une archive synthetique : spread constant, choc injecte si demande."""
    index = pd.date_range(
        start=pd.Timestamp(debut, tz="UTC"),
        periods=jours * 24 * 60 // 5,
        freq=f"{BARRE_S}s",
    )
    index = index[(index.weekday < 5)]
    spread = pd.Series(BASE, index=index, dtype="float64")
    amplitude = pd.Series(0.001, index=index, dtype="float64")
    for fenetre in ([choc] if choc is not None else []) + list(chocs or []):
        position = int(index.searchsorted(pd.Timestamp(fenetre[0], tz="UTC")))
        avant = min(int(index.searchsorted(pd.Timestamp(fenetre[1], tz="UTC"))),
                    len(index))
        # Une rampe : le pic en debut de fenetre, retour a la base ensuite.
        for rang in range(position, min(avant, len(index))):
            reste = (avant - rang) / max(1, avant - position)
            spread.iloc[rang] = BASE + (PIC - BASE) * reste
    return pd.DataFrame(
        {
            "symbole": symbole,
            "timeframe": "M5",
            # `date_range` peut rendre une precision en secondes : convertir
            # explicitement, sinon on ecrit des epoch en secondes lues comme
            # des nanosecondes et tout horodatage tombe en 1970.
            "time_utc": index.tz_convert(None).to_numpy().astype("datetime64[s]").astype("int64"),
            "open": 1.0,
            "high": 1.0 + amplitude,
            "low": 1.0,
            "close": 1.0,
            "spread": spread,
            "reconstruit": False,
        }
    )


def _archive(tmp_path, *, choc=None, chocs=None, symboles=SYMBOLES, jours=40):
    """Ecrit une archive M5 et rend sa racine."""
    racine = tmp_path / "barres" / "M5"
    racine.mkdir(parents=True, exist_ok=True)
    for symbole in symboles:
        _barres(symbole, choc=choc, chocs=chocs, jours=jours).to_parquet(
            racine / f"{symbole}.parquet", index=False
        )
    return tmp_path / "barres"


def _calendrier(tmp_path, impacts=None, instants=(PUBLICATION,)):
    impacts = tuple(impacts) if impacts is not None else ("High",) * len(instants)
    chemin = tmp_path / "calendrier_macro.json"
    chemin.write_text(
        json.dumps(
            {"events": [
                {
                    "title": f"publication {rang}",
                    "currency": "USD",
                    "scheduled_at": instant,
                    "impact": impact,
                    "forecast": 1.0,
                    "previous": 1.0,
                }
                for rang, (instant, impact) in enumerate(zip(instants, impacts, strict=True))
            ]}
        ),
        encoding="utf-8",
    )
    return chemin


def _choc(inverse: bool = True) -> tuple[str, str]:
    """Les bornes de l'instant du choc : l'outil les rend en duree."""
    instant = pd.Timestamp(PUBLICATION)
    debut = (instant - pd.Timedelta(seconds=CHOC_AVANT_S)).isoformat()
    fin = (instant + pd.Timedelta(seconds=CHOC_APRES_S)).isoformat()
    return (debut, fin) if inverse else (fin, debut)


def test_l_univers_est_lu_dans_les_classes_d_actifs_de_la_boucle(tmp_path):
    archive = _archive(tmp_path, choc=None)
    univers = cal.univers_negocie("M5", archive)
    assert set(SYMBOLES) <= set(univers.presents)
    assert "HSI" in univers.absents


def test_la_fenetre_retrouve_le_choc_injecte(tmp_path):
    """Le test central : les bornes mesurees sont celles qu'on a posees."""
    archive = _archive(tmp_path, choc=_choc())
    calendrier = _calendrier(tmp_path)
    ancres, detail = cal.ancres_calendrier(calendrier, "HIGH")
    assert detail["retenus"] == 1 and len(ancres) == 1

    series = cal.charger_series("EURUSD", "M5", archive)
    resultat = cal.mesurer(series, ancres, cal.SEUIL_EXCES)
    fenetre = resultat["fenetre"]
    assert fenetre["debut_s"] == CHOC_AVANT_S, fenetre
    assert fenetre["fin_s"] is not None, fenetre
    assert abs(fenetre["fin_s"] - CHOC_APRES_S) <= BARRE_S, fenetre


def test_sans_choc_injecte_la_fenetre_disparait(tmp_path):
    """Anti-tautologie : un outil qui rend toujours une fenetre echouerait ici."""
    archive = _archive(tmp_path, choc=None)
    calendrier = _calendrier(tmp_path)
    ancres, _ = cal.ancres_calendrier(calendrier, "HIGH")
    series = cal.charger_series("EURUSD", "M5", archive)
    fenetre = cal.mesurer(series, ancres, cal.SEUIL_EXCES)["fenetre"]
    assert fenetre["debut_s"] is None and fenetre["fin_s"] is None, fenetre
    assert "aucun exces" in fenetre["raison"]


def test_la_ligne_de_base_exclut_les_fenetres_d_ancre(tmp_path):
    """Sans exclusion, la ligne de base absorberait le choc mesure."""
    archive = _archive(tmp_path, choc=_choc())
    series = cal.charger_series("EURUSD", "M5", archive)
    ancres, _ = cal.ancres_calendrier(_calendrier(tmp_path), "HIGH")
    avec = cal.ratios(series, ancres)
    sans = cal.ratios(series, [])
    assert avec["exclu"].any()
    assert not sans["exclu"].any()
    # Le ratio au PIC reste entier quand la fenetre est exclue de la base.
    # La rampe place le pic a la borne basse de la fenetre injectee.
    pic = pd.Timestamp(PUBLICATION) - pd.Timedelta(seconds=CHOC_AVANT_S)
    rang = int(avec["t"].searchsorted(pic.to_pydatetime()))
    assert float(avec["ratio_spread"].iloc[rang]) == pytest.approx(PIC / BASE, rel=0.05)


def test_une_publication_hebdomadaire_dilue_la_base_si_on_ne_l_exclut_pas(tmp_path):
    """Mesure : douze publications, et la mediane par creneau les absorbe.

    C'est ce cas qui justifie l'exclusion — une publication isolee ne
    deplacerait pas une mediane, une publication hebdomadaire si.
    """
    instants = [
        pd.Timestamp("2026-05-05T13:30:00+00:00") + pd.Timedelta(weeks=rang)
        for rang in range(11)
    ]
    fenetres = [
        (
            (instant - pd.Timedelta(seconds=CHOC_AVANT_S)).isoformat(),
            (instant + pd.Timedelta(seconds=CHOC_APRES_S)).isoformat(),
        )
        for instant in instants
    ]
    # L'archive doit etre plus longue que la serie de publications, sinon
    # tous les creneaux de ce mardi sont choques et la base exclue est vide.
    archive = _archive(tmp_path, chocs=fenetres, jours=120)
    calendrier = _calendrier(tmp_path, instants=[i.isoformat() for i in instants])
    ancres, _ = cal.ancres_calendrier(calendrier, "HIGH")
    assert len(ancres) == len(instants)

    series = cal.charger_series("EURUSD", "M5", archive)
    avec = cal.ratios(series, ancres)
    sans = cal.ratios(series, [])

    pic = (instants[0] - pd.Timedelta(seconds=CHOC_AVANT_S)).to_pydatetime()
    ratio_avec = float(avec["ratio_spread"].iloc[int(avec["t"].searchsorted(pic))])
    ratio_sans = float(sans["ratio_spread"].iloc[int(sans["t"].searchsorted(pic))])
    assert ratio_avec == pytest.approx(PIC / BASE, rel=0.05)
    assert ratio_sans < 1.5, (
        f"la base sans exclusion devrait absorber le choc, ratio={ratio_sans}"
    )


def test_l_impact_faible_n_est_pas_une_ancre(tmp_path):
    """`min_impact=HIGH` ne retient pas une publication moyenne, et le dit."""
    calendrier = _calendrier(
        tmp_path,
        impacts=("Medium", "High"),
        instants=("2026-06-01T12:30:00+00:00", PUBLICATION),
    )
    ancres, detail = cal.ancres_calendrier(calendrier, "HIGH")
    assert detail["evenements"] == 2
    assert detail["retenus"] == 1
    assert [instant.isoformat() for instant in ancres] == ["2026-06-02T13:30:00+00:00"]


def test_un_calendrier_absent_leve(tmp_path):
    """Un calendrier manquant est une erreur, pas un calendrier vide."""
    with pytest.raises(ValueError):
        cal.ancres_calendrier(tmp_path / "absent.json", "HIGH")


def test_les_ancres_ne_se_chevauchent_pas(tmp_path):
    """Deux fenetres qui se recouvrent ne font pas deux observations."""
    archive = _archive(tmp_path, choc=None)
    debut = pd.Timestamp("2026-06-01T09:00:00+00:00")
    par_symbole = {}
    for symbole in SYMBOLES:
        series = cal.charger_series(symbole, "M5", archive)
        cadre = cal.ratios(series, [])
        # Trois minutes synchronisees, a 300 s d'ecart.
        for secondes in (0, 300, 600):
            cible = (debut + pd.Timedelta(seconds=secondes)).to_pydatetime()
            rang = int(cadre["t"].searchsorted(cible))
            assert 0 <= rang < len(cadre), "fixture hors de la fenetre"
            cadre.loc[rang, "ratio_spread"] = 5.0
        par_symbole[symbole] = cadre
    chocs, detail = cal.detecter_chocs(
        par_symbole, min_symboles=len(SYMBOLES), seuil=cal.SEUIL_EXCES
    )
    # Un seul episode, meme si trois minutes sont touchees.
    assert len(chocs) == 1
    assert detail["minutes_synchronisees"] >= 3


def test_un_choc_d_un_seul_symbole_n_est_pas_synchronise(tmp_path):
    archive = _archive(tmp_path, choc=None)
    par_symbole = {}
    for symbole in SYMBOLES:
        cadre = cal.ratios(cal.charger_series(symbole, "M5", archive), [])
        if symbole == "EURUSD":
            cadre.loc[100, "ratio_spread"] = 5.0
        par_symbole[symbole] = cadre
    chocs, _ = cal.detecter_chocs(
        par_symbole, min_symboles=len(SYMBOLES), seuil=cal.SEUIL_EXCES
    )
    assert chocs == []


def test_le_critere_de_saturation_est_mesure(tmp_path):
    """Un critere qui touche presque toutes les minutes doit le DIRE."""
    archive = _archive(tmp_path, choc=None)
    par_symbole = {
        symbole: cal.ratios(cal.charger_series(symbole, "M5", archive), [])
        for symbole in SYMBOLES
    }
    # Tout le monde est « anormal » en permanence : le critere est sature.
    for cadre in par_symbole.values():
        cadre["ratio_spread"] = 5.0
    _, detail = cal.detecter_chocs(
        par_symbole, min_symboles=len(SYMBOLES), seuil=cal.SEUIL_EXCES
    )
    assert detail["sature"] is True
    assert detail["part_minutes_synchronisees"] > 0.10


def test_les_plages_horaires_a_exclure_gere_minuit():
    assert cal._minutes_bloquees(["21:30-22:30"]) == set(range(21 * 60 + 30, 22 * 60 + 31))
    cheval = cal._minutes_bloquees(["23:30-00:30"])
    assert 23 * 60 + 45 in cheval and 15 in cheval and 12 * 60 not in cheval
