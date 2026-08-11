"""Le PROD fantôme.

Sa seule vertu est d'être inerte : il observe la porte en mode strict pour
chiffrer la fréquence du quorum 3, sans jamais toucher à une décision. Les
tests portent donc autant sur ce qu'il fait que sur ce qu'il **ne peut pas
faire** — journaliser sans effet de bord, et ne jamais lever, quoi qu'on lui
donne.
"""

from __future__ import annotations

import json

import pytest

from titanium.shadow import observer, resume


class _Porte:
    """Décision minimale, avec la forme réelle de `confluence_gate.Decision`."""

    def __init__(self, verdict="ENTER", code="", reasons=None, gates=(),
                 side=1, famille="continuation", support_passed=0):
        self.verdict = verdict
        self.code = code
        self.reasons = list(reasons or [])
        self.gates = list(gates)
        self.side = side
        self.setup_family = famille
        self.support_passed = support_passed


class _G:
    def __init__(self, passed):
        self.passed = passed


@pytest.fixture
def strict(monkeypatch):
    """Remplace la porte pour contrôler ce que le mode strict répond."""
    def poser(decision):
        from titanium.gates import confluence_gate
        monkeypatch.setattr(confluence_gate, "evaluate",
                            lambda *a, **k: decision)
    return poser


FEATS = {"_trace": {"bar_time": "2026-08-07T10:00:00"},
         "cost": {"edge_ok": None}}


class TestObserver:
    def test_journalise_un_enter_strict(self, tmp_path, strict):
        strict(_Porte(verdict="ENTER", gates=[_G(True)] * 3))
        j = tmp_path / "s.ndjson"
        ligne = observer(FEATS, "XAUUSD", "ENTER", j)
        assert ligne is not None
        assert json.loads(j.read_text(encoding="utf-8"))["verdict_prod"] == "ENTER"

    def test_journalise_une_divergence(self, tmp_path, strict):
        """Le cas qui compte : EXPLORE trade, PROD aurait refusé."""
        strict(_Porte(verdict="WAIT", code="G_QUORUM"))
        j = tmp_path / "s.ndjson"
        ligne = observer(FEATS, "XAUUSD", "ENTER", j)
        assert ligne["verdict_explore"] == "ENTER"
        assert ligne["verdict_prod"] == "WAIT"

    def test_ignore_les_accords_negatifs(self, tmp_path, strict):
        """Les deux modes disent WAIT : rien à apprendre, on n'écrit pas.
        Sinon le journal noierait le signal utile sous le bruit."""
        strict(_Porte(verdict="WAIT"))
        j = tmp_path / "s.ndjson"
        assert observer(FEATS, "XAUUSD", "WAIT", j) is None
        assert not j.exists()

    def test_compte_les_piliers_passes(self, tmp_path, strict):
        strict(_Porte(verdict="ENTER",
                      gates=[_G(True)] * 6, support_passed=3))
        j = tmp_path / "s.ndjson"
        assert observer(FEATS, "XAUUSD", "ENTER", j)["piliers"] == 3

    def test_note_edge_ok_tel_quel(self, tmp_path, strict):
        """`edge_ok` n'est injecté par personne : on note ce que la porte a
        vu, pas ce qu'on suppose. `None` est une information."""
        strict(_Porte(verdict="ENTER"))
        j = tmp_path / "s.ndjson"
        assert observer(FEATS, "XAUUSD", "ENTER", j)["edge_ok_vu"] is None

    def test_ne_leve_jamais_sur_porte_cassee(self, tmp_path, monkeypatch):
        """Une observation ne doit pas pouvoir arrêter le trading."""
        from titanium.gates import confluence_gate

        def boum(*a, **k):
            raise RuntimeError("porte cassée")
        monkeypatch.setattr(confluence_gate, "evaluate", boum)
        assert observer(FEATS, "XAUUSD", "ENTER", tmp_path / "s.ndjson") is None

    def test_ne_leve_jamais_sur_chemin_impossible(self, tmp_path, strict):
        strict(_Porte(verdict="ENTER"))
        assert observer(FEATS, "X", "ENTER", tmp_path / "n" / "o" / "s.ndjson")

    def test_ne_leve_jamais_sur_feats_vides(self, tmp_path, strict):
        strict(_Porte(verdict="ENTER"))
        assert observer({}, "X", "ENTER", tmp_path / "s.ndjson") is not None

    def test_ajoute_sans_ecraser(self, tmp_path, strict):
        strict(_Porte(verdict="ENTER"))
        j = tmp_path / "s.ndjson"
        for _ in range(3):
            observer(FEATS, "XAUUSD", "ENTER", j)
        assert len(j.read_text(encoding="utf-8").strip().splitlines()) == 3


