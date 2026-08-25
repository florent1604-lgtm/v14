# P1a — diagnostic descriptif de la cohorte servie

Décision collective offset 663 : le STOP hors ligne est levé **pour P1a
uniquement**. P1b causal sur les 690 reste BLOCK, faute de reconstructibilité
des 315 expirations.

**Descriptif seulement.** Aucune causalité affirmée, aucune promotion, aucun
changement de seuil. L'ordre suivi est celui imposé par Hermes à l'offset 662 :
contrôles → arithmétique → `exit_reason` → famille/régime → axes partiels.

Époque : corpus `051f50ad…`, 147/147 artefacts, arbre `0901ca68…`, écart publié
et permis.

---

## 1. Contrôles

```
limites closes            373
jointes (ticket exact)    373      perte de jointure 0
sorties au stop initial   191      51,2 %
```

La clé de jointure de `docs/CARTOGRAPHIE_JOINTURE_20260825.md` tient sur la
cohorte complète, une clôture de plus qu'au moment de la cartographie.

## 2. Arithmétique — le chiffre de Prime est reproduit

| | |
|---|---:|
| `pnl_r` moyen, économie d'entrée incluse | **−0,0428 R** |
| économie d'entrée moyenne | +0,08762 R |
| cumul de l'économie | **+32,68 R** |
| `pnl_r` moyen **sans** l'économie | **−0,1304 R** |
| médiane | −0,4788 R |
| taux de réussite | 48,79 % |

Prime annonçait −0,0431 et −0,1307 sur 372 clôtures. L'écart tient à la
clôture supplémentaire. **Réconciliation acquise.**

L'entrée passive a bien rapporté 32,68 R sur la période et réduit le déficit
des deux tiers.

---

## 3. Par motif de sortie — la gestion n'est pas le problème

| motif | n | part | `pnl_r` moyen | médiane | réussite | contribution |
|---|---:|---:|---:|---:|---:|---:|
| `trailing` | 115 | 30,8 % | **+1,1922** | +0,9176 | 100,0 % | **+137,10 R** |
| `breakeven` | 63 | 16,9 % | +0,1405 | +0,0589 | 93,7 % | +8,85 R |
| `init` | 195 | 52,3 % | **−0,8304** | −0,9971 | 4,1 % | **−161,93 R** |
| **total** | 373 | | −0,0428 | | | −15,97 R |

Le breakeven et le trailing produisent ensemble **+145,95 R**. La gestion
dynamique du stop fait son travail — c'est même la seule chose qui fonctionne.

**La totalité du déficit tient dans `init`** : 52,3 % des trades servis meurent
au stop initial, pour −161,93 R.

### Un avertissement sur cette partition

Les taux de réussite de 100 % et 93,7 % ne sont **pas des résultats** : ils sont
définitionnels. Un trade qui atteint +0,8 R déclenche le breakeven, donc change
de motif de sortie. Les trois seaux sont ordonnés par ce que le trade a atteint,
pas par une décision indépendante.

Ce qui reste informatif est la **répartition** : plus d'un trade servi sur deux
n'atteint jamais +0,8 R.

---

## 4. La cohorte `init` — les entrées bougent à peine

Excursion favorable maximale des 195 trades morts au stop initial :

```
MFE > 0,0 R    144 / 195    73,8 %
MFE > 0,2 R     84 / 195    43,1 %
MFE > 0,5 R     32 / 195    16,4 %
MFE médiane    0,148 R      moyenne 0,223 R
```

Les entrées ne sont pas du bruit pur : **73,8 % partent au moins un peu en
faveur**. Mais le mouvement est minuscule — une médiane de 0,148 R contre un
stop à 1 R. Le marché reconnaît le setup, puis se retourne avant qu'il ne paie.

Trente-deux trades ont atteint +0,5 R, soit les deux tiers du chemin vers le
breakeven, et ont tout rendu. C'est une observation, pas une recommandation de
seuil : abaisser le déclenchement du breakeven en sauverait certains et
couperait des `trailing` avant leur +1,19 R moyen. Le solde est inconnu et
relève de P3.

---

## 5. Famille et régime — aucun pouvoir de séparation

| famille | n | `pnl_r` moyen | part `init` | contribution |
|---|---:|---:|---:|---:|
| `continuation` | 354 | −0,0363 | 52,3 % | −12,84 R |
| `reversal` | 19 | −0,1650 | 52,6 % | −3,14 R |

