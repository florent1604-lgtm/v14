# Ordre d'action — dispatch Prime, 12/08/2026

Base de référence : commit **5c5884e** (master) — 50 fichiers mis en production
(35 modifiés + 15 nouveaux), arbre de travail propre.

## Règle permanente (tous les LLM)

Après **chaque** mise à jour de code :
1. `git add -A && git commit` (le hook pre-commit lance ruff + tests de sûreté) ;
2. redémarrer **tous** les services du bot (`DEMARRER_V14.bat` ou `tools/collab_services.ps1`) ;
3. relancer l'indexation (`node .gitnexus/run.cjs analyze`) pour que le code neuf soit indexé ;
4. mesurer l'effet avant toute nouvelle demande.

Aucune tâche ne reste en attente, hormis l'accumulation de données DEMO MT5
qui dépend du rythme des trades.

## Contenu mis en production dans 5c5884e

- `titanium/execution/limit_orders.py` — ordres limites
- `titanium/execution/pending_context.py` — contexte des ordres en attente
- `titanium/features/ict_structure.py`, `ict_market_profiles.py` — enrichissement ICT
- `titanium/backtest.py`, `titanium/selection.py` — conventions de coût (demi-spread entrée + sortie, plus de double comptage)
- `tools/live_demo.py`, `tools/analystes.py`, `titanium/web/state.py`, UI poste
- 5 nouveaux fichiers de tests (51 tests) + catalogue de skills gitnexus unifié (48 SKILL.md, miroirs .agents/.claude/.codex/.hermes identiques, doublons imbriqués supprimés)
- correctif `tests/test_ollama_base_url.py` : assertions insensibles à la mise en forme rich

## Affectations (journal de tâches, priorité P0)

| Agent | ID tâche | Objet |
|---|---|---|
| **hermes** (gpt-5.6-sol) | `46c06912` | Audit lourd de bout en bout du chemin critique + walk-forward par actif + impact des ordres limites → `collab/HERMES_AUDIT_POST_COMMIT.md` |
| **codex** | `fd5be523` | Reprise de la collecte DEMO sur le code neuf, réconciliation MT5 100 %, journal des clusters → `collab/CODEX_EDGE_SNAPSHOT.md` |
| **claude** | `ba74e58a` | Piliers manquants (G4_OTE_OB 27769/29780, G5_CANDLE 27338/29780) : mesurer l'effet de l'enrichissement ICT et corriger les alimentations → `collab/CLAUDE_PILLAR_FIX.md` |
| **prime** | `181aaa7c` | Mise en production, redémarrage des services, réindexation, suite complète 1042+ tests |

Consulter le détail : `.venv\Scripts\python.exe tools/task_journal.py list`
Mettre à jour : `tools/task_journal.py update --id <id> --status in_progress --note "..." --actor <agent>`

## Garde-fous inchangés

- DEMO uniquement, compte 10055401. Aucun ordre sur compte réel.
- Ne pas modifier `.env` sans validation explicite de Florent.
