# V14 — Titanium hybride

## Skills locaux Claude

Les 48 competences heritees de V12 sont disponibles dans `.claude/skills`,
miroir du catalogue canonique `.agents/skills`. Charger le `SKILL.md` pertinent
quand la demande correspond a sa description ou nomme le skill. Inventaire et
synchronisation : `collab/SKILLS_V14.md`.

Un skill guide la methode mais n'accorde aucune permission. Les garde-fous V14
et les instructions explicites de Florent priment toujours : aucun ordre reel,
aucune modification de `.env`, aucun armement, redemarrage, accord de permission
ou action destructive sur la seule base d'un skill.

**Delegation Prime du 09/08/2026.** Florent accorde a Prime Agent un acces autonome de
developpement a toute la racine V14. Prime peut utiliser le shell et le reseau, modifier
le code/configuration non secrete, gerer les dependances, faire des refactorings
multi-fichiers et piloter le dashboard/services de collaboration. La politique exacte est
`.prime/agent/APPEND_SYSTEM.md`. Les secrets, l'elevation UAC, MT5, `live_demo`, la boucle
de trading, l'armement et les ordres reels restent exclus.

Prime est code owner et responsable technique principal de V14 : ses arbitrages
d'architecture et d'integration prevalent dans le perimetre du code. Claude, Codex et
Hermes conseillent et relisent sans constituer une porte obligatoire, sauf instruction
explicite de Florent sur une tache donnee.

## Collaboration Claude / Codex / Hermes

Lire `collab/HERMES_BRIDGE.md` avant tout echange inter-agent. Le canal commun
est `collab_hub` (`http://127.0.0.1:8770/mcp`) et Hermes est disponible sur
`http://127.0.0.1:8766/mcp`. Ne jamais publier de secret ni approuver une
permission automatiquement. Cette collaboration reste PAPER ONLY et sans
autorite d'execution trading.

### Lancer Claude Code pour le travail d'equipe

Decision de Florent du 10/08/2026 : Claude travaille sans demande d'autorisation
a chaque outil, pour que les missions du terminal de collaboration s'executent
sans blocage.

```
claude --permission-mode bypassPermissions
```

Ce drapeau se pose **au lancement** ; il ne se change pas en cours de session, et
une session ne peut pas se l'octroyer elle-meme.

⚠️ Ce qu'il leve et ce qu'il ne leve pas. Il supprime les invites d'autorisation
de l'outil — lecture, ecriture, commandes. Il ne leve **aucun** garde-fou de V14,
qui ne dependent pas du systeme de permissions mais des regles du projet :
aucun ordre reel, `.env` jamais lu ni ecrit, executeur MT5 desarme, aucun
redemarrage de service, aucune approbation de permission a la place de Florent.
Ces murs restent entierement en vigueur, et c'est a l'agent de les tenir.

### Canaux et disponibilite

Le terminal de collaboration est sur **http://127.0.0.1:8097** ; tout passe par
la. Les missions arrivent par le hub MCP (`collab_read`), les comptes rendus
repartent par `POST /api/chat` ou `collab_publish`.

Claude surveille le flux commun en continu pendant une session et traite ce qui
lui est adresse sans attendre une relance de Florent. Repondre a une demande de
l'equipe est prioritaire sur la poursuite d'une tache en cours ; une mission
recue s'accuse d'abord, s'execute ensuite.

### GitNexus commun a chaque mission

Au debut de chaque mission et avant le compte rendu, executer :

```powershell
powershell -ExecutionPolicy Bypass -File tools/gitnexus_team.ps1 sync
```

Puis utiliser le MCP `gitnexus` pour explorer, mesurer l'impact upstream avant
edition et verifier le diff avec `detect_changes`. Le synchroniseur couvre le
travail non commite et serialise les reconstructions entre agents. Voir
`collab/GITNEXUS_TEAM_PROTOCOL.md`.

## Ce qu'est V14

**Fork intégral** de `TauricResearch/TradingAgents` v0.3.1 (06/08/2026), étendu
avec les briques déterministes de V12. On possède ce code : il n'y a plus de
`vendor/`, plus d'upstream à suivre.

La thèse en une phrase : **V12 exécute, V13 délibère.**

```
données MT5 (prioritaire) / yfinance (secours)
   → portes ET déterministes         ← véto, décide SI on peut entrer
   → délibération multi-agents LLM   ← décide COMBIEN et COMMENT
   → RiskGate unifié                 ← dernier mot
   → exécution MT5                   ← mur démo↔réel
```

## Les trois règles non négociables

1. **Le LLM n'a jamais l'autorité d'exécution.** Il module la taille et le plan
   d'entrée d'un setup *déjà validé*. Les portes gardent le droit de véto.
2. **Aucun appel LLM dans le chemin critique temps réel.** L'intraday ne
   supporte pas 30 appels réseau. La délibération sert le swing et le
   dimensionnement, jamais le déclenchement.
3. **Fail-closed conservé.** LLM indisponible, lent ou incohérent ⇒ on retombe
   sur le comportement déterministe, jamais sur un pari.

Corollaire de coût : délibérer sur 149 actifs est intenable. On ne délibère que
sur ce que les portes ont déjà validé — quelques décisions par jour.

## Arborescence

```
V14/
├── CLAUDE.md              ← ce fichier
├── .venv/                 ← Python 3.12, install éditable
├── .env                   ← clés API (gitignoré)
├── pyproject.toml         ← projet « titanium-v14 »
├── tradingagents/         ← socle forké (agents, graph, dataflows, llm_clients)
├── titanium/              ← les briques V12 portées
│   ├── gates/             ← portes ET de confluence (véto)
│   ├── risk/              ← RiskGate unifié
│   ├── execution/         ← MT5 + gestion de position
│   └── data/              ← vendeur MT5
├── cli/                   ← interface terminal
├── tests/
└── docs/
```

## État d'avancement

| Brique | État |
|---|---|
| Socle forké + install éditable | ✅ |
| **Portes ET de confluence** (`titanium/gates/confluence_gate.py`) | ✅ 35 |
| **RiskGate unifié** (`titanium/risk/riskgate.py`) | ✅ 52 |
| **Détecteurs + features** (`titanium/features/`) | ✅ 58 |
| **Délibérateur** (`titanium/deliberation.py`) | ✅ 41 |
| **Vendeur MT5 lecture seule** (`titanium/data/mt5_vendor.py`) | ✅ 24 |
| **Vendeur MT5 pour les agents** (`titanium/data/mt5_dataflows.py`) | ✅ 27 |
| **Exécution + mur démo↔réel** (`titanium/execution/mt5_executor.py`) | ✅ 54, **désarmé** |
| **Gestion de position** (`titanium/execution/position_manager.py`) | ✅ 43 |
| **Orchestrateur** (`titanium/orchestrator.py`) | ✅ 29 |

