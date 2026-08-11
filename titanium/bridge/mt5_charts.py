"""Pilotage de l'observateur de marché et des fenêtres de graphique.

Deux besoins, deux mécanismes très différents
----------------------------------------------
**L'observateur de marché** se pilote depuis Python : `symbol_select()`
suffit, et c'est un prérequis à toute lecture — un symbole non sélectionné
ne synchronise pas son historique, et `copy_rates_from_pos` rend vide sans
dire pourquoi.

**Les fenêtres de graphique**, non. `ChartOpen`, `ChartClose` et
`ChartIndicatorAdd` n'existent que côté MQL5 ; l'API Python ne les expose
pas. V14 ne peut donc pas ouvrir ses propres graphiques : il écrit la liste
de ceux qu'il veut voir, et l'EA `titanium_charts.mq5` fait converger le
terminal vers cette liste.

Sur le plafond de fenêtres
--------------------------
Chaque graphique ouvert consomme de la mémoire et un flux de ticks. Ouvrir
149 fenêtres rendrait le terminal inutilisable et, pire, ralentirait le flux
dont la boucle dépend. On montre donc les symboles **actifs** — ceux qui ont
une position ou un setup en cours — et pas tout le catalogue.
"""

from __future__ import annotations

from pathlib import Path

#: L'EA lit ce nom dans `MQL5/Files`. Fixé des deux côtés.
NOM_FICHIER_CHARTS = "titanium_charts.txt"

#: Au-delà, le terminal souffre et le flux de ticks se dégrade — or c'est
#: ce même flux qui alimente la boucle. L'EA applique le même plafond de
#: son côté : deux filets, parce qu'un fichier peut être écrit à la main.
MAX_FENETRES = 8


def selectionner_univers(mt5, symboles=None, *, tous: bool = False) -> tuple[int, int]:
    """Rend des symboles visibles dans l'observateur de marché.

    C'est un prérequis technique, pas un confort d'affichage : sans
    sélection, MT5 ne synchronise pas l'historique du symbole et toute
    lecture rend un tableau vide, sans erreur.

    Returns:
        ``(sélectionnés, déjà visibles)``.
    """
    catalogue = mt5.symbols_get() or []
    if tous:
        cibles = [s.name for s in catalogue]
    else:
        voulus = {str(x).upper() for x in (symboles or [])}
        cibles = [s.name for s in catalogue if s.name.upper() in voulus]

    connus = {s.name: s for s in catalogue}
    ajoutes = deja = 0
    for nom in cibles:
        info = connus.get(nom)
        if info is not None and info.visible:
            deja += 1
            continue
        if mt5.symbol_select(nom, True):
            ajoutes += 1
    return ajoutes, deja


def demander_fenetres(symboles, dossier: Path,
                      *, maxi: int = MAX_FENETRES) -> Path | None:
    """Écrit la liste des graphiques que V14 veut voir ouverts.

    L'écriture est atomique : l'EA relit ce fichier toutes les trois
    secondes, et le surprendre à moitié écrit lui ferait fermer des
    fenêtres au hasard.

    Une liste **vide est significative** — elle demande la fermeture de
    tout. C'est voulu : quand plus rien n'est actif, l'écran se vide.
    """
    dossier = Path(dossier)
    if not dossier.is_dir():
        return None

    # `dict.fromkeys` déduplique en gardant l'ordre : les premiers de la
    # liste sont les plus importants, et c'est eux qui survivent au plafond.
    uniques = list(dict.fromkeys(str(s) for s in symboles if s))[:maxi]

    cible = dossier / NOM_FICHIER_CHARTS
    tmp = cible.with_suffix(".tmp")
    entete = ("# écrit par Titanium V14 — un symbole par ligne\n"
              "# l'EA titanium_charts.mq5 fait converger les fenêtres\n")
    tmp.write_text(entete + "".join(f"{s}\n" for s in uniques),
                   encoding="ascii", errors="ignore")
    tmp.replace(cible)
    return cible


def symboles_actifs(mt5, candidats=None, *, maxi: int = MAX_FENETRES) -> list[str]:
    """Les symboles qui méritent une fenêtre, par ordre de priorité.

    Les positions ouvertes d'abord — ce sont celles qu'on veut surveiller —
    puis les candidats fournis par la boucle.
    """
    ordre: list[str] = []
    try:
        for p in (mt5.positions_get() or []):
            if p.symbol not in ordre:
                ordre.append(p.symbol)
    except Exception:  # noqa: BLE001 — l'affichage ne casse jamais le trading
        pass
    for s in (candidats or []):
        if s and s not in ordre:
            ordre.append(s)
    return ordre[:maxi]
