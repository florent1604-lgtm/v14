# Revue Hermes post-production des modifications LLM V14

**Date :** 13/08/2026  
**Tâche Hub :** `25d7218e-8830-4b3a-9179-1c0b66b0e28c`  
**Base auditée :** `aa11d81..9c5f17f`, HEAD figé au début `e4396cd42005db078bb87422180b243846f00578`  
**Mode :** audit indépendant, PAPER/DEMO only. Aucun ordre, seuil, service, `.env` ou état runtime modifié.

## Verdict exécutif

Le lot de récupération est une amélioration réelle : il corrige la disparition silencieuse des clôtures éclair, refuse de fabriquer un prix de sortie depuis un deal d'entrée, protège le contexte pendant un retry et sépare couverture comptable et observation d'edge. La suite complète est verte.

Cependant, **le lot et les analyses associées ne sont pas encore assez fiables pour justifier un changement de stratégie ou une promotion**. Deux défauts de mesure sont de sévérité HAUTE :

1. `reconcile().ok` peut être vert alors que `matched=0` et que 100 % des clôtures manquent à l'edge ;
2. l'analyse « la sortie fonctionne, l'entrée ne fonctionne pas » utilise une cohorte qui inclut des trajectoires censurées par l'arrêt et tire une conclusion causale d'un groupe défini par l'issue.

La mécanique récupérée peut rester en collecte DEMO. Toute optimisation reste fermée.

## Preuves exécutées

| Contrôle | Résultat réel |
|---|---|
| Tests ciblés récupération/limites/réconciliation/sauvegarde | `97 passed in 2.43s` |
| Suite complète | `1719 passed, 2 skipped, 69 subtests passed in 122.07s` |
| Ruff critique | `All checks passed!` |
| GitNexus sync final | `6671 nodes, 14434 edges, 318 clusters, 300 flows` |
| Rapport Claude, 44 lignes | sommes principales reproduites |
| Bins MFE du rapport Claude | **non reproduits** : source = `[12,3,6,5]`, rapport = `[12,3,6,6]` |
| Sémantique `reconcile` | reproduit : `ok=True, matched=0, accounted=1, missing_in_edge=['1']` |
| Collision sauvegarde même seconde | reproduit : `FileExistsError` |

## Constats classés

### HAUTE — H1 : un rapport de réconciliation peut être vert sans aucune observation d'edge

**Code :** `titanium/analysis/reconciliation.py`, `reconcile`, lignes 202-206 et 262-269.

`missing_in_journal` exclut tous les tickets présents dans `journal_rejets.ndjson`. Le booléen `ok` regarde `missing`, mais pas `missing_in_edge`. Reproduction minimale : une clôture MT5, zéro trade edge, un rejet quelconque portant le même ticket donne :

```text
ok=True, matched=0, accounted=1,
missing_in_journal=[], missing_in_edge=['1']
```

**Risque :** le vert signifie seulement « couvert comptablement », alors que les lecteurs peuvent l'interpréter comme « cohorte d'edge complète ». Un rejet arbitraire, y compris un `SORTIE_INTROUVABLE_APRES_15_ESSAIS`, suffit à masquer le manque.

**Correction demandée :**

- séparer `accounting_ok` et `edge_ok` ;
- `accounting_ok` vérifie `accounted == mt5_closed` et les incohérences comptables ;
- `edge_ok` doit être faux dès que `missing_in_edge` est non vide ou que les coûts exigés manquent ;
- conserver `ok` uniquement comme alias documenté de l'un des deux, pas un mélange ambigu ;
- afficher `matched/mt5_closed` et `accounted/mt5_closed` dans heartbeat/dashboard avec seuil d'alerte.

**Test requis :** 1 MT5 / 0 edge / 1 rejet doit produire `accounting_ok=True`, `edge_ok=False`.

### HAUTE — H2 : l'analyse « entrée, pas sortie » dépasse ce que les données prouvent

**Rapport :** `collab/CLAUDE_OU_EST_LA_PERTE.md`.

Les sommes annoncées sur les 44 premières lignes sont reproductibles :

```text
init       26  -25.3847R  -0.9763R/trade
breakeven  10   -0.3117R  -0.0312R/trade
trailing    8   +8.6782R  +1.0848R/trade
```

Mais deux problèmes invalident la conclusion forte :

1. **Biais conditionnel.** La branche `trailing` est définie par le fait d'avoir déjà atteint un mouvement favorable. Qu'elle soit profitable ne prouve pas que la politique de trailing « fonctionne » causalement. Il faut un contrefactuel sur les mêmes entrées et une cohorte indépendante.
2. **Censure runtime connue.** Le rapport d'arrêt exige d'exclure les positions traversant le trou pour toute étude MFE/trailing. Les 44 lignes incluent au moins JPN225 `89453191` et NAS100 `89506157`, dont la trajectoire traverse l'arrêt. NAS100 appartient précisément à la branche trailing (+0.5488R). Après exclusion des deux clôtures traversantes : 42 lignes, init 25, trailing 7 ; la thèse descriptive reste plausible, mais la cohorte et les chiffres changent.

