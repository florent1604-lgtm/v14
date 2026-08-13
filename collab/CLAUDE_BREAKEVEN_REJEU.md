# Coût réel des gagnants coupés par un breakeven plus bas

**Tâche Prime `65541466`** · Claude · 12/08/2026
39 clôtures rejouées · aucun seuil modifié · aucun ordre · lecture seule.

---

## Le chiffre demandé

> « Le contrefactuel ne débite pas les gagnants qu'un breakeven plus bas aurait
> coupés. Sans lui, la table des seuils reste une borne supérieure et ne peut
> justifier aucun changement. »

Le voici, seuil par seuil :

| BE armé à | stops évités | **gagnants coupés** | net |
|---|---|---|---|
| 0.30 | 7 · **+7,138R** | 4 · **−3,710R** | **+3,427R** |
| 0.40 | 3 · +2,938R | 3 · −3,104R | −0,167R |
| 0.50 | 3 · +2,938R | 2 · −2,694R | +0,244R |
| 0.60 | 1 · +0,838R | 0 · −0,000R | +0,838R |
| 0.70 | 0 | 0 | 0,000R |

Les quatre gagnants coupés à 0.30, nommément :

```
JPN225    +2.000R -> +0.050R
AUDUSD    +0.461R -> +0.050R
EURJPY    +0.656R -> +0.050R
EURJPY    +0.794R -> +0.050R
```

---

## Ce que ça change par rapport à la table existante

`tools/contrefactuel_breakeven.py` donnait une progression **monotone** — plus
le seuil baisse, meilleure est l'espérance :

```
0.30  -0.1679     0.50  -0.2241     0.70  -0.3075
0.40  -0.2241     0.60  -0.2519     0.80  -0.3654  <- actuel
```

Le rejeu montre que c'est faux. Une fois les gagnants coupés débités :

```
        défavorable   favorable
0.30      -0.2073      -0.1513
0.40      -0.2994      -0.2704     <- PIRE que 0.50 et 0.60
0.50      -0.2889      -0.2599
0.60      -0.2737      -0.2946
0.70      -0.2952      -0.3161
0.80      -0.2952      -0.3161     <- actuel
```

**0.40 est le pire des candidats**, et il était présenté comme meilleur que
0.60 et 0.70. La raison est mécanique : à 0.40 le breakeven s'arme assez tôt
pour couper JPN225, AUDUSD et EURJPY (−3,104R) mais pas assez tôt pour sauver
les stops que 0.30 sauve (+2,938R contre +7,138R). Il paie le coût sans
toucher le bénéfice.

Une table monotone invitait à descendre progressivement. La vraie forme dit le
contraire : **un pas intermédiaire est le plus mauvais choix possible.**

---

## Robustesse — un net positif porté par un seul trade n'est pas un résultat

```
BE 0.30 : net  +3.427R  ·  sans le plus influent  +2.377R  ·  tient
BE 0.40 : net  -0.167R  ·  sans le plus influent  -1.217R  ·  tient
BE 0.50 : net  +0.244R  ·  sans le plus influent  -0.806R  ·  NE TIENT PAS
BE 0.60 : net  +0.838R  ·  sans le plus influent  +0.000R  ·  NE TIENT PAS
```

0.50 et 0.60 ne survivent pas au retrait d'un seul trade : leur signe s'inverse.
Seul **0.30 reste positif** sans son trade le plus influent.

---

## Méthode

Les barres M15 réellement cotées entre l'ouverture et la sortie de chaque trade
sont repassées par **`decide_new_sl`, la fonction du moteur de production** —
pas une réimplémentation. Le seul paramètre qui change d'un scénario à l'autre
est `breakeven_r`. Tout écart mesuré est donc imputable au seuil.

**L'ambiguïté intra-barre est publiée, pas dissimulée.** Une barre M15 donne
O/H/L/C sans dire lequel du haut ou du bas a été touché en premier. Les deux
ordres sont rejoués et l'intervalle est affiché. Pour 0.30, la conclusion tient
même en comparant sa borne basse (−0,2073R) à la borne haute de l'actuel
(−0,3161R) : elle ne dépend pas de l'hypothèse.

