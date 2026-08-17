# Recalibrage du 17/08/2026 — « trop de trades perdants »

Source : `results/trades.ndjson` + `results/excursions.ndjson`, 128 trades clos
entre le 10/08 et le 17/08/2026 (compte démo 10055401, mode `explore`,
quorum 2, R:R 2.0). Chiffres reproductibles : `results/recalibrage_20260817.json`.

## 1. Ce que dit le journal

| Mesure | Valeur |
|---|---|
| Trades clos | 128 (≈ 21/jour) |
| Perdants | 73 (57 %) |
| Espérance | **−0.229 R** par trade, IC95 [−0.385 ; −0.069] |
| Total | **−29.3 R** |
| Sorties `init` (stop plein) | 73 → −60.7 R |
| Sorties `trailing` | 31 → +30.8 R |
| Sorties `breakeven` | 24 → +0.7 R |

## 2. La perte n'est PAS un problème de gestion

61.6 % des perdants n'ont jamais dépassé **+0.3 R** de MFE : ils étaient faux
dès l'entrée, aucune sortie ne les sauve. Contre-épreuve : rejeu de l'échantillon
avec un TP fixe à 0.75 / 1.0 / 1.25 / 1.5 / 2.0 R — le meilleur cas reste
**−22.7 R**. Le trailing et le breakeven actuels capturent déjà l'essentiel de
ce que les trades offrent.

## 3. D'où vient la perte

* **Croisements et exotiques FX** (AUDCAD, USDZAR, SGDJPY, EURCAD, EURHUF,
  CHFSEK, GBPNOK, USDSEK, AUDNZD, EURNZD…) : 42 trades, **−21.3 R = 73 % de la
  perte**, réussite 26 %, coût moyen 0.111 R contre 0.081 R sur les majeures.
  Ils sont entrés dans l'univers avec le passage au catalogue MT5 complet
  (`UNIVERS = []`, `LOT_PAR_TOUR = 60`) sans que `results/selection_actifs.json`
  n'existe : la rotation est uniforme, donc un exotique à spread large obtient
  exactement le même crédit qu'EURUSD.
* **Les shorts FX** : 51 des 53 shorts du journal, **−23.5 R**, réussite 29 %,
  IC95 [−0.67 ; −0.23] R — l'intervalle exclut zéro. Les longs sont à −0.08 R.
* **Le coût** : au-delà de 0.08 R de frais par trade, l'espérance s'effondre
  (−0.40 R sur la tranche 0.08–0.12 R contre +0.06 R sous 0.05 R).
* **Contre-intuitif à surveiller** : la strate S≥3 (« 4p ») fait **pire** que
  S=2 (−0.39 R contre −0.15 R par trade). La réserve `RESERVE_S3` donne donc
  ses deux derniers créneaux à la strate la moins rentable de l'échantillon.
  Non touché ici — c'est le canal d'accumulation qui ouvre le mode PROD — mais
  à re-mesurer au prochain palier.

## 4. Ce qui a été changé

1. `titanium/edge.py` — `FX_NEGOCIABLES`, `est_paire_fx()`, `fx_illiquide()` :
   l'univers FX est ramené aux dix paires de `ASSET_CLASSES["fx"]`.
   `est_paire_fx` reconnaît une paire par ses codes ISO, donc le filtre tient
   aussi hors session MT5 (où `asset_class_of` rend `""`).
2. `tools/live_demo.py` — le catalogue est purgé des paires FX illiquides
   avant la rotation ; les écartés sont comptés dans le tunnel
   (`flow/fx_illiquides_ecartes`).
3. `tools/live_demo.py` — `FX_SHORTS_SUSPENDUS = True` : un ENTER short sur une
   paire FX est refusé et compté (`post_enter_refusal/FX_SHORT_SUSPENDU`).
   **Suspension réversible**, pas une loi : une semaine de mesure ne prouve pas
   qu'un short FX ne vaut rien. À rouvrir sur mesure contraire.
4. `tests/test_univers_liquide.py` — verrouille le filtre et vérifie qu'il ne
   touche ni aux indices, ni aux métaux, ni à l'énergie, ni à la crypto.

## 5. Effet rejoué sur l'échantillon

| Univers | Trades | Perdants | Total | Espérance | Réussite |
|---|---|---|---|---|---|
| Tel quel | 128 | 73 | −29.30 R | −0.229 R | 43 % |
| Sans FX exotiques | 86 | 41 | −8.01 R | −0.093 R | 52 % |
| Sans FX exotiques ni shorts FX | 72 | 33 | −3.00 R | −0.042 R | 54 % |

Un tiers de trades en moins, 90 % de la perte en moins. La seconde moitié de
la période passe à **+0.086 R** par trade sous le nouveau filtre (contre
−0.068 R sans lui).

## 6. Ce que ce recalibrage ne prétend PAS

Il ne rend pas la stratégie rentable : l'espérance filtrée reste légèrement
négative (−0.04 R) et son IC95 contient zéro. Il supprime une perte
**identifiée et structurelle** (spread), il ne crée pas d'edge. La prochaine
mesure doit porter sur la strate S≥3 et sur le déficit des sorties `init`
sur indices, non sur un énième réglage de R:R.