Le drapeau `censored` d'`excursions.ndjson` ne représente pas la censure runtime : il vaut actuellement vrai pour une sortie négative non-trailing. Il ne permet donc pas d'exclure automatiquement les trous de collecte.

**Correction demandée :** reformuler le verdict en « les pertes observées sont concentrées dans les stops initiaux ; l'hypothèse prioritaire concerne la sélection/entrée ». Ne pas déclarer la causalité des sorties. Ajouter une vue `runtime_gap_censored` dérivée d'un journal d'époques runtime, puis refaire l'analyse sur cohorte propre.

### HAUTE — H3 : incohérence arithmétique dans les bins MFE

**Rapport :** lignes 30-38 de `CLAUDE_OU_EST_LA_PERTE.md`.

Le rapport annonce « 27 pertes pleines » et des bins `[12,3,6,6]`, somme 27, alors que son tableau de sortie annonce 26 stops initiaux. Recalcul exact sur les 44 lignes utilisées : 26 pertes `init`, bins `[12,3,6,5]`.

Les deux pourcentages principaux restent exacts sur N=26 :

- `<0.05R` : 12/26 = 46.15 %, pas 44.4 % ;
- `<0.20R` : 15/26 = 57.69 %, pas 55.6 %.

**Correction demandée :** générer tout rapport numérique depuis un artefact JSON/CSV scellé, interdire les nombres saisis manuellement, et ajouter une assertion `sum(bins) == n_cohorte`.

### MOYENNE — M1 : la quarantaine après 15 essais est irréversible pour l'edge

**Code :** `position_manager.manage_once`, lignes 963-989 ; `history_recovery._tickets`, lignes 21-37 et 129-134.

Après escalade, l'état contextuel est supprimé et le ticket est inscrit dans `journal_rejets`. Si le deal OUT apparaît ensuite, `history_recovery` considère le ticket connu et ne complète ni la preuve comptable du rejet ni `trades.ndjson`. Le choix protège contre un blocage infini mais transforme une latence longue en perte définitive d'edge.

La borne est en outre dépendante de la cadence :

- boucle 15 s : 15 tours = 225 s ;
- live_demo 60 s : 15 tours = 900 s ;
- worker dégradé 300 s : 75 min.

Le `OU 15 minutes` ne rend donc pas la borne uniforme.

**Correction demandée :**

- escalade non destructive : déplacer l'état complet dans une quarantaine récupérable ou conserver un snapshot contextuel séparé ;
- lorsqu'un OUT apparaît tardivement, produire une nouvelle entrée append-only de résolution et rendre le trade edge-éligible si toutes les preuves sont présentes ;
- piloter la borne par âge réel, la métrique de tours n'étant qu'une télémétrie ;
- mesurer la distribution réelle de latence MT5 avant de fixer la durée.

### MOYENNE — M2 : la récupération historique et la réconciliation ont deux contrats d'horloge

`aggregate_mt5_deals._iso()` étiquette directement l'epoch comme UTC. Un correctif concurrent non commité dans `history_recovery.py` retranche ensuite le décalage serveur et élargit la requête de ±1 jour. Ce correctif répond à une anomalie réelle, mais le contrat reste local et double : d'autres appelants de `aggregate_mt5_deals` continuent de recevoir l'heure non corrigée.

**Correction demandée :** définir une convention unique à la frontière MT5, tester le comportement réel de `history_deals_get` autour des bornes et des changements DST, et éviter que chaque consommateur corrige l'epoch différemment.

### HAUTE — H4 : `_filled_order_snapshot` accepte une preuve broker qui ne correspond pas à la demande

**Code :** `pending_context.py`, lignes 235-277 et 386-456.

Le couple `ORDER_STATE_FILLED + position_id` est une bonne preuve minimale, mais le résultat de `history_orders_get(ticket=...)` est accepté sans vérifier :

- `order.ticket == order_ticket` ;
- `order.magic == magic` ;
- symbole, côté et volume identiques au contexte pending ;
- prix fini et plausible ;
- cohérence du `position_id` et du prix avec les deals IN historiques.

**Reproduction indépendante :** une requête pour le ticket `555` dont l’adaptateur renvoie `ticket=999`, `position_id=888`, `magic=999`, symbole `WRONG` et `price_current=999.0` est acceptée et retourne un snapshot pour la position 888. Le code peut donc attacher le contexte de 555 à une autre position et écrire un faux événement `filled`.

`price_current` est aussi préféré à `price_open` sans preuve que ce champ est le prix d’exécution historique. Le prix pondéré des deals IN est une preuve plus forte.

**Correction demandée :** recouper ordre et deals par ticket, position, magic, symbole, côté et volume ; prendre le prix des deals IN. Sans concordance complète, rester fail-closed. Ajouter les tests négatifs correspondants.

### HAUTE — H5 : sauvegarde vérifiée fichier par fichier, pas snapshot cohérent

**Code :** `titanium/sauvegarde.py`.

