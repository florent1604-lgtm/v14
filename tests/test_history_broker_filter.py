"""Le courtier IGNORE le filtre ``position=`` — vérrouillé par ces tests.

Mesuré le 10/08/2026 sur le compte démo Axi 10055401 (`Axi-US50-Demo`,
`trade_mode=0`), terminal MetaTrader5 en service :

    history_deals_get(debut, fin, position=87498251) -> 115 deals, 32 symboles
    history_deals_get(debut, fin, position=87498987) -> 115 deals, ensemble IDENTIQUE
    history_deals_get(debut, fin, position=87498991) -> idem

Trois tickets distincts, le même lot complet : le terminal rend l'intégralité de
l'historique 30 jours quel que soit le paramètre, **sans lever d'erreur**. Les
61 `position_id` et les 32 symboles retournés incluent des opérations de balance
(`position_id=0`, symbole vide) et des positions d'autres journées.

C'est la cause racine de l'incident du 07/08/2026 : avant le filtre applicatif,
les frais et le brut étaient sommés sur *tous* ces deals et le prix de sortie
pris n'importe où dans le lot — d'où un prix GER40 (26 333,85) recopié sur
EURGBP, EURUSD et AUDUSD, et des `pnl_r` à 10⁸ R.

Ces tests ne vérifient donc pas un cas d'école : ils reproduisent le
comportement réel du courtier. Le filtre applicatif `position_id` + `symbole`
est **porteur**, pas défensif — toute la mesure d'edge (frais, net, donc
`cost_r` et `pnl_r`) est agrégée sur ce qu'il laisse passer. Si l'un de ces
tests tombe parce qu'on a « simplifié » en refaisant confiance au terminal, ce
n'est pas une ligne de journal qui devient fausse : c'est l'espérance entière.
"""

from __future__ import annotations

import pytest

from titanium.execution.position_manager import _cloture_depuis_historique

TICKET = 87498251
SYMBOLE = "EURGBP"


def _deal(**kw):
    """Un deal MT5 minimal ; les champs absents sont tolérés par le lecteur."""
    d = dict(time=1_800_000_000, price=0.857, commission=0.0, swap=0.0,
             fee=0.0, profit=0.0, entry=0, position_id=TICKET, symbol=SYMBOLE)
    d.update(kw)
    return type("Deal", (), d)()


def _mt5_qui_ignore_le_filtre(deals):
    """Reproduit le terminal réel : le paramètre `position` est ignoré."""
    return type("MT5", (), {
        "history_deals_get": staticmethod(lambda *a, **k: list(deals)),
    })()


def _historique_pollue():
    """L'historique tel que le terminal le rend : tout le compte, mélangé.

    Contient les trois pièges observés en production :
      * un autre symbole avec un prix d'un ordre de grandeur différent
        (GER40 à 26 333,85 — le prix exact qui a corrompu le journal) ;
      * une autre position **sur le même symbole**, que le seul recoupement
        de symbole ne suffirait pas à écarter ;
      * une opération de balance (`position_id=0`, symbole vide).
    """
    return [
        # --- La position cible : entrée puis sortie, plus ses frais.
        _deal(time=10, entry=0, price=0.85700, commission=-1.5, profit=0.0),
        _deal(time=40, entry=1, price=0.86100, commission=-1.5, swap=-0.25,
              profit=12.0),
        # --- Un autre symbole, prix sans commune mesure : le piège du 07/08.
        _deal(time=20, entry=1, price=26_333.85, symbol="GER40",
              position_id=86755945, commission=-9.0, profit=814.0),
        # --- Une autre position sur le MÊME symbole : le symbole ne suffit pas.
        _deal(time=30, entry=1, price=0.84200, symbol=SYMBOLE,
              position_id=86776744, commission=-4.0, profit=-77.0),
        # --- Une opération de balance : ni position, ni symbole.
        _deal(time=5, entry=0, price=0.0, symbol="", position_id=0,
              commission=0.0, profit=500.0),
    ]


