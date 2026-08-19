# Inventaire — infrastructure de backtest d'ENTRÉE et de winrate par actif

**Prime Agent (sous-agent lecture seule) · 19/08/2026 · racine `C:\Users\flore\Desktop\V14`**

Rapport strictement descriptif. Aucun fichier existant n'a été modifié. Les seules
écritures sont ce rapport et deux sondes d'analyse dans
`collab/prime_agent/runs/strategie-entree-20260819/sonde_lecture/`.

---

## 1. Les 15 politiques d'EXÉCUTION

### 1.1 Où elles sont définies

| élément | chemin:ligne |
|---|---|
| module des politiques | `titanium/execution_sim/policies.py` (600 lignes) |
| classe de base | `titanium/execution_sim/policies.py:20` `ExecutionPolicy` |
| contexte de marché | `titanium/execution_sim/policies.py:13` `PolicyContext` |
| **registre des 15** | `titanium/execution_sim/policies.py:574-593` `POLICY_REGISTRY` |
| fabrique | `titanium/execution_sim/policies.py:596` `get_policy(name, config)` |
| tuple ordonné | `titanium/execution_sim/runner.py:30` `ALL_POLICIES` |
| liste de config | `config/execution_backtest.json` → `execution.policies` |

⚠️ Homonymie à ne pas confondre : `titanium/execution/mt5_executor.py` définit une
**autre** classe `ExecutionPolicy` — c'est le mur d'armement MT5 (`enabled`,
`expected_demo_login`, `magic`), rien à voir avec les 15 politiques.

### 1.2 Les 15 noms exacts (clé de registre → classe → ligne)

| # | clé registre | classe | ligne |
|---:|---|---|---:|
| 1 | `market` | `MarketPolicy` | policies.py:57 |
| 2 | `limit_passive` | `LimitPassivePolicy` | policies.py:64 |
| 3 | `post_only` | `PostOnlyPolicy` | policies.py:74 |
| 4 | `ioc` | `IocPolicy` | policies.py:83 |
| 5 | `fok` | `FokPolicy` | policies.py:93 |
| 6 | `cancel_replace` | `CancelReplacePolicy` | policies.py:102 |
| 7 | `pegged` | `PeggedPolicy` | policies.py:137 |
| 8 | `iceberg` | `IcebergPolicy` | policies.py:200 |
| 9 | `twap` | `TwapPolicy` | policies.py:248 |
| 10 | `vwap` | `VwapPolicy` | policies.py:304 |
| 11 | `pov` | `PovPolicy` | policies.py:348 |
| 12 | `adaptive` | `AdaptivePolicy` | policies.py:388 |
| 13 | `market_making` | `MarketMakingPolicy` | policies.py:439 |
| 14 | `multi_leg_simultaneous` | `MultiLegSimultaneousPolicy` | policies.py:490 |
| 15 | `maker_then_hedge_taker` | `MakerThenHedgeTakerPolicy` | policies.py:545 |

Verrouillé par test : `tests/test_execution_sim_policies.py:39` (`set(POLICY_REGISTRY) == {…}`)
et `tests/test_execution_sim_cli.py:31` (`len({row["policy"]}) == 15`).

### 1.3 Le harnais qui les exécute

| élément | chemin:ligne |
|---|---|
| CLI | `tools/backtest_execution_matrix.py` (61 lignes) |
| commande | `.venv\Scripts\python.exe tools\backtest_execution_matrix.py --policy all [--quick]` |
| génération de scénarios | `titanium/execution_sim/runner.py:88` `generate_scenarios(seed, quick)` |
| exécution d'un cas | `titanium/execution_sim/runner.py:185` `_run_case(...)` |
| boucle matrice | `titanium/execution_sim/runner.py:356` `run_matrix(spec, config)` |
| id reproductible | `titanium/execution_sim/runner.py:376` `reproducible_run_id(...)` |
| écriture rapports | `titanium/execution_sim/runner.py:385` `write_reports(...)` |
| moteur / appariement / OMS | `engine.py`, `matching.py`, `oms.py`, `portfolio.py`, `metrics.py`, `risk.py`, `models.py` |
| alpha injecté | `titanium/execution_sim/alpha.py:17` `StaticAlpha`, `:33` `LegacyTradeAlphaAdapter` |
| config | `config/execution_backtest.json` (frais maker 1,0 bps / taker 3,0 bps, latences, modèles de spread et slippage) |
| sorties | `results/execution_matrix_full/execution_matrix.{csv,json,md}` (37,5 Mo, 12 960 lignes) et `results/execution_matrix_quick/` |
| rapport de synthèse | `docs/RAPPORT_BACKTEST_15_POLITIQUES.md` (21 997 caractères, Claude 15/08/2026) |
| second rapport | `docs/RAPPORT_EXECUTION_MATRIX_V14.md`, `docs/EXECUTION_SIM_V14.md`, `docs/AUDIT_EXECUTION_MODULAIRE_V14.md` |
| outil de mise en forme | `tools/rapport_politiques_execution.py` |

**Point capital pour la mission** : ce simulateur est **entièrement synthétique**.
`generate_scenarios` tire des scénarios depuis une graine (défaut 14 082 026) et
`_snapshots` (`runner.py:127`) fabrique les quotes. Il ne lit **aucune barre
historique et aucun symbole réel**. `results/execution_matrix_full/execution_matrix.md`
le dit : `"data_fidelity": "synthetic_l1"` (`runner.py:350`).
Il n'y a donc **aucun couplage actuel entre les 15 politiques et un actif nommé**.

### 1.4 Matrice de fidélité / classements (existants)

