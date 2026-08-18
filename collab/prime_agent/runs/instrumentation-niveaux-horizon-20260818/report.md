# Instrumentation en avant : niveaux d'entree et excursion a horizon fixe

Date : 18/08/2026 (reprise de session Prime)
Tache journal : `12bc0ad1-3796-4ad7-a0f8-b06acf9417c2` (status `done`)
Commits : `ec0c882` (code), `8556bbb` (journal des taches), pousses sur `origin/main`.

## Contexte

En reprenant la session, l'etat de la racine V14 montrait :

- les 5 services (dashboard, live_demo, analystes, enregistreur_quotes,
  enregistreur_carnet_binance) sains, PID stables, aucun incident au
  battement ;
- deux taches `in_progress` dans `collab/tasks.ndjson`, toutes deux des
  collectes qui n'attendent que le temps (aucun code a produire) ;
- un engagement explicite pris par Prime sur le hub le 18/08 (message
  `2873765e-15b3-4bbb-9c95-3f149a0f9296`, en reponse a une question de
  Claude sur le point d'entree) : livrer une instrumentation en avant
  (niveaux structurels a l'entree + excursion a horizon fixe) avant toute
  tranche de code sur le prix d'entree lui-meme, et prevenir Claude a la
  livraison ;
- quatre fichiers de sonde MT5 non commites (`_probe_mt5*.py/json`), issus
  d'un balayage precedent interrompu par une panne du fournisseur LLM, deja
  analyses et rapportes par Claude sur le hub (message
  `3e3daf23-d088-4c4e-8fa6-26d8d705c9bc`, 18/08 16:57Z) : M1/M15/H1 rendent
  zero barre chez ce courtier, seuls H4/D1 repondent.

Cette reprise a donc consiste a honorer l'engagement pris, pas a ouvrir une
nouvelle direction.

## Ce qui a ete livre

### 1. Niveaux structurels a la decision d'entree

Nouvelle fonction pure `tools/live_demo.py::_niveaux_entree(feats, *, entry,
side, r)` : lit `sr_level`, `vpoc`, `ote_zone`, `fvg_open` dans
`feats["_trace"]` — deja calcules par le builder pour les portes, mais
jamais journalises avec le trade — et calcule la distance entree->niveau en
R, convention de signe alignee sur `fav_r` (positif = niveau du cote
favorable).

Cablee dans les deux chemins d'ouverture : `_attacher_contexte` (ordre au
marche) et `_memoriser_contexte_limit` (ordre limite), donc couverte des le
premier trade des deux familles.

### 2. Excursion a horizon fixe

`titanium/execution/position_manager.py` :

- `TrackedState` gagne trois champs additifs : `entry_levels`, `entry_atr`,
  `horizon_excursions` (round-trip complet dans `to_dict`/`from_dict`,
  tolerant aux etats anciens qui ne les portent pas) ;
- `_maj_horizons(state, fav_r, now)` capture, une seule fois par horizon
  (+1, +4, +12 barres de la timeframe d'entree), les valeurs courantes de
  `peak_fav_r` (MFE) et `mae_r` (MAE), plus leur equivalent en unites d'ATR
  quand `entry_atr` est connu ;
- `decide_new_sl` recoit un parametre `now: datetime | None = None`
  optionnel, retrocompatible, uniquement pour piloter cette instrumentation.
  La logique de breakeven/trailing existante n'a pas change d'une ligne.

Pourquoi ce point compte : la MAE ecrite a la cloture est partiellement
circulaire (un stop touche vaut -1R par construction). L'excursion a
horizon fixe, capturee AVANT que le stop tranche, est le seul instrument qui
permette de mesurer objectivement si le prix d'entree est mal place, sans
retomber dans le biais que Claude avait lui-meme signale le 18/08.

## Preuves

| Verification | Resultat |
|---|---|
| Suite complete | 1962 passed / 2 skipped (avant ce lot : 1950) |
| Tests neufs | +12 (6 dans `test_position_manager.py`, 6 dans `test_live_demo_telemetry.py`) |
| Ruff strict, 4 fichiers touches | 14 erreurs, identique a HEAD (verifie fichier par fichier via `git show HEAD:<f> \| ruff check --stdin-filename <f> -`), aucune introduite |
| Porte critique (`E9,F63,F7,F82`) | verte |
| GitNexus `impact decide_new_sl --direction upstream` | HIGH, 8 symboles, 3 processus (`tools/rejeu_breakeven.py`, `titanium/execution/manage_loop.py`, `tools/live_demo.py`) — lu AVANT edition |
| GitNexus `detect-changes` | 6 fichiers, 18 symboles, risque HIGH (attendu : `TrackedState`/`decide_new_sl` sont sur le chemin live) |
| Non-regression de la decision de stop | test dedie `test_horizon_n_affecte_jamais_le_sl_decide` : meme trajectoire, avec et sans horloge injectee, meme SL decide |

## Fichiers modifies

- `titanium/execution/position_manager.py` (+103/-3)
- `tools/live_demo.py` (+70/-2)
- `tests/test_position_manager.py` (+86)
- `tests/test_live_demo_telemetry.py` (+82)
- `AGENTS.md`, `CLAUDE.md` (compteurs GitNexus, cosmetique, produits par le sync)
- `collab/tasks.ndjson` (append-only, tache `12bc0ad1` a `done`)

Nettoyage associe : suppression de `_probe_mt5.py`, `_probe_mt5.json`,
`_probe_mt5_range.json`, `_probe_syms.json` a la racine — scratch de la
sonde MT5 de la session precedente, deja analyse et rapporte par Claude sur
le hub ; aucune information perdue, le resultat vit dans le message du hub.

## Communication

- Message publie sur le hub (`prime -> claude`, via le terminal 8097,
  route `hub->claude` + `bus`) : livraison annoncee, preuves resumees,
  points residuels signales.
- `collab/tasks.ndjson` : nouvelle entree `12bc0ad1-3796-4ad7-a0f8-b06acf9417c2`
  a `done`, tracant l'engagement du 18/08.

## Points residuels, non bloquants

1. **`live_demo` n'a pas ete redemarre.** Le processus arme (PID 7012)
   tourne encore sur le commit `f8328e2` et ne verra cette instrumentation
   qu'au prochain redemarrage. Conforme au perimetre de Prime : redemarrer
   `live_demo` exige une demande humaine explicite distincte de la
   delegation de developpement (`.prime/agent/APPEND_SYSTEM.md`), je ne l'ai
   donc pas fait moi-meme.
2. **Index GitNexus corrompu apres le sync qui a suivi ce commit.** L'erreur
   observee : `Runtime exception: FTS index 'file_fts' is inconsistent: term
   'locaux' is missing during delete`. `gitnexus clean --force` execute via
   le wrapper `tools/gitnexus_team.ps1` n'a pas confirme la suppression
   (reste au stade de la demande de confirmation malgre `--force`). Le
   `detect-changes` cite plus haut avait deja tourne AVANT cet incident,
   donc l'evaluation de risque de ce lot reste fiable ; mais l'index restera
   perime pour le prochain agent tant que ce n'est pas repare separement.
3. Deux taches restent `in_progress` dans le journal (collecte 1 semaine
   DEMO, edge net a 20 clotures/contexte) : aucune n'attend de code, les
   deux attendent seulement l'ecoulement du temps de marche.

## Risque residuel

DEMO/PAPER uniquement. Aucun ordre envoye, aucun seuil ni garde modifie,
`positions.json` non touche. Le risque GitNexus HIGH est inherent au
perimetre (le lot touche `TrackedState`/`decide_new_sl`, sur le chemin de
gestion live) et attenue par : retrocompatibilite verifiee (parametre
optionnel, champs additifs, `from_dict` tolerant), test de non-regression
dedie sur la decision de stop, et absence de changement dans
`manage_once`/`decide_new_sl` autre que l'ajout du parametre optionnel et de
l'appel d'instrumentation isole par `contextlib.suppress(Exception)`.
