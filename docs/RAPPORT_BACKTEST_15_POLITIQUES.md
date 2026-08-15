# Backtest des 15 politiques d'exécution

**Claude · 15/08/2026** · source `results/execution_matrix_full/execution_matrix.csv`,
run `9cb2fb7c6175484c`, 12 960 lignes, mode dry-run.

---

## Ce que ce rapport ajoute au précédent

Le rapport du 14/08 (`docs/RAPPORT_EXECUTION_MATRIX_V14.md`) donnait un
classement par score composite. Il manquait quatre choses, toutes présentes dans
les données mais jamais sorties :

1. **la comparaison appariée** contre `market` — les 864 scénarios sont
   identiques d'une politique à l'autre et portent le même alpha, donc l'écart
   scénario par scénario isole le delta d'exécution du bruit de tirage ;
2. **le tiers hors échantillon**, annoncé « rapporté séparément » et jamais
   publié ;
3. **la conditionnalité par régime** — spread, volatilité, taille, liquidité ;
4. **le fait que cinq politiques sur quinze ne sont pas distinguables** dans ce
   simulateur.

Aucune donnée n'a été régénérée : tout vient du CSV existant.

---

## 1. Résultat brut par politique

864 scénarios chacune. `PF` est calculé sur les résultats de scénario
(somme des positifs / somme des négatifs), pas sur des trades individuels.
`IS` = implementation shortfall en points de base ; **négatif = meilleur que le
prix d'arrivée**.

| # | politique | net moyen | ±se | PF | fill | rejet | coût bps | IS bps | DD |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | market | 1,0459 | 0,0632 | 6,20 | 100,0 % | 0,0 % | 13,069 | +7,603 | 0,202 |
| 2 | limit passive | 1,1955 | 0,0704 | 14,66 | 80,1 % | 0,0 % | 0,461 | −3,396 | 0,088 |
| 3 | post-only | 1,1793 | 0,0684 | 20,79 | 74,5 % | 5,8 % | 0,088 | −2,853 | 0,060 |
| 4 | IOC | 0,4180 | 0,0415 | 4,95 | 52,1 % | 0,0 % | 5,267 | +1,530 | 0,106 |
| 5 | FOK | 0,3444 | 0,0403 | 5,13 | 47,3 % | 0,0 % | 3,904 | +1,095 | 0,083 |
| 6 | cancel/replace | 1,2145 | 0,0701 | 14,70 | 99,9 % | 0,0 % | 0,766 | −4,140 | 0,089 |
| 7 | pegged | 1,2827 | 0,0712 | 12,09 | 89,2 % | 0,0 % | 1,813 | −0,591 | 0,116 |
| 8 | iceberg | 1,1941 | 0,0704 | 14,54 | 80,1 % | 0,0 % | 0,484 | −3,386 | 0,088 |
| 9 | TWAP | 0,2308 | 0,0165 | 5,62 | 36,1 % | 0,0 % | 0,833 | −4,450 | 0,050 |
| 10 | VWAP | 0,6259 | 0,0415 | 9,19 | 58,2 % | 0,0 % | 0,598 | −3,811 | 0,077 |
| 11 | POV | 0,2594 | 0,0110 | 13,96 | 43,4 % | 0,0 % | 0,484 | −3,386 | 0,020 |
| 12 | adaptative maker→taker | 1,1731 | 0,0687 | 17,38 | 78,9 % | 2,4 % | 0,300 | −3,452 | 0,072 |
| 13 | market making | 0,0392 | 0,0077 | 1,96 | 51,4 % | 13,8 % | 0,000 | −3,537 | 0,266 |
| 14 | multi-jambes simultané | −0,9579 | 0,0547 | 0,05 | 90,5 % | 0,0 % | 11,957 | −0,126 | 1,511 |
| 15 | maker puis hedge taker | 0,9900 | 0,0611 | 16,06 | 74,5 % | 5,7 % | 1,564 | −13,105 | 0,249 |