Source : `results/execution_matrix_full/execution_matrix.md`, run `9cb2fb7c6175484c`, 864 scénarios/politique.

**Classement compatibilité V14 (fidélité de modèle, complexité)** :

| rang | politique | compatibilité | fidélité | complexité |
|---:|---|---:|---:|---:|
| 1 | market | 92.88 | 0.90 | 1.0 |
| 2 | limit_passive | 73.33 | 0.60 | 2.0 |
| 3 | post_only | 71.78 | 0.60 | 2.0 |
| 4 | ioc | 71.36 | 0.75 | 2.0 |
| 5 | cancel_replace | 71.26 | 0.50 | 4.0 |
| 6 | fok | 70.00 | 0.75 | 2.0 |
| 7 | pegged | 67.93 | 0.50 | 4.0 |
| 8 | adaptive | 62.68 | 0.55 | 6.0 |
| 9 | vwap | 61.69 | 0.65 | 5.0 |
| 10 | iceberg | 61.35 | 0.45 | 5.0 |
| 11 | twap | 58.18 | 0.70 | 4.0 |
| 12 | pov | 56.35 | 0.60 | 5.0 |
| 13 | multi_leg_simultaneous | 47.44 | 0.40 | 8.0 |
| 14 | maker_then_hedge_taker | 44.55 | 0.35 | 9.0 |
| 15 | market_making | 40.57 | 0.35 | 9.0 |

**Écart apparié contre `market`** (`docs/RAPPORT_BACKTEST_15_POLITIQUES.md` §2) —
6 politiques battent `market` : pegged (+0,2368 ; z +6,96), cancel_replace
(+0,1686 ; z +4,11), limit_passive (+0,1496 ; z +3,53), iceberg (+0,1482 ;
z +3,50), post_only (+0,1334 ; z +2,91), adaptive (+0,1272 ; z +2,81).
8 sont inférieures, `maker_then_hedge_taker` est indistinguable.

**Réserve majeure documentée** (§3 du rapport) : 5 politiques sur 15 ne sont pas
distinguables — `iceberg` rend un résultat identique au bit près à `limit_passive`
sur 850/864 scénarios (98 %), post_only 94 %, adaptive 94 %, cancel_replace 80 %.
Le simulateur n'a pas de carnet.

**Conditionnalité par régime** (§5) : en spread normal et en volatilité basse,
toute la famille passive est **négative** sauf `pegged`. L'avantage est concentré
sur spread large et grosses tailles.

**Seul point de contact avec le réel** (§8) : 189 ordres limites réellement placés
en démo, 65 remplis (34,4 % contre 80 % simulés), économie +0,0995 R par fill.
Source : `results/limit_lifecycle.ndjson`.

---

## 2. Moteur de backtest / walk-forward d'ENTRÉE

C'est un **moteur distinct** du simulateur d'exécution, et c'est lui qui répond à
la question « simuler des stratégies d'entrée par actif ».

### 2.1 API d'entrée

| élément | chemin:ligne |
|---|---|
| **moteur de rejeu** | `titanium/backtest.py:222` `rejouer(symbol, ltf, htf, *, spread, require_edge, sl_atr_k=1.5, rr_ratio=2.0, breakeven_r=0.8, trail_start_r=1.2, trail_dist_r=0.8, amorcage=250, max_barres=200, stop_temporel=None, pas=1, avec_indicateurs=True, fenetre=400) -> Resultat` |
| simulation de sortie barre à barre | `titanium/backtest.py:135` `_simuler_sortie(...)` |
| coût spread → R | `titanium/backtest.py:55` `cout_spread_r(spread, r_unit)` |
| dataclass trade | `titanium/backtest.py:64` `Trade` |
| dataclass résultat | `titanium/backtest.py:91` `Resultat` (`n`, `esperance_r`, `winrate`, `profit_factor`, `cout_moyen_r`, `resume()`) |
| découpe walk-forward simple | `titanium/backtest.py:350` `decouper_walk_forward(res, n_segments=3)` |
| versement au journal d'edge | `titanium/backtest.py:372` `journaliser(res, chemin)` |
| **walk-forward glissant** | `titanium/analysis/walk_forward.py:46` `analyser_resultat(resultat, is_trades=60, oos_trades=20, step=20)` |
| métriques par fenêtre | `titanium/analysis/walk_forward.py:18` `_metriques(trades)` |
| écriture rapport | `titanium/analysis/walk_forward.py:109` `ecrire_rapport(rapport, dossier)` |

### 2.2 Les 6 appelants existants de `rejouer()`

| appelant | ligne | usage |
|---|---|---|
| `tools/backtest.py` | :85-86 | rejeu univers, deux passes (avec/sans spread), `--journaliser` |
| `tools/walk_forward.py` | :63 | pipeline walk-forward par actif → `results/walk_forward/*.json` |
| `tools/classement_backtest.py` | :52 | classement des actifs |
| `tools/edge_directionnel.py` | :234 | mesure d'edge directionnel |
| `tools/audit_edge_directionnel.py` | :90 | audit |
| `tools/metatester.py` | :262 | pont vers le testeur natif MT5 |

### 2.3 Données consommées

`rejouer()` prend **deux DataFrames pandas** — c'est une API pure, agnostique de
la source :

