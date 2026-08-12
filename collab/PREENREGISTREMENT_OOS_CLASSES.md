# Préenregistrement — test hors échantillon de l'edge par classe d'actif

**Scellé le 2026-08-12T16:01:18.514480+00:00**
**Époque Git au moment du scellement : `fbf14e82fc23fb915f2e37a361bf64ac14bcc7d6`**

Ce document est écrit AVANT d'observer la moindre donnée de la fenêtre de test.
Il fixe la règle de décision pour qu'elle ne puisse pas être choisie après coup
en fonction du résultat. Toute modification ultérieure doit être un nouveau
document, jamais une réécriture de celui-ci.

## 1. Ce qui a été observé en échantillon (et ne compte pas comme preuve)

Audit indépendant `tools/audit_edge_directionnel.py`, 4 000 barres M15,
horizon 200 barres, 20 contrôles appariés par signal (même actif, même heure
UTC, même quartile de volatilité) :

| classe | n signaux | part longue | Δ tous | Δ **même côté** | dérive L/S |
|---|---:|---:|---:|---:|---:|
| énergie | 300 | 65,3 % | +15,30 | **+14,92** | 49,3 / 50,7 |
| métaux | 309 | 53,7 % | +13,45 | **+14,56** | 44,6 / 54,0 |
| indices | 169 | 62,7 % | −0,74 | **−2,62** | 47,9 / 53,7 |
| FX | 118 | 37,3 % | −6,03 | **−6,07** | 47,8 / 49,7 |

Le contrôle d'Hermes alterne les côtés 50/50 : sur un actif en tendance, un
signal majoritairement long pourrait paraître supérieur sans aucun talent de
timing. La colonne **même côté** neutralise cet effet — la dérive figure alors
dans les deux termes et s'annule. **L'avantage énergie/métaux survit** (+14,92
et +14,56 points), et la dérive mesurée est proche de 50/50. Ce n'est donc pas
une prime de tendance.

Exposition réelle de la boucle, sur 518 candidats ENTER journalisés :

| classe | part des candidats | risque cumulé proposé | Δ même côté |
|---|---:|---:|---:|
| **FX** | **59,7 %** | 92,5 pts | **−6,07** |
| indices | 18,0 % | 26,5 pts | −2,62 |
| énergie | 10,0 % | 14,0 pts | +14,92 |
| crypto | 6,4 % | 10,0 pts | non mesuré ici |
| agricole | 3,1 % | 4,5 pts | non mesuré |
| **métaux** | **2,9 %** | 2,5 pts | **+14,56** |

**Le capital va très majoritairement là où le signal est moins bon que le
hasard.** Ce n'est pas un choix : le catalogue du courtier compte 74 paires FX
sur 149 symboles, et le balayage suit le catalogue. Cela suffit à expliquer
une espérance live de −0,3834 R sans invoquer aucun défaut d'exécution.

**Rien de tout cela n'est une preuve.** C'est de l'in-sample : les mêmes barres
ont servi à formuler l'hypothèse et à la tester. Une règle bâtie dessus serait
ajustée au passé.

## 2. Hypothèse préenregistrée

> Sur une fenêtre **future**, le signal V14 conserve un avantage directionnel
> strictement positif en énergie et en métaux, et ne montre aucun avantage en
> FX, en comparaison appariée **de même côté**.

## 3. Règles fixées d'avance

- **Fenêtre** : barres postérieures au scellement ci-dessus. Aucune barre
  antérieure n'est admise.
- **Actifs** : énergie `UKOIL, USOIL, BRENT.fs, WTI.fs, NATGAS.fs` ;
  métaux `XAUUSD, XAGUSD, XPTUSD, COPPER.fs` ;
  FX (témoin négatif) `EURUSD, GBPUSD, USDJPY, AUDUSD`.
  Cette liste est close : aucun actif ne sera ajouté ni retiré après coup.
- **Mesure** : P(+1 R avant −1 R), horizon 200 barres M15, double touche dans
  une même barre comptée comme perte. Δ apparié **même côté**, 20 contrôles
  par signal, appariement actif + heure UTC + quartile de volatilité.
- **Taille minimale** : 200 signaux résolus par classe. En dessous, le
  résultat est déclaré **inconclusif** — pas « prometteur ».
- **Critère de succès** : borne inférieure de l'IC bootstrap 95 % du Δ même
  côté **strictement supérieure à 0** pour énergie ET métaux, séparément.
- **Critère d'échec** : borne inférieure ≤ 0 pour l'une des deux classes.
- **Contrôle de cohérence** : si le témoin FX ressort positif, l'étude entière
  est déclarée non concluante — ce serait le signe que la mesure capte autre
  chose que le signal.
- **Une seule exécution.** Pas de seconde passe avec d'autres paramètres. Une
  étude qu'on relance jusqu'à obtenir le bon chiffre ne mesure plus rien.

## 4. Ce qui est décidé selon l'issue

| Issue | Conséquence |
|---|---|
| Succès | Ouvrir une discussion sur la répartition du balayage par classe, avec un plan de mesure du PnL net — jamais une promotion directe |
| Échec | L'hypothèse est abandonnée. Le déséquilibre FX n'est pas la cause principale, et il faut chercher ailleurs |
| Inconclusif | Prolonger la collecte sans rien changer, jusqu'à la taille requise |

**Dans les trois cas, aucune promotion en argent réel.** L'avantage
directionnel est une mesure de direction brute : il ne dit rien du PnL net
après spread, commission, swap, slippage et gestion. Les critères de promotion
inchangés restent 60 clôtures par cellule, 90 % de coûts exacts, bootstrap et
validation native MT5.

## 5. Ce qui n'est PAS modifié aujourd'hui

Aucun seuil, aucun quorum, aucune garde, aucune restriction d'univers. La
boucle continue de balayer le catalogue entier. Restreindre l'univers
maintenant reviendrait à ajuster la production sur des données déjà vues —
exactement l'erreur que ce préenregistrement existe pour empêcher.
