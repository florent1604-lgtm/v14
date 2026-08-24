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


---

# Cloture executee — 24/08, 01h25 UTC

`raison_arret = termine`. Les huit lots sont sortis seuls, la veille a tenu
139 tours sans incident, et les trois etapes ont rendu 0.

## 1. Verdict sur la prediction : CONFORME

```
142 identiques | 5 changes, tous prevus | 1 hors univers | 0 INATTENDU
COCOA.fs  COFFEE.fs  IT40  SPA35  USDCLP        USDCOP hors univers
```

**Zero symbole hors des sept n a bouge d un chiffre.** La porte de granularite
fait exactement ce qui etait annonce, ni plus ni moins.

Une nuance mesuree, en faveur du moteur : **GER40 n a pas bouge**. La
prediction annoncait un changement des que le prefixe HTF tombait sous
400 barres, et GER40 en avait 375. Le seuil de 400 etait conservateur :
`titanium/backtest.py` saute une barre tant que `j < AMORCAGE` avec
`AMORCAGE = 250`, et decoupe ensuite `htf.iloc[max(0, j - fenetre + 1):j + 1]`
-- une fenetre de 400 au PLUS, pas au moins. Le vrai plancher est 250, GER40
est au-dessus, ses chiffres devaient donc rester identiques. La prediction
etait trop large d un symbole, dans le sens prudent.

## 2. Deux verrous a lever, tous deux benins

- `_analyse_rejeu.json`, fichier de service fige avec la reference, etait
  compte comme un symbole en attente et interdisait a jamais un verdict
  complet. Les noms prefixes par `_` sont desormais ignores (1 test).
- L artefact **perime de USDCOP** reste sur le disque : l analyse l ecarte
  proprement (`1 artefact d une AUTRE generation ecarte`) et l audit le
  signale. A purger, sans urgence.

## 3. Ce que l univers dit, apres la porte

```
147 symboles a l epoque 051f50ad | mediane -0,1040R | positifs 44/147
positifs en calibration ET en global : 38   (le critere publie par l analyse)
positifs en calibration ET en VERIFICATION, n_ver >= 60 : 29
```

Tete du classement hors echantillon (segment de verification, jamais utilise
pour selectionner) :

```
COFFEE.fs +0,1917 (1639)   USTECH +0,1684 (1800)   BTCUSD +0,1568 (2046)
XAUUSD    +0,1785 (1919)   UKOIL  +0,1646 (1922)   FRA40  +0,1567 (1892)
USOIL     +0,1737 (1833)   ETHUSD +0,1573 (1906)   WTI.fs +0,1558 (1824)
```

IT40, le symbole le plus touche par la porte, garde +0,1500 en verification
mais perd 42 % de ses clotures (2216 -> 1282) : son avantage etait en partie
lu sur des barres journalieres etiquetees M15. C est precisement ce que la
porte devait reveler.

38 actifs se reduisent a **21 paris independants** (correlations H1 sur les
rendements) : la plus grosse grappe est celle des indices US (7 actifs,
garder USTECH), puis le petrole (4, garder UKOIL) et le crypto majeur
(4, garder BTCUSD).

## 4. Reste ouvert

1. Reindexer GitNexus (differe pendant le run pour ne pas voler de CPU).
2. Purger le resume perime de USDCOP.
3. Arbitrage de la porte de cout (`docs/RAPPORT_COUT_DECISIONNEL_20260822.md`).
4. Instruire `RISKGATE_DENY` / `MAX_PAR_SYMBOLE` sur la boucle demo.
