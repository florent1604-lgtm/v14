# Mesure d'impact sur le carnet L2 archivé — le modèle de coût confronté à la donnée réelle

**Date :** 13 septembre 2026
**Objet :** confronter à un carnet réel les deux hypothèses sur lesquelles repose la porte de coût
qui refuse aujourd'hui la grande majorité des candidats — **spread fixe** et **impact de taille nul** —
et énoncer ce que cela implique pour le plafond de 12,5 %.
**Outils :** `tools/mesure_impact_carnet.py` (lecture, mesure, rapport) et
`tests/test_mesure_impact_carnet.py` (33 tests, tournent sans l'archive).
**Entrée liée :** hypothèse **H1** de `CATALOGUE_HYPOTHESES_ALPHA_V14_20260913.md`.

> **Le seuil n'est pas déplacé.** Aucun code de production n'est modifié, aucun seuil n'est touché,
> aucun comportement de trading ne change. Ce document produit le chiffre ; la décision reste humaine.

---

## 1. La commande, reproductible telle quelle

```powershell
.venv\Scripts\python.exe tools\mesure_impact_carnet.py `
  --symbole BTCUSDT --code-mt5 BTCUSD `
  --jours 2026-08-16 2026-08-21 2026-09-11 2026-09-12 `
  --minutes 10 --fenetres 3 --pas-cout 25 --horizons 0.1,1,10 `
  --spread-externe "BTCUSD=1.56;ETHUSD=5.03"
```

Même commande avec `--symbole ETHUSDT --code-mt5 ETHUSD`. Les deux ont été exécutées le 13/09/2026 ;
les chiffres ci-dessous en sortent directement, sans retouche.

**Fichiers et fenêtres :** 12 fenêtres par symbole, de 10 minutes chacune, réparties sur **4 journées**
(`2026-08-16`, `2026-08-21`, `2026-09-11`, `2026-09-12`), lues dans
`results/carnet_binance/{BTCUSDT,ETHUSDT}/<jour>.depth.ndjson` et le `.trades.ndjson` correspondant.
Chaque fenêtre démarre sur un instantané de la place et **s'arrête à la première rupture de session** —
une fenêtre a d'ailleurs été tronquée pour cette raison (09-12, 06:44 → 06:49 au lieu de 10 minutes).
Volumes lus : **41 594** observations à 0,1 s, **6 228** à 1 s, **661** à 10 s par symbole.

---

## 2. L'impact est bien linéaire en déséquilibre du flux — et il informe jusqu'à 10 s

`Δmid` en points de base contre l'OFI (déséquilibre du flux de Cont, Kukanov & Stoikov), normalisé par
la profondeur cumulée dans ±5 bp du mid. Le **comparateur naïf** est la variation de taille au sommet,
sans les indicateurs de prix ; le **flux agressif** est le volume signé des trades, son côté fourni par
la place.

### BTCUSDT

| Horizon | n | R² OFI | R² naïf | R² flux | pente | t |
|---|---|---|---|---|---|---|
| 0,1 s | 41 594 | **0,0936** | 0,0002 | 0,0000 | 0,0537 | **65,5** |
| 1 s | 6 228 | **0,3621** | 0,0213 | 0,0000 | 0,3878 | **59,5** |
| 10 s | 661 | **0,5686** | 0,0046 | 0,0003 | 5,8408 | **29,5** |

### ETHUSDT

| Horizon | n | R² OFI | R² naïf | R² flux | pente | t |
|---|---|---|---|---|---|---|
| 0,1 s | 41 577 | **0,0675** | 0,0003 | 0,0000 | 0,0509 | **54,9** |
| 1 s | 6 240 | **0,2859** | 0,0026 | 0,0002 | 0,4557 | **50,0** |
| 10 s | 660 | **0,5826** | 0,0041 | 0,0025 | 5,0642 | **30,3** |

**Trois lectures, dans l'ordre d'importance :**

1. **L'OFI bat le comparateur naïf à tous les horizons testés**, d'un facteur 17 à 470 sur le R², et
   avec un `t` jamais inférieur à 29. La relation est reproduite sur cette archive.
2. **Le flux agressif signé ne prédit rien** (R² ≤ 0,0025 partout). Le côté agresseur est fourni par la
   place, pas inféré : ce n'est donc pas un problème de mesure, c'est que la taille au sommet porte
   l'information et le volume échangé ne la porte pas.
3. **L'information tient jusqu'à 10 s**, c'est-à-dire **à la cadence exacte de la boucle**. C'est
   contraire à l'intuition « la microstructure est trop rapide pour un cycle de 10 s » — sur ces deux
   symboles, elle ne l'est pas. Rien ici ne garantit que le même chiffre tienne sur les CFD MT5.

### La loi en 1/profondeur, elle, n'est pas reproduite

| Symbole | Tercile | n | profondeur méd. | pente | R² | pente × profondeur |
|---|---|---|---|---|---|---|
| BTCUSDT | mince | 13 866 | 3,03 | 0,0393 | 0,0700 | 0,119 |
| BTCUSDT | moyen | 13 864 | 5,26 | 0,1180 | 0,2390 | **0,620** |
| BTCUSDT | épais | 13 864 | 8,58 | 0,0802 | 0,0618 | **0,688** |
| ETHUSDT | mince | 13 862 | 36,66 | 0,0309 | 0,0334 | 1,133 |
| ETHUSDT | moyen | 13 857 | 60,30 | 0,1377 | 0,2625 | **8,305** |
| ETHUSDT | épais | 13 858 | 109,42 | 0,1228 | 0,1248 | **13,434** |

Si la pente était en 1/profondeur, la dernière colonne serait constante. Elle varie de **6×** (BTC) et
**12×** (ETH), et pas dans le bon sens. **Conclusion : ne pas calibrer un seuil sur cette loi avec ces
données.** Deux réserves qui l'expliquent peut-être, et qu'il faut lire avant de conclure au rejet :
la bande de ±5 bp est une convention (la littérature emploie des définitions variées de la
profondeur), et le rejeu à 100 ms ne voit pas l'impact intra-100 ms.

---

## 3. Le coût réel à la taille de la boucle

`notionnel de référence` = `équité × risque_grappe / stop` = `1 865 × 5,7 % / 2 %` = **5 315**.
Les deux termes de droite sont des hypothèses explicites (équité observée ; distance de stop) : elles
doivent être re-fournies pour citer le chiffre.

### BTCUSDT

- spread médian observé : **0,0013 bp** (2 639 instants)
- notionnel en attente au **premier** niveau, deux côtés : **379 524** en médiane

| Notionnel | n | coût médian | coût p90 | coût / demi-spread | aller-retour / spread |
|---|---|---|---|---|---|
| 100 | 2 639 | 0,001 bp | 0,001 bp | 1,00 | 1,00 |
| 1 000 | 2 639 | 0,001 bp | 0,001 bp | 1,00 | 1,00 |
| 5 000 | 2 639 | 0,001 bp | 0,001 bp | 1,00 | 1,00 |
| 10 000 | 2 639 | 0,001 bp | 0,001 bp | 1,00 | 1,00 |
| 50 000 | 2 639 | 0,001 bp | 0,288 bp | 1,01 | 1,01 |
| 200 000 | 2 639 | 0,054 bp | 1,033 bp | **83,86** | **83,86** |

### ETHUSDT

- spread médian observé : **0,0410 bp** (2 625 instants)
- notionnel en attente au premier niveau, deux côtés : **136 809** en médiane

| Notionnel | n | coût médian | coût p90 | coût / demi-spread | aller-retour / spread |
|---|---|---|---|---|---|
| 100 | 2 625 | 0,021 bp | 0,027 bp | 1,01 | 1,01 |
| 5 000 | 2 625 | 0,021 bp | 0,027 bp | 1,01 | 1,01 |
| 10 000 | 2 625 | 0,021 bp | 0,027 bp | 1,01 | 1,01 |
| 50 000 | 2 625 | 0,027 bp | 0,722 bp | 1,29 | 1,29 |
| 200 000 | 2 625 | 0,769 bp | 1,898 bp | **37,54** | **37,54** |

**Lecture.** Un ordre servi dans le premier niveau paie **exactement la moitié du spread** : le ratio
`coût / demi-spread` vaut 1,00. C'est le signe d'un impact **nul**, pas d'une mesure manquante. La
première taille qui commence à payer est **200 000** (BTC) et **50 000** (ETH).

---

## 4. Ce que cela implique pour la règle des 12,5 %

### Le spread fixe : correct, et il ne sous-estime pas

À la taille de référence (**5 315**), le rapport `coût réel d'aller-retour / spread facturé` vaut
**1,00× sur BTCUSDT** et **1,01× sur ETHUSDT**. La taille de référence est respectivement **37,6×** et
**9,4×** sous la taille de bascule. Autrement dit :

> **Sur la place mesurée, l'hypothèse « spread fixe, impact nul » est exacte à la taille que la boucle
> enverrait.** L'erreur de la porte de coût — si erreur il y a — n'est donc **pas** l'impact de taille.

L'intervalle est étroit : 1,00–1,01× de 100 à 10 000 de notionnel, et il ne commence à s'ouvrir qu'à
50 000 (ETH, 1,29×). Aucune des tailles qu'un compte à 1 865 EUR peut atteindre n'entre dans la zone
d'impact.

### Mais la place mesurée n'est pas la place tradée

| Spread | Valeur | Comparaison |
|---|---|---|
| BTCUSDT (Binance spot, mesuré ici) | 0,0013 bp | — |
| **BTCUSD** (MT5, fourni par l'opérateur) | **1,560 bp** | **1 204×** le marché sous-jacent |
| ETHUSDT (Binance spot, mesuré ici) | 0,0410 bp | — |
| **ETHUSD** (MT5, fourni par l'opérateur) | **5,030 bp** | **123×** le marché sous-jacent |

La porte facture le **spread de la place où l'ordre part réellement** — le CFD MT5. Ce spread est deux
à trois ordres de grandeur plus large que le marché sous-jacent, et cet écart vient du courtier, pas
d'une erreur d'arithmétique. **Le modèle est donc conservateur, pas optimiste.**

### Ce que le chiffre ne tranche pas

Le terme qui reste non mesuré est le **prix de remplissage réel sur MT5** : la dérive entre le prix
vu à la décision et le prix obtenu par un ordre au marché. L'archive Binance ne peut rien en dire, et
aucun chiffre de ce document ne doit être étendu à un fill MT5. C'est la mesure suivante à faire, et
elle se prend sur le journal d'exécution, pas sur le carnet.

---

## 5. Ce qui change, et ce qui ne change pas

**Ce qui ne change pas :** aucun seuil, aucun garde-fou, aucune décision de trading. Le plafond de
12,5 % est mesuré juste sur la seule dimension que cette archive peut éclairer.

**Ce qui change :** une question posée depuis plusieurs passes reçoit une réponse chiffrée, et deux
candidats de correction sont écartés sur preuves plutôt que sur intuition — corriger la porte pour
« tenir compte de l'impact » serait corriger quelque chose qui n'existe pas à cette échelle, et
calibrer un seuil sur la loi en 1/profondeur serait calibrer sur une relation que ces données ne
reproduisent pas.

**Ce qui reste ouvert, dans l'ordre de valeur :**

1. **La dérive d'exécution sur MT5** (le terme non mesuré du coût) — sur le journal d'exécution.
2. **La pente d'impact comme caractéristique de symbole** : elle est mesurée, elle est significative,
   et elle distinguerait les symboles mieux que le seul spread. À valider sur un second jeu.
3. **Étendre la fenêtre** : 12 fenêtres sur 4 journées ne couvrent pas les régimes de crise ; ce
   document le dit à chaque exécution plutôt que de laisser croire à une mesure exhaustive.
