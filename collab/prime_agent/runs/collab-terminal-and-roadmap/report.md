# Rapport — Terminal de collaboration multi-LLM + Axes d'amélioration V14

**Date** : 2026-08-10  
**Auteur** : Prime Agent  
**Tâches** : `61f6409e` (Terminal) + audit roadmap

---

## 1. V14 Collab Terminal — Mise en production

### Livrable

Le terminal est opérationnel sur `http://127.0.0.1:8097`.  
Lanceur : `COLLAB_TERMINAL.bat` ou `python tools/collab_terminal/server.py`.

### Architecture

```
tools/collab_terminal/
├── server.py              ← serveur HTTP stdlib (même contrat que dashboard.py)
├── static/
│   ├── index.html         ← interface SPA
│   ├── terminal.css       ← dark mode professionnel
│   └── terminal.js        ← client avec polling 8s
├── chat_log.ndjson        ← journal de chat append-only
└── __init__.py
```

### Fonctionnalités implémentées

| Fonctionnalité | État | Description |
|---|---|---|
| **Chat temps réel** | ✅ | Messages entre Florent, Prime, Claude, Codex, Hermes, Copilot. Journal append-only. Filtrage de secrets. |
| **Liste des tâches** | ✅ | Affichage du journal commun `collab/tasks.ndjson`. Filtres par statut. Création de tâches. |
| **Répartition par LLM** | ✅ | Barres de distribution par agent et par statut, visibles sur le dashboard. |
| **Niveaux de validation multi-LLM** | ✅ | Matrice de validation croisée (qui valide le travail de qui). Indicateurs de validation par tâche. |
| **Scoring de performance** | ✅ | Score 0-100 par tâche (priorité + statut + validateurs + preuves). Classement des agents. |
| **Leaderboard** | ✅ | Classement par score moyen, taux de complétion, métriques par agent. |
| **Monitoring services** | ✅ | Surveillance des ports dashboard (8095), collab_hub (8770), hermes (8766). |
| **Heartbeat trading** | ✅ | Lecture de `results/loop_heartbeat.json` : équité, portables, tunnel. |
| **Rapports Prime** | ✅ | Affichage des rapports `collab/prime_agent/runs/*/report.md`. |
| **Bus d'activité** | ✅ | Lecture du flux `stream.ndjson` et `acks.ndjson` V12/V14. |

### API REST

| Route | Méthode | Description |
|---|---|---|
| `/api/state` | GET | État complet (tâches, performance, validation, services, chat, heartbeat) |
| `/api/tasks` | GET/POST | Liste des tâches scorées / Créer ou mettre à jour une tâche |
| `/api/chat` | GET/POST | Historique du chat / Envoyer un message |
| `/api/performance` | GET | Métriques de performance par agent |
| `/api/validation` | GET | Matrice de validation croisée |
| `/api/services` | GET | Statut des services |
| `/api/agents` | GET | Profil de chaque agent LLM |
| `/api/heartbeat` | GET | Dernier battement de la boucle trading |
| `/api/reports` | GET | Rapports Prime Agent |

### Contrat de sécurité

- **Lecture seule** sauf journal de chat et journal de tâches (même contrat que le Command Center)
- **Aucune route** ne passe d'ordre, ne redémarre un service, ni n'appelle un LLM
- **Redaction automatique** des secrets dans le chat et les messages bus
- **127.0.0.1 uniquement** : aucune exposition réseau

### Tests

```
14 passed in 0.49s
```

TestRedact, TestScoring, TestPerformance, TestValidationMatrix, TestChat, TestAPIState.

---

## 2. Axes d'amélioration V14 — Roadmap priorisée

### Diagnostic de l'état actuel

