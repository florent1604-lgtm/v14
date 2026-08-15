# Performance réelle des nouvelles stratégies d'exécution

**Claude · 15/08/2026** · 100 clôtures live, compte DEMO 10055401 · lecture seule.

---

## État au moment de la mesure

La boucle **tourne** — battement frais, armée — mais avec **6 actifs portables**
seulement : c'est le week-end, seule la crypto cote. Le catalogue en compte 149
en semaine.

| | 13/08 | 15/08 |
|---|---|---|
| equity | 4 124 € | **3 669 €** |
| clôtures journalisées | 44 | **100** |
| espérance | −0,3868 R | **−0,2610 R** |
| PF | 0,348 | **0,494** |
| winrate | 36,4 % | **43,0 %** |

L'espérance et le PF s'améliorent nettement, et pourtant l'equity perd 455 €
en deux jours. Les deux faits ne se contredisent pas : le système perd moins
par trade mais en fait beaucoup plus — 56 clôtures en 48 h.

---

## 1. Les ordres limites — la seule nouveauté qui tient ses promesses

```
placés     189
remplis     65   34,4 %
expirés    124   65,6 %

économie réalisée   63 fills · +0,0995 R en moyenne · +6,27 R cumulés
                    59 clôtures · +0,0988 R · +5,83 R
slippage           124 mesures · −0,0016 R — négligeable, et favorable
```

**Chaque fill économise environ un dixième de R.** Sur les 59 positions closes
issues d'une limite, cela fait **+5,8 R récupérés** sur un cumul total de
−26,1 R. Sans les ordres limites, la perte serait de l'ordre de −32 R.

Mieux : les trades entrés à la limite performent **au-dessus de la moyenne** —
−0,1829 R contre −0,2610 R pour l'ensemble. L'attente d'un meilleur prix ne
sélectionne pas de moins bons setups.

Le taux d'expiration de 65,6 % n'est pas une perte : un ordre expiré est un
trade non pris à un prix qu'on jugeait mauvais. Reste à mesurer ce que ces 124
setups auraient rendu s'ils avaient été pris au marché — ce chiffre-là
n'existe pas encore.

---

## 2. Le secours displacement — ma modification, et elle n'est pas bonne

```
displacement   34 clôtures · −0,3210 R ± 0,1424 · PF 0,418 · win 41,2 %
formes         18 clôtures · −0,1795 R ± 0,2339 · PF 0,663 · win 38,9 %
(aucune)       19 clôtures · −0,1198 R ± 0,1579 · PF 0,628 · win 57,9 %
```

La branche que j'ai ajoutée fait **moins bien que les formes**, et moins bien
que les trades **sans aucun pilier G5**. L'outil de mesure refuse le verdict —
il manque deux clôtures « formes » pour atteindre son seuil de 20 par branche —
et il a raison : l'écart de 0,14 R donne un z de **−0,52**, indistinguable du
bruit même à échantillon complet.

Mais le croisement suivant explique ce qui se passe, et il est plus net.

---

## 3. Le résultat qui compte : le quorum est inversé

```
S=2   64 clôtures · −0,1767 R ± 0,1104 · PF 0,622 · win 46,9 %
S=3   35 clôtures · −0,4419 R ± 0,1353 · PF 0,287 · win 34,3 %
S=4    1 clôture
```

**Les setups à 3 piliers perdent deux fois et demie plus que ceux à 2.**
z = −1,52 : la direction est franche, la conclusion pas encore acquise, mais
l'échantillon n'est plus anecdotique.

Or `titanium/confiance.py` **augmente le risque avec le nombre de piliers** :

| piliers | risque engagé |
|---|---|
| 2/4 | 0,50 % |
| 3/4 | ~1,13 % |
| 4/4 | 1,75 % |

**On mise plus de deux fois plus gros sur la strate qui perd le plus.** Le
fichier lui-même le disait — « c'est un pari, pas une optimisation démontrée,
rien n'établit que le nombre de piliers prédit le résultat ». Il y a maintenant
100 clôtures, et elles disent que le pari est à l'envers.

C'est aussi ce qui condamne ma modification :

```
displacement S=2   17 · −0,0158 R · PF 0,960 · win 58,8 %   ← la meilleure cellule
displacement S=3   17 · −0,6262 R · PF 0,112 · win 23,5 %   ← la pire
formes S=2         15 · −0,0871 R · PF 0,828
formes S=3          3 · −0,6414 R · PF 0,032
```

