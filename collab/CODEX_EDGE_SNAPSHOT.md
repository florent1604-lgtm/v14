# Snapshot edge net V14 — Codex

Date : 2026-08-11, 21:06 Paris
Sources : `results/trades.ndjson`, `results/reconciliation_mt5_recent.json`

## Verdict

**La collecte progresse, mais la performance observée se dégrade et aucune
rentabilité n'est démontrée.** Le journal contient 26 clôtures rapprochées
ticket par ticket avec MT5. Aucun contexte n'atteint 20 clôtures (maximum : 3)
et aucune ligne ne possède encore une décomposition de coût exacte.

## Mesure globale provisoire

- clôtures : **26** (10 positives, 16 négatives) ;
- PnL MT5 rapproché : **-226,48 EUR** ;
- somme : **-7,7669 R** ;
- espérance nette observée : **-0,2987 R/trade** ;
- profit factor : **0,488** ;
- Sharpe par trade non annualisé : **-0,308** ;
- drawdown maximal de la courbe en R : **13,6666 R** ;
- bootstrap 95 % de l'espérance : **[-0,6481 ; +0,0893] R** ;
- couverture des coûts exacts : **0/26 (0 %)**.

Le PnL comptable est net. `cost_r` reste une estimation de décomposition ;
`exact_cost=false` interdit de déclarer la couverture des coûts complète.

## Sensibilité à la veille du PC

La veille Windows du 10/08 21:02 au 11/08 06:11 Paris a perturbé deux clôtures
survenues pendant la coupure et une position EURHUF restée sans gestion
dynamique pendant cette fenêtre :

- `live:87940036` — USDCHF ;
- `live:88004818` — NAS100.fs ;
- `live:87650131` — EURHUF.

Hors ces trois observations : **23 clôtures**, espérance **-0,2175 R**, profit
factor **0,597**, Sharpe/trade **-0,217**, bootstrap 95 %
**[-0,5957 ; +0,1999] R**. La conclusion reste inchangée : résultat négatif,
incertain et non promouvable.

Trois positions ayant traversé la veille restent ouvertes au dernier contrôle :
`CHFSEK`, `AUDSGD` et `USDSGD`. Leur future clôture doit rester étiquetée comme
observation perturbée dans les analyses de sensibilité.

## Couverture actuelle

- journal total : S2 = 13, S3 = 12, S4 = 1 ;
- classes : FX = 15, indices = 7, crypto = 2, énergie = 2 ;
- cellules de promotion S>=3 : FX **7/60**, indices **5/60**,
  crypto **1/60**, énergie **0/60** ;
- contexte le plus répété : `NAS100.fs|long|continuation|4p`, **3 clôtures** ;
- `EURNZD|short|continuation|3p`, `EURCAD|short|continuation|4p` et
  `USDCAD|short|continuation|4p` : **2 clôtures** chacun.

## Critère de fin de la mission P0

La tâche ne peut pas être terminée artificiellement : elle dépend de nouvelles
clôtures de marché. Conserver les gardes, ne pas optimiser et recalculer après
chaque clôture. Le premier diagnostic exige 20 clôtures dans un même contexte ;
une décision de promotion exige au moins 60 observations propres par cellule,
90 % de coûts exacts, un intervalle de confiance/bootstrap et une validation
hors échantillon. La promotion reste fermée.
