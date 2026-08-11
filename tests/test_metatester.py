"""Le pont vers le testeur natif MT5.

Deux choses comptent ici et sont testées en priorité :

- le format d'échange doit survivre au parseur MQL5, qui est rustique et
  échoue en silence — un `\\r` de trop et l'EA lit zéro signal sans le dire ;
- le lecteur de rapport doit encaisser les variations de locale du
  terminal (virgule décimale, espaces de milliers, intitulés traduits),
  faute de quoi une optimisation entière est lue comme un tas de zéros.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from titanium.bridge.metatester import (
    ENTETE, NOM_EA, NOM_SIGNAUX, Passe, Plage, _epoch, _flottant,
    combinaisons, ecrire_ini, exporter_signaux, lire_rapport, retenir,
)


@dataclass
class FauxTrade:
    symbol: str = "XAUUSD"
    side: int = 1
    bar_entree: str = "2026-03-02T14:30:00"
    prix_entree: float = 2050.5
    sl: float = 2040.0
    tp: float = 2071.5
    r_unit: float = 10.5
    contexte: str = "XAUUSD|long|continuation|4p"
    pillars: int = 4
    family: str = "continuation"
    indicators: dict = field(default_factory=dict)


# ── Export ──────────────────────────────────────────────────────────────

class TestExport:
    def test_entete_et_ligne(self, tmp_path):
        f = tmp_path / "s.csv"
        assert exporter_signaux([FauxTrade()], f) == 1
        lignes = f.read_text(encoding="ascii").strip().split("\n")
        assert lignes[0] == ";".join(ENTETE)
        assert lignes[1].split(";")[0] == "XAUUSD"

    def test_pas_de_retour_chariot(self, tmp_path):
        """Le lecteur CSV de MQL5 rattache un \\r au dernier champ."""
        f = tmp_path / "s.csv"
        exporter_signaux([FauxTrade()], f)
        assert b"\r" not in f.read_bytes()

    def test_ascii_pur(self, tmp_path):
        """L'EA lit en FILE_ANSI : tout accent corromprait le champ."""
        f = tmp_path / "s.csv"
        exporter_signaux([FauxTrade(contexte="café|long|x|4p")], f)
        f.read_text(encoding="ascii")   # lèverait si non-ASCII

    def test_tri_chronologique(self, tmp_path):
        """L'EA avance un curseur sans revenir : l'ordre est un contrat."""
        f = tmp_path / "s.csv"
        exporter_signaux([
            FauxTrade(bar_entree="2026-03-05T10:00:00"),
            FauxTrade(bar_entree="2026-03-01T10:00:00"),
            FauxTrade(bar_entree="2026-03-03T10:00:00"),
        ], f)
        epochs = [int(l.split(";")[1])
                  for l in f.read_text().strip().split("\n")[1:]]
        assert epochs == sorted(epochs)

    def test_rejette_r_unit_nul(self, tmp_path):
        """Sans distance de stop, l'EA ne peut pas dimensionner."""
        f = tmp_path / "s.csv"
        assert exporter_signaux([FauxTrade(r_unit=0.0)], f) == 0

    def test_rejette_side_invalide(self, tmp_path):
        f = tmp_path / "s.csv"
        assert exporter_signaux([FauxTrade(side=0)], f) == 0

    def test_rejette_date_illisible(self, tmp_path):
        f = tmp_path / "s.csv"
        assert exporter_signaux([FauxTrade(bar_entree="jamais")], f) == 0

    def test_multi_symboles(self, tmp_path):
        """Un seul fichier porte tous les symboles ; l'EA filtre."""
        f = tmp_path / "s.csv"
        n = exporter_signaux(
            [FauxTrade(symbol="XAUUSD"), FauxTrade(symbol="EURUSD")], f)
        assert n == 2
        syms = {l.split(";")[0]
                for l in f.read_text().strip().split("\n")[1:]}
        assert syms == {"XAUUSD", "EURUSD"}

    def test_cree_le_dossier(self, tmp_path):
        f = tmp_path / "absent" / "s.csv"
        exporter_signaux([FauxTrade()], f)
        assert f.exists()


class TestEpoch:
    @pytest.mark.parametrize("txt", [
        "2026-03-02T14:30:00", "2026-03-02 14:30:00",
        "2026-03-02T14:30:00Z", "2026-03-02T14:30:00+00:00",
        "2026-03-02 14:30",
    ])
    def test_formats_acceptes(self, txt):
        assert _epoch(txt) is not None

    def test_utc_pas_local(self):
        """Le testeur raisonne en heure serveur ; un décalage local
        déporterait chaque signal d'une ou deux barres."""
        assert _epoch("1970-01-01T00:00:00") == 0

    @pytest.mark.parametrize("txt", ["", None, "n/a", "02/03/2026"])
    def test_refus(self, txt):
        assert _epoch(txt) is None


# ── Plages et grille ────────────────────────────────────────────────────

