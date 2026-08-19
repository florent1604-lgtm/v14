# Resume pour le parent — inventaire backtest entree (19/08/2026)

INVENTAIRE BACKTEST ENTREE — rapport complet : collab/prime_agent/runs/strategie-entree-20260819/inventaire_backtest.md (604 lignes). Lecture seule, aucun fichier existant modifie.

[1] LES 15 POLITIQUES D'EXECUTION
Definies dans titanium/execution_sim/policies.py, registre POLICY_REGISTRY lignes 574-593, fabrique get_policy() :250:596.
Noms exacts : market(:57) limit_passive(:64) post_only(:74) ioc(:83) fok(:93) cancel_replace(:102) pegged(:137) iceberg(:200) twap(:248) vwap(:304) pov(:348) adaptive(:388) market_making(:439) multi_leg_simultaneous(:490) maker_then_hedge_taker(:545).
Harnais : tools/backtest_execution_matrix.py -> runner.py:356 run_matrix ; sorties results/execution_matrix_full/*.{csv,json,md} (12960 lignes) ; rapport docs/RAPPORT_BACKTEST_15_POLITIQUES.md.
Matrice de fidelite (execution_matrix.md) : market 0.90 / ioc-fok 0.75 / twap 0.70 / vwap 0.65 / limit_passive-post_only-pov 0.60 / adaptive 0.55 / cancel_replace-pegged 0.50 / iceberg 0.45 / multi_leg 0.40 / maker_hedge-market_making 0.35.
POINT CRITIQUE : ce simulateur est 100% SYNTHETIQUE ("data_fidelity":"synthetic_l1", runner.py:350). Scenarios tires d'une graine, AUCUNE barre historique, AUCUN symbole reel. Zero couplage entre les 15 politiques et un actif nomme.

[4] JOURNAUX (mesures faites par sonde, .venv\Scripts\python.exe)
- results/trades.ndjson : 179 lignes, 18 champs, 57 symboles, 10->19/08/2026. Le symbole N'EST PAS un champ : prefixe de "context". source=live 179/179, mode=explore 179/179. exit_reason "init" sur 106/179.
- results/excursions.ndjson : 179 lignes, 23 champs, 57 symboles, appariement 1:1 par ticket. Porte symbol, entry/exit, r_unit, mae_r/mfe_r et le panel d'indicateurs -> journal le plus riche.
- results/shadow_prod.ndjson : 52773 lignes, 11 champs, 103 symboles, 9 jours. 100% verdict_explore=ENTER, 0 ENTER prod. Piliers : 2->45944, 3->6780, 4->49 => 6829 signaux (12,9%) passaient le quorum PROD, tous bloques BLOCK_EDGE_UNPROVEN. Substrat ideal d'un backtest d'entree, mais SANS prix ni resultat.
- results/limit_lifecycle.ndjson : 826 lignes, 31 champs, 69 symboles. 343 limites placees, 288 remplies, 138 closes avec pnl_r.

[5] WINRATE / PnL PAR SYMBOLE — CALCUL REEL (sonde_lecture/sortie_winrate.json)
Global : n=179, 57 symboles, winrate 40,78 %, PnL net moyen -0,2317 R, somme -41,475 R.
Top n : USTECH n=17 41,2% -0,2332R | UKOIL n=10 40,0% -0,1820R | EURCAD n=9 22,2% -0,1825R | JPN225 n=9 66,7% +0,2151R | EURJPY n=7 28,6% -0,6888R (t=-3,61) | NAS100.fs n=7 57,1% -0,3555R | XAGUSD n=7 71,4% +0,2557R | HK50 n=6 | WTI.fs n=6 83,3% +0,3149R | AUDCAD n=5 20,0% -0,6870R.
VERDICT : AUCUN symbole n'atteint n>=20, ni meme n=18. Maximum n=17. 21 symboles sur 57 ont n=1. Moyenne 3,14 trades/symbole. Le plancher recommande par docs/DESIGN_deadlock_edge.md est n>=60 (118 si sigma=1,4). Echantillon = 9 jours, un seul regime, 100% quorum-2.
=> Un winrate par actif est STATISTIQUEMENT IMPOSSIBLE sur trades.ndjson, aujourd'hui et pour des mois.

[2/3/6 en bref]
Le moteur d'entree EXISTE : titanium/backtest.py:222 rejouer(symbol, ltf, htf, spread=...) rejoue la MEME confluence_gate.evaluate() qu'en live (verdict ENTER produit ligne 267, piliers trend_sr/fair_value/liquidity/ote_ob/candle_confirmed, quorum 3 prod / 2 explore). Walk-forward : titanium/analysis/walk_forward.py:46 + results/walk_forward/{BTCUSD,EURUSD,XAUUSD}.json (XAUUSD : 129 trades, 3 fenetres, win_rate IS 65% / OOS 55%).
CE QUI MANQUE : (a) aucune archive de barres OHLCV sur disque — data/ et results/cache/ vides ; seules des archives de TICKS L1 (results/quotes, 5 symboles, 4-5 jours) et un carnet Binance (3,4 Go) existent ; tout backtest exige donc MT5 ouvert ; (b) un chargeur charger_barres(sym,tf,count) au contrat de get_rates — le couplage est trivial, rejouer() prend deux DataFrames et ignore MT5 : 3 lignes a substituer dans tools/backtest.py:78 et tools/walk_forward.py:61, zero changement dans titanium/ ; (c) un producteur d'archive via mt5_vendor.get_rates_range() (:320, deja ecrit, jamais appele) ; (d) le rejeu entre A LA CLOTURE de barre (backtest.py:303) = politique market parfaite, fill 100%, aucune latence — les 15 politiques ne sont couplees a rien. Seul pont existant : execution_sim/alpha.py:33 LegacyTradeAlphaAdapter.
RECOMMANDATION : archiver les barres -> lancer walk_forward sur les 103 symboles de shadow_prod -> c'est CA le winrate par actif exploitable -> coupler execution_sim seulement ensuite.
