# Balayage stop × objectif — XAUUSD, testeur natif MT5

**07/08/2026** · 699 signaux V14 rejoués sur ticks MT5 · 241 jours (2025-12-10 →
2026-08-08) · 30 combinaisons · forward = dernier tiers réservé par MT5.

`InpSLmult` est un **multiplicateur du stop que V14 place déjà** (1.5×ATR).
`1.0` = réglage actuel ; `3.0` = 4.5×ATR.

## Le résultat qui gouverne tout

| | réglage | espérance | PF | trades |
|---|---|---:|---:|---:|
| meilleur **in-sample** | SL 3.0 × RR 3.5 | **+0.506 R** | 1.81 | 51 |
| *le même, en forward* | | **0.000 R** | **0.86** | 14 |
| meilleur **forward** | SL 1.5 × RR 3.5 | +0.472 R | 1.71 | 71 |

**Optimiser sur l'échantillon aurait sélectionné exactement le pire réglage.**
C'est la démonstration la plus nette qu'on ait produite de l'utilité du
forward — et l'explication rétrospective de V12 : PF 2.31 en Python, PF 0.90
au testeur natif.

## La structure du paysage

| stop | forward | lecture |
|---|---|---|
| **1.0 – 1.5** | +0.29 à **+0.47 R**, PF 1.5–1.9, 57–194 trades | zone solide |
| 2.0 | +0.03 à +0.30, dégradé | frontière |
| **2.5 – 3.0** | 0.00 à **−0.19 R**, PF 0.71–0.95 | effondrement |

⚠️ **Les `0.000` exacts ne sont pas des espérances nulles mesurées** : ce sont
des passes **annulées** par `OnTester()` pour échantillon sous 30 trades. Une
lecture naïve les prendrait pour « neutre » alors qu'elles disent « on ne sait
pas ». Les valeurs négatives (SL 3.0 × RR 2.0 : −0.190 sur 35 trades) sont,
elles, mesurées.

## Ce que ça dit de la thèse « le SL est trop court »

**Vraie modérément, fausse dans sa version forte.**

- Élargir **jusqu'à 1.5×** le stop actuel : soutenu (+0.463 contre +0.294 à
  RR 2.0).
- Élargir **au-delà de 2×** : détruit l'edge, sans exception.

Et surtout, **le vrai levier n'est pas le stop mais l'objectif**. À stop
inchangé, porter le R:R de 2.0 à 3.5 fait passer le forward de **+0.294 à
+0.466 R**. Le même gain que l'élargissement du stop, sans le risque.

## Le mécanisme derrière l'effondrement

Le nombre de trades s'effondre avec la largeur du stop : 344 → 79 in-sample
entre SL 1.0 et SL 3.0, à R:R égal. Ce n'est pas le hasard — l'EA ne tient
qu'une position à la fois, et un stop large **garde le créneau occupé plus
longtemps**, donc écarte les signaux suivants.

C'est le même phénomène que celui qui a fait échouer le stop temporel, dans
l'autre sens : là il libérait le créneau trop tôt et provoquait des réentrées
plus mauvaises ; ici il le bloque. **La contrainte de créneau est un
paramètre de stratégie déguisé**, et elle contamine toute mesure qui la
laisse implicite.

## Ce que cette mesure ne dit pas

1. **Un seul actif**, et c'est celui qui était déjà le plus stable. Rien ne
   se transpose sans revérification.
2. **Entrées figées.** Les 699 signaux ont été *choisis* par V14 avec un stop
   à 1.5×ATR. Les rejouer avec un stop différent mesure « ces entrées, mieux
   gérées », pas « les entrées qu'on prendrait avec ce réglage ». Le biais
   avait déjà retourné les conclusions du stop temporel entre mesure appariée
   et rejeu complet.
3. **Le forward est meilleur que l'in-sample** sur toute la zone SL 1.0–1.5.
   C'est anormal et probablement un effet de régime : le dernier tiers
   (mai→août 2026) a bien tendu sur l'or. À ne pas lire comme une preuve de
   robustesse.
4. Modèle **OHLC M1**, pas ticks réels — plus rapide, moins fidèle sur les
   mèches. À refaire en `--modele 4` avant toute décision.

## Réplication sur ETHUSD — le résultat tient

Même grille, même protocole, actif indépendant.

| | réglage | espérance | PF | trades |
|---|---|---:|---:|---:|
| meilleur **in-sample** | SL 2.0 × RR 2.0 | +0.384 R | 1.72 | 132 |
| *le même, en forward* | | **−0.071 R** | **0.89** | 74 |

**Le même effondrement**, dans un coin de grille différent. Ce n'est donc pas
une particularité de l'or : optimiser sur l'échantillon sélectionne un
réglage qui perd hors échantillon, quelle que soit la case gagnante.

### L'effet du R:R, à stop inchangé, en forward

| R:R | XAUUSD | ETHUSD |
|---|---:|---:|
| 1.5 | +0.165 | +0.079 |
| 2.0 ← **réglage actuel** | +0.294 | +0.098 |
| 2.5 | +0.356 | +0.138 |
| 3.0 | +0.407 | +0.192 |
| **3.5** | **+0.466** | **+0.256** |
| 4.0 | +0.462 | +0.062 |

**Monotone jusqu'à 3.5 sur les deux actifs, puis décrochage à 4.0.** Le gain
de 2.0 → 3.5 vaut +0.172 R sur l'or et +0.158 R sur l'ether — deux mesures
indépendantes qui concordent à 0.014 R près.

C'est le résultat le plus solide produit jusqu'ici : répliqué, monotone, sur
125 à 211 trades par cellule, et il porte sur le paramètre le moins risqué à
changer.

### La réserve qui reste

Le nombre de trades décroît quand le R:R monte (211 → 125 sur ETHUSD). Un
objectif lointain **tient le créneau plus longtemps** et écarte des signaux —
le même mécanisme d'occupation qu'ailleurs dans ce rapport. Une part du gain
mesuré pourrait donc venir de « trader moins » plutôt que de « viser mieux ».
Séparer les deux demande de relâcher la contrainte d'une position à la fois.

**Le forward optimum d'ETHUSD (SL 2.5 × RR 2.5, +0.448 R) porte sur 39
trades** et tombe dans un coin différent de celui de XAUUSD : c'est du bruit,
pas un réglage. Seul l'effet monotone du R:R mérite qu'on s'y fie.

## Suite

- rejouer en ticks réels (`--modele 4`)
- étendre à ETHUSD, EURUSD, AUDUSD — dont deux à espérance négative, qui
  serviront de contrôle
- étapes 2 et 3 (stop temporel côté MT5, breakeven/trailing) **une fois** le
  R:R tranché, jamais en croisé
- mesurer avec la contrainte de créneau relâchée, pour séparer l'effet du
  réglage de l'effet d'occupation
