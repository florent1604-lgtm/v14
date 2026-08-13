# Registre des erreurs — V14

Chaque entrée est une erreur **commise**, pas un risque théorique. Elle porte
son coût mesuré, sa cause racine, et la règle qu'on en tire.

Une erreur sans chiffre n'apprend rien : « le spread était trop large » ne se
vérifie pas, « le spread mangeait 21 % du risque sur EURGBP » se vérifie. Une
erreur sans règle ne se transmet pas : le correctif protège ce cas précis, la
règle protège la famille.

Ordre : le plus coûteux d'abord.

---

## E1 — Généraliser un réglage validé sur deux actifs

**Coût : −318 EUR en une journée. 17 sorties sur 18 au stop, un seul TP.**

Le R:R avait été porté de 2.0 à 3.0 sur la foi d'un forward test **hors
échantillon** — la bonne méthode — mais mené sur **deux actifs seulement**,
XAUUSD et ETHUSD. Il a ensuite été appliqué aux 149 du catalogue.

Avec un objectif à 4,5×ATR et un stop à 1,5×ATR, le prix touche le stop bien
avant la cible sur la plupart des instruments. Le forward avait raison sur
ses deux actifs ; il ne disait rien des 147 autres.

> **Règle.** Un réglage validé sur N actifs vaut pour ces N actifs. L'étendre
> exige de refaire la mesure sur le nouvel univers — c'est exactement la
> faute que le walk-forward sert à éviter, commise à l'étage au-dessus.

---

## E2 — Ouvrir l'univers sans porte de coût

**Coût : inséparable de E1, mais 112 actifs sur 149 avaient un spread
au-dessus du plafond raisonnable.**

Le catalogue complet a été ouvert au balayage pour « accélérer
l'accumulation ». Le filtre de coût existait déjà — écrit dans
`titanium/selection.py`, avec sa note explicative — et n'a **jamais été
lancé**.

Le bot a tradé EURGBP (21 % du risque en spread), USDKRW (26 %), ETH-JPY
(21 %). Un trade dont le cinquième du risque est perdu avant d'exister ne
peut pas gagner : il faut un mouvement supplémentaire rien que pour revenir
à zéro.

> **Règle.** Un garde-fou écrit mais non branché ne protège de rien. Poser la
> porte **dans le chemin** (`tradable_universe`) plutôt que dans un
> classement consultatif : un classement s'ignore, une porte non.

---

## E3 — Compter le spread deux fois *(trouvée par Codex)*

**Coût : aucun trade perdu, mais tous mes chiffres de coût étaient doublés,
et je les ai rapportés comme des faits.**

`spec.spread × spec.point` est **déjà** l'écart ask-bid complet. La formule
le multipliait encore par deux. Chaque actif ressortait deux fois plus cher
que la réalité — et que le backtest, qui compte un demi-spread à l'entrée et
un demi à la sortie.

Conséquence de second ordre, plus grave que l'erreur : j'ai annoncé
« EURGBP à 42 % du risque » quand c'était 21 %. Le diagnostic tenait, les
nombres étaient faux, et ils ont servi à décider.

> **Règle.** Avant d'utiliser une grandeur du courtier, vérifier ce qu'elle
> contient déjà. Et faire concorder toute mesure avec la convention du
> backtest — deux conventions différentes rendent les résultats
> incomparables sans que personne ne s'en aperçoive.

---

## E4 — Un budget de risque aveugle à la corrélation

**Coût : six positions sur le yen, corrélées à 0.69, sous un budget de 6 %
parfaitement respecté.**

`MAX_RISQUE_CUMULE_PCT` comptait le risque total. Il ne voyait pas que six
positions portaient le même sous-jacent. Ce n'étaient pas six paris, c'était
un pari pris six fois — l'exposition réelle valait le triple de l'affichée.

Le danger avait été **écrit en commentaire** le matin même : « EURUSD,
GBPUSD, AUDUSD et NZDUSD longs, c'est quatre fois le même pari contre le
dollar ». Aucun garde-fou n'en avait été tiré.

> **Règle.** Un plafond par classe d'actif n'aurait rien vu — NZDJPY est du
> forex, JPN225 un indice, ETH-JPY de la crypto, et les trois suivaient le
> yen. Le regroupement doit venir des **prix**, pas des étiquettes.

---

## E5 — Archiver un état vivant comme s'il s'agissait d'un journal

**Coût : le contexte d'entrée de 8 positions ouvertes, définitivement perdu.**

Pour « repartir sur un compteur propre », `positions.json` a été archivé en
même temps que `trades.ndjson` et `excursions.ndjson`. Mais ce fichier n'est
pas un journal : il porte l'**état vivant** des positions **encore
ouvertes** — leur `r`, leur clé de contexte, leurs indicateurs figés à
l'ouverture.

