# Catalogue d'hypothèses d'alpha — Titanium V14

**Date :** 13 septembre 2026
**Objet :** transformer « la prochaine grande idée est peut-être dans un papier que personne ne lit » en une
liste de candidats **nommés, falsifiables et mesurables dans ce dépôt** — ou écartés avec la raison qui
le justifie.
**Documents liés :** `VEILLE_RECHERCHE_ALPHA_V14_20260913.md` (sources, pipeline en cinq portes,
inventaire de la donnée), `DOSSIER_PRODUCTION_V14_20260913.md` §3 (ce que les acteurs exécutent).

> **Statut de chaque entrée, sans ambiguïté.** « Donnée disponible » est **vérifié** sur le dépôt et le
> terminal le 13/09/2026. Toute **prédiction de pouvoir prédictif est non mesurée** : ce document est
> un backlog d'hypothèses, pas un résultat. Aucune ligne ci-dessous n'autorise une promotion de seuil
> ni une mise en live.

---

## Pourquoi la littérature est nécessaire ici — et pourquoi elle est dangereuse

La famille adaptative actuelle a été construite **par raisonnement** sur la mécanique du carnet
(bornage, échéance de tranche, passif vs agressif). Elle est mesurée honnêtement dans l'arène
(`seed 14082026`, 17 techniques, **10 battent `market`**), mais elle n'est adossée à **aucun résultat
de littérature**. La littérature apporte exactement ce qui manque : des **échelles de grandeur** et
des **relations testables** (impact ∝ déséquilibre ÷ profondeur, décroissance de l'impact, régimes de
toxicité). Elle apporte aussi son poison : des effets publiés sur d'autres marchés, d'autres
horizons, d'autres coûts.

**Règle d'entrée :** une hypothèse ci-dessous n'entre dans le catalogue de techniques que si (a) la
donnée existe localement, (b) la prédiction est chiffrée **avant** la mesure, et (c) la mesure tombe
sur un jeu que la famille actuelle n'a pas servi à calibrer.

---

## H1 — L'impact de prix est linéaire en déséquilibre du flux, et inversement proportionnel à la profondeur

