# P0 — récupération des clôtures MT5 invisibles

## Défaut prouvé

Une position peut être ouverte puis fermée avant le prochain tour de 60
secondes. EURAUD `89198681` a ainsi vécu environ 388 ms et perdu 22,48 EUR sans
apparaître dans `trades.ndjson`. Le journal d'edge était donc biaisé en faveur
du bot.

## Correction préparée pour Prime

- récupération bornée aux 7 derniers jours via `history_deals_get` ;
- attribution par magic/commentaire V14 ;
- écriture append-only et idempotente dans `journal_rejets.ndjson` ;
- conservation du résultat comptable MT5 ;
- `coverage_only=true` et `edge_eligible=false` quand le contexte n'a jamais
  été observé ;
- protection des tickets encore suivis afin qu'un échec I/O ne leur fasse pas
  perdre leur contexte ;
- réconciliation distinguant `matched` (edge mesurable) et `accounted`
  (couverture comptable).

## Preuves DEMO — 13 août 2026

- 55 clôtures historiques récupérées, dont EURAUD `89198681` ;
- 98/98 clôtures MT5 comptabilisées sur 7 jours ;
- `missing_in_journal=0` ;
- 55 clôtures sans contexte restent hors `trades.ndjson` ;
- 1 707 tests réussis, 2 ignorés, 0 échec, 69 sous-tests réussis ;
- Ruff critique vert ;
- rapport : `results/reconciliation_mt5_7d_history_recovery.json`.

Le rapport global reste rouge pour deux raisons distinctes de la couverture :
43 clôtures appariées n'ont pas de coûts exacts, et le ticket `89506157` est
hors de la fenêtre de 7 jours choisie mais présent dans le journal d'edge.

GitNexus classe la modification de `manage_once` HIGH : 31 dépendances et deux
processus touchés. Prime doit relire et intégrer avant tout rechargement. Codex
n'a modifié aucun seuil, service, `.env` ou ordre.

## Défaut connexe découvert pendant la preuve

Le lecteur de clôture utilisait le dernier deal disponible lorsque l'historique
ne contenait aucun `OUT`, `INOUT` ou `CLOSE_BY`. Un deal `ENTRY` pouvait donc
être pris pour sa propre sortie après une disparition transitoire de
`positions_get`. Le correctif refuse désormais cette fausse preuve, conserve
le contexte dans `positions.json` et réessaie au tour suivant.

Le test reproduit l'ancien comportement rouge, puis verrouille la chaîne
complète. Le retry est persisté dans `positions.json`. Les essais 1 à 14
conservent le contexte ; au quinzième essai ou après 15 minutes, la position
est mise en quarantaine avec le motif et le nombre d'essais. Une réapparition
dans `positions_get` remet le compteur à zéro.

Validation finale : **1 719 tests réussis, 2 ignorés**, 69 sous-tests réussis.
GitNexus classe `_cloture_depuis_historique` HIGH (46 dépendances).

NAS100 `89506157` reste un écart d'historique à surveiller : sa clôture est
présente dans le journal, mais le snapshot MT5 courant ne rend plus que son
deal d'entrée. Cette absence actuelle ne suffit pas à invalider rétroactivement
la ligne append-only produite lorsqu'une sortie était possiblement visible.
