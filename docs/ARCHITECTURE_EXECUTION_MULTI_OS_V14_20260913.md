# Architecture d'exécution multi-OS et déploiement — Titanium V14

**Date :** 13 septembre 2026
**Objet :** où faire tourner quoi (Windows natif / WSL2 / Linux), comment relier MT5 au moteur,
comment livrer l'application sur **Windows et macOS**, et quelle est la **recommandation unique**
de déploiement.
**Prérequis de lecture :** `DOSSIER_PRODUCTION_V14_20260913.md`, §2.

---

## 1. Le budget de latence, décomposé et honnête

« Sans aucune latence » n'existe pas. Ce qui se pilote, c'est la **somme** des sauts :

| Saut | Ordre de grandeur | Maîtrisable ? |
|---|---|---|
| Décision interne (feature → gate → verdict) | 30 ms mesuré (`build_feats` sans panel) | oui, par la conception |
| Cadence du tour de boucle | **10 000 ms** (constante `intervalle`) | oui — c'est le vrai levier |
| MT5 Python API (`account_info`, `positions_get`) | ~1 ms typique, IPC/COM Windows | oui, en réduisant le nombre d'appels |
| Socket loopback 127.0.0.1 | dizaines de µs | oui |
| Traversée WSL2 ↔ hôte si Python y tourne | **0,5 à 2 ms** (votre propre estimation) | oui, en **ne le faisant pas** |
| Aller-retour courtier (VPS → serveur) | 5 à 50 ms | non, hors de portée |

**Lecture :** la moyenne mobiles des postes maîtrisables pèse moins de 50 ms contre un tour de
**10 s**. Le déterminisme est atteignable ; la « vitesse » ne changera pas la rentabilité. Toute
décision d'architecture ci-dessous est prise pour la **robustesse** et le **déterminisme**, pas pour
gagner des microsecondes.

---

## 2. Où faire tourner quoi — la répartition retenue

### Décision : **le moteur de trading reste natif Windows. WSL2 ne porte que le calcul.**

Justification, dans l'ordre :

1. **MT5 n'existe que sous Windows.** L'API Python officielle est une passerelle IPC/COM vers
   `terminal64.exe`. Faire tourner le moteur dans WSL2 pour qu'il parle à MT5 **ajoute** un saut
   réseau virtuel (0,5–2 ms) et un second système de fichiers, là où la version actuelle parle
   directement au terminal. Vous perdriez sur les deux tableaux.
2. **Votre propre texte le dit** : « faire tourner le pont Python directement sous Windows natif…
   réduit encore cet écart ». C'est exact, et c'est aussi ce que fait déjà `desktop/lib/runtime.js`
   (`projectRoot/.venv/Scripts/python.exe`).
3. **Ce que Linux fait mieux n'est pas sur le chemin chaud** : backtests, arènes de mesure, entraînement
   JEPA, cortex LLM, workers d'analyse, dashboard. Ces charges sont **CPU/mémoire longues**, tolèrent
   1 ms de latence, et gagnent au fork/cgroup/`tmpfs`.

### Répartition cible

```
┌─ WINDOWS NATIF (obligatoire) ──────────────────────────────────────┐
│  terminal64.exe (MT5)                                              │
│  ├─ titanium_zones.mq5 / titanium_link.mq5  (EA : œil + main)      │
│  └─ moteur V14  (.venv\Scripts\python.exe)                         │
│       ├─ boucle de trading (tools/live_demo.py)   ← chemin chaud   │
│       ├─ lecture prix/compte/positions (MT5 Python API)            │
│       └─ pont socket loopback vers l'EA                            │
└────────────────────────┬──────────────────────────────────────────┘
                         │ fichier partagé (JSON/NDJSON) — contrat de recherche
┌────────────────────────▼──────────────────────────────────────────┐
│  WSL2 / Linux (tolérant à la latence)                             │
│  ├─ backtests, arènes, mesure des 17 techniques, JEPA             │
│  ├─ cortex : workers Claude/Codex, Ollama                         │
│  └─ dashboard FastAPI/HTMX + k3s (titanium-api)                   │
└───────────────────────────────────────────────────────────────────┘
```

**Interface entre les deux mondes : le système de fichiers.** Le moteur Windows écrit l'état
(heartbeat, tunnel, positions) ; WSL2 le lit. C'est déjà l'architecture actuelle
(`results/loop_heartbeat.json`, `results/refus_live.ndjson`), elle est **le bon choix** : un fichier
partagé est observable, rejouable, et ne peut pas bloquer le chemin chaud. Ne le remplacez pas par
un bus temps réel.