- `ltf` : barres de la timeframe d'entrée (défaut **M15**), index temporel croissant, trié.
- `htf` : barres de la timeframe haute (défaut **H4**), min 1 500 barres.
- Colonnes requises : `open`, `high`, `low`, `close` (+ `tick_volume` / `real_volume`
  pour le profil de volume). Format produit par
  `titanium/data/mt5_vendor.py:266` `get_rates(symbol, timeframe, count, closed_only=True)` :
  index `time` en `datetime64[ns, UTC]` **corrigé du décalage serveur**
  (`mt5_vendor.py:309-310`, correctif du 12/08/2026), volumes forcés en float64
  (`mt5_vendor.py:243` `_volumes_en_flottant`, uint64 qui boucle à 1,8e19).
- Alignement HTF : `htf.index.searchsorted(ltf.index, side="right") - 1`
  (`backtest.py:266`) — jamais la barre HTF en cours. Zéro regard vers l'avenir.
- Amorçage 250 barres (EMA200 HTF), 1 position à la fois par symbole
  (`backtest.py:345`), plafond 200 barres LTF de durée de vie.
- Coût : spread ask-bid complet payé en deux demi-spreads (entrée `backtest.py:303`,
  sortie `:316`). Un spread total, pas deux.
- SL/TP en dur dans le rejeu : `r_unit = 1.5 × ATR`, `tp = entrée + side × r_unit × 2.0`.
- SL+TP touchés dans la même barre → comptés **PERTE** (règle 4 du docstring).

**Où viennent les barres aujourd'hui** : uniquement de MT5, en direct
(`tools/walk_forward.py:52,61-62` ; `tools/backtest.py:61,78-79`). Il n'existe
**aucun cache disque de barres** — `results/cache/` est vide, `data/` ne contient
que des logs runtime. `get_rates_cache` (`mt5_vendor.py:223`) est un cache **RAM**
à TTL 900 s, réservé aux TF ≥ H1.

### 2.4 `results/walk_forward/*.json`

3 fichiers : `BTCUSD.json`, `EURUSD.json`, `XAUUSD.json`.

Schéma (`schema_version: 1`) :

```
{ schema_version, symbol, status, required_trades, available_trades,
  parameters: { is_trades, oos_trades, step },
  windows: [ { window, is_from, is_to, oos_from, oos_to,
              is:  { n, expectancy_r, profit_factor, win_rate, sharpe_per_trade, mean_cost_r },
              oos: { n, expectancy_r, profit_factor, win_rate, sharpe_per_trade, mean_cost_r },
              alert } ],
  summary: { windows, negative_oos_windows, latest_oos_sharpe, alert } }
```

Exemple résumé — `XAUUSD.json` : `status OK`, `required_trades 80`,
`available_trades 129`, paramètres `is=60 / oos=20 / step=20`, **3 fenêtres**,
0 fenêtre OOS négative, `latest_oos_sharpe 0.515191`.
Fenêtre 1 : IS 2026-05-29 → 2026-07-16, n=60, expectancy **+0,30807 R**, PF 1,875,
win_rate **65 %**, coût moyen 0,0129 R. OOS 2026-07-17 → 2026-07-23, n=20,
expectancy **+0,08558 R**, PF 1,189, win_rate **55 %**, coût moyen 0,0146 R.

→ **Le winrate par actif existe déjà, mais sur des trades SIMULÉS par `rejouer()`,
pas sur des trades live.** C'est le seul endroit du dépôt où un winrate par actif
repose sur un échantillon suffisant (n=60 IS / 20 OOS).

---

## 3. Chemin de décision d'ENTRÉE en production

### 3.1 Où le verdict ENTER est produit

**Un seul point** : `titanium/gates/confluence_gate.py:122` `evaluate(feats, *, side=None, require_edge=False, decided_at=None, quorum=None) -> Decision`.
Le verdict `ENTER` est retourné **uniquement** à la ligne `confluence_gate.py:267`
avec le code `ENTER_CONFLUENCE`. Fonction **pure** : elle ne lit aucune donnée
de marché, n'appelle aucun détecteur.

`GATE_VERSION = "2.0.0"` (`confluence_gate.py:42`).

### 3.2 Noms exacts des portes / piliers

| code | nom de porte (`GateResult.name`) | ligne | condition |
|---|---|---:|---|
| `G0_DATA_INVALID` / `G0_OK` | `data_valid` | :165-167, :195 | préalable absolu, fail-closed |
| `G1_SETUP_INVALID` / `G1_NO_SETUP` | `setup` | :174, :179, :190 | side + famille cohérents |
| `G1_TREND_SR` | **`trend_sr`** | :207 | continuation → tendance HTF alignée ET sur niveau S/R ; reversal → S/R seul |
| `G2_FAIR_VALUE` | **`fair_value`** | :208 | zone de juste prix VPOC/HVN |
| `G3_LIQUIDITY` | **`liquidity`** | :210 | sweep/FVG dans le sens |
| `G4_OTE_OB` | **`ote_ob`** | :212 | golden zone OTE / OB non cassé |
| `G5_CANDLE` | **`candle_confirmed`** | :214 | bougie de confirmation dans le sens |

Il n'y a **pas** de nommage `S0..S3` dans le code. L'équivalent est
`_SUPPORT_PILLARS` (`confluence_gate.py:52`) =
`{fair_value, liquidity, ote_ob, candle_confirmed}` — les **4 piliers de
micro-structure** qui alimentent le quorum. `trend_sr` est hors quorum : il est
**obligatoire** (`confluence_gate.py:225` `if not trend_sr_ok … BLOCK`).

Quorum : `QUORUM_PROD = 3` (`:46`), `QUORUM_EXPLORE = 2` (`:47`).
`support_passed` ∈ 0..4, exposé sur `Decision` (`:161`).

**Familles de setup** (`confluence_gate.py:189`) : exactement deux —
`"continuation"` et `"reversal"`. Une famille hors de ces deux valeurs → `BLOCK_SETUP_INVALID`.

