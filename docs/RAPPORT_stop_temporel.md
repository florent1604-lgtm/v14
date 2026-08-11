# Stop temporel — mesure et verdict

**Date** : 07/08/2026 · **Données** : MT5 Axi, M15/H4, 5000 barres LTF, spread
réel du courtier appliqué à l'entrée **et** à la sortie · **Actifs** : XAUUSD,
ETHUSD (espérance positive) · EURUSD, AUDUSD (contrôles négatifs).

---

## Verdict : REJET

**Ne pas activer.** `stop_temporel` reste à `None` en production comme en
backtest.

Sur les deux actifs qui gagnent de l'argent, le stop temporel en **détruit** :
XAUUSD passe de +0.2464 R à +0.2246 R par trade, ETHUSD de +0.1351 R à
+0.0908 R (couple (10, 0.5), rejeu système complet). Il n'« améliore » que
l'actif le plus perdant du lot, AUDUSD, qu'il fait remonter de −0.1069 R à
−0.0787 R — c'est-à-dire qu'il perd moins vite sur un actif qu'il ne faut pas
trader.

Le chiffre qui résume tout, agrégé sur les 4 actifs :

| | trades | résultat total | espérance |
|---|---:|---:|---:|
| référence (stop temporel désactivé) | 842 | **+56.97 R** | +0.0677 R |
| stop temporel (10 barres, 0.5 R) | 901 | **+49.37 R** | +0.0548 R |
| stop temporel (8 barres, 0.5 R) | 922 | **+41.53 R** | +0.0450 R |
| stop temporel (20 barres, 0.75 R) | 864 | +57.24 R | +0.0663 R |

**59 trades de plus, 7.6 R de moins.** Plus de risque pris pour moins d'argent
gagné : c'est le contraire de ce qu'on cherchait.

L'hypothèse de départ était descriptivement juste et prescriptivement inutile.
Les trois raisons mécaniques sont détaillées plus bas, et elles sont plus
instructives que le verdict lui-même.

---

## 1. Ce qui a été ajouté au code

`titanium/backtest.py` — `_simuler_sortie()` et `rejouer()` acceptent
`stop_temporel: tuple[int, float] | None`, soit `(barres_max, seuil_r)`.

- **`None` par défaut.** Vérifié par test : l'argument absent et l'argument
  explicitement `None` rendent des sorties identiques, motif compris. Aucun
  chiffre déjà publié ne bouge.
- **Ordre dans la barre** : `SL` → `TP` → stop temporel → gestion dynamique.
  L'inverser transformerait des pertes pleines au stop en sorties tièdes à la
  clôture et fabriquerait un gain qui n'a jamais existé. Deux tests bloquent
  cette régression, un par sens.
- **Le critère est le pic**, pas le prix courant : « n'a pas atteint +0.5 R » se
  lit « n'y est jamais monté ». Un trade monté à +0.9 R puis redescendu relève
  du trailing, pas du couperet.
- **Piège trouvé à l'écriture** : la MFE rapportée est plancherée à 0
  (`pic = max(pic, fav)` partant de `0.0`), donc `pic < 0.0` est structurellement
  impossible et `seuil_r = 0.0` aurait été un **no-op silencieux** — cinq
  combinaisons du balayage sur vingt rendues inertes sans que rien ne le signale.
  Un pic non plancheré, dédié au seul critère temporel, corrige cela sans
  toucher à `mfe_r` (test dédié : la MFE rapportée reste ≥ 0).

`tests/test_stop_temporel.py` — **18 tests**, tous verts. Suite complète verte
après l'ajout (1110 → 1128 au moment de la mesure ; le total a continué de
monter, d'autres briques étant livrées en parallèle).

---

## 2. Méthode

**80 combinaisons testées** : 5 valeurs de `barres` {5, 8, 10, 15, 20} × 4
valeurs de `seuil` {0.0, 0.25, 0.5, 0.75} × 4 actifs. C'est ce nombre qui fixe la
sévérité de Benjamini-Hochberg, appliqué sur les 80 p-values à FDR 10 % via
`titanium/analysis/discriminants.py::_benjamini_hochberg`.

Deux mesures distinctes, et l'écart entre elles est un résultat en soi :

**a) Comparaison appariée (entrées figées).** Un rejeu de référence par actif
fixe l'ensemble des entrées ; chaque couple ne rejoue que la **sortie** de ces
mêmes trades. Cela isole l'effet de la règle de sortie de tout le reste.
Contrôle d'appariement : rejouer la sortie **sans** stop reproduit la référence à
`0.0001 R` près (arrondi seul). p-value par test de permutation à inversion de
signe sur les écarts appariés, 2000 permutations, graine fixe.