Les deux familles ont **la même proportion d'échecs au stop initial**. L'axe ne
discrimine pas. L'effectif `reversal` est trop faible pour conclure quoi que ce
soit d'autre.

---

## 6. Axes restants

### Classe d'actif — la seule séparation nette

| classe | n | `pnl_r` moyen | part `init` | contribution |
|---|---:|---:|---:|---:|
| métaux | 40 | +0,1588 | 40,0 % | +6,35 R |
| énergie | 53 | +0,1192 | 41,5 % | +6,32 R |
| crypto | 37 | +0,0434 | 56,8 % | +1,60 R |
| FX | 94 | −0,1164 | 54,3 % | −10,95 R |
| **indices** | 141 | **−0,1521** | **58,2 %** | **−21,44 R** |

Les indices portent la plus grosse perte, sur le plus gros effectif, avec la
plus forte proportion d'échecs au stop initial. Le FX est désormais suspendu :
ses −10,95 R sont historiques.

### Piliers — le résultat contre-intuitif

| quorum | n | `pnl_r` moyen | part `init` | contribution |
|---|---:|---:|---:|---:|
| 2 piliers | 316 | **+0,0075** | 50,0 % | +2,38 R |
| 3 piliers | 57 | **−0,3220** | **64,9 %** | −18,36 R |

**Le seau à trois piliers perd, celui à deux est à l'équilibre.** Et il échoue
au stop initial dans 64,9 % des cas contre 50,0 %.

Cela contredit la prémisse du dimensionnement modulé par la confiance
(`titanium/confiance.py`) : plus de piliers vaut plus de risque. Ici la strate
la mieux dotée est la seule qui perd, donc la modulation **amplifie la perte**.

Réserve ferme : **n = 57**. C'est descriptif, ce n'est pas une preuve, et
CLAUDE.md qualifiait déjà cette modulation de « pari, pas une optimisation
démontrée ». Le résultat justifie de la tester, pas de la renverser.

### Unité de temps

| | n | `pnl_r` moyen | part `init` |
|---|---:|---:|---:|
| M15 | 292 | −0,0230 | 49,7 % |
| H1 | 74 | −0,0837 | 59,5 % |

### Spread à l'entrée — aucune relation monotone

| tranche (R) | n | `pnl_r` moyen | part `init` |
|---|---:|---:|---:|
| < 0,05 | 111 | −0,0628 | 50,5 % |
| 0,05–0,10 | 220 | −0,0169 | 51,8 % |
| 0,10–0,15 | 42 | −0,1259 | 59,5 % |

Pas de monotonie. Cohérent avec une porte de coût déjà active à 0,125 : les
trades chers ont été écartés en amont, il ne reste que la partie plate de la
relation.

**Subtilité de jointure à retenir** : `spread_r` n'est pas porté par
l'événement `closed`. Il faut joindre l'événement `placed` par `order_ticket`
— 373/373 y parviennent. Un consommateur qui lit `closed` seul conclurait que
l'axe n'existe pas ; il n'est qu'ailleurs.

---

## 7. Ce que ce diagnostic établit, et ce qu'il n'établit pas

**Établi, descriptivement :**

- Le déficit ne vient pas de la gestion du stop, qui contribue +145,95 R.
- Il vient de 52,3 % de trades qui meurent au stop initial après un mouvement
  favorable médian de 0,148 R.
- Les indices concentrent la perte ; métaux et énergie sont positifs.
- La strate à trois piliers est la seule nettement perdante, sur 57 trades.

**Non établi :**

- Aucune causalité. Un axe qui sépare n'explique pas.
- Aucun contrefactuel : le sort d'une entrée marché aux mêmes instants reste
  inconnu, et le coût d'opportunité des 315 expirations garde un signe inconnu.
- Rien sur la contre-tendance : l'axe est à 0/373, le correctif `4c2ab54` est
  trop récent pour qu'un trade clos le porte.
- Aucune recommandation de seuil. Les observations sur le breakeven à 0,5 R et
  sur la modulation par piliers sont des hypothèses pour P3.

---

## 8. Reproduire

```python
norm = lambda t: str(t).split(":")[-1]
# closed → trades/excursions par position_ticket
# placed → closed par order_ticket, pour spread_r
```

Sources : `results/limit_lifecycle.ndjson`, `results/trades.ndjson`,
`results/excursions.ndjson`.
