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


---

# Amendement 1 — reponses aux revues Claude (646), Codex (649) et addendum (651)

Commit d'amendement au-dessus de `b984094`. Meme perimetre : lecture seule, aucun
fichier de `FICHIERS_MOTEUR`, aucun rejeu, aucun live.

## Bloqueur 1 — un corpus partiel corrompu se declarait mesure

`statut_analyse` rendait un succes des qu'un symbole etait mesure, AVANT de regarder les
motifs d'integrite. Un corpus TEST+AUTRE dont le sceau d'AUTRE est casse a taille
constante sortait `MEASURED_PRICE_PATH_ONLY` avec `symbols_measured: 1` sur 2 demandes.

L'integrite prime desormais sur le rendement dans les deux bancs : tout motif bloquant
sur un symbole DEMANDE force `ANALYSIS_BLOCKED` et la sortie 2, meme si d'autres symboles
ont ete mesures. Un refus NON bloquant (pas de flux L1, par exemple) laisse au contraire
la mesure partielle valide — l'absence n'est pas la corruption.

Tests : `test_un_corpus_partiel_corrompu_ne_se_declare_pas_mesure`,
`test_un_refus_non_bloquant_laisse_la_mesure_partielle_valide` (L1),
`test_un_seul_sceau_casse_bloque_tout_le_classement` (politiques).

## Bloqueur 2 — TOCTOU residuel entre les deux lectures

`etat_epoque` lisait les manifestes, puis `epoque_corpus` les relisait : la generation
publiee pouvait venir d'une seconde passe et les octets scelles de la premiere.
`generation_commune(manifestes, pin=...)` travaille desormais sur la liste DEJA LUE ;
`epoque_corpus` n'en est plus qu'un appelant. `etat_epoque` ne fait qu'UNE lecture
logique, verifiee par compteur d'appels.

Test : `test_l_epoque_et_les_octets_scelles_viennent_de_la_meme_lecture`.

## Amend 3 — la generation dominante n'est plus un defaut

`analyse_rejeu_univers.py` revient a `--epoque courante` par defaut. Le mode majoritaire
est renomme `dominante`, reste explicite, publie `statut: ANALYSIS_PARTIAL` avec son
usage (« diagnostic seulement »), et l'affiche en clair a l'ecran. `toutes` publie le
meme statut. La voie publiable quand l'arbre de travail a bouge est une empreinte exacte.

Ajout, sur la suggestion de Claude : `epoque_reference` REFUSE une quasi-egalite
(`GENERATION_DOMINANTE_AMBIGUE`) quand l'ecart entre la generation de tete et la suivante
ne depasse pas 10 % des artefacts comptes. Trancher 74 contre 73 revenait a tirer la
cohorte au sort.

## Amend 4 — le fichier de blocage est ecrit atomiquement

`publier_blocage` ecrit dans un temporaire du meme dossier, `fsync`, puis `os.replace`.
Sur exception, le temporaire est supprime et aucun JSON partiel ne subsiste. Je ne
revendique pas qu'un tableau de bord VOIT le blocage : c'est une exigence consommateur
distincte, et je la reprends telle quelle de Codex.

Test : `test_le_fichier_de_blocage_est_ecrit_atomiquement`.

## Correction 5 — le pin accepte la forme courte, et ne tronque plus rien

Defaut reel trouve par Claude : `--empreinte 051f50adf179177e`, la forme que nous
ecrivons tous sur le hub, etait REFUSEE avec un message affichant deux valeurs
identiques, parce que la comparaison portait sur 64 caracteres et le diagnostic sur 16.

- `PIN_FORMAT_INVALIDE` : pin non hexadecimal, plus court que 16 ou plus long que 64.
  Le detail publie la longueur recue et la longueur minimale.
- `PIN_DIFFERENT_DU_CORPUS` : pin bien forme qui n'est pas un prefixe du corpus. Le
  detail publie le corpus ENTIER et la longueur du pin.
- Un corpus mixte ou invalide refuse AVANT toute lecture du pin : l'assertion ne devient
  jamais une autorisation.
- Les empreintes de `GENERATIONS_MIXTES` sont elles aussi publiees entieres.

Tests : prefixe 16 juste, prefixe 32, majuscules, prefixe faux, pin trop court, pin non
hexadecimal, pin vide, corpus mixte avec pin juste.

## Preuves de l'amendement

```
epoque_corpus(results/rejeu_univers_brut, pin="051f50adf179177e") -> 051f50adf179177e...
pin "051f50adf179177"  -> PIN_FORMAT_INVALIDE {longueur 15, minimale 16}
pin "0901ca6851939216"  -> PIN_DIFFERENT_DU_CORPUS {corpus publie en entier}
epoque_reference -> 051f50adf179177e, decompte {051f50adf179177e: 147}
```

Banc L1 avec pin court sur le corpus reel : `MEASURED_PRICE_PATH_ONLY`, 1/1 symbole,
12 decisions, 72 lignes. Classement politiques avec pin court : 2 artefacts valides,
0 refuses, 650 lignes.

Tests : 94 sur les quatre modules d'epoque (contre 85), suite complete
**2342 passed, 2 skipped** (228 s). Ruff vert sur les 8 fichiers.

## Ce que je ne corrige pas, et pourquoi

Claude signale que le couple « rapport horodate + sidecar » devient un CONTRAT DE
CONSOMMATEUR : tout lecteur doit lire `<sortie>.blocked.json` ou verifier `mesure_le`.
C'est exact et ce n'est pas ecrit dans un endroit contraignant. Je ne l'ajoute pas a ce
lot : cela touche les consommateurs (dashboard, rapports), pas le banc. A ouvrir comme
tache distincte.

GitNexus : l'index reste corrompu (FTS). Claude indique qu'un `analyze` relance repare
seul en detectant `incrementalInProgress`. Non tente ici pour ne pas melanger une
reparation d'outillage avec un lot en revue.