**Papier :** Cont, Kukanov & Stoikov (2014), *The Price Impact of Order Book Events*, Journal of
Financial Econometrics 12(1) — [SSRN 1712822](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822) ·
[arXiv 1011.6402](https://ideas.repec.org/p/arx/papers/1011.6402.html)

**Idée :** sur de courts intervalles, la variation de prix est **linéaire** en *order flow imbalance*
(différence entre offre et demande au niveau des meilleures limites), avec une pente **inversement
proportionnelle à la profondeur** du carnet.

**Donnée requise :** carnet L1/L2 avec tailles, et flux d'ordres.
**V14 :** **oui, de première main** — `results/carnet_binance/{BTCUSDT,ETHUSDT}` contient les flux
`depth` **et** `trades`, en NDJSON, depuis le 16/08/2026, 20 Go, 52 fichiers.

**Prédiction falsifiable :** sur les données V14, la régression de `Δmid` sur l'OFI agrégé par
intervalle donne un `R²` **supérieur à celui du déséquilibre de volume seul**, et la pente `λ` décroît
avec la profondeur moyenne sur la fenêtre. Rejet si `R²` est comparable ou si `λ` est stable quelle
que soit la profondeur.

**Ce que ça change dans V14 :** c'est la mesure qui **teste le modèle de coût actuel** — la porte des
12,5 % compare `spread / stop`, en supposant un spread fixe et un impact de taille **nul**. `λ` donne
le terme manquant, et il rend la porte dépendante de la **taille** au lieu du seul spread.

**Porte :** à mesurer. C'est l'entrée n°1 du backlog (doc 4, §6 R1).

---

## H2 — Le déséquilibre des files d'attente au touch prédit le prochain tick

**Papier :** Gould & Bonart (2016), *Queue Imbalance as a One-Tick-Ahead Price Predictor in a Limit
Order Book*, Market Microstructure and Liquidity 2(2) —
[arXiv 1512.03492](https://arxiv.org/html/1512.03492v1)

**Idée :** l'asymétrie des tailles en attente au meilleur bid contre meilleur ask prédit la direction
du **prochain** mouvement du mid-prix ; l'effet est plus fort sur les instruments à **gros tick**
(tick large relativement au prix).

**Donnée requise :** tailles en file au meilleur niveau, tick par tick.
**V14 :** **oui côté crypto** (le flux `depth` Binance porte les tailles par niveau) ; **non côté MT5
retail**, qui n'expose qu'un L1 agrégé — donc l'hypothèse n'est testable que sur la grappe crypto,
ce qui est précisément la grappe qui tourne aujourd'hui.

**Prédiction falsifiable :** sur BTCUSDT/ETHUSDT, le taux de bonne direction à **un tick** d'avance
dépasse 0,5 hors échantillon, et le gain décroît avec le rapport `prix / tick`.

**Ce que ça change dans V14 :** rien sur la décision d'entrée, mais cela donne une mesure honnête de
**l'horizon maximal où un avantage est exploitable** — la réponse chiffrée à « pourquoi un cycle de
10 s rend cet effet inatteignable si sa demi-vie est de 200 ms ». Une réponse négative est un résultat
utile : elle ferme un chantier.

**Porte :** à mesurer, secondaire. Sa valeur est surtout de **borner le bruit de microstructure** du
système.

---

## H3 — La toxicité du flux (VPIN) annonce les épisodes de volatilité — hypothèse **contestée**, donc à tester ici

**Papiers, y compris la dispute :**
- Easley, López de Prado & O'Hara — *Flow Toxicity and Liquidity in a High Frequency World*
  ([SSRN 1695596](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596))
- **Critique :** Andersen & Bondarenko (2014), *VPIN and the flash crash*, Journal of Financial
  Markets 17 — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1386418113000189)
  (« poor predictor of short run volatility », VPIN ne culmine pas **avant** mais **après** le krach)
- **Réplique :** Easley, López de Prado & O'Hara, *VPIN and the Flash Crash: A rejoinder* (2014) —
  [RePEc](https://ideas.repec.org/a/eee/finmar/v17y2014icp47-52.html)

**Idée :** le déséquilibre de volume **échantillonné par le volume** (et non par le temps) est un
indicateur temps réel de toxicité, et donc de volatilité à venir.

**Pourquoi celle-ci compte plus que les autres :** c'est **la seule entrée du catalogue dont la
littérature est en désaccord public**. C'est exactement le type de papier « que presque personne ne
lit » — la dispute est moins citée que l'indicateur — et c'est le meilleur cas d'usage du protocole :
plutôt que de croire l'un des deux camps, on tranche **sur nos données**, où le sens des trades est
**exact** (Binance fournit le côté agressif dans `.trades.ndjson`, alors que la plupart des études
doivent l'inférer — c'est un avantage de mesure réel).

**Donnée requise :** trades horodatés avec volume et côté agressif.
**V14 :** **oui**, dans l'archive crypto.

**Prédiction falsifiable :** VPIN dépasse son 90ᵉ percentile **avant** les pires épisodes de
réalized volatility mesurés sur la même fenêtre, avec un délai d'avance mesurable. Rejet — au sens
d'Andersen-Bondarenko — si l'extrême survient **après** l'épisode, ou si un indicateur trivial (spread
moyen, volatilité réalisée passée) fait aussi bien.

**Le test de départage doit inclure l'indicateur naïf.** C'est la critique centrale d'AB : VPIN ne
bat pas les indicateurs ordinaires. Un test qui omet le comparateur ne prouve rien.

**Ce que ça change dans V14 :** un **état de régime** (calme / élevé / toxique) alimentant la même
décision de veto que les états macro `CLEAR/ELEVATED/BLACKOUT` déjà livrés. Attention à la règle
« aucun état sans lecteur » : l'état doit avoir **un** consommateur identifié avant d'être écrit.

**Porte :** à mesurer, **priorité haute** — coût nul (donnée locale), question tranchable, et le
résultat vaut dans les deux sens.

---

## H4 — Le retour de la première demi-heure prédit celui de la dernière

**Papier :** Gao, Han, Li & Zhou (2018), *Market Intraday Momentum*, Journal of Financial Economics
129(2) — [SSRN 2440866](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2440866)

**Idée :** sur le marché actions US, le retour de la première demi-heure (depuis la clôture de la
veille) prédit significativement celui de la dernière demi-heure, avec une prévisibilité concentrée
en fin de journée ; les volumes et la volatilité suivent un profil en **U**.

**Donnée requise :** barres intraday du marché actions.
**V14 :** **oui** — `US500` et `GER40` sont au catalogue MT5, en unités intraday ; **`VIX.fs`** est
également disponible, ce qui permet de conditionner l'effet au régime de volatilité (prolongement
naturel que le papier lui-même invite à tester).

