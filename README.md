# Titanium V14

Titanium V14 est un bot de recherche et d'exécution **MT5 DEMO** qui combine
des portes déterministes héritées de V12 et une délibération multi-agents
inspirée de V13. Le LLM ne crée jamais un signal et n'a jamais l'autorité
d'envoyer un ordre : il peut seulement moduler la taille d'un setup déjà
validé. Le dernier mot appartient aux garde-fous déterministes.

> État actuel : collecte DEMO/PAPER. Aucune promotion en réel n'est autorisée.
> Les résultats observés ne prouvent pas encore un edge rentable.

## Architecture

```text
Catalogue MT5
    │
    ├─ portabilité : marché, ATR, coût/spread, taille minimale
    │
    ├─ features multi-échelles
    │      G1 tendance/SR
    │      G2 fair value
    │      G3 liquidité
    │      G4 OTE / order block
    │      G5 bougie de confirmation
    │
    ├─ porte de confluence ── BLOCK / WAIT / ENTER
    │
    ├─ avis LLM asynchrone et fail-neutral (taille seulement)
    │
    ├─ RiskGate + réserve S3 + exposition par symbole/grappe
    │
    ├─ contrôle du coût réel et de la dérive
    │
    └─ exécuteur MT5 protégé DEMO ── journal append-only
```

Composants principaux :

- `tools/live_demo.py` : boucle de collecte, classement et exécution DEMO ;
- `tools/analystes.py` : délibérations hors chemin critique ;
- `titanium/gates/` : portes de confluence ;
- `titanium/risk/` : décision et plafonds de risque ;
- `titanium/execution/` : mur DEMO/réel, ordres et gestion des positions ;
- `titanium/features/` : structure, SMC/ICT, profils et bougies ;
- `titanium/web/` : dashboard et état opérationnel ;
- `collab/` : journal commun Codex, Claude, Hermes et Prime ;
- `results/` : données de travail locales, exclues de Git.

## Sécurité et gouvernance

Les règles suivantes sont non négociables :

1. PAPER/DEMO par défaut ; le compte et la politique sont revérifiés à chaque
   ordre.
2. Aucun secret n'est commité, affiché dans le Hub ou copié dans un rapport.
3. Aucune modification automatique de `.env`.
4. Aucun passage au réel, changement de seuil, redémarrage ou action
   destructive sans validation humaine explicite.
5. Les journaux de trades restent append-only ; une correction produit une
   nouvelle preuve au lieu de réécrire l'historique.
6. Une tâche n'est `done` qu'avec preuves et tests ; les collectes dépendantes
   du marché restent `in_progress` jusqu'au critère mesuré.

Les instructions d'autorité se trouvent dans [AGENTS.md](AGENTS.md),
[CLAUDE.md](CLAUDE.md) et `collab/HERMES_LIMITES.md`.

## Démarrage local

Pré-requis : Windows, Python 3.12, MetaTrader 5 configuré sur un compte DEMO,
et les dépendances installées dans `.venv`.

```powershell
# Vérification déterministe, sans ordre
.venv\Scripts\python.exe scan_v14.py

# Dashboard V14
.venv\Scripts\python.exe dashboard.py

# Travailleur d'analystes, séparé du moteur
.venv\Scripts\python.exe tools\analystes.py

# Boucle d'observation, aucun ordre
.venv\Scripts\python.exe tools\live_demo.py
```

Le mode armé DEMO possède plusieurs verrous et doit être lancé uniquement via
le processus opérationnel documenté dans `collab/`, après autorisation.

## Interfaces locales

Les ports peuvent évoluer ; le Command Center affiche l'état réel :

- dashboard trading : `http://127.0.0.1:8095/` ;
- terminal de collaboration : `http://127.0.0.1:8097/` ;
- Hub commun : `http://127.0.0.1:8770/` ;
- Hermes MCP : `http://127.0.0.1:8766/` ;
- GitNexus MCP : `http://127.0.0.1:4750/mcp`.

Une interface disponible ne signifie pas qu'un LLM est actif en permanence :
le Hub est asynchrone. Chaque agent lit les tâches lorsqu'il est réveillé.

## Collaboration multi-agents

- **Florent** : autorité humaine, objectifs et permissions sensibles ;
- **Prime** : orchestration, revue et vue d'ensemble ;
- **Claude** : diagnostic, features et analyses longues ;
- **Codex** : implémentation, tests, rapprochements et audits ;
- **Hermes** : avis risque consultatif, sans autorité d'exécution.

Le fichier `collab/tasks.ndjson` est la source locale append-only des tâches.
Les messages transitent par le bus commun ; une carte de synthèse est affichée
dans le terminal. Voir `collab/TERMINAL_INSTRUCTIONS.md` et
`collab/HERMES_BRIDGE.md`.

## Données et preuves

Fichiers utiles, non destinés à Git :

- `results/trades.ndjson` : clôtures et R observés ;
- `results/positions.json` : états de gestion ;
- `results/excursions.ndjson` : excursions favorables/défavorables ;
- `results/loop_heartbeat.json` : télémétrie agrégée de la boucle ;
- `results/shadow_prod.ndjson` : comparaison EXPLORE/PROD ;
- `results/candidats_grappe.ndjson` : grappe et risque engagé par candidat ;
- `results/avis_*.ndjson` et `results/cout_llm.ndjson` : flux de délibération ;
- `results/reconciliation_mt5*.json` : rapprochement MT5/journal.

Les rapports d'audit actuels se trouvent dans `collab/CODEX_*.md` et
`collab/DATA_QUALITY_WINDOWS.md`.

## Tests et qualité

```powershell
# Suite complète
.venv\Scripts\python.exe -m pytest -q

# Vérification rapide du hook versionné
git config core.hooksPath .githooks
.githooks\pre-commit

# Cohérence des skills communs
.venv\Scripts\python.exe tools\sync_llm_skills.py --check

# Synchronisation de la carte GitNexus
powershell -ExecutionPolicy Bypass -File tools\gitnexus_team.ps1 sync
```

Le hook exécute les erreurs Ruff critiques et un sous-ensemble rapide de tests.
La suite complète reste obligatoire avant une promotion sensible.

## Critères de rentabilité

V14 ne doit pas être jugé sur le nombre d'ordres. Une promotion exige au
minimum : données réconciliées, coûts cohérents, taille suffisante par contexte,
bootstrap/OOS crédible, profit factor et expectancy nets positifs, et drawdown
compatible avec le budget de risque. Tant que ces preuves manquent, la bonne
décision est de collecter sans optimiser les seuils sur le petit échantillon.

## Provenance

V14 réutilise et adapte des composants du projet open source
[TradingAgents](https://github.com/TauricResearch/TradingAgents). Les choix de
sécurité, l'intégration MT5, les portes Titanium et la collaboration locale sont
propres à ce chantier.