---

## 2. Le tableau qui décide : écart apparié contre market

Même scénario, même graine, même alpha. `z` = écart moyen / erreur type.

| politique | Δ vs market | ±se | z | gagne | perd | pire cas |
|---|---:|---:|---:|---:|---:|---:|
| **pegged** | **+0,2368** | 0,0340 | **+6,96** | 81,9 % | 11,0 % | −6,95 |
| **cancel/replace** | **+0,1686** | 0,0410 | **+4,11** | 75,2 % | 19,7 % | −6,55 |
| **limit passive** | **+0,1496** | 0,0424 | **+3,53** | 75,2 % | 19,7 % | −6,95 |
| **iceberg** | **+0,1482** | 0,0424 | **+3,50** | 75,6 % | 20,1 % | −6,95 |
| **post-only** | **+0,1334** | 0,0458 | **+2,91** | 77,7 % | 22,3 % | −8,83 |
| **adaptative** | **+0,1272** | 0,0453 | **+2,81** | 77,4 % | 22,6 % | −8,83 |
| maker puis hedge taker | −0,0559 | 0,0419 | −1,33 | 68,1 % | 31,9 % | −8,83 |
| VWAP | −0,4201 | 0,0431 | −9,74 | 42,7 % | 57,3 % | −6,95 |
| IOC | −0,6279 | 0,0475 | −13,22 | 11,7 % | 41,0 % | −7,87 |
| FOK | −0,7015 | 0,0541 | −12,97 | 11,8 % | 40,9 % | −8,83 |
| POV | −0,7865 | 0,0581 | −13,54 | 50,8 % | 47,1 % | −7,94 |
| TWAP | −0,8151 | 0,0516 | −15,79 | 30,0 % | 70,0 % | −6,97 |
| market making | −1,0067 | 0,0637 | −15,79 | 31,4 % | 68,6 % | −10,02 |
| multi-jambes simultané | −2,0038 | 0,0993 | −20,18 | 11,9 % | 81,5 % | −18,34 |

**Six politiques battent `market`, huit lui sont inférieures, une est
indistinguable.** L'appariement fait passer les z de « rien de concluant » à
+2,8 … +7,0 sur les six premières : ce n'est pas un effet de taille
d'échantillon, c'est le bruit de scénario qui a été retiré des deux côtés.

⚠️ Même les gagnantes perdent dans 11 à 23 % des scénarios, avec un pire cas à
−6,9. Un avantage moyen n'est pas une garantie par trade.

---

## 3. ⚠️ Cinq politiques sur quinze ne sont pas distinguables

En comparant les résultats **scénario par scénario** au sein de la famille
passive :

| paire | scénarios au résultat identique |
|---|---:|
| limit passive ↔ **iceberg** | **850 / 864 — 98 %** |
| limit passive ↔ post-only | 814 / 864 — 94 % |
| limit passive ↔ adaptative | 812 / 864 — 94 % |
| limit passive ↔ cancel/replace | 692 / 864 — 80 % |

`iceberg` rend un chiffre **identique au bit près** à `limit passive` sur 98 %
des cas. Sa cinquième place n'est pas un résultat indépendant : c'est
`limit passive` assortie d'une pénalité de fidélité. C'est cohérent — ce qui
distingue un iceberg, c'est la quantité cachée dans le carnet, et **ce
simulateur n'a pas de carnet**.

Conséquence directe : le classement interne à la famille passive
(1ʳᵉ, 3ᵉ, 4ᵉ, 5ᵉ, 7ᵉ) repose sur des différences qui n'existent que dans 2 à
20 % des scénarios. **Choisir `cancel_replace` plutôt que `limit_passive` sur
cette base n'est pas justifié.** La conclusion soutenable est qu'il existe une
famille passive qui bat `market`, et que ce simulateur ne sait pas trancher
entre ses membres.

---

## 4. D'où vient l'écart — une seule hypothèse porte tout

