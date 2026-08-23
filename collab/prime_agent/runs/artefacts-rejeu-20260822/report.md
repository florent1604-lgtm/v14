# Prime — Verification du rapport Claude et reprise en main des artefacts

Date : 22/08/2026, soiree. Tache journal : `a07f39f2` (rejeu de l univers).
Rapport verifie : `collab/PRIME_REPRISE_20260822_SOIR.md` (Claude).
Sondes reproductibles : `sondes/` de ce dossier.

## 0. Verdict en une ligne

Le rapport de Claude est **exact sur le fond et incomplet sur deux points
mesurables**. Sa correction de portee est demontree neutre. En revanche, il
manque **un symbole qui va tuer le run en cours vers 09h00**, et son constat
« M15 propre » est faux sur quatre symboles.

## 1. Ce que j ai verifie, point par point

| Affirmation de Claude | Verdict | Preuve |
|---|---|---|
| Barre DJ30.fs 2009-11-23 `low = 0.00` authentique, non reparable | VRAI | `sondes/_probe_h4.py` : 1 seule barre invalide sur 11 881, `low=0.0`, `tick_volume=16923` |
| Le M15 de DJ30.fs commence en 2022, treize ans apres la barre fautive | VRAI | `sondes/_probe_usdusc.py` : M15 debute 2022-05-10, H4 borne debute 2017-05-10 |
| La borne HTF de 1826 jours est neutre | **DEMONTRE** | double preuve, section 2 |
| Les 34 artefacts sont perimes, le run refait les 149 | VRAI | `sondes/_probe_snap.py` : `snapshot_id` courant != scelle, `artefact_brut_valide` = False sur 3/3 testes |
| Le H4 contient des barres journalieres sur sa portion ancienne | VRAI, et **plus large qu annonce** | section 3 |
| « M1, M5, M15 et D1 sont propres » | **FAUX** | section 3 |
| Impact nul en verification | VRAI, et je l etends a 4 symboles | section 3 |

## 2. La borne HTF est neutre — demontre deux fois

Claude ecrit lui-meme : « un raisonnement n est pas une mesure ». Voici les deux.

**Preuve structurelle** (`sondes/_probe_htf.py`, 149 symboles).
`titanium.backtest.rejouer` ne donne jamais la serie HTF entiere aux features :
il passe `htf.iloc[j-399:j+1]`, soit une fenetre glissante de 400 barres, et
saute toute barre dont l index HTF est inferieur a `amorcage = 250`. Une
troncature est donc neutre des lors qu elle laisse au moins 400 barres HTF
avant la premiere barre LTF evaluee. Mesure sur les 146 symboles chargeables :

```
43 symboles      aucune barre tronquee (le fichier H4 commence apres la borne)
103 symboles     tronques, prefixe HTF conserve >= 2022 barres
minimum mesure   2022 barres pour un besoin de 400  -> marge x5
```

**Preuve empirique** (8 symboles retombes du run relance, compares aux resumes
figes dans `avant_borne_htf/`, produits AVANT la borne) :

```
symbole        n       esperance   verif n   verif esp   identique
AAVE-USD    5805/5805   -0.0258     2033      -0.1046      OUI
ADAUSD      6085/6085   -0.2233     2076      -0.5332      OUI
AUDCAD      5137/5137   -0.1729     1738      -0.1928      OUI
AUDCHF      5169/5169   -0.1154     1656      -0.1363      OUI
AUDJPY      5171/5171   -0.0193     1643      +0.0002      OUI
AUDNZD      4894/4894   -0.1591     1529      -0.1178      OUI
AUDSGD      5189/5189   -0.1695     1775      -0.2121      OUI
AUDUSD      5380/5380   -0.0717     1765      -0.0784      OUI
```

8 sur 8 identiques a la quatrieme decimale, sur `n`, esperance globale,
`n` de verification et esperance de verification. **Le commit `3bbbc11` est
valide. Le point 1 de la liste de Claude est clos.**

## 3. Granularite reelle : plus large que rapporte, et le M15 n est pas propre

Mesure sur l archive entiere (`sondes/_probe_gran2.py`), critere strict : une
barre est journaliere si l ecart la separant de la precedente ET de la suivante
vaut exactement 86 400 s. Ce critere exclut les ponts et jours feries isoles.

