# Audit du RAPPORT_TECHNIQUE_V14_CLAUDE.md (Kimi, 15/08/2026)

Auteur : Prime Agent — 15/08/2026
Source auditée : `C:\Users\flore\Downloads\RAPPORT_TECHNIQUE_V14_CLAUDE.md`
Mode : lecture + exécution de tests. Aucune modification de code.

## Verdict

Le rapport demande de construire une couche d'exécution modulaire à 15 techniques
dans un moteur de backtest. **Cette couche existe déjà dans V14** et est livrée
depuis le commit `87bde89` (14/08/2026). Le rapport a été rédigé sur un instantané
GitHub antérieur : ses trois constats « ABSENT TOTAL » (§3.1, §3.2) sont faux sur
la racine locale.

## Preuve d'exécution

```
.venv\Scripts\python.exe -m pytest tests/test_execution_sim_*.py -q
62 passed in 1.22s
```

## Conformité point par point

| Exigence du rapport | État réel V14 | Écart |
|---|---|---|
| §3.1 moteur de backtest « absent » | `titanium/execution_sim/` (2 262 lignes, 12 modules) + `titanium/backtest.py` (rejeu OHLCV) | constat faux |
| §3.2 matching engine « absent » | `titanium/execution_sim/matching.py`, marche le carnet, fills partiels | constat faux |
| §5.3 order lifecycle | `models.py` / `oms.py` : états, transitions horodatées, fill pendant CANCEL_PENDING | conforme |
| §7 les 15 techniques | 15 classes + `POLICY_REGISTRY`, `ALL_POLICIES` | conforme |
| §8 configuration | `config/execution_backtest.json` (JSON, pas YAML), `live_enabled=false` verrouillé au chargement | conforme, format différent |
| §9 runner + parallélisme | `tools/backtest_execution_matrix.py`, `--policy all`, `--quick`, `--jobs` ; test parallèle == séquentiel | conforme (threads, pas process) |
| §10 matrice de régimes + splits | 864 scénarios × 3 tiers (development / validation / final_oos), 12 960 cas | conforme |
| §11 métriques | `metrics.py` : perf, coût, qualité, sélection adverse, multi-jambes, Pareto, 2 classements | conforme |
| §12 tests (20 attendus) | 62 tests, dont non-lookahead VWAP, reproductibilité par seed, reduce-only, kill switch, régression baseline | dépasse la demande |
| §13 verrous sécurité | refus si `live_enabled` ≠ false ; aucun import MT5 dans `execution_sim` | conforme |
| Livrables 6/8 : Parquet, HTML | CSV + JSON + Markdown seulement | **manquant (cosmétique)** |
| Livrables 12/13/14 : `docs/data_limitations.md`, `docs/reproduction.md`, `docs/params_reference.md` | contenu présent dans `docs/EXECUTION_SIM_V14.md` et `docs/RAPPORT_BACKTEST_15_POLITIQUES.md`, pas sous ces noms | **manquant (cosmétique)** |
| §5.1 DataFeed MT5/CSV/Parquet + alpha réel (`confluence_gate`, `riskgate`) | la matrice tourne sur snapshots **synthétiques** (`symbol="SYNTH"`, 12 snapshots) avec une intention unique | **écart réel, voir ci-dessous** |

## Le seul écart qui compte

La matrice est un laboratoire A/B contrôlé, pas un backtest sur données réelles.
Ce n'est pas un oubli d'implémentation : `docs/RAPPORT_BACKTEST_15_POLITIQUES.md`
§9-10 établit qu'aucune quote n'était archivée au 15/08 après-midi. Voir
l'ERRATUM ci-dessous : l'archive L1 Axi a été livrée le soir même (`523160f`),
l'enregistreur du carnet L2 Binance reste absent.

Conséquence : brancher un DataFeed réel comme le demande le rapport §5.1 ne
produirait rien de plus fidèle aujourd'hui, faute de L1 horodaté. Le geste
bloquant est l'archivage, pas le moteur.

De plus, le rapport ignore la contrainte de place de marché : Axi est un dealer
CFD sans carnet central (`market_book_add` → `False`). `pegged`, `iceberg`,
`post_only` et `market_making` — 4 des 15 techniques demandées — sont
**inexécutables sur le compte où V14 trade**, quel que soit le simulateur.

## ERRATUM (16/08/2026, avant travaux)

La phrase « `copy_ticks_from`, `copy_ticks_range`, `orderbook_ws` et `aggTrade`
n'apparaissent dans aucun `.py` de la racine V14 » est **fausse pour la moitié
MT5**. Un premier `rglob` avait rendu zéro résultat ; une seconde vérification
par `git grep` — méthode indépendante — donne :

```
git grep -c copy_ticks -- tools/ titanium/
tools/enregistreur_quotes.py:3
```

`tools/enregistreur_quotes.py` (236 lignes) et `tests/test_enregistreur_quotes.py`
(196 lignes, 8 tests verts) ont été livrés au commit `523160f` du 15/08/2026
20:13. L'archive L1 Axi **existe donc déjà** : `results/quotes/BTCUSD/` et
`results/quotes/ETHUSD/` contiennent 3 521 ticks réels du 15/08.

Reste vrai après revérification : aucun `.py` de V14 ne contient `orderbook_ws`,
`aggTrade` ni `depth@`. **L'enregistreur du carnet L2 Binance est bien absent.**

Leçon de méthode : un résultat de recherche négatif n'est pas une preuve tant
qu'une seconde méthode ne le confirme pas. Toute affirmation d'absence doit être
doublée par `git grep`.

## Recommandation

1. Classer le rapport comme obsolète : sa mission principale est faite et dépassée.
2. Ne pas renommer `titanium/execution_sim/` en `titanium/backtest/` : coût sans gain,
   et `titanium/backtest.py` occupe déjà le nom pour le rejeu historique.
3. Si Florent veut la conformité littérale des livrables : ajouter export Parquet+HTML
   et les trois fichiers `docs/` (≈ une demi-journée, valeur informative faible).
4. Priorité réelle inchangée : **archiveur de ticks L1 Axi** et **enregistreur du carnet
   L2 Binance déjà reçu et jeté**. C'est le seul travail qui crée de la donnée
   irremplaçable ; chaque jour sans lui est perdu.

Aucune de ces conclusions n'autorise un passage live.
