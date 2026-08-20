# Ai-je assez de donnees pour executer les meilleurs points d'entree ?

**Date** : 20/08/2026 · **Auteur** : Prime Agent · **Nature** : mesure en lecture seule.
Aucun parametre de strategie modifie, aucun ordre, rien d'arme.

## Question de Florent

« As-tu assez de donnees maintenant pour executer les meilleurs points d'entree ? »

## Reponse en une ligne

**Assez de donnees : oui, et de tres loin.** Assez de preuve pour dire quel point
d'entree est le meilleur : **pas encore**, et la premiere mesure montre deja que
la question ne se pose pas de la meme facon selon l'actif.

## Ce que la donnee permet maintenant, et ne permettait pas hier

`titanium/data/archive_barres.py` rend les barres archivees au **contrat exact**
de `mt5_vendor.get_rates`. `titanium.backtest.rejouer` rejoue donc la **meme**
`confluence_gate.evaluate()` qu'en live, sans terminal, de facon reproductible.

Pilote mesure sur 9 actifs, 20 000 barres M15 chacun (environ 10 mois), spread
du courtier applique, sortie simulee barre par barre :

| Actif | Trades | /an | Esperance R | Winrate | PF | 3 segments |
|---|---|---|---|---|---|---|
| XAUUSD | 1 186 | 1 401 | **+0,2053** | 64,2 % | 1,57 | **stable** +0,170 / +0,253 / +0,193 |
| BTCUSD | 1 180 | 2 071 | **+0,1972** | 61,5 % | 1,51 | **stable** +0,200 / +0,226 / +0,166 |
| US500 | 1 017 | 1 186 | **+0,1075** | 59,3 % | 1,26 | **stable** +0,184 / +0,097 / +0,041 |
| GBPUSD | 1 042 | 1 294 | +0,0286 | 55,2 % | 1,07 | instable |
| USDJPY | 1 057 | 1 312 | +0,0071 | 54,9 % | 1,02 | instable |
| AUDUSD | 1 078 | 1 338 | −0,0420 | 46,9 % | 0,91 | negatif |
| USDCAD | 1 066 | 1 323 | −0,0516 | 46,1 % | 0,89 | negatif |
| EURJPY | 1 038 | 1 289 | −0,0553 | 51,2 % | 0,88 | negatif |
| EURUSD | 1 031 | 1 280 | −0,0646 | 50,5 % | 0,87 | negatif |

**Le plancher statistique est franchi d'un ordre de grandeur.** Le live avait
179 clotures sur 57 actifs en 9 jours, mediane 3 par actif ; l'archive rend
**environ 1 000 clotures par actif en 10 mois**, et la profondeur M15 disponible
(2,8 ans) en promet environ 3 500. Le seuil de 60 clotures par cellule exige par
le protocole preenregistre devient atteignable **par actif et par regime**, pas
seulement globalement.

## Ce que la mesure dit deja, et qu'il faut prendre au serieux

La meme porte d'entree, sans aucune modification, est **positive et stable** sur
metal, crypto et indice, **negative** sur quatre paires FX majeures. C'est la
meme forme que le live (XAGUSD, JPN225, WTI gagnants ; EURJPY, AUDCAD perdants)
et que le recalibrage du 17/08. Deux sources independantes qui disent la meme
chose, ce n'est plus du bruit.

XAUUSD affiche **64,2 % de winrate sur 1 186 trades** — au-dessus du seuil de
60 % demande. Ce chiffre n'est pas un edge demontre pour autant : voir ci-dessous.

## Pourquoi je ne dis pas « oui, on peut executer »

1. **Aucun hors-echantillon.** Les trois segments testent la stabilite, pas la
   generalisation. Rien n'a encore ete decide sur une periode puis verifie sur
   une autre.
2. **Neuf actifs sur 149, sans correction de multiplicite.** Balayer les 149
   sans Benjamini-Hochberg fabriquerait des gagnants par hasard.
3. **L'execution est idealisee.** Entree a la cloture de barre, remplissage
   parfait, aucune latence, aucun slippage, spread du jour applique a tout
   l'historique alors que l'archive contient le spread **par barre**. Les 15
   politiques d'execution restent synthetiques.
4. **La sortie est simulee sur barres M15**, sans chemin intrabar : un stop et
   une cible touches dans la meme barre ne sont pas departages par la donnee.
5. **M15 est plafonne a 2,8 ans** par `MaxBars=100000` du terminal.

## Ce que je propose, dans l'ordre

1. Rejouer les **149 actifs** sur toute la profondeur M15, spread historique par
   barre, protocole preenregistre : esperance en R, plancher 60 clotures, FDR.
   Cout mesure : 13 min par actif pour 20 000 barres avec 8 processus en
   parallele ; environ 20 h pour l'univers complet a pleine profondeur.
2. Decoupe hors-echantillon stricte : calibrage sur les deux premiers tiers,
   verdict sur le dernier, jamais relu.
3. Coupler l'execution seulement ensuite, avec le carnet Binance pour les actifs
   ou il a un sens.

## Preuves

- Pilote : `pilote_archive.py`, `pilote_*.json`, `pilote_*.log` (ce dossier).
- Lecteur : `titanium/data/archive_barres.py`, 8 tests.
- Archive v2 : `collab/prime_agent/runs/mt5-collecte-elargie-20260819/report.md`.