| politique | spread | slippage | impact | frais maker | frais taker | spread capté | opp. perdue | maker % |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| market | 0,1688 | 0,2413 | 0,2413 | 0,0000 | 0,2025 | 0,0000 | 0,0000 | 0 % |
| limit passive | 0,0064 | 0,0000 | 0,0000 | 0,0509 | 0,0105 | 0,1298 | 0,5102 | 74,8 % |
| post-only | 0,0000 | 0,0000 | 0,0000 | 0,0506 | 0,0000 | 0,1292 | 0,5856 | 74,5 % |
| cancel/replace | 0,0064 | 0,0000 | 0,0000 | 0,0641 | 0,0105 | 0,1298 | 0,0013 | 94,6 % |
| pegged | 0,0103 | 0,0000 | 0,0000 | 0,0549 | 0,0163 | 0,0000 | 0,2861 | 81,1 % |
| adaptative | 0,0034 | 0,0000 | 0,0000 | 0,0516 | 0,0052 | 0,1305 | 0,5438 | 76,0 % |
| multi-jambes sim. | 0,2236 | 0,4777 | 0,4777 | 0,0000 | 0,2700 | 0,0000 | 0,2770 | 0 % |

Le coût total passe de **13,07 bps** pour `market` à **0,46 bps** pour
`limit passive` — un rapport de **28×**. Tout le résultat du rapport tient dans
ce rapport-là.

Et c'est exactement la partie synthétique. `market` se voit facturer spread +
slippage + impact + frais taker ; les passives ne paient presque rien et
**encaissent** du spread. Aucun de ces quatre postes n'est mesuré sur des quotes
Axi : ils sortent du modèle. Si le modèle surestime le coût d'un ordre au marché,
il fabrique mécaniquement l'avantage passif — et rien dans ce jeu de données ne
permet de le savoir.

---

## 5. L'avantage passif est conditionnel, pas général

Écart apparié contre `market`, par régime :

| politique | spread normal | spread large | vol. basse | vol. haute | taille petite | taille grande |
|---|---:|---:|---:|---:|---:|---:|
| limit passive | **−0,0410** | +0,3403 | **−0,1880** | +0,3680 | +0,0164 | +0,2829 |
| post-only | **−0,0895** | +0,3563 | **−0,1880** | +0,3082 | +0,0137 | +0,2530 |
| cancel/replace | **−0,0412** | +0,3783 | **−0,1541** | +0,3760 | +0,0211 | +0,3161 |
| pegged | **−0,0336** | +0,5072 | **+0,0430** | +0,3454 | +0,0408 | +0,4328 |
| iceberg | **−0,0435** | +0,3399 | **−0,1880** | +0,3642 | +0,0164 | +0,2800 |
| adaptative | **−0,0944** | +0,3487 | **−0,1878** | +0,2941 | +0,0128 | +0,2415 |

C'est le résultat le plus exploitable du lot, et il n'apparaissait nulle part :

- **en spread normal, toute la famille passive est légèrement négative** ; son
  avantage vient entièrement des périodes de spread large ;
- **en volatilité basse, elle est négative** — sauf `pegged`, la seule à rester
  positive partout ;
- **le gain est concentré sur les grosses tailles** (+0,24 à +0,43 contre +0,01
  à +0,04 sur les petites).

Autrement dit : passer en passif *en permanence* n'est pas ce que dit l'étude.
Ce qu'elle dit, c'est de passer en passif **quand le spread s'élargit et que la
taille est significative** — ce qui est précisément la situation où un ordre au
marché coûte cher.

`pegged` est la seule politique dont l'avantage ne dépend d'aucun régime.

---

## 6. Tenue hors échantillon

