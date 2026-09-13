# Momentum intraday H4 — l'effet testé sur les seuls symboles que la porte de coût laisse passer

**Date :** 13 septembre 2026
**Objet :** tester l'hypothèse **H4** du catalogue — Gao, Han, Li & Zhou (2018), *Market Intraday
Momentum* : le retour de la **première** demi-heure de séance prédit celui de la **dernière** —
sur les symboles que la porte de coût laisse réellement passer, avec le comparateur naïf dans le
même test, une séparation dedans/dehors et le coût **mesuré** du symbole.
**Outils :** `tools/mesure_momentum_intraday.py` (lecture, mesure, rapport, artefact JSON) et
`tests/test_mesure_momentum_intraday.py` (**30 tests**, tournent **sans le terminal MT5** et sans
l'archive de barres).
**Entrée liée :** hypothèse **H4** de `CATALOGUE_HYPOTHESES_ALPHA_V14_20260913.md`.

> **Le seuil n'est pas déplacé.** Aucun code de production n'est modifié, aucun seuil n'est
> touché, aucun comportement de trading ne change : ni la boucle, ni les plafonds 5,7 / 17,1,
> ni le contrat de cycle de vie, ni le gate de coût. Ce document produit le chiffre ; la
> décision reste humaine.

---

## 1. La commande, reproductible telle quelle

```powershell
.venv\Scripts\python.exe tools\mesure_momentum_intraday.py `
  --symboles US500 GER40 `
  --timeframe M5 `
  --archive results\barres `
  --json mesures_momentum.json
```

Exécutée le 13/09/2026. Les chiffres de ce document sortent directement de l'artefact JSON
produit par cette commande, sans recopie à la main.

**Fichiers et fenêtre :**

| Source | Contenu |
|---|---|
| `results/barres/M5/US500.parquet` | barres M5 US500, colonnes `open/high/low/close/tick_volume/spread/real_volume` |
| `results/barres/M5/GER40.parquet` | barres M5 GER40, mêmes colonnes |
| `results/barres/_specifications.json` | `point` du symbole, sans lequel le coût n'est pas inventé |
| `results/barres/_metadonnees.json` | contrat de l'archive (schéma 2) |

- **US500** — 358 séances, du **2025-03-19** au **2026-08-19**, début de séance modal **13:30 UTC**, `point = 0.01`.
- **GER40** — 372 séances, du **2025-03-03** au **2026-08-19**, début de séance modal **11:00 UTC**, `point = 0.01`.

L'archive a été écrite le 2026-08-19 ; aucune barre postérieure n'existe. La **famille de tests** compte 12 membres, d'où un seuil corrigé de Bonferroni **|t| > 2.865** au lieu de 1,960 — annoncé dans l'en-tête **et** appliqué au verdict.

---

## 2. Résultat — l'effet directionnel n'est pas démontré

**US500** : hors échantillon, la pente du retour de clôture sur celui d'ouverture vaut **+0,0444** pour un R² de **0,02746** et un t de **+1,73** — sous le seuil de famille de 2,865. L'écart des deux groupes y vaut **+4,110 bp** (t = +1,16, n = 65/43) et sa borne basse à 2σ est **-3,389 bp** : le signe n'est donc pas établi.

**GER40** : hors échantillon, la pente du retour de clôture sur celui d'ouverture vaut **-0,0006** pour un R² de **0,00002** et un t de **-0,05** — sous le seuil de famille de 2,865. L'écart des deux groupes y vaut **+1,284 bp** (t = +0,53, n = 64/48) et sa borne basse à 2σ est **-3,825 bp** : le signe n'est donc pas établi.

Un détail qui parle : l'écart est **-1,443 bp et -2,127 bp** sur l'échantillon entier, donc du signe **opposé** à celui qu'il prend hors échantillon (+4,110 bp et +1,284 bp). Un effet qui change de signe quand on change de moitié d'échantillon ressemble à du bruit, pas à un avantage.

Le détail par symbole, avec les comparateurs naïfs dans le **même** test :

### US500

Séance détectée **au volume** : début modal **13:30 UTC**, pas 5 min, durée 390 min. **358 séances mesurables** du 2025-03-19 au 2026-08-19, 1 journée(s) écartée(s) pour couverture insuffisante, 78 barres par séance (médiane).

Spread lu dans l'archive : médiane **0,437 bp**, p90 **0,868 bp** sur 358 séances. Découpe : 250 séances dedans / 108 dehors.

**DEHORS — l'échantillon qui tranche (US500)**

| prédicteur | n | R² | pente | t |
|---|---|---|---|---|
| **ouverture (H4)** — le prédicteur du papier | 108 | 0,02746 | +0,0444 | +1,73 |
| ouverture session (variante hors gap) | 108 | 0,00377 | -0,0428 | -0,63 |
| clôture de la veille | 108 | 0,00295 | -0,0543 | -0,56 |
| milieu de séance | 108 | 0,00073 | -0,0085 | -0,28 |
| |ouverture| — comparateur de magnitude | 108 | 0,03179 | +0,0541 | +1,87 |
| |ouverture session| — comparateur de magnitude | 108 | 0,11000 | +0,2498 | +3,62 |
| volatilité passée — comparateur de magnitude | 108 | 0,00854 | -0,1644 | -0,96 |

Échantillon entier (US500) — pour situer, pas pour conclure

| prédicteur | n | R² | pente | t |
|---|---|---|---|---|
| **ouverture (H4)** — le prédicteur du papier | 358 | 0,00177 | -0,0122 | -0,80 |
| ouverture session (variante hors gap) | 358 | 0,00001 | -0,0027 | -0,06 |
| clôture de la veille | 357 | 0,00402 | -0,0634 | -1,20 |
| milieu de séance | 358 | 0,00350 | +0,0157 | +1,12 |
| |ouverture| — comparateur de magnitude | 358 | 0,12385 | +0,1019 | +7,09 |
| |ouverture session| — comparateur de magnitude | 358 | 0,14541 | +0,3194 | +7,78 |
| volatilité passée — comparateur de magnitude | 355 | 0,09536 | +0,4454 | +6,10 |

**Effet directionnel, échantillon entier (US500)**

- écart des deux groupes : **-1,443 bp** (± 2,244, t = -0,64, n = 194/164)
- variante hors gap : -0,047 bp (± 2,206, t = -0,02)
- coût d'aller-retour mesuré du symbole : 0,437 bp
- **net après coût : -1,880 bp**, borne basse à 2σ **-6,367 bp**
- verdict : **INDÉCIS au seuil de famille (|t| < 2.87).**

**Effet directionnel, hors échantillon (US500)**

- écart des deux groupes : **+4,110 bp** (± 3,532, t = +1,16, n = 65/43)
- variante hors gap : -1,556 bp (± 3,255, t = -0,48)
- coût d'aller-retour mesuré du symbole : 0,437 bp
- **net après coût : +3,674 bp**, borne basse à 2σ **-3,389 bp**
- verdict : **INDÉCIS au seuil de famille (|t| < 2.87).**

### GER40

Séance détectée **au volume** : début modal **11:00 UTC**, pas 5 min, durée 390 min. **372 séances mesurables** du 2025-03-03 au 2026-08-19, 1 journée(s) écartée(s) pour couverture insuffisante, 78 barres par séance (médiane).

Spread lu dans l'archive : médiane **0,304 bp**, p90 **0,579 bp** sur 372 séances. Découpe : 260 séances dedans / 112 dehors.

**DEHORS — l'échantillon qui tranche (GER40)**

| prédicteur | n | R² | pente | t |
|---|---|---|---|---|
| **ouverture (H4)** — le prédicteur du papier | 112 | 0,00002 | -0,0006 | -0,05 |
| ouverture session (variante hors gap) | 112 | 0,03793 | +0,0520 | +2,08 |
| clôture de la veille | 112 | 0,01070 | -0,1034 | -1,09 |
| milieu de séance | 112 | 0,00476 | -0,0137 | -0,73 |
| |ouverture| — comparateur de magnitude | 112 | 0,02932 | +0,0209 | +1,82 |
| |ouverture session| — comparateur de magnitude | 112 | 0,12247 | +0,0669 | +3,92 |
| volatilité passée — comparateur de magnitude | 112 | 0,02368 | +0,2131 | +1,63 |

Échantillon entier (GER40) — pour situer, pas pour conclure

| prédicteur | n | R² | pente | t |
|---|---|---|---|---|
| **ouverture (H4)** — le prédicteur du papier | 372 | 0,02361 | -0,0253 | -2,99 |
| ouverture session (variante hors gap) | 372 | 0,00391 | +0,0307 | +1,21 |
| clôture de la veille | 371 | 0,00059 | -0,0241 | -0,47 |
| milieu de séance | 372 | 0,00628 | +0,0163 | +1,53 |
| |ouverture| — comparateur de magnitude | 372 | 0,09733 | +0,0523 | +6,32 |
| |ouverture session| — comparateur de magnitude | 372 | 0,04431 | +0,0840 | +4,14 |
| volatilité passée — comparateur de magnitude | 369 | 0,08916 | +0,4111 | +5,99 |

**Effet directionnel, échantillon entier (GER40)**

- écart des deux groupes : **-2,127 bp** (± 1,589, t = -1,34, n = 203/169)
- variante hors gap : -2,376 bp (± 1,617, t = -1,47)
- coût d'aller-retour mesuré du symbole : 0,304 bp
- **net après coût : -2,431 bp**, borne basse à 2σ **-5,610 bp**
- verdict : **INDÉCIS au seuil de famille (|t| < 2.87).**

**Effet directionnel, hors échantillon (GER40)**

- écart des deux groupes : **+1,284 bp** (± 2,403, t = +0,53, n = 64/48)
- variante hors gap : -0,194 bp (± 2,406, t = -0,08)
- coût d'aller-retour mesuré du symbole : 0,304 bp
- **net après coût : +0,980 bp**, borne basse à 2σ **-3,825 bp**
- verdict : **INDÉCIS au seuil de famille (|t| < 2.87).**

---

## 3. Ce qui survit, et ce que cela vaut

Deux choses survivent au seuil corrigé, et aucune n'est directionnelle :

| symbole | prédicteur | R² hors éch. | pente | t hors éch. |
|---|---|---|---|---|
| US500 | \|ouverture session\| sur \|retour de clôture\| | 0,11000 | +0,2498 | +3,62 |
| GER40 | \|ouverture session\| sur \|retour de clôture\| | 0,12247 | +0,0669 | +3,92 |

C'est la **magnitude** de la première demi-heure qui annonce la **magnitude** de la dernière, pas son signe : la volatilité se regroupe, la direction non. Le comparateur de volatilité passée, lui, ne suffit pas à l'expliquer (US500 t = -0,96 ; GER40 t = +1,63).

**Pourquoi ce n'est pas exploitable ici.** Un effet de magnitude ne se prend pas avec un ordre directionnel, et l'expression qui le capturerait — une position de volatilité, achat simultané d'un call et d'un put — exige une chaîne d'options. Le catalogue du courtier compte **149 symboles et aucun contrat d'option** (constat déjà posé dans le dossier de production). La magnitude est donc un fait mesuré, pas un signal exécutable **avec ce courtier**.

---

## 4. Le coût, et ce qu'il implique pour la règle des 12,5 %

| symbole | coût aller-retour mesuré (médiane) | p90 | écart directionnel hors éch. | borne basse à 2σ |
|---|---|---|---|---|
| US500 | **0,437 bp** | 0,868 bp | +4,110 bp | **-3,389 bp** |
| GER40 | **0,304 bp** | 0,579 bp | +1,284 bp | **-3,825 bp** |

Le coût des indices est **deux à trois ordres de grandeur** sous celui de la crypto mesurée sur Binance (BTCUSD 1,56 bp, ETHUSD 5,03 bp — `MESURE_IMPACT_CARNET_20260913.md`). La conséquence est à double tranchant, et il faut la dire dans les deux sens.

1. **Le coût n'est pas ce qui bloque ici.** 0,437 bp et 0,304 bp d'aller-retour sont dérisoires au regard des écarts mesurés : sur ces symboles, l'argument « le marché est trop cher pour ce plafond » ne tient pas. C'est pourtant lui qui domine les refus du tunnel sur la crypto — un relevé du journal de la boucle, **que ce document ne re-mesure pas** et dont il ne cite donc aucun chiffre.
2. **Mais un coût faible ne crée pas un avantage.** L'écart hors échantillon est positif (+4,110 bp et +1,284 bp), pourtant sa borne basse à 2σ reste négative (-3,389 bp et -3,825 bp) : la donnée ne permet pas d'affirmer que l'avantage est là, seulement de constater que le coût ne suffit pas à le réfuter.

**Conséquence pour la porte de coût : aucune.** La porte facture le spread du CFD pour décider si un candidat est portable ; ici le spread des deux indices est si bas que le plafond de 12,5 % du stop n'est pas le facteur limitant sur ces symboles. Rien ne bouge : le seuil reste une décision humaine, et ce document ne le déplace pas.

---

## 5. Ce que cette mesure ne dit pas

- **Le début de séance est détecté au volume, pas par un calendrier de place.** Pour **US500**, le mode tombe à 13:30 UTC, soit 09:30 à New York : c'est bien la définition du papier. Pour **GER40**, le mode tombe à **11:00 UTC**, qui n'est **pas** l'ouverture de Xetra : la fenêtre d'ouverture n'est donc pas, pour ce symbole, celle que le papier étudie. Le résultat GER40 se lit comme « cette fenêtre de volume-là n'annonce rien », pas comme un test fidèle de l'ouverture du DAX.
- **Le test porte sur une seule fenêtre d'observation** (mars 2025 → août 2026, soit 358 et 372 séances). Un régime absent de cette fenêtre ne peut pas y apparaître.
- **Le prix d'exécution réel n'est pas dans l'archive.** Le retour de dernière demi-heure est mesuré sur des barres de clôture ; la dérive entre le prix vu à la décision et le prix obtenu par un ordre au marché n'est pas mesurable ici.
- **Le coût est celui de l'archive** (colonne `spread`, convertie par le `point` du symbole), soit un aller-retour au marché. Le slippage de taille réelle n'y est pas.
- **Le proxy n'est pas le symbole MT5.** Les barres viennent de l'archive du dépôt ; les spécifications (`volume_min`, `trade_contract_size`) confirment que ce sont les CFD du courtier, mais une mesure de prix reste une mesure de prix : elle ne prouve pas qu'un ordre aurait été rempli à ce prix.
- **Aucune conclusion n'est posée sur les autres symboles.** Le test ne porte que sur les deux symboles nommés ; son extension exige de relancer la commande avec d'autres noms.

---

## 6. Verdict

**Pas d'effet directionnel exploitable, et le coût n'y est pour rien.** L'hypothèse H4, testée sur les seuls symboles que la porte de coût laisse passer, ne franchit le seuil corrigé sur aucun des deux — ni en US500, ni en GER40. Hors échantillon, l'écart est du signe de l'effet cherché mais sa borne basse à 2σ le couvre de zéro, et sur l'échantillon entier il s'inverse. Rien là-dedans ne soutient une règle de trading.

Ce qui **est** établi sur cette fenêtre, et qui est d'une autre nature : la **magnitude** de la première demi-heure de séance annonce celle de la dernière, au-delà du seuil corrigé et sur les deux symboles (US500 R² = 0,11000, t = +3,62 ; GER40 R² = 0,12247, t = +3,92). C'est du regroupement de volatilité, pas un avantage directionnel — et ce n'est pas tradable ici, faute de chaîne d'options chez ce courtier.

C'est un résultat **négatif utile** : il ferme la piste la moins coûteuse à tester du catalogue, il est reproductible par une commande du dépôt, et il laisse la porte de coût inchangée. La prochaine piste du catalogue à instruire — H1, l'impact linéaire en déséquilibre du flux — est déjà mesurée et se trouve dans `MESURE_IMPACT_CARNET_20260913.md`.