Huit positions ont été orphelinées. À leur clôture, il ne restait rien à
journaliser, et le contexte n'existe nulle part ailleurs.

> **Règle.** Un journal se purge, un état vivant non. Avant toute remise à
> zéro, vérifier si le fichier décrit le **passé** ou le **présent**.

---

## E6 — Avaler une exception en silence

**Coût : une heure d'arrêt total du balayage, sans une ligne au journal.**

Un `except Exception: continue` muet dans la boucle. Un `NameError` sur
chaque actif — `get_rates_cache` non importé — a produit zéro évaluation
pendant une heure. Le bot semblait tourner : les tours s'enchaînaient, le
battement était frais, le tableau de bord affichait « ARMÉE ».

Le seul indice était `evalues: 0` dans les statistiques, et il fallait le
chercher.

> **Règle.** Un `continue` muet transforme une panne totale en calme plat.
> Toute exception avalée doit incrémenter un compteur **visible** et
> s'imprimer au moins une fois.

---

## E7 — Une clé de déduplication ancrée sur l'instant du calcul

**Coût : trois positions LONG sur AUDUSD en quelques minutes — trois fois le
risque prévu sur un actif unique, 3,4 % du compte au lieu de 1,14 %.**

La clé d'idempotence valait `symbole:M15:{decided_at}`, où `decided_at` est
`datetime.now()`. Elle changeait à chaque balayage : la déduplication n'a
jamais fonctionné une seule fois.

> **Règle.** Une clé de déduplication s'ancre sur l'**évènement** — ici
> l'horodatage de la dernière barre clôturée — jamais sur l'instant du
> calcul.

---

## E8 — Deux garde-fous corrects formant un interblocage *(à deux avec Codex)*

**Coût : aucun, attrapé en revue. Aurait gelé le bot indéfiniment.**

Deux décisions justes, prises séparément :

- **Bornes de plausibilité du R** (Claude) — elles protègent des stops
  résiduels ou mal normalisés. L'incident observé à `+101 280 739 R`
  venait en réalité d'un prix de sortie GER40 recopié sur une paire FX ;
  il exige donc aussi une validation du ticket et du symbole des deals.
- **Rétention d'état si la journalisation échoue** (Codex) — perdre l'état
  rendrait l'écart MT5/journal irréparable.

Ensemble : un trade au résultat aberrant est refusé **définitivement**, son état est
gardé **indéfiniment**, `JOURNAL_GAP` reste actif, et la boucle bloque toute
nouvelle entrée **pour toujours**. Un seul trade suffisait.

> **Règle.** Deux garde-fous justes peuvent composer un piège. Quand l'un
> refuse et l'autre conserve, il faut distinguer le refus **transitoire**
> (réessai utile) du refus **définitif** (quarantaine et purge).

---

## E9 — Un test qui dépend du jour de la semaine

**Coût : nul, mais le test ne verrouillait rien depuis sa création.**

`test_edge_inconnu_bloque_en_prod` recevait `BLOCK_COST_WEEKEND` au lieu de
`BLOCK_EDGE_UNPROVEN` quand on le lançait un samedi. Il passait du lundi au
vendredi et échouait le week-end.

> **Règle.** Un test dont le résultat dépend de l'horloge ne verrouille rien.
> Neutraliser explicitement les conditions annexes pour que l'assertion porte
> sur ce qu'elle prétend tester.

---

## E10 — Compter les processus au lieu des services

**Coût : trois boucles armées sur le même compte, simultanément.**

Le `python.exe` du venv est un **lanceur-relais** : chaque service apparaît
en deux processus, parent et enfant. Compter les processus voyait un doublon
là où il n'y en avait pas, et masquait les vrais derrière un nombre pair.

Pire : `taskkill /F /PID` tuait le parent et laissait l'enfant **orphelin et
vivant**, qui continuait de trader et comptait ensuite comme une nouvelle
racine. Chaque tentative d'arrêt créait une instance au lieu d'en supprimer
une.

> **Règle.** Compter les **racines** — un processus dont le parent n'est pas
> lui-même du service. Et tuer l'arbre, jamais le seul parent.

---

## E11 — Un entier non signé qui boucle *(signalée par Florent)*

**Coût : 4 lignes sur 6 des excursions archivées contaminées.**

`tick_volume` et `real_volume` arrivent de MT5 en `uint64`. Une soustraction
négative n'y donne pas un nombre négatif : elle **boucle** à
2⁶⁴ = 1,8446744 × 10¹⁹. Chaque baisse de volume produisait cette valeur.

Le danger n'est pas la valeur aberrante, qui se voit. C'est qu'elle
contamine moyenne, écart-type, corrélation — et l'analyse discriminante,
dont la conclusion aurait été dictée par une poignée de 10¹⁹.

