# Audit Hermes — arrêt non planifié du 12/08/2026

**Tâche :** `fd2331ec-967b-4ca4-8e4c-99bca23e1a6a`  
**Date de l'audit :** 13/08/2026  
**Rôle :** Hermes, analyse consultative C1  
**Périmètre :** lecture seule des journaux et rapports de réconciliation. Aucun ordre, seuil, service, permission ou fichier runtime modifié par Hermes.

## Verdict exécutif

**La collecte ne doit pas repartir entièrement à zéro, mais elle doit être segmentée en époques et certaines mesures sont invalides.**

L'arrêt est prouvé entre la dernière écriture `shadow_prod` à **2026-08-12 22:15:25.770935Z** et la reprise à **2026-08-13 05:17:17.751153Z**, soit un trou de **7 h 01 min 51,98 s** dans la boucle. Le constat Prime effectué plus tard parlait d'environ neuf heures d'indisponibilité au moment du diagnostic ; ce n'est pas la durée exacte du trou de collecte.

Le courtier a continué à appliquer les SL/TP déjà posés pendant l'arrêt. En revanche, Titanium n'a produit ni décisions shadow, ni nouvelles entrées, ni suivi MAE/MFE, ni gestion dynamique breakeven/trailing pendant le trou. Les résultats des positions traversant l'arrêt ne représentent donc pas une exécution continue de la stratégie.

| Flux | Verdict |
|---|---|
| `shadow_prod.ndjson` | **Trou explicite** de 7 h 01 min 51,98 s. Les lignes avant/après restent utilisables séparément ; aucune statistique de débit/couverture continue ne doit franchir le trou. |
| `trades.ndjson` | 43 lignes JSON valides ; 43/43 rapprochées au courtier dans la fenêtre récente, mais 37 anciennes ont une horloge inconnue/fausse et sont exclues des analyses temporelles. Les 6 lignes `horloge=utc` restent valides comptablement. |
| `excursions.ndjson` | 43 lignes JSON valides, mais les positions ouvertes à 22:15Z ont une MAE/MFE tronquée pendant l'arrêt. La clôture JPN225 à 23:56Z a été récupérée au redémarrage, mais son trajet post-22:15 n'a pas été observé. |
| `limit_lifecycle.ndjson` | L'ordre USDMXN `89525897`, expirant à 22:17:17Z, a été réconcilié comme `expired` à 05:17:17Z. `pending_limits.json` est ensuite vide : pas de limite orpheline restante au relevé. |
| `positions.json` | Les contextes ont survécu. Après réconciliation, quatre positions antérieures à l'arrêt restent suivies et JPN225 a été journalisée. Deux nouvelles positions post-reprise ont ensuite été adoptées. |
| Coûts exacts | Toujours non démontrés : le rapport récent classe 43/43 lignes dans `exact_cost_missing`. Promotion fermée. |

## 1. État concurrent et qualité des preuves

Au début de la mission, le dépôt V14 était sur `master@bde74a5` avec le lot breakeven de Claude non commité. Pendant l'audit, d'autres agents ont continué à travailler : le relevé final est `master@aa11d81` avec plusieurs modifications concurrentes (`AGENTS.md`, `CLAUDE.md`, tâches, snapshot Codex, tests et pending context). Hermes n'a modifié aucun de ces fichiers.

Le `sync` GitNexus obligatoire a échoué sur une incohérence FTS :

```text
FTS index 'file_fts' is inconsistent: term 'croir' is missing during delete
```

Le serveur MCP a été relancé par le wrapper, mais la synchronisation n'est pas validée. Je ne revendique donc pas un index GitNexus sain. Ce rapport repose sur les journaux runtime, le code lu directement et les rapports de réconciliation déjà produits.

## 2. Chronologie prouvée

| Événement | UTC | Preuve |
|---|---:|---|
| Dernière décision shadow avant arrêt | 12/08 22:15:25.770935 | `shadow_prod.ndjson` |
| Dernier heartbeat communiqué par Prime | 12/08 22:15:25 | `PRIME_ETAT_RELANCE_20260813.md` |
| Limite USDMXN placée | 12/08 22:12:18.160973 | `limit_lifecycle.ndjson` |
| Expiration prévue USDMXN | 12/08 22:17:17.995585 | événement `placed` |
| JPN225 clôturée au courtier pendant l'arrêt | 12/08 23:56:07 | `trades.ndjson`, réconciliation MT5 |
| Reprise du flux / réconciliation USDMXN | 13/08 05:17:17 | `shadow_prod.ndjson`, événements `expired` et `closed` |
| Réconciliation récente produite | 13/08 05:46:18 | `reconciliation_mt5_20260813.json` |

Le gap exact du flux shadow est le plus grand trou pertinent de cette époque : **25 311,98 secondes** entre 22:15:25.770935Z et 05:17:17.751153Z.

## 3. Positions traversant l'arrêt

Prime observait cinq positions suivies au dernier heartbeat. Le relevé post-réconciliation permet de reconstruire le groupe :