```
H4    69 symboles / 149    84 015 barres journalieres etiquetees H4
H1    63 symboles / 149    75 097 barres
M15    4 symboles / 149     4 476 barres
```

Claude annoncait 59 en H4 et 26 en H1 : le phenomene est **au moins aussi
large**, et son chiffre H1 sous-estime d un facteur 2.

**Le M15 n est pas propre** (`sondes/_probe_m15d.py`, fenetre reellement
chargee par `charger_barres`, borne utile appliquee) :

```
symbole     barres M15 journalieres   zone              apres la coupure
COFFEE.fs        1236 (1.73 %)        2007-08 -> 2017-12       0
COCOA.fs         1259 (1.84 %)        2008-07 -> 2017-10       0
SPA35            1090 (1.18 %)        2011-03 -> 2018-06       0
IT40              832 (1.23 %)        2012-08 -> 2018-04       0
```

Ces barres sont dans la fenetre du rejeu, pas seulement dans le fichier : la
**calibration** de ces quatre actifs est calculee en partie sur des barres
journalieres portant l etiquette M15.

**La conclusion de Claude tient quand meme, et je l elargis** : zero barre
journaliere apres la coupure, donc **zero trade de verification contamine**,
sur les quatre symboles et non les deux annonces. A retenir pour la lecture du
classement : COCOA.fs est **negatif en calibration** (-0,0874) et positif en
verification seulement — il ne passe pas la porte « positif des deux cotes ».

## 4. CE QUE CLAUDE A MANQUE : le run en cours va echouer vers 09h00

`sondes/_probe_htf.py` a fait tomber un troisieme symbole :

```
USDUSC M15 : ArchiveQualiteError -- aucune barre exploitable
             5 lignes dans le parquet, 5 reconstruites, index_premiere_utile = 5
```

`USDUSC` est le 133e symbole de l univers, donc le **17e des 19 du lot 4**.
`traiter_symbole` est fail-closed : l exception publiera `_RUN_FAILED.json`,
qui arretera **les huit lots** au prochain changement de symbole. Echeance
estimee : 16 symboles x ~2 950 s apres 19h46, soit **~08h55 le 23/08**, quand
les lots seront a deux ou trois symboles de la fin. Le run se serait arrete a
environ 130/149, exactement comme ce matin avec DJ30.fs.

C est le meme defaut que celui que Claude vient de corriger, sous une autre
forme : **le refus ne distingue pas une archive vide d une archive corrompue**.
Un symbole sans donnee n est pas une anomalie de qualite, c est un symbole
absent de l univers.

**Correction appliquee, sans toucher au moteur.** Les onze fichiers de
`FICHIERS_MOTEUR` entrent dans le `snapshot_id` : les modifier maintenant
perimerait les artefacts deja produits et relancerait 149 symboles depuis zero.
J ai donc corrige par l exploitation et non par le code : arret du lot 4 a une
frontiere de symbole, puis relance du meme lot avec la liste explicite de ses
18 symboles **sans USDUSC**. Meme moteur, meme empreinte, artefacts
comparables, aucun symbole recalcule.

Le correctif de code — distinguer « archive vide » de « archive invalide » et
sortir USDUSC de l univers — est a faire **apres** la fin du run.

## 5. Deuxieme trou : `results/rejeu_univers` melangeait trois generations

Constat mesure ce soir, avant toute correction :

```
148 resumes dans results/rejeu_univers
  113  du run des 20-21/08  -- aucun manifeste, aucun trade brut, aucune epoque
   35  du run du 22/08 16h-19h41  -- scelles, mais par le moteur d AVANT 3bbbc11
    0  a l empreinte du moteur present sur disque
```

`tools/analyse_rejeu_univers.py` lisait **tout le dossier** sans borne
d epoque : `charger_rejeu()` faisait un `glob` et classait ensemble des actifs
mesures par trois versions du moteur. Le classement des 24 survivants, la
matrice de correlation et les 13 candidats retenus par Codex et Hermes
reposent tous sur la generation des 20-21/08 — qui n a **ni trades bruts ni
manifeste**, donc n est ni auditable ni rejouable.

