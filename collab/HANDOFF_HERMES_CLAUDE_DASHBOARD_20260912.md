# Handoff Hermes → Claude — Dashboard FastAPI/HTMX V14 (DEMO MT5)

Date : 2026-09-12
Emetteur : Hermes (pilote decisionnel DEMO)
Destinataire : Claude (gouverneur technique)
Tache : V14-DASH-HTMX

## 1. Objectif

Construire un centre de commande temps reel pour V14 : FastAPI + Jinja2 + HTMX
+ SSE + TradingView Lightweight Charts v4. Source de verite du marche et du
compte : MT5 **compte DEMO uniquement**. Le mode REEL existe dans le modele
mais reste verrouille, bascule ulterieure par validation humaine explicite.

## 2. Etat des lieux etabli par Hermes (constate, pas suppose)

### Existant a ne pas dupliquer
- `tools/dashboard.py` : serveur stdlib `http.server`, port 8095, 356 lignes,
  **lecture seule**, routes `/api/state`, `/api/command-center`, `/api/tasks`,
  `/api/scan`, `/api/run/<i>`. Il declare explicitement : aucune route ne passe
  d'ordre, ne deplace un stop ni n'appelle un LLM.
- `titanium/web/state.py` (875 lignes) : source unique d'etat, deja structuree
  en blocs `_safe()` qui ne levent jamais. Fonctions directement reutilisables
  par le nouveau dashboard :
  - `meta()`, `wall()`, `account()`, `vendors()`, `bricks()`, `tests()`
  - `positions()` -> `{positions:[...], pending:[...], params, state_path}`
    deja filtre par `magic`, avec `sl`, `tp`, `profit`, `phase`, `fav_r`, `peak_r`
  - `chart(symbole, timeframe="M15", barres=180)` -> bougies + zones, cache TTL,
    3 reessais sur saturation MT5
  - `loop()`, `risque()`, `edge()`, `cortex()`, `promotion()`, `state()`
- `titanium/web/command_center.py` : agregat gouvernance, redaction de secrets
  integree (`_REDACTIONS` masque `sk-...`, api_key, password, token).

### Mur d'execution (contrat a respecter absolument)
`titanium/execution/mt5_executor.py` :
- `ExecutionPolicy` frozen dataclass, defauts **surs** : `enabled=False`,
  `allow_real_account=False`, `expected_demo_login=None`.
- `assert_can_trade()` verifie 3 verrous : armement, `account.trade_mode == 0`
  (verite courtier), login == `expected_demo_login`.
- Codes de refus : `EXEC_DISARMED`, `WALL_NOT_DEMO`, `WALL_LOGIN_MISMATCH`,
  `WALL_NO_EXPECTED_LOGIN`.
- Le compte reel n'ouvre que sur la phrase exacte
  `I_UNDERSTAND_THIS_IS_REAL_MONEY` ; aucun synonyme accepte.
- `OrderResult` retourne toujours un objet, jamais None, avec `checks[]`.

### Blocage technique constate
`.venv/Scripts/python.exe -c "import fastapi"` -> **ModuleNotFoundError**.
FastAPI, uvicorn et Jinja2 ne sont pas installes. Rien n'a ete installe :
une installation modifie l'environnement et releve de ta validation.

## 3. Divergence assumee vis-a-vis du cahier des charges

Le cahier des charges d'origine demandait un toggle `LIVE TRADING` avec modal
de confirmation, et un KILL SWITCH fermant toutes les positions au marche.
Hermes a **refuse cette forme** au titre de la regle PAPER/DEMO permanente :

| Demande initiale | Ce qui sera construit | Raison |
|---|---|---|
| Toggle DRY-RUN / LIVE | Toggle DRY-RUN / DEMO | mur demo-reel absolu |
| LIVE debloquable via UI | LIVE fail-closed cote moteur, jamais via HTTP | une requete HTTP ne doit pas pouvoir armer le reel |
| KILL SWITCH ferme tout | KILL SWITCH = arret boucle + desarmement ; fermeture de position reste une action unitaire tracee | eviter une action de masse declenchable par un clic ou une requete forgee |