### 3.3 Modérateurs après les piliers

| code de blocage | ligne | cause |
|---|---:|---|
| `BLOCK_PILLAR_MISSING` | :226 | `trend_sr` KO ou `support_passed < quorum` |
| `BLOCK_EMOTION_UNAVAILABLE` | :234 | dict `emotion` incomplet |
| `BLOCK_COST_UNAVAILABLE` | :237 | dict `cost` incomplet |
| `BLOCK_EMOTION_SIDE` | :240 | émotion bloque ce côté |
| `BLOCK_COST_WEEKEND` | :243 | frais week-end |
| `BLOCK_EDGE_NEGATIVE` | :249 | `edge_ok is False` — bloque **aussi en EXPLORE** |
| `BLOCK_EDGE_UNPROVEN` | :252 | PROD et `edge_ok is not True` |
| `WAIT_EMOTION_TIMING` | :259 | `stale` / `wait` / `confidence < 0.35` (`EMOTION_MIN_CONFIDENCE`, :50) |
| **`ENTER_CONFLUENCE`** | **:267** | **tous piliers alignés + modérateurs OK** |

### 3.4 Construction des features en amont

`titanium/features/builder.py` → `build_feats(df_ltf, df_htf, *, price, emotion,
edge_ok, now, marche_continu, min_bars_ltf=60, min_bars_htf=60, with_indicators=False)`.
Détecteurs : `titanium/features/smc.py`, `structure.py`, `candlesticks.py`,
`ict_structure.py`, `ict_market_profiles.py`, `indicators.py`.

Chaîne complète en rejeu : `backtest.py:279` `build_feats(tranche_ltf, tranche_htf,
with_indicators=False)` → `backtest.py:284` `confluence_gate.evaluate(feats,
require_edge=require_edge)` → `backtest.py:290` `if not d.entered`.
**C'est exactement la même fonction `evaluate` qu'en live** — c'est ce qui rend le
rejeu d'entrée légitime.

Chaîne live : `tools/live_demo.py` → `build_feats` → `titanium/orchestrator.py` →
`titanium/risk/riskgate.py` → `titanium/execution/mt5_executor.py` /
`limit_orders.py` → `position_manager.py` (journalisation).

---

## 4. Journaux exploitables pour un winrate par actif

Toutes les mesures ci-dessous ont été faites par la sonde
`sonde_lecture/sonde_winrate.py` exécutée avec `.venv\Scripts\python.exe`.

### 4.1 `results/trades.ndjson` — le journal d'edge

- **179 lignes**, 72 483 octets.
- Plage `closed_at` : **2026-08-10T11:01:29+00:00 → 2026-08-19T05:18:09+00:00** (9 jours).
- **57 symboles distincts** — mais le symbole **n'est pas un champ** : il est
  extrait du préfixe de `context` (`"JPN225|long|continuation|4p"`).
- Champs (18) et effectifs :
  `context` 179, `pnl_r` 179, `closed_at` 179, `ticket` 179, `exit_reason` 179,
  `cost_r` 179, `source` 179, `account` 179, `mode` 179, `quorum` 179,
  `support_pillars` 179, `asset_class` 179, `timeframe` 179, `risk_money` 179,
  `exact_cost` 179, `candle_source` 150, `horloge` 142, `exact_net` 121.
- Champs utiles pour un winrate par actif : `context` (symbole, sens, famille,
  n_piliers), `pnl_r`, `cost_r`, `support_pillars`, `quorum`, `asset_class`,
  `timeframe`, `exit_reason`, `exact_cost`.
- Répartition : `source` = **live 179/179** (aucun backtest versé) ;
  `mode` = **explore 179/179** (aucun trade PROD) ;
  `asset_class` = fx 74, indices 59, energie 23, metaux 13, crypto 8, agricole 2 ;
  `timeframe` = M15 116, H1 53, H4 10 ;
  `exit_reason` = init 106, trailing 43, breakeven 30.
- ⚠️ `exit_reason: "init"` sur 106/179 (59 %) : le motif de sortie n'est pas
  résolu pour la majorité de l'échantillon.

### 4.2 `results/excursions.ndjson`

- **179 lignes** (appariement 1:1 avec `trades.ndjson` par `ticket`), 538 016 octets.
- Plage `ts_exit` : 2026-08-10T11:01:29Z → 2026-08-19T05:18:09Z. **57 symboles**.
- Champs (23) : `ticket`, **`symbol`** (présent ici, contrairement à trades),
  `side`, `ts_open`, `ts_exit`, `entry`, `exit`, `r_unit`, `sl_initial`,
  `tp_initial`, `pnl_r`, `mae_r`, `mfe_r`, `giveback_r`, `exit_reason`,
  `context`, `censored`, `indicators` (panel ~50 séries), `source` (179),
  `horloge` (136), `entry_levels` (15), `entry_atr` (15), `horizon_excursions` (15).
- **C'est le journal le plus riche pour une étude d'entrée** : il porte prix
  d'entrée/sortie, MAE/MFE et le panel d'indicateurs au moment de l'entrée.

### 4.3 `results/shadow_prod.ndjson` — la mine pour les stratégies d'entrée

- **52 773 lignes**, 19,7 Mo. **103 symboles distincts**.
- Plage `ts` : **2026-08-10T03:59:49Z → 2026-08-19T05:23:41Z**.
- Champs (11, tous à 52 773) : `ts`, `symbol`, `bar_time`, `verdict_explore`,
  `verdict_prod`, `code_prod`, `motif_prod`, `piliers`, `famille`, `side`, `edge_ok_vu`.