**b) Rejeu système complet.** L'appariement fige les entrées ; le vrai système
ne le fait pas. Couper un trade à la barre 10 libère le moteur dix barres plus
tôt, et il entrera ailleurs. Les couples finalistes ont donc été rejoués
entièrement, cascade d'entrées comprise. **C'est le seul chiffre honnête**, et
il est systématiquement moins bon que l'apparié (§ 6).

Walk-forward : `decouper_walk_forward()`, 3 segments consécutifs. Aucune
conclusion n'est tirée de l'échantillon complet.

---

## 3. Avant / après par actif — comparaison appariée

Référence (stop temporel désactivé), qui reproduit exactement le constat de
l'audit :

| actif | trades | espérance | PF | winrate | segments WF |
|---|---:|---:|---:|---:|---|
| XAUUSD | 207 | **+0.2464 R** | 1.704 | 65.2 % | +0.290 / +0.213 / +0.236 |
| ETHUSD | 239 | **+0.1375 R** | 1.336 | 46.9 % | +0.144 / +0.314 / −0.041 |
| EURUSD | 164 | −0.0078 R | 0.983 | 49.4 % | +0.297 / −0.197 / −0.119 |
| AUDUSD | 233 | −0.1069 R | 0.784 | 38.6 % | −0.068 / −0.050 / −0.200 |

Δ espérance (R par trade) pour chaque couple. **Vert = amélioration** :

| barres · seuil | XAUUSD | ETHUSD | EURUSD | AUDUSD |
|---|---:|---:|---:|---:|
| 5 · 0.00 | 0.0000 | −0.0102 | −0.0095 | +0.0007 |
| 5 · 0.25 | −0.0367 | +0.0038 | +0.0103 | −0.0124 |
| 5 · 0.50 | **−0.0521** | −0.0045 | +0.0445 | +0.0122 |
| 5 · 0.75 | **−0.0547** | **−0.0695** | −0.0110 | +0.0575 |
| 8 · 0.00 | 0.0000 | +0.0009 | −0.0142 | 0.0000 |
| 8 · 0.25 | −0.0129 | +0.0081 | −0.0137 | +0.0172 |
| 8 · 0.50 | −0.0111 | +0.0030 | +0.0412 | +0.0432 |
| 8 · 0.75 | −0.0176 | −0.0249 | +0.0164 | +0.0549 |
| 10 · 0.00 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 10 · 0.25 | −0.0147 | +0.0011 | −0.0054 | +0.0113 |
| 10 · 0.50 | −0.0066 | +0.0031 | +0.0246 | **+0.0490** |
| 10 · 0.75 | −0.0209 | −0.0130 | +0.0166 | **+0.0579** |
| 15 · 0.00 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 15 · 0.25 | 0.0000 | +0.0032 | −0.0076 | +0.0027 |
| 15 · 0.50 | +0.0077 | +0.0055 | +0.0222 | +0.0079 |
| 15 · 0.75 | −0.0042 | −0.0082 | +0.0108 | +0.0115 |
| 20 · 0.00 | 0.0000 | 0.0000 | 0.0000 | 0.0000 |
| 20 · 0.25 | 0.0000 | +0.0019 | +0.0030 | 0.0000 |
| 20 · 0.50 | +0.0047 | +0.0042 | +0.0260 | +0.0097 |
| 20 · 0.75 | +0.0072 | +0.0116 | +0.0134 | +0.0144 |

**La structure de ce tableau est le résultat principal.** L'effet du stop
temporel est une fonction décroissante de l'edge de l'actif :

| actif | espérance de référence | meilleur Δ | pire Δ |
|---|---:|---:|---:|
| XAUUSD | +0.2464 R | +0.0077 | **−0.0547** |
| ETHUSD | +0.1375 R | +0.0116 | **−0.0695** |
| EURUSD | −0.0078 R | +0.0445 | −0.0110 |
| AUDUSD | −0.1069 R | **+0.0579** | −0.0124 |

Une règle qui aide d'autant plus que l'actif perd d'autant plus n'améliore pas
la **sélection** : elle réduit l'**exposition**. Sur une espérance négative,
n'importe quelle règle qui ferme les positions plus tôt fait gagner de l'argent
— y compris tirer à pile ou face. Ce n'est pas un edge, c'est une soustraction.

---