**Suite complète : 939 passed, 2 skipped** — 363 titanium + 576 socle.
Relevé authoritatif : `python tools/dashboard.py --tests`. Ajouter une brique
dans `BRIQUES` (tools/dashboard.py) la fait apparaître sur la page **et** dans
le compteur — les deux lisaient auparavant des listes distinctes, et les
nouveaux fichiers de tests étaient oubliés en silence.

## Données : MT5 prioritaire, yfinance en secours

`titanium/data/mt5_dataflows.py` est enregistré dans `VENDOR_METHODS` de
`tradingagents/dataflows/interface.py` sous la clé `mt5`, **en tête** de
`get_stock_data` et `get_indicators`. La config par défaut :

```
core_stock_apis      : mt5,yfinance
technical_indicators : mt5,yfinance
fundamental_data     : yfinance      ← un courtier CFD n'a pas de bilan
news_data            : yfinance
```

La bascule est **gratuite** : MT5 lève `NoMarketDataError` (symbole absent du
courtier) ou `Mt5NotAvailableError` (terminal fermé, un
`VendorNotConfiguredError`), et le routeur de V13 passe au vendeur suivant sans
une ligne de code d'appelant. Vérifié : `EURUSD`/`XAUUSD` → MT5, `AAPL` →
yfinance.

Résolution de symbole : `NAS100` → `NAS100.fs`, `BTC-USD` → `BTCUSD`,
`XAUUSD+` → `XAUUSD`. Catalogue courtier lu **une seule fois** par processus —
l'itérer à chaque requête affamerait le verrou MT5 (leçon V12).

## Configuration — une seule, `TITANIUM_*`

V14 est autonome : son préfixe est `TITANIUM_`. `TRADINGAGENTS_` reste lu en
second, pour que le socle forké et ses tests fonctionnent sans réécriture. UNE
table (`_ENV_OVERRIDES`), UN chemin de code.

Le mur d'exécution vit dans `DEFAULT_CONFIG` avec le reste
(`exec_enabled`, `demo_login`, `allow_real_account`…) et
`ExecutionPolicy.from_config()` le lit de là. **Ce module ne relit jamais
l'environnement de son côté** — c'est ce qui évite deux configs qui se
contredisent.

⚠️ Piège rencontré : les tests upstream vidaient l'environnement en itérant
`_ENV_OVERRIDES`, dont les clés sont devenues des **suffixes**. `TITANIUM_*`
survivait au nettoyage et le `.env` du projet fuitait dans les tests. D'où
`default_config.env_var_names()` — l'utiliser dans tout test qui isole la config.

## Le mur démo↔réel — comment l'armer

Trois verrous indépendants, vérifiés à **chaque** ordre, jamais mis en cache.
Le terminal peut basculer de compte entre deux appels : c'est arrivé le
06/08/2026 (compte réel 60261188 le matin, démo 50061786 l'après-midi).

| Verrou | Variable | État |
|---|---|---|
| 1. Armement | `TITANIUM_EXEC_ENABLED` | `0` — **fermé** |
| 2. Le courtier dit DEMO (`trade_mode==0`) | — (lu du serveur) | ✅ ouvert |
| 3. Login attendu | `TITANIUM_DEMO_LOGIN=10055401` | ✅ concordant |

Pour armer sur la démo : `TITANIUM_EXEC_ENABLED=1`. Rien d'autre à toucher.

Le compte réel exige `TITANIUM_ALLOW_REAL_ACCOUNT=I_UNDERSTAND_THIS_IS_REAL_MONEY`
— **la phrase exacte, aucun synonyme**. « yes », « true », « 1 » sont refusés.
Cette graphie existe pour rendre la décision cherchable dans l'historique, pas
pour le confort. Et elle ne dispense pas du verrou 3.

Règle de conception : une variable mal orthographiée **désarme**, jamais
l'inverse.

## Ce qui a été porté, et comment

### Portes ET de confluence

Source : V12 `fusion/confluence_gate.py` (GATE_VERSION 1.2.0).
**La logique de décision est inchangée** — c'est la méthode de Florent, éprouvée
en démo puis en réel, on ne la relitige pas.

Modifications assumées (→ `GATE_VERSION = "2.0.0"`) :
- le quorum micro-structure devient un **paramètre** (V12 : `if` en dur, 3 en
  prod / 2 en explore) ;
- fonction **pure**, zéro import du reste de la base → testable sans MT5 ;
- seuils émotionnels remontés en constantes nommées.

Rappel du principe : **un pilier fort ne compense jamais un pilier absent.**
G1 (structure) est obligatoire ; les 4 piliers de micro-structure passent par un
quorum. Un sweep de liquidité dans le mauvais sens n'est pas un demi-point,
c'est un pilier absent.

### RiskGate unifié

Source : V12 `risk/riskgate.py`. Trois corrections de fond :

1. **Fail-CLOSED.** V12 est fail-OPEN par conception (« un bug du RiskGate ne
   bloque jamais un trade ») : l'appelant enveloppe l'appel dans un `try/except`
   qui avale tout. Une porte de risque qui laisse passer quand elle casse ne
   protège rien. Ici toute anomalie ⇒ DENY tracé, et `evaluate()` ne lève jamais.
2. **Un seul calcul de taille.** Dans V12, `pillar_size` est calculé ligne 154
   depuis le barème par piliers puis **écrasé** ligne 173 par la confiance de
   Cloe — le barème et sa fonction sont du code mort. Ici un seul modulateur :
   la confiance mesurée du contexte.
3. **Découplé.** V12 dépend de `core.state.SystemState` et `core.config`. Ici
   l'entrée est un dataclass explicite → testable sans démarrer le bot.

Détails de conception :
- les booléens de danger de `RiskInput` valent **True par défaut** : un champ
  oublié bloque au lieu de laisser passer ;
- le véto d'exposition passe **avant** le dimensionnement (V12 calculait puis
  vétoait, jetant le calcul) ;
- un lot nul n'est pas un ALLOW : c'est un `DENY / TAILLE_NULLE` nommé ;
- un `side` non numérique donne `PAS_DE_SENS`, pas `ERREUR_INTERNE` — le motif
  du refus est un dataset, il doit nommer la cause réelle.

### Gestion dynamique du stop

Source : V12 `execution/demo_position_manager.py`. **Logique inchangée** — elle
vient d'un post-mortem chiffré : *23 % des pertes de V12 avaient atteint +0.8 R
en leur faveur avant de repartir au stop*. Un SL figé les a laissées revenir en
perte.

Changement de forme : V12 mêle décision et appels MT5 dans une boucle de
140 lignes, intestable sans terminal ni position réelle. Ici `decide_new_sl()`
est **pure** et l'I/O est une coquille mince autour — d'où 43 tests hors ligne
sur les règles de sécurité.

