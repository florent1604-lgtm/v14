# Première mission V14

Objectif : établir l'état réel du pipeline d'edge et corriger une incohérence technique ou documentaire prioritaire sans toucher au trading actif.

1. Lire `AGENTS.md`, `CLAUDE.md`, `titanium/edge.py`, `tools/live_demo.py` et les tests associés.
2. Vérifier si le défaut de comptage des piliers dans la clé de contexte est encore présent ou déjà corrigé.
3. Comparer les affirmations de `CLAUDE.md` avec le code et les tests actuels.
4. Si le code est déjà corrigé, mettre à jour la documentation obsolète et rechercher une seule incohérence voisine dans le flux contexte -> journal -> promotion.
5. Si un défaut réel est confirmé, ajouter un test de non-régression puis appliquer la correction minimale.
6. Ne pas lire `.env`, ne pas contacter MT5, ne pas démarrer de service et ne pas lancer de test nécessitant un fournisseur LLM.
7. Exécuter les tests ciblés puis produire `collab/prime_agent/runs/first-edge-audit/report.md` avec constat, fichiers modifiés, tests et risques résiduels.

Critère de fin : verdict prouvé sur le comptage des piliers, documentation cohérente, tests ciblés verts et aucune action sensible.
