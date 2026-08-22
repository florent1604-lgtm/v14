# Rejeu univers : n_enter = 0 — cause racine, correctif, relance

Tache : `a07f39f2-487e-43c1-a6b9-46b8b34e3fed`
Commit : `6845a43`
Auteur : Prime Agent — 22/08/2026

## 1. Point de depart

Note de reprise de Claude (`collab/PRIME_REPRISE_20260822.md`) : la v2 du rejeu
republie six symboles a `n=0` par dessus des resultats valides, sans erreur.
Etat mesure a la reprise : 148 resumes, 142 exploitables, 6 a zero
(AAVE-USD, ADAUSD, AUDCAD, AUDCHF, AUDJPY, AUDNZD), 6 dossiers bruts avec
`trades.ndjson` vide, aucun processus de rejeu en cours. La destruction s'etait
arretee d'elle-meme a 6.

## 2. Cause racine

`titanium/backtest.rejouer` appelait `build_feats` **sans horodatage**. En
l'absence de `now`, la porte de cout retombe sur l'horloge du systeme. Le
22/08/2026 est un **samedi** : `_weekend_block` renvoyait vrai pour toutes les
barres, `confluence_gate` rendait `BLOCK_COST_WEEKEND` sur les 99 751 barres, et
`marche_continu` n'etait jamais transmis — la crypto etait bloquee comme le FX.

Un rejeu dependait donc de l'heure a laquelle il etait lance, et non de
l'horloge des barres. Les passes precedentes, lancees en semaine, n'avaient rien
montre.

### Preuve

`collab/prime_agent/runs/rejeu-nenter-zero-20260822/sonde_nenter.py`, AAVE-USD,
6 000 barres M15, meme archive, meme spread :

```
[corrige ] barres=2763 n_enter=404 trades=404 erreurs=0
[horl.mur] barres=5749 n_enter=0   trades=0   erreurs=0
```

La sonde ne publie aucun artefact : elle rejoue en memoire et remet l'ancien
comportement par patch local.

## 3. Correctif livre (`6845a43`)

- `titanium/backtest.py` : `_duree_barre` derive la duree modale de l'index,
  `decision_at = ouverture + duree` est passe a `build_feats` et a
  `confluence_gate.evaluate`; `marche_continu` vient de `asset_class_of`.
- `tools/rejeu_univers.py` : `valider_resultat_avant_publication` verifie la
  coherence resume/trades, la somme des splits et `n_enter`, et **refuse** un
  zero suspect (precedent non nul ou >= 1 000 barres evaluees). Le lot passe en
  fail-closed : il s'arrete et publie `_RUN_FAILED.json`, que les autres lots
  lisent au symbole suivant. `--autoriser-vide` reste disponible pour un
  diagnostic explicite.
- `tools/audit_rejeu_artefacts.py` : audit semantique et pas seulement des
  sceaux; devient la source commune du moniteur 10 min et des vues.
- `tools/rejeu_progression.py` : un resume a n=0 n'est plus compte comme fait.

Le defaut initial n'etait pas seulement un bug : un artefact vide avait la forme
d'un resultat valide. C'est cette forme qui est desormais refusee.

## 4. Verification

- Suite complete : **2072 passes, 2 skipped** (139 s).
- Tests cibles : 27 passes sur `test_backtest_causality`,
  `test_rejeu_univers_raw`, `test_rejeu_progression`, `test_audit_rejeu_artefacts`.
- Chaine complete resume+brut en bac a sable
  (`sonde_chaine.py`, AAVE-USD, 6 000 barres) :
  `n_enter=404`, global 404 = calibration 268 + verification 136,
  `trades.ndjson` 404 lignes / 1 196 029 octets, manifeste scelle valide,
  **aucun champ A/B manquant** — `decision_at`, `asset_class`, `quantity`,
  `quantity_unit`, `side`, `trade_id` tous presents.
