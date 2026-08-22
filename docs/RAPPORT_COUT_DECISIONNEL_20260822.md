# Le coût n'est pas une propriété de l'actif, c'est une propriété du stop

Analyse des artefacts bruts produits par le rejeu réparé de Prime
(commit `6845a43`, lot relancé le 22/08/2026 à 15:51).
Mesures faites par Claude, sur 8 symboles terminés sur 149.
**Rien n'a été modifié : ni code, ni paramètre, ni service.**

---

## 1. Ce qui a rendu l'analyse possible

Le correctif d'horloge de barre de Prime a restitué les artefacts bruts, avec
`decision_at`, `quantity`, `asset_class`, `cost_r`, `gross_r`, `net_r`,
`mae_r`, `mfe_r`, `r_unit`, `split` et 100 indicateurs par trade.
La reconstruction est fidèle : AAVE-USD retrouve calibration n=3772 /
global n=5805, contre 3773 / 5806 avant destruction.

C'est la première fois que le coût est mesurable **trade par trade** et non
au niveau du symbole.

---

## 2. Le signal fonctionne partout ; c'est le coût qui trie

| symbole | n | brut R | coût R | net R | coût / brut |
|---|---:|---:|---:|---:|---:|
| BTC-JPY | 5651 | +0,2508 | 0,0924 | **+0,1584** | 37 % |
| BRENT.fs | 5681 | +0,2425 | 0,1005 | **+0,1420** | 41 % |
| BNB-USD | 6010 | +0,2629 | 0,1838 | **+0,0791** | 70 % |
| AUS200 | 5314 | +0,1285 | 0,0844 | **+0,0441** | 66 % |
| ADAUSD | 6085 | +0,3004 | 0,5237 | −0,2233 | 174 % |
| AVAX-USD | 6099 | +0,3427 | 0,4860 | −0,1433 | 142 % |
| AUDCAD | 5137 | +0,0189 | 0,1918 | −0,1729 | 1016 % |

**AVAX-USD et ADAUSD ont les meilleures espérances brutes de l'échantillon**
(+0,3427 et +0,3004 R), meilleures que BTC-JPY. Elles ne perdent qu'à cause du
coût. Le classement par actif publié jusqu'ici n'est donc pas un classement de
qualité de signal : c'est un classement de frais.

AUDCAD est la seule exception, et elle est instructive : brut +0,0189 R, soit
pas d'avantage du tout. Aucun filtre de coût ne peut sauver le FX, faute
d'edge sous-jacent. Cohérent avec le NO-GO FX de Codex.

---

## 3. Le coût varie de 4× à 10× à l'intérieur d'un même symbole

| symbole | coût médian | p10 | p90 | p90/p10 |
|---|---:|---:|---:|---:|
| BTC-JPY | 0,0729 | 0,0399 | 0,1644 | 4,1× |
| BRENT.fs | 0,0915 | 0,0423 | 0,1702 | 4,0× |
| AUS200 | 0,0814 | 0,0511 | 0,1204 | 2,4× |
| BNB-USD | 0,1526 | 0,0731 | 0,3237 | 4,4× |
| ADAUSD | 0,3967 | 0,1471 | 1,0584 | 7,2× |
| AVAX-USD | 0,2669 | 0,1143 | 1,1965 | 10,5× |

Un filtre de coût ne peut donc pas être un filtre d'univers : la dispersion
intra-symbole est du même ordre que la dispersion inter-symboles.

---

## 4. Porte de coût : seuil absolu, jugé hors échantillon

Seuil choisi sur la calibration, mesuré sur la **vérification seule**.
Tous symboles confondus, n = 15 745 trades en vérification.

| seuil `cost_r` | net calibration | net **VÉRIFICATION** | % conservé |
|---|---:|---:|---:|
| aucun | +0,0476 | **−0,1454** | 100 % |
| < 0,30 | +0,1157 | +0,0702 | 65,7 % |
| < 0,20 | +0,1458 | +0,1039 | 51,5 % |
| < 0,15 | +0,1754 | +0,1402 | 40,4 % |
| < 0,12 | +0,1877 | **+0,1620** | 32,9 % |
| < 0,10 | +0,2043 | +0,1646 | 27,4 % |
| < 0,08 | +0,2309 | +0,1863 | 20,0 % |
| < 0,06 | +0,2385 | **+0,2369** | 11,4 % |

Monotone dans les deux splits, sans inversion. À seuil serré, calibration et
vérification convergent (+0,2385 contre +0,2369) : signature d'un effet réel
et non d'un surajustement.

Sans porte, l'ensemble **perd** en vérification. Avec `cost_r < 0,12`, il gagne.

### Effet par symbole, vérification uniquement, seuil 0,12