Invariants conservés : le SL ne recule jamais · distance minimale du courtier
respectée · seules nos positions (magic) · le TP n'est jamais touché · une
position en erreur n'interrompt pas la boucle · le mur s'applique aussi (gérer
un SL est un ordre). Le pic favorable est un **cliquet** : il ne redescend
jamais, sinon le trailing rendrait du gain déjà sécurisé.

### Orchestrateur — les trois règles en code

```
portes ET  →  délibération LLM  →  RiskGate  →  exécution
(décide SI)   (décide COMBIEN)   (dernier mot)   (agit)
```

1. **Le LLM n'a jamais l'autorité d'exécution.** Sa seule sortie est une
   conviction ∈ [0,1] qui module la TAILLE. Il ne peut ni créer une entrée
   refusée, ni inverser un sens, ni lever un véto. Testé : conviction 1.0 sur un
   setup BLOCK ⇒ rien.
2. **Aucun appel LLM hors du chemin validé.** La délibération n'est atteinte que
   sur un `ENTER`. C'est la réponse à la question de coût : sur 149 actifs, on
   passe de milliers d'appels par jour à quelques-uns. Testé explicitement.
3. **Fail-closed de bout en bout.** Délibérateur qui plante, qui rend `NaN`,
   `None` ou du texte ⇒ conviction neutre, la chaîne continue en déterministe.

`side` et `confidence` fournis dans le contexte de risque sont **ignorés** : ils
viennent des étages précédents, un appelant ne peut pas écraser la décision des
portes.

## Leçons de V12 déjà intégrées

À ne pas re-litiger sans nouveaux backtests :

- **Le coût est le tueur dominant.** Baseline PF ≈ 0.20 en réel. Un `edge_ok`
  explicitement négatif bloque même en mode explore.
- **Élargir les cibles ne sauve rien** (sweep TP : R:R 1.5 est l'optimum mesuré).
- **Winrate élevé ≠ profit.** Toujours vérifier l'espérance OOS après coûts Axi
  réels (spread + swap). Des configs superbes en Python ont échoué au testeur
  natif (XAUUSD : PF 2.31 → 0.90).
- Le vrai levier restant est la **sélectivité** — ne trader que les contextes
  mesurés profitables. C'est exactement ce que la délibération doit servir.

## Pièges de V12 à ne PAS reproduire

Constatés en lisant le code (cf. `V13/docs/DIAGNOSTIC_V12_V13.md`) :

1. **Pas de doublons de modules.** V12 a `core/confluence_adapter.py` et
   `fusion/confluence_adapter.py` vivants et divergents de 845 lignes, chacun
   portant un correctif que l'autre n'a pas. Un module = un fichier.
2. **Pas de docstring qui ment.** `risk/riskgate.py` affirme « NON CÂBLÉ » alors
   que le code l'appelle. Ça m'a fait écrire une conclusion fausse.
3. **Pas de `_archive/` dans l'arbre de travail.** 1 601 fichiers `.py` morts
   qui polluent chaque recherche.
4. **Pas de fail-OPEN sur une porte de risque.** Le RiskGate de V12 est
   fail-open par conception ; celui de V14 sera fail-closed.
5. **Ne jamais archiver `results/positions.json` avec des positions ouvertes.**
   Ce fichier est un état vivant, pas un journal : le déplacer orpheline les
   tickets et détruit leur contexte de clôture. Les journaux peuvent être
   archivés ; l'état des positions seulement quand MT5 confirme zéro position V14.

## Lancer V14

| Fichier | Ce que ça fait |
|---|---|
| `LANCER_V14.bat` | interface terminal des agents (la « fenêtre DOS ») |
| `SUIVI_V14.bat` | **tableau de bord de contrôle** `http://localhost:8095` |
| `run_v14.py TICKER DATE` | une analyse multi-agents en ligne de commande |
| `scan_v14.py [SYMBOLES…]` | **la chaîne complète** sur données MT5 : features → portes → RiskGate → décision. `--deliberer` ajoute le LLM, `--prod` durcit le quorum. N'exécute jamais. |
| `python tools/dashboard.py --tests` | relève la suite et alimente la page |
| `PRIME_V14.bat` | **harnais de développement Prime Agent** — écrit du code, ne trade pas |

Port 8095 choisi pour ne heurter ni V12 (8090), ni JARVIS (8080/8765), ni
Open WebUI (3000).

## Prime Agent — le harnais de développement (09/08/2026)

`PrimeIntellect-ai/prime-agent` v0.7.1 (MIT, sorti le 05/08/2026) est installé et câblé
sur la racine V14. **Outil de développement uniquement** : aucune autorité de trading,
aucun câblage dans une boucle d'exécution.

Ce qu'il apporte au chantier : le modèle ne dispose que d'un **kernel IPython persistant**
(RLM) où les sous-agents sont des appels de fonction (`await rlm(…)`) et où les variables
survivent à la compaction — auditer 42 fichiers n'occupe plus 42 lectures dans le contexte
du parent. Et un **état de harnais durable** (`/refine`) : ce qui est appris sur V14 reste
acquis d'une session à l'autre.

- Lancer : `PRIME_V14.bat` ou `tools/prime_agent_v14.sh` — **jamais `prime-agent` nu** :
  le lanceur fixe la racine, exporte `GEMINI_API_KEY` depuis le `GOOGLE_API_KEY` du `.env`
  et désigne le python du kernel.
- Réglages projet `.prime/agent/settings.json` ; skill projet
  `.prime/agent/skills/v14-boucle-dev/SKILL.md` (méthode, garde-fous, critère OOS).
- ⚠️ Le bootstrap du kernel est **cassé sous Windows** en 0.7.1 (il cherche
  `kernel-venv/bin/python`, chemin POSIX). Contourné par
  `PRIME_AGENT_KERNEL_PYTHON`. **Après chaque `prime-agent update`**, rejouer la
  réparation décrite dans `docs/PRIME_AGENT.md`.
- Le venv du kernel est séparé du `.venv` de V14 : pour exécuter du code du projet,
  passer par `%%bash .venv/Scripts/python …`.
- **Le kernel et les outils tournent dans le démon, pas dans le processus lancé.** D'où
  deux règles : les lanceurs passent `--cwd` (sans lui l'agent analyse le mauvais dossier
  et répond faux **sans erreur**), et après tout changement de variable d'environnement il
  faut `prime-agent shutdown --force` — sinon le client se rattache à l'ancien démon.
- Modèle actif : **`claude-opus-5`** via l'abonnement Claude Pro/Max (connecté le
  09/08/2026, facturé en usage supplémentaire au token). Gemini reste le secours —
  et depuis le 15/08/2026 ce repli s'annonce, au lieu de se produire en silence.
  `PRIME_V14.bat` codait `claude-sonnet-5` alors que `.prime/agent/settings.json`
  disait `claude-opus-5` : le drapeau explicite passé au binaire gagne toujours sur
  le fichier de réglages, donc le réglage projet ne servait à rien.