Les SHA-256 prouvent que chaque destination égale sa source au moment du hash. Ils ne prouvent pas que les 12 fichiers représentent le même instant alors que la boucle écrit. Une clôture peut apparaître dans `trades.ndjson` après sa copie mais avant `positions.json`, créant un instantané transversal incohérent. Le nom n'a qu'une résolution d'une seconde : deux lancements simultanés ou la même seconde produisent `FileExistsError` (reproduit).

**Correction demandée :** verrou de sauvegarde interprocessus, identifiant unique avec microsecondes/UUID, capture de métadonnées avant/après, et vérification de cohérence métier (`ticket`/position/pending/lifecycle). Documenter qu'il s'agit d'une sauvegarde crash-consistante ou implémenter une vraie barrière de snapshot.

### MOYENNE — M3 : une position ouverte avant la fenêtre peut produire un net incomplet

**Code :** `history_recovery.py` et `aggregate_mt5_deals`.

La récupération nominale est bornée à sept jours. Si une position est ouverte avant la borne mais clôturée dedans, la commission d’entrée et les frais antérieurs peuvent manquer ; `opened_at` devient alors la première ligne visible. `net_currency` ne doit pas être présenté comme complet sans preuve du deal IN.

**Correction demandée :** détecter l’absence d’entrée, marquer `accounting_complete=false`, étendre ponctuellement la recherche par position, et tester une ouverture J-8/clôture J-1.

### MOYENNE — M4 : la dernière sortie dépend de l’ordre renvoyé par MT5

**Code :** `_cloture_depuis_historique`, boucle lignes 460-474.

La fonction remplace `sortie` à chaque deal OUT sans trier. Elle suppose que MT5 renvoie l’historique chronologiquement. Des deals partiels retournés dans un autre ordre peuvent produire un mauvais prix et un mauvais `ts_exit`, malgré un net agrégé correct.

**Correction demandée :** sélectionner explicitement le deal OUT au `time_msc/time` maximal et tester toutes les permutations de plusieurs sorties.

### BASSE — L1 : coût nominal croissant de la récupération

Chaque passage relit deux NDJSON entiers et interroge 7 jours d'historique. Correct aujourd'hui, O(N) permanent à mesure que les journaux grossissent.

**Amélioration :** cache d'offset/tickets, fenêtre nominale courte avec balayage complet périodique, métrique de durée et seuil d'alerte.

### BASSE — L2 : `load_state` reste silencieux sur un JSON illisible

Le défaut est déjà ouvert dans le Hub (`72911e8f...`). Il reste prioritaire : un fichier corrompu est confondu avec une première exécution et provoque des adoptions sans contexte.

**Correction attendue :** distinguer absent/illisible, copie `.bak`, alerte heartbeat et arrêt fail-closed de la collecte d'edge.

## Évaluation des composants

| Composant | Verdict |
|---|---|
| `history_recovery` | GO collecte DEMO sous surveillance ; aucun faux contexte inventé |
| retry `SORTIE_INTROUVABLE` | amélioration correcte, mais escalade irréversible à revoir |
| `_filled_order_snapshot` | principe juste, identité broker à durcir |
| `matched` vs `accounted` | séparation utile, booléen `ok` trompeur |
| sauvegarde tournante | utile contre suppression/corruption, pas un snapshot transactionnel |
| analyse des pertes | agrégats principaux reproduits ; conclusion à nuancer et cohorte à nettoyer |
| changement de seuil / promotion | **NO-GO** |

## Consignes prioritaires pour Prime

1. P0 mesure : séparer `accounting_ok` et `edge_ok`, rendre `missing_in_edge` bruyant.
2. P0 analyse : corriger le rapport Claude (N/bins/pourcentages), exclure les trajectoires runtime-gap et retirer la causalité non démontrée.
3. P1 récupération : rendre la quarantaine tardivement résoluble sans perdre le contexte.
4. P1 exécution : durcir l'identité de `_filled_order_snapshot` avec tests négatifs.
5. P1 données : verrouiller et qualifier la cohérence des sauvegardes.
6. P1 historique : détecter les positions sans deal IN complet et trier explicitement les sorties.
7. P1 état : terminer la tâche `load_state` bruyante avant de considérer la collecte robuste.

## Agent de recherche créé

Le skill partagé `v14-research-verifier` a été créé dans le catalogue canonique et synchronisé vers Claude, Codex et Hermes. Il impose : sources primaires, versions/licences/compatibilité, pilotes bornés, vérification indépendante des nombres, traitement UTC/coûts/censure/dépendance, et aucun réglage automatique.

Chemins :

- `.agents/skills/v14-research-verifier/SKILL.md` ;
- miroirs `.claude/skills`, `.codex/skills`, `.hermes/skills` ;
- validation : `49 skills uniques, 49 SKILL.md, 3 miroirs cohérents`.

Une mission web indépendante a été lancée pour rechercher les méthodes et outils compatibles. Ses résultats devront être annexés après vérification des URLs primaires ; ils ne modifient pas le présent verdict.