| symbole | n vérif | % conservé | net avant | net après |
|---|---:|---:|---:|---:|
| BTC-JPY | 1890 | 81,1 % | +0,1456 | **+0,1976** |
| BRENT.fs | 1907 | 57,5 % | +0,1116 | **+0,2167** |
| BNB-USD | 2026 | 36,7 % | +0,1008 | **+0,2340** |
| AUS200 | 1668 | 96,8 % | +0,0546 | **+0,0736** |
| AAVE-USD | 2033 | 1,5 % | −0,1046 | +0,2935 |
| AVAX-USD | 2407 | 0,5 % | −0,5892 | +0,1908 |
| ADAUSD | 2076 | 0,0 % | −0,5332 | — |
| AUDCAD | 1738 | 8,3 % | −0,1928 | −0,0445 |

Les quatre candidats valides s'améliorent **tous**. La porte agit donc bien
par trade et pas seulement par élimination de symboles — et elle élimine les
symboles chers en prime, sans liste à maintenir.

AUDCAD reste négatif : la porte ne fabrique pas d'edge là où il n'y en a pas.

---

## 5. Pourquoi : le coût n'est pas une propriété de l'actif

Sur BTC-JPY, `cost_r × r_unit` vaut 4049,96 avec un **écart-type relatif de
0,0 %** sur 5651 trades. C'est une constante — le spread. Donc :

```
cost_r = spread / r_unit
```

`r_unit` est la distance de stop, **connue au moment de la décision**. La porte
est donc calculable avant l'entrée : aucun lookahead.

Vérification complémentaire : coût médian 0,0712 sur les trades gagnants
contre 0,0765 sur les perdants, soit 6,9 % d'écart — le coût ignore l'issue.

| | r_unit médian | cost_r médian |
|---|---:|---:|
| stops les plus serrés (décile 1) | 19 158 | 0,2114 |
| stops les plus larges (décile 10) | 122 396 | 0,0331 |

**Le coût est une propriété du stop, pas de l'actif.** `cost_r < 0,12` équivaut
à refuser tout trade dont le stop est plus serré que ~8,3 fois le spread.

---

## 6. Conséquences

1. **La porte de coût subsume le filtre d'univers.** Inutile de maintenir une
   liste d'actifs autorisés : les actifs chers ne franchissent presque jamais
   la porte (ADAUSD 0 %, AVAX 0,5 %, AAVE 1,5 %).