- Détail complet, fournisseurs et limites : **`docs/PRIME_AGENT.md`**.

## L'interface de contrôle

`tools/dashboard.py` (serveur stdlib) + `tools/ui/` (HTML/CSS/JS, aucun build,
aucune dépendance distante — l'interface reste utilisable hors ligne, ce qu'un
poste de trading doit rester).

**Routes** — toutes en lecture : `/api/state` (mur, compte, vendeurs, briques,
tests, edge, positions, analyses), `/api/scan` (balayage déterministe),
`/api/run/<i>` (détail d'une analyse). Aucune ne passe d'ordre ni n'appelle un
LLM. Le balayage est le seul appel coûteux, et il est **manuel** : le lancer en
boucle monopoliserait le verrou MT5.

Design system issu du skill `ui-ux-pro-max` : Modern Dark, fond `#020617`
(jamais `#000` — il bave sur OLED), accent `#22c55e`, densité 9/10, pile de
polices système.

**Trois défauts trouvés au navigateur, pas à l'œil** (Playwright) :

1. `--txt-faint` mesurait **4.24:1**, sous le minimum AA de 4.5 → porté à
   `#7d8ca4` (5.92:1).
2. Le serveur était **mono-thread** : un balayage gelait toute l'interface,
   rafraîchissement d'état compris → `ThreadingHTTPServer`.
3. `grid-template-columns: auto-fit` produisait 4 pistes pour 3 cartes et
   laissait une colonne vide → grille 12 colonnes explicite avec paliers.

Les piliers sont rendus en **forme *et* couleur** (chiffre dans une pastille),
jamais par la couleur seule — un daltonien doit pouvoir lire le tableau.

Rejouer la vérification : installer `playwright` dans le venv puis piloter
`http://localhost:8095`. ⚠️ Ne pas attendre `networkidle` : la page interroge
l'API toutes les 10 s, elle n'est jamais « au repos ». Attendre
`domcontentloaded` puis le sélecteur `#wall .verdict`.

## Environnement

- **Lancer depuis la racine V14** (`find_dotenv(usecwd=True)` pour les clés).
- Fournisseur LLM : **Google Gemini Flash** — OpenAI et Anthropic sont à sec,
  Gemini Pro est hors free tier. Config dans `.env` (`TRADINGAGENTS_*`).
- Machine **CPU seul** (Ryzen 7 7730U, pas de GPU utilisable) — aucun LLM local
  dans le chemin de décision.
- MT5 : terminal Axi, **compte réel 60261188**. Le mur démo↔réel est
  obligatoire avant tout `order_send`.

## Rapport à V12 et V13

- **V12** (`C:\Users\flore\Desktop\v12`) tourne en argent réel. **Lecture seule
  depuis V14**, toujours.
- **V13** (`C:\Users\flore\Desktop\V13`) est le banc d'essai qui a validé la
  mécanique de délibération. On y garde le diagnostic comparatif.

## Journal

- **2026-08-06** — Création de V14 par fork de V13/TradingAgents. Projet renommé
  `titanium-v14`, extra `mt5` ajouté, package `titanium/` créé. Portes ET de
  confluence portées depuis V12 avec quorum paramétrable : 35 tests verts.
- **2026-08-06** — RiskGate porté et rendu fail-closed (52 tests). Vendeur MT5
  lecture seule (23 tests) : verrou réentrant `mt5_lock`, bougie en cours
  écartée par défaut, garde-fou AST qui interdit tout appel d'ordre dans le
  module. Lecture vérifiée sur le terminal : compte **60261188 Axi-US52-Live,
  trade_mode=2 (RÉEL)**, 1 272 symboles, H4 EURUSD OK.
- **2026-08-07** — le démo **50061786 expire** (« Invalid account » en boucle
  dans le journal réseau). Nouveau démo **10055401**, Axi-US50-Demo,
  **5000 EUR**. Conséquence majeure sur le dimensionnement : 16/16 actifs
  testés deviennent tradables au risque cible de 1 %, contre 10/17 auparavant
  — et **aucun n'est plus forcé au lot minimum** (XAUUSD était à 29.1 % de
  risque, BTCUSD à 41.3 %, sur 59.58 USD). C'est le levier que l'analyse du
  deadlock avait chiffré : ~3,5 mois pour valider une cellule d'edge au lieu
  de ~16.
- **2026-08-06** — Florent bascule le terminal sur le compte démo **50061786**
  (Axi-US50-Demo, USD, 109,16). Vérifié : `trade_mode=0`. Le mur peut être
  construit sur une vérité serveur.
- **2026-08-06** — Exécuteur MT5 livré (51 tests), **désarmé**. Le mur refuse
  compte réel, compte concours, login différent, et absence de login déclaré.
  Calcul de lot : `perte_par_lot = distance/tick_size × tick_value`, arrondi
  **vers le bas** au pas du courtier (un arrondi supérieur dépasserait le
  risque voulu). Le cas « lot sous le minimum » — fréquent sur un compte à
  109 USD — est **refusé par défaut** avec le ratio de sur-risque nommé, plutôt
  qu'entré silencieusement à 0.01.
- **2026-08-06** — MT5 branché sur le registre de V13 (27 tests) et config
  unifiée sous `TITANIUM_*`. Le refactor a cassé 11 tests upstream, tous pour de
  bonnes raisons : 9 laissaient fuiter le `.env` du projet (préfixe non nettoyé),
  2 codaient en dur `"yfinance"` comme témoin d'un test d'isolation. Corrigés au
  fond, pas contournés.
- **2026-08-06** — ⚠️ Le `.env` de V14 pointait encore ses sorties vers
  `V13\results` : un run V14 aurait écrit dans V13. Corrigé.
- **2026-08-06** — **Viabilité prouvée.** `run_v14.py EURUSD 2026-08-05` :
  décision **Hold** en 96 s, données servies par MT5
  (`# Source: MetaTrader 5 (broker feed, D1 bars)`), graphe complet — 4 rapports
  d'analystes, débat bull/bear (3434 / 3524 car.), comité de risque à 3 voix,
  décision du Portfolio Manager. Dégradations connues et non bloquantes :
  FRED (pas de clé), Polymarket (certificat TLS invalide côté vendeur), Reddit
  (429).
- **2026-08-06** — Gestion de position portée (43 tests) et **orchestrateur
  livré** (29 tests). La chaîne complète existe :
  `portes → délibération → RiskGate → exécution`. Suite : **840 passed**.
  V14 décide de bout en bout ; l'exécution reste désarmée.
- **2026-08-06** — **Délibérateur branché** (41 tests) et **features portées**
  (58 tests). Suite : **939 passed, 363 titanium**. Premier balayage réel via
  `scan_v14.py` sur 6 instruments MT5 : 3 setups retenus, dont **USTECH accepté
  par les portes puis refusé par le RiskGate en `CONTRE_TENDANCE`** — la défense
  en profondeur fonctionne, le second étage rattrape le premier. C'est
  exactement ce que V12 n'a pas, son RiskGate étant éteint (`RISKGATE_ENABLED=0`).