| Dimension | État | Score |
|---|---|---|
| **Architecture** | Fork TradingAgents intégré, briques V12 portées, 44 modules titanium | 🟢 8/10 |
| **Tests** | 939 passed (363 titanium + 576 socle), 89 fichiers de test | 🟢 9/10 |
| **Collaboration** | Bus Claude/Codex/Hermes, Command Center, terminal multi-LLM | 🟢 8/10 |
| **Exécution** | MT5 désarmé, 8 positions ouvertes, 741 ENTER, shadow prod actif | 🟡 6/10 |
| **Rentabilité** | Tunnel mesuré mais pas encore prouvé après coûts OOS | 🔴 3/10 |
| **Documentation** | CLAUDE.md, AGENTS.md, LECONS.md, plans, rapports | 🟢 7/10 |
| **Observabilité** | Dashboard, heartbeat, excursions, shadow, réconciliation | 🟡 6/10 |

### Axes prioritaires

---

#### AXE 1 — Mesure d'edge net après coûts (P0, critique)

**Problème** : 741 signaux ENTER, 8 ordres envoyés, mais aucune preuve OOS nette
que le système est rentable après spread + commission + swap. La promotion C3
est fermée.

**Actions** :
1. Collecter 20+ trades clos avec coûts exacts (fills MT5, pas estimations)
2. Calculer pnl_r net, Sharpe net, drawdown max
3. Comparer au spread moyen par actif pour identifier les instruments rentables
4. Walk-forward sur les 3 meilleurs actifs avant toute généralisation

**Métrique de succès** : pnl_r moyen net > 0 sur 20 trades OOS, Sharpe > 0.3

**Risque** : overfit si on optimise sur moins de 20 trades par actif

---

#### AXE 2 — Sélectivité et filtre de coût (P0)

**Problème** : Le tunnel montre 741 ENTER mais seulement 8 ordres (post-refusal:
MAX_PAR_SYMBOLE 347, RISKGATE_DENY 212, RESERVE_S3 160). Trop de signaux,
pas assez de qualité.

**Actions** :
1. Réactiver le filtre de coût dans le chemin critique (leçon E2)
2. Durcir les seuils de confluence : exiger S3+ au lieu de S1+
3. Ajouter un filtre ATR/spread pour éliminer les actifs trop coûteux
4. Mesurer le taux de conversion ENTER → profit après chaque changement

**Métrique** : ratio ENTER/profit > 40%, actuellement inconnu

---

#### AXE 3 — Walk-forward automatisé (P1)

**Problème** : Les réglages sont validés manuellement. Pas de pipeline
walk-forward continu qui détecte la dégradation.

**Actions** :
1. Implémenter un pipeline walk-forward par actif (IS/OOS glissant)
2. Stocker les résultats dans `results/walk_forward/`
3. Intégrer dans le dashboard : courbe de PnL, dégradation détectée
4. Alerte si Sharpe OOS tombe sous 0

**Métrique** : Sharpe OOS stable sur 3 mois glissants

---

#### AXE 4 — Coûts de délibération LLM (P1)

**Problème** : La délibération multi-agents LLM coûte en tokens. Sur 149 actifs,
c'est intenable. Actuellement non mesuré.

**Actions** :
1. Instrumenter les appels LLM : tokens in/out, coût $/appel, latence
2. Ajouter un budget maximal par jour/semaine
3. Ne délibérer que sur les actifs S3+ (réduction 10-20x)
4. Afficher le coût cumulé dans le dashboard

**Métrique** : coût LLM < 5% du PnL brut attendu

---

#### AXE 5 — Backtest cohérent avec le live (P1)

**Problème** : `titanium/backtest.py` existe (15k chars) mais la convention de
coûts ne concorde pas nécessairement avec le live (leçon E3).

**Actions** :
1. Audit de cohérence backtest ↔ live (conventions de spread, slippage)
2. Backfill MT5 avec `source="backfill"` (plan docs/PLAN_RENTABILITE_PRIME.md)
3. Valider que le backtest reproduit les trades live à ±5% de PnL
4. Documenter les divergences connues

**Métrique** : écart backtest/live < 10% sur le PnL net

---

#### AXE 6 — Corrélation inter-actifs (P1)

**Problème** : Leçon E4 — 6 positions yen corrélées à 0.69 sous un budget
respecté. Le regroupement par étiquette ne protège pas.

