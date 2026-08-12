# Prime Agent — harnais de développement de V14

**Dépôt** : [`PrimeIntellect-ai/prime-agent`](https://github.com/PrimeIntellect-ai/prime-agent)
(MIT, publié le **05/08/2026**, #1 GitHub Trending le 07/08). Version installée ici :
**0.7.1** (canal `stable`).

Installé le **09/08/2026** sur ce poste, câblé sur la racine V14. C'est un **outil de
développement** : il écrit du code et de la documentation. Il n'a aucune autorité de
trading, ne passe aucun ordre, et n'est câblé à aucune boucle d'exécution de V14.

## Pourquoi celui-là

Deux abstractions le distinguent d'un assistant de code classique, et les deux servent
directement le chantier V14 :

- **RLM (Recursive Language Model)** — le modèle ne dispose que d'un outil : un **kernel
  IPython persistant**. Le contexte devient des variables Python ; les sous-agents sont des
  appels de fonction (`await rlm("audite les portes", name="audit-portes")`). Les variables,
  imports et résultats **survivent aux tours et à la compaction**. Conséquence pratique :
  auditer 42 fichiers `titanium/` ne consomme plus 42 lectures dans le contexte du parent —
  l'agrégation se fait en Python, seul le résultat remonte.
- **Continual harness** — prompts complémentaires, mémoires, descriptions de skills et
  spécifications de sous-agents sont un **état durable** (`/refine`), pas un prompt jetable.
  Ce qui est appris sur V14 une fois reste acquis pour les sessions suivantes.

Ce que ça change pour V14 : la recherche (comparer V12↔V14, balayer des backtests, croiser
des contextes mesurés) devient parallélisable et bornée, au lieu d'être une suite de
lectures qui saturent une fenêtre de contexte.

## Ce qui a été installé

| Élément | Emplacement |
|---|---|
| Binaire global | `C:\Users\flore\AppData\Roaming\npm\prime-agent{,.cmd,.ps1}` |
| Paquet npm | `C:\Users\flore\AppData\Roaming\npm\node_modules\prime-agent` |
| État global (auth, sessions, logs) | `C:\Users\flore\.prime\agent\` |
| Venv du kernel IPython | `C:\Users\flore\.prime\agent\kernel-venv\` (Python 3.11) |
| Réglages projet V14 | `.prime\agent\settings.json` |
| Skill projet V14 | `.prime\agent\skills\v14-boucle-dev\SKILL.md` |
| Lanceurs | `PRIME_V14.bat`, `tools\prime_agent_v14.sh` |

L'installeur officiel (`curl … install.sh | sh`) ne gère **que macOS et Linux**. Sous
Windows la distribution reste utilisable : c'est un paquet npm. Ce qui a été fait, à
l'identique de ce que fait l'installeur :

```bash
curl -fsSL https://pub-728493de92a943e2a9b2d17b4719f318.r2.dev/stable          # -> v0.7.1
curl -fsSL .../releases/v0.7.1/prime-agent-0.7.1.tgz -o prime-agent-0.7.1.tgz
curl -fsSL .../releases/v0.7.1/SHA256SUMS
sha256sum prime-agent-0.7.1.tgz   # d68612c8…dcdb — conforme au SHA256SUMS publié
npm install -g ./prime-agent-0.7.1.tgz
```

Le paquet n'est **pas** sur le registre npm public (`npm view prime-agent` → 404) : la
distribution passe par le bucket R2 de Prime Intellect. Toujours vérifier le SHA256.

## Un bug Windows, et son contournement

Le bootstrap automatique du kernel IPython de la 0.7.1 invoque :

```
uv pip install --python C:\Users\flore\.prime\agent\kernel-venv\bin\python  ipykernel …
```

`bin/python` est le chemin **POSIX** d'un venv ; sous Windows c'est
`Scripts\python.exe`. L'installation échoue donc systématiquement, et l'agent répond
« the IPython kernel runtime failed to set up » — c'est-à-dire qu'il perd son unique outil.

Contournement appliqué (celui que la documentation officielle prévoit) : peupler le venv à
la main puis désigner son interpréteur.

```bash
uv pip install --python "C:\Users\flore\.prime\agent\kernel-venv\Scripts\python.exe" \
  ipykernel "C:\Users\flore\AppData\Roaming\npm\node_modules\prime-agent\dist\prime-agent-runtime" \
  dill requests httpx pyyaml tomli python-dotenv pandas numpy scipy \
  beautifulsoup4 lxml pydantic tyro nest-asyncio
```

> `prime-agent-runtime` n'existe pas sur PyPI : c'est le paquet **local** livré dans
> `dist/prime-agent-runtime` du paquet npm. L'installer par son nom échoue toujours.

Puis, variable d'environnement **utilisateur** (déjà posée) :

```
PRIME_AGENT_KERNEL_PYTHON = C:\Users\flore\.prime\agent\kernel-venv\Scripts\python.exe
```

⚠️ **Après chaque `prime-agent update`**, rejouer la commande `uv pip install` ci-dessus :
la variable désactive le bootstrap automatique, donc un runtime devenu obsolète ne se
répare pas tout seul. Symptôme : l'agent dit à nouveau que le kernel a échoué.

Vérification bout en bout effectuée : dans la racine V14, question « compte les `.py` sous
`titanium/` » → réponse `42`, confirmée par `find titanium -name "*.py" | wc -l`.

## Lancer

```
PRIME_V14.bat                     depuis l'explorateur, cmd.exe ou Windows Terminal
./tools/prime_agent_v14.sh        depuis Git Bash
```

Le lanceur se place à la racine V14, exporte les clés depuis le `.env`, fixe
`PRIME_AGENT_KERNEL_PYTHON`, choisit le fournisseur et le passe **explicitement**. Aucune
clé n'est recopiée sur disque.

Commandes hors session : `prime-agent list` (agents), `prime-agent status`,
`prime-agent doctor`, `prime-agent shutdown`, `prime-agent model list google`.
En session : `/login`, `/refine`, `/goal`, `/autonomous`, `/settings`.

### La fenêtre reste figée et n'accepte aucune touche

C'est arrivé à la première mise en service, le 09/08/2026. **Cause : l'interface était
lancée à travers Git Bash (`bash -c …`) depuis une console Windows.** Node n'obtient alors
pas de console : le TUI s'affiche, se rafraîchit, et ne reçoit jamais une touche.

Trois règles qui en découlent, toutes appliquées dans les lanceurs :

1. **Depuis cmd.exe / Windows Terminal / l'explorateur** : `PRIME_V14.bat` appelle
   `prime-agent` **directement**. Ne jamais réintroduire un `bash -c` autour de l'interface.
   Git Bash reste indispensable, mais comme *shell interne* de l'agent (`shellPath`).
2. **Depuis Git Bash (mintty)** : même problème en sens inverse — mintty ne fournit pas de
   console Windows à Node. `tools/prime_agent_v14.sh` interpose `winpty` automatiquement
   quand il détecte mintty et un mode interactif.
3. **`PRIME_V14.bat` doit rester en ASCII pur.** Un caractère accentué ou un tiret cadratin
   casse le parseur de cmd.exe ligne par ligne : des commandes s'exécutent tronquées, avec
   des messages du type `'ho' n'est pas reconnu`. Constaté, puis corrigé.

## Fournisseurs

Prime Agent lit `~/.prime/agent/auth.json` (0600) puis les variables d'environnement.
Fournisseurs disponibles ici :

| Fournisseur | Variable | État sur ce poste |
|---|---|---|
| **Anthropic (Claude Pro/Max)** | OAuth `auth.json` | ✅ **connecté le 09/08/2026 — fournisseur actif** (`claude-sonnet-5`) |
| Google Gemini | `GEMINI_API_KEY` | secours automatique si l'abonnement est déconnecté |
| Anthropic (clé API) | `ANTHROPIC_API_KEY` | clé **valide, solde à zéro** — câblée, inactive |
| OpenAI | `OPENAI_API_KEY` | clé présente, crédit à sec |
| Groq | `GROQ_API_KEY` | clé présente, non testée |

Le lanceur exporte **les deux** clés (`GOOGLE_API_KEY` → `GEMINI_API_KEY`, et
`ANTHROPIC_API_KEY`) depuis le `.env`. Aucune n'est recopiée ailleurs sur disque : le `.env`
reste la source unique, et `auth.json` n'a pas besoin d'être renseigné.

## Faire tourner Prime Agent sur Claude

Le catalogue Anthropic est bien servi (`prime-agent model list anthropic` liste
`claude-opus-5`, `claude-sonnet-5`, `claude-haiku-4-5`, …) : la clé est authentifiée. Mais
tout appel échoue :

```
Provider rejected the request (invalid_request_error, 400):
Your credit balance is too low to access the Anthropic API.
```

Vérifié le 09/08/2026 sur `claude-haiku-4-5`. Deux voies.

### Voie retenue — l'abonnement Claude Pro/Max (`/login`)

Aucune clé API, aucun crédit à acheter : Prime Agent embarque le fournisseur OAuth
`anthropic` / « Anthropic (Claude Pro/Max) ». **Trois étapes, à faire par Florent** — c'est
un OAuth interactif sur son propre compte, aucun agent ne peut le faire à sa place :

1. Lancer `PRIME_V14.bat`.
2. Taper `/login`, choisir **Anthropic (Claude Pro/Max)**.
3. Le navigateur s'ouvre, autoriser. (Si le navigateur ne s'ouvre pas, l'interface propose
   de coller le code manuellement.)

Le jeton est écrit dans `~/.prime/agent/auth.json` sous `"anthropic": {"type": "oauth", …}`
et se rafraîchit tout seul. **Il est prioritaire sur la variable d'environnement** : la clé
API morte du `.env` ne le masquera pas.

**Le lanceur bascule alors tout seul.** `tools/prime_agent_v14.sh` détecte cette entrée et
démarre sur `claude-sonnet-5`. Surcharges :

```
set PRIME_V14_CLAUDE_MODEL=claude-opus-5      pour changer le modèle par défaut
PRIME_V14.bat --provider google --model gemini-2.5-flash    pour revenir à Gemini
```

`Ctrl+P` bascule en cours de session entre les modèles Gemini et Claude (`enabledModels`).

> ⚠️ **C'est une décision de facturation, pas une décision technique.** Anthropic facture
> l'usage d'un abonnement Claude depuis un harnais tiers en **usage supplémentaire, au
> token** — ce n'est **pas** décompté du forfait Pro/Max. Sur un agent RLM qui délègue à des
> sous-agents, rien ne plafonne la consommation. Prime Agent affiche l'avertissement à
> chaque session (`warnings.anthropicExtraUsage`, laissé actif volontairement — ne pas le
> désactiver).

### Voie alternative — créditer la clé API

Console Anthropic → Plans & Billing. Aucune reconfiguration ensuite, la clé est déjà
exportée par le lanceur ; la bascule est immédiate :

```
PRIME_V14.bat --provider anthropic --model claude-sonnet-5
```

Tant que ni l'une ni l'autre n'est faite, le défaut reste **Gemini Flash**, qui fonctionne.

## Accès de développement autonome et garde-fous

Prime Agent lit automatiquement `AGENTS.md` et `CLAUDE.md` : les règles V14 s'appliquent à
lui comme aux autres agents. Le skill `.prime/agent/skills/v14-boucle-dev` les rappelle.

- Prime dispose d'un accès autonome à toute la racine V14 : code, tests, documentation,
  configuration non secrète, shell, réseau documentaire, dépendances et refactorings
  multi-fichiers. La politique autoritative est `.prime/agent/APPEND_SYSTEM.md`.
- Prime est code owner : il peut arbitrer l'architecture, intégrer les changements,
  réattribuer les tâches techniques et les clôturer après tests et rapport. Les revues des
  autres agents sont consultatives sauf demande contraire explicite de Florent.
- Il peut piloter le dashboard et les services locaux de collaboration après contrôle des
  PID/ports et vérification post-action.
- **PAPER/DEMO uniquement** — aucun ordre réel, l'exécuteur MT5 reste désarmé.
- `.env` et les valeurs secrètes sont **inaccessibles au modèle** ; les fournisseurs sont
  configurés par injection dans le lanceur.
- MT5, `live_demo`, la boucle de trading, l'élévation UAC et les actions hors V14 restent
  soumis à une demande humaine explicite distincte.
- Le kernel IPython **n'est pas un bac à sable** : il exécute du code avec les droits de
  l'utilisateur. Relire un skill tiers avant de l'installer.
- Mode `--autonomous` : toujours borné par la suite de tests du projet comme porte de
  sortie, jamais par une porte qui toucherait au trading.

```
prime-agent --autonomous \
  --autonomous-gate ".venv/Scripts/python -m pytest tests/ -q" \
  --autonomous-max-turns 12
```

## Ce que le kernel n'a pas

Le venv du kernel est **séparé** du `.venv` de V14 : `titanium` et `tradingagents` n'y sont
pas importables, volontairement (isoler l'agent de l'environnement de production du projet).
Pour exécuter du code V14, passer par un sous-shell :

```bash
%%bash
.venv/Scripts/python -m pytest tests/ -q
```

## Le repli silencieux de fournisseur — pourquoi le lanceur est explicite

Le quota gratuit Gemini est vite atteint : deux requêtes rapprochées suffisent à déclencher
`Provider rate limit exceeded (ApiError, 429)`. Laissé à lui-même, **Prime Agent bascule
alors en silence sur un autre fournisseur crédité d'`auth.json`** — ici Anthropic, dont le
solde est nul. L'utilisateur reçoit :

```
Provider rejected the request (invalid_request_error, 400):
Your credit balance is too low to access the Anthropic API.
```

…alors que le vrai problème est un quota Gemini. Diagnostic coûteux : le message accuse le
mauvais fournisseur, et rien dans l'interface ne dit qu'un repli a eu lieu.

D'où la règle : **les lanceurs passent toujours `--provider` et `--model` explicitement.**
Un échec devient lisible (`429` Gemini) au lieu de migrer vers un fournisseur qu'on n'a pas
choisi. Un `--provider` ou `--model` fourni à la main l'emporte sur ce choix.

## Le démon décide, pas le lanceur — deux pièges

Prime Agent ne fait pas tourner l'agent dans le processus qu'on lance : un **démon** est
démarré en arrière-plan et le client s'y attache. Le kernel IPython et les outils tournent
**dans le démon**, pas dans le client. Deux conséquences, découvertes à la mise en service :

1. **Le répertoire de travail est celui du démon.** Un `cd` du lanceur ne suffit pas :
   demander « compte les `.py` sous `titanium/` » renvoyait **0 au lieu de 42**, sans la
   moindre erreur. Une réponse fausse et silencieuse est bien pire qu'un échec. Les deux
   lanceurs passent donc **`--cwd`** explicitement.
2. **L'environnement est celui du démon au moment où il a démarré.** Modifier
   `PRIME_AGENT_KERNEL_PYTHON` puis relancer le client ne change rien tant qu'un ancien
   démon tourne : le client s'y rattache et hérite de l'ancien environnement. Après tout
   changement de variable :

```
prime-agent shutdown --force
```

   ⚠️ Cette commande **arrête aussi les agents longue durée** en cours. C'est pour cette
   raison que les lanceurs ne le font pas automatiquement.

## Deux venvs de kernel coexistent

| Venv | Origine | Python |
|---|---|---|
| `~/.prime/agent/kernel-venv` | créé par le bootstrap, peuplé à la main | 3.11 (uv) |
| `~/.prime/agent/kernel-win` | créé depuis le `.venv` de V14 | 3.12 |

**Seul `kernel-win` est fiable.** `kernel-venv` est le venv que le bootstrap gère lui-même :
**chaque tentative de bootstrap ratée le reconstruit à vide**, effaçant ce qu'on y avait
installé à la main. Ne rien y déposer, ne pas compter dessus.

Celui qui sert est désigné par la variable utilisateur `PRIME_AGENT_KERNEL_PYTHON`
(actuellement `kernel-win`). Les lanceurs ne l'écrasent pas si elle est déjà valide, et ils
**valident réellement** le candidat — pas « le fichier existe » mais « cet interpréteur
sait-il importer `rlm` ? ». C'est le seul test qui distingue un venv sain d'un venv vidé.

Aucun des deux n'a `titanium` ni `tradingagents` importables : pour exécuter du code du
projet, passer par `%%bash .venv/Scripts/python …`.

Le skill Python intégré `websearch` est désactivé dans `.prime/agent/settings.json`
(`bundledSkills.websearch: false`) : sans clé Serper il est inutilisable, et tout skill
Python déclenche une synchronisation du kernel dont on n'a pas besoin ici.

## Correctif du démon Windows 0.7.1 (10/08/2026)

Le démon officiel 0.7.1 ne pouvait pas établir durablement sa connexion avec ses
workers sous Windows. Trois causes indépendantes ont été reproduites puis corrigées sur
le tag stable `v0.7.1` (`95afd319a78ae017a41241d50b013d656a0685ce`) :

1. le socket worker pouvait livrer son premier frame avant l'installation du handler ;
   le client met maintenant le socket en pause jusqu'à l'installation du canal puis le
   reprend ;
2. la vérification `worker_auth` prend environ 3 à 4 secondes sur cette machine alors que
   le délai officiel était de 1 seconde ; sous `win32`, le délai est maintenant borné à
   10 secondes et au délai global de connexion ;
3. la compaction du journal appelait `fsync` sur un handle de répertoire après le renommage
   atomique ; Windows renvoie `EPERM` pour cette opération. Le fichier reste synchronisé et
   renommé atomiquement, mais le `fsync` du répertoire n'est exécuté que hors Windows.

Preuves : build complet du paquet stable, 6 tests du journal de reprise, mission Opus
répondant `PRIME_GLOBAL_OK`, démon persistant sur `\\.\pipe\prime-agent-daemon`, puis suite
V14 à **1499 tests passés, 2 ignorés**. L'ancien bundle global est conservé ici pour un
rollback récupérable :

```
C:\Users\flore\AppData\Roaming\npm\node_modules\prime-agent\dist\bundle.backup-20260810-064810
```

Une mise à jour de Prime peut écraser ce correctif local. Après chaque mise à jour, tester
une première mission puis une seconde sur le même démon et rechercher `worker_auth`,
`EPERM` et `fsync` dans `~/.prime/agent/logs/prime-agent-daemon.*.log`.
Le diff réapplicable est conservé dans `docs/PRIME_AGENT_WINDOWS_071.patch`.

## Stabilité des sessions Windows

`PRIME_V14.bat` exécute maintenant `tools/prime_agent_preflight.py` avant tout
lancement. Le preflight traite les deux causes du blocage observé le 12/08/2026 :

- un `session-leases/*.lock` dont le PID propriétaire est mort ou réutilisé ;
- un descripteur `daemon-workers/*/<worker>.json` qui désigne un worker mort.

La preuve ne repose pas sur le PID seul : Windows réutilise les PID. Le script
compare aussi `processStartId` à la date de création réelle du processus. Un
artefact illisible reste en place (fail-closed). Un artefact prouvé orphelin est
déplacé sous `~/.prime/agent/quarantine/<UTC>/`, jamais supprimé, afin de garder
un retour arrière et les journaux de diagnostic.

Sans argument explicite, le lanceur interroge ensuite `prime-agent list --json`.
S'il existe déjà une session `live` avec un worker `ready` sur la racine V14, il
fait `prime-agent attach <id>` au lieu de créer une seconde session. Si l'état
du daemon ne peut pas être prouvé, le lancement est bloqué plutôt que de risquer
un doublon. Des arguments explicites conservent le comportement de création
voulu, par exemple pour changer de modèle.

Diagnostic sans mutation :

```
.venv\Scripts\python.exe tools\prime_agent_preflight.py repair --dry-run
.venv\Scripts\python.exe tools\prime_agent_preflight.py active --cwd C:\Users\flore\Desktop\V14
```

Tests reproductibles : `tests/test_prime_agent_preflight.py`.

## Limites connues

- **Quota Gemini free tier** : `429` sur des requêtes rapprochées. Voir la section sur le
  repli silencieux ci-dessus.
- Au tout premier démarrage à froid, le premier client peut encore quitter sans afficher
  la réponse ; la mission suivante sur le même démon fonctionne. Le démon, l'authentification
  worker et le journal ne tombent plus en erreur. Ce défaut d'affichage initial reste à
  isoler dans une future mise à jour.
- Une mission dépassant l'intervalle de maintenance peut produire
  `Idle eviction sweep failed: Timed out draining daemon mutations for idle eviction` :
  le balayage d'éviction attend cinq secondes alors qu'une mutation longue est encore
  active. La mission et le worker continuent puis se ferment normalement ; ce warning est
  non bloquant mais doit être distingué des erreurs `worker_auth` et `EPERM` corrigées.
- La recherche web intégrée (skill `websearch`) exige une clé Serper — non configurée.
- L'installeur officiel ne prend pas Windows en charge : toute mise à jour par
  `prime-agent update` doit être suivie de la réparation du kernel décrite plus haut.
- **Plusieurs agents modifient cette installation.** `auth.json`, `models.json`,
  `kernel-win/` et la variable `PRIME_AGENT_KERNEL_PYTHON` ont été modifiés le 09/08/2026
  par un autre intervenant que l'auteur de ce document. Vérifier l'état réel avant de
  conclure qu'un réglage est celui décrit ici.