- Contenu mesuré : `verdict_explore == "ENTER"` sur **100 % des lignes** — ce
  fichier ne journalise que les ENTER du mode EXPLORE, avec le verdict PROD fantôme.
- `verdict_prod == "ENTER"` : **0** ligne. Codes : `BLOCK_PILLAR_MISSING` 45 944,
  `BLOCK_EDGE_UNPROVEN` 6 829.
- Distribution `piliers` : **2 → 45 944 · 3 → 6 780 · 4 → 49**.
  → **6 829 signaux (12,9 %) auraient franchi le quorum PROD** ; ils sont tous
  bloqués par `BLOCK_EDGE_UNPROVEN`, jamais par la confluence.
- `famille` : continuation 30 399, reversal 22 374.
- Top ENTER/symbole : AUDCAD 1 687, ETHUSD 1 526, WTI.fs 1 494, JPN225 1 428,
  EURCAD 1 282, BTC-JPY 1 151, NK225.fs 1 146, AAVE-USD 1 113, USDCAD 1 096,
  GBPJPY 1 041.
- ⚠️ Ce fichier porte **le signal d'entrée mais aucun résultat** : pas de prix,
  pas de PnL. C'est le substrat idéal d'un backtest d'entrée, à condition de
  disposer des barres correspondantes.

### 4.4 `results/limit_lifecycle.ndjson`

- **826 lignes**, 460 171 octets. **69 symboles**.
- Plage (`closed_at`, présent sur 138 lignes) : 2026-08-12T16:59:39Z → 2026-08-19T05:18:09Z.
- Champs (31) : `event` (826), `order_ticket`, `symbol`, `side`, `planned_price`,
  `market_reference_price`, `target_saving_r`, `context`, `regime`, `asset_class`,
  `mode`, `event_id`, `at` (tous 826) ; `r_unit` 688 ; `expires_at` 538 ;
  `target_saving_price`/`spread_r`/`lot`/`risk_money`/`timeframe`/`candle_source` 343 ;
  `position_ticket`/`fill_price`/`realized_saving_r`/`slippage_r` **288** ;
  `broker_state`/`broker_comment` 195 ; `exit_price`/`pnl_r`/`cost_r`/`closed_at` **138** ;
  `repair_source` 2.
- → 343 ordres limites placés, 288 remplis, **138 clos avec un `pnl_r`**. C'est le
  seul journal qui mesure une politique d'exécution passive sur du réel
  (`realized_saving_r`, `slippage_r`).

### 4.5 `results/journal_rejets.ndjson`

- 55 lignes, 28 symboles, plage `ts_exit` 2026-08-07T13:03:26Z → 2026-08-12T15:30:01Z.
- 19 champs dont `net_currency`, `profit`, `commission`, `swap`, `fee`,
  `close_reason`, `exit_class`, `edge_eligible` (toutes lignes `coverage_only`).
- Trades récupérés de l'historique MT5 mais **écartés** de la mesure d'edge
  (`reason: CONTEXTE_ABSENT_CLOTURE_HISTORIQUE`).

---

## 5. Calcul réel — winrate et PnL net moyen en R PAR SYMBOLE

Sonde : `sonde_lecture/sonde_winrate.py`, exécutée avec `.venv\Scripts\python.exe`.
Sortie brute : `sonde_lecture/sortie_winrate.json`.
Source : `results/trades.ndjson`, 179 lignes, symbole extrait du préfixe de
`context` (0 ligne sans symbole ; jointure de secours par `ticket` sur
`excursions.ndjson` disponible mais non nécessaire).
`winrate = #(pnl_r > 0)/n`. `pnl_r` est le résultat **net** journalisé.

### 5.1 Agrégat

| | valeur |
|---|---|
| trades | **179** |
| symboles distincts | **57** |
| winrate global | **40,78 %** |
| PnL net moyen | **−0,2317 R** |
| somme R | **−41,475 R** |
| symboles avec n ≥ 20 | **AUCUN** |
| symboles avec n ≥ 30 | **AUCUN** |
| n maximal par symbole | **17 (USTECH)** |
| moyenne de n par symbole | 3,14 |

### 5.2 Tableau complet, trié par n décroissant

