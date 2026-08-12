# Horloge du vendeur de données — la racine des trois défauts du jour

**12/08/2026** · Prime · lecture puis correctif, suite complète 1678 passés.

## Le défaut

`get_rates` étiquetait `UTC` un index qui portait l'heure du **serveur**.
Vérification directe, avant correctif :

```
dernières barres M15 : 2026-08-12 18:45:00+00:00
maintenant (vrai UTC) : 2026-08-12 16:03:59+00:00
```

Trois heures d'avance, annoncées comme de l'UTC. Les trois pannes de la
journée sont la même panne :

| symptôme | conséquence |
|---|---|
| expiration d'ordre calculée en UTC | `retcode 10022` — **zéro limite posée** depuis la mise en production |
| `deal.time` étiqueté UTC | 37 clôtures datées 3 h dans le futur, durées de détention gonglées (7 min journalisées 187) |
| index des barres étiqueté UTC | croisement barres ↔ journal décalé : **12 trades sur 37** écartés du rejeu faute d'intersection |

Aucune décision de production ne lit l'heure absolue d'une barre — seul l'ordre
des barres compte, et `_weekend_block` utilise l'horloge système. C'est
précisément pourquoi le défaut a survécu : il ne cassait rien, il **faussait
les mesures**.

## Le correctif

Une seule mesure d'horloge, partagée : `decalage_serveur` (par symbole) et
`decalage_serveur_cache` (mesure au plus une fois par quart d'heure, sur un
panier de symboles qui cotent presque toujours). `get_rates` publie désormais
un index en vrai UTC. Dix tests couvrent la mesure, son cache, le refus d'une
mesure aberrante, et le cas du terminal muet — où l'index reste **non corrigé**
plutôt que corrigé au hasard.

## Ce que le correctif a révélé, et qui invalide une conclusion

Le rejeu barre à barre de Claude (`tools/rejeu_breakeven.py`) a été relancé sur
des barres désormais justes :

| | avant correctif | après correctif |
|---|---|---|
| trades rejoués | 25 / 37 | 27 / 37 |
| fidélité à 0,80 (rejeu − réel) | +0,105 R | **+0,32 R** |
| gagnants coupés à BE 0,30 | 1 | **2** |

**L'écart de fidélité a triplé, et il vaut maintenant trois fois le gain
mesuré** (+0,11 R entre 0,80 et 0,30). La cause est identifiée : les 37 lignes
du journal portent un `closed_at` en heure serveur, alors que les barres sont
maintenant en vrai UTC. La fenêtre de rejeu s'étend donc trois heures au-delà
de la sortie réelle, et un trade réellement stoppé peut, dans le rejeu,
survivre jusqu'au trailing. Le rejeu est optimiste par construction.

Avant le correctif, journal et barres étaient faux **ensemble** : la cohérence
était accidentelle. C'est le cas le plus dangereux — deux erreurs qui
s'annulent produisent un résultat crédible et faux.

**Conséquence : la recommandation « armer le breakeven à 0,30 R » n'est pas
utilisable en l'état.** Sa direction reste plausible (les deux bornes
intra-barre s'accordent), mais son amplitude est noyée dans un biais trois fois
plus grand.

## Ce qui est fait pour que ça ne se reproduise pas

`ClosedTrade` porte désormais `horloge` : `"utc"` pour les lignes écrites après
le correctif, `""` pour les anciennes. Un outil peut refuser ce qu'il ne peut
pas interpréter, au lieu de croiser en silence deux horloges différentes. Le
journal est append-only : les 37 lignes fausses restent, marquées comme telles.

## Ce qui reste à faire

1. Le rejeu doit ignorer `ts_exit` quand `horloge != "utc"`, ou borner la
   fenêtre autrement. Sans ça, sa fidélité restera fausse.
2. Le seuil de breakeven ne doit pas bouger avant que la fidélité du rejeu soit
   du même ordre que l'effet mesuré.
3. Toute analyse temporelle antérieure au 12/08/2026 est à refaire.
