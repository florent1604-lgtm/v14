# Revue des tâches et décision de mise en production — 12/08/2026

Base : `master`, suite complète **1626 passés, 2 skippés**. Compte DEMO 10055401.

## Décision

**Aucune tâche ne passe en production. Le blocage n'est pas administratif : il
est mesuré.**

Espérance live **−0,3834 R/trade** sur 35 clôtures, PF **0,361**, 0/35 coûts
exacts. Mettre en production un système dont l'espérance mesurée est négative
transforme une perte simulée en perte réelle, à la vitesse du levier. La revue
ci-dessous dit, pour chaque tâche, ce qui est **prouvé** et ce qui manque.

## Ce qui est réellement livré et vérifié

| Tâche | Livré | Preuve indépendante |
|---|---|---|
| `181aaa7c` conventions de coût | oui | convention spread unifiée backtest/live, test verrouillé |
| `46c06912` audit Hermes | oui | `collab/HERMES_AUDIT_POST_COMMIT.md`, walk-forward rejoué |
| `ba74e58a` piliers ICT | oui | A/B sur barres identiques, G5 11,9 → 23,7 % |
| `fd5be523` télémétrie EXECUTION | oui | c'est elle qui a révélé les deux pannes du jour |
| chaîne d'ordre limite | **partiel** | placement et contexte prouvés ; **fill non prouvé** |

Trois défauts trouvés aujourd'hui, tous invisibles jusqu'à ce qu'on les
cherche — ils illustrent l'écart entre « le code est écrit » et « le chemin
fonctionne » :

1. `expiration` en `datetime` → `order_send` renvoie `None`. **Zéro limite
   posée depuis la mise en production.**
2. expiration en UTC, serveur à UTC+3 → `retcode 10022`, 15 refus sur 15.
3. `deal.time` (heure serveur) étiqueté `+00:00` → les **35 clôtures du
   journal portent une heure fausse de trois heures**, et toutes les durées de
   détention sont gonflées d'autant : 7 minutes réelles journalisées 187.

Le troisième est corrigé pour les clôtures à venir. Les 35 lignes déjà écrites
gardent leur horodatage faux : le journal est append-only, et le réécrire
détruirait la seule propriété qui en fait une preuve. Toute analyse temporelle
antérieure au correctif doit être considérée comme fausse.

## Pourquoi le bot perd — mesure, pas opinion

```
sorties au stop plein : 21 / 35 (60 %), dont 11 après être passées en gain
MFE médian  +0,691 R      MAE médiane  −0,587 R
```

Le marché va presque aussi souvent contre le trade que pour lui. Répartition
par sortie :

| sortie | n | espérance |
|---|---:|---:|
| `init` (stop plein) | 21 | **−0,9689 R** |
| `breakeven` | 8 | −0,0374 R |
| `trailing` | 6 | **+1,2043 R** |

Onze trades sont montés jusqu'à +0,79 R puis sont revenus mourir au stop, sans
jamais atteindre le breakeven armé à +0,80 R. La tentation évidente est de
descendre ce seuil. `tools/contrefactuel_breakeven.py` le chiffre, en borne
**favorable** (les stops évités sont crédités, les gagnants coupés ne sont pas
débités) :

```
 BE armé à   espérance      PF  stops évités  gagnants exposés
      0.30     -0.1746   0.565             7                 9
      0.50     -0.2340   0.488             5                 9
      0.80     -0.3834   0.361             0                 9   <- actuel
```

**Même dans l'hypothèse la plus flatteuse, l'espérance reste négative.** Le
réglage de la gestion ne sauve pas ce système : le signal lui-même n'a pas
d'edge directionnel démontré. C'est le résultat le plus important du jour, et
il ferme la porte au réglage de seuils comme réponse.

Deux leviers restent, dans cet ordre :

1. **La sélectivité du signal.** 60 % de stops pleins avec un MFE médian de
   0,69 R décrit une entrée qui n'anticipe rien. Le quorum devrait plutôt
   MONTER que descendre — à vérifier en comparant l'espérance des trades à 2
   piliers (−0,4532 R, n=19) et à 3 (−0,3655 R, n=15). La pente va dans le bon
   sens, l'échantillon est trop petit pour conclure.
2. **La taille des gagnants.** Les 6 trades en trailing rapportent +1,2043 R.
   Avec 60 % de pertes pleines, il faudrait environ +1,5 R pour seulement
   revenir à zéro. Le trailing coupe trop tôt, ou le TP est mal placé.

Le FX est la classe la plus coûteuse : n=22, **−0,5460 R**, PF 0,220. Les
indices sont à −0,1409 R. Aucun échantillon n'autorise encore d'exclure une
classe, mais c'est la première chose à surveiller.

## Conditions de passage en production

Inchangées, et aucune n'est remplie aujourd'hui :

- espérance nette **positive**, borne inférieure de bootstrap > 0 ;
- au moins 60 clôtures par cellule candidate (max actuel : 3) ;
- 90 % de coûts exacts (actuel : **0 %**) ;
- fill rate et économie réalisée des limites mesurés ;
- réconciliation ticket 100 % maintenue ;
- validation hors échantillon sur MT5 natif.

La collecte DEMO continue, gardes inchangées, aucun ordre forcé.
