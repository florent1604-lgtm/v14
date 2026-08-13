# Codex — couverture journal/MT5 et ticket NAS100 89506157

Date de contrôle : 2026-08-13. Périmètre : compte MT5 **DEMO** uniquement,
lecture de l'historique et observabilité. Aucun ordre, seuil, configuration ou
service n'a été modifié ou redémarré.

## Résultat

Le ticket `89506157` n'est pas une clôture fabriquée. L'historique DEMO rend
désormais les deux deals cohérents :

- entrée NAS100.fs, position `89506157`, 0,04 lot à `29835.25` ;
- sortie SL, deal `70629009`, ordre `89765118`, à `29870.40` ;
- profit brut/net `+24.40 EUR` ;
- journal edge : risque `44.46 EUR`, `pnl_r=0.5488`, clôture
  `2026-08-13T06:01:45+00:00`.

Le calcul est exact à l'arrondi : `24.40 / 44.46 = 0.5488R`. La fonction de
lecture du gestionnaire retourne maintenant `(29870.4,
2026-08-13T06:01:45+00:00, 0.0, 24.4)`.

## Cause de l'invisibilité temporaire

Le timestamp brut du deal de sortie est `2026-08-13T09:01:45+00:00`, soit
l'heure serveur Axi encodée avec **+3 h**. Une requête d'historique terminée à
`now UTC` ne voit donc pas les trois dernières heures de deals. Le gestionnaire
de clôture était déjà protégé par une marge de +1 jour ; la récupération
générale et son heartbeat ne l'étaient pas.

Correction préparée :

- requête d'historique élargie symétriquement ;
- mesure du décalage serveur avec les ticks disponibles ;
- filtrage final sur la fenêtre UTC réelle de 7 jours ;
- nouvelles lignes de récupération écrites en vrai UTC ;
- publication heartbeat de `mt5_closed`, `journal_edge`, `missing_in_edge`,
  `missing_in_edge_rate`, fenêtre et motif d'indisponibilité.
- séparation du verdict de rapprochement en `accounting_ok` et `edge_ok` ;
  l'ancien `ok` devient l'alias conservateur de `edge_ok`.

La revue indépendante Hermes a reproduit le cas dangereux « 1 clôture MT5,
0 edge, 1 rejet » : l'ancien rapport rendait `ok=True`. Après correction, ce
cas rend `accounting_ok=True`, `edge_ok=False`, `ok=False`.

Son addendum a également été appliqué avant handoff :

- `_filled_order_snapshot` exige maintenant une identité exacte ordre
  (ticket, magic, symbole, côté limite, volume) **et** les deals IN liés au
  même ordre/position ; le prix de fill vient de la moyenne pondérée des deals,
  pas de `price_current` seul ;
- plusieurs deals OUT sont triés par `time_msc/time` avant de retenir la
  dernière sortie chronologique.

Preuve réelle DEMO sur l'ordre NAS100 `89506157` après durcissement : position
`89506157`, fill `29835.25`, SL `29771.21`, TP `29963.32`, état broker FILLED.

## Mesure DEMO après correction

Contrôle réel idempotent :

- `mt5_closed=102` ;
- `journal_edge=47` ;
- `missing_in_edge=55` ;
- `missing_in_edge_rate=0.539216` (53,92 %) ;
- `recovered=0`, car les 55 lignes sont déjà comptabilisées hors edge ;
- empreintes SHA-256 de `trades.ndjson` et `journal_rejets.ndjson` inchangées.

Ce taux ne signifie pas 53,92 % de pertes de données comptables : les 55
clôtures sont conservées dans `journal_rejets.ndjson`, mais exclues de l'edge
car leur contexte n'a jamais été observé. La hausse future du nombre absolu ou
du taux reste une régression de capture à surveiller.

## Validation

- 83 tests du chemin exécution/réconciliation : PASS ;
- 30 tests identité fill/tri historique : PASS ;
- Ruff critique : PASS ;
- suite complète : 1 721 PASS, 2 SKIP, 1 échec **extérieur au lot** ;
- échec partagé : 49 skills présents, test du catalogue encore figé à 48.
- suite globale hors ce seul fichier partagé incohérent : 1 724 PASS, 2 SKIP.

## Alertes et suites recommandées

1. Les 55 anciennes lignes récupérées ont été écrites par la version
   précédente avec `horloge=utc` alors que leurs timestamps provenaient encore
   de l'heure serveur. Ne pas réécrire l'append-only ; prévoir un manifeste de
   correction/compatibilité pour toute analyse temporelle de ces lignes.
2. Le ratio brut restera structurellement élevé tant que l'historique legacy
   reste dans la fenêtre. Ajouter ensuite un compteur par `runtime_epoch_id` ou
   au minimum une base de référence pour distinguer héritage et nouvelle
   régression.
3. Le scan complet de 7 jours tourne à chaque passage de 60 s. Après validation
   fonctionnelle, mesurer sa durée et envisager un curseur incrémental sans
   sacrifier le rattrapage après arrêt.
4. Corriger le test du catalogue de skills seulement après intégration du
   nouveau skill par Prime/son auteur.

Le lot attend la revue et le commit de Prime. Le heartbeat neuf n'apparaîtra
dans l'interface qu'après ce commit et un rechargement contrôlé des services.