> **Règle.** Convertir les entiers du courtier en flottant **à la lecture**,
> pas à chaque point d'usage : un seul endroit à ne pas oublier.

---

## E12 — Un heuristique auto-réalisateur

**Coût : les tracés MT5 invisibles pendant une journée entière.**

`dossier_mql5_files()` choisissait « le dossier de terminal le plus récemment
modifié ». Y écrire notre propre fichier le rendait le plus récent : le choix
se verrouillait sur le premier venu. Depuis qu'une seconde instance MT5
existait, V14 écrivait dans un dossier que le terminal actif ne lisait pas.

Aucune erreur nulle part. Les fichiers étaient bien écrits, le terminal les
cherchait ailleurs.

> **Règle.** Quand MT5 connaît une valeur, la lui demander
> (`terminal_info().data_path`) plutôt que la déduire. Un heuristique dont le
> résultat modifie son propre critère est une boucle, pas une mesure.

---

## E13 — Deux artefacts de mesure, tous deux flatteurs *(12/08/2026)*

**Coût : aucun en euros. Deux conclusions fausses évitées de justesse.**

Le même jour, deux chiffres que j'avais produits se sont révélés être des
artefacts de mon propre code d'analyse. Ils sont réunis ici parce que ce n'est
pas la coïncidence qui compte, c'est le motif commun.

**Le premier.** Mesurant si les breaker blocks ICT pouvaient alimenter le
pilier G4, j'ai défini « le prix est au contact de la zone » comme une
tolérance de 0,2 % du prix. Sur EURUSD, 0,2 % vaut **6 ATR** ; sur BTCUSD,
0,3 ATR. La même ligne de code mesurait donc deux choses différentes selon
l'actif. Résultat annoncé : 68,7 % de passage. Résultat après reprise en
multiples d'ATR : 57,5 %.

**Le second.** Rejouant les trades clos pour chiffrer le coût d'un breakeven
plus bas, je demandais un nombre de barres proportionnel à la **durée** du
trade au lieu de son **ancienneté**. Un trade court ouvert l'avant-veille
était demandé sur 120 barres M15 — trente heures, qui ne remontaient pas
jusqu'à lui. Douze trades sur trente-sept ont été écartés « faute de barres ».

Ces douze n'étaient pas un échantillon neutre : c'étaient les **plus gros
gagnants** (JPN225 +1,331R, EURCAD +2,034R, BTCUSD +0,674R), c'est-à-dire
précisément les trades qu'un breakeven bas peut couper. L'outil annonçait
« 1 gagnant coupé, net +5,431R » au lieu de « 4 gagnants coupés, net
+3,427R » — bénéfice gonflé de 58 %.

Le motif commun n'est pas l'erreur de calcul. C'est que **les deux artefacts
allaient dans le sens de la conclusion attendue**, et qu'aucun des deux n'a
été trouvé par un test : le premier parce qu'un taux de 68,7 % semblait trop
élevé pour un signal de confluence, le second parce qu'un net de +5,4R semblait
trop beau. Dans les deux cas, c'est l'invraisemblance du résultat qui a
déclenché la vérification — pas une alarme.

> **Règle.** Une tolérance exprimée en pourcentage d'un prix ne veut rien dire
> d'un actif à l'autre : la normaliser par l'ATR. Et quand un filtre écarte des
> données, **vérifier ce qu'il écarte avant de lire ce qu'il garde** — une
> exclusion silencieuse corrélée au résultat est indistinguable d'un tri.

---

## Ce qui revient dans presque toutes

Trois motifs, par ordre de fréquence.

**Le silence.** E6, E12 et une partie de E2 partagent la même signature : le
système fonctionne en apparence, aucune erreur n'est levée, et le défaut ne
se voit qu'en cherchant un chiffre précis. Les pannes bruyantes se corrigent
en minutes ; celles-là durent des heures.

**La mesure non refaite.** E1 et E3 viennent d'un chiffre juste appliqué hors
de son domaine de validité. La rigueur du calcul initial donne une fausse
confiance dans son extension.

**Le garde-fou écrit mais pas branché.** E2 et E4 étaient tous deux
documentés en commentaire avant d'être commis. Écrire le danger ne protège
pas ; seule la porte dans le chemin protège.

**L'erreur qui flatte.** E13 ajoute un quatrième motif, plus insidieux que les
trois autres parce qu'il ne ressemble pas à une panne : un défaut de l'outil de
mesure qui pousse le résultat dans la direction espérée. Rien ne le signale —
ni erreur, ni test rouge, ni chiffre absurde. Seule l'habitude de se méfier
d'un résultat *trop favorable* le débusque. C'est le seul motif du registre
contre lequel la discipline vaut mieux que l'outillage.