---

## 3. Le pont EA ↔ moteur : trois transports comparés

### 3.1 Ce qui existe aujourd'hui (mesuré)

| Chemin | Mécanisme | Fichiers |
|---|---|---|
| Affichage MT5 (zones, niveaux) | **dépôt de fichiers** dans `MQL5/Files` | `titanium/bridge/mt5_zones.py` → `titanium_zones.mq5` |
| Prix, compte, positions | **API Python MT5** (IPC/COM) | `titanium/data/mt5_vendor.py` |
| Rejeu / backtest graphique | fichiers | `titanium_replay.mq5`, `titanium_charts.mq5` |

**Aucun pont socket n'existe entre V14 et MT5.** Précision vérifiée, parce qu'un « pas de socket »
global serait faux : le dépôt utilise déjà des sockets, mais ailleurs —
`titanium/web/command_center.py:60`, `titanium/web/cortex_status.py:92` et
`tools/collab_terminal/server.py:648` font des **sondes de vivacité** par
`socket.create_connection(("127.0.0.1", port), timeout=…)` sur un port local, et
`tools/enregistreur_carnet_binance.py:481` consomme le **flux L2 Binance** via `websockets`
(dépendance optionnelle). Le pont MT5, lui, reste fichier + API Python ; c'est le seul chemin dont
il est question ici, et c'est le seul à ajouter.

Autrement dit : poser un socket entre Python et l'EA serait la **troisième** utilisation du réseau
local dans ce projet, pas la première — l'outillage est donc déjà connu de l'équipe.

### 3.2 Les trois options

