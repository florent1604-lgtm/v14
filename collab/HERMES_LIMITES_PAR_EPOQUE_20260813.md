# Cycle des limites segmenté par époque runtime

Date de mesure : 13/08/2026 vers 12:54 UTC  
Sources : `results/shadow_prod.ndjson`, `results/limit_lifecycle.ndjson`  
Méthode : nouvelle époque après un trou strictement supérieur à 600 secondes

## Verdict

Le taux global de remplissage de 29,41 % mélange deux époques actives, mais la
segmentation ne révèle pas de rupture suffisante pour attribuer les expirations
à la durée de vie plutôt qu'à la distance de limite. Les durées de vie moyennes
sont pratiquement identiques. Modifier expiration ou distance maintenant serait
un réglage sans preuve.

## Funnel par époque

| Époque inférée | Fenêtre UTC | Placés | Remplis | Expirés | Ouverts | Fill rate résolu | TTL moyen | Distance cible moyenne | Clôturés | P&L net clos |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 7 | 12/08 13:38–22:15 | 22 | 6 | 16 | 0 | 27,27 % | 381,69 s | 0,12975 R | 5 | -2,4395 R |
| 8 | 13/08 05:17–12:54 | 30 | 9 | 20 | 1 | 31,03 % | 379,88 s | 0,12293 R | 6 | -2,8325 R |
| **Total** | deux époques | **52** | **15** | **36** | **1** | **29,41 %** | ~380,65 s | ~0,1258 R | **11** | **-5,2720 R** |

Le dénominateur du fill rate résolu exclut l'ordre encore ouvert :
`15 / (15 + 36) = 29,41 %`.

## Ce que les données permettent de conclure

- Le fill rate augmente de 3,76 points entre les deux époques.
- Le TTL moyen ne change que de 1,81 seconde.
- La distance cible moyenne diminue d'environ 0,0068 R.
- Les deux époques restent nettes négatives sur les clôtures observées.
- Avec seulement deux époques actives et 52 intentions, la variation de fill ne
  permet pas d'identifier causalement distance ou durée.

## Donnée manquante pour trancher

Pour chaque intention, le futur outil doit produire :

- markout du marché à l'expiration pour les non-fills ;
- P&L contrefactuel d'une exécution marché conservatrice ;
- excursion du prix entre placement et expiration ;
- distance de limite normalisée par spread et volatilité ;
- délai de fill et état de marché ;
- résultat par `runtime_epoch_id`, actif et session.

La comparaison pertinente est l'**intention-to-trade** : amélioration de prix
des fills moins sélection adverse et coût d'opportunité des non-fills. Le seul
fill rate ne mesure pas la rentabilité.

## Décision

- Aucun changement de durée de vie ou de distance.
- Conserver la collecte DEMO et le journal complet.
- Refaire la comparaison quand des `runtime_epoch_id` natifs et les markouts
  sont disponibles.
- Le lot de journalisation du cycle de vie est techniquement exploitable ; son
  verdict économique reste NO-GO avec `-5,2720 R` sur 11 clôtures.
