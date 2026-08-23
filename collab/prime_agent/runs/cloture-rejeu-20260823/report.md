# Cloture automatique du backfill de rejeu — outillage et veille

Date : 23/08/2026, apres-midi. Tache journal : `a07f39f2` (rejeu de l univers).
Demande de Florent : preparer la validation des sept predictions et l analyse
d epoque courante pour qu elles partent seules a la fin du backfill.

## 0. Ce qui a ete livre

| Fichier | Role |
|---|---|
| `tools/valider_predictions_granularite.py` | confronte la prediction publiee avant la porte de granularite aux artefacts reels, symbole par symbole |
| `tools/cloture_backfill_rejeu.py` | veille la fin des huit lots, puis enchaine audit -> validation -> classement et publie la cloture |
| `tests/test_valider_predictions_granularite.py` | 8 tests |
| `tests/test_cloture_backfill_rejeu.py` | 8 tests |

Aucun des deux n appartient a `FICHIERS_MOTEUR` : les ecrire ne perime aucun
artefact et n a pas touche au run en cours. Les deux sont en LECTURE SEULE sur
les artefacts ; ils ne relancent aucun lot et n ont aucune autorite d execution.

## 1. La reference est complete, et je l ai verifiee avant de m en servir

`collab/prime_agent/runs/artefacts-rejeu-20260822/avant_granularite/` contient
149 resumes. La question etait : de quelle generation ?

Mesure : les 108 symboles pas encore recalcules par le run v4 sont
**octet pour octet identiques** a leur copie de reference (sha256, 108/108).
La reference est donc exactement la generation `16e79f53`, complete, produite
par le run v3 termine a 11h18. La comparaison v3 -> v4 isole donc bien le seul
commit `ea5abba`.

## 2. Ce que la validation compare, et ce qu elle refuse de compter

La porte deplace la FENETRE lue : la mesurer comme un ecart ferait echouer la
verification par construction. La validation separe donc deux familles :

- **resultat** (decide le verdict) : `n_enter`, `barres_evaluees`, `erreurs`,
  `coupure`, et pour `global` / `calibration` / `verification` les six mesures
  `n`, `esperance_r`, `ecart_type_r`, `winrate`, `profit_factor`, `somme_r` ;
- **fenetre** (rapportee, jamais decisive) : `debut`, `fin`, `barres_ltf`.

Verdicts : `CONFORME`, `PARTIEL` (des symboles restent d une autre epoque),
`NON_CONFORME` (un symbole hors des sept a change de chiffres). Un symbole
attendu qui ne bouge PAS ne fait pas echouer mais est signale : la prediction
serait alors trop large. Un symbole consigne dans `_HORS_UNIVERS.json` n est
pas un ecart.

## 3. Etat de la prediction a mi-parcours (41/149 symboles)

```
validation granularite : PARTIEL (moteur 051f50adf179177e)
  change 2 | en_attente 109 | identique 38

COCOA.fs  [attendu]  n_enter 4326 -> 4110   esperance globale +0.017741 -> +0.026284
COFFEE.fs [attendu]  n_enter 4574 -> 4404   esperance globale +0.108749 -> +0.105795
```

**38 symboles retombent au chiffre pres**, et les deux seuls qui bougent sont
deux des sept annonces. La prediction tient jusqu ici ; le verdict definitif
sera rendu sur les 147.

## 4. La veille tourne

```
pid 28816   tools/cloture_backfill_rejeu.py --intervalle 300 --silence-h 3 --max-h 20
journal     collab/prime_agent/runs/cloture-rejeu-20260823/veille.log
[14:01:47]  termines 41/149 | restants 108 | hors univers 0 | arret: non
```

Quatre conditions d arret, dans cet ordre de priorite :

1. `sentinelle` — `_RUN_FAILED.json` publie : la cloture rapporte l echec ;
2. `termine` — chaque symbole porte un artefact a l epoque courante, ou est
   consigne hors univers ;
3. `silence` — plus rien d ecrit depuis 3 h : les lots sont morts ;
4. `delai` — 20 h ecoulees.

Dans tous les cas elle publie ce qu elle sait plutot que d attendre sans fin.

**Le compteur d avancement ne coute rien.** Premiere version : audit semantique
complet a chaque tour, 100,7 s de CPU toutes les 5 minutes en concurrence avec
huit lots. Corrige : la veille ne lit que les sceaux, **0,14 s** par tour, et
l audit complet n est fait qu une fois, a la cloture.

## 5. Ce que la cloture publiera, dans cet ordre

1. `tools/audit_rejeu_artefacts.py` — les 147 artefacts sont-ils tous de
   l epoque `051f50ad`, sans invalide ni resume sans manifeste ;
2. `tools/valider_predictions_granularite.py` — verdict sur la prediction ;
3. `tools/analyse_rejeu_univers.py` — classement, correlations et grappes,
   **seulement a l epoque courante** et sous plancher de 30 symboles.

Sorties : `cloture.md` et `cloture.json` dans ce dossier.

Ordre voulu : un classement publie avant la validation de la prediction serait
un classement dont personne ne sait s il decrit les actifs ou le commit.

## 6. Preuves d execution

```
pytest tests/test_valider_predictions_granularite.py
       tests/test_cloture_backfill_rejeu.py                    16 passed
ruff check (4 fichiers)                                        All checks passed
essai a blanc de la chaine complete                            essai_a_blanc/cloture.md
  audit 0 | validation 0 | classement 2 (REFUS sous plancher, comportement voulu)
reference verifiee identique aux artefacts non recalcules      108/108 sha256
```

L essai a blanc a ete lance avec `--maintenant --min-symboles 999` : il prouve
que les trois etapes s enchainent, et que le classement REFUSE de publier sur
un echantillon partiel au lieu de produire un tableau trompeur.

## 7. Ce que je n ai pas fait

Aucun artefact reecrit, aucun lot relance ou arrete, aucun fichier de
`FICHIERS_MOTEUR` touche, aucun seuil ni parametre de risque modifie, aucun
service de trading demarre ou arrete.

**Reserve assumee** : l index GitNexus n a pas ete reconstruit. `analyze` sature
les huit coeurs deja pris par le backfill et allongerait un run de treize
heures. Les deux fichiers livres sont NEUFS et n editent aucun symbole
existant, donc l analyse d impact amont etait sans objet. Reindexation a faire
apres la cloture.

## 8. Point ouvert, non traite ici

Le heartbeat de la boucle demo du 23/08 13h17Z montre **93 ENTER, 0 envoye,
93 `RISKGATE_DENY`** sur 137 tours. A instruire apres la cloture : plafond
d exposition atteint, ou refus systematique ? Ce n est pas un correctif a faire
pendant un backfill.
