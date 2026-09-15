# Piloter la rentabilité de V14 — guide opératoire

Toutes les restrictions ont été levées le 14/09/2026 pour laisser le bot vivre
sa preuve. Ce document dit ce que cette preuve coûte, ce qui la rendrait
concluante, et quels leviers sont **mesurés** plutôt que supposés.

Il ne déplace **aucun seuil**. Les seuils sont la décision de l'opérateur ; ici
il n'y a que des mesures, des prix et des critères d'arrêt.

---

## 1. Ce que « garantir la rentabilité » ne peut pas vouloir dire

Aucun dispositif ne garantit une rentabilité future. Ce qui est mesurable, en
revanche, est déjà mesuré — et il est défavorable :

| mesure | valeur | source |
|---|---|---|
| journal live, 10/08 → 15/09 | **917 clôtures**, 88 symboles | `results/trades.ndjson` |
| espérance par trade | **−0,1403 R** (écart-type 1,011 ; erreur type 0,0334) | idem |
| significativité contre une espérance nulle | **t = −4,20 σ**, p ≈ 2,7 × 10⁻⁵ | calcul |
| profit factor | **0,719** | idem |
| sorties `init` (le trade n'a **jamais** travaillé) | **569 / 917 = 62,1 %**, total **−402,24 R** | idem |
| coût estimé par trade | **0,0918 R** (65,4 % de la perte moyenne) | idem, `exact_cost: false` |
| sélection de cohorte par espérance | **p = 0,593** — indistinguable du hasard | `tools/optimiser_allocation_cohorte.py` (PR #8) |

À −4,2 σ et sur 917 clôtures, **ce n'est pas du bruit** : la surface de décision
actuelle perd, de façon mesurée. Élargir les plafonds ne crée pas d'espérance —
cela met à l'échelle une perte déjà démontrée.

C'est la raison pour laquelle ce guide ne promet pas la rentabilité : il montre
comment **acheter la preuve au prix le plus bas** et comment savoir, par avance,
ce qui compterait comme preuve.

## 2. L'état à l'instant, depuis la bascule

Bascule = redémarrage de la boucle armée le **14/09/2026 à 20:11 Z** (elle porte
la posture sans restrictions). Mesure du 15/09 à 09:20 Z, via
`tools/suivi_bascule.py --bascule 2026-09-14T20:11:00+00:00` :

| | avant | après |
|---|---|---|
| n | 873 | **44** |
| espérance | −0,1332 R | **−0,2819 R** |
| témoin hors FX | −0,1169 R | −0,1777 R |
| verdict de l'outil | | **INDISTINGUABLE** (0,93 σ) |

**44 clôtures en 12,8 h : −209,91 EUR, soit −11,5 % d'un compte de 1 822,97 EUR.**
Rythme : 3,43 trades/h → **−16,38 EUR/h → −393 EUR/jour**.

L'intervalle de confiance à 95 % de la moyenne post-bascule est
**[−0,5947 ; +0,0309] R : il contient zéro.** La posture agressive n'est donc
**pas encore démontrée pire** — mais son témoin hors FX recule de 41 % autant
que le global (delta −0,0608 contre −0,1487) : sur 12,8 h, une bonne part du
mouvement vient du marché, pas de la politique.

## 3. Les quatre leviers, classés par ce qui est mesuré

### 3.1 Le coût — à mesurer avant de toucher à quoi que ce soit

`cost_r` vaut 0,0918 R par trade et porte `exact_cost: false` : c'est une
**estimation**, pas une mesure. Rapportée à la perte moyenne, elle en
représente 65 %. Sur 917 trades, cela représente ≈ 84 R de frottement pour
−128,7 R de résultat.

Un chiffre estimé qui pèse deux tiers du résultat est la première chose à
convertir en chiffre mesuré. Le champ `exact_cost` existe : il vaut `false`
partout. Tant qu'il vaut `false`, tout raisonnement sur le coût repose sur une
hypothèse.

### 3.2 Les sorties `init` — c'est là qu'est la perte

569 trades sur 917 sortent sur la protection initiale, sans jamais atteindre une
excursion favorable, et portent −402 R. Depuis la bascule, 35 des 44 clôtures
sont des `init` (−19,77 R).

C'est le seul axe de **sortie** qui soit actionnable, et il ne s'agit pas de
sortir autrement : il s'agit de **ne pas entrer**. Un trade qui n'a jamais
travaillé est une décision d'entrée qui n'a pas été payée par le marché.

> Piège de lecture, déjà rencontré : `trailing` et `breakeven` sont positifs
> **par construction** — un trade sort là *parce qu'il* a travaillé. Ce ne sont
> pas des leviers, ce sont des conséquences.

### 3.3 L'allocation par symbole — mesurément inerte

Tester la règle « tout ce qui est mesuré positif entre » hors échantillon donne
**p = 0,593** : ses 11 retenus font −0,1945 R en OOS, *moins* que les 17 exclus
(−0,1736 R). Le signe se retourne même avec la fenêtre (BTCUSD : +0,522 en IS,
−0,022 sur la fenêtre pleine).

Conclusion opératoire : **arrêter d'investir du raisonnement dans le choix des
symboles**. Il n'en rapporte rien de mesurable. Accessoirement, **9 des 30
symboles de la cohorte n'ont jamais eu un seul trade** — ils occupent une place
sans rien mesurer.

### 3.4 Les axes qui se retournent — à ne pas suivre

| axe | avant la bascule | après la bascule |
|---|---|---|
| côte | shorts **−0,2709 R** (n=286) contre longs −0,0661 (n=587) | shorts **+0,0379 R** (n=22) contre longs −0,6018 (n=22) |

Le même axe donne le pire et le meilleur résultat selon la fenêtre. C'est du
bruit, pas un signal : **ne pas régler la côte sur ces chiffres.**

## 4. Le protocole falsifiable — ce qui compterait comme preuve

Une preuve doit dire, **avant** d'être faite, ce qu'elle établirait et ce
qu'elle coûte.

* **Critère.** L'intervalle de confiance à 95 % de l'espérance post-bascule
  exclut zéro **par le haut** — c'est-à-dire que la borne basse soit positive.
* **Effectif nécessaire.** Pour trancher l'écart observé (−0,1487 R) à 2 σ, avec
  l'écart-type mesuré (1,06), il faut **n ≈ 203 clôtures post-bascule**. On en a
  44.
* **Horizon** : ≈ 59 h, soit **2,5 jours** au rythme actuel de 3,44 trades/h.
* **Test** : `tools/suivi_bascule.py --bascule 2026-09-14T20:11:00+00:00`,
  au même instant pour les deux fenêtres — c'est le seul instrument qui sait
  distinguer « décision » de « marché » grâce à son témoin.

## 5. Le prix de la preuve — et la seule façon de la rendre abordable

Au rythme post-bascule (−4,77 EUR par trade), les 203 clôtures nécessaires
coûtent **−967 EUR, soit −53 % du compte de 1 823 EUR**. Autrement dit : **la
preuve, au niveau de risque actuel, consomme la moitié du compte avant d'être
rendue.**

La même information se paie proportionnellement au risque engagé :

| risque par trade | perte attendue de la preuve |
|---:|---:|
| 2,0 % (aujourd'hui) | **−70,7 %** du compte |
| 1,0 % | −35,4 % |
| 0,5 % | −17,7 % |
| **0,2 %** | **−7,1 %** |
| 0,1 % | −3,5 % |

**C'est le seul arbitrage de ce document qui déplace un seuil, donc il est à
vous, et il n'est pas déplacé ici.** Le point mesuré est que la preuve est
achetable : à 0,2 % par trade elle coûte 7 % du compte au lieu de 53 %, pour la
même conclusion. **La durée de la preuve, elle, ne change pas** : elle dépend du
nombre de clôtures et du rythme de la boucle, pas de leur taille — 203 clôtures à
3,44 trades/h, soit ≈ 59 h dans les deux cas. Réduire le risque ne ralentit donc
pas la preuve ; cela ne fait que la rendre payable au lieu de ruineuse.

## 6. Les signaux orthogonaux, non encore testés

Les leviers ci-dessus cherchent dans l'historique. Deux candidats n'en sont pas,
et ce sont les seuls qui ne soient pas des classements :

* **EMOTION** — réveillé le 14/09 au soir après avoir été muet depuis la
  création du projet (`BLOCK_EMOTION_SIDE` resté à zéro), premier relevé à
  **39 déclenchements en cinq minutes**. Il ne juge pas par espérance de trade :
  il lit valence et arousal. **Il n'a pas encore passé le test IS/OOS + null.**
* **Le veto macro** — livré et prouvé, mais **aucun appelant de production ne
  porte encore la posture macro dans la boucle armée** : il refuse, il ne nuance
  pas.

Un signal qui n'a pas passé le split ne vaut pas mieux qu'une sélection de
symboles. La même discipline s'applique : d'abord le test, ensuite la croyance.

## 7. Critères d'arrêt

À écrire avant de les subir :

1. **Sur les preuves.** Quand n ≥ 203 : si la borne **haute** de l'IC 95 % de
   l'espérance post-bascule est **inférieure à zéro**, la posture sans
   restrictions est démentie par la mesure et se retire sur ce chiffre, pas sur
   une humeur.
2. **Sur le compte.** Un seuil de perte en pourcentage de l'équité est la seule
   barrière qui reste : les plafonds mesurent l'exposition **simultanée**, pas la
   perte **journalière** — la rotation les recharge à chaque clôture, et les
   protections journalières sont désarmées. Ce seuil est votre décision ; il
   n'existe pas aujourd'hui.
3. **Sur le coût.** Tant que `exact_cost` vaut `false`, aucun arbitrage de coût
   n'est mesuré. Le convertir est un préalable, pas une amélioration.

## 8. Ce que ce document ne permet pas de conclure

Rien ici ne dit qu'une posture est rentable. Les 44 clôtures post-bascule ne
tranchent pas (IC à 95 % contenant zéro) ; l'écart-type est grand, l'échantillon
est petit, une seule place est concernée (Axi DEMO), et le journal est **vivant** :
chaque chiffre porte sa fenêtre et son effectif, et deux exécutions à des instants
différents ne donnent pas les mêmes totaux. `cost_r` est estimé et non mesuré.
Enfin, corréler une posture à un résultat sur 12,8 h confond la politique, le
marché et la chance — c'est exactement ce que le témoin du suivi de bascule
existe pour démêler.

## 9. Reproduire chaque chiffre

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\suivi_bascule.py --bascule 2026-09-14T20:11:00+00:00
.\.venv\Scripts\python.exe -X utf8 tools\optimiser_allocation_cohorte.py --journal results\trades.ndjson
```
