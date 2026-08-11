# Snapshot edge net V14 — Codex

Date : 2026-08-11  
Source : `results/trades.ndjson` (lecture seule)

## Verdict

**Échantillon insuffisant et performance provisoire négative : aucune preuve de
rentabilité.** Le journal contient 14 clôtures réconciliées. Aucun contexte
n'atteint 20 clôtures (maximum observé : 2) et aucune ligne ne possède encore
un coût déclaré exact.

## Mesure globale provisoire

- clôtures : **14** ;
- espérance nette observée : **-0,1195 R/trade** ;
- profit factor observé : **0,733** ;
- Sharpe par trade non annualisé : **-0,123** ;
- coût moyen journalisé : **0,0797 R** ;
- couverture des coûts exacts : **0/14 (0 %)**.

`pnl_r` provient du net comptable. La série est passée de positive à négative
avec trois observations supplémentaires, ce qui confirme son instabilité.
`exact_cost=false` sur toutes les lignes signifie aussi que la décomposition
spread + commission + swap + frais n'est pas encore exacte. La promotion reste
fermée.

## Couverture et répétition

- régimes : S2 = 7, S3 = 6, S4 = 1 ;
- classes : FX = 8, indices = 5, crypto = 1 ;
- mode : exploration = 14 ;
- contextes les plus répétés : `NAS100.fs|long|continuation|4p`,
  `EURCAD|short|continuation|4p` et `EURNZD|short|continuation|3p`, chacun avec
  seulement 2 clôtures.

Aucun classement ou filtrage de stratégie ne doit être décidé sur ces tailles.

## Critère de fin de la mission P0

Recalculer automatiquement à chaque nouvelle clôture. Conserver le statut
`review` jusqu'à au moins 20 clôtures par contexte pour un premier diagnostic,
puis exiger 60 clôtures propres pour une décision de promotion, les coûts
exacts, un intervalle de confiance/bootstrap et un verdict réellement hors
échantillon. Un seuil ponctuel de Sharpe ne suffit pas.