Le displacement n'est pas mauvais en soi — `displacement S=2` est la meilleure
cellule de tout le tableau, presque à l'équilibre. Le mal est concentré en S=3,
et il frappe **les deux sources pareillement**. Ce n'est donc pas le
displacement qui est en cause, c'est le passage en S=3.

Sauf que mon correctif a fait exactement cela : il a **multiplié par six** le
nombre de trades S=3 (17 contre 3 pour les formes seules), en fournissant le
troisième pilier. Il a poussé des trades vers la strate la plus perdante *et*
vers un risque deux fois plus élevé.

---

## 4. Où part l'argent, par classe d'actif

```
fx        57 · −0,3445 R · PF 0,404 · cumul −19,64 R
indices   20 · −0,3484 R · PF 0,318 · cumul  −6,97 R
energie   14 · +0,0311 R · PF 1,073 · cumul  +0,44 R
metaux     5 · +0,1003 R · PF 2,003 · cumul  +0,50 R
crypto     4 · −0,1088 R ·           · cumul  −0,44 R
```

**FX et indices portent la totalité de la perte** (−26,6 R sur un total de
−26,1 R). Énergie et métaux sont légèrement positifs, sur des échantillons trop
petits pour conclure.

---

## 5. La gestion de sortie reste la partie saine

```
init        54 · −0,9375 R · cumul −50,62 R
trailing    24 · +0,9994 R · cumul +23,98 R
breakeven   22 · +0,0243 R · cumul  +0,53 R
```

Vingt-quatre sorties en trailing à **+1,00 R de moyenne** : ce que la gestion
attrape, elle le tient. Le breakeven fait son travail à l'équilibre exact. La
perte est intégralement dans les 54 stops pleins.

---

## Ce que je recommande, par ordre de force

**1. Mesurer avant d'agir sur le sizing — mais le sujet est urgent.** La
modulation du risque par le nombre de piliers repose sur une hypothèse que les
données contredisent. Je ne propose pas de l'inverser sur un z de 1,52 ; je
propose de **la geler à plat** (même risque quel que soit le nombre de piliers)
le temps d'accumuler. Une modulation neutre ne coûte rien si l'hypothèse est
vraie, et arrête l'hémorragie si elle est fausse. C'est le seul changement dont
l'asymétrie est favorable.

**2. Reconsidérer mon secours displacement.** Il n'est pas nuisible en lui-même,
mais il alimente une strate perdante. Deux options : l'éteindre
(`DISPLACEMENT_FALLBACK = False`, un test verrouille le retour arrière), ou —
mieux — le garder et corriger le sizing, puisque `displacement S=2` est la
meilleure cellule mesurée. La décision dépend du point 1.

**3. Garder les ordres limites tels quels.** C'est la seule brique dont le
bénéfice est mesuré, positif et cohérent : +0,0995 R par fill, slippage
négligeable, et des trades entrés meilleurs que la moyenne.

**4. Ne rien conclure de la matrice d'exécution.** `results/execution_matrix_full/`
classe quinze politiques en dry-run, mais son propre encart « Limites de
fidélité » précise que les quotes L1, les profondeurs et les chemins
intrabarres sont **synthétiques**. Le classement `cancel_replace` en tête ne
mesure pas un edge : il mesure le comportement d'un simulateur. Sa
recommandation — comparer `market`, `limit_passive` et `adaptive` sur des
quotes broker archivées avant toute promotion — est la bonne lecture.

---

## Réserves

Cent clôtures restent peu pour trancher entre strates : la cellule
`displacement S=3` compte 17 trades, `formes S=3` en compte 3. Aucun des écarts
rapportés ici n'atteint |z| = 2.

Ce qui a changé de statut depuis le 13/08 n'est pas la certitude, c'est la
**cohérence** : le même signe se retrouve dans trois découpages indépendants —
par source, par nombre de piliers, et dans leur croisement. Un artefact
expliquerait un tableau ; il en explique difficilement trois.

Toutes les clôtures listées ici ont été gérées avec `breakeven_r = 0,8` et le
sizing actuel. Aucun seuil n'a été modifié pour produire ce rapport.