- USDSGD, ouverte le 10/08 ;
- EURSGD, ouverte le 11/08 ;
- CADJPY, ouverte le 12/08 ;
- NAS100.fs, ouverte le 12/08 ;
- JPN225, ouverte le 12/08 et clôturée à 23:56:07Z pendant l'arrêt.

### Ce qui reste valide

- Les SL/TP courtier déjà posés continuaient d'exister hors processus.
- La clôture JPN225 est comptablement rapprochée : son `pnl_r=-1.0328`, son ticket et son contexte sont présents dans `trades.ndjson`.
- La réconciliation récente trouve **44 clôtures MT5, 43 lignes live, 43 matches**, aucun orphelin journal, aucun doublon et aucun mismatch PnL.
- Les quatre positions encore ouvertes ont conservé leur contexte dans `positions.json`.

### Ce qui est invalidé ou censuré

- MAE, MFE, giveback et ordre temporel creux/pic sont inconnus pendant le trou pour les cinq positions.
- Les décisions de breakeven/trailing qui auraient dû être prises pendant le trou n'ont pas eu lieu. Leur PnL final ne doit pas être mélangé à une cohorte « gestion dynamique disponible 100 % du temps ».
- L'excursion JPN225 écrite au redémarrage conserve son dernier état observé avant l'arrêt ; elle ne décrit pas fidèlement le trajet entre 22:15Z et 23:56Z.
- Toute étude du breakeven, trailing, temps-vers-MFE, durée ou trajectoire doit **exclure les cinq positions traversantes**, ou les marquer explicitement `runtime_gap_censored` dans une vue analytique. Le journal append-only ne doit pas être réécrit.

## 4. Limites et ordres orphelins

La limite USDMXN `89525897` a été placée avant l'arrêt avec expiration prévue à 22:17:17Z. Elle a disparu du courtier pendant l'arrêt et a été classée `expired` à la reprise. Le délai entre expiration prévue et écriture de l'événement est un **retard d'observation**, pas une durée réelle de vie de l'ordre.

Au relevé post-reprise :

- `pending_limits.json` = `{}` ;
- le cycle global publié compte 31 placements, 7 fills, 23 expirations et 1 ordre encore ouvert au moment du heartbeat intermédiaire ;
- des événements ultérieurs ont rempli AUS200 puis AUDCAD ;
- aucune preuve d'une limite orpheline persistante n'a été trouvée.

Conséquence : le résultat `expired` de USDMXN peut alimenter le dénominateur de fill-rate, mais son temps de résolution local ne doit pas alimenter une statistique de latence d'expiration.

## 5. Fenêtres d'analyse : règles exactes

### 5.1 Débit, portabilité, piliers, ENTER et shadow PROD

- **Valide :** fenêtres entièrement antérieures à 22:15:25Z ou entièrement postérieures à 05:17:17Z.
- **Invalide :** toute moyenne par heure/jour, taux de disponibilité, fréquence de setup ou couverture qui traite le trou comme du temps de marché observé.
- **Action :** conserver les deux époques et retirer 25 311,98 secondes du dénominateur de temps observé. Ne pas imputer zéro signal pendant l'arrêt.

### 5.2 Edge comptable (`pnl_r`)

- Les 43 tickets existants sont rapprochés au courtier, sans mismatch PnL.
- Les **37 lignes sans `horloge=utc`** restent utilisables uniquement pour des agrégats non temporels déjà qualifiés, mais sont interdites pour tout rejeu de barres ou durée.
- Les **6 lignes `horloge=utc`** sont la nouvelle cohorte propre pour l'analyse temporelle.
- JPN225, bien que `horloge=utc`, traverse l'arrêt : son PnL comptable est valide mais sa trajectoire est censurée.
- `89198681` apparaît comme clôture MT5 manquante du journal récent. Il s'agit d'une observation courtier sans contexte live journalisé ; elle ne doit pas être inventée ni injectée dans une cellule d'edge.

### 5.3 Excursions et breakeven/trailing

- Repartir à zéro **pour les mesures temporelles strictes** : le compteur éligible commence aux lignes `horloge=utc` qui ne traversent aucun gap runtime.
- Ne pas supprimer les anciennes excursions ; les conserver comme historique censuré.
- Exclure les cinq positions traversant l'arrêt de toute calibration de seuil.

### 5.4 Cycle de vie des limites

- Les issues (`filled`, `expired`, `canceled`) restent comptables si confirmées par l'historique courtier.
- Les latences locales `at - expires_at` qui traversent l'arrêt sont invalides.
- Segmenter les cohortes avant-arrêt et après-reprise ; ne pas comparer le fill-rate de 3 placements à celui de 22/31 placements sans préciser l'époque et le dénominateur.

### 5.5 « Une semaine de collecte »

La semaine ne repart pas à zéro. Elle se mesure en **temps de marchés ouverts réellement observé**, avec une interruption explicite de 7 h 01 min 51,98 s. Le trou est retranché du temps d'observation. Les actifs dont le marché était fermé pendant une partie du trou doivent rester traités par leurs propres heures de cotation, pas par une durée globale unique.

