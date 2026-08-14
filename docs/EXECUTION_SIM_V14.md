# Couche d'exécution simulée V14

## Sécurité et périmètre

`titanium.execution_sim` est un laboratoire déterministe, sans adaptateur
broker. Il n'importe ni MetaTrader5, ni `titanium.execution.mt5_executor`, ne
lit pas `.env` et ne possède aucune fonction d'envoi réseau. Le chargeur refuse
toute configuration où `execution.live_enabled` n'est pas exactement `false`.

Le module ne change ni les signaux, ni les stops, ni les paramètres du moteur
DEMO. Une intention alpha contient seulement symbole, sens et quantité ; la
politique choisit les ordres, le risque les borne, le matching les remplit,
l'OMS conserve leur historique et le portefeuille comptabilise les fills.

## Commandes

Validation rapide des quinze politiques :

```powershell
.venv\Scripts\python.exe tools\backtest_execution_matrix.py `
  --config config\execution_backtest.json `
  --output results\execution_matrix_quick `
  --policy all --quick --seed 14082026 --jobs 4
```

Une seule politique :

```powershell
.venv\Scripts\python.exe tools\backtest_execution_matrix.py `
  --policy adaptive --quick --output results\execution_adaptive
```

Matrice complète : supprimer `--quick`. Elle couvre 864 scénarios par
politique (288 combinaisons dans chacun des splits développement, validation
et final hors échantillon). Les workers ne partagent aucun OMS ; un seul
écrivain produit les rapports après collecte ordonnée des résultats.

## Architecture

- `models.py` : intentions, quotes/barres, ordres, fills, états et événements ;
- `risk.py` : taille, inventaire, reduce-only et kill switch ;
- `policies.py` : registre des quinze techniques ;
- `matching.py` : profondeur, limites, file, partiels, IOC/FOK et frais ;
- `oms.py` : idempotence, transitions, cancel race et replacement ;
- `portfolio.py` : positions, prix moyen, cash, frais et P&L réalisé ;
- `metrics.py` : performance, coûts, qualité, sélection adverse, multi-jambes ;
- `runner.py` : scénarios communs, parallélisation, classements et exports.

## Politiques disponibles

`market`, `limit_passive`, `post_only`, `ioc`, `fok`, `cancel_replace`,
`pegged`, `iceberg`, `twap`, `vwap`, `pov`, `adaptive`, `market_making`,
`multi_leg_simultaneous`, `maker_then_hedge_taker`.

Le VWAP ne reçoit que les volumes antérieurs fournis au `PolicyContext`. Le
POV déduplique les identifiants d'événement. Le hedge maker→taker est créé sur
la quantité effectivement remplie, jamais sur la quantité parent. Toute
modification/recharge d'iceberg perd la priorité par défaut.

## Paramètres

Le fichier [execution_backtest.json](../config/execution_backtest.json) donne
les valeurs prudentes exécutables.

### `execution`

- `enabled` : active le simulateur ; doit valoir `true` pour lancer un run ;
- `live_enabled` : verrou absolu, doit rester `false` ;
- `policy` / `policies` : choix par défaut et catalogue autorisé ;
- `fees.maker_bps`, `fees.taker_bps` : frais par fill ;
- `latency.*_ms` : données, décision, soumission, ACK, cancel et hedge ;
- `spread.source`, `fallback_bps` : L1 disponible ou spread synthétique ;
- `slippage` : base, multiplicateur de volatilité et participation ;
- `passive.offset_ticks` : distance au meilleur prix ;
- `passive.max_age_ms` : expiration ;
- `passive.refresh_threshold_ticks` : déplacement déclenchant un refresh ;
- `passive.queue_model` : `pessimistic`, `central` (défaut) ou `optimistic` ;
- `post_only.cross_behavior` : `reject` ou `slide_to_passive` ;
- `iceberg.visible_ratio`, `replenish_delay_ms` : tranche visible/recharge ;
- `twap.duration_seconds`, `slices`, `catchup_policy` : `cancel`, `aggressive`
  ou `carry` ;
- `vwap.profile_lookback_sessions`, `max_participation`, `catchup_policy` :
  profil passé et plafond de volume ;
- `pov.participation_rate`, `min_slice`, `max_slice` : participation observée ;
- `adaptive.deadline_seconds`, `stages`, `max_reprices` : escalade d'urgence ;
- `market_making.*` : inventaire, skew, tailles/niveaux, refresh, garde de
  volatilité et spread net minimal ;
- `multi_leg.*` : TIF, ratio/symbole de hedge, minimum, délai, slippage,
  exposition résiduelle et action d'urgence.

### `risk`

Les plafonds portent sur la taille d'ordre, exposition brute/nette,
inventaire, ordres ouverts, perte quotidienne, drawdown, slippage, déséquilibre
des jambes et délai de hedge. `kill_switch=true` bloque les nouveaux ordres et
annule tous ceux qui restent ouverts dans l'OMS simulé.

## Modèle de données et fidélité

Le runner livré génère des observations L1 déterministes avec deux niveaux de
profondeur, un chemin OHLCV et un volume. C'est une matrice de stress contrôlée,
pas une preuve de microstructure réelle.

- Marché/IOC/FOK : bonne cohérence fonctionnelle avec L1/profondeur, mais
  profondeur synthétique.
- Limites/post-only/cancel/peg/TWAP/VWAP/POV : approximation centrale ; un
  contact ne remplit jamais automatiquement et la file consomme du volume.
- Iceberg, market making et multi-jambes : fidélité faible sans trades/L2
  synchronisés, séquences d'ACK/cancel broker et horloges multi-marchés.

Une validation avant toute décision économique demanderait : trades avec
agresseur, snapshots L2/L3, identifiants de séquence, statuts broker, horloges
nanoseconde/milliseconde synchronisées et barème exact des frais/funding.

## Résultats et reproduction

Chaque lancement écrit :

- `execution_matrix.csv` : une ligne politique × scénario ;
- `execution_matrix.json` : config, version moteur, `run_id`, lignes,
  classements et Pareto ;
- `execution_matrix.md` : synthèse lisible et limites.

Le `run_id` est le SHA-256 tronqué de la configuration, de la seed, des
politiques et de la version du moteur. À config et seed identiques, les lignes
fonctionnelles sont identiques même avec `--jobs 1` ou `--jobs 4`. Les temps
muraux sont volontairement exclus des hashes et neutralisés dans les lignes.

Le split `final_oos` porte toujours
`parameter_selection_eligible=false`. Il ne doit servir ni à choisir une
politique ni à régler ses paramètres.
