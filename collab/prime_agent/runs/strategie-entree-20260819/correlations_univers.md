# Qui bouge avec qui, et qui bouge en premier ?

**Date** : 20/08/2026 · **Auteur** : Prime Agent · **Nature** : mesure en lecture seule
sur l'archive de barres v2, pendant le rejeu de l'univers. Aucun parametre de
strategie touche, aucun ordre.

## Demande

« Pendant ce rejeu, identifie les actifs qui sont correles et qui peuvent
identifier les mouvements des marches. »

## Deux questions qu'il ne faut pas confondre

1. **Qui bouge ensemble ?** — correlation contemporaine. Elle sert au **risque**
   (ne pas empiler trois fois le meme pari) et a la **lisibilite** (un
   representant par bloc suffit a lire le marche).
2. **Qui bouge en premier ?** — avance-retard. C'est la seule qui serait un
   signal, et c'est la plus fragile : sur un flux de courtier, une correlation
   decalee d'une barre est le plus souvent une cotation qui trainait.

La reponse mesuree est nette : **la premiere est massive et exploitable pour le
risque ; la seconde est vide chez les actifs liquides.**

## 1. Les blocs — 148 symboles, H1, depuis 2023 (31 830 barres)

79 blocs au seuil 0,7 de correlation absolue. Les blocs qui comptent :

| Taille | Representant | Membres |
|---|---|---|
| 10 | ETH-JPY | crypto : ADAUSD, AVAX-USD, BTC-JPY, BTCUSD, DOGUSD, DOTUSD, ETHUSD... |
| 9 | EUSTX50.fs | indices EU : CAC40.fs, DAX40.fs, EU50, FRA40, GER40, NETH25, SPA35... |
| 7 | US500 | indices US : DJ30.fs, NAS100.fs, S&P.fs, US2000, US30, USTECH |
| 4 | USOIL | BRENT.fs, UKOIL, WTI.fs |
| 4 | USDJPY | CADJPY, SGDJPY, USDJPC |
| 4 | EURUSD | USDCZK, USDINDEX.fs, USDPLN |
| 4 | XAUGBP | XAUAUD, XAUEUR, XAUUSD |
| 3 | EURJPY | CHFJPY, GBPJPY |
| 3 | AUDUSD | AUDCAD, AUDSGD |

**37 paires depassent 0,90** — ce sont deux noms pour un seul actif :
HK50/HSI.fs 0,997 · NAS100.fs/USTECH 0,996 · USDJPC/USDJPY 0,995 ·
S&P.fs/US500 0,994 · DJ30.fs/US30 0,993 · CHINA50.fs/CN50 0,992 ·
AUS200/SPI200.fs 0,991 · USOIL/WTI.fs 0,990 · CAC40.fs/FRA40 0,986 ·
BRENT.fs/UKOIL 0,986.

**Consequence directe pour V14** : la boucle traite ces symboles comme des paris
independants. Prendre US500 et S&P.fs, ou USOIL et WTI.fs, c'est **une position
de taille double** qu'aucun compteur ne voit. C'est un defaut de risque, pas une
curiosite statistique.

## 2. Le mouvement commun — analyse en composantes principales

Sur les 123 symboles bien couverts et les 17 148 barres ou tout le monde cote :

| Facteur | Part de variance | Ce qu'il est | Meilleurs thermometres |
|---|---|---|---|
| CP1 | **24,6 %** | appetit pour le risque | S&P.fs, US500, NAS100.fs, USTECH, US2000, ETHUSD, DJ30.fs |
| CP2 | 11,9 % | le dollar | USDINDEX.fs (−), EURUSD (+), USDSGD (−), NZDUSD (+) |
| CP3 | 9,5 % | le yen et le franc | USDJPY, CADJPY, CADCHF, USDCHF |

Trois facteurs expliquent **46 %** de tout ce qui bouge sur 123 actifs. Pour
lire le marche, **US500, USDINDEX.fs et USDJPY suffisent** : le reste en est
majoritairement une combinaison.

Piege evite en cours de route, et il vaut d'etre note : juger la couverture d'un
symbole sur toutes les barres eliminait tout le FX, qui ne cote que 24 h sur 5.
Le premier facteur ne gardait alors que les 29 actifs cotes en continu et
« expliquait 64 % du marche » — en decrivant le bitcoin. Le filtre correct est
de restreindre d'abord aux barres de seance. Sans ce garde-fou, la conclusion
etait spectaculaire et fausse.

## 3. Qui bouge en premier ? Personne, chez les liquides

M15, 30 actifs liquides, depuis 2024, 92 247 barres, decalages testes de −4 a +4 :

| Couple | Meilleur decalage | r | r contemporain |
|---|---|---|---|
| US500 -> NAS100.fs | **0** | +0,945 | +0,945 |
| US500 -> US30 | **0** | +0,888 | +0,888 |
| EU50 -> GER40 | **0** | +0,915 | +0,915 |
| USDINDEX.fs -> EURUSD | **0** | −0,904 | −0,904 |
| GBPJPY -> EURJPY | **0** | +0,869 | +0,869 |

**Le meilleur decalage est systematiquement zero.** Le meilleur gain d'un
decalage non nul sur toute la table est de **+0,009**, au niveau du seuil de
bruit (0,008). Autrement dit : sur ce flux, a M15, aucun actif liquide n'annonce
un autre. C'est le resultat attendu d'un marche qui fonctionne, et c'est une
bonne nouvelle : cela ferme une piste qui aurait coute des semaines.

Sur l'univers complet en H1, les seules « avances » apparentes sortent toutes
sur des exotiques a cotation rare — USDCHC, EUCUSD, USDCOP, USDHKD — avec une
correlation contemporaine nulle et une correlation a +1 barre de −0,27. Ce
n'est pas de l'information : c'est **une cotation qui traine d'une barre**, sur
des symboles qui figurent deja parmi les sept que l'archive v2 signale comme
inexploitables. Les traiter comme un signal reviendrait a parier sur le retard
du courtier, avec un spread qui mange tout.

## Ce que j'en fais

1. **Risque** : dedupliquer l'univers par bloc avant d'ouvrir plusieurs
   positions ; une exposition par bloc, pas par symbole. Les 37 paires a plus de
   0,90 sont a traiter comme un seul actif.
2. **Lisibilite** : suivre CP1 (US500), CP2 (USDINDEX.fs) et CP3 (USDJPY) comme
   trois thermometres du marche.
3. **Signal** : ne pas construire de strategie d'avance-retard sur ce flux. La
   mesure dit non, et elle le dit sur 92 247 barres.
4. Le rejeu des 149 actifs tourne en parallele ; le croisement bloc x esperance
   dira si l'edge mesure est un edge par actif ou **un seul edge par bloc**
   compte plusieurs fois — c'est la question que ce travail rend possible.

## Preuves

- `tools/correlations_univers.py`, 7 tests dans `tests/test_correlations_univers.py`.
- `results/correlations/structure_H1.json`, `structure_M15.json`,
  `correlation_H1.csv`, `correlation_M15.csv`.