**Actions** :
1. `titanium/correlation.py` existe (9.7k) — vérifier qu'il est branché
2. Ajouter un plafond de risque par cluster de corrélation
3. Mesurer la corrélation glissante sur les 20 derniers jours
4. Bloquer une entrée si la corrélation du portefeuille dépasse 0.6

**Métrique** : corrélation max du portefeuille < 0.5

---

#### AXE 7 — Terminal de collaboration étendu (P2)

**Problème** : Le terminal est opérationnel, mais il peut être enrichi.

**Actions** :
1. ✅ Mise en production initiale (fait)
2. WebSocket pour le chat temps réel (sans polling)
3. Intégration directe des appels LLM depuis le terminal (proxy Claude/Gemini)
4. Historique des décisions de trading avec contexte LLM
5. Mode "revue de code" avec diff intégré

---

#### AXE 8 — Git et CI/CD (P2)

**Problème** : V14 n'est pas encore sous Git. Aucune branche, aucun historique.

**Actions** :
1. `git init` + premier commit
2. `.gitignore` complet (résultats, .env, __pycache__, .venv)
3. Pre-commit hooks : ruff + pytest subset
4. CI GitHub Actions (déjà configuré dans `.github/`)

---

#### AXE 9 — Réconciliation automatisée (P2)

**Problème** : `reconciliation_mt5.json` existe (31k) mais la boucle de
réconciliation n'est pas automatique.

**Actions** :
1. Automatiser le rapprochement journal/MT5 après chaque clôture
2. Alerter si une position MT5 n'a pas de contrepartie dans le journal
3. Intégrer dans le heartbeat

---

#### AXE 10 — Observabilité avancée (P3)

**Actions** :
1. Métriques Prometheus exportées par la boucle
2. Grafana ou intégration dashboard pour les courbes temps réel
3. Alertes email/Telegram sur drawdown ou erreur critique

---

### Prioritisation finale

| Priorité | Axe | Effort estimé | Impact |
|---|---|---|---|
| **P0** | AXE 1 — Edge net OOS | 1-2 semaines | Critique — raison d'être de V14 |
| **P0** | AXE 2 — Sélectivité | 2-3 jours | Fort — réduit le bruit |
| **P1** | AXE 3 — Walk-forward | 1 semaine | Fort — détecte la dégradation |
| **P1** | AXE 4 — Coûts LLM | 2-3 jours | Moyen — maîtrise du budget |
| **P1** | AXE 5 — Backtest cohérent | 1 semaine | Fort — crédibilité |
| **P1** | AXE 6 — Corrélation | 2-3 jours | Fort — protection du capital |
| **P2** | AXE 7 — Terminal étendu | continu | Moyen — productivité |
| **P2** | AXE 8 — Git/CI | 1 jour | Moyen — traçabilité |
| **P2** | AXE 9 — Réconciliation | 2-3 jours | Moyen — fiabilité |
| **P3** | AXE 10 — Observabilité | 1 semaine | Faible court terme |

---

## 3. Preuves

### Terminal
- Serveur : `http://127.0.0.1:8097` — actif, toutes APIs répondent
- Tests : `14 passed in 0.49s` (`tests/test_collab_terminal.py`)
- Chat fonctionnel avec 2 messages de test
- Tâches : 8 tâches affichées avec scoring

### Fichiers créés
- `tools/collab_terminal/server.py` — backend (15k chars)
- `tools/collab_terminal/static/index.html` — interface (9k chars)
- `tools/collab_terminal/static/terminal.css` — styles (17k chars)
- `tools/collab_terminal/static/terminal.js` — client (16k chars)
- `tools/collab_terminal/__init__.py`
- `COLLAB_TERMINAL.bat` — lanceur Windows
- `tests/test_collab_terminal.py` — 14 tests
- `collab/prime_agent/runs/collab-terminal-and-roadmap/report.md` — ce rapport

### Commandes de vérification

