# P0 441bfea8 — l'epoque d'analyse des bancs hors ligne est reparee

Tache : `441bfea8-3549-4756-a9a9-4b3be5dbdc09`. Contrat : Codex offset 627 (7 points),
Hermes offset 633 section A (6 garde-fous) et 638 point 5, Claude offset 640 (4 cas de
refus + durcissement du point 5 de Codex).

PAPER/DEMO only. Lecture seule sur les artefacts : aucun rejeu, aucun fichier de
`FICHIERS_MOTEUR` touche, aucun ordre, aucun seuil, aucun redemarrage, aucune promotion.

## 1. La panne

`titanium/edge.py` est un fichier moteur (`tools/rejeu_univers.py:67-79`). Le commit
`4c2ab54` y a ajoute un champ de journal live et a fait basculer l'empreinte du moteur
SUR DISQUE de `051f50adf179177e` a `0901ca6851939216`. Les deux bancs hors ligne
figeaient `empreinte_attendue = epoque_rejeu.empreinte_courante()`, c'est-a-dire l'arbre
de travail : les 147 artefacts scelles, intacts, ont ete refuses d'un coup
(`EPOQUE_REJEU_INCOMPATIBLE`, `symbols_measured: 0`, code de sortie **0**).

Une mesure ne se rattache pas au code present sur disque. Elle se rattache a la
GENERATION qui a produit les artefacts lus.

## 2. Ce qui est livre

**`tools/epoque_rejeu.py` — source unique de l'epoque d'analyse declaree.**

| Fonction | Role |
|---|---|
| `manifestes_corpus` | manifestes du corpus DEMANDE + generation, ou echec ferme |
| `epoque_corpus` | generation commune; `pin` = assertion, jamais autorisation |
| `etat_epoque` | bloc publiable : corpus, arbre de travail, ecart, listes demandee/retenue |
| `epoque_reference` | generation DOMINANTE d'un dossier vivant (backfill en cours) |
| `publier_blocage` / `lever_blocage` | `ANALYSIS_BLOCKED` ecrit a cote du rapport |

Cas de refus fermes : `CORPUS_VIDE`, `MANIFESTE_ILLISIBLE`, `EPOQUE_ABSENTE`,
`ARTEFACT_ABSENT_OU_INCOHERENT`, `GENERATIONS_MIXTES` (qui NOMME les symboles fautifs),
`PIN_DIFFERENT_DU_CORPUS`.

**Les deux bancs** (`tools/politiques_execution_reel.py`, `tools/evalue_l1_passif.py`)
prennent l'epoque du corpus, acceptent `--empreinte` (assertion), publient
`corpus_epoch` / `workspace_engine_epoch` / `workspace_matches_corpus` / liste des
manifestes retenus + `manifests_sha256`, et distinguent trois etats : mesure faite,
corpus valide sans decision, analyse bloquee. Le rapport politiques porte desormais les
empreintes du code analyseur (`code.analyse_sha256`, `code.limit_pricing_sha256`), comme
le banc L1 le faisait deja.

**`tools/analyse_rejeu_univers.py`** : `--epoque corpus` devient le defaut (generation
dominante), `courante` et `toutes` restent disponibles, une empreinte de 64 hexa epingle
une generation precise ; l'ecart avec l'arbre de travail est imprime.

## 3. Reponse point par point aux trois contrats

| Exigence | Ou |
|---|---|
| Codex 1 — corpus demande, echec ferme | `manifestes_corpus`, `epoque_corpus` |
| Codex 2 — `--empreinte` = assertion | `epoque_corpus(pin=...)`, refuse meme si le pin est juste et le corpus mixte |
| Codex 3 — epoque + ecart + manifestes au rapport | bloc `epoque` des deux rapports |
| Codex 4 — empreintes du code analyseur | `code.*` (politiques), deja present (L1) |
| Codex 5 — `symbols_measured: 0` != resultat | `statut_analyse`, `ANALYSIS_BLOCKED`, sortie 2 |
| Codex 6 — sept tests minimaux | `tests/test_epoque_corpus.py`, `tests/test_bancs_epoque_corpus.py` |
| Codex 7 — ne pas sortir `ClosedTrade`/`TradeJournal` | non touche |
| Hermes A.6 — pas de TOCTOU | `manifest_bytes_sha256` = octets REELLEMENT lus, scelles au rapport |
| Hermes A.4 — lecture du mismatch | `reading` : « analyse courante d'un corpus scelle » |
| Claude 1 — corpus vide | `CORPUS_VIDE` |
| Claude 2 — corpus partiel silencieux | `requested_symbols` / `retained_symbols` scelles ; symbole demande absent = refus |
| Claude 3 — manifeste orphelin | `ARTEFACT_ABSENT_OU_INCOHERENT` (nom + taille declaree) |
| Claude 4 — nommer le symbole fautif | `GENERATIONS_MIXTES.par_epoque` |
| Claude 5 — `ANALYSIS_BLOCKED` DANS le rapport publie | `<sortie>.blocked.json`, sans jamais ecraser le dernier rapport valide |

