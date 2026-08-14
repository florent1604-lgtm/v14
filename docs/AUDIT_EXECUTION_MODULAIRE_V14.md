# Audit préalable — couche d'exécution simulée V14

Date : 2026-08-14

## État observé avant implémentation

- Révision : `b9d2e07`.
- Langage/frameworks : Python 3.10+, pandas, pytest, Backtrader/TradingAgents ;
  MetaTrader5 est une dépendance Windows optionnelle.
- Entrée opérationnelle : `tools/live_demo.py`; alpha : features puis porte de
  confluence ; risque : `titanium/risk/riskgate.py`; gestion de positions :
  `titanium/execution/position_manager.py`.
- Ordres existants : marché et limite MT5, avec sens `-1/+1`, idempotence,
  stop-loss/take-profit et gestion breakeven/trailing.
- Données disponibles au runtime : OHLCV multi-timeframe, tick bid/ask L1 et
  spécifications symbole. Aucun historique L2/L3 canonique n'est persisté.
- Coûts : spread estimé avant ordre, commission/swap/fee issus des deals clos,
  slippage observable par rapprochement ; funding/emprunt non disponible comme
  série autonome.
- Résultats : fichiers JSON/NDJSON append-only sous `results/`; configuration
  live issue de l'environnement et `ExecutionPolicy.from_config`, sans lecture
  de `.env` dans cette mission.
- Broker : MT5. Le rejeu historique est séquentiel ; les simulations nouvelles
  peuvent être parallélisées si chaque run conserve son propre état.
- Suite complète : `1811 passed, 2 skipped, 69 subtests passed` en 128,27 s.
- Le rejeu existant (`titanium.backtest.rejouer`) modélise une entrée au marché,
  un spread fixe et une sortie OHLCV conservatrice. Il ne possède ni OMS, ni
  ordres partiels, ni file d'attente, ni politique d'exécution interchangeable.
- Le chemin limite existant (`titanium.execution.limit_orders`) est un exécuteur
  MT5 DEMO protégé ; il ne doit pas devenir une dépendance du simulateur.
- GitNexus classe une modification de `rejouer` **CRITICAL** : 6 appelants
  directs, 17 symboles et 8 flux affectés. La solution retenue est donc additive.

## Baseline historique figé

- `run_id` : `5e5d19719c358317`
- seed : `14082026`
- moteur : `titanium.backtest.rejouer@b9d2e07`
- données : `mt5:XAUUSD:M15:791bd329fe141bf5:H4:ececc459cf57b436`
- configuration : M15/H4, 1499/499 barres closes, spread 0,16, pas 5,
  SL 1,5 ATR, R:R 2,0.
- avec coût : 46 trades, espérance +0,1468 R, PF 1,412, coût moyen
  0,0145 R ; segments +0,0735 / +0,1310 / +0,2303 R.
- sans coût : 50 trades, espérance +0,2680 R, PF 1,939.

Ce témoin est une preuve de non-régression, pas une preuve de rentabilité : les
données sont un instantané MT5 et le modèle reste OHLCV.

## Architecture cible et frontières

Le nouveau package `titanium.execution_sim` séparera :

1. `Strategy/Alpha` : produit uniquement une intention cible ;
2. `RiskEngine` : plafonne quantité, exposition et reduce-only ;
3. `ExecutionPolicy` : transforme l'intention en ordres ;
4. `MatchingSimulator` : simule carnet/quotes/OHLCV et coûts ;
5. `OMS` : états, idempotence, annulation, remplacement et fills ;
6. `Portfolio` : positions, cash et PnL ;
7. `Metrics` : performance, exécution, robustesse et classement.

Le package n'importera ni MetaTrader5, ni `mt5_executor`, ni `.env`. Sa
configuration contiendra obligatoirement `execution.live_enabled: false` et le
chargeur refusera toute valeur vraie. Le CLI ne proposera aucune option live.

## Fidélité annoncée

- Avec quotes/L1 : touch/fill déterministe avec latence, volume disponible,
  participation et slippage configurés.
- Avec OHLCV seul : chemin intrabarre conservateur et explicite, spread et
  profondeur synthétiques ; aucune prétention de reproduire une priorité FIFO
  ou un carnet L2 réel.
- Les techniques dépendantes du carnet (post-only, peg, iceberg, market making,
  simultanéité multi-jambes) seront signalées comme approximations lorsque les
  données ne contiennent pas la profondeur nécessaire.

## Risques principaux

- biais de remplissage et d'ordre intrabarre sur OHLCV ;
- avantage artificiel des limites si la file n'est pas pénalisée ;
- comparaison trompeuse si les politiques ne reçoivent pas les mêmes scénarios ;
- sur-ajustement du classement sur un seul régime ;
- explosion combinatoire des grilles.

Les contre-mesures seront : scénarios identiques et seeds fixes, modèle de file
conservateur, coûts complets, quotas de participation, walk-forward, tests de
stress, exports avec version des données/config et classement Pareto séparé du
classement de compatibilité.
