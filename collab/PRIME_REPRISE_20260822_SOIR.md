# Reprise Prime — 22/08/2026, 19h40

Note ecrite par Claude a la demande de Florent, apres relance complete de ton
arbre (clients, demons, kernels) et une session neuve sur Opus 5.

Ta session precedente etait **gelee** : kernels a 18,2 s de CPU cumule avec un
delta nul sur quatre heures, demon a 289,6 s sans progression. Le preflight a
mis en quarantaine trois artefacts orphelins, dont le worker `166b79bdc1ae` et
son bail.

Ton travail, lui, a tourne sans toi — et c'est la bonne nouvelle de la journee.

---

## 1. Ton correctif d'horloge de barre fonctionne, c'est prouve

Les six artefacts detruits ce matin sont reparus et fideles :

```
AAVE-USD   calibration n=3772   global n=5805
           avant destruction :  3773  /  5806
```

Une barre d'ecart. La regression `n_enter = 0` est eteinte.

Les trades bruts portent `decision_at`, `quantity`, `asset_class`, `trade_id`,
`side` — **l'auditeur A/B de Codex est debloque.**

---

## 2. Ton garde-fou a fonctionne, et il a arrete le run

A 19:03:17 le lot 0 a echoue :

```
DJ30.FS H4: 1 OHLC invalides (exemples time_utc: 1259013600)
-> _RUN_FAILED.json publie
-> lots 1, 4, 5, 6 arretes sur sentinelle
-> lots 2, 3, 7 termines puis arretes
```

Bilan : **34 symboles sur 149**, run interrompu.

Le comportement est le bon — refuser plutot que produire un artefact
silencieusement faux. C'est la **portee** du refus qui etait fausse.

### La barre

```
DJ30.fs, 23 novembre 2009, low = 0.00
o=10411.50  h=10437.50  l=0.00  c=10403.50   tick_volume=16923
```

Elle est **authentique**. `copy_rates_range` la rend encore aujourd'hui, en D1,
H4 et H1. Le defaut est chez Axi, pas dans ton archive. Elle n'est donc ni
reparable depuis la source, ni interpolable sans fabriquer un prix qui n'a
jamais existe — ce que Florent a explicitement interdit : « aucune donnee ne
doit etre tronquee ni recalculee, il faut du reel ».

### Pourquoi le refus etait mal porte

`rejeu_univers.rejouer_symbole_brut` chargeait le HTF **sans borne** :

```python
ltf = charger_barres(symbole, ltf_tf, barres, **portes)   # borne par `barres`
htf = charger_barres(symbole, htf_tf, **portes)           # TOUT le fichier
```

Le M15 de DJ30.fs commence le **10 mai 2022**. La barre de 2009 est treize ans
avant la premiere barre que le rejeu peut lire. Elle etait validee, puis jetee.

Dans `charger_barres`, la validation OHLC tournait aussi avant que `count` ne
reduise la fenetre : on refusait des donnees qu'on ne rendait pas.

---

## 3. Ce que j'ai change, et ce que je n'ai pas touche

**`titanium/data/archive_barres.py`**
- `_ohlc_invalides` extrait, applique deux fois : une mesure sur l'archive
  entiere qui informe, un refus sur la fenetre rendue qui decide.
- Nouveau parametre `depuis_utc` : borne basse appliquee **avant** la
  validation.
- `archive_quality` publie desormais `ohlc_invalides_archive` et `depuis_utc`,
  pour que l'anomalie reste visible au lieu d'etre tue.
- Le garde-fou est intact : une barre invalide **dans la fenetre rendue** fait
  toujours echouer, fail-closed. Verifie : sans `count`, DJ30.fs H4 refuse
  toujours.

**`tools/rejeu_univers.py`**
- Le HTF est borne a la portee du LTF moins **1826 jours** de prechauffe.
  Le plus long indicateur HTF est une SMA 200, soit 33 jours en H4 : la marge
  est deux ordres de grandeur au-dessus du besoin, donc l'amorcage est
  rigoureusement identique a un chargement integral. **Les 34 symboles deja
  rejoues restent comparables aux suivants.**

**Tests** : 4 nouveaux dans `test_archive_barres.py` (invalide hors fenetre ne
refuse pas / reste visible dans les metadonnees / dans la fenetre refuse
toujours / sans count la portee reste le fichier), et
`test_rejeu_univers_quality.py` mis a jour.

**Non touche** : aucune donnee d'archive, aucun seuil de strategie, aucun
quorum, aucun parametre de risque.

---

## 4. Une decouverte plus lourde que la barre a zero

En cherchant la cause, j'ai trouve que **le fichier H4 contient des barres
JOURNALIERES sur sa portion ancienne**. Pour DJ30.fs : 2729 lignes sur 11881,
soit 23 %, de 2008-10-11 a 2018-11-20. La colonne `timeframe` dit `H4` sur les
11881 lignes.

Ce n'est pas un bug de l'archiveur. J'ai interroge le courtier : pour ce
symbole en 2009, `copy_rates_range` rend **les memes sept barres journalieres**
que l'on demande D1, H4 ou H1. Axi n'a pas d'historique intraday si loin, et
sert la serie journaliere sous l'etiquette demandee. EURNZD, lui, rend bien 36
barres H4 authentiques sur la meme fenetre — le comportement depend du symbole.

Ampleur : **59 symboles sur 149 en H4, 26 en H1.** M1, M5, M15 et D1 sont
propres.

**Impact sur les resultats : nul en verification.** Seuls COCOA.fs et COFFEE.fs
ont un rejeu qui mord sur la zone journaliere, et uniquement en calibration :

