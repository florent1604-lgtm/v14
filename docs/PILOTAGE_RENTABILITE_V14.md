# Piloter la rentabilité de V14 — guide opératoire

Toutes les restrictions ont été levées le 14/09/2026 pour laisser le bot vivre
sa preuve. Ce document dit ce que cette preuve coûte, ce qui la rendrait
concluante, et quels leviers sont **mesurés** plutôt que supposés.

Il ne déplace **aucun seuil**. Les seuils sont la décision de l'opérateur ; ici
il n'y a que des mesures, des prix et des critères d'arrêt.

**Mesure du 15/09/2026 à 10:15 Z**, compte DEMO 10055401. Le journal est vivant :
chaque chiffre porte sa fenêtre et son effectif, et deux exécutions à des instants
différents ne donnent pas les mêmes totaux. Les commandes de reproduction sont au
§9 — citer un chiffre sans sa fenêtre et son N, ce n'est pas le reproduire.

---

## 1. Ce que « garantir la rentabilité » ne peut pas vouloir dire

Aucun dispositif ne garantit une rentabilité future. Ce qui est mesurable, en
revanche, est déjà mesuré — et il est défavorable :

| mesure | valeur | source |
|---|---|---|
| journal live, 10/08 11:01 → 15/09 10:10 Z | **926 clôtures**, 89 symboles | `results/trades.ndjson` |
| espérance par trade | **−0,1410 R** (écart-type 1,0098 ; erreur type 0,0332) | idem |
| significativité contre une espérance nulle | **t = −4,25 σ**, p ≈ 2,2 × 10⁻⁵ | idem |
| profit factor | **0,720** | idem |
| total | **−130,55 R** | idem |
| sorties `init` (le trade n'a **jamais** travaillé) | **574 / 926 = 62,0 %**, total **−406,77 R** | idem |
| coût estimé par trade | **0,0919 R** (65,2 % de la perte moyenne) | idem, `exact_cost: false` sur **926/926** |
| sélection de cohorte par espérance | **p = 0,58** — indistinguable du hasard | `tools/optimiser_allocation_cohorte.py` (PR #8) |

À −4,25 σ sur 926 clôtures, **ce n'est pas du bruit** : la surface de décision
actuelle perd, de façon mesurée. Élargir les plafonds ne crée pas d'espérance —
cela met à l'échelle une perte déjà démontrée.

C'est la raison pour laquelle ce guide ne promet pas la rentabilité : il montre
comment **acheter la preuve au prix le plus bas** et comment savoir, par avance,
ce qui compterait comme preuve.

## 2. L'état à l'instant, depuis la bascule

Bascule = redémarrage de la boucle armée le **14/09/2026 à 20:11 Z** (elle porte
la posture sans restrictions) :

| | avant | après |
|---|---|---|
| n | 873 | **53** |
| espérance | −0,1332 R | **−0,2695 R** |
| témoin hors FX | −0,1169 R | −0,1462 R |
| écart avant/après | | **−0,1363 R** |
| verdict de l'outil | | **INDISTINGUABLE** |

**53 clôtures depuis la bascule : −244,43 EUR, soit −4,612 EUR par trade.** Au
rythme de 3,80 clôtures/h, cela fait **−17,5 EUR/h → −422 EUR/jour**.

L'intervalle de confiance à 95 % de la moyenne post-bascule est
**[−0,5456 ; +0,0066] R : il contient zéro.** La posture agressive n'est donc
**pas encore démontrée pire** — mais son témoin hors FX recule de 21,5 % autant
que le global (delta −0,0293 contre −0,1363) : sur ≈ 14 h, une part du mouvement
vient du marché, pas de la politique.

> **L'équité de ce guide a été corrigée.** Le solde courtier relevé à cet instant
> est **1 327,07 EUR** (équité 1 340,84). La version précédente de ce document
> citait 1 822,97 EUR : ce chiffre ne se réconcilie ni avec le solde du courtier
> ni avec le journal, et il n'est pas repris ici. Conséquence, et elle est
> limitée : dans le tableau de §5 les **pourcentages ne dépendent pas de
> l'équité** (elle s'annule dans le rapport), seule la colonne en euros en
> dépendait. Le pourcentage publié était juste ; le montant ne l'était pas.
>
> Deuxième écart, non résolu : la somme `pnl_r × risk_money` du journal vaut
> **−2 722,87 EUR** sur 926 clôtures, ce qu'un compte arrivé à 1 327 EUR ne peut
> pas avoir encaissé. **Les figures en R sont donc les seules fiables.** La
> colonne en euros du journal — et la seule ligne « engagé mesuré » de §5, qui
> lit `risk_money` — héritent de cette réserve.

## 3. Les quatre leviers, classés par ce qui est mesuré

### 3.1 Le coût — à mesurer avant de toucher à quoi que ce soit

`cost_r` vaut 0,0919 R par trade et porte `exact_cost: false` sur **la totalité
du journal** (926/926) : c'est une **estimation**, pas une mesure. Rapportée à la
perte moyenne, elle en représente 65 %.

Un chiffre estimé qui pèse deux tiers du résultat est la première chose à
convertir en chiffre mesuré. Tant que `exact_cost` vaut `false`, tout raisonnement
sur le coût repose sur une hypothèse.

### 3.2 Les sorties `init` — c'est là qu'est la perte

574 trades sur 926 sortent sur la protection initiale, sans jamais atteindre une
excursion favorable, et portent −406,77 R. Depuis la bascule, 40 des 53 clôtures
sont des `init` (−24,30 R).

C'est le seul axe de **sortie** qui soit actionnable, et il ne s'agit pas de
sortir autrement : il s'agit de **ne pas entrer**. Un trade qui n'a jamais
travaillé est une décision d'entrée qui n'a pas été payée par le marché.

> Piège de lecture, déjà rencontré : `trailing` et `breakeven` sont positifs
> **par construction** — un trade sort là *parce qu'il* a travaillé. Ce ne sont
> pas des leviers, ce sont des conséquences.

### 3.3 L'allocation par symbole — mesurément inerte

Tester la règle « tout ce qui est mesuré positif entre » hors échantillon
(555 clôtures de classement / 371 de jugement) donne **p = 0,58** :

| compartiment | OOS n | espérance |
|---|---:|---:|
| **retenus** — les 11 « mesurés positifs » | 83 | **−0,1769 R** |
| exclus — les 17 « mesurés négatifs » | 153 | −0,1620 R |
| non mesurés | 135 | −0,2100 R |
| **tout** — aucune sélection | 371 | −0,1828 R |

Les retenus font *moins* bien que la moyenne du journal. Le signe se retourne
aussi avec la fenêtre : **BTCUSD** est premier du classement in-sample et sa
mesure pleine fenêtre est négative. Conclusion opératoire : **arrêter
d'investir du raisonnement dans le choix des symboles** — il n'en rapporte rien
de mesurable.

Sur la cohorte de 30 symboles effectivement portée par la boucle :
**8 n'ont jamais eu un seul trade** (BAT-USD, COMP-USD, DOTUSD, KSM-USD,
LRC-USD, MKR-USD, SAND-USD, XTZ-USD) — ils occupent une place sans rien mesurer.
La cohorte rend **+0,1353 R sur la fenêtre pleine (217 clôtures) mais −0,0091 R
hors échantillon (95 clôtures)** : l'écart entre ces deux nombres est la mesure
de ce que le choix « au mesuré » achète, et il n'achète rien.

