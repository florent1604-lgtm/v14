# Résultat — matrice d'exécution V14

Date : 2026-08-14
Run : `9cb2fb7c6175484c`
Empreinte moteur : `59427dd10480fc16`
Mode : **backtest/dry-run**, `execution.live_enabled=false`

## Preuve exécutée

- 15 politiques ;
- 864 scénarios identiques par politique ;
- 12 960 cas au total ;
- 4 320 cas développement, 4 320 validation, 4 320 final OOS ;
- seed `14082026` ;
- 0 NaN/infini, 0 ratio hors borne, 0 coût total négatif.

Artefacts détaillés locaux :

- `results/execution_matrix_full/execution_matrix.csv` ;
- `results/execution_matrix_full/execution_matrix.json` ;
- `results/execution_matrix_full/execution_matrix.md`.

## Classement performance robuste synthétique

| rang | politique | score | P&L net moyen | fill | fidélité |
|---:|---|---:|---:|---:|---:|
| 1 | cancel_replace | 1,6853 | 1,2145 | 99,9 % | 0,50 |
| 2 | pegged | 1,6250 | 1,2827 | 89,2 % | 0,50 |
| 3 | limit_passive | 1,5520 | 1,1955 | 80,1 % | 0,60 |
| 4 | post_only | 1,4748 | 1,1793 | 74,5 % | 0,60 |
| 5 | iceberg | 1,4614 | 1,1941 | 80,1 % | 0,45 |
| 6 | market | 1,4255 | 1,0459 | 100,0 % | 0,90 |
| 7 | adaptive | 1,3838 | 1,1731 | 78,9 % | 0,55 |
| 8 | vwap | 0,6976 | 0,6259 | 58,2 % | 0,65 |
| 9 | maker_then_hedge_taker | 0,5916 | 0,9900 | 74,5 % | 0,35 |
| 10 | ioc | 0,5792 | 0,4180 | 52,1 % | 0,75 |
| 11 | fok | 0,5077 | 0,3444 | 47,3 % | 0,75 |
| 12 | pov | 0,3961 | 0,2594 | 43,4 % | 0,60 |
| 13 | twap | 0,0929 | 0,2308 | 36,1 % | 0,70 |
| 14 | market_making | -0,1234 | 0,0392 | 51,4 % | 0,35 |
| 15 | multi_leg_simultaneous | -2,3513 | -0,9579 | 90,5 % | 0,40 |

Le score pénalise drawdown, dégradation OOS, concentration temporelle, faible
échantillon, optimisme du modèle de fill, sensibilité aux scénarios,
complexité, exposition résiduelle et latence.

## Classement compatibilité V14

1. market — 92,88 ;
2. limit_passive — 73,33 ;
3. post_only — 71,78 ;
4. IOC — 71,36 ;
5. cancel_replace — 71,30 ;
6. FOK — 70,00 ;
7. pegged — 67,93 ;
8. adaptive — 62,68 ;
9. VWAP — 61,69 ;
10. iceberg — 61,35 ;
11. TWAP — 58,18 ;
12. POV — 56,35 ;
13. multi-leg simultané — 47,44 ;
14. maker puis hedge taker — 44,55 ;
15. market making — 40,57.

Front de Pareto synthétique : adaptive, cancel/replace, iceberg, limite
passive, market, pegged, post-only, POV et TWAP.

## Verdict

`market` reste le témoin le plus compatible et le plus fidèle aux données
actuelles. Le trio à mesurer ensuite sur des quotes broker archivées est :

1. market (baseline) ;
2. limit passive (première alternative à faible complexité) ;
3. adaptive maker→taker (candidat shadow, pas promotion).

La première place synthétique de cancel/replace ne prouve pas une rentabilité :
sa fidélité n'est que 0,50 et dépend fortement du modèle de file/replacement.
Peg, iceberg, market making et multi-jambes restent **data-gated** jusqu'à
disposer de trades/L2 synchronisés et de statuts broker horodatés.

Cette matrice distingue l'alpha (même intention et même prix d'arrivée pour
toutes les politiques) du delta d'exécution. Elle ne prouve aucun edge réel et
n'autorise aucun passage live.

## Suite — 15/08/2026

Le tiers hors échantillon annoncé « rapporté séparément » ci-dessus ne l'avait
jamais été. Il l'est maintenant, avec la comparaison appariée contre `market`,
la conditionnalité par régime et une réserve qui change la lecture du classement
(les membres de la famille passive sont identiques dans 80 à 98 % des
scénarios) : **`docs/RAPPORT_BACKTEST_15_POLITIQUES.md`**.

Reproduction : `python tools/rapport_politiques_execution.py`.
