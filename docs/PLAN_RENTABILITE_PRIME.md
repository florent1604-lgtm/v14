# Plan de remédiation Prime — mesure avant optimisation

Date : 2026-08-09

## Contrat désormais appliqué

- `ClosedTrade.pnl_r` provient exclusivement du net comptable MT5 divisé par
  le risque monétaire figé à l'ouverture.
- Une clôture sans `risk_money`, sans contexte d'entrée ou sans net comptable
  est placée dans `results/journal_rejets.ndjson`. Elle ne compte jamais dans
  `MIN_SAMPLES` et ne peut pas promouvoir PROD.
- `cost_r` est la décomposition spread + commission + swap + fee. `None`
  signifie inconnu ; `0.0` signifie coût réellement nul.
- Le spread actuellement capturé par la boucle est une estimation pré-ordre.
  Il reste utile dans `cost_r`, mais ne permet pas de poser
  `exact_cost=True`. La promotion C3 reste donc fermée jusqu'à une mesure issue
  des fills.
- OUT, INOUT et CLOSE_BY sont traités comme des sorties dans le gestionnaire et
  le rapprochement.

## Backfill MT5 — prochain lot, jamais mélangé au live

Le backfill sert à accélérer le diagnostic, pas à ouvrir PROD. Toute ligne
reconstruite doit porter `source="backfill"`; `promotion.evaluate_cells`
continue de n'accepter que `source="live"`.

Étapes exigées avant implémentation :

1. Lire les positions V14 closes via `history_deals_get` et conserver le net,
   les frais, les heures d'ouverture/clôture et la raison de sortie.
2. Pour chaque ouverture, relire les barres strictement antérieures à
   `opened_at` sur le timeframe concerné. Ne jamais inclure une barre future.
3. Rejouer `build_feats`, puis la porte, avec `decided_at=opened_at`.
4. Marquer explicitement le contexte comme reconstruit et mesurer le taux de
   reconstruction réussie. Une absence de timeframe ou de stop initial reste
   un rejet, jamais une valeur par défaut.
5. Écrire dans un fichier séparé, par exemple
   `results/trades_backfill.ndjson`. Aucun remplacement automatique de
   `results/trades.ndjson`.
6. Comparer le backfill et le rapprochement avant toute interprétation : somme
   du net, nombre de positions, doublons et données censurées doivent concorder.

## Journal actif avec lignes rejetées

Un rejet bloque l'edge par conception. La remise en service est une opération
humaine, récupérable et traçable :

1. arrêter la boucle lors d'une fenêtre contrôlée et confirmer zéro écriture en
   cours ;
2. copier le journal actif avec horodatage, sans le supprimer ;
3. identifier les lignes rejetées avec `tools/verifier_accumulation.py` et les
   comparer au rapport `results/reconciliation_mt5.json` ;
4. construire un nouveau journal candidat contenant uniquement les lignes
   validées, sans modifier l'original ;
5. exécuter la suite complète et le rapprochement strict sur ce candidat ;
6. remplacer le journal actif uniquement après validation humaine explicite.

Le code ne répare jamais silencieusement un journal append-only : une réparation
automatique ferait disparaître la preuve de l'incident.

## Télémétrie attendue après redémarrage contrôlé

Le battement doit exposer `stats.tunnel` avec au minimum :

- `flow` : catalogue, sélectionnés, portables, non portables ;
- `portability_refusal` : motifs normalisés ;
- `features` : lisibles / illisibles ;
- `support_passed` : S0 à S4 ;
- `pillar_missing` : piliers manquants ;
- `gate_verdict`, `gate_code` et `post_enter_refusal`.

Le cockpit affiche l'entonnoir, la distribution des piliers et les trois refus
principaux. L'ancien processus ne chargera ces changements qu'après un
redémarrage contrôlé séparé.