## Le délibérateur

`titanium/deliberation.py` convertit la notation à 5 niveaux du Portfolio
Manager (`Buy · Overweight · Hold · Underweight · Sell`) en conviction ∈ [0,1].

La conversion mesure une **concordance**, jamais une direction : le sens est
déjà décidé par les portes. Accord total → pleine taille ; neutre → taille de
référence ; désaccord total → plancher.

**`CONVICTION_FLOOR = 0.15`, strictement positif.** Une conviction de 0 vaut
`TAILLE_NULLE` côté RiskGate, donc un refus : laisser le graphe descendre à 0
lui rendrait par la fenêtre le droit de véto qu'on lui a refusé par la porte.
Le plancher est configurable — si tu décides un jour que la contradiction doit
bloquer, ce sera un choix explicite.

Le graphe est mis en cache par `(symbole, date)` : un passage coûte plusieurs
dizaines d'appels LLM (~96 s mesuré), plusieurs setups le même jour ne le
repaient pas. `static_deliberator("Buy")` rejoue une note connue sans coût.

## Les features des portes

`titanium/features/` porte les détecteurs de V12 :

- `smc.py` — ATR (natif, sans `pandas_ta`), EMA200, FVG **non comblées**,
  alignement OB/FVG, sweep de liquidité normalisé ATR, bougie de rejet ;
- `structure.py` — niveaux S/R clusterisés, profil de volume (VPOC/HVN/value
  area), zone OTE de Fibonacci ;
- `builder.py` — `build_feats(ltf, htf)` assemble le dict exact que la porte
  attend, plus `risk_context_from()` pour le RiskGate.

Les deux correctifs Codex du 22/07/2026 sont conservés : **FVG non comblées
uniquement** (sinon les deux côtés sont vrais et le pilier reste à 0/20) et
**tampon de sweep en fraction d'ATR** (0,3 % fixe vaut ~9 ATR sur un indice
calme). `cost.edge_ok` vaut **None** par défaut — jamais True : l'edge est
inconnu tant qu'un laboratoire ne l'a pas mesuré.

⚠️ **Deux écarts assumés par rapport à V12 :**

1. **Le pilier bougie est RÉDUIT.** V12 utilise
   `poles/smc/candlestick_engine.net_bias_on_df` : 336 lignes, bibliothèque de
   patterns complète et automate de confirmation. `smc.candle_bias` n'en
   implémente que deux (rejet, avalement). G5 est donc **plus sévère** qu'en
   V12 — moins d'entrées. C'est le sens sûr de l'erreur, mais il faudra porter
   le moteur complet pour retrouver le comportement de V12.
2. **Pas de plan géométrique.** V12 calcule un régime lu par sa porte
   neuronale ; V14 n'a pas cette porte, la clé n'aurait pas de lecteur.

## ⚠️ Défaut trouvé en réel — idempotence (07/08/2026)

**Ce qui s'est passé.** La clé d'idempotence était `symbole:M15:{decided_at}`.
Or `decided_at` vaut `datetime.now()` au moment du calcul : elle change à
**chaque** balayage. La clé n'était jamais deux fois la même, donc la
déduplication n'a jamais fonctionné.

**Coût mesuré** : trois positions LONG sur AUDUSD en quelques minutes
(0.70330 / 0.70327 / 0.70324) — trois fois le risque prévu sur un seul actif
corrélé, 3.4 % du compte au lieu de 1.14 %. Les trois ont touché le stop.
Sur un compte réel, c'est le type de défaut qui vide un compte.

**Deux corrections, testées** (`tests/test_idempotence_barre.py`, 12 tests) :

1. `_trace.bar_time` — horodatage de la dernière barre **clôturée** — est
   maintenant exposé par `build_feats`, et c'est lui qui ancre la clé.
   `decided_at` reste disponible mais **ne doit jamais servir à dédupliquer**.
2. `MAX_PAR_SYMBOLE = 1` dans `tools/live_demo.py` — plafond par actif,
   **indépendant** de l'idempotence. Si la clé échoue pour une autre raison,
   ce filet empêche encore d'empiler le même risque.

Règle générale à retenir : **une clé de déduplication s'ancre sur l'évènement,
jamais sur l'instant du calcul.**

## Pont MQL5 — V14 trace ses zones sur MT5

`titanium/bridge/mt5_zones.py` (écrivain Python) + `titanium/bridge/titanium_zones.mq5`
(indicateur compagnon, installé dans `MQL5/Indicators/`).

L'API Python de MT5 n'a **aucune** fonction de dessin. Le pont passe donc par
`MQL5/Files/titanium_zones.json`, écrit de façon atomique à chaque barre et relu
par l'indicateur. Sont tracés : niveaux S/R (épaisseur ∝ force), FVG **non
comblées**, golden zone OTE, VPOC, entrée/SL/TP — donc le R:R visible — et le
verdict avec l'état des six piliers.

Deux garanties testées : l'indicateur ne supprime que les objets préfixés
`TITANIUM_` (vos tracés manuels survivent), et il ne contient **aucun appel de
trading** — un test échoue s'il en gagne un.

Le tracé vient des **valeurs numériques de la trace** (`sr_level`, `ote_zone`,
`fvg_open`, `vpoc`), pas d'une reconstruction : le graphique montre ce qui a
décidé, sinon il mentirait.

À faire une fois côté MetaTrader : MetaEditor → F7 pour compiler → glisser
l'indicateur sur le graphique.

## Panel d'indicateurs — mesurer, jamais décider

`titanium/features/indicators.py` calcule **100 indicateurs** (49 familles × 2
timeframes) et les range dans `_trace.indicators`. Activé par
`build_feats(with_indicators=True)` — désactivé par défaut, +88 ms.

**Aucune porte ne le lit.** Un test structurel échoue si `confluence_gate` ou
`riskgate` mentionnent `indicators`. Le panel existe pour être **journalisé avec
le résultat du trade**, pas pour peser sur un verdict.

Normalisation obligatoire, sinon rien n'est comparable entre actifs :
oscillateurs bornés tels quels · niveaux en **écart au prix, en ATR** ·
volatilité en **% du prix** · volumes en **ratio à leur moyenne**.

### L'analyse discriminante — et pourquoi elle est nécessaire

`titanium/analysis/discriminants.py` classe les indicateurs par pouvoir de
séparation gagnants/perdants. Trois garde-fous, tous obligatoires :

1. **delta de Cliff** — non paramétrique, insensible aux trades aberrants ;
2. **test de permutation** — aucune hypothèse de normalité, pas de scipy ;
3. **correction de Benjamini-Hochberg** — sans elle, tester 100 indicateurs sur
   200 trades sort ~5 « découvertes » par pur hasard.

