

class TestMultiSymboles:
    """Un bloc par symbole dans un seul fichier.

    Avant cette découpe, le fichier ne portait qu'un symbole et la ligne
    `if(sym != _Symbol) return;` faisait que **seul le dernier symbole écrit
    dessinait** — neuf fenêtres sur dix restaient vides par construction.
    Constaté le 07/08/2026 avec dix graphiques ouverts et aucun tracé.
    """

    def _charge(self, sym):
        from titanium.bridge.mt5_zones import Plan, ZonePayload
        return ZonePayload(symbol=sym, timeframe="M15", verdict="ENTER",
                           plan=Plan(side=1, entry=1.1, sl=1.09, tp=1.13))

    def test_ecrit_tous_les_symboles(self, tmp_path):
        from titanium.bridge.mt5_zones import NOM_FICHIER, ecrire_multi
        ecrire_multi([self._charge(s) for s in ("EURUSD", "XAUUSD", "GER40")],
                     tmp_path)
        txt = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        for s in ("EURUSD", "XAUUSD", "GER40"):
            assert f'"{s}"' in txt

    def test_blocs_separes(self, tmp_path):
        from titanium.bridge.mt5_zones import (
            NOM_FICHIER, SEPARATEUR, ecrire_multi,
        )
        ecrire_multi([self._charge(s) for s in ("EURUSD", "XAUUSD")], tmp_path)
        txt = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        assert len(txt.split(SEPARATEUR)) == 2

    def test_chaque_bloc_est_du_json_valide(self, tmp_path):
        """Le parseur MQL5 découpe puis lit : chaque morceau doit tenir seul."""
        import json

        from titanium.bridge.mt5_zones import (
            NOM_FICHIER, SEPARATEUR, ecrire_multi,
        )
        ecrire_multi([self._charge(s) for s in ("EURUSD", "XAUUSD")], tmp_path)
        txt = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        for bloc in txt.split(SEPARATEUR):
            assert json.loads(bloc)["symbol"] in ("EURUSD", "XAUUSD")

    def test_separateur_absent_du_json(self, tmp_path):
        """Si le séparateur pouvait apparaître dans un bloc, la découpe
        couperait au milieu d'un objet."""
        from titanium.bridge.mt5_zones import (
            NOM_FICHIER, SEPARATEUR, ecrire_multi,
        )
        c = self._charge("EURUSD")
        c.note = "===TITANIUM=== injecté"
        ecrire_multi([c], tmp_path)
        txt = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        # Le séparateur complet porte des sauts de ligne ; `json.dumps` les
        # échappe en \n, donc il ne peut pas apparaître par accident.
        assert SEPARATEUR not in txt

    def test_meme_horodatage_pour_tous(self, tmp_path):
        """Une écriture atomique doit montrer le même instant partout."""
        import json

        from titanium.bridge.mt5_zones import (
            NOM_FICHIER, SEPARATEUR, ecrire_multi,
        )
        ecrire_multi([self._charge(s) for s in ("A", "B", "C")], tmp_path)
        txt = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        dates = {json.loads(b)["written_at"] for b in txt.split(SEPARATEUR)}
        assert len(dates) == 1

    def test_liste_vide_n_ecrase_rien(self, tmp_path):
        from titanium.bridge.mt5_zones import NOM_FICHIER, ecrire_multi
        ecrire_multi([self._charge("EURUSD")], tmp_path)
        avant = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        ecrire_multi([], tmp_path)
        assert (tmp_path / NOM_FICHIER).read_text(encoding="utf-8") == avant \
            or (tmp_path / NOM_FICHIER).read_text(encoding="utf-8") == ""

    def test_separateur_identique_des_deux_cotes(self):
        """Python et MQL5 doivent découper au même endroit.

        Deux définitions qui divergent donneraient un fichier écrit que
        personne ne sait découper — et des graphiques vides sans le moindre
        message d'erreur.
        """
        from pathlib import Path

        import titanium.bridge as b
        from titanium.bridge.mt5_zones import MARQUEUR

        src = (Path(b.__file__).parent / "titanium_zones.mq5").read_text(
            encoding="utf-8", errors="replace")
        import re
        m = re.search(r'#define\s+MARQUEUR\s+"([^"]*)"', src)
        assert m, "MARQUEUR non défini dans l'indicateur MQL5"
        assert m.group(1) == MARQUEUR, (
            f"découpe divergente : MQL5 {m.group(1)!r} ≠ Python {MARQUEUR!r}")

    def test_marqueur_sans_saut_de_ligne(self):
        """`LireFichier` lit en FILE_TXT, qui RETIRE les fins de ligne.

        Un marqueur contenant un saut de ligne ne serait jamais retrouvé
        après lecture : `ExtraireBloc` rendrait le fichier entier et chaque
        graphique dessinerait les zones du PREMIER bloc. C'est exactement le
        défaut du 07/08/2026.
        """
        from titanium.bridge.mt5_zones import MARQUEUR
        assert not any(c.isspace() for c in MARQUEUR)

    def test_le_marqueur_survit_a_la_lecture_ligne_a_ligne(self, tmp_path):
        """Simule la lecture MQL5 : lignes concaténées sans fin de ligne."""
        from titanium.bridge.mt5_zones import (
            MARQUEUR, NOM_FICHIER, ecrire_multi,
        )
        ecrire_multi([self._charge(s) for s in ("EURUSD", "XAUUSD")], tmp_path)
        brut = (tmp_path / NOM_FICHIER).read_text(encoding="utf-8")
        comme_mql5 = "".join(brut.splitlines())
        assert comme_mql5.count(MARQUEUR) == 1, (
            "le marqueur ne survit pas au dépouillement des sauts de ligne")
