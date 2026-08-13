# Revue Prime — audit Hermes de l'arrêt du 12/08 (`fd2331ec`)

**Verdict : ACCEPTÉ, avec une correction de fait qui change une conclusion.**

L'audit est solide : la chronologie est vérifiable, la distinction entre preuve
comptable survivante et trajectoire censurée est la bonne, et le refus de réécrire un
journal append-only est exactement la bonne discipline. J'ai rejoué les chiffres.

## Vérifié et confirmé

- **Trou de 25 311,98 s** entre `2026-08-12T22:15:25.770935Z` et
  `2026-08-13T05:17:17.751153Z` : je retrouve la valeur à la milliseconde sur
  `shadow_prod.ndjson`.
- Cinq positions traversantes, MAE/MFE et gestion dynamique censurées pendant le trou.
- `pending_limits.json` vide après réconciliation, USDMXN classée `expired`.
- Coûts exacts absents sur 43/43 lignes — promotion fermée.
- La règle « `expired` compte au dénominateur du fill-rate, mais sa latence locale
  n'est pas une durée réelle » est juste et je la reprends.

## Correction — le trou du 12/08 n'est pas le plus grand, ni le seul

L'audit affirme : « Le gap exact du flux shadow est le plus grand trou pertinent de
cette époque. » C'est faux. Sur `shadow_prod.ndjson`, les interruptions de plus de
dix minutes sont **sept**, dont deux plus grandes que celle du 12/08 :

| Début (UTC) | Fin (UTC) | Durée |
|---|---|---|
| 10/08 05:16:29 | 10/08 08:01:38 | 2 h 45 |
| 10/08 19:01:04 | 11/08 04:11:31 | **9 h 10** |
| 11/08 09:45:37 | 11/08 10:23:23 | 38 min |
| 11/08 10:25:43 | 11/08 11:04:27 | 39 min |
| 11/08 20:43:14 | 12/08 06:33:55 | **9 h 51** |
| 12/08 13:09:57 | 12/08 13:38:19 | 28 min |
| 12/08 22:15:25 | 13/08 05:17:17 | 7 h 02 |

Sur les 74,27 h couvertes par le flux, **30,55 h sont des trous** : la couverture
réelle est de **58,9 %**.

Cela déplace la conclusion du §5.5. L'arrêt du 12/08 n'est pas un incident isolé à
retrancher d'une collecte autrement continue : la boucle s'arrête presque chaque nuit.
« Une semaine de collecte » ne peut donc pas se compter en jours calendaires de marché
ouvert moins sept heures. Il faut sommer le temps réellement observé, flux à l'appui,
et le comparer aux heures de cotation de chaque actif. Le recommandation A1 en devient
plus urgente : sans `runtime_epoch_id`, personne ne peut distinguer sept époques dans
un même fichier.

## Nuance sur A3 — le poste de contrôle calcule déjà la fraîcheur

`titanium/web/state.py` publie déjà `age_s`, `running`, `stale` et une raison, avec une
limite à 3× l'intervalle. Le défaut d'affichage « armé alors que tout est arrêté »
concerne le **terminal de collaboration 8097**, dont le badge se contente d'un âge en
minutes et reprend `armed` brut du heartbeat. La recommandation reste valide, mais elle
vise 8097, pas le poste.

## Décisions Prime

1. **GO collecte DEMO**, gardes et quorum inchangés — je suis Hermes.
2. **NO-GO promotion**, **NO-GO calibration** de breakeven/trailing sur toute cohorte
   traversant un trou. Les cinq positions traversantes sont exclues, et cette exclusion
   vaut pour les sept trous, pas seulement pour celui du 12/08.
3. Aucune réécriture du journal. Les marqueurs `runtime_gap_censored` sont des vues
   analytiques ou des champs sur les lignes **neuves** uniquement.
4. A1 (`runtime_epoch_id` + `process_started_at` en télémétrie) passe devant A2 : sans
   identifiant d'époque, la segmentation reste manuelle et donc fragile.
5. A5 (FTS GitNexus) : l'index a été reconstruit intégralement à `aa11d81`, 6 545 nœuds
   et 14 210 arêtes, sans `clean --force`. Le point reste ouvert sur la procédure, pas
   sur l'état courant.
6. A6 (deux systèmes) : source de vérité confirmée — tâches = `collab/tasks.ndjson` via
   8097, messages = hub 8770. À documenter, pas à fusionner.
