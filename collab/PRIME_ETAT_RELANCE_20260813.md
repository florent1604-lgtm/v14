# Etat V14 avant reprise - Prime, 13/08/2026 07:15 (local)

Verification faite pendant que Claude relance les services.

## Services

| Service | Etat |
|---|---|
| live_demo (boucle armee) | ARRETE |
| dashboard | ARRETE |
| analystes | ARRETE |

`tools/etat_services.py` renvoie 1 (aucun service actif).
`terminal64.exe` **n est pas lance** : `DEMARRER_V14.bat` refusera de demarrer
tant que MetaTrader 5 n est pas ouvert et connecte. C est le premier geste.

## Derniere activite

- Dernier battement : `results/loop_heartbeat.json` a **2026-08-12T22:15:25Z**
  (00:15 local) - soit environ 9 h d arret au moment de ce constat.
- Etat au dernier tour : `armed: true`, equity **4116.46**, 340 tours,
  11 868 evaluations portables, 2 885 ENTER, **19 ordres envoyes**.
- Positions suivies : `results/positions.json` = 5 entrees.
  Limites en attente : `results/pending_limits.json` (dont USDMXN expirant a 22:17Z,
  donc perimee pendant l arret).
- Cycle de vie des limites (heartbeat) : placed 22, filled 6, expired 15,
  fill_rate **0.286**, economie moyenne +0.0926R, slippage -0.0020R,
  net **-1.9555R**.

## Depot

- Branche `master`, HEAD `bde74a5`.
- **Arbre sale** :
  - modifies : `docs/LECONS.md`, `tools/rejeu_breakeven.py`
  - non suivis : `collab/CLAUDE_BREAKEVEN_REJEU.md`, `tests/test_rejeu_breakeven.py`
- Ce lot est le travail de rejeu de breakeven de Claude : il doit etre commite
  (hook pre-commit = ruff + tests de surete) avant toute nouvelle mesure.

## Tests

Derniere suite complete connue (`logs/pytest_full_20260812b.log`, 12/08 13:32) :
**1599 passes, 2 skips, 0 echec** en 184 s.

## Sequence de reprise

1. Ouvrir MetaTrader 5 et se connecter au compte DEMO.
2. `ARRETER_V14.bat` (nettoyage des restes) puis `DEMARRER_V14.bat`.
3. `.venv\Scripts\python.exe tools/etat_services.py` -> les 3 services ACTIFS.
4. Reconcilier apres l arret : `tools/reconcile_mt5_journal.py` (positions et
   limites orphelines, notamment les pendantes expirees hors ligne).
5. Verifier que `loop_heartbeat.json` avance (un tour par minute).
6. Commit du lot Claude, suite complete, reindexation `node .gitnexus/run.cjs analyze`.
7. Chaque LLM reprend sa tache : voir les notes du journal
   (`.venv\Scripts\python.exe tools/task_journal.py list`).

## Rappel qui conditionne toutes les mesures

Les clotures anterieures au correctif d horloge (`0f452c5`) portent un
`closed_at` en heure serveur etiquetee UTC, faux de 3 h. Le journal est
append-only : ces lignes restent fausses. Toute analyse temporelle doit
n utiliser que les clotures posterieures au marqueur d horloge UTC.
Promotion **fermee** : esperance -0.3834R, PF 0.361, max 3/20 clotures par contexte.
