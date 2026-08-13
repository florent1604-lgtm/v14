# Vérification indépendante des lots du 13/08/2026

Date de clôture : 13/08/2026. Environnement : `master`, compte DEMO, aucun
changement de seuil, aucun ordre déclenché par cette vérification.

## Résultat global

- suite complète intégrée : **1785 passed, 2 skipped, 0 failed** ;
- suite ciblée horloge/quarantaine : **146 passed** ;
- rapprochement MT5 réel, après alignement d'horloge : **107/107 clôtures
  comptabilisées**, **52/52 lignes live appariées**, 0 orpheline et 0 mismatch ;
- l'état `edge_ok=false` est attendu : 55 clôtures historiques restent
  `coverage_only`, sans contexte inventé, et 52 clôtures n'ont pas encore de
  coût courtier exact ;
- services observés avant intégration finale : `live_demo`, `dashboard` et
  `analystes` actifs, heartbeat armé et frais.

## Commits reproduits

| Commit | Lot | Vérification / réserve |
|---|---|---|
| `43136e2`, `7b67f77`, `16e5ae0` | journal append-only | verrou noyau inter-processus, écriture partielle bouclée, historique non réécrit ; tests multiprocessus verts |
| `bb71fa4`, `2861db0`, `7b67f77` | sauvegarde | collisions, manifeste avant/après, staging et cohérence métier corrigés ; garantie crash-consistante, non transactionnelle |
| `5612487`, `ed2d2eb` | analyse des pertes | 44 sources, 2 exclusions runtime, 42 propres ; `sum(bins)==n==25`, sceau déterministe |
| `52ca98d` | horloge et quarantaine | offsets UTC+3/UTC+2, escalade à l'âge réel, snapshot conservé, résolution append-only avec IN/OUT équilibrés |
| `9aa4c6f`, `b4cdd3d` | trous runtime | qualification par couverture temporelle et 13 tests ; aucun compte absolu de barres |
| `bc44ef1` | rapprochement réel | le CLI utilisait encore des bornes UTC brutes ; corrigé pour l'horloge serveur, puis vérifié sur MT5 : faux 5 orphelins supprimé |
| `ed0c256` | rejeu breakeven | 9/9 lignes UTC rejouées, 43 anciennes refusées ; résultat mesuré mais NO-GO à cause de la fidélité et de la taille |

## Mesures économiques au moment de la clôture

- limites : 57 placées, 15 remplies, 41 expirées, 1 ouverte ; taux résolu
  **26,79 %** ;
- 12 limites clôturées : **-6,2768 R** net ; économie d'entrée moyenne
  **+0,1072 R**, slippage moyen **-0,00155 R** ;
- cohorte live UTC : 15 clôtures, **-7,122 R**, soit **-0,4748 R/trade** ;
- coûts exacts : **0/15** sur la cohorte UTC ; maximum par contexte : **2/20** ;
- rejeu breakeven UTC : 9 trades, effet apparent du seuil 0,30 de +1,359 R
  face à 0,80, mais erreur de fidélité de 0,1544 à 0,1611 R/trade.

## Verdict

Les corrections techniques sont acceptées. Le moteur n'est pas démontré
rentable : l'économie obtenue par les limites ne compense pas la sélection
perdante. **Aucun réglage de sélection, TTL, distance de limite ou breakeven ne
doit être promu** avant une cohorte plus grande, des coûts exacts et une
validation hors échantillon. Les seules tâches encore ouvertes sont donc des
collectes longitudinales, pas des corrections de code dissimulées.

