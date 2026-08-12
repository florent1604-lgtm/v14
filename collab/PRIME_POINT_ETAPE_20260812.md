# Point d'étape Prime — 12/08/2026, 14:05 Paris

Base : `master@e30fa2d`. Suite complète **1605 passés, 2 skippés**. Compte DEMO
10055401, équity ~4 403 EUR. Aucun ordre forcé, aucun seuil ni quorum touché.

## Ce qui a changé aujourd'hui

1. **Zéro ordre limite n'était posé depuis la mise en production** — et personne
   ne pouvait le voir. La télémétrie de Codex (`execution_refusal` /
   `execution_gate_failed`) l'a rendu visible au premier rechargement :
   `ORDER_SEND_NUL` sur **19 tentatives sur 19**.

   Deux défauts empilés, corrigés l'un après l'autre :

   | # | défaut | symptôme | preuve |
   |---|---|---|---|
   | 1 | `expiration` passé en `datetime` | `order_send` renvoie `None` **avant tout envoi** | `last_error = (-2, 'Invalid "expiration" argument')` |
   | 2 | expiration calculée en UTC | serveur Axi à **UTC+3** : l'expiration lui paraît passée | `retcode 10022` (INVALID_EXPIRATION), 15 refus sur 15 |

   Après correction : **limites posées naturellement**, TTL respecté, aucune
   modification de prix, de taille ni de garde.

2. **Le contexte d'ouverture était perdu silencieusement.** `_stratification`
   produisait `candle_source`, que `TrackedState` refusait ; le `TypeError`
   était avalé par un `except` d'observabilité. `results/pending_limits.json`
   n'était jamais écrit. Un trade clos sans contexte est *définitivement*
   inexploitable pour la mesure d'edge.

   `candle_source` traverse désormais `TrackedState` → `pending_limits.json` →
   `ClosedTrade`, et `tests/test_contexte_ouverture_contrat.py` verrouille le
   contrat entre `live_demo` et `TrackedState` dans les deux sens. Test vérifié
   discriminant : retirer le champ fait échouer les trois assertions.

   Preuve runtime :

   ```json
   "context_key": "JPN225|long|continuation|3p", "quorum": 2,
   "support_pillars": 2, "spread_r": 0.1191, "candle_source": "displacement"
   ```

3. Le secours displacement de Claude est en production (G5 11,9 % → 23,7 %,
   quorum 19,3 % → 24,4 %, A/B sur barres identiques). L'audit Hermes est rendu.

## Ce qui n'est PAS prouvé

- **Le fill.** Une limite posée n'est pas une limite remplie. L'adoption du
  contexte par `reconcile_pending_contexts` et la clôture journalisée restent à
  observer.
- **L'économie de spread.** Aucun `saving_r` réalisé n'existe : seul
  `limites_placees` est compté. C'est la mesure que réclame Hermes.
- **L'edge.** 29 clôtures, −0,3339 R/trade, PF 0,436, coûts exacts 0/29.
  Promotion fermée. Rien de ce qui précède ne change ce verdict.

## Note de méthode

Les compteurs cumulés d'avant 13:41 appartiennent à une époque de code
différente et ne doivent pas être agrégés avec les nouveaux. Trois
rechargements ont eu lieu aujourd'hui ; chacun ouvre une époque.

Le journal des trades, lui, est continu : les 29 clôtures restent comparables,
aucune n'a été produite par le chemin limite (qui n'a jamais envoyé un ordre).