| politique | dev | validation | **OOS final** | OOS − dev |
|---|---:|---:|---:|---:|
| market | 0,9494 | 1,1713 | 1,0170 | +0,0676 |
| limit passive | 1,1661 | 1,2777 | 1,1428 | −0,0233 |
| post-only | 1,2010 | 1,2177 | 1,1191 | −0,0819 |
| IOC | 0,3512 | 0,5035 | 0,3992 | +0,0481 |
| FOK | 0,3002 | 0,4052 | 0,3280 | +0,0278 |
| cancel/replace | 1,1815 | 1,2987 | 1,1632 | −0,0183 |
| pegged | 1,1914 | 1,4053 | 1,2513 | +0,0599 |
| iceberg | 1,1642 | 1,2771 | 1,1409 | −0,0233 |
| TWAP | 0,2270 | 0,2464 | 0,2188 | −0,0082 |
| VWAP | 0,5555 | 0,7048 | 0,6173 | +0,0618 |
| POV | 0,2567 | 0,2746 | 0,2469 | −0,0097 |
| adaptative | 1,1817 | 1,2153 | 1,1222 | −0,0596 |
| market making | 0,0464 | 0,0372 | 0,0341 | −0,0123 |
| multi-jambes sim. | −0,9742 | −0,9325 | −0,9670 | +0,0072 |
| maker puis hedge taker | 1,0066 | 1,0295 | 0,9339 | −0,0727 |

Dégradation maximale **−0,082**, classement stable sur les trois tiers, aucune
politique ne décroche. L'étude n'est donc pas sur-ajustée à ses scénarios.

Mais les trois tiers sortent du **même générateur**. Cet OOS teste la robustesse
à la variation de scénario, pas la fidélité au courtier : il ne peut pas
détecter l'erreur qui compte, à savoir que le modèle de microstructure soit
faux. Un hors-échantillon de simulation reste de la simulation.

---

## 7. Fiches — les 15, dans l'ordre demandé

**1. Market** — témoin. Fill 100 %, coût 13,07 bps, IS +7,6 bps, DD 0,202. La
seule politique dont la fidélité de modèle est haute (0,90) : elle ne dépend
d'aucune hypothèse de file d'attente. C'est ce qui tourne aujourd'hui.

**2. Limit passive** — Δ **+0,1496** (z 3,53). Fill 80,1 %, coût 0,46 bps,
0,87 unité non exécutée par scénario. Fidélité 0,60, complexité 2. Meilleur
rapport gain/complexité du lot. Négative en spread normal et en volatilité basse.

**3. Post-only** — Δ **+0,1334** (z 2,91). Coût le plus bas de tous (0,088 bps)
et meilleur PF (20,79), mais **5,8 % de rejets** et le fill le plus faible de la
famille (74,5 %). Identique à limit passive sur 94 % des scénarios.

**4. IOC** — Δ **−0,6279** (z −13,22). Fill 52,1 %, dont 16,6 % de partiels, et
2,54 unité non exécutée. Coût 5,27 bps. Elle paie le coût d'un taker sans la
certitude d'exécution. **Perd dans 41 % des scénarios et ne gagne que dans 12 %.**

**5. FOK** — Δ **−0,7015** (z −12,97). Pire fill du lot (47,3 %), 2,92 non
exécuté. Le tout-ou-rien coûte plus cher que l'IOC sans rien apporter ici.

**6. Cancel/replace** — Δ **+0,1686** (z 4,11). Fill **99,9 %** avec 94,6 % en
maker et une opportunité perdue quasi nulle (0,0013) : sur le papier, le beurre
et l'argent du beurre. **Mais fidélité 0,50, complexité 4** — le résultat dépend
entièrement du modèle de file et de replacement, qui n'est adossé à aucune
donnée. Et 80 % de ses scénarios sont identiques à `limit passive`.

**7. Pegged** — Δ **+0,2368** (z **6,96**), le meilleur du lot, et **la seule
politique positive dans tous les régimes**, y compris en volatilité basse.
Fill 89,2 %. Mais fidélité 0,50, complexité 4, et le peg suppose un carnet de
référence — **Axi ne diffuse pas de L2**. Non validable chez ce courtier.

