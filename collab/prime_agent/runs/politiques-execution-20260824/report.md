# Lot execution — corrections demandees par Codex et Hermes

Tache : `V14-CODEX-DELEGATION-20260824`, volet PRIME (hub offsets 586 et 588).
Date : 24/08/2026. Perimetre : correction du worktree non commite du lot
« politiques d'execution sur donnees reelles ». Aucun ordre, aucun service,
aucun `.env`, aucune position touchee. PAPER/DEMO only.

## Verdict

Les six NO-GO initiaux sont corrigés. La revue finale Codex/Hermes a ensuite
trouvé trois biais résiduels : confusion entre contact et service simulé,
quatre politiques dynamiques rejouées infidèlement, et comparaison de
`v14_live` sur une cohorte différente du marché. Ces trois points sont corrigés
dans le contrat de rapport v3. Le classement a été recalculé sur 147 symboles.
**Rien n'est promu.**

## Correspondance demande -> correction -> preuve

| Demande (hub) | Correction | Preuve |
|---|---|---|
| OHLC MT5 = BID (`bid=close`, `ask=close+spread`) | `_bid_ask()` | `test_le_close_mt5_est_le_bid_et_non_le_milieu` ; mesure : l'ecart achat/vente tombe de 16.96 pts a 2.07 pts |
| Enveloppe SELL = Ask-high | `snapshots_de_matching()` : `low` = bas du BID, `high` = haut de l'ASK | `test_une_vente_est_servie_sur_le_haut_de_l_ask` (matcher reellement execute) |
| Parite exacte `v14_live` / `plan_limit_entry`, arrondi tick, fail-closed | nouveau module `titanium/execution/limit_pricing.py`, appele par les DEUX chemins | `test_la_boucle_live_et_le_simulateur_appellent_la_meme_fonction` (identite d'objet) + 10 cas de parite EXACTE + 18 cas d'echec ferme |
| TTL M5 non causal + look-ahead intra-barre | plan sur la derniere barre CLOSE, appariement sur les suivantes, snapshot de decision a enveloppe degeneree ; expiration hors grille = INDETERMINE ex ante | `test_le_snapshot_de_decision_n_a_aucune_enveloppe_future`, `test_une_expiration_hors_grille_est_indeterminee` ; mesure : `post_only` passe de 98.94 % a 58.17 % de touche |
| Exclusion des 3 politiques non directionnelles | `NON_DIRECTIONNELLES`, retirees de `POLITIQUES` | `test_les_politiques_non_directionnelles_ne_sont_pas_mesurees_par_defaut` |
| Adaptive : reutilisation de `snapshots[0]` | detection generique `sequence_intra_barre()` sur les decalages reels ; `adaptive`, `twap`, `iceberg` sortent du classement | `test_un_decoupage_intra_barre_sort_du_classement` |
| Validation obligatoire du manifeste | `valider_artefact()` : `artifact_type`, `schema_version=2`, empreinte moteur courante, puis `artefact_brut_valide` (sceaux, hashes, compteurs) | 6 tests de refus, dont `schema_version=4` REFUSE (rectification Codex offset 588) |
| Contact, franchissement et service synthétique confondus | trois métriques séparées : `taux_contact_inclusif`, `taux_franchissement`, `taux_service_synthetique_scenario` | test du contact exact sans franchissement + agrégations v3 |
| Runner M5 non fidèle à cancel/replace, pegged, VWAP et POV | quatre politiques mesurées mais exclues du classement | tests d'exclusion par motif explicite |
| Cohorte `v14_live` partielle comparée au marché global | exclusion du classement global et comparaison appariée sur `decision_id` commun | test de cohorte commune et blocage si indéterminés |
| `_prix_moyen` : jambe opposee | filtre sur le sens de l'intention | `test_une_jambe_opposee_ne_deplace_pas_le_prix_d_entree` |
| Artefacts sans `decision_id`/`decision_at`/`side` | chaque ligne les porte, plus `prix_entree_simule` ; export NDJSON de 382 200 lignes | `test_l_artefact_de_sortie_permet_de_refaire_l_appariement` |
| Recalculer rapport et classement | 2 passes completes (fenetre 12 et 3) | `classement_fenetre12.txt`, `classement_fenetre3.txt` |

## Fichiers

Crees :
- `titanium/execution/limit_pricing.py` — source unique du prix d'entree passif
- `tests/test_limit_pricing.py`
- `tools/politiques_execution_reel.py`, `tests/test_politiques_execution_reel.py`
- `docs/AUDIT_POLITIQUES_EXECUTION_20260824.md` (version 3)

Modifies :
- `titanium/execution/limit_orders.py` — delegue le prix, API publique inchangee
- `titanium/execution_sim/policies.py` — `V14LivePolicy` appelle la fonction live
- `titanium/execution_sim/runner.py` — `executer_sur_snapshots` extrait de `_run_case`
- `tests/test_execution_sim_policies.py`

`titanium/execution_sim/matching.py` n'est **pas** modifie : la correction de
l'enveloppe se fait a la construction des snapshots, ce qui evite une edition
sur un symbole a impact HIGH.

## Impact GitNexus (avant edition)

| Symbole | Direction | Impact | Risque |
|---|---|---|---|
| `plan_limit_entry` | upstream | 3 (`place_limit_order` -> `tools/live_demo.py`) | LOW |
| `MatchingSimulator._passive` | upstream | 5 | LOW |
| `V14LivePolicy` | upstream | 6 (imports) | LOW |
| `executer_sur_snapshots` | upstream | 7 | **HIGH** — non modifie |

`plan_limit_entry` touche le chemin live : sa signature, son type de retour et
ses erreurs sont inchanges, et les 21 tests de `tests/test_limit_orders.py`
passent sans modification.

## Commandes et resultats

```
powershell -File tools/gitnexus_team.ps1 sync        9 528 nodes | 19 446 edges (81 s)
tools/lint_gate.sh (porte unique du depot)           All checks passed
ruff check sur les fichiers du lot                   All checks passed
pytest cible (prix, politiques, audit)                118 passed
python -m pytest -q                                  2278 passed, 2 skipped (237 s)
tools/politiques_execution_reel.py --limite 200      147 symboles, 29 400 decisions, 382 200 lignes
GitNexus detect_changes                              22 symboles, 10 flux, risque high revu
```

## Resultats mesures (segment de jugement, fenetre 12)

```
post_only      +0.409166  service synthetique 72.04%  contact 99.07%
limit_passive  +0.411136  service synthetique 94.51%  contact 99.07%
market         -0.742100  service synthetique 100%
v14_live versus market, cohorte commune n=4 331 : uplift +0.119968 R
hors classement : adaptive, iceberg, twap, cancel_replace, pegged, pov, vwap, v14_live
```

## Risques residuels, declares

1. **Le service est synthétique.** Contact inclusif, franchissement strict et
   service simulé sont maintenant séparés, mais aucun n'est un fill observé.
   Aucune promotion possible sans un modèle de file sur ticks L1.
2. **58 % des decisions sont indeterminees pour `v14_live`** (TTL 120 s sur une
   grille de 300 s), et ce sont les decisions a spread large. Sur la moitie du
   flux, ce banc ne dit rien de la politique qui tourne.
3. **L'esperance de base est negative** (-0.74 R par decision au marche). La
   priorite est la porte d'entree, pas l'execution.
4. **La profondeur du carnet reste reconstruite.**

## Etat

Corrections v3 validées par Codex : suite complète et Ruff verts, rapport
intégral recalculé et sorties scellées. En attente du dernier accusé Claude et
Hermes sur le hub avant commit.