Validé sur vérité connue : le vrai signal est retenu (delta +0.546), les six
bruits rejetés — dont un à p brut 0.072 (« presque significatif ») que la
correction ramène à 0.25. **Sans correction on l'aurait câblé.**

Un discriminant trouvé ici est une **hypothèse**, jamais une conclusion. Rien
n'est câblé automatiquement.

## Ce qui n'est PAS possible — vérifié, pas supposé

| Question | Verdict | Preuve |
|---|---|---|
| Carnet d'ordres **niveau 2** via Axi/MT5 | ❌ **non** | `market_book_add` rend `False` et 0 niveau sur EURUSD, XAUUSD, US500, BTCUSD. Seul le L1 (bid/ask) existe. |
| Dessiner sur MT5 depuis Python | ❌ non | zéro fonction graphique dans l'API — d'où le pont MQL5 |
| Piloter TradingView de l'extérieur | ❌ non | aucune API publique |
| Analyse multi-timeframe | ✅ **oui** | 7 TF × 600 barres = 1,8 s/actif, 21,7 s pour 12 actifs |

## ⚠️ `wmic` n'existe plus sous Windows 11 (07/08/2026)

La sonde qui détectait la boucle d'amorçage scannait les processus via `wmic`.
Windows 11 l'a retiré : la commande échoue silencieusement, la sonde rendait
toujours `running: False`, **et le tableau de bord affichait « boucle arrêtée »
alors qu'elle tournait** (pid 26720/29256, vérifié par `Get-CimInstance`).

Remplacé par un **battement de cœur** : `tools/live_demo.py` écrit
`results/loop_heartbeat.json` en début ET en fin de tour. Le début prouve la
vitalité même si un balayage se bloque ; la fin porte des statistiques à jour.