- Audit avant relance : `accepted=0, legacy=142, invalid=6, missing=1` sur 149.

## 5. Relance

`tools/lancer_backfill_rejeu.py` (nouveau) lance des lots detaches, refuse de
demarrer si une sentinelle d'echec n'a pas ete examinee, et se reprend en
relancant simplement la meme commande.

```
.venv\Scripts\python.exe tools\lancer_backfill_rejeu.py --lots 8 --prefixe backfill_v3
```

149 symboles, 8 lots, profondeur complete. L'empreinte du moteur a change avec
le correctif : les 142 artefacts legacy sont donc rejoues eux aussi, ce qui est
precisement ce que l'A/B de Codex attend. Duree estimee 14 a 18 h.

## 6. Ce qui reste ouvert

- L'A/B SHADOW de Codex reste BLOCKED tant que le backfill n'a pas produit les
  bruts. Les donnees de file et d'agresseur restent absentes : le validateur
  restera NO-GO sur ce volet meme avec les bruts complets.
- GitNexus : `analyze` echoue sur une corruption d'index FTS
  (`FTS index 'file_fts' is inconsistent: term 'combien' is missing during
  delete`). Index a reconstruire; sans effet sur le correctif.
- PAPER/DEMO only : rien de ce lot ne touche l'execution, aucun parametre de
  trading n'a ete modifie, aucun service n'a ete redemarre.

## 7. Premiers resultats du backfill v3 (50 min apres lancement)

Les huit lots ont chacun clos leur premier symbole. Les six artefacts detruits
sont restaures :

```
AAVE-USD  n=5805  esp -0.0258 R  win 43.5%   verif n=2033  [2938 s]
ADAUSD    n=6085  esp -0.2233 R  win 38.7%   verif n=2076  [3009 s]
AUDCAD    n=5137  esp -0.1729 R  win 34.7%   verif n=1738  [2978 s]
AUDCHF    n=5169  esp -0.1154 R  win 36.5%   verif n=1656  [2953 s]
AUDJPY    n=5171  esp -0.0193 R  win 50.9%   verif n=1643  [2966 s]
AUDNZD    n=4894  esp -0.1591 R  win 34.8%   verif n=1529  [2864 s]
AUDSGD    n=5189  esp -0.1695 R  win 34.5%   verif n=1775  [2999 s]
AUDUSD    n=5380  esp -0.0717 R  win 40.6%   verif n=1765  [2854 s]
```

AAVE-USD retrouve **n=5805** contre **n=5806** avant la regression : le
correctif restitue la mesure d'origine, il ne la deplace pas.

Audit : `accepted=8, legacy=140, invalid=0, missing=1` sur 149. Le seul manquant
est `USDUSC`, jamais rejoue jusqu'ici; il est dans l'inventaire du lot courant.
Environ 3 000 s par symbole, 19 symboles par lot : fin attendue vers 15 a 16 h.

Ces esperances sont **negatives sur presque tous ces symboles** — ce sont les
symboles chers deja identifies. Ce lot repare une mesure, il ne cree pas d'edge.

## 8. GitNexus

L'index etait corrompu (`FTS index 'file_fts' is inconsistent`). `gitnexus clean
--force` supprime le dossier `.gitnexus` entier, y compris `run.cjs` et le
runtime OpenSSL, et ce dossier n'est pas versionne. Reconstruction complete par
le CLI global : 8 593 noeuds, 17 993 aretes, 398 clusters, 300 flux en 50 s,
puis `tools/gitnexus_team.ps1 sync` repasse au vert.

`detect-changes --repo titanium-v14 --base-ref cd2b47a` : 19 fichiers,
99 symboles, 52 processus affectes, risque **critical** — attendu, `rejouer` est
un point de passage central et les flux touches incluent `Analyse ->
_weekend_block`, exactement la porte en cause. Contrepartie : suite complete
verte, plus la comparaison numerique avant/apres sur AAVE-USD ci-dessus.