## 4. Preuves

Banc L1, corpus reel, sortie temporaire (avant : `refused_symbols {XAUUSD:
EPOQUE_REJEU_INCOMPATIBLE}`, `symbols_measured 0`) :

```
.venv\Scripts\python.exe tools\evalue_l1_passif.py --cutoff 2026-08-24T08:17:55.705Z \
  --symboles XAUUSD --sortie results\_tmp_l1_prime.json --details results\_tmp_l1_prime.ndjson
-> status MEASURED_PRICE_PATH_ONLY | symbols_measured 1 | 12 decisions | 72 lignes
   corpus_epoch 051f50adf179177e | workspace 0901ca6851939216 | matches false
```

Classement des politiques :

```
.venv\Scripts\python.exe tools\politiques_execution_reel.py --symboles XAUUSD ETHUSD \
  --limite 25 --sortie results\_tmp_politiques_prime.json
-> epoque rejeu 051f50adf179177e — 2 artefacts valides, 0 refuses, 50 decisions, 650 lignes
   arbre de travail 0901ca6851939216 — DIFFERENT de la generation mesuree (ecart permis, publie)
```

Pin faux (assertion) et blocage publie :

```
... tools\evalue_l1_passif.py ... --empreinte 0901ca68...b260b
-> ANALYSIS_BLOCKED / PIN_DIFFERENT_DU_CORPUS, code de sortie 2,
   results\_tmp_l1_prime.blocked.json ecrit, rapport valide precedent CONSERVE
```

Corpus reel : `epoque_corpus(results/rejeu_univers_brut)` = `051f50adf179177e`,
147 demandes / 147 retenus, unanimite.

Tests : `85 passed` sur les quatre modules d'epoque ; suite complete
**2333 passed, 2 skipped** (202,8 s). Ruff : `All checks passed!` sur les 8 fichiers.
Les sorties temporaires ont ete effacees.

## 5. GitNexus

`gitnexus_team.ps1 sync` : index incremental `changed=7, added=4`, puis **echec connu**
de l'ecriture (`FTS index 'file_fts' is inconsistent`) — corruption deja constatee, non
reparee ici car `clean --force` detruirait tout `.gitnexus`. `detect-changes` a donc
tourne sur l'index precedent : **7 fichiers, 22 symboles, 0 flux d'execution affecte,
risque low**.

AVERTISSEMENT RISQUE : `impact empreinte_courante --direction upstream` rend
**CRITICAL, 112 dependants, 87 directs**. Cette fonction n'a ete ni modifiee ni
deplacee : seuls deux de ses appelants (les deux bancs) cessent de l'utiliser comme
critere de validation. Aucun flux d'execution n'est affecte.

## 6. Risques residuels

- L'ecart corpus/arbre de travail est desormais PERMIS. Il est publie partout, mais un
  lecteur presse peut lire un rapport ancien comme une reproduction du moteur courant :
  le champ `reading` est la pour l'en empecher, il n'est pas une garantie.
- `epoque_reference` (dossier vivant) choisit la generation DOMINANTE : pendant un
  backfill, la majorite peut basculer d'une generation a l'autre en cours de route. Le
  decompte par epoque est publie pour rendre le basculement visible.
- L'index GitNexus reste corrompu (FTS) ; la prochaine analyse complete echouera tant
  que l'index n'est pas reconstruit hors mission.
- Le fond du probleme demeure : `titanium/edge.py` porte de la telemetrie live tout en
  etant un fichier moteur. Sortir `ClosedTrade`/`TradeJournal` est reporte au prochain
  rejeu complet (Codex point 7).

## 7. Suite

Trois revues croisees attendues : Claude (contrat fonctionnel), Codex (impact, tests),
Hermes (integrite scientifique). Aucune mesure hors ligne enchainee avant leurs ACCEPT.
