# Hermes : pilote decisionnel V14 — integration du 6 septembre 2026

## Comportement implemente

Les organes Titanium calculent les candidats sur l'univers disponible et les
horizons configures. Hermes recoit leur actif, direction, contexte/horizon,
observations compactes, cout du spread, statistiques de memoire et predictions
Market-JEPA lorsqu'elles sont disponibles. Il choisit ALLOW, WAIT ou BLOCK.
Les positions recoivent CALM, CAUTION, FEAR, PANIC ou UNKNOWN. Deux verdicts
de peur distincts et qualifies restent necessaires pour une sortie cognitive.

La boucle ne lance aucun appel LLM. Elle relit une politique Hermes scellee,
avec controle du producteur, modele, contexte et expiration. Une reponse Qwen
historique ne peut plus ouvrir une position ni declencher une sortie de peur.
Une politique BLOCK recente annule les ALLOW precedents meme apres expiration.
Les politiques anciennes sans le nouveau schema sont rejetees, pas reecrites.

Les demandes depassees ne sont pas envoyees au modele. M1, M5 et autres horizons
ont respectivement 60, 120 et 300 secondes de validite maximale depuis la fin
de la barre source. La mise en file ne rajeunit pas cette observation.

## Connaissances effectivement transmises

`titanium/organism/trading_knowledge.py` fournit un petit catalogue versionne
joint a chaque analyse, adapte a crypto, FX, metaux, energie, indices/actions.
Il decrit tendance/repli, cassure/retest, retour a la moyenne, balayage de
liquidite, reaction a une publication et contexte de portage. Chaque methode
precise regime, observations necessaires et invalidation. La valeur relative
reste une hypothese exigeant des series synchronisees et des tests de stabilite.

Il s'agit d'un contexte de decision, pas d'un entrainement des poids du modele.
Aucune methode n'est promue sur sa seule description. La conviction du LLM
n'est pas interpretee comme une probabilite de gain calibree.

## Sources et fraicheur

- Le collecteur existant lit Binance, Bybit et OKX pour BTC/ETH. Ses snapshots
  locaux frais alimentent desormais aussi la collecte du cortex. Leur portee
  reste celle du spot/USDT externe, distinct du prix executable Axi/MT5.
- CoinGecko fournit le contexte de prix avec `last_updated_at`; cache 30 s.
- FRED et EIA conservent la date de l'observation et leur periodicite macro.
  Les cles sont optionnelles et n'ont pas ete lues ni modifiees.
- Les changes BCE passent par les references EUR. Un cross USDJPY est calcule
  seulement avec USD/EUR et JPY/EUR portant la meme date.
- Les RSS Fed/BCE sont mutualises entre actifs. Un echec est marque UNAVAILABLE
  et retente apres une pause courte. Il n'est jamais remplace par un fait invente.
- CURRENT_CONTEXT, STALE, UNKNOWN_TIME et FUTURE_TIME accompagnent les faits.
  Une publication quotidienne ou hebdomadaire ne constitue pas un tick frais.

Documentation officielle : [FRED](https://fred.stlouisfed.org/docs/api/fred/),
[EIA API v2](https://www.eia.gov/opendata/documentation.php),
[BCE SDMX](https://data.ecb.europa.eu/help/api/data-examples).
La [documentation Coinbase WebSocket](https://docs.cdp.coinbase.com/exchange/websocket-feed/channels)
decrit une extension possible level2/heartbeat; elle n'est pas encore branchee.

## Apprentissage et prochaines extensions

La memoire des resultats existante est preservee. L'adaptation doit etre evaluee
par actif, regime et horizon sur les clotures MT5 reconciliees nettes de couts,
avec effectif, drawdown et verification hors echantillon. Aucun nouvel
auto-entrainement ni changement autonome des seuils n'est revendique ici.

Les prochaines extensions mesurables sont la couverture microstructure au-dela
de BTC/ETH, un flux WebSocket avec controles de sequence, les calendriers de
publications par actif et la calibration des politiques sur les resultats DEMO.
Le chemin reflexe peut rester rapide; la generation distante d'Opus ne peut
pas etre annoncee instantanee. Mesurer separement collecte, attente, analyse,
lecture de politique et acquittement MT5.

## Verification

Tests de regression : `tests/test_cortex_authority.py`,
`tests/test_trading_knowledge.py`, `tests/test_hermes_cortex.py`,
`tests/test_live_intelligence.py` et `tests/test_position_sentiment.py`.
Le rapport de livraison donne les resultats observes et le commit exact.
