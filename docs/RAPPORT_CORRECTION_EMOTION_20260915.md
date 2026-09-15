# Module ÉMOTION — correction des défauts prouvés (15/09/2026)

Port du pôle émotion V12→V14 (`titanium/emotion/`, câblé dans
`tools/live_demo.py:2059` et `:2112`). Le présent lot ne touche **que**
`titanium/emotion/{engine,contexte}.py` et ajoute son banc de tests dédié.
La logique de décision de V12 (modèle circumplex, fade capitulation/euphorie,
seuil `MIN_ACTIONABLE_CONFIDENCE = 0.35`) n'est **pas** relitigée.

## Quatre défauts prouvés, quatre corrections

| # | Défaut (prouvé par sonde, pas par lecture) | Correction | Falsification |
|---|---|---|---|
| 1 | Une contribution de signal non convertible (ex. `fn` rendant `"abc"`) levait `ValueError` **hors** du `try` et crashait `compute_emotion` entier — en production, un seul signal mal nourri tuait tout l'organe | `engine.py` : la conversion `float(val)` est dans le même régime fail-safe — non convertible = donnée **absente**, le signal est omis, l'agrégation continue | retirer la garde → `test_contribution_non_convertible_est_omise_pas_un_crash` échoue ✔ |
| 2 | Le tick MT5 est en heure **serveur** (Axi = UTC+3) ; `contexte.py` le stockait brut comme fraîcheur. Mesuré sur le terminal vivant : décalage 10 800 s — un tick frais d'3 h dans le futur (âge négatif, jamais `STALE`) et un marché fermé lu 3 h trop vieux dérangeaient la fenêtre STALE **dans les deux sens** | `contexte.py` (2 sites : `live_raw_mt5`, `emotion_depuis_barres`) : `delta_ts = tick − decalage_serveur_cache((symbole,))` — la même autorité d'horloge que `get_rates` et `tests/test_horloge_serveur.py`, TTL 900 s, zéro appel MT5 supplémentaire en régime établi | retirer la soustraction → `test_tick_serveur_corrige_en_utc` échoue ✔ |
| 3 | `age is None` (producteur sans horodatage) rendait `stale=False` : « je ne sais pas quand c'a été mesuré » lu comme « c'est frais » — le même motif fail-open payé dans `macro/risk.py` (calendrier vide ⇒ `CLEAR`) et dans le garde-fou de pertes | `engine.py` : `age is None ⇒ stale=True` — la porte **attend** au lieu de passer ; cohérent avec `tests/test_emotion_contexte.py` (trouvé non suivi dans l'arbre, il figeait le contrat inverse) | retirer la correction → `test_source_sans_horodatage_est_perimee_malgre_les_bougies` échoue ✔ |
| 4 | Une confiance **négative** (poids négatif via un registre injecté) lirait « pire qu'aucune donnée » comme un passant au seuil `EMOTION_MIN_CONFIDENCE = 0.35` | `engine.py` : `confidence = max(0.0, …)` (fail-closed) | retirer le clamp → `test_confiance_jamais_negative` échoue ✔ |

## Preuve sur la vraie surface

Sonde exécutée sur le terminal MT5 **vivant** : tick EURUSD à 1,4 s d'âge
après correction (10 800 s corrigés), le chemin complet `build_context`
rend `source_age_s = 0,7` — la fraîcheur est désormais ancrée sur l'horloge
UTC partagée, pas sur l'heure murale du serveur.

## Validation

- `tests/test_emotion.py` (nouveau, 10 tests, exécutable **sans MT5** — le
  contexte est fabriqué par `RawInputs` injecté, les accès MT5 sont
  `monkeypatchés`) : **10/10** verts.
- `tests/test_emotion_contexte.py` (trouvé non suivi dans l'arbre) : **1/1**.
- `tests/test_confluence_gate.py` : 12/12 — la porte consomme le bloc corrigé
  sans changement de son côté.
- Suite complète : **3 152 passés, 2 skipped** ; les 8 échecs sont
  **attribués au bit près** : rejoués dans un worktree propre sur `HEAD`,
  ils sont **absents** (2 échecs environnementaux seulement : sceau corpus
  CRLF déjà corrigé sur `main`, worktree sans `.venv`). Les 6 supplémentaires
  (`test_correlation`, `test_live_loss_guard` ×4, `test_sizing`,
  `test_echelle`) viennent des fichiers **modifiés non commités d'autres
  agents** (`titanium/sizing.py` porte la levée de `MAX_COUT_SPREAD_PCT`
  0,125→1,00 du 14/09, `correlation.py`/`live_loss_guard.py` idem) — aucun
  n'importe `titanium.emotion`.
- Porte de lint (`tools/lint_gate.sh`, la seule, celle du hook et de la CI) :
  **verte**. `ruff check tests/test_emotion.py` (select strict) : vert.

## Restes nommés, non instruits

- `titanium/emotion/` n'est **pas commité** : le rapport du 14/09 explique
  que 7 tests rouges d'autres lots bloquent le hook pre-commit. Ce lot
  n'en ajoute aucun mais reste non suivi pour la même raison.
- Le chemin crypto complet (Binance WS : delta-volume réel, funding,
  long/short) n'existe pas dans V14 — l'émotion crypto reste plus grossière,
  la confiance baisse d'elle-même (écart assumé du port, documenté).
- Le risque macro n'entre pas dans le contexte (module sur une autre branche
  au moment du port) : donnée omise, jamais inventée.
- Validation OOS de l'organe (split IS/OOS + null par tirage) : toujours à
  faire — un avantage de principe n'est pas un résultat.
