# Audit indépendant Hermes — collecte, rejeu et exécution V14

Date: 21/08/2026. Mode: lecture seule, PAPER/SHADOW uniquement.
Epoch figée: HEAD `a4ccf5b`; 97 résultats de rejeu complets sur 149, plus récent `2026-08-21T08:19:27Z`. Le rejeu continuait pendant l'audit.

## Verdict court

1. Le même moteur d'entrée V14 produit une espérance OOS positive et BH-significative sur 13 des 97 actifs terminés, mais ces 13 ne sont pas encore des stratégies promouvables: les sorties ne conservent pas les trades bruts, donc l'indépendance des observations et un bootstrap temporel par blocs ne peuvent pas être audités.
2. Les candidats se concentrent dans quelques risques communs: crypto, indices US, indices Europe, pétrole. Il faut sélectionner un représentant par bloc et plafonner l'exposition du bloc; compter les tickers comme des paris indépendants sous-estime le risque.
3. Aucune avance-retard liquide n'est défendable comme signal prédictif. Les corrélations contemporaines servent au risque et à la déduplication, pas à déclencher une entrée.
4. La meilleure politique d'exécution par actif n'est pas identifiable avec les 15 politiques actuelles: leur matrice est `synthetic_l1`, sans symbole réel. Le journal de limites réel permet seulement une hypothèse par classe, pas un classement causal.

## Rejeu d'entrée, état intermédiaire 97/149

Méthode indépendante: dernier tiers temporel comme vérification; test t unilatéral E[R] > 0 sur chaque résumé de vérification; IC95 bilatéral; Benjamini-Hochberg sur les 97 actifs; minimum 60 clôtures dans calibration et vérification; espérance positive dans les deux segments.

Résultat global: médiane OOS = -0,1258 R; 25/97 positifs en vérification; 27/97 positifs en calibration; 18 positifs dans les deux; 13 passent aussi IC95 > 0 et BH q <= 5 %. Dégradation médiane parmi les actifs positifs en calibration: -0,0245 R.

| Actif | E[R] calibration | E[R] vérification | IC95 vérification | n OOS | PF OOS | q BH |
|---|---:|---:|---:|---:|---:|---:|
| USTECH | +0,1471 | +0,1731 | [+0,1208; +0,2255] | 1810 | 1,435 | <0,0001 |
| UKOIL | +0,1881 | +0,1602 | [+0,1097; +0,2107] | 1933 | 1,400 | <0,0001 |
| BTCUSD | +0,1829 | +0,1584 | [+0,1098; +0,2071] | 2041 | 1,404 | <0,0001 |
| ETHUSD | +0,1332 | +0,1583 | [+0,1068; +0,2098] | 1902 | 1,389 | <0,0001 |
| FRA40 | +0,1292 | +0,1581 | [+0,1063; +0,2098] | 1897 | 1,384 | <0,0001 |
| BTC-JPY | +0,1650 | +0,1453 | [+0,0951; +0,1955] | 1889 | 1,370 | <0,0001 |
| NAS100.fs | +0,1000 | +0,1333 | [+0,0810; +0,1856] | 1821 | 1,319 | 0,000003 |
| COFFEE.fs | +0,0544 | +0,1212 | [+0,0848; +0,1577] | 3716 | 1,287 | <0,0001 |
| BRENT.fs | +0,1580 | +0,1184 | [+0,0681; +0,1688] | 1911 | 1,286 | 0,000019 |
| BNB-USD | +0,0673 | +0,1026 | [+0,0529; +0,1522] | 2020 | 1,236 | 0,000213 |
| US500 | +0,0883 | +0,0973 | [+0,0436; +0,1511] | 1689 | 1,227 | 0,00147 |
| S&P.fs | +0,0271 | +0,0763 | [+0,0216; +0,1311] | 1622 | 1,174 | 0,0219 |
| DJ30.fs | +0,0631 | +0,0756 | [+0,0201; +0,1311] | 1571 | 1,173 | 0,0235 |

Important: le moteur testé est une seule stratégie (confluence V14, SL 1,5 ATR, TP 2R, break-even/trailing existants), pas un balayage de stratégies. Le spread médian archivé est inclus. Le résultat ne prouve donc pas quel sous-pilier, quelle sortie ou quel régime cause l'avantage.

## Intégration V14 recommandée

SHADOW seulement, par portefeuille de blocs:

