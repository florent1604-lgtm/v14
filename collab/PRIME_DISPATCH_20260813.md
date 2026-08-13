# Dispatch Prime — exécution à quatre, 13/08/2026

Base : `master 66e3c5c`, arbre propre, suite complète **1746 passés / 2 skips**,
GitNexus réindexé (6 827 nœuds, 14 661 arêtes).

Objectif : vider le reste du backlog en parallèle, chacun **commitant son propre
lot**, sans se marcher dessus.

## Le risque à comprendre avant de commencer

Nous partageons **un seul arbre de travail**, sans branches. Un `git add -A`
emporte le travail à moitié écrit des trois autres — c'est exactement ce qui a
failli arriver ce matin, où j'ai dû découper des diffs à la main pour séparer les
lots Codex et Claude d'un même fichier.

**Règle absolue : `git add <chemins de ton périmètre>` uniquement. Jamais `-A`,
jamais `git commit -a`, jamais `git checkout`/`stash`/`reset` sur l'arbre commun.**

## Périmètres exclusifs

| Agent | Tâches | Fichiers qui t'appartiennent |
|---|---|---|
| **codex** | `ba27713d` horloge unique à la frontière MT5 · `a994727b` quarantaine `SORTIE_INTROUVABLE` résoluble | `titanium/data/mt5_vendor.py`, `titanium/analysis/reconciliation.py`, `titanium/execution/history_recovery.py`, `titanium/execution/position_manager.py`, `tests/test_history_*.py`, `tests/test_reconciliation.py`, `tests/test_position_manager.py`, `collab/CODEX_*.md` |
| **claude** | `454c1ec2` analyse des pertes depuis artefact scellé · `ce00fa9a` verrou inter-processus du journal de tâches | `tools/` (nouvel outil d'analyse), `titanium/collab_tasks.py`, `tests/test_collab_tasks*.py`, `tests/test_analyse_*.py`, `collab/CLAUDE_*.md`, `results/` (artefact scellé) |
| **hermes** | A1 : spécification `runtime_epoch_id` · vérification indépendante de chaque commit du jour · segmentation par époque du cycle des limites (`f384244f`) | `collab/HERMES_*.md` uniquement — lecture seule sur le code, comme d'habitude |
| **prime** | `3c756faf` sauvegarde cohérente (verrou, identifiant unique) · `cb312b43` clôture · `f33c21e3` reprise · rechargement contrôlé | `titanium/sauvegarde.py`, `tools/sauvegarder_resultats.py`, `tests/test_sauvegarde_resultats.py`, `collab/PRIME_*.md`, `collab/tasks.ndjson` |

`collab/tasks.ndjson` : tout le monde y écrit **via le CLI uniquement**
(`tools/task_journal.py update --actor <toi>`), jamais à la main, jamais en le
stageant — c'est moi qui le committe.

Un fichier hors de ton périmètre te bloque ? Tu le signales sur le hub, tu ne le
prends pas.

## Protocole de commit

1. Suite complète verte **avant** de stager : `.venv\Scripts\python.exe -m pytest -q`.
2. `git add` de tes chemins, un lot = un commit.
3. Message : `V14: lot <agent> - <objet>`, puis ce qui est prouvé et comment.
   Un chiffre sans reproduction n'est pas une preuve.
4. Le hook pre-commit (ruff critique + 21 tests de sûreté) doit passer. S'il
   échoue, tu corriges — tu ne contournes pas.
5. Tu publies le hash sur le hub **et** en note de tâche.
6. Si `git status` montre des fichiers d'un autre agent modifiés, c'est normal :
   tu les laisses.

## Interdits inchangés

Aucun ordre, aucun seuil, aucun `.env`, **aucun redémarrage de service ou de
MT5**. Le rechargement est un geste unique, tenu par Prime, une fois l'arbre vert
et les quatre lots posés — sinon on redémarre sur du code à moitié écrit.

## État de production

Le code en production dans MT5 **n'est pas encore celui du dépôt** : les
services tournent toujours sur le code d'avant `9c5f17f`. Récupération des
clôtures invisibles, couverture au battement, incidents d'état : rien de tout
cela ne s'exécute encore. C'est la dernière étape de la journée.
