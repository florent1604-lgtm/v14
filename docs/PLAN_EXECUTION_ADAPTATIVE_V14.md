# Plan d'exécution adaptative V14 — structure, catalogue et mesure

**Statut : étude + lot implémenté, mode dry-run.** Aucune promotion live. Aucun
seuil de trading modifié.

Date : 2026-09-12 · moteur mesuré : `b39a985ecdc8941b` · seed `14082026`

> **Passe de correction du 12/09/2026.** La première livraison annonçait 17
> techniques et « 11 battent `market` ». Deux défauts, trouvés en auditant le
> livrable plutôt qu'en le relisant, invalidaient ce chiffre :
>
> 1. **Deux paires étaient identiques au bit près sur 864/864 scénarios** —
>    `adapt_inventory_skew` ≡ `adapt_join_touch` (l'axe inventaire n'était jamais
>    varié, donc l'inclinaison valait toujours zéro) et `adapt_spread_budget` ≡
>    `adapt_spread_expansion` (3,0 × 2,0 = 6,0 bps, soit exactement le même seuil
>    absolu). Deux noms ne faisaient qu'un résultat, et le compte des gagnants
>    était gonflé par ce doublon.
> 2. **Trois axes d'adaptation — inventaire, urgence, horizon — n'étaient jamais
>    conduits par le harnais.** Trois techniques ne pouvaient pas s'adapter à ce
>    qu'elles annonçaient.
>
> Les deux sont corrigés, et les deux sont désormais **des portes de test**, pas
> des relectures : une collision sur tous les scénarios fait échouer la suite, et
> un axe déclaré inerte aussi. Le classement ci-dessous contient 17 lignes
> indépendantes.

---

## 1. Ce que « rentable » veut dire ici, et pourquoi je ne le promets pas

Une technique d'exécution n'a pas de rentabilité propre : elle ne fait que
*déplacer le prix réalisé et le coût* d'une intention qui existe déjà. Ce qui est
mesurable, c'est le **delta d'exécution apparié** — l'écart de résultat, scénario
par scénario, contre le témoin `market`, à alpha identique. C'est la méthode du
rapport des 15 politiques (`docs/RAPPORT_BACKTEST_15_POLITIQUES.md`).

Les techniques sont donc livrées comme **hypothèses mesurées**, jamais comme
gains acquis. Le document donne les chiffres obtenus, y compris les mauvais :
**10 techniques sur 17 battent `market`, 7 lui sont inférieures.**

---

## 2. Diagnostic de la structure d'exécution actuelle

### 2.1 Ce qui existe et qui est solide

| Brique | Rôle | Force |
|---|---|---|
| `titanium/execution/` | exécuteur MT5 DEMO protégé, gestion de position | mur DEMO/réel, idempotence, journal append-only |
| `titanium/execution_sim/` | laboratoire offline (`live_enabled=false` forcé) | OMS, matching, risk, portfolio, métriques, matrice reproductible |
| `titanium/gates/` | porte de confluence BLOCK/WAIT/ENTER | veto déterministe avant toute exécution |
| `titanium/risk/` | RiskGate, plafonds d'exposition | le dernier mot n'appartient jamais au LLM |
| `titanium/features/` | structure, SMC/ICT, profils | features multi-échelles, pas de fuite future |

La séparation **alpha → risque → politique d'exécution → matching → métriques**
est le bon dessin et reste en place.

### 2.2 Les huit points faibles structurels

1. **L'arène est figée, pas conditionnelle.** Les 15 politiques sont des formes
   d'ordre statiques ; `adaptive` est une échelle à durées fixes.
2. **Aucune technique ne lit le contexte d'arrivée de façon unifiée.**
3. **Pas d'échec fermé systématique** hors `v14_live`.
4. **Pas de trace de décision** : on mesure *ce qui* a été envoyé, pas *pourquoi*.
5. **Aucune mesure appariée par défaut** : le rapport des 15 l'a introduite après
   coup sur un CSV existant.
6. **Un seul axe d'adaptation par politique.**
7. **Pas de sélecteur méta**, alors que l'avantage passif est conditionnel.
8. **Coût de fidélité non isolé** pour les techniques dépendantes du carnet.

### 2.3 Frontières de modules et invariants à propriétaire unique

Le lot P0 laissait quatre tensions structurelles : un catalogue de 950 lignes
mélangeant features, base, techniques, sélecteur et registre ; deux machines à
états séquentielles parallèles ; deux dictionnaires de métadonnées à tenir
synchronisés à la main ; et un contrat de remplissage réécrit dans chaque
exécuteur. Chacune est désormais portée par **un seul fichier**, dont le rôle est
nommé dans son en-tête.

| Module | Ce qu'il possède, et lui seul |
|---|---|
| `adaptive_features.py` | **ce qu'une technique voit à l'arrivée** : `AdaptiveFeatures` et `build_features`. L'échec fermé (tick nul, carnet inversé, prix non fini, quantité ≤ 0, référence manquante) vit ici, une fois. |
| `adaptive_base.py` | **la mécanique commune** : dérivation du contexte, bornage dur au carnet, échéance de tranche, trace de décision. Une technique n'écrit que `decide`. |
| `adaptive.py` | **le catalogue, et rien d'autre** : 17 techniques, le sélecteur, le registre. Chaque technique *déclare* nom, hypothèse, axe, complexité, fidélité, séquentialité. |
| `fills.py` | **le contrat de remplissage** : `FillBudget`, « jamais plus que la quantité voulue », partagé par les deux exécuteurs. |
| `runner.py` | **l'ordonnancement** : une seule machine séquentielle, une seule discrétisation `_index_activation`. |
| `metrics.py` | **les profils** : une seule table pour l'arène historique ; la famille adaptative lit les déclarations. |

Les invariants qui n'ont plus qu'un propriétaire :

| Invariant | Propriétaire unique |
|---|---|
| jamais de sur-remplissage | `fills.FillBudget` (`autoriser` / `enregistrer`) |
| offset millisecondes → indice de snapshot | `runner._index_activation` |
| exécution séquentielle (deux familles) | `runner._executer_politique_sequentielle` + `ProfilSequentiel` |
| contexte d'arrivée | `adaptive_features.build_features` |
| métadonnées d'une technique | attributs de classe, lus par le registre, les métriques et la sonde d'axes |
| comparabilité de l'arène historique | `policies.POLICY_REGISTRY`, `runner.ALL_POLICIES` — inchangés |

Conséquence pratique : **une technique = une classe dans `adaptive.py`** (plus
ses valeurs par défaut) ; **un axe = un champ dans `AdaptiveFeatures`** ; **un
changement de règle de remplissage = un seul fichier**. `ProfilSequentiel`
nomme en un endroit les quatre choix qui distinguent l'arène `adaptive`
historique de la famille adaptative, au lieu de les dupliquer dans deux
machines.

Ces frontières sont **sans effet observable** : voir §6.5 pour la preuve de
reproduction exacte.

---

## 3. Plan d'amélioration structurelle

### P0 — livré

| Lot | Contenu | Critère vérifiable |
|---|---|---|
| P0-1 | Contexte d'arrivée unifié (`AdaptiveFeatures`) | mêmes entrées ⇒ mêmes features |
| P0-2 | Échec fermé par construction | aucun ordre sur tick nul, carnet inversé, prix non fini, quantité ≤ 0 |
| P0-3 | Trace de décision par ordre | `technique`, `decision`, `hypothesis` sur chaque ordre |
| P0-4 | 17 techniques adaptatives | 17 comportements **distincts**, mesuré |
| P0-5 | Chemin séquentiel correct | jamais de sur-remplissage : un seul contrat (`fills.FillBudget`), une seule machine séquentielle, partagés par les deux exécuteurs |
| P0-6 | Isolation de l'arène historique | `POLICY_REGISTRY` et `ALL_POLICIES` inchangés, matrice historique bit-identique |
| P0-7 | Mesure appariée par défaut + portes d'indépendance et d'axes | delta par régime et par tiers, collisions détectées, axes sondés |
| P0-8 | Un propriétaire par concern | `adaptive_features` / `adaptive_base` / `adaptive` / `fills` séparés (cf. §2.3), résultat inchangé à la ligne près |

### P1 — prochains développements

| Lot | Contenu | Pourquoi | Risque / retour arrière |
|---|---|---|---|
| P1-1 | **Archiver les quotes broker (L1)** | sans quotes réelles, aucune de ces mesures n'est confrontable au courtier | lecture seule, aucun ordre ; le geste qui débloque tout |
| P1-2 | **Brancher les techniques sur les quotes archivées** | la matrice synthétique surestime le passif | même protocole ; réversible |
| P1-3 | **Attribution intention → deals** | l'audit du 06/09 la classe P1 | couverture 100 %, orphelin = UNKNOWN |
| P1-4 | **Registre de politiques versionnées** | comparer deux versions d'une technique dans le temps | append-only |

### P2 — reporté

- Modèle de file FIFO réaliste : impossible sans données de place (Axi ne diffuse
  pas de L2, `market_book_add` → `False`).
- Techniques dépendant du carnet L2 : hypothèses tant que le carnet n'est pas
  archivé.
- Optimisation de seuils sur ces données : **prohibée** par le contrat du projet.

---

## 4. Le catalogue — 17 techniques adaptatives

Toutes héritent de `AdaptiveTechnique` et partagent quatre garanties : contexte
unifié, échec fermé, bornage dur (un ordre passif ne traverse jamais le carnet),
trace de décision.

**Les axes sont désormais conduits par le harnais** (inventaire, urgence,
horizon), et l'outil vérifie que chaque axe déclaré change réellement le
résultat.

| # | Technique | Axe d'adaptation | Axe conduit ? | Hypothèse |
|---:|---|---|---|---|
| 1 | `adapt_spread_budget` | spread vs budget absolu | — | au-delà d'un budget, le passif bat le marché |
| 2 | `adapt_volatility_scale` | volatilité | — | reculer de k×vol améliore le prix moyen |
| 3 | `adapt_depth_guard` | profondeur prenable | — | prendre n'est peu coûteux que si le carnet couvre la taille |
| 4 | `adapt_urgency_ladder` | **urgence** | **oui** | l'agressivité optimale suit l'urgence |
| 5 | `adapt_deadline_ladder` | **horizon** | **oui** | tranches passives + clôture garantie à l'échéance |
| 6 | `adapt_microprice_anchor` | déséquilibre du carnet | — | le microprice anticipe mieux que le milieu |
| 7 | `adapt_inventory_skew` | **inventaire** | **oui** | un inventaire engagé doit rendre les ajouts plus sélectifs |
| 8 | `adapt_cost_benefit` | spread vs volatilité | — | le passif ne vaut que si l'économie dépasse l'anti-sélection |
| 9 | `adapt_volatility_abort` | volatilité extrême | — | au-delà d'un seuil, ne pas exécuter est le moins coûteux |
| 10 | `adapt_depth_slice` | profondeur | — | limiter chaque enfant à une fraction de la profondeur |
| 11 | `adapt_improve_touch` | spread en ticks | — | améliorer d'un tick gagne la priorité de file |
| 12 | `adapt_join_touch` | — (témoin maker) | — | jonction la moins informative, référence maker |
| 13 | `adapt_spread_participation` | spread vs référence | — | plus le spread s'étend, plus il paie de répartir en tranches |
| 14 | `adapt_size_patience` | taille | — | petite taille vite, grosse taille fractionnée |
| 15 | `adapt_midpoint_aggressive` | spread | — | au milieu en spread large, au touch sinon |
| 16 | `adapt_ladder_maker_taker` | **horizon** | **oui** | tentative maker bornée, clôture taker du reliquat |
| 17 | `adapt_selector` | contexte global | — | une règle d'arbitrage bat ses propres délégations |

### 4.1 Les deux collisions corrigées

**`adapt_inventory_skew` ≡ `adapt_join_touch` (864/864).** L'inventaire n'étant
jamais varié, l'inclinaison valait zéro sur tous les scénarios : la technique
*était* `join_touch`, à la ligne près. Corrigé en conduisant l'axe
(`runner.axes_adaptation`, paliers −0,75 / 0 / +0,75 du plafond, soit plusieurs
ticks de déplacement). Les deux sont maintenant distinctes et l'inclinaison
d'inventaire apparaît comme la **3ᵉ** meilleure technique.

**`adapt_spread_budget` ≡ `adapt_spread_expansion` (864/864).** Les deux
comparaient le spread à un seuil absolu, et 3,0 × 2,0 = 6,0 bps = le budget.
**Aucun réglage de seuil ne les aurait distinguées** : le harnais ne compte que
deux niveaux de spread (normal ≈ 3 bps, large ≈ 12 bps), donc deux règles à seuil
sont identiques dès que leurs seuils tombent dans le même intervalle. La
différence est donc portée par la **règle** : `adapt_spread_budget` rend un ordre
unique, `adapt_spread_participation` répartit la taille en tranches dont le
*nombre* suit le rapport d'expansion (4 tranches à 12/3 bps). Les deux sont
maintenant distinctes — et la seconde **perd** (−0,212), ce que la première
cachait.

### 4.2 Les deux corrections trouvées en mesurant, pas en relisant

- **Échéance relative au départ de la tranche** : comptée depuis l'arrivée, une
  tranche programmée tard naissait déjà expirée.
- **Clôture agressive au prix qui prévaut** : un IOC plafonné à l'ask d'arrivée
  ne se remplit plus dès que le marché a bougé.
- **Bornage du remplissage** : `BacktestExecutionEngine` ne bornait pas la
  quantité au reliquat, alors que le runner le faisait. Une échelle de trois
  tranches remplissait **10 unités pour une intention de 6 (66 %)** — invisible
  depuis le chemin que les tests couvraient. Le contrat est maintenant honoré par
  les deux exécuteurs, et testé sur les deux.

---

## 5. Protocole de mesure

```powershell
.venv\Scripts\python.exe -X utf8 tools\execution_adaptative.py --jobs 4
```

- **témoin** : `market`, dans les mêmes 864 scénarios ;
- **appariement** : par `(scenario_id, split)`, alpha identique des deux côtés ;
- **axes** : inventaire, urgence et horizon sont **conduits** pour la famille
  adaptative, et pour elle seule — l'arène historique, qui lit
  `PolicyContext.inventory`, reste donc bit-identique ;
- **régimes** : spread, volatilité, taille, liquidité, tendance, **plus les trois
  axes** ;
- **tiers** : développement / validation / **final OOS rapporté séparément** ;
- **portes automatiques** : indépendance (aucune paire identique sur tous les
  scénarios) et sonde d'axes (tout axe déclaré doit changer le résultat).

---

## 6. Résultats mesurés (17 techniques × 864 scénarios)

Delta apparié contre `market`. Négatif = pire que le témoin.

| # | technique | delta | ±se | z | gagne | perd | pire cas | fill | coût bps |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `adapt_midpoint_aggressive` | **+0,2446** | 0,0348 | **+7,03** | 82,2 % | 11,6 % | −6,95 | 88,3 % | 1,23 |
| 2 | `adapt_microprice_anchor` | **+0,2369** | 0,0342 | **+6,92** | 82,4 % | 10,5 % | −6,95 | 89,7 % | 1,71 |
| 3 | `adapt_inventory_skew` | **+0,1730** | 0,0418 | **+4,14** | 76,2 % | 19,1 % | −6,95 | 78,5 % | 0,62 |
| 4 | `adapt_spread_budget` | **+0,1705** | 0,0328 | **+5,20** | 39,2 % | 9,7 % | −6,95 | 90,0 % | 5,40 |
| 5 | `adapt_selector` | **+0,1667** | 0,0292 | **+5,71** | 54,4 % | 8,0 % | −4,85 | 92,1 % | 5,16 |
| 6 | `adapt_improve_touch` | **+0,1569** | 0,0413 | **+3,80** | 75,8 % | 18,8 % | −6,95 | 81,2 % | 0,64 |
| 7 | `adapt_join_touch` | **+0,1556** | 0,0424 | **+3,67** | 75,6 % | 19,4 % | −6,95 | 80,3 % | 0,45 |
| 8 | `adapt_volatility_scale` | **+0,1419** | 0,0476 | **+2,98** | 73,1 % | 23,8 % | −7,87 | 75,3 % | 0,24 |
| 9 | `adapt_volatility_abort` | **+0,1233** | 0,0244 | **+5,06** | 26,5 % | 3,1 % | −6,95 | 97,0 % | 8,75 |
| 10 | `adapt_cost_benefit` | **+0,1084** | 0,0377 | **+2,87** | 50,3 % | 15,3 % | −6,95 | 84,4 % | 3,79 |
| 11 | `adapt_urgency_ladder` | −0,1449 | 0,0374 | −3,88 | 30,9 % | 19,4 % | −7,87 | 79,3 % | 6,19 |
| 12 | `adapt_depth_guard` | −0,1481 | 0,0445 | −3,33 | 24,7 % | 27,2 % | −7,74 | 68,1 % | 4,00 |
| 13 | `adapt_spread_participation` | −0,2122 | 0,0377 | −5,64 | 20,3 % | 29,7 % | −7,87 | 65,5 % | 5,31 |
| 14 | `adapt_depth_slice` | −0,4260 | 0,0518 | −8,22 | 56,8 % | 43,2 % | −8,83 | 42,7 % | 0,10 |
| 15 | `adapt_size_patience` | −0,4488 | 0,0515 | −8,71 | 28,0 % | 40,4 % | −8,83 | 49,7 % | 2,68 |
| 16 | `adapt_deadline_ladder` | −0,7281 | 0,0458 | −15,91 | 30,0 % | 70,0 % | −6,62 | 98,4 % | 30,80 |
| 17 | `adapt_ladder_maker_taker` | −0,7387 | 0,0470 | −15,70 | 29,5 % | 70,5 % | −6,62 | 98,1 % | 31,04 |

**Comptes honnêtes : 17 noms, 17 comportements distincts, 10 gagnants, dont 10
distincts.** Aucune paire n'est identique sur tous les scénarios ; aucune paire
n'est indiscernable sur ≥ 99 % des scénarios.

### 6.1 Le résultat le plus exploitable

Le classement par régime reproduit, sur des techniques *nouvelles*, la conclusion
du rapport des 15 politiques :

| technique | spread normal | spread large | vol. basse | vol. haute | taille petite | taille grande |
|---|---:|---:|---:|---:|---:|---:|
| `adapt_midpoint_aggressive` | −0,030 | +0,519 | +0,084 | +0,357 | +0,042 | +0,447 |
| `adapt_microprice_anchor` | −0,045 | +0,519 | +0,061 | +0,348 | +0,046 | +0,428 |
| `adapt_inventory_skew` | −0,032 | +0,378 | −0,164 | +0,424 | +0,024 | +0,322 |
| `adapt_join_touch` | −0,030 | +0,341 | −0,186 | +0,370 | +0,018 | +0,293 |

**L'avantage passif reste négatif en spread normal** et concentré sur les grandes
tailles et les spreads larges. Les techniques adaptatives ne renversent pas ce
fait : elles le **conditionnent**.

### 6.2 Tenue hors échantillon

Les 10 gagnantes restent positives sur le tiers `final_oos` ; dégradation
maximale modérée (`adapt_cost_benefit` : dev +0,151 → OOS +0,066). Les 7
perdantes restent négatives sur les trois tiers. Le classement est stable sur les
trois tiers — mais les trois tiers sortent du *même générateur* : robustesse à la
variation de scénario, pas fidélité au courtier.

### 6.3 Ce que la mesure a réfuté ou confirmé

- **Deux paires étaient un seul résultat** (voir §4.1). Le compte précédent de
  « 11 gagnants » était gonflé par le doublon `spread_budget`/`spread_expansion` ;
  le compte correct est **10**.
- **L'inventaire est un axe utile, contrairement à ce que la première mesure
  laissait croire.** Quand l'axe est réellement conduit, `adapt_inventory_skew`
  passe de « copie de `join_touch` » à 3ᵉ place (+0,173, z +4,14).
- **Le sélecteur ne bat toujours pas sa meilleure délégation** : +0,1667 contre
  +0,2446 pour `adapt_midpoint_aggressive`. Sa branche « profondeur
  insuffisante → `adapt_depth_guard` » coûte, puisque `adapt_depth_guard` est
  elle-même négative (−0,148). **La règle n'a pas été ajustée après avoir vu le
  résultat** : la correction reste une hypothèse à préenregistrer.
- **L'hypothèse de la répartition par spread est réfutée** :
  `adapt_spread_participation` est négative (−0,212) et notamment **en spread
  large** (−0,424), exactement là où elle devait aider.
- **Le fractionnement détruit plus qu'il n'économise** : `adapt_depth_slice`
  (−0,426) et `adapt_size_patience` (−0,449) ont le coût le plus bas du lot mais
  remplissent mal (42,7 % et 49,7 %).
- **Les clôtures taker sont chères** : `adapt_deadline_ladder` (30,8 bps) et
  `adapt_ladder_maker_taker` (31,0 bps), désormais ordonnées par l'horizon
  déclaré, paient la garantie d'exécution (−0,73 et −0,74).
- **Ancienne coïncidence disparue** : `adapt_urgency_ladder` rendait exactement
  le delta de l'`ioc` historique (−0,6279) parce que l'urgence valait toujours
  par défaut, ce qui la réduisait à un IOC. L'axe conduit, elle vaut −0,1449 et
  se comporte comme annoncé : à urgence 0,9 son delta est **0,000** (elle envoie
  au marché, donc comme le témoin) et à urgence 0,5 il vaut −0,765 (elle prend un
  IOC).
- **Inertie résiduelle, non déclarée** : `adapt_volatility_abort` rend 0,000 en
  volatilité basse et moyenne — dans ces régimes elle envoie au marché, donc
  elle *est* le témoin. Ce n'est pas un défaut (elle ne déclare pas ces axes),
  mais cela signifie que son delta de +0,123 vient **entièrement** du régime de
  volatilité haute : sur 2 scénarios sur 3, elle ne fait rien de particulier.

### 6.4 Sonde d'axes

Même scénario, un seul axe déplacé à la fois, sur 12 scénarios : les 4 axes
déclarés (`inventory_skew`→inventaire, `urgency_ladder`→urgence,
`deadline_ladder` et `ladder_maker_taker`→horizon) **réagissent tous**. Aucun axe
déclaré inerte.

### 6.5 Reproduction exacte après restructuration

La séparation en modules (§2.3) est **sans effet observable**, prouvé sur les
artefacts et non sur une lecture :

| Vérification | Résultat |
|---|---|
| 15 552 lignes de mesure brutes (17 techniques × 864 scénarios) | **0 ligne diffère**, tous champs confondus |
| rapport JSON complet | identique, **sauf** `engine_version` (empreinte du paquet, qui change car il compte trois fichiers de plus) |
| rapport Markdown | identique, **sauf** la ligne `moteur <empreinte>` |
| politique `adaptive` de l'arène historique (seule des quinze à passer par la machine séquentielle refactorisée) | **0 différence** sur les 78 colonnes, 864/864 scénarios |
| `POLICY_REGISTRY` / `ALL_POLICIES` | inchangés |

Les quatre politiques qui diffèrent d'une référence vieille d'un mois
(`cancel_replace`, `pegged`, `pov`, `vwap`) suivent le chemin événementiel, dont
le corps n'est pas touché par ce lot : leurs écarts préexistaient. Aucune n'est
concernée par la machine séquentielle.

---

## 7. Ce que cette étude ne prouve pas

- **Aucune rentabilité.** Quotes, profondeur et chemin intrabarre sont
  synthétiques.
- **Un delta moyen n'est pas une garantie par trade.** Même les gagnantes perdent
  dans 8 à 24 % des scénarios, avec un pire cas autour de −7.
- **La dépendance au carnet n'est pas mesurée.** `adapt_microprice_anchor` et
  `adapt_depth_slice` reposent sur une profondeur synthétique ; fidélité
  déclarée 0,50 et 0,60, pas mesurée.
- **Rien ne dit que ces résultats tiendront chez Axi.** Dealer CFD : pas de
  carnet central, pas de file, pas de rebate maker. Quatre techniques y sont
  probablement approximatives par construction.
- **La conduite des axes reste un modèle.** L'inventaire est tiré à trois paliers
  et l'urgence à trois niveaux : cela prouve que l'axe *agit*, pas que ces
  valeurs ressemblent à un inventaire réel de V14.

---

## 8. Feuille de route préenregistrée

Toute correction de règle doit être écrite **avant** la mesure.

1. **P1-1 — archiver les quotes broker L1.** Seul geste qui débloque une
   confrontation au réel ; chaque jour sans lui est un jour perdu.
2. **P1-2 — rejouer les 17 techniques sur ces quotes**, fidélité mesurée au lieu
   de déclarée. Critère : le classement tient-il sur des quotes réelles ?
3. **P1-3 — rétablir l'attribution intention → ordre → deals → clôture.**
4. **P1-4 — hypothèse de correction du sélecteur, écrite avant mesure :**
   « retirer la branche `depth_guard` fait passer le sélecteur au niveau de sa
   meilleure délégation. » À tester sur quotes archivées.
5. **P1-5 — ne remplacer aucune technique perdante sur ce seul run.**
   `depth_guard`, `depth_slice`, `size_patience` et `spread_participation` sont
   négatives *dans ce modèle de coût* ; c'est le modèle de coût qui porte
   l'essentiel du résultat.
6. **P1-6 — étendre la sonde d'axes aux techniques qui devraient réagir sans le
   déclarer** (par exemple `volatility_abort` sur la volatilité) : la sonde
   couvre aujourd'hui l'axe *déclaré*, pas la sensibilité réelle.

---

## 9. Garde-fous

- `execution_sim` refuse `execution.live_enabled` vrai ; aucun réseau, aucune
  clé, aucun `.env`.
- L'arène historique est **inchangée** : matrice des 15 politiques **bit-identique**
  (SHA-256 des 12 960 lignes comparable avant/après ce lot), `POLICY_REGISTRY` et
  `ALL_POLICIES` intacts.
- Le chemin séquentiel ne remplit jamais plus que la quantité voulue, dans le
  runner **et** dans `BacktestExecutionEngine` (testé sur les deux).
- Les 17 techniques survivent à un contexte non fiable en ne produisant aucun
  ordre (testé pour les 17).
- Aucun seuil de trading modifié. Aucune promotion.

---

## 10. Fichiers de ce lot

| Fichier | Nature | Propriétaire de |
|---|---|---|
| `titanium/execution_sim/adaptive_features.py` | contexte d'arrivée, échec fermé | ce qu'une technique voit |
| `titanium/execution_sim/adaptive_base.py` | base commune | la mécanique partagée des techniques |
| `titanium/execution_sim/adaptive.py` | catalogue | 17 techniques, sélecteur, registre |
| `titanium/execution_sim/fills.py` | contrat de remplissage | « jamais plus que la quantité voulue » |
| `titanium/execution_sim/runner.py` | ordonnancement | axes conduits, machine séquentielle unique, discrétisation |
| `titanium/execution_sim/engine.py` | exécuteur générique | *utilise* `FillBudget`, ne le réimplémente plus |
| `titanium/execution_sim/policies.py` | `get_policy` : repli paresseux vers la famille adaptative | l'arene historique (inchangée) |
| `titanium/execution_sim/metrics.py` | une table de profils | complexité / fidélité |
| `titanium/execution_sim/config.py` | paramètres par défaut, versionnables | les seuils |
| `tools/execution_adaptative.py` | mesure appariée, régimes, tiers, indépendance, sonde d'axes | la mesure |
| `tests/test_execution_sim_adaptive.py` | 55 tests, dont les portes d'indépendance et d'axes | la non-régression |
| `docs/PLAN_EXECUTION_ADAPTATIVE_V14.md` | la présente étude | — |

Reproduction complète :

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/test_execution_sim_adaptive.py -q
.venv\Scripts\python.exe -X utf8 tools\execution_adaptative.py --jobs 4
.venv\Scripts\python.exe -m ruff check titanium/execution_sim/ tools/execution_adaptative.py
```