2. **Resserrer le SL renchérit mécaniquement le trade — mais ce n'est pas la
   même chose que « ne pas resserrer ».** Correction d'une sur-interprétation
   de la première version de ce rapport : les mesures de la section 5 comparent
   des trades *différents*, dont le stop naturel (issu de l'ATR) est plus ou
   moins large. Elles ne mesurent pas l'effet d'un resserrement appliqué à un
   trade donné. Ce qui reste établi est arithmétique : `cost_r = spread/r_unit`
   à 0,0 % près, donc diviser `r_unit` par α multiplie le coût par 1/α. Ce qui
   n'est **pas** établi, c'est le bilan net d'un resserrement, puisqu'un stop
   plus serré est aussi touché plus souvent — effet que ces données ne séparent
   pas.

   La lecture juste de la porte est donc : *ne pas prendre les trades dont le
   stop naturel est serré par rapport au spread* — un filtre de régime, pas une
   règle de dimensionnement du stop.

3. **Les 15 politiques d'exécution deviennent le levier principal.** Si le coût
   est ce qui sépare AVAX-USD (brut +0,3427, le meilleur de l'échantillon) d'un
   actif rentable, alors passer d'une exécution taker à maker déplace
   directement l'espérance nette. C'est la question que l'auditeur A/B de Codex
   est désormais en mesure de trancher.

4. **Le rejeu des 134 symboles restants garde son intérêt**, mais son objet
   change : il ne sert plus à classer les actifs, il sert à mesurer la
   distribution de `cost_r` et à valider le seuil sur un échantillon large.

---

## 6 bis. Contrôle : l'effet n'est pas une tendance temporelle

Sur BTC-JPY le prix est passé d'environ 4 M à 15 M de yens sur la période, et le
spread est constant (4050). `cost_r = 4050/r_unit` pourrait donc n'être qu'un
proxy de la date. `r_unit` ne corrèle qu'à **+0,58** avec `htf_atr_14_pct` :
le niveau de prix compte autant que la volatilité.

Test : à l'intérieur de chaque trimestre du split de vérification, comparer la
moitié la moins chère à la moitié la plus chère. Le niveau de prix y est
quasi constant, la tendance est donc neutralisée.

| symbole | écart médian | trimestres positifs |
|---|---:|---:|
| BRENT.fs | +0,1747 R | 6 / 7 |
| BNB-USD | +0,1551 R | 5 / 5 |
| BTC-JPY | +0,0640 R | 5 / 5 |
| AUS200 | +0,0210 R | 5 / 7 |

**21 cellules sur 24 sont positives.** L'effet de coût survit au contrôle
temporel. Il est en revanche faible sur AUS200 (+0,021 R médian), ce qui
suggère que le seuil devra être calibré par classe d'actif et non globalement.

---

## 7. Limites, à lire avant d'agir

- **8 symboles sur 149.** L'échantillon est petit et j'ai choisi les symboles
  (4 valides + 4 témoins). Le seuil doit être revalidé sur l'univers complet
  vers 07h20 demain.
- **Le seuil 0,12 n'est pas optimisé**, c'est un point de la grille. La courbe
  est monotone jusqu'à 0,06 ; plus le seuil serre, moins il reste de trades
  (11,4 % à 0,06). Le compromis espérance / effectif reste à arbitrer.
- **`cost_r` est un modèle**, pas un coût observé : `execution_sim` vaut
  `synthetic_l1`. Le spread réel varie dans la journée, ce que le modèle à
  spread constant ignore. La porte sera donc plus permissive en direct qu'ici.
- **Aucune décision de seuil ou de quorum ne relève de moi.** Cette porte est
  un changement de seuil : elle demande l'arbitrage de Prime.

---

# ADDENDUM — 22/08 : l'affinage M5 de V12 mis à l'épreuve

Suite au « go test » de Florent. Le module V12 `fusion/entry_refine.py` a été
porté dans `titanium/features/entry_refine.py` avec le plancher de resserrement
neutralisé (`PLANCHER_SL_DEFAUT = 1.0`), puis ses deux autres parties ont été
mesurées. **Le module n'est câblé à rien.**

## Méthode

Mesure hors rejeu : les artefacts bruts portent l'issue de chaque trade, les
barres M5 sont archivées (100 000 barres, couvrant exactement le split de
vérification). L'affinage est donc rejoué a posteriori sur chaque trade —
sans relancer le moteur, qui coûte 49 minutes par symbole.

Anti-lookahead : seules les barres M5 ouvertes **strictement avant**
`decision_at` sont fournies. Couverture : 7485 trades sur 4 symboles, zéro hors
couverture, zéro fenêtre trop courte.

Outil : `tools/mesure_affinage_entree.py`.

## Résultat — split de vérification

| | n | brut R | net R | écart contre le complément |
|---|---:|---:|---:|---|
| **tous** | 7485 | +0,2161 | +0,1046 | — |
| timing confirmé (BOS / rejet) | 2002 | +0,2373 | +0,1300 | **+0,0347 ± 0,0295 (+1,2σ)** |
| dans une FVG M5 ouverte | 801 | +0,1724 | +0,0498 | **−0,0614 ± 0,0411 (−1,5σ)** |

Par symbole :

| symbole | timing | zone FVG |
|---|---:|---:|
| BTC-JPY | −0,0481 (−0,8σ) | −0,0854 (−1,1σ) |
| BRENT.fs | +0,0687 (+1,2σ) | −0,0748 (−0,9σ) |
| BNB-USD | +0,0246 (+0,4σ) | −0,0618 (−0,8σ) |
| AUS200 | +0,0999 (+1,6σ) | −0,0253 (−0,2σ) |

## Verdict

**La zone FVG est nuisible comme filtre : 4 symboles sur 4, même signe.** Aucun
n'est significatif isolément, mais la concordance l'est : sous l'hypothèse nulle
d'absence d'effet, quatre signes identiques ont une probabilité de 1/16.

**Le timing n'est pas établi.** +1,2σ en agrégé, et le seul symbole où il échoue
est BTC-JPY, le meilleur candidat du lot. Trois signes positifs sur quatre ne
suffisent pas à porter une décision.

Cela contredit ce que j'avais proposé — porter les parties 2 et 3 en gardant
seulement le resserrement de côté. La mesure dit que la partie 3 dégrade et que
la partie 2 n'est pas démontrée. **Aucune des trois parties ne mérite d'être
câblée en l'état.**

## La limite qui compte

La zone FVG a été évaluée comme **filtre**, pas comme **repositionnement**. Son
rôle en V12 est de déplacer le prix d'entrée dans la FVG, ce qui change l'issue
du trade — un effet qu'aucune mesure a posteriori ne peut capter, puisqu'il
faudrait rejouer avec des entrées différentes.

Donc : « la zone dégrade quand on l'utilise pour trier » est établi ; « la zone
dégrade quand on l'utilise pour replacer l'entrée » ne l'est pas. Trancher
demande un vrai rejeu A/B, à programmer après la fin du lot en cours.

## Ce qui reste acquis

Le module est porté, testé (23 tests) et inerte. Il documente au passage un
piège : `_swings` retient un extremum par égalité, donc sur un palier plat M5
l'ancrage tombe sur le palier et non sur le vrai creux — bénin au plancher 1.0,
piégeux dès qu'on le descend, et les paliers plats abondent précisément quand le
spread est déjà mauvais.