## 4. Stabilité walk-forward et correction pour tests multiples

Critère exigé : positif sur **les 3 segments**, pas en moyenne.

Onze combinaisons sur 80 passent le test naïf « Δ > 0 sur les 3 segments ». Six
d'entre elles sont des faux positifs de rounding : le segment concerné ne
contient **aucun trade coupé** et son Δ vaut 2×10⁻⁶ — du bruit d'arrondi, pas un
effet. En exigeant qu'au moins un trade soit effectivement coupé dans chaque
segment, il reste **5 combinaisons sur 80** :

| actif | combo | coupés / segment | Δ / segment | p brut | p corrigé BH |
|---|---|---|---|---:|---:|
| AUDUSD | (8, 0.25) | 2 / 4 / 3 | +0.021 / +0.010 / +0.021 | 0.0850 | 0.850 |
| AUDUSD | (8, 0.50) | 5 / 9 / 9 | +0.031 / +0.033 / +0.065 | 0.0040 | 0.160 |
| AUDUSD | (8, 0.75) | 15 / 13 / 17 | +0.083 / +0.010 / +0.072 | 0.0250 | 0.500 |
| AUDUSD | (10, 0.50) | 2 / 3 / 7 | +0.028 / +0.050 / +0.068 | 0.0005 | **0.040** ✱ |
| AUDUSD | (10, 0.75) | 10 / 7 / 12 | +0.048 / +0.035 / +0.090 | 0.0270 | 0.432 |

**Les cinq sont sur AUDUSD.** Zéro sur XAUUSD, zéro sur ETHUSD, zéro sur EURUSD.

Une seule combinaison survit à Benjamini-Hochberg à FDR 10 % sur 80 tests :
**AUDUSD (10, 0.50)**, p corrigé 0.040. Et cette unique découverte fait passer
AUDUSD de PF 0.784 à PF 0.871 — d'un actif très perdant à un actif encore
perdant. Elle ne rend rien tradable ; elle confirme seulement qu'AUDUSD ne
devrait pas être tradé.

Sept combinaisons atteignent p brut < 0.05 : six sur AUDUSD, une sur EURUSD,
**aucune sur XAUUSD ni ETHUSD**. Sans correction on en aurait retenu sept au lieu
d'une — mais le point important n'est pas le nombre : c'est que la significativité
est confinée aux deux actifs sans edge. Un balayage lu sans les contrôles négatifs
aurait produit sept « découvertes » et une règle nuisible.

---

## 5. Les deux effets, séparément

C'est la question centrale : **combien de perdants sauvés, combien de gagnants
amputés ?** Comparaison appariée, entrées figées, couple phare (10 barres,
0.5 R) :

| actif | coupés | perdants sauvés | R récupérés | par trade | gagnants amputés | R perdus | par trade | net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XAUUSD | 12 / 207 | 7 | +5.28 | +0.75 | 5 | −6.64 | **−1.33** | **−1.36** |
| ETHUSD | 22 / 239 | 15 | +7.81 | +0.52 | 7 | −7.07 | −1.01 | +0.75 |
| EURUSD | 16 / 164 | 12 | +9.17 | +0.76 | 4 | −5.13 | −1.28 | +4.03 |
| AUDUSD | 12 / 233 | 12 | +11.42 | +0.95 | 0 | 0.00 | — | +11.42 |
| **TOTAL** | **62 / 843** | **46** | **+33.68** | **+0.73** | **16** | **−18.84** | **−1.18** | **+14.84** |

Lecture :

- **Un perdant sauvé rapporte +0.73 R** en moyenne : il serait allé au stop
  (−1.00 R en moyenne), il sort à −0.27 R.
- **Un gagnant amputé coûte −1.18 R** : il aurait fini à **+1.06 R** en moyenne,
  il sort à −0.11 R.
- **Un gagnant amputé coûte donc 1.61× ce qu'un perdant sauvé rapporte.** Le
  seuil de rentabilité de la règle est de **61.7 % de perdants dans la
  population coupée**. Observé : 74.2 % globalement, mais seulement **58 % sur
  XAUUSD** — d'où le net négatif sur le meilleur actif.

Le même calcul sur les autres réglages, pour montrer que l'arithmétique est
structurelle et pas propre à (10, 0.5) :

