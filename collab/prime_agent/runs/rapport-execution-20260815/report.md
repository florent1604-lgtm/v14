# Rapport — nouvelles stratégies d'exécution en test (V14)

Date : 2026-08-15 · Auteur : Prime Agent · Périmètre : PAPER/DEMO, aucune autorité d'exécution.

## 1. Ce qui est réellement "en test"

Deux chantiers distincts, à ne pas confondre :

| Chantier | Nature | Où | État |
|---|---|---|---|
| **Ordre limite passif à l'entrée** (`titanium/execution/limit_orders.py`) | Stratégie d'exécution **réellement exécutée** sur le compte DEMO, mode `explore` | boucle `tools/live_demo.py` | en collecte depuis le 12/08 |
| **Matrice d'exécution `titanium.execution_sim`** | Laboratoire déterministe hors ligne, 15 politiques | `tools/backtest_execution_matrix.py` | run complet du 14/08, `live_enabled=false` verrouillé |

## 2. Stratégie live : limite passive au lieu du marché

Principe (`plan_limit_entry`) : prix passif au bid (achat) / ask (vente) ; si le spread dépasse
5 % du R initial, on exige jusqu'à 5 % de R d'amélioration supplémentaire. TTL dégressif selon
le poids du spread dans R : 600 s (spread_r ≤ 0,08), 300 s (≤ 0,15), 120 s au-delà.
L'ordre expire au lieu de poursuivre le prix. Aucun chase, aucun re-pricing.

### Mesures sur `results/limit_lifecycle.ndjson` (12/08 14:11 → 14/08 19:16 UTC)

- 189 ordres placés, 67 remplis, 124 expirés → **fill rate 35,4 %**.
- Économie visée moyenne : **0,123 R** ; spread moyen à l'entrée : **0,087 R**.
- Économie réalisée sur les fills : **+0,0996 R en moyenne** (médiane +0,098, min +0,012, max +0,181).
- **Slippage négatif ou nul sur 100 % des fills** (moyenne −0,0016 R) : aucun fill dégradé.
- Délai de fill : médiane 65 s, moyenne 267 s, max 4 804 s.
- 59 clôtures issues de ces limites : **−10,79 R cumulés**, moyenne **−0,183 R**, win 47,5 %, PF 0,61.

### Comparaison de cohortes (`results/trades.ndjson`, 100 dernières clôtures)

| Cohorte | n | P&L moyen | médiane | win | PF | coût moyen |
|---|---:|---:|---:|---:|---:|---:|
| Entrées limite remplies | 59 | −0,183 R | −0,300 | 47,5 % | 0,61 | 0,077 R |
| Entrées marché / autres | 41 | −0,374 R | −0,952 | 36,6 % | 0,36 | 0,093 R |

Écart cohérent en ordre de grandeur avec l'économie de prix mesurée (+0,10 R) et le coût
plus faible (−0,016 R). **Mais les deux cohortes perdent** : la limite améliore l'exécution,
elle ne crée pas d'edge. Le problème V14 reste l'alpha, pas l'exécution.

### Biais de sélection identifié (nouveau)

- Économie *visée* des ordres **remplis : 0,098 R** contre **0,136 R pour les expirés**.
- Spread à l'entrée des remplis : **0,072 R** contre **0,094 R** pour les expirés.

Autrement dit, on ne remplit que les cas où l'on demande peu et où le spread est déjà serré.
Les symboles chers (CN50, USDHUF, USDBRL, USDZAR, EURSGD, GBPNOK) accumulent les expirations
à 0–23 % de fill, les symboles serrés (WTI.fs, USTECH, EURUSD, XAUUSD, AUS200) remplissent
à 100 %. La rentabilité de la technique dépend donc du **coût d'opportunité des 124 non-fills**,
qui n'est toujours pas mesuré.

## 3. Laboratoire : matrice des 15 politiques (14/08)

Run `9cb2fb7c6175484c`, 12 960 cas, seed 14082026, 0 NaN, splits dev/validation/OOS.
Tête du classement robuste : cancel_replace 1,685 · pegged 1,625 · limit_passive 1,552 ·
post_only 1,475 · iceberg 1,461 · market 1,426. Queue : TWAP 0,093 · market_making −0,123 ·
multi-leg simultané −2,351.

Classement de compatibilité V14 (MT5, données disponibles) : market 92,9 · limit_passive 73,3 ·
post_only 71,8 · IOC 71,4 · cancel_replace 71,3.

Lecture : la première place de cancel_replace n'est **pas** une preuve — sa fidélité modèle
n'est que 0,50 et dépend du modèle de file/replacement. Peg, iceberg, market making et
multi-jambes restent **data-gated** (pas de L2/trades synchronisés persistés).

Trio retenu pour la suite : `market` (témoin) → `limit_passive` (déjà en DEMO) →
`adaptive` maker→taker (candidat shadow uniquement).

Tests : `83 passed` sur les 7 fichiers `test_execution_sim_*` + `test_limit_orders.py`
(.venv, 1,27 s).

## 4. Verdict

- **Techniquement GO** : la limite passive fonctionne, ne glisse jamais, économise ~0,10 R
  par fill et journalise tout son cycle de vie.
- **Économiquement NO-GO / non conclu** : −10,79 R sur 59 clôtures. L'économie d'exécution est
  réelle mais absorbée par un alpha négatif, et le fill rate de 35 % est structurellement
  sélectif.
- Aucun changement de TTL ni de distance n'est justifié aujourd'hui (confirme le verdict
  Hermes du 13/08 : les époques ne discriminent pas durée vs distance).

## 5. Prochaine mesure décisive (manquante)

Pour chaque intention : markout du marché à l'expiration, P&L contrefactuel d'une exécution
marché conservatrice, excursion prix entre placement et expiration, distance normalisée par
spread et volatilité, délai de fill, `runtime_epoch_id` natif. La métrique juste est
**intention-to-trade** = amélioration de prix des fills − sélection adverse − coût
d'opportunité des non-fills. Le fill rate seul ne mesure aucune rentabilité.

## Sources

`results/limit_lifecycle.ndjson` (439 événements) · `results/trades.ndjson` (100) ·
`results/loop_heartbeat.json` · `docs/RAPPORT_EXECUTION_MATRIX_V14.md` ·
`docs/EXECUTION_SIM_V14.md` · `docs/AUDIT_EXECUTION_MODULAIRE_V14.md` ·
`config/execution_backtest.json` · `collab/HERMES_LIMITES_PAR_EPOQUE_20260813.md`.

Note : `tools/gitnexus_team.ps1 sync` a dépassé 600 s et a été interrompu ; mission en
lecture seule, aucun symbole édité, donc sans impact sur les conclusions.