class TestPlage:
    def test_valeur_figee(self):
        assert Plage("InpRiskPct", 1.0).rendu_ini() == "InpRiskPct=1"

    def test_balayage(self):
        r = Plage("InpSLmult", 1.0, 0.5, 3.0).rendu_ini()
        assert r == "InpSLmult=1||1||0.5||3||Y"

    def test_cardinal(self):
        assert Plage("x", 1.0, 0.5, 3.0).cardinal() == 5
        assert Plage("x", 1.0).cardinal() == 1

    def test_combinaisons_est_un_produit(self):
        """Le chiffre qui dit ce que vaut un « meilleur résultat »."""
        assert combinaisons([
            Plage("a", 1, 1, 3),      # 3
            Plage("b", 0, 1, 1),      # 2
            Plage("c", 5),            # 1
        ]) == 6

    def test_pas_de_notation_scientifique(self):
        """MT5 lit mal `1e-05` et prend la valeur pour zéro."""
        assert "e" not in Plage("x", 0.00001).rendu_ini().lower()


class TestIni:
    def _ini(self, tmp_path, **kw):
        d = dict(symbol="XAUUSD", periode="M15", depuis="2025.01.01",
                 jusqua="2026.01.01", plages=[Plage("InpSLmult", 1.0, 0.5, 3.0)],
                 rapport="r")
        d.update(kw)
        p = ecrire_ini(tmp_path / "t.ini", **d)
        return p.read_text(encoding="utf-16")

    def test_utf16(self, tmp_path):
        """MT5 refuse silencieusement un .ini qui n'est pas en UTF-16."""
        p = ecrire_ini(tmp_path / "t.ini", symbol="XAUUSD", periode="M15",
                       depuis="2025.01.01", jusqua="2026.01.01",
                       plages=[], rapport="r")
        assert p.read_bytes()[:2] in (b"\xff\xfe", b"\xfe\xff")

    def test_critere_custom(self, tmp_path):
        """Sans cela l'optimiseur maximise le profit, donc la taille."""
        assert "OptimizationCriterion=6" in self._ini(tmp_path)

    def test_forward_actif_par_defaut(self, tmp_path):
        """Le seul garde-fou sérieux contre le sur-ajustement."""
        assert "ForwardMode=2" in self._ini(tmp_path)

    def test_arret_automatique(self, tmp_path):
        """Sans ShutdownTerminal, l'appel ne rend jamais la main."""
        assert "ShutdownTerminal=1" in self._ini(tmp_path)

    def test_ea_nomme(self, tmp_path):
        assert f"Expert={NOM_EA}" in self._ini(tmp_path)

    def test_plages_transmises(self, tmp_path):
        assert "InpSLmult=1||1||0.5||3||Y" in self._ini(tmp_path)

    def test_ticks_reels_par_defaut(self, tmp_path):
        """C'est l'écart Python/réel que ce pont existe pour mesurer."""
        assert "Model=4" in self._ini(tmp_path)


# ── Lecture du rapport ──────────────────────────────────────────────────

def _xml(lignes: list[list[str]]) -> str:
    ns = "urn:schemas-microsoft-com:office:spreadsheet"
    corps = ""
    for l in lignes:
        cells = "".join(
            f'<Cell><Data ss:Type="String">{v}</Data></Cell>' for v in l)
        corps += f"<Row>{cells}</Row>"
    return (f'<?xml version="1.0"?><Workbook xmlns="{ns}" xmlns:ss="{ns}">'
            f"<Worksheet><Table>{corps}</Table></Worksheet></Workbook>")