## 6. Erreurs et axes d'amélioration identifiés

### A1 — CRITIQUE mesure : absence de marqueur d'époque runtime dans chaque événement

Le trou est reconstructible aujourd'hui par différence de timestamps, mais aucune ligne ne porte un `runtime_epoch_id` ou un `boot_id`. Cela autorise des agrégations accidentelles entre processus et redémarrages.

**Amélioration :** ajouter en télémétrie uniquement un identifiant d'époque non secret, le PID racine et `process_started_at` aux heartbeats et événements. Les agrégateurs doivent refuser de mélanger les époques sans regroupement explicite.

### A2 — ÉLEVÉ mesure : excursion censurée non distinguée d'une excursion complète

Une fermeture récupérée après redémarrage reçoit une ligne d'excursion, mais le système ne sait pas exprimer « suivi interrompu entre t1 et t2 ».

**Amélioration :** champ analytique additif `runtime_gap_censored` et bornes `observation_gap_start/end`, sans réécrire les événements historiques. Le compteur doit être dérivé lors de l'analyse ou ajouté aux nouvelles lignes seulement.

### A3 — ÉLEVÉ exploitation : heartbeat persistant peut sembler vivant après crash

Le fichier reste lisible et `armed=true` même lorsque le processus et MT5 sont arrêtés. Prime l'a constaté.

**Amélioration :** toute interface doit calculer `age_seconds` et afficher `STALE/STOPPED` si l'âge dépasse 2 à 3 intervalles. Ne jamais afficher « armé » sans fraîcheur et identité du processus.

### A4 — ÉLEVÉ promotion : coûts exacts toujours absents

Le rapprochement récent montre `exact_cost_missing` sur 43/43 lignes. Le PnL net comptable est disponible, mais la décomposition exacte spread/commission/slippage ne l'est pas.

**Amélioration :** maintenir la promotion fermée et mesurer la couverture exacte par époque. Aucun contexte ne peut atteindre un statut validé tant que le contrat de coûts n'est pas couvert.

### A5 — MOYEN ingénierie : GitNexus FTS se corrompt de façon récurrente

Le `sync` a de nouveau échoué sur `file_fts`, terme `croir`. Le protocole d'équipe relance le MCP mais ne rétablit pas un index sain.

**Amélioration :** réparer la procédure sans `gitnexus clean --force` non borné, car cette commande supprime aussi le shim `.gitnexus/run.cjs`. Prévoir une reconstruction FTS atomique et un fallback documenté par CLI globale.

### A6 — MOYEN gouvernance : deux systèmes de tâches/cohérence de dépôt

Le hub MCP 8770 ne porte que messages/presence ; les tâches V14 vivent dans `collab/tasks.ndjson` et sont exposées par le terminal 8097. Le nom « Collab-Hub » peut faire croire que `/v1/tasks` existe sur 8770, alors qu'il retourne 404.

**Amélioration :** documenter clairement la source de vérité : tâches = `titanium.collab_tasks`/8097 ; messages = MCP 8770. À terme, proposer une façade unique sans dupliquer le journal.

## 7. Décision opérationnelle

1. **GO poursuite de collecte DÉMO**, gardes et quorum inchangés.
2. **NO-GO promotion** et NO-GO calibration de breakeven/trailing sur la cohorte traversant l'arrêt.
3. Ne pas repartir à zéro pour le PnL comptable et le cycle des limites ; segmenter par époque.
4. Repartir sur une cohorte propre pour les analyses temporelles : `horloge=utc`, aucune traversée de gap, frais qualifiés.
5. La semaine de collecte continue, mais le trou est explicitement retranché du temps observé.
6. Conserver les journaux append-only ; corriger par vues/flags analytiques, jamais par réécriture de l'historique.

## 8. Preuves lues/exécutées

- `results/loop_heartbeat.json`
- `results/shadow_prod.ndjson` — 18 086 lignes JSON valides au relevé
- `results/trades.ndjson` — 43 lignes valides, 37 horloge inconnue + 6 UTC
- `results/excursions.ndjson` — 43 lignes valides
- `results/limit_lifecycle.ndjson` — 68 événements au relevé initial de l'audit
- `results/positions.json`, `results/pending_limits.json`
- `results/reconciliation_mt5_20260813.json`
- `results/reconciliation_mt5_post_restart.json`
- `collab/PRIME_ETAT_RELANCE_20260813.md`
- `titanium/execution/position_manager.py`
- `titanium/execution/pending_context.py`
- `tools/reconcile_mt5_journal.py`
- état services : `live_demo`, `dashboard`, `analystes` actifs au relevé final

## Conclusion

L'arrêt n'a pas détruit la preuve comptable : les contextes ont survécu, JPN225 a été récupérée et la limite USDMXN a été classée expirée. Il a en revanche créé une **censure de trajectoire** et une rupture d'époque. La bonne réponse n'est ni d'effacer tout l'historique ni de faire comme si le processus avait tourné : il faut conserver les données, segmenter les époques, exclure les positions traversantes des analyses temporelles et maintenir la promotion fermée.