### 3.4 Les axes qui se retournent — à ne pas suivre

| axe | avant la bascule | après la bascule |
|---|---|---|
| côte | shorts **−0,2709 R** (n=286) contre longs −0,0661 (n=587) | shorts **−0,0525 R** (n=27) contre longs −0,4948 (n=26) |

Le même axe donne le pire et le meilleur résultat selon la fenêtre. C'est du
bruit, pas un signal : **ne pas régler la côte sur ces chiffres.**

## 4. Le protocole falsifiable — ce qui compterait comme preuve

Une preuve doit dire, **avant** d'être faite, ce qu'elle établirait et ce
qu'elle coûte.

* **Critère.** L'intervalle de confiance à 95 % de l'espérance post-bascule
  exclut zéro **par le haut** — c'est-à-dire que la borne basse soit positive.
* **Effectif nécessaire.** Pour trancher l'écart observé (−0,1363 R) à 2 σ, avec
  l'écart-type post-bascule mesuré (1,0257), il faut
  `n = (2 σ / |écart|)² = 227` clôtures. **On en a 53.** Le nombre est produit
  par l'outil, pas dérivé à la main (§5 et §9).
* **Horizon** : ≈ 59,8 h au rythme actuel de 3,80 clôtures/h.
* **Test** : `tools/suivi_bascule.py`, au même instant pour les deux fenêtres —
  c'est le seul instrument qui sait distinguer « décision » de « marché » grâce à
  son témoin.

## 5. Le prix de la preuve — et la seule façon de la rendre abordable

### Le fondement : une seule formule, énoncée

```
perte attendue de la preuve = n_requis × |espérance post-bascule| × risque par trade × équité
```

`n_requis` vient du §4 (227), l'espérance post-bascule est ce que chaque clôture
coûte en R (−0,2695), le risque est la part d'équité engagée à chaque clôture.
**Rien d'autre n'entre dans la formule**, donc deux lignes ne peuvent pas se
contredire : chacune ne change que le risque.

Cette formule a un propriétaire dans le dépôt —
`tools/suivi_bascule.py --equite <E>` — et le tableau ci-dessous est sa sortie.
Les éditions précédentes de ce document portaient **deux chiffres différents pour
la même ligne** (−53 % dans la prose, −70,7 % dans le tableau) parce qu'elles
mêlaient deux bases non énoncées : l'extrapolation du résultat observé en euros,
qui incorpore le risque *réellement engagé*, et l'extrapolation au plafond. Le
tableau ci-dessous n'a plus qu'une base.

