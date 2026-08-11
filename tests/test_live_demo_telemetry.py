import json

from tools.live_demo import _code_portabilite, _compter_tunnel


def test_compteur_tunnel_agrege_par_etape_et_motif():
    stats = {}

    _compter_tunnel(stats, "gate_code", "G1_TREND_SR")
    _compter_tunnel(stats, "gate_code", "G1_TREND_SR")
    _compter_tunnel(stats, "gate_code", "G2_STRUCTURE")
    _compter_tunnel(stats, "gate_verdict", "BLOCK")

    assert stats["tunnel"] == {
        "gate_code": {"G1_TREND_SR": 2, "G2_STRUCTURE": 1},
        "gate_verdict": {"BLOCK": 1},
    }


def test_compteur_tunnel_normalise_un_motif_vide():
    stats = {"tunnel": {}}
    _compter_tunnel(stats, "gate_code", "")
    assert stats["tunnel"]["gate_code"] == {"INCONNU": 1}


def test_compteur_tunnel_accepte_un_volume_et_reste_serialisable():
    stats = {}
    _compter_tunnel(stats, "flow", "catalogue", 149)
    _compter_tunnel(stats, "flow", "portables", 4)
    _compter_tunnel(stats, "flow", "portables", 0)
    assert stats["tunnel"]["flow"] == {"catalogue": 149, "portables": 4}
    json.dumps(stats)


def test_motifs_de_portabilite_sont_stables():
    assert _code_portabilite("marché fermé") == "MARCHE_FERME"
    assert _code_portabilite("actif hors de portée : lot minimum") == "LOT_MIN_HORS_PORTEE"
    assert _code_portabilite("spread trop élevé") == "COUT_SPREAD"
    assert _code_portabilite("injouable à toute échelle — meilleur H4 à 20 %") == "COUT_SPREAD"
