# Evaluation L1 passive — contrat de mesure

Ce chantier sépare strictement trois faits qui étaient auparavant mélangés :

1. **contact de prix observé** : achat si `ask <= limite`, vente si `bid >= limite` ;
2. **franchissement observé** : achat si `ask < limite`, vente si `bid > limite` ;
3. **service passif** : **non observable** dans l'archive L1 actuelle.

L'archive porte `bid`, `ask`, `spread`, `last` et `volume`, mais ni profondeur,
ni priorité de file, ni transaction séquencée, ni côté agresseur. Le rapport
laisse donc `service = null` et `taux_service = null`. Aucun modèle de file ou
fill synthétique n'est utilisé.

## Porte de couverture

Les TTL 120, 300 et 600 secondes sont évalués séparément sur l'intervalle
causal `[arrivée, expiration)`. Une fenêtre est retenue uniquement si :

- la première quote après la décision arrive avant `max_gap_ms` ;
- une quote postérieure ferme explicitement l'expiration ;
- aucun intervalle entre observations, y compris celui qui traverse
  l'expiration, ne dépasse `max_gap_ms` ;
- toutes les observations précèdent le cutoff UTC scellé.

Cette porte est volontairement conservatrice : le collecteur enregistre les
changements de quotes, pas un heartbeat distinct. Un long silence peut être un
prix immobile ou une collecte interrompue ; sans preuve, la fenêtre est rejetée.

## Reproductibilité

`tools/evalue_l1_passif.py` scelle dans chaque rapport :

- cutoff UTC, seuil de trou et TTL ;
- SHA-256 du préfixe exact de chaque fichier quote consommé ;
- manifestes, trades et résumés des rejeux validés ;
- empreinte de l'époque du moteur de rejeu ;
- SHA-256 de l'outil et de la source unique de prix `limit_pricing.py` ;
- NDJSON détaillé, nombre de lignes, octets et SHA-256 ;
- SHA-256 canonique du rapport final.

Commande de référence, PAPER/DEMO uniquement :

```powershell
.venv\Scripts\python.exe tools\evalue_l1_passif.py `
  --cutoff 2026-08-20T00:00:00Z `
  --max-gap-ms 5000
```

Le cutoff du 20 août couvre les décisions chevauchant les premières archives
sans dépendre des fichiers encore alimentés les jours suivants. Aucun résultat
de ce banc ne peut promouvoir une politique : il mesure un chemin de prix, pas
une exécution.

## Mesure scellée du 24 août

Paramètres : cutoff `2026-08-20T00:00:00Z`, trou maximal strict `5 000 ms`.
Le banc a trouvé 952 décisions candidates sur 136 symboles possédant à la fois
un rejeu valide et une période L1 potentiellement chevauchante. Sept symboles
du rejeu n'ont aucune archive quotes correspondante.

| Politique | TTL | Couverture stricte | Contacts | Taux contact | Franchissements |
|---|---:|---:|---:|---:|---:|
| best_passive | 120 s | 211 | 115 | 54,50 % | 112 |
| best_passive | 300 s | 152 | 109 | 71,71 % | 107 |
| best_passive | 600 s | 122 | 97 | 79,51 % | 97 |
| v14_live | 120 s | 211 | 101 | 47,87 % | 100 |
| v14_live | 300 s | 152 | 99 | 65,13 % | 98 |
| v14_live | 600 s | 122 | 93 | 76,23 % | 93 |

La baisse du nombre de fenêtres quand le TTL augmente est un effet de la porte
de continuité stricte : une fenêtre plus longue a davantage de chances de
rencontrer un silence supérieur à cinq secondes. Les taux entre TTL ne doivent
donc pas être lus comme une courbe causale sur une cohorte identique.

Preuves de scellement vérifiées indépendamment :

- `snapshot_id` : `ebd7d839e0793e7f8895588d2cdcf6773bf6fbbe1a4519952d0e1fe80ac41d4c` ;
- manifeste du rapport : `6f69fbc1e92a4943ef00a606ed22b3106260336c762d123ebd2f37085d280f4e` ;
- 5 712 lignes détaillées, 3 474 028 octets ;
- 150 préfixes de fichiers quotes et 136 artefacts de rejeu inclus ;
- 76 symboles disposent d'au moins une fenêtre strictement couverte ;
- scellement du rapport et SHA-256 du NDJSON revérifiés : valides.

Conclusion : le L1 montre que le prix `v14_live`, plus passif, est moins souvent
contacté que le meilleur bid/ask sur les fenêtres observables. Il ne montre pas
qu'un ordre aurait été servi, ni que l'écart de contact améliorerait le PnL.