| combo | coupés | % perdants dans les coupés | seuil de rentabilité | net total |
|---|---:|---:|---:|---:|
| (5, 0.50) | 182 | 61.0 % | 61.5 % | **−1.72 R** |
| (8, 0.50) | 91 | 68.1 % | 58.0 % | +15.27 R |
| (10, 0.50) | 62 | 74.2 % | 61.7 % | +14.84 R |
| (10, 0.75) | 122 | 59.0 % | 54.7 % | +8.78 R |
| (15, 0.50) | 23 | 82.6 % | 60.0 % | +8.39 R |
| (20, 0.75) | 28 | 78.6 % | 52.9 % | +9.82 R |

La marge entre pureté observée et seuil de rentabilité n'excède jamais
~22 points, et tombe à −0.5 point à (5, 0.50). C'est une règle qui vit sur le
fil, et le fil penche du mauvais côté dès qu'on la resserre.

Sur XAUUSD précisément, la pureté de la population coupée est de **58 % à
(10, 0.5) et 46 % à (10, 0.75)** — à 0.75 R le couperet frappe les gagnants
**plus souvent** qu'au hasard.

---

## 6. Pourquoi l'hypothèse échoue — trois mécanismes

L'audit avait raison sur les faits. La référence les reproduit exactement :

| actif | gagnants : barres méd. / MFE méd. | perdants : barres méd. / MFE méd. |
|---|---|---|
| XAUUSD | 7 / **1.59 R** | 4 / **0.32 R** |
| ETHUSD | 7 / **1.85 R** | 5 / **0.48 R** |
| EURUSD | 6 / **1.53 R** | 4 / **0.32 R** |
| AUDUSD | 6 / **1.65 R** | 4 / **0.46 R** |

La séparation d'un facteur 3 à 4 est réelle. Elle n'est simplement pas
exploitable, pour trois raisons cumulatives.

### a) La population visée est presque vide

Le stop temporel ne peut agir que sur les trades **encore ouverts** à la
barre N. Or les perdants meurent vite aussi — ils touchent le stop.

Part des trades encore ouverts à la barre N, et part que la règle (N, 0.5)
coupe effectivement :

| actif | N=5 | N=8 | N=10 | N=15 | N=20 |
|---|---|---|---|---|---|
| XAUUSD | 63 % / **19 %** | 39 % / 10 % | 29 % / **6 %** | 11 % / 1 % | 6 % / 1 % |
| ETHUSD | 59 % / **23 %** | 36 % / 12 % | 26 % / **9 %** | 15 % / 4 % | 11 % / 2 % |
| EURUSD | 54 % / **22 %** | 30 % / 12 % | 26 % / **10 %** | 19 % / 5 % | 10 % / 3 % |
| AUDUSD | 54 % / **21 %** | 36 % / 10 % | 29 % / **5 %** | 13 % / 1 % | 6 % / 1 % |

À la barre 10, la règle ne touche que **5 à 10 % des trades**, représentant
**6 à 11 % du P&L en valeur absolue**. Même parfaite, elle ne pourrait pas
déplacer l'aiguille beaucoup. La MFE médiane des perdants est basse parce qu'ils
meurent tôt, pas parce qu'ils traînent : le couperet arrive après l'enterrement.

### b) La discrimination existe mais reste sous le seuil de rentabilité

