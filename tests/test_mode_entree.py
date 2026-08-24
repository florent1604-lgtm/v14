"""Le type d'ordre d'entree est une decision de risque : elle se prouve.

Le 12/08/2026, la boucle est passee au tout-limite (5c5884e). Douze jours plus
tard, `results/limit_lifecycle.ndjson` montrait 689 limites placees, 374
executees et **315 expirees**, pour une economie moyenne de +0.084 R par ordre
rempli et un cumul de -19.7 R : l'attrition coutait davantage que l'economie ne
rapportait. Florent a tranche le 24/08 : « je ne veux pas d'economie mais du
risque ».

Ce test verrouille le routage. Il ne teste pas le prix, il teste QUI envoie
l'ordre -- la seule chose qui decide si un signal valide devient une position
ou disparait.
"""

from titanium.execution.limit_orders import place_limit_order
from titanium.execution.mt5_executor import place_market_order
from tools.live_demo import MODE_ENTREE, _envoi_entree


def test_le_mode_marche_envoie_au_marche():
    assert _envoi_entree("MARCHE") is place_market_order


def test_le_mode_limite_reste_disponible_et_passif():
    assert _envoi_entree("LIMITE") is place_limit_order


def test_le_defaut_du_module_est_le_mode_de_production():
    assert _envoi_entree() is _envoi_entree(MODE_ENTREE)


def test_la_production_prend_le_risque():
    """Garde-fou explicite : revenir au tout-limite doit etre un choix visible."""
    assert MODE_ENTREE == "MARCHE"
    assert _envoi_entree() is place_market_order


def test_un_mode_inconnu_ne_bloque_pas_l_entree():
    """Fail-open cote decision : un libelle errone ne doit pas rendre la boucle
    passive en silence. Tout ce qui n'est pas explicitement "LIMITE" entre au
    marche."""
    assert _envoi_entree("marche") is place_market_order
    assert _envoi_entree("n_importe_quoi") is place_market_order
