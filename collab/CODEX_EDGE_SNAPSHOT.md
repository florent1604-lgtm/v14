# Snapshot edge net V14 — Codex

Date : 2026-08-13, 08:00 Paris
Périmètre : collecte DEMO/PAPER, sans ordre forcé ni modification des gardes.

## Verdict

**La relance est saine, mais V14 n'a toujours pas démontré de rentabilité.**
Les trois services sont actifs sur le compte DEMO et le battement avance. La
fenêtre statistiquement exploitable après correction de l'horloge ne contient
que **6 clôtures**, une seule par contexte : aucune promotion n'est possible.

## Reprise après l'arrêt non planifié

- interruption observée : environ 9 heures, du 12/08 22:15 UTC au 13/08
  05:17 UTC ;
- services après relance : `live_demo`, `dashboard` et `analystes` actifs ;
- compte contrôlé en lecture seule : Axi DEMO, magic V14 `14000` ;
- le pending USDMXN `89525897`, expiré pendant l'arrêt, a été récupéré comme
  `expired` avec l'état broker 6 ;
- la clôture JPN225 `89453191`, survenue pendant l'arrêt, a été récupérée avec
  un horodatage marqué `utc` ;
- rapprochement MT5↔journal : **43/43 lignes du journal rapprochées**, aucun
  orphelin journal, aucun doublon, aucun écart de PnL ;
- le rapport global reste `ok=false` à cause de **55 positions MT5 sans ligne
  moderne** et de **43/43 coûts exacts manquants**.

L'interruption ne remet pas les compteurs à zéro. Elle doit être exclue des
mesures de disponibilité et documentée comme trou de collecte ; les clôtures
valides déjà marquées `utc` restent exploitables.

## Nouvelle base propre après correction d'horloge

Les 37 premières clôtures n'ont pas de marqueur d'horloge et restent exclues de
toute analyse temporelle. Les **6** lignes `horloge="utc"` donnent :

- somme : **-4,0455 R** ;
- espérance provisoire : **-0,6742 R/trade** ;
- profit factor : **0,0134** ;
- coûts exacts : **0/6** ;
- meilleur dénominateur par contexte : **1/20**.

Cet échantillon est trop petit pour estimer l'edge, mais il ne fournit aucun
signal permettant d'assouplir les portes. La collecte continue sans optimiser.

## Cycle de vie des ordres limites

Mesure cumulée au dernier relevé :

- **30** limites placées ;
- **7** remplies ;
- **22** expirées ;
- **1** encore ouverte ;
- fill-rate résolu : **24,14 %** (`7 / (7 + 22)`) ;
- économie moyenne réalisée : **+0,0915 R** ;
- slippage moyen : **-0,0017 R**, légèrement favorable ;
- **4** positions issues de limites clôturées : **-2,9883 R** net cumulé.

Le chiffre initial de 66,7 % provenait de seulement 3 placements résolus. Il
n'est pas contradictoire avec 24,14 % : le dénominateur est passé à 29. Le gain
d'entrée moyen ne compense pas, à ce stade, la perte directionnelle des trades.

Le nouveau fill AUS200 `89754001` a été adopté normalement avec son contexte
`AUS200|long|continuation|3p` et une économie réalisée de **+0,0847 R**.

## Erreur identifiée : fill puis clôture avant le tour suivant

Le rapprochement a révélé EURAUD `89198681`, ordre V14 au magic 14000 :

- ouverture et stop séparés d'environ **0,388 seconde** ;
- perte comptable : **-22,48 EUR** ;
- aucune ligne dans `trades.ndjson`.

Le risque existe aussi pour une limite remplie puis clôturée entre deux tours
de 60 secondes : elle n'apparaît plus ni dans `orders_get` ni dans
`positions_get`. Le pending pouvait alors finir classé `unknown`, ce qui perdait
le contexte et le résultat.

Le correctif Codex préparé pour revue Prime :

1. ne modifie pas `_order_issue`, classé HIGH par GitNexus ;
2. reconnaît séparément un ordre historique uniquement si son état broker est
   `FILLED` et si un `position_id` positif est présent ;
3. restaure le contexte pending dans `positions.json` et écrit l'événement
   append-only `filled` ;
4. laisse `manage_once` relire MT5 et journaliser la clôture au même tour ;
5. reste fail-closed si le ticket de position ou le prix de fill manque.

Cette réparation protège les prochains cas. Elle n'invente pas rétroactivement
le contexte EURAUD, désormais perdu.

## Axes d'amélioration recommandés

1. Intégrer puis recharger le correctif de fill ultra-court après revue Prime.
2. Continuer jusqu'à 20 clôtures propres par contexte sans changer les seuils.
3. Séparer dans le dashboard les clôtures `utc` des 37 lignes historiques dont
   l'heure est inconnue, pour éviter une agrégation silencieusement invalide.
4. Ajouter un indicateur explicite de couverture du journal :
   `journalisées / positions stratégie MT5` sur la période moderne.
5. Obtenir les coûts exacts depuis les fills ; tant que la couverture reste à
   0 %, l'analyse nette des coûts demeure incomplète.
6. Conserver le préenregistrement OOS par classe d'actifs : l'énergie et les
   métaux sont des hypothèses à confirmer, pas des univers à sélectionner sur
   les données déjà vues.

## Validation

- tests ciblés limites/télémétrie/journal : **78 réussis, 0 échec** ;
- suite complète : **1 703 réussis, 2 ignorés, 0 échec** ;
- sous-tests : **69 réussis** ;
- Ruff critique (`E9,F63,F7,F82`) : vert ;
- impact de `reconcile_pending_contexts` : LOW, 1 flux affecté ;
- `_order_issue` : impact HIGH, volontairement laissé intact ;
- aucun seuil, quorum, `.env`, service ou ordre modifié par Codex.

Statut : collecte edge `in_progress`; cycle limite et correctif ultra-court en
`review` jusqu'à intégration Prime et observation d'une nouvelle clôture.