class TestFiltreCourtierIgnore:
    """Le lecteur doit se défendre seul : le terminal ne filtre rien."""

    def test_prix_de_sortie_vient_de_la_bonne_position(self):
        """Le prix d'un autre symbole ne doit jamais être retenu.

        C'est l'assertion qui aurait empêché les `pnl_r` à 10⁸ R.
        """
        prix, _, _, _ = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(_historique_pollue()),
            str(TICKET), expected_symbol=SYMBOLE)
        assert prix == pytest.approx(0.86100)

    def test_le_prix_ger40_n_est_jamais_retourne(self):
        """Formulation directe de l'incident, pour qu'il reste cherchable."""
        prix, _, _, _ = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(_historique_pollue()),
            str(TICKET), expected_symbol=SYMBOLE)
        assert prix != pytest.approx(26_333.85)

    def test_frais_n_agregent_que_la_position_cible(self):
        """-1.5 -1.5 -0.25 = -3.25 ; les frais des autres sont exclus.

        Sans ce filtre, les -9.0 du GER40 et les -4.0 de l'autre EURGBP
        entreraient dans `cost_r` et fausseraient l'espérance mesurée.
        """
        _, _, frais, _ = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(_historique_pollue()),
            str(TICKET), expected_symbol=SYMBOLE)
        assert frais == pytest.approx(-3.25)

    def test_net_n_agrege_que_la_position_cible(self):
        """net = brut + frais = 12.0 - 3.25.

        Le +814 du GER40 et le +500 de l'opération de balance sont exclus :
        c'est `net_devise` qui alimente la voie exacte de `pnl_r`.
        """
        _, _, _, net = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(_historique_pollue()),
            str(TICKET), expected_symbol=SYMBOLE)
        assert net == pytest.approx(8.75)

    def test_autre_position_du_meme_symbole_est_exclue(self):
        """Le recoupement du symbole seul ne suffit pas — il faut le ticket.

        Deux positions simultanées sur la même paire sont banales ; sans le
        `position_id`, la sortie de l'une serait attribuée à l'autre.
        """
        deals = [
            _deal(time=10, entry=0, price=0.85700),
            _deal(time=99, entry=1, price=0.84200, position_id=86776744,
                  profit=-77.0),
        ]
        prix, _, _, net = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(deals), str(TICKET),
            expected_symbol=SYMBOLE)
        assert prix is None
        assert net == pytest.approx(0.0)

    def test_aucun_deal_coherent_rend_fail_closed(self):
        """Pas de ligne vaut mieux qu'une ligne fausse.

        Quand l'historique ne contient rien qui appartienne à la position, le
        lecteur ne doit pas se rabattre sur le premier deal venu.
        """
        deals = [
            _deal(time=20, entry=1, price=26_333.85, symbol="GER40",
                  position_id=86755945, profit=814.0),
            _deal(time=5, entry=0, price=0.0, symbol="", position_id=0,
                  profit=500.0),
        ]
        prix, quand, frais, net = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(deals), str(TICKET),
            expected_symbol=SYMBOLE)
        assert prix is None
        assert quand == ""
        assert frais == pytest.approx(0.0)
        assert net == pytest.approx(0.0)

    def test_deal_entree_seul_ne_devient_jamais_fausse_cloture(self):
        """Une disparition transitoire de positions_get ne prouve pas la sortie."""
        deals = [_deal(
            time=10,
            entry=0,
            price=0.85700,
            commission=-1.5,
            profit=0.0,
        )]

        result = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(deals),
            str(TICKET),
            expected_symbol=SYMBOLE,
        )

        assert result == (None, "", 0.0, 0.0)

    def test_historique_complet_du_compte_ne_deborde_pas(self):
        """Volume réel : 115 deals, 32 symboles, 61 position_id.

        Le lot observé le 10/08/2026 contenait une seule position cible noyée
        dans l'historique de trente jours. On vérifie que la sélection reste
        exacte à cette échelle, pas seulement sur trois lignes.
        """
        bruit = [
            _deal(time=t, entry=t % 2, price=100.0 + t,
                  symbol=f"SYM{t % 32}", position_id=86_000_000 + t,
                  commission=-1.0, profit=7.0)
            for t in range(115)
        ]
        cible = [
            _deal(time=1_000, entry=0, price=0.85700, commission=-1.5),
            _deal(time=1_001, entry=1, price=0.86100, commission=-1.5,
                  profit=12.0),
        ]
        prix, _, frais, net = _cloture_depuis_historique(
            _mt5_qui_ignore_le_filtre(bruit + cible), str(TICKET),
            expected_symbol=SYMBOLE)
        assert prix == pytest.approx(0.86100)
        assert frais == pytest.approx(-3.0)
        assert net == pytest.approx(9.0)