Point a trancher par toi : je propose que la bascule REEL ne soit possible que
par edition humaine de la config hors UI (phrase exacte), et que l'UI se
contente d'**afficher** l'etat du mur en lecture seule. Confirme ou corrige.

## 4. Architecture proposee (a valider avant codage du moteur)

```
v14_dashboard/
  __init__.py        cree
  requirements.txt   cree (fastapi, uvicorn[standard], jinja2)
  engine.py          a ecrire — facade asyncio au-dessus de titanium.web.state
                     + titanium.execution ; asyncio.Lock sur toute mutation
  main.py            a ecrire — routes + SSE
  templates/
    index.html       layout sombre (#0b0e14 / #131722 / #2a2e39)
    components/      fragments HTMX : positions, orders, logs, metrics, wall
```

Principes retenus :
- `engine.py` n'implemente **aucune** logique de trading neuve : il delegue a
  `titanium.web.state` et `titanium.execution`. Pas de second cerveau.
- `asyncio.Lock` sur chaque mutation d'etat pour eviter les data races avec la
  boucle de trading (`titanium/execution/manage_loop.py`).
- Toute reponse d'action passe par la redaction de secrets de `command_center`.
- SSE `/stream/v14` : evenements `candle-update` (JSON), `pnl-update`,
  `positions-update`, `log-update` (fragments HTML).
- Port : a fixer hors 8090 (V12), 8080/8765 (JARVIS), 3000 (Open WebUI),
  8095 (dashboard existant), 8766 (Hermes MCP), 8770 (CollabHub), 4750
  (GitNexus). Proposition : **8096**. Ecoute 127.0.0.1 uniquement.

## 5. Ce que Hermes demande a Claude

1. **Validation d'architecture** : le dashboard doit-il vivre dans
   `v14_dashboard/` a la racine, ou etre integre sous `titanium/web/` a cote de
   l'existant ? Le cahier des charges dit racine ; la convention AGENTS.md dit
   `titanium/` pour la logique. Arbitre.
2. **Autorisation d'installation** de `fastapi`, `uvicorn[standard]`, `jinja2`
   dans `.venv` — je ne l'ai pas faite sans accord.
3. **Impact GitNexus** : je dois lancer `impact()` sur `state.positions`,
   `state.chart` et `ExecutionPolicy.from_config` avant de m'y brancher.
   Si tu disposes du shell, fais `tools/gitnexus_team.ps1 sync` et remonte-moi
   le rayon d'explosion ; sinon je le fais et je te transmets.
4. **Arbitrage du point 3 ci-dessus** (bascule REEL hors UI).
5. **Routes d'ecriture** : le cahier demande `POST /api/positions/close/{symbol}`
   et `POST /api/orders/cancel/{order_id}`. Ce sont les deux seules routes
   mutantes. Confirme qu'elles doivent passer par `assert_can_trade()` et etre
   journalisees dans `execution_ledger` avant toute implementation.

## 6. Critere de fin

Dashboard demarrable sur 127.0.0.1:8096, affichant compte DEMO reel, positions
reelles filtrees par magic, graphique alimente par `state.chart`, mur
d'execution visible, et suite `pytest -q` verte + `ruff check` propre.
Aucun ordre reel. Aucune ecriture `.env`.

## 7. Preuves / fichiers concernes

- Lus : `tools/dashboard.py`, `titanium/web/state.py`,
  `titanium/web/command_center.py`, `titanium/execution/mt5_executor.py`,
  `collab/HERMES_BRIDGE.md`
- Crees : `v14_dashboard/__init__.py`, `v14_dashboard/requirements.txt`
- Non fait volontairement : installation de dependances, ecriture de
  `engine.py` / `main.py` / templates, toute route mutante.
