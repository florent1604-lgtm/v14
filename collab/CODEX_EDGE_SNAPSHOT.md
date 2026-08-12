# Snapshot edge net V14 — Codex

Date : 2026-08-12, 13:16 Paris
Sources : `results/trades.ndjson`, `results/reconciliation_mt5_recent.json`,
`results/loop_heartbeat.json`, `results/candidats_grappe.ndjson`

## Verdict

**La collecte progresse, mais la performance observée reste négative et aucune
rentabilité n'est démontrée.** Le journal contient maintenant 29 clôtures toutes
rapprochées ticket par ticket avec MT5. Aucun contexte n'atteint 20 clôtures
(maximum : 3) et aucune ligne ne possède une décomposition de coût exacte.

## Mesure globale provisoire

- clôtures : **29** (11 positives, 18 négatives) ;
- PnL MT5 des 29 tickets rapprochés : **-295,82 EUR** ;
- somme : **-9,6826 R** ;
- espérance nette observée : **-0,3339 R/trade** ;
- profit factor : **0,436** ;
- Sharpe par trade non annualisé : **-0,356** ;
- drawdown maximal de la courbe en R : **13,6666 R** ;
- bootstrap i.i.d. 95 % de l'espérance : **[-0,6572 ; +0,0130] R** ;
- couverture des coûts exacts : **0/29 (0 %)**.

Le PnL comptable est net. `cost_r` reste une estimation de décomposition ;
`exact_cost=false` interdit de déclarer la couverture des coûts complète.

## Réconciliation MT5

Rapport régénéré en lecture seule le 12/08 à 11:00 UTC :

- journal live rapproché : **29/29 (100 %)** ;
- tickets du journal absents de MT5 : **0** ;
- doublons : **0** ;
- écarts de PnL : **0** ;
- coûts exacts manquants : **29/29** ;
- 54 positions MT5 historiques antérieures au périmètre du journal moderne
  restent sans ligne de journal.

Le champ global `ok=false` vient donc des 54 observations historiques et des
29 coûts exacts manquants, pas d'une rupture nouvelle de la réconciliation.

## Sous-motifs des refus EXECUTION et ordres limites

Heartbeat au 12/08 à 11:16:03 UTC : **254 tours**, **1 800 ENTER**,
**628 refus EXECUTION**, **0 ordre envoyé** et **0 limite placée**.

Le processus démarré à 08:33 ne conservait que le compteur agrégé
`post_enter_refusal.EXECUTION`. Les sous-motifs historiques étaient imprimés
dans une console sans journal persistant : les 576 raisons passées ne peuvent
pas être reconstituées de façon honnête.

Le correctif Codex ajoute deux compteurs structurés au heartbeat :

- `execution_refusal.<reason>` : raison stable (`RETCODE_*`, `WALL_*`,
  `LOT_*`, `PAS_DE_PRIX`, etc.) ;
- `execution_gate_failed.<gate>` : porte précise (`wall`, `lot`, `send`, etc.).

Le compteur historique `post_enter_refusal.EXECUTION` est conservé pour la
continuité des séries. Le correctif est testé mais ne sera observable qu'après
un rechargement contrôlé du moteur par Prime ou Florent.

Le câblage statique est confirmé : `tour()` appelle `place_limit_order()` ; en
cas de succès `_memoriser_contexte_limit()` appelle `save_pending_context()` ;
au tour suivant `reconcile_pending_contexts()` rattache le contexte au fill ou
le purge après expiration. **La preuve dynamique reste bloquée par l'absence de
toute limite naturellement acceptée** ; aucun ordre n'a été forcé pour fabriquer
la preuve.

## Journal des grappes

Le journal append-only est alimenté : **126 candidats** au dernier relevé,
dernière ligne à **11:00:12 UTC**. Les colonnes `cluster`, `cluster_members`,
`cluster_risk_engaged_pct`, `proposed_risk_pct`, `support_pillars` et `rank`
sont présentes. Le test de déduplication et d'append-only passe.

## Sensibilité à la veille du PC

La veille Windows du 10/08 21:02 au 11/08 06:11 Paris a perturbé trois
observations identifiées (`live:87940036`, `live:88004818`, `live:87650131`).
Hors ces trois lignes : **26 clôtures**, espérance **-0,2661 R**, profit factor
**0,519**, Sharpe/trade **-0,275**. La conclusion reste inchangée.

## Couverture actuelle

- contexte le plus répété : `NAS100.fs|long|continuation|4p`, **3 clôtures** ;
- `EURCAD|short|continuation|4p` : **3 clôtures** ;
- `EURNZD|short|continuation|3p` et `USDCAD|short|continuation|4p` :
  **2 clôtures** chacun.

## Validation et critère de fin

- tests télémétrie, limites, contexte pending, grappes et journal :
  **65 réussis, 0 échec** ;
- impact GitNexus de `tour` : **LOW**, 1 processus affecté ;
- aucun seuil, quorum ou garde de risque modifié ;
- aucun ordre forcé et aucun redémarrage du moteur effectué par Codex.

La tâche reste `in_progress`. Fin technique immédiate : recharger le correctif
de télémétrie puis publier le premier sous-motif naturel et la première preuve
du chemin limite, ou un bloqueur courtier explicite. Fin statistique : premier
contexte à 20 clôtures propres ; promotion seulement avec au moins 60
observations par cellule, 90 % de coûts exacts, bootstrap et validation OOS.