```
COFFEE.fs  verification n=3716, 100 % vrai H4  ->  +0,1212 tient
COCOA.fs   verification n=3290, 100 % vrai H4  ->  +0,0508 tient
```

Zero trade de verification contamine, sur les 34 symboles. Les chiffres publies
tiennent. Mais la borne de prechauffe de 1826 jours ne resout pas ce point-la :
il faudra decider si l'archive doit marquer la granularite reelle par barre.
**C'est ton arbitrage.**

---

## 5. Ce que les 34 disent

Neuf positifs en verification, vingt-cinq negatifs.

```
BTCUSD     +0,1568     AUS200    +0,0546
BTC-JPY    +0,1456     COCOA.fs  +0,0508
COFFEE.fs  +0,1212     ----------------------
BRENT.fs   +0,1116     AVAX-USD  -0,5892
BNB-USD    +0,1008     CRV-USD   -0,8426
DAX40.fs   +0,0706     BAT-USD   -1,3273
COPPER.fs  +0,0650     COMP-USD  -1,6366
```

Les cinq candidats de Codex presents sont les cinq meilleurs. **DAX40.fs,
COPPER.fs et COCOA.fs sont nouveaux** — absents de sa liste de 13, positifs
hors echantillon. Le FX reste uniformement negatif.

---

## 6. Deux resultats qui attendent ton arbitrage

Detail complet : `docs/RAPPORT_COUT_DECISIONNEL_20260822.md` (commit `cdd09ec`).

**a) Le classement par actif est un classement de frais.** AVAX-USD et ADAUSD
ont les meilleures esperances **brutes** de l'echantillon (+0,3427 et +0,3004 R)
et ne perdent que par le cout. Une porte de cout absolue fait basculer la
verification de −0,1454 R a +0,1620 R en gardant 33 % des trades ; monotone,
survivant au controle temporel (21 cellules trimestre x symbole sur 24).
`cost_r = spread / r_unit` a 0,0 % pres, et `r_unit` est connu a l'entree :
calculable avant l'ordre, sans lookahead. **C'est un changement de seuil, donc
ta decision.**

**b) L'affinage M5 de V12 ne merite pas d'etre cable.** Porte dans
`titanium/features/entry_refine.py` avec le plancher de resserrement neutralise
(1.0 au lieu de 0.6), puis mesure : la zone FVG **degrade sur 4 symboles sur 4**
(−0,0614 R agrege) et le timing n'est pas demontre (+1,2 sigma, et il echoue
sur BTC-JPY, le meilleur candidat). Le module est **inerte**, cable a rien.
Limite : la zone est evaluee comme filtre, pas comme repositionnement du prix
d'entree — trancher demande un rejeu A/B.

---

## 7. Le run est reparti, et il RECALCULE TOUT

```
19:48   8 lots relances, 16 processus
        sentinelle effacee (archivee dans
        collab/prime_agent/runs/strategie-entree-20260819/_RUN_FAILED_20260822T1903.json)
        fin estimee ~09h15 le 23/08
```

**Il ne reprend pas aux 34 acquis, il refait les 149.** Ce n'est pas un
accident : j'ai touche a `rejeu_univers.py` et a `archive_barres.py`, donc le
`snapshot_id` a change, donc `artefact_brut_valide` declare les 34 artefacts
perimes. C'est ton snapshot transitif (`dbfdeb2`) qui joue son role — interdire
qu'un meme jeu de resultats melange deux versions du moteur.

J'ai laisse faire plutot que de forcer la reprise. Mon raisonnement est que la
marge de prechauffe rend les resultats identiques, mais un raisonnement n'est
pas une mesure, et ton garde-fou existe precisement pour ne pas avoir a me
croire.

Pour que ce soit verifiable, **les 148 resumes precedents sont figes** dans
`collab/prime_agent/runs/strategie-entree-20260819/avant_borne_htf/`. Quand les
premiers symboles retomberont, comparer : AAVE-USD doit rendre exactement
n=5805, esp −0,0258 R. Si l'ecart est nul sur une dizaine de symboles, la borne
HTF est neutre et c'est demontre. Sinon, c'est moi qui ai tort et il faudra
revenir sur le commit `3bbbc11`.

## 8. Commits de la journee, cote Claude

```
cdd09ec  mesurer le cout par trade et mettre a l epreuve l affinage M5
3bbbc11  porter le refus qualite sur la fenetre rendue, pas sur le fichier
```

Tout est sur `master`, 2103 tests passes, lint propre.

## 9. Ce qui t'attend, par ordre d'urgence

1. **Verifier la neutralite de la borne HTF** contre `avant_borne_htf/`. C'est
   le seul point ou j'ai modifie ton moteur ; si je me suis trompe, tout le
   reste attend.
2. **Arbitrer la porte de cout** (section 6a). C'est un changement de seuil,
   donc ta decision, et c'est le levier le plus fort mesure a ce jour :
   verification de −0,1454 R a +0,1620 R.
3. **Decider du marquage de granularite reelle** dans l'archive (section 4).
   59 symboles portent des barres journalieres etiquetees H4. Sans impact
   mesure aujourd'hui, mais c'est une bombe a retardement pour tout rejeu qui
   remonterait plus loin que le M15 disponible.
4. **Rendre la main a Codex** pour l'A/B : les artefacts bruts portent
   desormais `decision_at`, `quantity`, `asset_class`, `trade_id`, `side`.

## 10. Ce que je n'ai pas fait

Aucune donnee d'archive modifiee. Aucun seuil, quorum ou parametre de risque
touche. Aucun ordre, aucun armement. Le module `entry_refine` est inerte.
Le seul service redemarre est le tien, sur demande explicite de Florent.
