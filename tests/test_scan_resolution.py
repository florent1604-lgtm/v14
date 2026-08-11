"""Le scan doit résoudre les suffixes courtier — la boucle, elle, n'en a pas besoin.

Asymétrie mesurée le 10/08/2026, marchés ouverts :

    scan_v14.py NAS100      -> « données indisponibles (NoMarketDataError) »
    scan_v14.py NAS100.fs   -> [✓ ✓ ✓ · · ·] BLOCK BLOCK_PILLAR_MISSING

Le même instrument, que la boucle live tradait au même moment. La cause n'est
pas le résolveur — il est correct — mais le fait que **personne ne l'appelait
sur ce chemin** : `mt5_vendor.get_rates` ne résout rien, la table des suffixes
vit dans `mt5_dataflows`, et `live_demo` n'en a jamais eu besoin puisqu'il lit
le catalogue MT5 déjà suffixé. Seul le scan reçoit des noms tapés à la main.

Pourquoi ça méritait un correctif et un test : le scan est l'**instrument de
diagnostic** de ce projet. Un instrument qui déclare absent ce que le moteur
exploite ne se contente pas d'être inutile, il fait conclure faux — il a
faussé une distribution de motifs de blocage publiée le 09/08.
"""

from __future__ import annotations

import scan_v14


class TestResolutionSuffixesCourtier:
    def test_delegue_au_resolveur_du_projet(self, monkeypatch):
        """Un seul résolveur fait foi : celui de `mt5_dataflows`."""
        appels = []

        def faux_resolveur(symbole):
            appels.append(symbole)
            return f"{symbole}.fs"

        monkeypatch.setattr("titanium.data.mt5_dataflows.resolve_mt5_symbol",
                            faux_resolveur)
        assert scan_v14._resoudre("NAS100") == "NAS100.fs"
        assert appels == ["NAS100"]

    def test_repli_sur_le_nom_tape_si_le_resolveur_echoue(self, monkeypatch):
        """Terminal fermé ou catalogue illisible : le scan reste aussi capable
        qu'avant le correctif, jamais moins. Il tente le nom tel quel."""
        def resolveur_casse(symbole):
            raise RuntimeError("catalogue courtier illisible")

        monkeypatch.setattr("titanium.data.mt5_dataflows.resolve_mt5_symbol",
                            resolveur_casse)
        assert scan_v14._resoudre("NAS100") == "NAS100"

    def test_nom_deja_suffixe_reste_intact(self, monkeypatch):
        """Taper le nom courtier directement doit continuer de marcher."""
        monkeypatch.setattr("titanium.data.mt5_dataflows.resolve_mt5_symbol",
                            lambda s: s)
        assert scan_v14._resoudre("NAS100.fs") == "NAS100.fs"
