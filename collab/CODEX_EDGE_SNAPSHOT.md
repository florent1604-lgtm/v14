# Snapshot edge net V14 — Codex

Date : 2026-08-12, 16:40 Paris
Périmètre : collecte DEMO/PAPER uniquement, sans ordre forcé ni modification des gardes.

## Verdict

**V14 n'a toujours pas démontré de rentabilité.** La revue Prime porte sur 35
clôtures : espérance nette **-0,3834 R/trade**, profit factor **0,361**, et
**0/35** décomposition de coûts exacte. Le contexte le plus fourni reste à
**3/20 clôtures**. La promotion en réel demeure fermée.

## Premier fill naturel d'une limite

Le cycle instrumenté a maintenant produit sa première preuve dynamique après
rechargement du moteur :

- **2** limites placées naturellement ;
- **1** expirée : `GBPNOK` ;
- **1** remplie : `ETHUSD`, ordre/position `89325926` ;
- fill-rate provisoire : **50 %** — échantillon beaucoup trop petit pour conclure ;
- prix marché de référence : **1896,26** ;
- prix limite planifié : **1894,467** ;
- prix de fill : **1894,4** ;
- économie réalisée reconstruite : **+0,1322 R** ;
- slippage vs prix planifié : **-0,0048 R**, donc légèrement favorable.

La position ETHUSD était encore ouverte au contrôle en lecture seule, avec le
magic V14 `14000`. Aucune clôture limite et donc aucun `pnl_r` final ne sont
encore disponibles.

## Défaut détecté et correctif prêt

La première adoption réelle a révélé que les champs de provenance de la limite
avaient été ajoutés au chemin des ordres marché (`_attacher_contexte`) au lieu
du chemin pending (`_memoriser_contexte_limit`). Conséquence : le fill était
bien reconnu dans le journal append-only, mais `positions.json` conservait un
ticket limite nul et ne pouvait pas relier proprement la future clôture.

Le lot Codex corrige ce câblage et ajoute une réparation idempotente :

1. les nouveaux ordres limites enregistrent ticket, prix planifié, référence
   marché et économie cible dans le contexte pending ;
2. la réconciliation relit les événements immuables `placed` + `filled` ;
3. si la position adoptée existe mais a perdu sa provenance, seuls les champs
   limite manquants sont reconstruits — aucune donnée de gestion, SL, TP,
   risque ou phase n'est modifiée ;
4. un événement `filled_metrics` append-only conserve l'économie et le
   slippage réparés sans doubler le dénominateur du fill-rate ;
5. le chemin marché reste explicitement neutre sur ces champs.

Après revue, commit et rechargement contrôlé par Prime, le prochain tour pourra
réparer la position ETHUSD ouverte et permettre à sa clôture future de produire
le lien ordre → position → PnL net.

## État statistique et recommandations

- La priorité reste la **preuve d'edge directionnel** du signal, pas
  l'assouplissement des portes.
- Le contrefactuel favorable de breakeven à 0,30 R reste négatif
  (**-0,1746 R/trade**) : la gestion de sortie seule ne sauve pas le système.
- Les 35 horodatages historiques sont décalés de +3 h ; le correctif vaut pour
  les prochaines clôtures, mais toute analyse temporelle passée doit être
  refaite.
- Continuer la collecte sans optimisation jusqu'à au moins 20 clôtures propres
  par contexte, puis exiger 60 observations par cellule, ≥90 % de coûts exacts,
  bootstrap et validation hors échantillon avant toute promotion.
- Ne pas utiliser le fill-rate de 50 % ni l'économie de +0,1322 R comme preuve
  d'edge : ce sont respectivement 2 placements et 1 fill.

## Validation

- tests ciblés télémétrie/limites : **79 réussis, 0 échec** ;
- suite complète : **1 633 réussis, 2 ignorés, 0 échec** ;
- sous-tests : **69 réussis** ;
- lint critique Ruff (`E9,F63,F7,F82`) : **vert** ;
- impact direct pré-édition des fonctions modifiées : **LOW** ; analyse finale
  du lot partagé : **MEDIUM**, 4 flux de réconciliation affectés et couverts ;
- aucun seuil, quorum, garde, `.env` ou service modifié par Codex.

Statut : correctif prêt pour revue Prime. La tâche reste `in_progress` jusqu'à
la clôture naturelle de la première position issue d'une limite et au calcul
de son PnL net.