class TestResume:
    def _ecrire(self, tmp_path, lignes):
        j = tmp_path / "s.ndjson"
        j.write_text("\n".join(json.dumps(x) for x in lignes) + "\n",
                     encoding="utf-8")
        return j

    def test_journal_absent(self, tmp_path):
        assert resume(tmp_path / "rien.ndjson") == {"lignes": 0}

    def test_compte_les_enter_prod(self, tmp_path):
        j = self._ecrire(tmp_path, [
            {"verdict_prod": "ENTER", "symbol": "XAUUSD"},
            {"verdict_prod": "ENTER", "symbol": "XAUUSD"},
            {"verdict_prod": "WAIT", "verdict_explore": "ENTER",
             "code_prod": "G_QUORUM"},
        ])
        r = resume(j)
        assert r["prod_aurait_entre"] == 2
        assert r["explore_seul"] == 1
        assert r["par_symbole"]["XAUUSD"] == 2

    def test_agrege_les_motifs_de_refus(self, tmp_path):
        j = self._ecrire(tmp_path, [
            {"verdict_prod": "WAIT", "verdict_explore": "ENTER",
             "code_prod": "G_QUORUM"},
            {"verdict_prod": "WAIT", "verdict_explore": "ENTER",
             "code_prod": "G_QUORUM"},
            {"verdict_prod": "BLOCK", "verdict_explore": "ENTER",
             "code_prod": "BLOCK_EDGE_UNPROVEN"},
        ])
        assert resume(j)["motifs_de_refus"]["G_QUORUM"] == 2

    def test_distingue_quorum_et_edge_non_prouve(self, tmp_path):
        """Zéro ENTER PROD ne veut pas dire que le quorum n'arrive jamais."""
        j = self._ecrire(tmp_path, [
            {"verdict_prod": "BLOCK", "verdict_explore": "ENTER",
             "code_prod": "BLOCK_PILLAR_MISSING"},
            {"verdict_prod": "BLOCK", "verdict_explore": "ENTER",
             "code_prod": "BLOCK_EDGE_UNPROVEN"},
            {"verdict_prod": "ENTER", "symbol": "BTCUSD"},
        ])
        r = resume(j)
        assert r["prod_aurait_entre"] == 1
        assert r["quorum_atteint"] == 2
        assert r["edge_non_prouve"] == 1

    def test_ligne_corrompue_ignoree(self, tmp_path):
        j = tmp_path / "s.ndjson"
        j.write_text('{"verdict_prod": "ENTER"}\npas du json\n\n',
                     encoding="utf-8")
        assert resume(j)["lignes"] == 1


class TestInertie:
    """Le module ne doit contenir aucun chemin d'exécution."""

    def _source(self):
        from pathlib import Path

        import titanium.shadow as m
        return Path(m.__file__).read_text(encoding="utf-8")

    def test_aucune_execution(self):
        src = self._source()
        for interdit in ("order_send", "OrderSend", "positions_get",
                         "PositionClose", "executer", "envoyer_ordre"):
            assert interdit not in src, f"{interdit} dans un module d'observation"

    def test_reprend_le_quorum_de_la_porte(self):
        """Une constante dupliquée finirait par diverger et mentir."""
        assert "from titanium.gates.confluence_gate import QUORUM_PROD" \
            in self._source()

    def test_n_injecte_pas_edge_ok(self):
        """Injecter le verdict d'edge ferait tomber BLOCK_EDGE_NEGATIVE, qui
        s'applique AUSSI en EXPLORE — cela éteindrait la boucle
        d'accumulation qu'on cherche à alimenter.

        On vérifie le CODE, pas la prose : la docstring parle légitimement
        d'`edge_ok`, et un test qui cherche la chaîne dans tout le fichier
        échouerait sur sa propre documentation.
        """
        import ast

        arbre = ast.parse(self._source())
        appels = [n for n in ast.walk(arbre) if isinstance(n, ast.Call)]
        for appel in appels:
            nom = getattr(appel.func, "attr", None) \
                or getattr(appel.func, "id", None)
            assert nom not in ("build_feats", "edge_ok_for"), (
                f"{nom} appelé : l'observateur alimenterait la décision")
            for kw in appel.keywords:
                assert kw.arg != "edge_ok", "edge_ok injecté dans un appel"