class TestRapport:
    ENT = ["Pass", "Result", "Profit", "Trades", "Profit Factor",
           "Drawdown", "Sharpe Ratio", "Custom", "InpSLmult", "InpRR"]

    def _ecrire(self, tmp_path, lignes):
        f = tmp_path / "r.xml"
        f.write_text(_xml(lignes), encoding="utf-16")
        return f

    def test_absent_rend_liste_vide(self, tmp_path):
        assert lire_rapport(tmp_path / "rien.xml") == []

    def test_xml_casse_ne_leve_pas(self, tmp_path):
        f = tmp_path / "r.xml"
        f.write_text("<Workbook><oops>", encoding="utf-16")
        assert lire_rapport(f) == []

    def test_lecture_nominale(self, tmp_path):
        f = self._ecrire(tmp_path, [
            self.ENT,
            ["0", "1500.0", "1500.0", "142", "1.85", "230.0", "1.2",
             "0.315", "2.0", "3.0"],
        ])
        p = lire_rapport(f)
        assert len(p) == 1
        assert p[0].trades == 142
        assert p[0].profit_factor == pytest.approx(1.85)
        assert p[0].esperance_r == pytest.approx(0.315)

    def test_parametres_recuperes(self, tmp_path):
        """Ce sont eux qu'on rejouera : les perdre vide l'exercice."""
        f = self._ecrire(tmp_path, [
            self.ENT,
            ["0", "1500", "1500", "142", "1.85", "230", "1.2",
             "0.315", "2.5", "3.0"],
        ])
        params = lire_rapport(f)[0].params
        assert params["InpSLmult"] == pytest.approx(2.5)
        assert params["InpRR"] == pytest.approx(3.0)

    def test_locale_francaise(self, tmp_path):
        """Terminal en français : virgule décimale, espaces de milliers."""
        f = self._ecrire(tmp_path, [
            ["Pass", "Résultat", "Transactions", "Facteur de profit",
             "Personnalisé"],
            ["0", "1 500,50", "142", "1,85", "0,315"],
        ])
        p = lire_rapport(f)[0]
        assert p.profit_factor == pytest.approx(1.85)
        assert p.esperance_r == pytest.approx(0.315)

    def test_statistiques_non_mappees_ne_deviennent_pas_parametres(self, tmp_path):
        """Un en-tête inconnu n'est PAS un réglage.

        Sans ce filtre, « Recovery Factor » ou « Equity DD % » entrent dans
        `params` et on croit rejouer un réglage qui n'existe pas.
        """
        f = self._ecrire(tmp_path, [
            ["Pass", "Result", "Trades", "Profit Factor", "Custom",
             "Recovery Factor", "Equity DD %", "InpSLmult"],
            ["0", "1500", "142", "1.85", "0.315", "5.17", "8.82", "2.5"],
        ])
        params = lire_rapport(f)[0].params
        assert set(params) == {"InpSLmult"}
        assert params["InpSLmult"] == pytest.approx(2.5)

    def test_plusieurs_passes(self, tmp_path):
        f = self._ecrire(tmp_path, [self.ENT] + [
            ["0", "100", "100", "50", "1.1", "10", "0.5", "0.05", "1", "2"],
            ["1", "900", "900", "80", "1.9", "20", "1.5", "0.31", "2", "3"],
        ])
        assert len(lire_rapport(f)) == 2


class TestFlottant:
    @pytest.mark.parametrize("txt,att", [
        ("1.85", 1.85), ("1,85", 1.85), ("1 500,50", 1500.50),
        ("1,500.50", 1500.50), ("-0.42", -0.42), ("85%", 85.0),
        ("", 0.0), ("n/a", 0.0),
    ])
    def test_variantes(self, txt, att):
        assert _flottant(txt) == pytest.approx(att)


# ── Filtrage : le protocole hérité de V12 ───────────────────────────────

class TestRetenir:
    def _p(self, **kw):
        d = dict(trades=50, profit_factor=1.5, esperance_r=0.2)
        d.update(kw)
        return Passe(**d)

    def test_rejette_echantillon_court(self):
        """« PF 8.0 » tiré de 3 trades est la fabrique à illusions."""
        assert retenir([self._p(trades=12, profit_factor=8.0)]) == []

    def test_rejette_pf_insuffisant(self):
        assert retenir([self._p(profit_factor=1.05)]) == []

    def test_rejette_esperance_negative(self):
        assert retenir([self._p(esperance_r=-0.1)]) == []

    def test_garde_le_conforme(self):
        assert len(retenir([self._p()])) == 1

    def test_tri_par_esperance_pas_par_profit(self):
        """Trier sur le profit récompenserait la taille, pas le réglage."""
        a = self._p(esperance_r=0.10, resultat=9000.0)
        b = self._p(esperance_r=0.40, resultat=100.0)
        assert retenir([a, b])[0] is b

    def test_plafond(self):
        assert len(retenir([self._p() for _ in range(30)], top=5)) == 5


# ── Cohérence avec l'EA ─────────────────────────────────────────────────

class TestContratEA:
    """Le fichier .mq5 et ce module doivent rester d'accord."""

    def _source(self):
        from pathlib import Path
        import titanium.bridge as b
        return (Path(b.__file__).parent / f"{NOM_EA}.mq5").read_text(
            encoding="utf-8", errors="replace")

    def test_ea_present(self):
        assert self._source()

    def test_meme_nom_de_fichier(self):
        """`tester_file` est un littéral MQL5 : il ne peut pas dériver."""
        assert f'tester_file "{NOM_SIGNAUX}"' in self._source()
        assert f'FileOpen("{NOM_SIGNAUX}"' in self._source()

    def test_separateur_point_virgule(self):
        assert "FILE_CSV" in self._source() and "';'" in self._source()

    def test_ea_ne_decide_rien(self):
        """Le jour où l'EA calcule un signal, les deux implémentations
        divergent — c'est exactement la faute qui a tué V12."""
        src = self._source()
        for interdit in ("iRSI", "iMA(", "iATR", "iMACD", "iStochastic",
                         "iBands", "iADX"):
            assert interdit not in src, (
                f"{interdit} dans l'EA : il recalculerait une décision")

    def test_ea_annule_les_petits_echantillons(self):
        assert "InpMinTrades" in self._source()

    def test_ea_rend_une_esperance_pas_un_profit(self):
        src = self._source()
        assert "double OnTester()" in src
        assert "STAT_PROFIT" not in src.split("OnTester()")[1][:900]
