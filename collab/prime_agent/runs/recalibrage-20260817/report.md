# Recalibrage 17/08/2026 — « V14 exécute trop de trades perdants »

Tâche : `d4f50ef1-ab02-4873-95c2-b0236be3ab9d` (statut `review`).

## Mission
Vérifier la plainte « trop de trades perdants » sur données réelles et recalibrer
ce qui est mesurable, sans toucher à la méthode de confluence.

## Constat (128 trades clos, 10→17/08/2026, démo 10055401, mode explore)
- Espérance −0.229 R/trade, IC95 [−0.385 ; −0.069], total −29.3 R, 73 perdants sur 128.
- 61.6 % des perdants n'atteignent jamais +0.3 R de MFE ⇒ défaut d'ENTRÉE, pas de gestion.
  Contre-épreuve : rejeu TP fixe 0.75/1.0/1.25/1.5/2.0 R ⇒ meilleur cas −22.7 R.
- Croisements/exotiques FX : 42 trades, −21.3 R (73 % de la perte), réussite 26 %,
  coût 0.111 R contre 0.081 R sur majeures.
- Shorts FX : 51 trades, −23.5 R, réussite 29 %, IC95 [−0.67 ; −0.23] (exclut zéro).
- Alerte non traitée : la strate S≥3 fait pire que S=2 (−0.39 R contre −0.15 R),
  or `RESERVE_S3` lui réserve deux créneaux.

## Changements
| Fichier | Nature |
|---|---|
| `titanium/edge.py` | `FX_NEGOCIABLES`, `est_paire_fx()`, `fx_illiquide()` (reconnaissance ISO, valable hors session MT5) |
| `tools/live_demo.py` | purge du catalogue avant rotation + compteur tunnel `flow/fx_illiquides_ecartes` |
| `tools/live_demo.py` | `FX_SHORTS_SUSPENDUS = True` + refus compté `post_enter_refusal/FX_SHORT_SUSPENDU` |
| `tests/test_univers_liquide.py` | 4 tests de verrouillage (nouveau) |
| `docs/RECALIBRAGE_20260817.md` | rapport de mesure (nouveau) |
| `results/recalibrage_20260817.json` | chiffres bruts reproductibles (nouveau) |

Aucun changement de R:R, de quorum, de stop, de trailing, de breakeven, de sizing,
ni de `.env`. Aucun service démarré/arrêté, aucun ordre, aucune position touchée.

## Rejeu sur l'échantillon
| Univers | Trades | Perdants | Total | Espérance | Réussite | 2ᵉ moitié |
|---|---|---|---|---|---|---|
| Tel quel | 128 | 73 | −29.30 R | −0.229 R | 43 % | −0.068 R |
| Sans FX exotiques | 86 | 41 | −8.01 R | −0.093 R | 52 % | +0.043 R |
| + shorts FX suspendus | 72 | 33 | −3.00 R | −0.042 R | 54 % | +0.086 R |

## Tests
- `.venv\Scripts\python.exe -m pytest tests/test_univers_liquide.py tests/test_classe_actif.py -q` → 9 passed.
- `.venv\Scripts\python.exe -m pytest -q` → **1924 passed, 2 skipped, 69 subtests** (204 s).
- `ruff check` : 7 erreurs, **identiques à HEAD** (pré-existantes, aucune introduite).
- Fumée hors MT5 : `fx_illiquide` écarte USDZAR/AUDCAD/CHFSEK, garde EURUSD(.fs), EURJPY,
  XAUUSD, BTCUSD, US500, UKOIL.

## Risques résiduels
- Une semaine, un régime de marché : la suspension des shorts FX est **réversible** et
  doit être rejugée sur échantillon plus long (le verdict reste journalisé en shadow).
- L'espérance filtrée reste négative (−0.04 R, IC95 contient zéro) : ce recalibrage
  supprime une perte structurelle de spread, il ne crée pas d'edge.
- Le filtre réduit l'univers FX à 10 paires : l'accumulation par classe `fx` ralentira,
  donc la promotion PROD de cette classe aussi. Assumé.
- `results/selection_actifs.json` est absent : la rotation reste uniforme. Prochaine
  action utile : produire la hiérarchie via `tools/classement_backtest.py`.