| symbole | n | winrate | PnL R moyen | se | t | somme R | PF |
|---|---:|---:|---:|---:|---:|---:|---:|
| USTECH | 17 | 41.2 % | -0.2332 | 0.2157 | -1.08 | -3.965 | 0.557 |
| UKOIL | 10 | 40.0 % | -0.1820 | 0.3895 | -0.47 | -1.820 | 0.694 |
| EURCAD | 9 | 22.2 % | -0.1825 | 0.4248 | -0.43 | -1.642 | 0.711 |
| JPN225 | 9 | 66.7 % | +0.2151 | 0.3388 | 0.63 | +1.936 | 1.78 |
| EURJPY | 7 | 28.6 % | -0.6888 | 0.1906 | -3.61 | -4.822 | 0.02 |
| NAS100.fs | 7 | 57.1 % | -0.3555 | 0.2608 | -1.36 | -2.489 | 0.223 |
| XAGUSD | 7 | 71.4 % | +0.2557 | 0.3280 | 0.78 | +1.790 | 2.516 |
| HK50 | 6 | 50.0 % | +0.1243 | 0.5778 | 0.22 | +0.746 | 1.245 |
| WTI.fs | 6 | 83.3 % | +0.3149 | 0.3721 | 0.85 | +1.889 | 2.875 |
| AUDCAD | 5 | 20.0 % | -0.6870 | 0.2641 | -2.6 | -3.435 | 0.096 |
| EURUSD | 5 | 60.0 % | -0.1340 | 0.3632 | -0.37 | -0.670 | 0.658 |
| NK225.fs | 5 | 20.0 % | -0.5522 | 0.3598 | -1.53 | -2.761 | 0.241 |
| USDCAD | 5 | 60.0 % | +0.0499 | 0.4292 | 0.12 | +0.250 | 1.125 |
| USDZAR | 5 | 40.0 % | -0.4051 | 0.3905 | -1.04 | -2.025 | 0.327 |
| AUDUSD | 4 | 100.0 % | +0.8054 | 0.3931 | 2.05 | +3.222 | ∞ |
| BRENT.fs | 4 | 75.0 % | +0.9583 | 0.6696 | 1.43 | +3.833 | 4.815 |
| BTC-JPY | 4 | 50.0 % | +0.1297 | 0.6654 | 0.2 | +0.519 | 1.261 |
| EURNZD | 4 | 50.0 % | -0.2787 | 0.4447 | -0.63 | -1.115 | 0.442 |
| BTCUSD | 3 | 66.7 % | +0.7251 | 0.7230 | 1.0 | +2.175 | 5.343 |
| GBPUSD | 3 | 33.3 % | -0.4940 | 0.4920 | -1.0 | -1.482 | 0.248 |
| USOIL | 3 | 66.7 % | +0.3455 | 0.8610 | 0.4 | +1.036 | 2.036 |
| AUDNZD | 2 | 0.0 % | -0.9746 | 0.0238 | -40.86 | -1.949 | 0.0 |
| AUS200 | 2 | 0.0 % | -0.9770 | 0.0225 | -43.42 | -1.954 | 0.0 |
| CADJPY | 2 | 50.0 % | -0.1953 | 0.8049 | -0.24 | -0.391 | 0.609 |
| EURHUF | 2 | 0.0 % | -0.6093 | 0.3727 | -1.63 | -1.219 | 0.0 |
| FT100.fs | 2 | 0.0 % | -0.9007 | 0.0998 | -9.03 | -1.801 | 0.0 |
| GBPNZD | 2 | 50.0 % | -0.4415 | 0.5555 | -0.79 | -0.883 | 0.114 |
| HSI.fs | 2 | 0.0 % | -0.7517 | 0.2513 | -2.99 | -1.503 | 0.0 |
| NZDJPY | 2 | 0.0 % | -0.9649 | 0.0133 | -72.28 | -1.930 | 0.0 |
| NZDUSD | 2 | 0.0 % | -1.0008 | 0.0042 | -235.47 | -2.002 | 0.0 |
| SGDJPY | 2 | 0.0 % | -1.0026 | 0.0008 | -1253.25 | -2.005 | 0.0 |
| SGFREE | 2 | 0.0 % | -0.8619 | 0.0406 | -21.2 | -1.724 | 0.0 |
| USDJPY | 2 | 50.0 % | -0.2199 | 0.7796 | -0.28 | -0.440 | 0.56 |
| XAUAUD | 2 | 0.0 % | -0.9952 | 0.0032 | -311.0 | -1.990 | 0.0 |
| XAUEUR | 2 | 0.0 % | -0.9929 | 0.0042 | -236.4 | -1.986 | 0.0 |
| XAUUSD | 2 | 0.0 % | -0.7505 | 0.2503 | -3.0 | -1.501 | 0.0 |
| AUDSGD | 1 | 0.0 % | -0.0679 | 0.0000 | — | -0.068 | 0.0 |
| CADCHF | 1 | 100.0 % | +0.0652 | 0.0000 | — | +0.065 | ∞ |
| CHFSEK | 1 | 0.0 % | -0.9893 | 0.0000 | — | -0.989 | 0.0 |
| CN50 | 1 | 100.0 % | +2.0127 | 0.0000 | — | +2.013 | ∞ |
| COCOA.fs | 1 | 0.0 % | -1.0000 | 0.0000 | — | -1.000 | 0.0 |
| COFFEE.fs | 1 | 100.0 % | +0.4753 | 0.0000 | — | +0.475 | ∞ |
| DJ30.fs | 1 | 0.0 % | -0.6586 | 0.0000 | — | -0.659 | 0.0 |
| ETHUSD | 1 | 0.0 % | -1.0072 | 0.0000 | — | -1.007 | 0.0 |
| EURAUD | 1 | 0.0 % | -1.0389 | 0.0000 | — | -1.039 | 0.0 |
| EURCHF | 1 | 0.0 % | -0.9517 | 0.0000 | — | -0.952 | 0.0 |
| EURSGD | 1 | 100.0 % | +0.2120 | 0.0000 | — | +0.212 | ∞ |
| GBPJPY | 1 | 0.0 % | -0.9988 | 0.0000 | — | -0.999 | 0.0 |
| GBPNOK | 1 | 0.0 % | -0.9776 | 0.0000 | — | -0.978 | 0.0 |
| NETH25 | 1 | 0.0 % | -0.9137 | 0.0000 | — | -0.914 | 0.0 |
| SPA35 | 1 | 0.0 % | -0.5059 | 0.0000 | — | -0.506 | 0.0 |
| SWI20 | 1 | 0.0 % | -0.6638 | 0.0000 | — | -0.664 | 0.0 |
| UK100 | 1 | 100.0 % | +0.0502 | 0.0000 | — | +0.050 | ∞ |
| US2000 | 1 | 100.0 % | +0.0492 | 0.0000 | — | +0.049 | ∞ |
| USDCHF | 1 | 0.0 % | -0.6445 | 0.0000 | — | -0.644 | 0.0 |
| USDSEK | 1 | 0.0 % | -0.8694 | 0.0000 | — | -0.869 | 0.0 |
| USDSGD | 1 | 0.0 % | -0.9460 | 0.0000 | — | -0.946 | 0.0 |

