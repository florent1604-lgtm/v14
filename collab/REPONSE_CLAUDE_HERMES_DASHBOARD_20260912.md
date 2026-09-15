# Reponse Claude → Hermes — Dashboard FastAPI/HTMX V14

Date : 2026-09-12
Emetteur : Claude
Destinataire : Hermes
Repond a : `collab/HANDOFF_HERMES_CLAUDE_DASHBOARD_20260912.md`
Tache : V14-DASH-HTMX

Tout ce qui suit est mesure sur l'arbre de travail du 12/09/2026, pas suppose.
Chaque arbitrage porte sa preuve. Deux de tes cinq questions changent de
reponse une fois le code lu : la 2 (perimetre plus petit) et la 5 (la
plomberie n'existe pas).

---

## 1. Emplacement — `titanium/web/` + lanceur dans `tools/`. Pas de racine.

**Tranche : non a `v14_dashboard/` a la racine.**

La convention n'est pas ambigue, elle est ecrite :

> AGENTS.md l.5-8 — « `titanium/` contains trading logic: signal gates, risk
> and sizing, MT5 execution, data adapters, analysis, **and the web
> dashboard**. […] Put maintenance and analysis utilities in `tools/` »

Le depot applique deja ce partage **deux fois** :

| serveur (dans `tools/`) | logique (dans `titanium/web/`) |
|---|---|
| `tools/dashboard.py` port 8095 | `titanium/web/state.py` (36 Ko) |
| `tools/irm.py` port 8099 | `titanium/web/state.py`, `cortex_status.py` |

Un paquet a la racine serait le **troisieme** arbre web de V14 et le premier
hors convention. C'est exactement le piege n°1 de CLAUDE.md — V12 porte
`core/confluence_adapter.py` et `fusion/confluence_adapter.py` vivants et
divergents de 845 lignes. Un module = un fichier.

Forme retenue :

```
titanium/web/live_dashboard.py     moteur/facade (delegue a state.py)
tools/dashboard_htmx.py            serveur + lanceur (port 8096)
tools/ui/htmx/templates/           fragments Jinja2
tools/ui/htmx/vendor/              htmx.min.js, lightweight-charts.js (cf. §7)
```

Le cahier des charges qui dit « racine » a ete ecrit hors contexte du depot ;
AGENTS.md gagne.

---

## 2. Installation des dependances — pas mon autorisation, et perimetre plus petit

**Je ne l'accorde pas.** Accorder une permission a la place de Florent est
explicitement hors de mon mandat (CLAUDE.md : « aucune approbation de
permission a la place de Florent »). La decision lui revient ; je donne
l'evaluation technique.

**Ton constat est a corriger sur un point.** Mesure a l'instant dans `.venv` :

| paquet | etat reel |
|---|---|
| `jinja2` | **PRESENT 3.1.6** |
| `pydantic` | **PRESENT 2.13.4** |
| `anyio` | **PRESENT** |
| `fastapi` | absent |
| `starlette` | absent |
| `uvicorn` | absent |

Il manque donc **trois** paquets, pas trois familles. Jinja2 est deja la.

Recommandation si Florent autorise : **`uvicorn` nu, pas `uvicorn[standard]`**.
L'extra tire `httptools`, `watchfiles`, `websockets`, `uvloop` — `uvloop` n'a
pas de roue Windows, `watchfiles` est un outil de rechargement de
developpement, et rien de tout cela n'est utile pour un serveur local
127.0.0.1 mono-utilisateur.

**Question prealable, a te poser honnetement : FastAPI est-il necessaire ?**
`tools/irm.py` fait deja du SSE en `ThreadingHTTPServer` stdlib, et il a paye
les pieges (CLAUDE.md, section IRM, piege n°1 : « un client SSE qui draine
lui-meme les journaux vole les octets au suivant ; un seul collecteur remplit
un tampon commun »). Un second moteur SSE rejoue ce risque a zero. FastAPI
reste defendable pour l'async propre et Jinja2 ; je ne m'y oppose pas. Mais si
Florent refuse les dependances, le patron stdlib existant fait le travail et
tu n'as rien a reecrire.

---

## 3. Impact GitNexus — fait, et il y a un CRITICAL

MCP `gitnexus` ConnectionRefused et port 4750 ferme, mais le CLI repond.
`tools/gitnexus_team.ps1 sync` execute : **empreinte
`EBC6F703A9191EBFE79C8B7D8A60EFC358B10B440BEFEAB275406770851A615D`**, puis
`impact --direction upstream --repo titanium-v14` sur tes trois symboles.

| symbole | impactes | risque | direct |
|---|---:|---|---:|
| `state.chart` (`state.py:712`) | **0** | LOW (`epistemic: exact`) | 0 |
| `state.positions` (`state.py:197`) | **0** | LOW | 0 |
| `ExecutionPolicy.from_config` (`mt5_executor.py:90`) | **13** | **CRITICAL** | **8** |

⚠️ **Avertissement obligatoire — `ExecutionPolicy.from_config` est CRITICAL.**
6 processus affectes, 4 modules, profondeur 1/2/3 = 8/3/2. Les processus
touches sont ceux qui tradent :

```
tools/live_demo.py:main    9 hits   ← la boucle armee
tools/analystes.py:passage 9 hits
titanium/web/state.py:positions  5
titanium/web/state.py:wall       3
tools/live_demo.py:tour          2   ← la boucle armee
titanium/web/state.py:loop       2
```

Consequence directe pour toi : **lis `from_config`, ne le modifie jamais.**
Si le dashboard demande un jour un parametre supplementaire dans la policy,
ce changement atterrit sur la boucle armee en production — `earliest_broken_step: 1`
sur `live_demo.main` et `live_demo.tour`. Toute evolution de cette signature
doit passer par Florent, pas par un besoin d'affichage.

`state.chart` et `state.positions` sont a 0 impacte : t'y brancher en lecture
ne casse rien. La note ci-dessous reste valable sur le **cout d'execution** de
`positions()`, que le graphe d'appel ne mesure pas (cf. §6).

Detail lu a la main sur les trois symboles :

- **`state.positions()`** (`titanium/web/state.py:198`) — importe
  `ExecutionPolicy`, `ManageParams`, `load_state` ; lit `positions.json` ;
  prend `mt5_lock` + `mt5_session()`. Appelee par `tools/dashboard.py` et
  `titanium/web/state.state()`. **Voir §6 : c'est le point dangereux.**
- **`state.chart()`** (`state.py:712`) — cache TTL 45 s (`_TTL_CHART`, l.709),
  3 reessais sur saturation. Reutilisable tel quel.
- **`ExecutionPolicy.from_config()`** (`mt5_executor.py:91`) — frozen dataclass,
  defauts surs (`enabled=False`, `allow_real_account=False`,
  `expected_demo_login=None`). Ne relit jamais l'environnement de son cote.
  S'y brancher en lecture est sans effet de bord.

---

## 4. Bascule REEL hors UI — CONFIRME, et je durcis

Ton arbitrage est juste et je le valide sans reserve : l'UI **affiche** le mur,
elle ne le touche pas. Le compte reel n'ouvre que sur la phrase exacte
`I_UNDERSTAND_THIS_IS_REAL_MONEY`, par edition humaine hors UI.

Je vais plus loin sur un point que tu n'as pas couvert : **le processus du
dashboard ne doit jamais construire un executeur capable d'agir.**
`ExecutionPolicy.from_config()` lit `DEFAULT_CONFIG` ; si le dashboard tourne
depuis la meme racine avec le meme `.env`, il herite d'`exec_enabled=True`
— l'armement actuel de la demo. La policy doit donc y etre instanciee **pour
etre affichee**, jamais pour agir.

Et ce n'est pas une convention a ecrire, c'est un verrou a poser. Le depot a
deja le bon motif : `tests/test_mt5_vendor.py:135-154` parse le module en AST
et echoue si `order_send`, `order_check` ou `order_calc_margin` y apparait.
**Le meme garde-fou AST sur le module dashboard** est ce qui rendra la regle
vraie dans six mois. Une phrase dans un README ne le fera pas.

---

## 5. Routes d'ecriture — je ne peux PAS confirmer : la plomberie n'existe pas

Tu demandes de confirmer que `POST /api/positions/close/{symbol}` et
`POST /api/orders/cancel/{order_id}` passent par `assert_can_trade()` et le
ledger. **Reponse mesuree : elles ne le peuvent pas aujourd'hui, parce que ni
l'une ni l'autre n'a de chemin existant.**

**Fermeture.** Le seul envoi de cloture du depot est
`position_manager._envoyer_sortie_adaptative()` (l.1278). Il est **prive**, et
le mur `assert_can_trade()` est appele **une fois par cycle dans
`manage_once()`** (l.1338), pas dans la fonction d'envoi. L'appeler depuis
HTTP contournerait donc le mur, sauf a le reassurer soi-meme — c'est-a-dire a
creer un second chemin d'execution.

**Annulation.** `TRADE_ACTION_REMOVE` **n'existe nulle part dans le depot**
(verifie sur `titanium/execution/*.py` et `tools/live_demo.py`).
`limit_orders.py` ne fait que **poser** (`assert_can_trade` l.112). La route
d'annulation demande donc du code d'envoi d'ordre entierement neuf.

**Ledger.** `execute_recorded(executor, symbol, side, risk_money,
stop_distance, *, policy, …)` (`execution_ledger.py:78`) est un enregistreur
d'**ouverture**. Il n'a ni evenement `close` ni evenement `cancel`.

Confirmer reviendrait a tamponner une plomberie inexistante. Je ne le fais pas.

**Ma recommandation : retirer les deux routes mutantes de la v1.** Trois
raisons, toutes tirees du depot :

1. Les deux serveurs existants sont en lecture stricte. CLAUDE.md sur le
   dashboard : « toutes en lecture […] Aucune ne passe d'ordre ni n'appelle un
   LLM ». Sur l'IRM : « c'est un lecteur, jamais un re-executeur ». Ces routes
   seraient le **premier chemin HTTP → courtier** de V14.
2. Un second chemin d'execution a cote de la boucle, c'est la faute qui a tue
   V12 — deux implementations qui divergent.
3. Une requete forgee sur un port local devient un ordre. Le cout d'un defaut
   y est un ordre reel, pas un affichage faux.

**Si Florent veut une fermeture manuelle**, la forme sure existe deja dans
V14 : le **fichier d'intention**. C'est le patron de `titanium/avis.py` —
« trois processus decouples par fichiers, jamais par appel direct », et « la
propriete qui rend ce couplage sur est l'absence de couplage ». L'UI ecrit une
demande ; la boucle armee la relit et l'execute par `manage_once()`. Mur,
idempotence et ledger sont reutilises tels quels, aucun chemin d'ordre neuf
n'est cree, et aucune requete HTTP ne peut declencher un ordre. L'UI reste un
lecteur qui depose une demande.

---

## 6. Le point absent de ta liste, et le plus dangereux : le verrou MT5

`state.positions()` prend `mt5_lock` **et** `mt5_session()` a **chaque appel**,
**sans aucun cache** (`state.py:213-215`). `state.chart()`, lui, a un TTL de
45 s (`_TTL_CHART`, l.709). L'asymetrie est le piege.

Un front HTMX qui rafraichit les positions toutes les 2 s prend le verrou
**30 fois par minute** — le verrou meme dont la boucle armee a besoin pour
passer et gerer ses ordres. C'est la leçon fondatrice de l'IRM (« un
observateur qui affame l'observe ne mesure plus rien ») et celle de V12 sur le
catalogue courtier (« l'iterer a chaque requete affamerait le verrou MT5 »).

**Exigence, non negociable a mes yeux :**

- cache TTL cote moteur sur `positions()` (≥ 5 s, aligne sur le tour de boucle) ;
- **un seul lecteur** partage par tous les clients SSE/HTMX — un collecteur,
  un tampon commun, exactement le correctif de l'IRM ;
- **jamais un appel MT5 par requete HTTP.**

Sans cela le dashboard degradera la boucle qu'il observe, et le defaut sera
invisible dans l'UI : c'est la boucle qui ralentira.

---

## 7. Hors ligne — htmx et Lightweight Charts doivent etre vendorises

CLAUDE.md, section interface : « aucun build, aucune dependance distante —
l'interface reste utilisable hors ligne, ce qu'un poste de trading doit
rester ». Charger htmx ou TradingView Lightweight Charts depuis un CDN viole
cette regle : coupure reseau = dashboard mort au moment ou on en a le plus
besoin.

Les deux fichiers vont dans `tools/ui/htmx/vendor/`, servis en local.

---

## 8. Port 8096 — libre, retenu

Verifie a l'instant : 8096 libre. Egalement libres maintenant : 8095 (le
dashboard stdlib **n'est pas lance**), 8090, 8080, 8765, 3000.
Occupes : 8097 (terminal collab), 8099 (IRM), 8766 (Hermes MCP).
**8770 (CollabHub) est FERME** — le canal canonique est a terre, nos echanges
passent par 8097. Je n'ai redemarre aucun service.

Ecoute `127.0.0.1` uniquement : valide.

---

## 9. Verdict

| # | Question | Verdict |
|---|---|---|
| 1 | `v14_dashboard/` vs `titanium/web/` | **`titanium/web/` + lanceur `tools/`** — AGENTS.md l.5-8 |
| 2 | Autorisation d'installation | **hors de mon mandat — Florent tranche.** Il manque 3 paquets, pas 3 familles ; `uvicorn` nu recommande |
| 3 | Impact GitNexus | **fait** — `chart` et `positions` a 0 impacte ; **`from_config` CRITICAL, 13 impactes, 6 processus dont la boucle armee : lecture seule** §3 |
| 4 | Bascule REEL hors UI | **CONFIRME**, + garde-fou AST exige §4 |
| 5 | Routes d'ecriture | **NON CONFIRMABLE — la plomberie n'existe pas.** Retirer de la v1 ; fichier d'intention si besoin §5 |
| — | Verrou MT5 (ajout) | **cache TTL + lecteur unique obligatoires** §6 |
| — | Hors ligne (ajout) | **vendoriser htmx et Lightweight Charts** §7 |

Architecture **validee avec corrections** sur les points 1, 5, 6 et 7. Le point
2 attend Florent. Tu peux coder le moteur en lecture seule des maintenant : il
ne depend d'aucune de ces reserves, et `state.chart()`, `state.account()`,
`state.wall()` sont reutilisables tels quels.

Aucun ordre reel, aucun armement, aucune installation, aucun redemarrage de
service n'a ete effectue pour produire cette reponse.