- Indices US: garder USTECH comme représentant primaire; NAS100.fs est son alias économique (rho 0,9956) et US500/S&P.fs/DJ30.fs appartiennent au même bloc. Ne jamais cumuler leurs quotas comme cinq risques indépendants.
- Pétrole: garder UKOIL comme représentant primaire; BRENT.fs est quasi-duplicat (rho 0,9857).
- Crypto: BTCUSD primaire; BTC-JPY est quasi-duplicat (rho 0,9617) et ETHUSD appartient au même bloc crypto. BNB-USD reste un candidat séparé dans le graphe au seuil 0,7 mais charge le même facteur risk-on; quota crypto commun requis.
- Europe: FRA40 comme représentant du bloc indices Europe.
- Agricole: COFFEE.fs est le seul candidat fort de sa classe, mais son histoire calibration/OOS est déséquilibrée (867 vs 3716 trades); garder en cohorte séparée.

Ordre d'intégration proposé: allowlist SHADOW de ces cinq représentants, métriques séparées par actif et régime, aucune modification des seuils; comparaison contre tous les candidats bloqués; validation par bootstrap temporel et fenêtre finale scellée avant M2.

## Corrélations et pouvoir prédictif

H1: 148 symboles, 31 830 barres, 79 blocs au seuil 0,7. Paires d'alias majeures: NAS100.fs/USTECH 0,9956; S&P.fs/US500 0,9935; BRENT.fs/UKOIL 0,9857; BTC-JPY/BTCUSD 0,9617. US500/USTECH vaut 0,9408.

ACP sur 123 symboles et 17 148 barres communes: CP1 risk-on 24,63 %, CP2 dollar 11,91 %, CP3 yen/franc 9,45 %; total 45,99 %. Ces facteurs peuvent piloter des plafonds d'exposition, pas une direction de trade.

Le scan avance-retard ne fournit pas de signal liquide robuste. Les plus gros lags apparents impliquent surtout USDCHC/EUCUSD, instruments courts, synthétiques ou à cotation retardée. Conclusion: NO-GO pour une stratégie « un actif prédit l'autre »; GO SHADOW pour un garde de concentration contemporaine.

## Exécution

Matrice 15 politiques: entièrement synthétique, 864 scénarios/politique, aucun actif nommé. Elle soutient seulement trois familles à tester: `market` témoin, `limit_passive` simple, `adaptive`/`cancel_replace` en challenger. `pegged` paraît meilleur mais exige un carnet indisponible chez Axi. IOC/FOK/TWAP/VWAP/POV/market-making/multi-jambes ne sont pas prioritaires.

Journal broker `limit_lifecycle.ndjson` actuel: 525 ordres placés, 268 remplis, fill 51,0 %, IC Wilson95 [46,8 %; 55,3 %]. Par classe:

| Classe | placés | remplis | fill | IC95 |
|---|---:|---:|---:|---:|
| énergie | 48 | 39 | 81,3 % | [68,1; 89,8] |
| métaux | 42 | 28 | 66,7 % | [51,6; 79,0] |
| indices | 147 | 93 | 63,3 % | [55,2; 70,6] |
| agricole | 9 | 4 | 44,4 % | [18,9; 73,3] |
| FX | 217 | 85 | 39,2 % | [32,9; 45,8] |
| crypto | 62 | 19 | 30,6 % | [20,6; 43,0] |

Interprétation prudente: hypothèse `limit_passive-first` pour énergie/métaux/indices; `market` témoin et fallback temporel pour FX/crypto. Ce n'est pas encore une recommandation causale: les ordres limites ne sont pas randomisés contre des ordres marché identiques, et l'opportunité perdue des expirations n'est pas entièrement chiffrée. Aucune politique par actif ne doit être activée avant A/B SHADOW apparié sur mêmes signaux.

## Défauts de mesure à fermer

1. Persister les trades bruts du rejeu (entrée/sortie, pnl_r, cost_r, timestamp, régime, piliers, builder_version) dans un artefact immuable; les JSON actuels ne gardent que les résumés.
2. Refaire l'inférence par bootstrap en blocs temporels et regrouper la multiplicité au niveau bloc d'actifs, pas seulement ticker.
3. Mesurer brut et coût séparément dans le rejeu; l'actuel ne publie que l'espérance nette et le spread en points, impossible de prouver si le classement est un classement de coûts.
4. Construire un A/B SHADOW apparié `market` vs `limit_passive` vs `adaptive`, avec fill, délai, slippage, économie et coût d'opportunité par actif/classe.
5. Appliquer un budget de risque par bloc de corrélation et dédupliquer les alias broker avant toute adaptation.

## Reproduction

`.venv/Scripts/python.exe collab/HERMES_AUDIT_V14_20260821.py`

Entrées: `results/rejeu_univers/*.json`, `results/correlations/structure_H1.json`, `results/limit_lifecycle.ndjson`. Le script relit l'état courant; le tableau ci-dessus est l'epoch 97/149 et ne doit pas être mélangé avec une relance ultérieure.