**8. Iceberg** — Δ +0,1482 (z 3,50). **Résultat identique à limit passive sur
98 % des scénarios.** Sans carnet, la quantité cachée ne change rien. Fidélité
0,45. Aucun apport mesurable ici.

**9. TWAP** — Δ **−0,8151** (z −15,79). Fill 36,1 %, le plus bas ; délai moyen
2 657 ms, p95 3 153 ms ; opportunité perdue 1,466. Découper dans le temps sur un
horizon court détruit plus qu'il n'économise. Perd dans 70 % des scénarios.

**10. VWAP** — Δ **−0,4201** (z −9,74). Fill 58,2 %. Moins mauvaise que TWAP
parce qu'elle suit le volume, mais toujours nettement sous `market`.

**11. POV** — Δ **−0,7865** (z −13,54). Le plus faible drawdown de tous (0,020)
et 3,72 unité non exécutée : elle protège en n'exécutant pas. Effondrement sur
les grosses tailles (**−1,58**), ce qui est l'inverse du besoin.

**12. Adaptative maker→taker** — Δ **+0,1272** (z 2,81). Fill 78,9 %, coût
0,30 bps, 2,4 % de rejets. **Complexité 9 pour un gain statistiquement égal à
`limit passive`** (identiques sur 94 % des scénarios), et fidélité 0,55. Le
rapport gain/complexité est le plus mauvais des six gagnantes.

**13. Market making avec contrôle d'inventaire** — Δ **−1,0067** (z −15,79).
Net 0,039, PF 1,96, **13,8 % de rejets**, DD 0,266 — le deuxième pire. Fidélité
0,35. Sans carnet ni file, ce qui est simulé n'est pas du market making.

**14. Multi-jambes simultané** — Δ **−2,0038** (z −20,18). **La seule politique
à espérance négative** (−0,9579), PF 0,05, DD 1,511, exposition résiduelle 1,781
et perte de jambe orpheline 0,2634. 20,4 % de fills partiels : les jambes
n'arrivent pas ensemble, et ce qui reste est une position non voulue. À écarter
sans réserve.

**15. Maker puis hedge taker** — Δ −0,0559 (z −1,33), **la seule indistinguable
de `market`**. IS −13,1 bps, le meilleur du lot, mais net 0,9900 sous `market` :
elle obtient un excellent prix d'entrée et le rend en frais de couverture
(taker 0,0952, le plus élevé après multi-jambes). Délai p95 **6 612 ms**,
complexité 9, fidélité 0,35.

---

## 8. Ce que dit le compte réel

Une seule conclusion de cette étude a un équivalent mesuré en démo : sur
**189 ordres limites réellement placés**, 65 remplis (34,4 %), l'économie
réalisée est de **+0,0995 R par fill** et le slippage de −0,0016 R. Les trades
entrés à la limite performent au-dessus de la moyenne (−0,1829 R contre
−0,2610 R).

Le synthétique et le réel vont donc dans le même sens sur la famille passive.
Deux réserves : le taux de fill réel (34,4 %) est **très en dessous** des 80 %
simulés, et l'avantage réel est bien plus modeste en proportion que le rapport
de 28× sur les coûts.

---

## 9. Verdict

**Ce qui est acquis.** Une famille passive bat `market` dans ce simulateur, de
façon appariée et robuste aux trois tiers. Huit politiques lui sont nettement
inférieures — IOC, FOK, TWAP, VWAP, POV, market making, multi-jambes, et le
maker-puis-hedge est au mieux neutre. Ces huit-là peuvent être écartées.

**Ce qui n'est pas acquis.** Le classement *interne* à la famille passive : ses
membres sont identiques dans 80 à 98 % des scénarios. Rien ne justifie de
préférer `cancel_replace` à `limit_passive`, et `iceberg` n'apporte
mesurablement rien.

