# Spécification `runtime_epoch_id` — V14

Date : 13/08/2026  
Statut : spécification A1, aucune modification runtime  
Périmètre : PAPER/DEMO uniquement

## Verdict

Une statistique temporelle V14 ne doit plus agréger silencieusement des lignes
issues de processus séparés. Les données actuelles contiennent sept interruptions
de plus de dix minutes et seulement 58,9 % de couverture observée sur la fenêtre
auditée. Le temps mural n'est donc pas du temps de collecte.

## Contrat d'identité

Au démarrage de la boucle racine, V14 crée une fois :

- `runtime_epoch_id` : UUID v4 aléatoire en minuscules, non secret, jamais
  dérivé du compte MT5, d'une clé ou du PID et jamais réutilisé ;
- `process_started_at` : instant UTC ISO-8601 avec suffixe `+00:00` ;
- `root_pid` : PID du processus racine, utile uniquement au diagnostic local.

Ces trois valeurs sont immuables jusqu'à la fin du processus. Un redémarrage,
même immédiat, produit obligatoirement un nouvel identifiant.

## Propagation obligatoire

Les champs doivent apparaître au niveau racine dans :

1. `results/loop_heartbeat.json` ;
2. chaque nouvelle ligne de `results/shadow_prod.ndjson` ;
3. chaque nouvelle ligne de `results/trades.ndjson` ;
4. chaque nouvelle ligne de `results/excursions.ndjson` ;
5. chaque nouvel événement de `results/limit_lifecycle.ndjson`.

Un événement dérivé conserve l'époque de l'événement source. Une clôture
récupérée après redémarrage porte en plus :

- `observed_runtime_epoch_id` : époque qui a observé/récupéré la clôture ;
- `origin_runtime_epoch_id` : époque d'ouverture si elle est prouvée ;
- `runtime_gap_censored=true` si la trajectoire traverse une époque inconnue ou
  un intervalle non observé.

L'absence de `origin_runtime_epoch_id` reste `null` : elle ne doit jamais être
remplacée par l'époque courante.

## Règle d'agrégation

Par défaut, tout agrégateur temporel doit :

- grouper explicitement par `runtime_epoch_id`, ou refuser le calcul ;
- refuser un mélange comprenant une ligne `legacy_unknown` ;
- calculer la couverture comme la somme des durées observées par époque, jamais
  comme `dernier_timestamp - premier_timestamp` ;
- exclure des calculs MFE/MAE, trailing et replay toute position dont
  `runtime_gap_censored=true` ;
- autoriser une agrégation multi-époques uniquement avec une option explicite
  `allow_cross_epoch=true` et publier le détail par époque dans le résultat.

Une agrégation comptable de P&L peut conserver une clôture censurée si les deals
et coûts sont complets, mais elle ne peut pas l'utiliser comme trajectoire de
prix ou preuve causale de gestion de sortie.

## Migration sans réécriture

Les journaux existants restent append-only. Aucune ligne historique n'est
modifiée.

Les lecteurs affectent virtuellement aux anciennes lignes :

```json
{
  "runtime_epoch_id": null,
  "runtime_epoch_quality": "legacy_unknown"
}
```

Un outil hors ligne peut produire un manifeste séparé d'époques inférées à
partir des trous du flux. Ces identifiants doivent commencer par `inferred-` et
ne remplacent jamais une identité runtime native. Le seuil d'inférence, les
bornes, les sources et le SHA-256 des fichiers doivent figurer dans le
manifeste.

## Critères d'acceptation testables

1. Deux démarrages consécutifs produisent deux UUID différents.
2. Toutes les lignes créées pendant un processus portent le même triplet.
3. Un redémarrage change l'UUID même si le PID est réutilisé.
4. Le heartbeat et les cinq flux exposent les champs sans secret.
5. Une clôture récupérée après redémarrage conserve origine et observation
   distinctes et devient `runtime_gap_censored=true`.
6. L'agrégateur refuse par défaut deux UUID différents.
7. L'agrégateur refuse par défaut une ligne `legacy_unknown`.
8. Le mode multi-époques explicite retourne le détail par époque.
9. La couverture additionne les durées observées et exclut les trous.
10. Les journaux historiques gardent exactement les mêmes tailles et SHA-256.

## Ordre d'implémentation recommandé

1. créer un petit objet immuable `RuntimeEpoch` sans dépendance MT5 ;
2. l'instancier dans la boucle racine et le transmettre explicitement ;
3. ajouter les champs aux écrivains append-only ;
4. rendre les agrégateurs fail-closed ;
5. ajouter le manifeste de compatibilité legacy ;
6. vérifier les SHA-256 historiques avant/après.

Aucun seuil, garde de risque ou comportement d'ordre n'est concerné.