**Prédiction falsifiable :** sur nos indices et nos barres, la corrélation entre retour de première
et de dernière demi-heure est positive hors échantillon, et **supérieure** sur les journées à fort
`VIX.fs`.

**Ce que ça change dans V14 :** pas une technique d'exécution, mais une **contrainte d'horaire**
(restreindre les entrées à certaines fenêtres, ou moduler l'urgence selon l'heure). C'est le candidat
le moins coûteux à tester et il touche directement le problème réel : **le système ne prend aucune
entrée**.

**Porte :** à mesurer, **priorité haute**, coût de test le plus faible du catalogue.

---

## H5 — L'impact se dissipe : la résilience du carnet impose une forme de calendrier, pas un TWAP uniforme

**Papier :** Obizhaeva & Wang (2013), *Optimal trading strategy and supply/demand dynamics*, Journal
of Financial Markets 16(1) — [ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S1386418112000328) ·
[PDF MIT](http://web.mit.edu/wangj/www/pap/ObizhaevaWang13.pdf)

**Idée :** la profondeur **se reconstitue** après un trade, selon un taux de décroissance de l'impact.
Conséquence : l'exécution optimale n'est pas un découpage uniforme mais typiquement **un bloc
initial puis un calendrier décroissant** — ce qui explique en théorie pourquoi TWAP et VWAP sont
sous-optimaux dès qu'il y a de l'impact.

**Donnée requise :** flux de trades et profondeur reconstituée pour estimer la **demi-vie** de
l'impact.
**V14 :** **oui**, sur l'archive L2 crypto.

**Prédiction falsifiable :** la profondeur consommée par un trade agressif se reconstitue selon une
décroissance exponentielle dont la **demi-vie** est estimable et stable par symbole ; les techniques
séquentielles du catalogue V14 qui se rapprochent d'un calendrier décroissant ont un coût réalisé
inférieur à celui des techniques à découpage uniforme, **sur les quotes archivées** (et non sur la
matrice synthétique, qui surestime le passif — le doc 4 §5 P1-2 le dit déjà).

**Ce que ça change dans V14 :** la **paramétrisation** des techniques séquentielles existantes
(inventaire, urgence, horizon) — c'est-à-dire un axe déjà présent, nourri par une théorie au lieu
d'une intuition. Ce que ça **ne** change **pas** : le nombre de techniques. Ajouter une 18ᵉ technique
issue de H5 serait redondant avec les techniques à échéance de tranche.

**Porte :** à mesurer, priorité moyenne.

---

## H6 — L'urgence est un paramètre d'arbitrage, pas un réglage : la calibrer contre Almgren-Chriss

**Référence :** le compromis impact / risque de dérive, tel que le formalise la littérature
d'exécution optimale (Almgren-Chriss) et tel que le dossier de production §3 le transpose.

**Idée :** l'urgence `κ` arbitre explicitement le coût d'impact contre le risque de ne pas exécuter.
V14 possède déjà un **axe d'urgence** dans le catalogue adaptatif.

**Prédiction falsifiable :** la technique gagnante de l'arène correspond à une urgence **du même ordre
de grandeur** que celle que la théorie prédit pour nos coûts et notre volatilité mesurés. Un écart
d'un facteur important signifie soit que la théorie n'est pas le bon cadre ici, soit que l'axe
d'urgence est mal paramétré — dans les deux cas, une information.

**Ce que ça change dans V14 :** c'est une **validation croisée** du catalogue existant par une théorie
externe. C'est exactement le rôle que la littérature peut jouer sans ajouter une ligne de code.

**Porte :** analyse, coût quasi nul, **à faire en même temps que H3/H4** car elle ne consomme que des
artefacts déjà produits.

---

## Écartés, avec la raison

| Hypothèse | Pourquoi elle est écartée ici |
|---|---|
| **GEX, 0DTE, options** | **Aucun contrat d'option** dans les 149 symboles du courtier (vérifié) ; exige un flux OPRA. Écarté jusqu'à changement de fournisseur de données — voir doc 4, §4. |
| **SOR / dark pools** | Pas d'accès multi-venues sur ce compte. |
| **Carnet MBO, position dans la file** | Donnée indisponible en retail ; l'hypothèse H2 s'en approche **au niveau agrégé**, pas au niveau de la file. |
| **Apprentissage profond sur le carnet (ex. « deep order flow »)** | La donnée existe (20 Go) mais le protocole honnête (cloisonnement train/test, comptage des essais) n'existe pas encore dans le dépôt ; un modèle profond mesuré sur des scénarios déjà vus est un ajustement. **À rouvrir après** la porte 4 du pipeline. |

---

## Priorités, et pourquoi cet ordre

| Rang | Entrée | Coût de test | Ce qu'elle apporte |
|---|---|---|---|
| 1 | **H1** (OFI, impact ∝ déséquilibre/profondeur) | faible — donnée locale | valide ou invalide le **modèle de coût** qui décide aujourd'hui de 84,7 % des refus |
| 2 | **H4** (momentum intraday) | très faible — barres MT5 | une **contrainte d'horaire** testable, sur le problème réel « aucune entrée » |
| 3 | **H3** (VPIN, controverse) | faible — donnée locale | tranche une dispute publiée **sur nos données**, avec le comparateur naïf |
| 4 | **H6** (urgence vs Almgren-Chriss) | quasi nul — artefacts existants | valide le catalogue actuel par une théorie externe |
| 5 | **H5** (résilience) | moyen — archive + quotes | paramètre les techniques séquentielles |
| 6 | **H2** (queue imbalance) | faible mais portée limitée à la crypto | borne l'horizon exploitable ; peut fermer un chantier |

**Ce qui n'est pas dans ce tableau, et qui compte le plus :** la mesure d'attribution d'exécution
(écart à la VWAP réalisée par technique, doc 4 §6 R2). Aucune hypothèse ci-dessus ne peut être jugée
« rentable » sans elle, parce qu'aucune n'est jugée sur **où** l'entrée a été prise, seulement sur
**si** elle l'a été.

---

## Le garde-fou qui doit survivre à tout ce document

1. **Écrire la prédiction avant de mesurer.** Un critère fixé après avoir vu le résultat n'est pas un
   critère.
2. **Compter les essais.** Six hypothèses testées sur les mêmes scénarios, c'est six tirages ; la
   meilleure ligne est en partie de la sélection.
3. **Toujours le comparateur naïf.** Leçon directe de la dispute VPIN : un indicateur qui ne bat pas
   le spread moyen ou la volatilité passée n'apporte rien.
4. **Un jeu tenu à part.** Les 864 scénarios ont servi à choisir la famille actuelle ; sans jeu
   cloisonné, toute amélioration annoncée est un ajustement.
5. **Le cortex lit, il ne mesure pas, et il ne signe pas.** Un LLM peut résumer un papier et en
   extraire l'hypothèse ; il ne produit ni mesure ni promotion.
