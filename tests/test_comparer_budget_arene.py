"""Le triplement du budget de risque atteint-il une seule decision du bot ?

Ce que ces tests gardent
------------------------
1. **Le decouplage structurel.** Aucun module de `titanium.execution_sim` ne
   nomme un plafond de production ni n'importe `titanium.correlation` ou
   `tools.live_demo`. C'est la raison de fond : l'arene ne PEUT pas lire ce
   budget, elle ne l'importe pas.
2. **La comparaison n'est pas vide par construction.** Les deux passes sont
   reellement posees sous des plafonds differents (relies sur le defaut des
   appelants, pas seulement sur l'attribut du module), et le controle abaisse
   un reglage que l'arene LIT vraiment (`risk.max_gross_exposure`) : sans ce
   temoin, « aucune cellule ne bouge » ne prouverait rien.
3. **La passe porte sur la mesure publiee**, pas sur un harnais qui a derive :
   rejouee au seed de l'artefact, elle le reproduit cellule par cellule.
   L'artefact est sous `results/`, non versionne : le test se saute d'un mot
   quand il est absent, comme les tests qui dependent d'un service externe.

Sans MT5, sans repertoire prive, sans ordre reel.
"""

from __future__ import annotations

import pytest

from tools import comparer_budget_arene as cmp

pytestmark = pytest.mark.unit


def test_l_arene_ne_nomme_aucun_plafond_de_production():
    """Le decouplage est structurel : il se lit dans les sources du simulateur."""
    fautifs = cmp.structure_immunisee()
    assert fautifs == [], (
        "un module du simulateur nomme un plafond de production : "
        "l'arene pourrait alors en dependre sans que rien ne l'annonce"
    )


def test_la_porte_de_grappe_suit_le_plafond_qu_on_lui_donne():
    """La sonde mesure la vraie porte : refus plus tard a 5,7 qu'a 2,0."""
    avant = cmp.porte_de_production(cmp.PLAFONDS_PRECEDENTS)
    apres = cmp.porte_de_production(cmp.PLAFONDS_LIVRES)
    refus_avant = avant["refus_quand_la_grappe_porte_pct"]
    refus_apres = apres["refus_quand_la_grappe_porte_pct"]
    assert refus_avant is not None and refus_apres is not None
    assert refus_avant < refus_apres, (
        f"la porte ne suit pas le plafond : refus a {refus_avant} % sous 2,0 % "
        f"et a {refus_apres} % sous 5,7 %"
    )
    # La porte tombe a un pas du plafond, jamais ailleurs.
    for refus, plafond in ((refus_apres, cmp.PLAFONDS_LIVRES["grappe"]),
                           (refus_avant, cmp.PLAFONDS_PRECEDENTS["grappe"])):
        assert plafond - 0.5 <= refus <= plafond + 0.5


def test_la_porte_ne_mute_aucun_etat_global():
    """Une sonde qui pose un plafond global contaminerait l'appelant."""
    from titanium import correlation

    temoin_plafond = correlation.MAX_RISQUE_GRAPPE_PCT
    temoin_defaut = correlation.place_disponible.__kwdefaults__["plafond"]
    cmp.porte_de_production(cmp.PLAFONDS_PRECEDENTS)
    assert temoin_plafond == correlation.MAX_RISQUE_GRAPPE_PCT, (
        "la sonde a pose un plafond de grappe derriere elle"
    )
    assert temoin_defaut == correlation.place_disponible.__kwdefaults__["plafond"], (
        "la sonde a deplace le plafond que lisent les appelants"
    )


def test_les_deux_passes_portent_bien_des_plafonds_differents():
    """Le budget est VRAIMENT change d'une passe a l'autre, defaut inclus."""
    resultat = cmp.comparer_arene(seed=cmp.SEED_ARTEFACT, jobs=1, quick=True)
    a = resultat["plafonds_effectifs"]["A"]
    b = resultat["plafonds_effectifs"]["B"]
    assert a != b, "les deux passes tourneraient sous les memes plafonds"
    assert a["grappe"] == cmp.PLAFONDS_LIVRES["grappe"]
    assert a["grappe_defaut_appelants"] == cmp.PLAFONDS_LIVRES["grappe"]
    assert a["global"] == cmp.PLAFONDS_LIVRES["global"]
    assert b["grappe"] == cmp.PLAFONDS_PRECEDENTS["grappe"]
    assert b["grappe_defaut_appelants"] == cmp.PLAFONDS_PRECEDENTS["grappe"]
    assert b["global"] == cmp.PLAFONDS_PRECEDENTS["global"]


def test_le_budget_ne_deplace_aucune_cellule_mais_le_controle_si():
    """Le resultat, et le temoin qui l'empeche d'etre creux."""
    resultat = cmp.comparer_arene(seed=cmp.SEED_ARTEFACT, jobs=1, quick=True)
    budget = resultat["budget_vs_budget"]
    controle = resultat["budget_vs_controle"]

    assert budget["cellules_comparees"] > 0
    assert budget["cellules_differentes"] == 0, (
        "le budget de production change une cellule : l'arene le lit, "
        "contrairement a ce que le decouplage structurel annonce"
    )
    assert resultat["a"]["empreinte"] == resultat["b"]["empreinte"]

    # Temoin : un reglage que l'arene lit vraiment doit la deplacer.
    assert controle is not None
    assert controle["cellules_differentes"] > 0, (
        "le controle ne deplace rien : la comparaison serait vide par "
        "construction et ne prouverait pas l'insensibilite au budget"
    )


def test_la_passe_rend_les_plafonds_livres_a_l_appelant():
    """Aucun test ne doit retrouver le plafond precedent pose par un autre."""
    from titanium import correlation

    cmp.comparer_arene(seed=cmp.SEED_ARTEFACT, jobs=1, quick=True)
    attendu = cmp.PLAFONDS_LIVRES["grappe"]
    assert attendu == correlation.MAX_RISQUE_GRAPPE_PCT
    defaut_appelants = correlation.place_disponible.__kwdefaults__["plafond"]
    assert defaut_appelants == attendu


@pytest.mark.skipif(not cmp.ARTEFACT.exists(), reason="artefact d'arene absent")
def test_la_passe_reproduit_l_artefact_publie():
    """Rejouee au seed de l'artefact, la passe doit le rendre cellule par cellule."""
    reproduction = cmp.verifier_reproduction(cmp.SEED_ARTEFACT, jobs=1)
    assert reproduction["present"]
    assert reproduction["cellules_publiees"] == reproduction["cellules_rejouees"]
    assert reproduction["cellules_differentes"] == 0, (
        "la passe diverge de l'artefact publie : les deux passes ne portent "
        "alors pas sur la mesure publiee"
    )
    assert reproduction["empreinte_publiee"] == reproduction["empreinte_rejouee"]
    assert len(reproduction["sha256_ndjson"]) == 64
    assert "sha256_markdown" in reproduction