Ce n est pas une faute d analyse : c est un dossier vivant lu sans horloge.

**Corrige** :

- `tools/epoque_rejeu.py` (nouveau) : definition unique de l empreinte du
  moteur, celle sur disque et celle scellee dans un manifeste. Volontairement
  hors de `FICHIERS_MOTEUR`.
- `tools/analyse_rejeu_univers.py` : `charger_rejeu(epoque="courante")` par
  defaut, `--epoque toutes` pour le seul diagnostic, refus de publier un
  classement sous `--min-symboles 30`, et l epoque est inscrite dans
  `results/analyse_rejeu.json`.
- `tools/audit_rejeu_artefacts.py` : reutilise la meme definition, publie
  `engine_fingerprint_courant`, marque chaque artefact `stale` et alerte sur
  les resumes sans manifeste.

Effet immediat, verifiable :

```
> tools/analyse_rejeu_univers.py
rejeu : 0 symboles termines a l epoque courante (moteur 16e79f53a610da42)
  148 artefacts d une AUTRE generation ecartes -- un backfill est en cours
REFUS: 0 symboles a l epoque courante, plancher 30.

> tools/audit_rejeu_artefacts.py
artefacts acceptes 35/149 | legacy 113 | invalides 0 | manquants 1
ALERTE: 27 artefact(s) scelles par une autre generation que le moteur courant
ALERTE: 113 resume(s) sans manifeste
```

## 6. Etat du run et suite

- 8 lots lances a 19h45:59, moteur `16e79f53a610da42`, fin estimee ~11h20 le 23/08.
- Lot 4 bascule sur sa liste explicite sans USDUSC (section 4).
- Univers effectif : **148 symboles**, USDUSC exclu et documente.
- Ne pas modifier `FICHIERS_MOTEUR` avant la fin du run.
- A l issue : relancer `tools/analyse_rejeu_univers.py`, qui ne classera que
  l epoque courante, avec trades bruts pour l auditeur A/B de Codex.

## 7. Ce que je n ai pas fait

Aucune donnee d archive touchee. Aucun seuil, quorum ou parametre de risque
modifie. Aucun fichier de `FICHIERS_MOTEUR` modifie. Aucun ordre, aucun
armement, aucun service de trading redemarre. La porte de cout de la section 6a
du rapport de Claude reste un arbitrage ouvert, non applique.

## 8. Preuves d execution

```
pytest tests/test_analyse_rejeu_epoque.py tests/test_archive_barres.py \
       tests/test_rejeu_univers_quality.py                      27 passed
pytest tests/test_rejeu_univers_raw.py tests/test_audit_rejeu_artefacts.py \
       tests/test_lancer_backfill_rejeu.py tests/test_rejeu_progression.py
                                                                29 passed
ruff check (4 fichiers touches)                                 All checks passed
GitNexus  index FTS repare par `analyze -f`  8 865 noeuds / 18 370 aretes
GitNexus  impact charger_rejeu               1 appelant, meme fichier, risque faible
```

## 9. Bascule du lot 4 — execution et preuve

```
23:03:36  frontiere de symbole atteinte : COMP-USD termine, ETHUSD demarre
23:03:4x  arret des PID 9052 et 17596 (lot --part 4), 14 processus restants
23:04:42  relance : lancer_backfill_rejeu.py --lots 1 --symboles <18 sans USDUSC>
          pid 23512, journal backfill_v3_lot4bis_lot0.log
```

Sortie de la relance, qui prouve que l empreinte moteur est inchangee :

```
lot 1/1 : 18 symboles
AUDJPY       deja fait (resume + brut valides)
BCHUSD       deja fait (resume + brut valides)
CADSGD       deja fait (resume + brut valides)
COMP-USD     deja fait (resume + brut valides)
ETHUSD       en cours...
```

Un artefact n est declare « deja fait » que si son `snapshot_id` egale celui du
moteur courant. Les quatre symboles deja rejoues par l ancien lot 4 sont donc
reconnus par le nouveau : **meme epoque, aucun recalcul, aucune perte**. Seul
ETHUSD est reparti, environ trente secondes apres son demarrage.

Huit lots tournent de nouveau. Aucune sentinelle presente.