**Ce qui est hors de portée chez Axi.** `pegged` domine tout — z 6,96, positive
dans tous les régimes — mais suppose un carnet de référence, et Axi ne diffuse
pas de L2 (`market_book_add` rend `False`, vérifié). Même chose pour `iceberg`
et `market_making`. Ces trois-là ne seront jamais validables **chez ce
courtier**, quelles que soient les données qu'on accumule.

⚠️ Cette phrase est vraie pour Axi et fausse en général : voir la section 10.
Binance expose un carnet L2, et le code qui le maintient existe déjà.

**Ce qui est faisable maintenant.** Le trio `market` / `limit_passive` /
`adaptive` n'a besoin que du L1. Mais **rien n'archive les quotes** :
`copy_ticks_from` et `copy_ticks_range` n'apparaissent nulle part dans le code.
Tant que ce n'est pas corrigé, aucune de ces conclusions ne pourra être
confrontée au réel.

**Recommandation, par ordre de force :**

1. **archiver les ticks L1** — seul geste qui débloque quoi que ce soit, sans
   effet sur aucune décision de trading ;
2. **écarter définitivement** les huit politiques perdantes et les trois
   data-gated : il reste `market`, `limit_passive`, `post_only`, `adaptive` ;
3. **ne pas promouvoir `cancel_replace`** malgré sa première place au score
   composite — 80 % de ses scénarios sont ceux de `limit_passive`, pour une
   fidélité de 0,50 et une complexité double ;
4. si un jour un passage passif est envisagé, le **conditionner au spread et à
   la taille**, jamais l'appliquer en permanence : en spread normal, la famille
   passive est négative.

Aucune de ces mesures n'autorise un passage live. Cette matrice compare des
politiques dans un simulateur ; elle ne prouve aucun edge réel.

---

## 10. Réévaluation : et avec le carnet L2 de Binance ?

Ajouté le 15/08/2026 sur objection de Florent. La conclusion de la section 9
— « jamais validables » — était scopée à Axi sans le dire. Binance change le
tableau, mais pas comme on pourrait l'espérer.

### Ce qui existe déjà, et qui tourne

`ingestion/market/orderbook_ws.py` (V12, 267 lignes) maintient un **vrai carnet
L2** : snapshot REST initial puis diffs `@depth@100ms`, spot **et** futures, le
carnet complet tenu en dictionnaires prix→quantité pour que les diffs restent
exacts. Profondeur configurée : **100 niveaux spot, 1 000 niveaux futures**.

Ce n'est pas un prototype : `poles/indicators/orderbook.py` et
`poles/smc/signal_engine.py` le consomment, et `tests/test_orderbook_l2.py`
verrouille l'application des diffs.

Donc oui — la donnée qui manque chez Axi existe chez Binance, et le code pour la
lire est écrit.

### Ce que Binance ne donne pas : le L3

`@depth` est **agrégé par niveau de prix**. Aucun identifiant d'ordre, aucune
file individuelle. **La position dans la file reste invisible.**

C'est précisément ce dont dépendent `post_only`, `cancel_replace` et `iceberg` —
et c'est la raison de leurs fidélités de 0,45 à 0,50. Le L2 fait monter cette
fidélité de façon importante ; il ne la ferme pas. La seule manière d'observer
sa propre position de file reste de poster de vrais ordres et de mesurer ses
propres fills.

Corollaire : la première place de `cancel_replace` resterait indéterminée même
avec le L2 Binance, puisqu'elle vient entièrement du modèle de replacement.

### Ce qui n'est toujours pas archivé — mais qui est réparable

Le carnet vit **en RAM** : `OrderBookState.history` est un anneau de
`ORDERBOOK_HISTORY_SIZE = 30` snapshots, top 20 niveaux. Rien sur disque, aucun
NDJSON, aucune base. Deux symboles suivis : `BTC/USDT` et `PAXG/USDT`.

Aujourd'hui il y a donc **zéro historique L2 exploitable**, comme chez Axi —
mais pour une raison opposée, et c'est ce qui compte :