Ce qui reste ouvert à la barre 10 sans avoir touché +0.5 R contient bien une
majorité de perdants (58 à 100 % selon l'actif), mais l'asymétrie de coût exige
62 %. Sur l'actif le plus rentable, on est en dessous.

Et cette majorité est acquise sur des effectifs minuscules : les 100 % de pureté
d'AUDUSD à (10, 0.5), c'est 12 trades sur 12. Les 5 combinaisons stables
reposent toutes sur des segments de 2 à 17 trades coupés.

### c) La cascade d'entrées annule le reste — et c'est l'effet le plus grand

Un trade coupé à la barre 10 rend la main dix barres plus tôt, et le moteur
entre à nouveau. Ces entrées supplémentaires sont **moins bonnes que la
moyenne** : elles sont produites par les barres que le système évitait
justement parce qu'il était occupé.

Rejeu système complet, couple (10, 0.5) :

| actif | trades | espérance | PF | Δ segments WF |
|---|---|---|---|---|
| XAUUSD | 207 → **218** | +0.2464 → **+0.2246** | 1.704 → 1.674 | −0.037 / +0.017 / −0.045 |
| ETHUSD | 238 → **265** | +0.1351 → **+0.0908** | 1.329 → 1.221 | −0.103 / −0.075 / +0.043 |
| EURUSD | 164 → **177** | −0.0078 → **−0.0263** | 0.983 → 0.939 | −0.051 / +0.011 / −0.019 |
| AUDUSD | 233 → **241** | −0.1069 → −0.0787 | 0.784 → 0.828 | −0.010 / +0.075 / +0.019 |

L'écart avec la mesure appariée est décisif :

| actif | Δ apparié (entrées figées) | Δ système complet | verdict |
|---|---:|---:|---|
| XAUUSD | −0.0066 | **−0.0218** | dégradé, aggravé |
| ETHUSD | +0.0031 | **−0.0444** | **change de signe** |
| EURUSD | +0.0246 | **−0.0185** | **change de signe** |
| AUDUSD | +0.0490 | +0.0282 | positif, divisé par 1.7 |

**Deux des quatre actifs changent de signe** une fois les entrées libérées, dont
EURUSD, qui était le deuxième meilleur candidat. La mesure appariée flatte
systématiquement la règle.

Et l'unique survivante de Benjamini-Hochberg — AUDUSD (10, 0.50) — **perd sa
stabilité walk-forward** dans le rejeu complet : son premier segment passe à
−0.010. La seule découverte statistiquement significative du balayage ne survit
pas à la mesure honnête.

---

## 7. Recommandation

**Rejet. Le paramètre reste livré et désactivé (`None`).**

Il est conservé dans le code parce qu'il est testé, gratuit à l'arrêt, et qu'il
documente une piste fermée : la prochaine personne qui aura cette idée trouvera
la mesure au lieu de la refaire.

Ce qu'il ne faut pas faire :

- **Ne pas retenir (20, 0.75)** malgré son Δ agrégé quasi nul (+0.27 R sur 842
  trades). Il ne coupe que 28 trades sur 843 ; c'est du bruit habillé en
  neutralité, et il dégrade quand même XAUUSD, ETHUSD et EURUSD individuellement.
- **Ne pas retenir AUDUSD (10, 0.50)** au motif qu'il passe BH. Il améliore un
  PF de 0.784 à 0.828 : la bonne décision sur AUDUSD n'est pas un stop temporel,
  c'est de ne pas le trader. La sélectivité par actif est déjà le levier
  identifié dans `CLAUDE.md` — ce résultat le confirme une fois de plus, il ne
  l'enrichit pas.
- **Ne pas relancer un balayage plus fin** (barres 6, 7, 9, seuils 0.4, 0.6…).
  80 combinaisons ont déjà produit une seule survivante, sur le mauvais actif.
  Densifier la grille augmente le dénominateur de Benjamini-Hochberg sans
  changer l'arithmétique du § 5.

Ce que ce travail apprend de réutilisable, en revanche :

1. **La MFE des perdants est basse parce qu'ils meurent vite**, pas parce qu'ils
   stagnent. Toute future règle indexée sur la durée butera sur le même mur :
   à la barre 10, 71 à 74 % des trades sont déjà clos.
2. **Une règle qui aide d'autant plus que l'actif perd est une réduction
   d'exposition, pas un edge.** Ce test — comparer l'effet à travers des actifs
   d'edge connu et opposé — mérite d'être appliqué systématiquement à toute
   nouvelle règle de sortie. Il a coûté deux contrôles négatifs et il a tranché.
3. **Toute mesure à entrées figées flatte une règle qui raccourcit les trades.**
   Le rejeu système complet n'est pas une formalité de confirmation : c'est lui
   qui a fait changer de signe la moitié des candidats.

---

## Annexe — reproduire

```
.venv\Scripts\python.exe -m pytest tests/test_stop_temporel.py -q     # 18 tests
```

Paramètre : `rejouer(..., stop_temporel=(10, 0.5))` ou
`_simuler_sortie(..., stop_temporel=(10, 0.5))`. `None` = désactivé (défaut).

Réserves de mesure, énoncées :

- 5000 barres M15 ≈ 5 mois de données par actif. Trois segments walk-forward de
  ~55 à 80 trades chacun. C'est peu, et c'est précisément pourquoi la correction
  pour tests multiples n'est pas négociable ici.
- Les données ont été relues entre le balayage et le rejeu de confirmation :
  ETHUSD compte 239 trades dans le premier et 238 dans le second (une barre
  M15 close entre les deux appels MT5). Sans effet sur les conclusions.
- Coûts : spread réel du courtier uniquement (XAUUSD 0.160, ETHUSD 1.250,
  EURUSD/AUDUSD 0.00007). **Le swap n'est pas modélisé** — il pénaliserait les
  trades longs, donc jouerait *en faveur* du stop temporel. Même avec ce vent
  dans le dos non compté, la règle perd.