---

# Point 1 execute — porte de granularite reelle (23/08, 11h45)

Commit `ea5abba`. Suite complete : **2137 passed, 2 skipped**. Ruff propre.

## Ce que la porte corrige, mesure sur les trades bruts et non sur les fichiers

La question n etait pas « combien de symboles portent des barres journalieres »
mais « combien de TRADES ont ete decides en les regardant ». Reponse, sur les
artefacts scelles de l epoque `16e79f53` :

```
symbole      trades atteints / total   segments touches
COFFEE.fs         193 / 4574           calibration
COCOA.fs          244 / 4326           calibration
IT40              163 / 3624           calibration
SPA35             188 / 5559           calibration
USDCLP            203 /  674           calibration
USDCOP            651 /  651           CALIBRATION ET VERIFICATION
```

USDCOP porte du journalier etiquete H4 **jusqu au 05/03/2026**. La totalite de
son rejeu, verification comprise, est decidee sur une serie a deux echelles de
volatilite. Le defaut n est donc pas cantonne a l histoire ancienne — c est la
correction que j apporte a la lecture de Claude, qui le croyait inerte.

## Trois decisions d ingenierie

**1. La borne decide, la mesure verifie.** `charger_barres` demarre apres la
derniere barre grossiere, borne publiee par `tools/borne_granularite.py`, puis
recompte les barres grossieres **dans la fenetre rendue** et refuse s il en
reste. Une borne perimee par une collecte plus fraiche fait donc echouer au
lieu de passer inapercue. Meme forme que le refus OHLC de Claude.

**2. La granularite se mesure sur la source, avant exclusion des barres
fabriquees.** Retirer une barre reconstruite creuse un trou de 24 h qui imite
une serie journaliere sans en etre une. Sans cette precaution, DOGUSD etait
refuse pour un trou, pas pour une granularite — faux positif attrape en
mesurant les 148 symboles avant de committer.

**3. `ArchiveHorsUniversError` separe enfin les deux refus qui ont arrete deux
backfills le 22/08.** Une archive VIDE ne signale aucune casse : le lot la
consigne dans `_HORS_UNIVERS.json` et passe au suivant. Une barre CORROMPUE
reste fail-closed avec sentinelle. USDUSC et USDCOP sortent ainsi de l univers
sans casser un run de quinze heures. La rustine d hier soir sur le lot 4
devient inutile.

**Et le trou de scellement est ferme** : `_metadonnees.json` et
`bornes_granularite.json` entrent dans `fichiers_entree` du snapshot. Ces deux
fichiers decident de la fenetre lue autant que le code ; jusqu ici un artefact
pouvait diverger apres un simple recalcul de borne, invisible a
`artefact_brut_valide`.

## Portee mesuree avant de relancer

```
87 symboles   fenetre inchangee
60 symboles   HTF demarre plus tard
 4 symboles   M15 demarre plus tard  (COCOA.fs COFFEE.fs IT40 SPA35)
 2 symboles   hors univers           (USDUSC vide, USDCOP 209 barres H4)
```

Une troncature HTF est neutre tant qu il reste 400 barres avant la premiere
barre evaluee — demontre hier. Apres la porte, le prefixe minimal est de
**796 barres sur 130 symboles**. Il tombe sous le seuil sur sept seulement :
COCOA.fs 100, COFFEE.fs 86, IT40 85, SPA35 93, GER40 375, USDCLP 0, USDCOP 0.

**Prediction verifiable : seuls ces sept-la doivent changer de chiffres.** Les
141 autres sont attendus identiques, et les resumes precedents sont figes dans
`avant_granularite/` pour le prouver symbole par symbole, comme Claude l a fait
avec `avant_borne_htf/`.

Trois de ces sept sont dans les 38 survivants : **IT40 (3e), COFFEE.fs (16e),
GER40 (17e)**. Leur classement n est pas acquis.

## Etat

```
empreinte moteur   16e79f53a610da42 -> 051f50adf179177e
artefacts          les 148 sont perimes, l analyse refuse deja de les classer
backfill v4        8 lots relances a 11h45, fin estimee ~02h00 le 24/08
univers cible      147 symboles (149 - USDUSC - USDCOP)
```