| | Axi / MT5 | Binance |
|---|---|---|
| le L2 existe-t-il ? | **non** (`market_book_add` → `False`) | **oui**, 100 ms |
| le reçoit-on ? | — | **oui**, déjà branché |
| l'archive-t-on ? | non | **non — on le jette** |
| réparable ? | jamais | **oui, en écrivant ce qui passe déjà** |

Chez Axi la donnée n'existe pas. Chez Binance elle arrive et on la laisse
tomber. Le second cas se corrige.

### Le piège qui décide : la place de marché

Titanium exécute via **MT5 / Axi**, et Axi est un **dealer CFD** : il n'y a pas
de carnet central. On ne poste pas dans une file, il n'y a ni rebate maker, ni
post-only, ni quantité affichée d'iceberg. MT5 offre des ordres en attente et
des modes de remplissage IOC/FOK — pas les types d'ordres d'une place.

Donc valider `pegged`, `iceberg`, `post_only` ou `market_making` sur le carnet
Binance validerait des politiques **inexécutables sur le compte où l'on trade**.

La question n'est plus « peut-on les tester » — elle devient **« veut-on
exécuter sur Binance »**, ce qui est un changement de place de marché : nouveau
compte, nouvel exécuteur, mur démo↔réel à reconstruire, régime fiscal et
contrepartie différents. C'est une décision de Florent, pas un réglage.

### Reclassement des 15

| politique | statut chez Axi | statut avec le L2 Binance |
|---|---|---|
| market, limit passive | exécutable et mesurable | inchangé |
| IOC, FOK | exécutables (modes de remplissage MT5) | inchangé |
| TWAP, VWAP, POV | ordonnanceurs, indépendants du carnet | inchangé |
| multi-jambes ×2 | limité par la synchronisation, pas par le carnet | marginal — résultat déjà catastrophique |
| **post-only** | **inexécutable** | **validable**, fidélité en hausse, file toujours inconnue |
| **cancel/replace** | approximable par annuler+replacer | **validable**, mais son avantage vient du modèle de file : indéterminé même avec le L2 |
| **pegged** | **inexécutable** | **validable** — et c'est la mieux classée du lot |
| **iceberg** | **inexécutable** | **validable** — et enfin distinguable de `limit passive`, ce qu'elle n'est pas ici |
| **market making** | **inexécutable** | **validable**, la plus dépendante du carnet |

Cinq politiques passent de « hors de portée » à « validable, sur une autre
place ». Aucune ne passe à « exécutable sur Axi ».

### Ce que je recommande

1. **Archiver le carnet Binance qui passe déjà** — au minimum `BTC/USDT`,
   niveaux et horodatage, plus le flux `@aggTrade` qui donne les transactions
   avec le côté maker. Les flux de marché publics de Binance ne demandent
   **aucune clé API** : un enregistreur peut vivre dans V14, sans toucher V12 ni
   aucun compte. C'est le seul geste qui crée de la donnée irremplaçable, et
   chaque jour sans lui est un jour perdu.
2. **En parallèle, archiver les ticks L1 Axi** — recommandation inchangée, et
   c'est l'autre moitié : le L1 Axi valide ce qu'on peut réellement exécuter, le
   L2 Binance valide ce qu'on ne peut pas encore.
3. **Rejouer les six politiques dépendantes du carnet** une fois quelques
   semaines accumulées, avec une fidélité **mesurée** au lieu de postulée.
4. **Ne pas confondre validation et exécution.** Un bon résultat sur Binance ne
   justifie pas un ordre sur Axi, et ne justifie pas non plus d'ouvrir Binance :
   ce serait une décision distincte, à prendre pour elle-même.

Réserve de méthode : la microstructure de `BTCUSDT` sur Binance n'est pas celle
du CFD `BTCUSD` chez Axi. Même validées sur un vrai carnet, ces politiques
resteraient mesurées sur un marché qui n'est pas celui où l'argent est engagé.