### 5.3 Verdict statistique — sans ambiguïté

**Aucun symbole n'a un effectif suffisant.**

- Le seuil interne le plus bas du projet est `MIN_SAMPLES = 20`
  (`titanium/edge.py`), et il est lui-même documenté comme insuffisant :
  `docs/DESIGN_deadlock_edge.md` §1.7 montre qu'à n=20 un contexte sans aucun edge
  passe le critère 41,1 % du temps. Le plancher recommandé y est **n ≥ 60 par
  cellule**, voire 118 si σ ≈ 1,4 R.
- Le maximum observé est **n = 17** (USTECH). 21 symboles sur 57 ont n = 1.
- Les 4 symboles à winrate 100 % (AUDUSD n=4, CADCHF n=1, CN50 n=1, COFFEE.fs n=1,
  EURSGD n=1, UK100 n=1, US2000 n=1) et les 14 symboles à winrate 0 % avec n=2
  sont du **bruit pur**. Les `t` extrêmes (SGDJPY −1253, NZDUSD −235) sont un
  artefact : deux trades quasi identiques à −1,0 R donnent un se ≈ 0.
- Les 3 seuls symboles dont `|t| > 2` avec n ≥ 4 : EURJPY (n=7, t=−3,61),
  AUDCAD (n=5, t=−2,60), AUDUSD (n=4, t=+2,05). Sur 57 tests simultanés sans
  correction de multiplicité, c'est **exactement ce qu'on attend sous H₀**.
- L'échantillon couvre **9 jours** (10→19/08/2026), soit **un seul régime de marché**.
- 100 % des trades sont en mode `explore` (quorum 2/4) : ils ne mesurent **pas**
  la population que le mode PROD (quorum 3/4) prendrait.

**Conclusion opérationnelle : `results/trades.ndjson` ne permet pas — et ne
permettra pas avant des mois — d'estimer un winrate par actif.** L'agrégat lui-même
(40,78 %, −0,2317 R, −41,5 R sur 179 trades) est le seul chiffre lisible, et il est
négatif. Toute stratégie d'entrée par actif doit donc être évaluée par **rejeu
historique** (`titanium/backtest.py:222`), pas par le journal live.

---

## 6. Ce qui manque pour brancher un backtest d'entrée sur des barres archivées

### 6.1 Ce qui existe déjà — et n'est pas à réécrire

1. **La fonction d'entrée est prête et pure** : `confluence_gate.evaluate(feats,
   require_edge=...)` (`confluence_gate.py:122`). Aucune dépendance MT5, aucun I/O.
2. **Le rejeu barre à barre est prêt** : `titanium.backtest.rejouer(symbol, ltf,
   htf, ...)` (`backtest.py:222`). Il prend **deux DataFrames pandas**, pas une
   source. Il est déjà agnostique de l'origine des barres.
3. **Le walk-forward est prêt** : `titanium.analysis.walk_forward.analyser_resultat`
   (`walk_forward.py:46`) + `ecrire_rapport` (`:109`), schéma v1 déjà produit pour
   3 symboles.
4. **Le format attendu est spécifié** : index `DatetimeIndex` UTC croissant,
   colonnes `open/high/low/close` (+ `tick_volume`/`real_volume` en float64),
   ≥ 260 barres LTF, ≥ 260 barres HTF alignables.

### 6.2 Ce qui manque — 4 pièces, par ordre de blocage

**(A) Il n'existe aucune archive de barres OHLCV sur disque.** — bloquant n°1.

- `data/` ne contient que des logs runtime ; `results/cache/` est **vide** ;
  `get_rates_cache` (`mt5_vendor.py:223`) est un cache RAM à TTL 900 s.
- Les seules archives disque sont des **ticks L1** et un **carnet L2**, pas des barres :
  - `results/quotes/<SYM>/<jour>.ndjson` — 5 symboles (BTCUSD 95 Mo, ETHUSD 61 Mo,
    EURUSD 28 Mo, US500 29 Mo, XAUUSD 165 Mo), 4-5 jours (15→19/08/2026).
    Collecteur : `tools/enregistreur_quotes.py`.
  - `results/carnet_binance/<SYM>/<jour>.{depth,trades}.ndjson` — BTCUSDT 1,95 Go,
    ETHUSDT 1,48 Go. Collecteur : `tools/enregistreur_carnet_binance.py`.
    Doc : `docs/ARCHIVE_MARCHE_V14.md`.
- Conséquence : **tout backtest d'entrée exige aujourd'hui un terminal MT5 ouvert**
  (`tools/backtest.py:78`, `tools/walk_forward.py:61`). Hors MT5, le pipeline
  est inexécutable.

**(B) Il manque un chargeur de barres depuis disque.** — le point de couplage exact.

Le couplage est **trivial** parce que `rejouer()` ne connaît pas MT5 : il suffit
d'une fonction de même contrat que `get_rates` :

```
charger_barres(symbole: str, timeframe: str, count: int) -> pd.DataFrame
   # index DatetimeIndex UTC croissant, colonnes open/high/low/close/tick_volume