---

## Trois réserves, dont une qui limite l'usage du chiffre

**1. Le rejeu est optimiste d'environ 0,06R par trade.** Au seuil réellement
appliqué (0.80), il rend −0,2952R / −0,3161R quand le journal dit −0,3707R.
L'écart vient de ce que le rejeu ne modélise ni le spread, ni les frais de
portage, ni la granularité infra-M15 des déclenchements. **Les niveaux absolus
sont donc à ignorer** ; seules les *différences* entre seuils sont
exploitables, puisqu'elles sont calculées sous des hypothèses identiques.

**2. 39 trades.** Le net de +3,427R à 0.30 vaut +0,088R par trade. C'est du
même ordre que l'écart-type de l'espérance sur un échantillon de cette taille.
Le résultat oriente ; il ne conclut pas.

**3. Ces 39 clôtures viennent d'une époque de gestion unique** — toutes ont été
gérées à 0.80. Un breakeven à 0.30 changerait aussi *quand* le trailing démarre
sur les trades survivants, effet que le rejeu capture mais que la population
observée n'a jamais subi.

---

## Erreur commise, trouvée et corrigée en cours d'analyse

La première exécution n'a rejoué que **25 trades sur 37**, écartant les douze
autres « faute de barres ». Cause : `barres_du_trade` demandait un nombre de
barres proportionnel à la **durée** du trade, pas à son **ancienneté**. Un
trade court ouvert l'avant-veille était demandé sur 120 barres M15 — trente
heures, qui ne remontaient pas jusqu'à lui.

Les douze écartés n'étaient pas un échantillon neutre : c'étaient **les plus
gros gagnants** (JPN225 +1,331R, EURCAD +2,034R, BTCUSD +0,674R…). Précisément
les trades qu'un breakeven bas peut couper. Avec eux absents, l'outil annonçait
« BE 0.30 : 1 gagnant coupé, net +5,431R » — au lieu de 4 gagnants coupés et
+3,427R. Le biais gonflait le bénéfice de 58 %.

C'est la seconde fois aujourd'hui qu'un de mes chiffres est un artefact de mon
propre code (la première : une tolérance « au contact » en pourcentage du prix,
valant 6 ATR sur EURUSD). Le point commun : dans les deux cas l'artefact
**allait dans le sens de la conclusion** et n'a été trouvé qu'en interrogeant
un nombre qui semblait trop beau. Consigné pour `docs/LECONS.md`.

Verrouillé par `test_trade_court_mais_ancien_est_rejouable`.

---

## Recommandation

**Je ne propose pas de changer le seuil**, et la décision appartient à Prime.

Si Prime veut agir, la seule option que la mesure soutient est **0.30**, jamais
un pas intermédiaire : 0.40 est mesuré *négatif*, 0.50 et 0.60 ne survivent pas
au retrait d'un trade. Descendre « prudemment » de 0.80 à 0.60 puis 0.40 serait
le pire chemin possible.

Avant tout changement, deux conditions me paraissent nécessaires :

1. **doubler l'échantillon** — 39 clôtures pour un écart de 0,088R/trade ne
   permettent pas de distinguer l'effet du bruit ;
2. **corriger l'écart de fidélité** en modélisant le spread dans le rejeu, pour
   que les niveaux absolus deviennent lisibles et pas seulement les écarts.

---

## Livrables

| fichier | rôle |
|---|---|
| `tools/rejeu_breakeven.py` | le rejeu barre à barre, deux ordres intra-barre, jackknife |
| `tests/test_rejeu_breakeven.py` | 17 tests · fenêtre de barres, priorité du stop, ordres, chargement |
| `collab/CLAUDE_BREAKEVEN_REJEU.md` | ce document |

`tools/contrefactuel_breakeven.py` est conservé tel quel : il reste juste dans
ce qu'il annonce (une borne supérieure) et son avertissement était exact.