| | **A. Fichiers `MQL5/Files`** (aujourd'hui) | **B. TCP loopback, `Socket*` natifs MQL5** | **C. ZeroMQ** |
|---|---|---|---|
| Latence | ~10–100 ms (sonde par `OnTimer`) | **dizaines de µs** | dizaines de µs |
| Dépendance à embarquer | aucune | **aucune** (bibliothèque standard MQL5) | **`libzmq` + wrapper DLL compilé** |
| Auditabilité | totale (rejouable) | bonne (frames loggables) | moyenne |
| Fonctionne dans le **Strategy Tester** | **oui** | **non** (les sockets y sont indisponibles) | non |
| Complexité de reprise (reconnexion, backpressure) | nulle | faible | moyenne |
| Bloquant ? | non (`OnTimer`) | oui si mal écrit → `SocketRead` avec timeout obligatoire | oui |

Les fonctions `SocketCreate`, `SocketConnect`, `SocketSend`, `SocketRead`, `SocketClose` font partie
de la **bibliothèque standard MQL5** ([docs MetaQuotes](https://www.mql5.com/en/docs/network)) —
il n'y a **rien à compiler ni à embarquer**. C'est la différence décisive avec ZeroMQ, qui exige de
livrer `libzmq` et une DLL d'interface à côté de l'EA.

### 3.3 Recommandation

> **Option B pour le live, option A conservée pour le tester. Pas de ZeroMQ.**

Trois raisons : (1) le tester ne connaît pas les sockets — et vos mesures scellées en dépendent, donc
le chemin fichier **reste** de toute façon ; (2) zéro dépendance native à embarquer, donc rien à
auditer de plus ; (3) sur un loopback, ZeroMQ n'apporte que ce qu'il faut de toute façon écrire
soi-même (reconnexion, framing).

### 3.4 Le contrat de message à imposer

Peu importe le transport, cinq propriétés doivent tenir, sinon le pont devient un risque :

1. **Framing** : longueur explicite en en-tête, puis charge. Un JSON sans préfixe de taille se
   désynchronise au premier octet perdu.
2. **Idempotence** : chaque intention porte une clé (`symbole + sens + id de barre`). Le système a
   déjà cette clé — c'est elle qui produit `TRACE_DUPLICATE`. Le pont ne doit jamais envoyer deux
   fois la même clé.
3. **Acquittement** : l'EA répond `ack`/`reject` avec la clé. Pas d'`ack` → l'intention reste
   **pendante**, jamais « envoyée ». Le journal actuel (`execution_ledger`) est le bon propriétaire.
4. **Fail-closed** : socket absente, EA muet, réponse illisible → **WAIT**, jamais une entrée.
   C'est la règle de la campagne macro ; elle doit s'appliquer identiquement ici.
5. **Budget de temps** : `SocketRead` toujours avec délai explicite, jamais bloquant au-delà de la
   cadence du tour. Une lecture sans délai qui bloque fige la boucle — c'est le mode d'échec
   classique de ce type de pont.

**Ce que le pont fait gagner, en toute honnêteté :** pas des entrées supplémentaires. Trois choses
réelles : l'EA peut gérer stop et trailing **sans** repasser par Python (utile quand la machine
sature), l'affichage devient poussé au lieu d'être relu, et le chemin chaud cesse de dépendre de la
passerelle COM pour les données que l'EA a déjà en mémoire.

---

## 4. Livrer sur Windows **et** macOS — cinq blocages mesurés

Le shell desktop existe (`desktop/`, Electron 44, `electron-builder` 26.15.3) mais il est
**Windows-only par construction**. Voici ce qui bloque, fichier par fichier :

| # | Blocage | Fichier | Correctif |
|---|---|---|---|
| 1 | Cible de build unique | `desktop/package.json` → `build.win` seulement | ajouter `build.mac` (`dmg`, `zip`, `arm64` + `x64`) |
| 2 | Chemin Python Windows en dur | `desktop/lib/runtime.js` → `.venv/Scripts/python.exe` | résoudre `Scripts/python.exe` (Windows) **ou** `bin/python3` (macOS/Linux) |
| 3 | Tueur de processus Windows | `desktop/main.js` → `taskkill.exe /pid /t /f` | `taskkill` sous Windows, `process.kill(-pid, 'SIGTERM')` avec `detached: true` ailleurs |
| 4 | Port `8095` en dur | `desktop/lib/runtime.js` → `const PORT = 8095` | port dynamique (0 → port libre) écrit dans l'environnement du backend |
| 5 | Racine de projet en dur | `desktop/app-config.json` → `C:\\Users\\flore\\Desktop\\V14` | résoudre au premier lancement, stocker dans `userData` |

Deux sujets qu'on ne peut pas contourner et qu'il faut budgétiser :

- **Signature et notarisation macOS.** Sans certificat Apple Developer (99 $/an) et notarisation,
  Gatekeeper refuse l'application. C'est la vraie barrière du portage, pas le code.
- **Le backend Python.** Aujourd'hui l'app dépend d'un `.venv` **du dépôt** : elle ne peut pas être
  installée sur une autre machine. Deux voies : (a) installer le paquet `titanium` via `pipx`/`uv`
  et faire pointer `app-config.json` dessus — simple, exige Python ; (b) **geler** le backend
  (PyInstaller) et l'embarquer dans `resources/` — autonome, mais multiplie la taille et casse les
  mises à jour partielles.

**Recommandation :** voie (a) pour la v1 macOS, voie (b) seulement si vous vendez l'application.
Windows est déjà en avance : la cible `nsis` + `portable` d'`electron-builder` est prête.

**Un avertissement de périmètre :** le desktop pilote un bot qui parle à **MT5**, qui n'existe pas
sur macOS. Une version mac ne peut donc pas faire tourner le moteur de trading : elle doit être
**une console de supervision distante** (elle lit l'état publié par la machine Windows) ou
**l'environnement de recherche** (backtests, arènes, cortex). Prétendre le contraire produirait une
application qui s'installe et ne fait rien.

---

## 5. Recommandation de déploiement — une seule, tranchée

> **Un hôte Windows comme poste de trading (moteur + MT5 + EA + pont socket), et WSL2/k3s sur la même
> machine comme laboratoire et service de supervision. Pas de séparation en deux machines avant que
> le premier ordre ne soit passé.**

Détail :

1. **Poste Windows** — `terminal64.exe`, l'EA (`titanium_link.mq5`), le moteur (`.venv`), la boucle
   armée, le dashboard sur `127.0.0.1:8095`. C'est le seul endroit où l'ordre peut naître.
2. **WSL2 (déjà en place, 6 Go / 4 vCPU)** — backtests, arènes, cortex, k3s (`titanium-api`).
   Il lit l'état par le système de fichiers partagé.
3. **Consoles** — desktop Electron sur Windows ; sur macOS, la même console en **mode supervision**
   (lecture seule) tant que l'hôte de trading est Windows.
4. **Ce qui déclencherait un passage à deux machines** : le besoin d'un VPS proche du courtier pour
   l'exécution (latence au courtier, disponibilité). À ce moment-là, le moteur d'exécution part sur
   un VPS Windows et la recherche reste ici — mais c'est une décision d'infrastructure, pas une
   optimisation de performance, et elle n'a aucune valeur avant que la boucle n'exécute.

**Ce qu'il ne faut pas faire :** mettre le moteur dans WSL2 « pour la performance » (§2), séparer
recherche et exécution avant d'avoir un premier ordre, ou adopter ZeroMQ pour éviter d'écrire un
framing de 20 lignes.