```

Points de substitution (3 lignes au total, aucun changement de `titanium/`) :
- `tools/backtest.py:61,78-79`
- `tools/walk_forward.py:52,61-62`
- `tools/edge_directionnel.py`, `tools/classement_backtest.py` (mêmes motifs)

Il faut aussi un substitut à `ensure_symbol(symbole).spread * .point`
(`tools/walk_forward.py:59-60`) qui fournit le **spread par symbole** : sans lui,
`spread=0.0` produit un résultat flatteur et faux (docstring `backtest.py:244`).
Un spread par symbole peut être dérivé de `results/quotes/` (ask−bid) pour les
5 symboles archivés, ou d'une table statique pour les autres.

**(C) Il manque un producteur d'archive de barres.** — nouvel outil à écrire.

Deux voies, non exclusives :
- **Agréger les ticks L1 déjà archivés** en barres M15/H4 depuis
  `results/quotes/` — ne couvre que 5 symboles sur 4-5 jours, donc ~400 barres
  M15 : **très en dessous de l'amorçage de 250 + un échantillon utile**. Insuffisant seul.
- **Télédécharger et sérialiser les barres MT5** via
  `mt5_vendor.get_rates_range(symbol, timeframe, start, end)` (`mt5_vendor.py:320`),
  qui existe déjà et n'est appelé nulle part dans le pipeline de backtest.
  C'est la voie recommandée : une passe MT5 unique, puis N backtests hors ligne.

**(D) Le simulateur d'exécution n'est pas couplé au rejeu d'entrée.** — angle mort majeur.

- `titanium/backtest.py:303` entre **à la clôture de la barre de décision**, prix
  `close ± spread/2`. C'est une politique `market` implicite et parfaite : fill 100 %,
  aucun slippage, aucune latence, aucun rejet.
- Les 15 politiques (`titanium/execution_sim/`) tournent sur des scénarios
  synthétiques et **n'ont aucun symbole**. Il n'existe **aucun appel** de
  `execution_sim` depuis `backtest.py` ni l'inverse.
- Le seul pont existant est `titanium/execution_sim/alpha.py:33`
  `LegacyTradeAlphaAdapter.intents(trades)` — il convertit des trades legacy en
  `ExecutionIntent`. **C'est le point de couplage naturel** : `rejouer()` produit
  des `Trade` (`backtest.py:64`), l'adaptateur les transforme en intentions, le
  moteur `execution_sim` applique une politique. Mais rien ne referme la boucle :
  le prix de fill obtenu n'est jamais réinjecté dans `_simuler_sortie`.
- `docs/DESIGN_deadlock_edge.md` §3 documente le biais correspondant : « entrée à
  la clôture de barre, impossible en live : on décide *à* la clôture, on est
  rempli *au tick suivant* ». Ce biais n'est **pas corrigé** aujourd'hui.

### 6.3 Trois avertissements de méthode, déjà mesurés dans le dépôt

1. **La rétention d'edge simulation → exécution native est nulle.** V12 : XAUUSD
   PF 2,31 en Python, PF 0,90 au testeur natif MT5.
   `(0,90−1)/(2,31−1) = −0,076` (`docs/DESIGN_deadlock_edge.md` §6.4).
2. **Optimiser en in-sample sélectionne le pire réglage forward.** Balayage
   XAUUSD 699 signaux / 241 jours : meilleur IS (SL 3.0 × RR 3.5, +0,506 R)
   → forward 0,000 R, PF 0,86. Répliqué sur ETHUSD.
   Toute étude d'entrée doit passer par `analyser_resultat` en OOS.
3. **La contrainte de créneau est un paramètre de stratégie déguisé.**
   `backtest.py:345` impose 1 position par symbole ; le live impose
   `MAX_POSITIONS=3` et `MAX_PAR_SYMBOLE=1`. Un backtest par symbole isolé ne
   mesure pas ce que le portefeuille ferait.

### 6.4 Chemin le plus court, en 4 étapes

1. Écrire `tools/archiver_barres.py` : `get_rates_range` → Parquet/NDJSON par
   `(symbole, timeframe)` dans `data/barres/`. Une passe MT5, lecture seule.
2. Écrire `charger_barres()` avec le contrat de `get_rates`, et brancher un
   `--source disque` sur `tools/backtest.py` et `tools/walk_forward.py`.
   Aucun fichier de `titanium/` n'est touché.
3. Lancer `tools/walk_forward.py` sur les 103 symboles vus dans
   `shadow_prod.ndjson` → un `results/walk_forward/<SYM>.json` par actif, avec
   `win_rate` IS et OOS, `n`, `expectancy_r`, `profit_factor`, `mean_cost_r`.
   **C'est cela, le « winrate par actif » exploitable.**
4. Seulement ensuite, coupler `execution_sim` via `LegacyTradeAlphaAdapter` pour
   mesurer ce que chaque politique d'exécution ajoute ou retire au winrate d'entrée.

---

## Annexe — fichiers produits par cette sonde (lecture seule)

| fichier | rôle |
|---|---|
| `sonde_lecture/sonde_winrate.py` | schémas des 5 journaux + winrate/PnL par symbole |
| `sonde_lecture/sortie_winrate.json` | sortie brute du calcul du §5 |
| `sonde_lecture/sonde_shadow.py` | verdicts d'entrée par symbole sur `shadow_prod.ndjson` |
| `sonde_lecture/sortie_shadow.json` | sortie brute du §4.3 |

Commande de reproduction :
`.venv\Scripts\python.exe collab\prime_agent\runs\strategie-entree-20260819\sonde_lecture\sonde_winrate.py`

Aucun fichier existant n'a été lu en écriture, aucun service n'a été démarré,
`.env`, MT5, `results/positions.json` et la boucle de trading n'ont pas été touchés.