| risque par trade | perte attendue de la preuve | part du compte |
|---:|---:|---:|
| 2,0 % (le plafond par trade) | 1 623,71 EUR | **122,4 %** |
| 1,0 % | 811,85 EUR | 61,2 % |
| **1,07 % — le risque réellement engagé** | **867,06 EUR** | **65,3 %** |
| 0,5 % | 405,93 EUR | 30,6 % |
| 0,2 % | 162,37 EUR | 12,2 % |
| 0,1 % | 81,19 EUR | 6,1 % |

Deux lectures, et la seconde est celle qui compte :

* **au plafond** (2 % par trade), la preuve coûte **122 % du compte** : elle le
  consomme entièrement avant d'être rendue ;
* **au risque réellement engagé** — 1,07 %, mesuré sur `risk_money` — elle coûte
  déjà **65 % du compte**.

La même information s'achète à **12 % du compte** à 0,2 % par trade. **C'est le
seul arbitrage de ce document qui déplace un seuil, donc il est à vous, et il
n'est pas déplacé ici.** Le point mesuré est que la preuve est achetable : à
0,2 % elle coûte un huitième de ce qu'elle coûte aujourd'hui, pour la même
conclusion. **La durée de la preuve, elle, ne change pas** : elle dépend du
nombre de clôtures et du rythme de la boucle, pas de leur taille — 227 clôtures à
3,80 clôtures/h, soit ≈ 60 h dans tous les cas. Réduire le risque ne ralentit donc
pas la preuve ; cela ne fait que la rendre payable au lieu de ruineuse.

## 6. Les signaux orthogonaux, non encore testés

Les leviers ci-dessus cherchent dans l'historique. Deux candidats n'en sont pas,
et ce sont les seuls qui ne soient pas des classements :

* **EMOTION** — réveillé le 14/09 au soir après avoir été muet depuis la création
  du projet (`BLOCK_EMOTION_SIDE` resté à zéro), premier relevé à **39
  déclenchements en cinq minutes**. Il ne juge pas par espérance de trade : il lit
  valence et arousal. **Il n'a pas encore passé le test IS/OOS + null.**
* **Le veto macro** — livré et prouvé, mais **aucun appelant de production ne
  porte encore la posture macro dans la boucle armée** : il refuse, il ne nuance
  pas.

Un signal qui n'a pas passé le split ne vaut pas mieux qu'une sélection de
symboles. La même discipline s'applique : d'abord le test, ensuite la croyance.

## 7. Critères d'arrêt

À écrire avant de les subir :

1. **Sur les preuves.** Quand n ≥ 227 : si la borne **haute** de l'IC 95 % de
   l'espérance post-bascule est **inférieure à zéro**, la posture sans
   restrictions est démentie par la mesure et se retire sur ce chiffre, pas sur
   une humeur.
2. **Sur le compte.** Un seuil de perte en pourcentage de l'équité est la seule
   barrière qui reste : les plafonds mesurent l'exposition **simultanée**, pas la
   perte **journalière** — la rotation les recharge à chaque clôture. Ce seuil est
   votre décision ; il n'existe pas aujourd'hui. **Il n'a jamais été aussi
   nécessaire : le journal a perdu 18,4 % du compte en ≈ 14 h.**
3. **Sur le coût.** Tant que `exact_cost` vaut `false`, aucun arbitrage de coût
   n'est mesuré. Le convertir est un préalable, pas une amélioration.

## 8. Ce que ce document ne permet pas de conclure

Rien ici ne dit qu'une posture est rentable. Les 53 clôtures post-bascule ne
tranchent pas (IC à 95 % contenant zéro) ; l'écart-type est grand, l'échantillon
est petit, une seule place est concernée (Axi DEMO), et le journal est **vivant**.
`cost_r` est estimé et non mesuré, et la conversion en euros du journal ne se
réconcilie pas avec le solde du courtier (§2). Enfin, corréler une posture à un
résultat sur ≈ 14 h confond la politique, le marché et la chance — c'est
exactement ce que le témoin du suivi de bascule existe pour démêler.

## 9. Reproduire chaque chiffre

```powershell
# le suivi avant/après, le témoin, le verdict — et le prix de la preuve
.\.venv\Scripts\python.exe -X utf8 tools\suivi_bascule.py `
    --bascule 2026-09-14T20:11:00+00:00 --equite 1327.07

# la sélection de symboles hors échantillon et son null
.\.venv\Scripts\python.exe -X utf8 tools\optimiser_allocation_cohorte.py `
    --journal results\trades.ndjson

# la même, jugée sur la cohorte que la boucle porte réellement.
# `--cohorte` prend la liste jointe par des virgules :
#   titanium\execution\demo_cohort.py, DEMO_COHORT_SYMBOLS
```

`--equite` est le seul paramètre que l'appelant doit fournir : le journal ne
porte pas l'équité du compte. Le solde se lit au tableau de bord (`/api/state`,
champ `account.balance`).