Trois états au lieu de deux — et c'est le vrai gain : `arrêtée` (aucun
battement), `en cours` (battement frais), **`FIGÉE`** (battement plus vieux que
3× l'intervalle). Un scan de processus ne sait pas distinguer les deux derniers.

Règle : ne jamais dépendre d'un outil système pour une sonde d'observabilité —
un fichier horodaté est plus fiable et plus informatif.

## ✅ Flux retour reconnecté (07/08/2026)

C'était **la** rupture de V14 : `results/trades.ndjson` n'avait qu'un écrivain,
`tools/backtest.py`. La boucle live purgeait les tickets clos sans rien écrire →
registre d'edge vide → `edge_ok` toujours inconnu → **mode PROD jamais ouvrable**.

Trois pièces posées :

1. **`TrackedState` retient le contexte d'entrée** — `entry`, `sl_initial`,
   `tp_initial`, `context_key`, `indicators`, `ts_open`, `mae_r`. Capturé à
   l'ouverture parce que la clôture est le seul instant où résultat et contexte
   coexistent, et qu'à ce moment MT5 ne voit déjà plus la position.
   `from_dict` reste **tolérant aux états anciens** : une mise à jour du code
   alors que des positions sont ouvertes ne doit pas casser le gestionnaire.
2. **`journaliser_cloture()`** écrit à la purge du ticket. Prix de sortie lu dans
   `history_deals_get` (la position n'est plus dans `positions_get`). Idempotent
   par `live:<ticket>` — sans quoi chaque redémarrage dupliquerait la ligne.
3. **`_attacher_contexte()`** dans la boucle : le contexte est rattaché au ticket
   **dès l'envoi de l'ordre**, pas découvert au tour suivant.

Deux fichiers, deux usages — c'est délibéré :
- `results/trades.ndjson` — format `ClosedTrade`, ce dont la **mesure d'edge** a
  besoin : contexte, résultat, coût.
- `results/excursions.ndjson` — MAE, MFE, giveback, `censored`, **panel
  d'indicateurs** : ce dont l'**analyse discriminante** a besoin.

Pièges conservés de V12 : le `giveback` n'est compté que s'il y a **eu** un gain
(sinon il double-compte la MAE), et `censored` marque les sorties au stop — sans
ce drapeau toute statistique de MFE est biaisée à la baisse, définitivement.

Vérifié de bout en bout : 25 trades journalisés → `edge_ok=True`, et le panel
est relu par l'analyse discriminante.

## Testeur natif MT5 — `tools/metatester.py` (07/08/2026)

Balayage des paramètres de **gestion** sur ticks réels, sans consommer de
jeton. Motif : V12 donnait XAUUSD PF 2.31 en Python et **PF 0.90 au testeur
natif** — même stratégie. Cet écart est ce que ce pont existe pour mesurer.

**L'EA ne décide rien.** `titanium/bridge/titanium_replay.mq5` lit les
décisions exportées par V14 et les exécute ; il n'a aucun accès aux
indicateurs (un test l'interdit : `iRSI`, `iMA`… bannis du source). Porter
la chaîne de décision en MQL5 créerait une seconde implémentation qui
divergerait — c'est la faute qui a tué V12, 845 lignes divergentes.

Conséquence à ne jamais oublier : **l'optimiseur répond à « comment mieux
gérer ces entrées », jamais à « faut-il les prendre »**.

### Instance dédiée, obligatoire
`~/MT5Tester` (`tools/setup_tester_instance.py`). Le `.ini` du testeur porte
`ShutdownTerminal=1` : lancé sur l'installation principale, il **fermerait
le terminal qui trade**. Un garde-fou refuse désormais ce cas.
- copie le programme (~400 Mo), **pas** les 30 Go de cache d'historique
- `/portable` indispensable pour MetaEditor **et** le terminal, sinon la
  bibliothèque standard est cherchée dans un AppData vide (`error 106` sur
  `Trade.mqh`, alors que le fichier est bien présent)

### ⚠️ Conflit de compte — un seul terminal par login
L'instance de test se connecte au **même compte** que le terminal principal,
ce qui **l'éjecte** : MT5 n'admet pas deux sessions sur un login. Le rejeu
Python, qui passe par le terminal principal, échoue alors sur
`(-6, 'Terminal: Authorization failed')` — panne observée en chaîne sur
EURUSD/AUDUSD après un ETHUSD réussi.

Deux issues, la première est la bonne :
1. **Un second compte démo dédié au testeur** — il n'a besoin que des
   spécifications de symbole et de l'historique, pas d'un solde utile.
2. Produire tous les signaux d'abord (`--signaux-seulement`) pendant que le
   terminal principal est connecté, puis lancer les passes.

Tant que les deux partagent un login, **le testeur et la boucle live ne
peuvent pas tourner ensemble** — ce qui annule une partie de l'intérêt de
l'instance séparée.

### Grilles étagées, pas croisées
Le produit cartésien complet fait **5 400 combinaisons** ; sur 702 signaux
cela revient à essayer 8 réglages par trade — on trouverait un gagnant sur
du bruit pur. Trois étapes de 30 / 15 / 12 le ramènent à **57**, chacune
répondant à une question et figeant sa réponse.

Garde-fous non débrayables : forward MT5 (dernier tiers hors échantillon),
`OnTester()` annule toute passe sous 30 trades, `OnTester()` rend l'**espérance
en R** et non le profit (maximiser le profit récompense la taille de lot,
pas le réglage), et le nombre de combinaisons cumulées est imprimé pour
figurer dans toute conclusion.

La fenêtre du testeur est déduite des signaux, jamais du calendrier : une
fenêtre plus large ferait tourner le testeur à vide sur la part non couverte.

## ❌ Stop temporel — piste REJETÉE (07/08/2026)

Implémenté, balayé, **désactivé par défaut**. `docs/RAPPORT_stop_temporel.md`.

| | trades | total |
|---|---:|---:|
| référence | 842 | **+56.97 R** |
| stop (10 barres, 0.5 R) | 901 | **+49.37 R** |

Mesure appariée : 46 perdants sauvés (+0.73 R chacun), 16 gagnants amputés
(−1.18 R chacun). **Un gagnant amputé coûte 1.61× ce qu'un perdant sauvé
rapporte** → il faudrait 61.7 % de perdants dans la population coupée ;
XAUUSD n'en a que 58 %.

Trois raisons de fond, réutilisables ailleurs :
1. **Le couperet arrive après l'enterrement.** Les perdants meurent en 4-5
   barres au stop ; à la barre 10 il ne reste que 26-29 % des trades. La MFE
   basse des perdants vient de leur mort rapide, pas d'une stagnation.
2. **L'effet est inversement proportionnel à l'edge** — la règle aide
   d'autant plus que l'actif perd. Ce n'est pas un edge, c'est une réduction
   d'exposition.
3. **La cascade d'entrées annule le reste** : couper tôt libère un créneau,
   le moteur réentre, plus mal. ETHUSD et EURUSD *changent de signe* entre
   mesure appariée et rejeu complet. ⚠️ **Toute mesure à entrées figées
   flatte une règle qui raccourcit les trades** — vaut aussi pour le
   metatester ci-dessus.

80 combinaisons, Benjamini-Hochberg FDR 10 % : **une seule survit** (AUDUSD),
et elle perd sa stabilité walk-forward au rejeu complet.

Piège évité : `seuil_r = 0.0` aurait été un **no-op silencieux** (`mfe_r` est
plancherée à 0, donc `pic < 0.0` impossible) — 5 combinaisons sur 20 inertes
sans aucun signal.

## ✅ Bot armé — risque modulé par la confiance (07/08/2026)

Compte **10055401** Axi-US50-Demo, 5000 EUR. `TITANIUM_EXEC_ENABLED=1`,
`tools/live_demo.py --armer`. Premiers ordres : EURUSD, AUDUSD, GER40.

### `titanium/confiance.py` — le risque suit le setup
Échelle ancrée sur trois points connus, pas sur une courbe arbitraire :

| piliers | risque |
|---|---|
| 2/4 (quorum EXPLORE) | 0.50 % — plancher |
| 3/4 (quorum PROD) | ~1.13 % |
| 4/4 | 1.75 % — plafond de modulation |

`MAX_RISK_PCT = 2 %` reste un **mur intact** : le plafond de modulation est
délibérément en dessous, pour que le plafond dur garde son rôle de dernier
recours. La conviction du délibérateur nuance de ±25 % au plus — un LLM
nuance la taille, il ne la décide pas.

⚠️ **C'est un pari, pas une optimisation démontrée.** Rien n'établit
aujourd'hui que le nombre de piliers prédit le résultat — le registre d'edge
est vide. La modulation amplifie ce qui existe, dans les deux sens. Le
multiplicateur est journalisé pour permettre de trancher a posteriori.

### 🐛 Le défaut attrapé avant l'armement
Le premier câblage comptait **toutes les portes passées**. Or une décision en
porte **six** : `data_valid` (validité) et `trend_sr` (obligatoire, hors
quorum) s'ajoutent aux quatre piliers de micro-structure.

Conséquence : un setup à **2 piliers sur 4** — le plus faible admis —
comptait 4/4 et recevait le risque **maximum**. Exactement à l'envers.
Observé en marche à blanc : tous les ENTER sortaient à 1.75 %.

Corrigé en reprenant `_SUPPORT_PILLARS` **à la source** plutôt qu'en
redéfinissant la liste : celle qui décide du risque doit être celle qui
décide de l'entrée. Après correction les mêmes setups sortent à 0.42–0.50 %.

### R:R porté de 2.0 à 3.0
Seul réglage validé **hors échantillon** par le testeur natif, sur deux
actifs indépendants : XAUUSD +0.294 → +0.407 R, ETHUSD +0.098 → +0.192 R.
On s'arrête à 3.0 et non au 3.5 mesuré optimal — l'écart 3.0→3.5 tient sur
moins de trades et 4.0 décroche déjà sur ETHUSD. Vérifié en vol : R:R réel
2.98 à 3.18 sur les trois positions.

### ✅ Défaut corrigé : la clé de contexte suit la décision de porte
Le défaut observé le 07/08/2026 rangeait un setup à **2 piliers alignés** dans
le seau `4p` : `context_from_feats` mesurait la non-nullité
(`liquidity != 0`) tandis que la porte mesure l'alignement
(`liquidity == side`).

`context_from_feats` délègue désormais à `confluence_gate.evaluate`, puis
`context_from_decision` compte uniquement les portes passées de
`_SUPPORT_PILLARS`, avec `trend_sr` obligatoire ajouté à la signature. Un setup
à 2 piliers alignés est donc journalisé `3p` (2 supports + `trend_sr`), et non
`4p`. Le test `test_les_deux_constructions_concordent` verrouille la concordance
entre les deux chemins. La promotion PROD peut maintenant exploiter cette
stratification sans le sur-comptage connu.

### Panneau MT5
`titanium_zones.mq5` affiche désormais le panel d'indicateurs et le risque
retenu, sous le bandeau verdict/piliers. Les lignes arrivent **déjà mises en
forme** depuis Python : le parseur MQL5 sait extraire une chaîne, pas
parcourir un objet aux clés inconnues. Le champ `indicators` est déclaré
**après** `plan` dans le payload — le parseur est positionnel et ses tests
verrouillent l'ordre `pillars < zones < plan`.

## ✅ Pont Titanium → analystes, hors chemin critique (07/08/2026)

`titanium/avis.py` + `tools/analystes.py`. **Trois processus découplés par
fichiers**, jamais par appel direct.

Une délibération prend des minutes, la boucle tourne en 60 s. Un appel
synchrone couperait le flux — donc la boucle **dépose** ce que Titanium a vu
et continue, un travailleur séparé délibère, la boucle **relit** l'avis sans
jamais attendre.

La propriété qui rend ce couplage sûr est **l'absence de couplage** : si le
travailleur est arrêté, en retard, ou que le fournisseur est en panne, la
boucle se comporte exactement comme si le pont n'existait pas → conviction
neutre. Deux tests interdisent tout appel réseau et tout chemin d'exécution
dans `avis.py`.

### Le brief atteint réellement le graphe
`propagate(..., extra_context=...)` → injecté dans `instrument_context`, vu
par **tous** les agents. **Ajouté, jamais substitué** : l'identité résolue du
ticker reste souveraine, un brief malformé ne peut pas l'effacer.

`extra_context` n'est transmis que s'il est non vide — la signature
historique reste valable pour tout graphe qui l'ignore, doublures de test
comprises. Il entre dans la clé de cache : deux lectures du même symbole sont
deux questions différentes.

Le brief est **factuel et sans conclusion**, et demande explicitement les
points de désaccord. Un brief qui vend le setup obtiendrait un accord poli et
sans valeur — un test le vérifie.

### Autorité des analystes : la taille, rien d'autre
Un désaccord de direction **abaisse** la conviction (plafonnée à 0.2), donc
la taille. Il n'annule ni n'inverse la décision déterministe. Péremption
45 min : un avis plus vieux décrit un marché qui n'existe plus, et compte
comme une absence d'avis.

## ✅ Observateur de marché et fenêtres (07/08/2026)

**149/149 symboles visibles.** `selectionner_univers(mt5, tous=True)`. Ce
n'est pas cosmétique : sans `symbol_select`, MT5 ne synchronise pas
l'historique et toute lecture rend un tableau vide **sans erreur**.

**Fenêtres de graphique** : `ChartOpen`/`ChartClose`/`ChartIndicatorAdd`
n'existent **que** côté MQL5, l'API Python ne les expose pas. D'où
`titanium_charts.mq5` — V14 écrit la liste voulue, l'EA fait converger le
terminal. Plafond de 8 fenêtres des deux côtés : chaque graphique consomme un
flux de ticks, et c'est ce même flux qui alimente la boucle.

L'EA ne ferme **que** des graphiques portant `titanium_zones` — fermer une
fenêtre ouverte à la main serait une prise de contrôle non demandée.

⚠️ **Geste manuel requis une fois** : glisser `titanium_charts` sur un
graphique. Aucune API ne permet d'attacher un EA depuis Python. Il persiste
ensuite dans le profil.

## 🐛 Le bug qui rendait TOUT invisible sur MT5 (07/08/2026)

`dossier_mql5_files()` prenait « le dossier de terminal le plus récemment
modifié ». Heuristique **auto-réalisatrice** : y écrire notre propre fichier
le rendait le plus récent, ce qui verrouillait le choix sur le premier venu.

Depuis que l'instance de test du metatester a créé un second dossier
(`5456DB5F…`), V14 écrivait zones ET demandes de fenêtres dans un dossier
que le terminal actif (`D0E8209F…`) ne lisait pas. Aucune erreur nulle part :
les fichiers étaient bien écrits, le terminal les cherchait ailleurs.

Corrigé en **demandant le chemin à MT5** (`terminal_info().data_path`). Le
repli par date ne sert plus que si MT5 est injoignable.

⚠️ Règle générale : quand MT5 connaît une valeur, la lui demander plutôt que
la déduire. Même leçon que `_SUPPORT_PILLARS` lu à la source.

## Plafonds relevés — 8 positions + budget de risque (07/08/2026)

`MAX_POSITIONS` 3 → **8**, mais **jamais seul** : `MAX_RISQUE_CUMULE_PCT = 6`.

Compter les positions ne borne PAS l'exposition. À 1.75 % chacune, huit
positions font 14 %. Et l'univers est saturé de corrélations — EURUSD,
GBPUSD, AUDUSD, NZDUSD longs, c'est quatre fois le même pari contre le
dollar. Sans budget global, augmenter le nombre de trades **multiplie une
exposition unique au lieu de la diversifier**.

`_risque_engage_pct()` mesure la perte si tous les stops tombaient ensemble.
Une position sans stop compte pour le plafond dur — sinon la plus dangereuse
passerait pour gratuite.

## Tableau de bord — quatre blocs neufs

Il n'était pas figé techniquement (rafraîchissement 10 s, battement à 31 s) :
son **contenu** l'était. Il affichait `max_positions: 3` codé en dur alors
que la boucle en autorisait 8 — un tableau qui ment est pire qu'un tableau
vide, il inspire confiance. Les plafonds sont désormais lus à la source par
analyse AST de `live_demo.py` (l'importer exécuterait son parsing d'arguments).

Ajoutés : **Budget de risque** (jauge d'occupation), **Analystes LLM**
(avis récents, désaccords en orange), **PROD fantôme** (fréquence du quorum
strict), et la ligne **fenêtres demandées** dans le pont MQL5.

Vérifié au navigateur : aucune erreur console, aucun débordement horizontal
à 1600 px ni à 420 px.

## Ce qui reste

Une seule chose, et elle ne dépend pas du code :

- **Accumuler des trades pour mesurer l'edge.** `titanium/edge.py` lit un
  journal append-only et rend un verdict par contexte, mais il faut
  `MIN_SAMPLES = 20` trades clos par contexte. Aujourd'hui : **0**. Tant que
  l'edge est inconnu, le mode PROD bloque tout — par conception, c'est le
  correctif du fail-OPEN de V12.
- **Armer la démo** quand tu le décides : `TITANIUM_EXEC_ENABLED=1`. C'est ce
  qui déclenchera l'accumulation ci-dessus.

Le moteur de chandeliers complet, la boucle de gestion et la machinerie d'edge
sont livrés.

## Audit Prime du contrat de mesure (09/08/2026)

Le journal live n'accepte désormais que `net_devise / risque_devise` avec un
contexte d'entrée complet. Une position adoptée après perte d'état ne peut pas
reconstituer la perception de la porte : elle est mise en quarantaine à la
clôture au lieu d'alimenter un faux seau `?|?|0p`.

`cost_r` reste la décomposition spread + commission + swap + fee. Le spread de
la boucle étant une estimation pré-ordre, `exact_cost` reste faux tant qu'une
mesure issue des fills n'existe pas. Le PnL, lui, reste le net comptable MT5.

Le battement porte maintenant l'entonnoir amont complet (catalogue, sélection,
portabilité, features, S0..S4, piliers manquants et refus). Détail du prochain
lot backfill et de la réparation contrôlée du journal :
`docs/PLAN_RENTABILITE_PRIME.md`.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **titanium-v14** (8490 symbols, 17838 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/titanium-v14/context` | Codebase overview, check index freshness |
| `gitnexus://repo/titanium-v14/clusters` | All functional areas |
| `gitnexus://repo/titanium-v14/processes` | All execution flows |
| `gitnexus://repo/titanium-v14/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
