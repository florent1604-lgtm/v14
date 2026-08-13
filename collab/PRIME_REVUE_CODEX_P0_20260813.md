# Revue Prime — P0 `cb599499` (Codex) : récupération des clôtures invisibles

**Verdict : RENVOYÉ EN REVUE.** Un point bloquant, prouvé par reproduction. Le reste
du lot est bon et sera intégré tel quel une fois ce point corrigé.

## Ce qui est juste et que je garde

- `titanium/execution/history_recovery.py` : borné à 7 jours, attribution par magic,
  append-only, idempotent (tickets connus lus dans `trades.ndjson` **et**
  `journal_rejets.ndjson`), tickets suivis protégés. La règle
  `coverage_only=true` / `edge_eligible=false` est exactement la bonne : on conserve la
  preuve comptable sans fabriquer un `pnl_r` ni des piliers qui n'ont jamais été perçus.
- `pending_context._filled_order_snapshot` : récupérer un fill via `history_orders_get`
  en état FILLED **avec** `position_id` est la seule preuve solide quand l'ordre a
  disparu entre deux tours. Ce chemin-là, lui, restitue le contexte, donc l'edge.
- `reconcile()` qui sépare `matched` (mesurable) de `accounted` (couvert).
- Preuve DEMO cohérente avec la mienne : 55 récupérées, dont EURAUD `89198681`.

## Point bloquant — une latence d'historique détruit un trade mesurable

`_cloture_depuis_historique` rend désormais `None` quand aucun deal de sortie n'est
trouvé. Le commentaire annonce : « L'état reste alors disponible pour un nouvel essai
au tour suivant. » **C'est faux**, et je l'ai reproduit sur une position au contexte
complet dont l'historique ne contient encore que le deal d'entrée :

```
details: ['#1 clôturé — ÉCHEC journal',
          '#1 rejet définitif mis en quarantaine: PNL_NET_INCONNU']
etat restant: []
trades:  (aucun)
rejets:  {"ticket": "live:1", "reason": "PNL_NET_INCONNU",
          "context": "EURUSD|long|continuation|3p", ...}
```

La chaîne est mécanique : pas de deal de sortie → `historique_exact=False` →
`net_devise=None` → `journaliser_cloture` refuse avec `PNL_NET_INCONNU` →
ce motif appartient à `MOTIF_REFUS_DEFINITIF` → quarantaine immédiate et **purge de
l'état**. Le contexte d'entrée, seul instant où résultat et perception coexistent, est
détruit. Et comme le ticket entre dans `journal_rejets.ndjson`, `history_recovery` ne
le reprendra jamais (il est « connu ») et `reconcile` le comptera `accounted`.

Une lenteur de synchronisation MT5 de quelques secondes suffit donc à transformer un
trade parfaitement mesurable en observation sans contexte. C'est l'inverse de
l'objectif du lot : la direction est juste — ne jamais fabriquer un prix de sortie à
partir du deal d'entrée, l'ancien `max(deals)` écrivait un faux zéro — mais la
classification du refus est fausse.

### Correction demandée

1. Distinguer le motif : `SORTIE_INTROUVABLE`, **absent** de `MOTIF_REFUS_DEFINITIF`.
2. Conserver l'état et réessayer au tour suivant.
3. Escalader en quarantaine seulement après une borne explicite (âge de la disparition,
   par exemple 15 minutes ou N tours), et journaliser l'escalade avec le nombre d'essais.
4. Un test qui prouve les deux branches : retry conservé au premier tour, escalade au-delà
   de la borne.

## Deux remarques non bloquantes

**`ok` ne doit pas devenir aveugle.** `missing` exclut désormais les tickets mis en
quarantaine, donc une clôture invisible cesse d'allumer l'alarme dès qu'elle est
récupérée. `missing_in_edge` est publié mais ne pilote rien. La quarantaine est une
résolution comptable, pas une résolution de mesure : je veux le taux
`missing_in_edge / mt5_closed` dans le heartbeat, et une hausse traitée comme une
régression.

**L'orphelin `89506157` n'est pas hors fenêtre.** Le rapport porte
`until = 06:10:46Z` et ce ticket est clôturé à `06:01:45Z`, donc dedans. La cause est
ailleurs — très probablement `open_position_ids`, construit depuis un instantané de
`positions.json` qui le listait encore ouvert, ou la même latence d'historique. À
expliquer avant de clore : un rapport qui vire au rouge sur un artefact de bord
apprend à l'équipe à ignorer le rouge.

## Coût d'exécution

`recover_unobserved_closures` tourne à **chaque** tour de 60 s : `history_deals_get`
sur 7 jours plus relecture intégrale de deux NDJSON. Acceptable aujourd'hui (55 lignes),
à surveiller quand le journal grossira. Un cache d'empreinte ou une fenêtre glissante
courte pour le cas nominal suffirait.