```powershell
# Lancer le terminal
.venv\Scripts\python.exe tools\collab_terminal\server.py

# Ouvrir dans le navigateur
start http://127.0.0.1:8097

# Tests
.venv\Scripts\python.exe -m pytest -xvs tests/test_collab_terminal.py
```

## 4. Risques résiduels

- Le terminal utilise le polling HTTP (8s). Un WebSocket serait plus réactif pour le chat.
- Le scoring est calculé côté serveur sans historique complet des transitions.
- Copilot n'a pas encore de tâches dans le journal — son intégration est préparée mais pas active.
- La validation multi-LLM est limitée au champ `updated_by` — un système de votes explicites serait plus précis.


---

## 5. Upgrade v2 — Chat bidirectionnel avec dispatch multi-canal

### Problème résolu

Le chat v1 était un journal local isolé. Les messages envoyés dans le terminal
n'atteignaient aucun agent. La v2 connecte le terminal aux vrais canaux de
communication.

### Architecture de routage

```
                      ┌─────────────────┐
   Florent ──POST──>  │  Collab Terminal │
                      │   (port 8097)    │
                      └───┬──────┬───┬──┘
                          │      │   │
              ┌───────────┘      │   └──────────────┐
              v                  v                  v
    ┌─────────────────┐  ┌──────────────┐  ┌────────────────┐
    │  Hub MCP (8770)  │  │ Bus fichier  │  │ Hermes MCP     │
    │  SQLite durable  │  │ stream.ndjson│  │  (port 8766)   │
    │  344+ messages   │  │ 484 messages │  │  Telegram      │
    └────┬───┬───┬────┘  └──────────────┘  └────────────────┘
         │   │   │
         v   v   v
      Claude Codex Hermes
```

### Ce qui est connecté

| Canal | Direction | Statut |
|---|---|---|
| **Hub MCP** (8770) | Terminal -> Hub -> Agents | ✅ Opérationnel (344 messages, WAL) |
| **Hub MCP** (8770) | Hub -> Terminal (ingestion) | ✅ Poller 5s actif |
| **Bus fichier** | Terminal -> Bus | ✅ Écrit dans stream.ndjson |
| **Bus fichier** | Bus -> Terminal (ingestion) | ✅ Poller 5s actif |
| **Hermes MCP** (8766) | Terminal -> Hermes bridge | ✅ Marqué dans les routes |

### Preuve de routage

```
Message de Florent vers "all" :
  Routes: ['hub->hermes', 'hub->codex', 'hub->claude', 'bus', 'hermes-bridge']

Message de Florent vers "claude" :
  Routes: ['hub->claude', 'bus']

Message de Florent vers "hermes" :
  Routes: ['hub->hermes', 'bus', 'hermes-bridge']
```

Messages vérifiés sur le Hub MCP (offsets 346-350) :
```
  offset=346 [florent] -> hermes  | [Terminal] Bonjour a tous les agents...
  offset=347 [florent] -> codex   | [Terminal] Bonjour a tous les agents...
  offset=348 [florent] -> claude  | [Terminal] Bonjour a tous les agents...
  offset=349 [florent] -> claude  | [Terminal] Claude, peux-tu faire une revue...
  offset=350 [florent] -> hermes  | [Terminal] Hermes, quel est l'etat de ta revue...
```

### Ingestion automatique

Le poller ramène dans le chat :
- **177 messages** du hub (historique complet Claude/Codex/Hermes)
- Les messages récents du bus fichier
- Dédoublonnage par ID et signature (from+content)

### Tests v2

```
17 passed in 0.81s (v2)
28 passed in 1.85s (non-régression)
```

### Quand les agents répondront

- **Claude** : verra les messages à sa prochaine session quand il lira le hub
  (`collab_read after_offset=345`)
- **Codex** : idem, ses sessions lisent le hub au démarrage
- **Hermes** : les messages sont sur le hub ET marqués pour le bridge Hermes.
  Les réponses Hermes apparaîtront dans le terminal via le poller.
- **Copilot** : pas encore connecté au hub (roadmap AXE 7)
- **Prime** : c'est moi. Je réponds directement.
