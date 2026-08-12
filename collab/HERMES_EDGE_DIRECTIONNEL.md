# Audit Hermes — edge directionnel du signal d'entrée

Date : 2026-08-12  
Mission : `f6d05cca-e8de-4ddc-8cfb-24727f517e3c`  
Rôle : Hermes C1 consultatif, mesure read-only  
Époque Git : `65bb599eaca01ee44f252692b13dbef41f933968` (`master`)  
Verdict : **edge directionnel hétérogène ; NO-GO réglage global ou promotion ; GO validation OOS ciblée par classe**

## 1. Question testée

Avant tout réglage de sortie, le signal d'entrée bat-il un tirage aléatoire comparable ?

Mesure principale : probabilité que le prix touche **+1 R avant -1 R** après l'entrée. Les excursions MFE/MAE sont mesurées sur le même horizon. Le contrôle est tiré sur le même actif, à la même heure UTC et dans le même quartile de volatilité, avec directions longues/courtes équilibrées.

Aucun seuil, garde, ordre, service ou configuration de trading n'a été modifié.

## 2. Protocole reproductible

Outil : `tools/edge_directionnel.py`  
Tests : `tests/test_edge_directionnel.py`  
Artefact agrégé : `results/edge_directionnel_65bb599.json`

Paramètres :

- 12 actifs, regroupés en trois lots disjoints ;
- 4 000 barres M15 et HTF H4 par actif ;
- chaîne de signal V14 réelle via `titanium.backtest.rejouer` ;
- évaluation d'une barre sur quatre (`--step 4`) ;
- horizon de 200 barres M15 ;
- risque unitaire : 1,5 ATR, identique à la chaîne rejouée ;
- 20 contrôles par signal ;
- contrôle apparié sur actif + heure UTC + quartile de volatilité ;
- directions de contrôle équilibrées 50/50 ;
- les barres d'entrée signal sont exclues des candidats aléatoires ;
- double touche +1 R/-1 R dans une même barre comptée comme -1 R, règle conservatrice ;
- bootstrap apparié au niveau du **signal**, et non pseudo-réplication des 20 contrôles ;
- graines SHA-256 stables entre processus.

Commandes exécutées :

```text
.venv/Scripts/python.exe tools/edge_directionnel.py EURUSD AUDUSD USDJPY USDCAD --bars 4000 --step 4 --draws 20 --horizon 200 --output results/edge_directionnel_fx.json
.venv/Scripts/python.exe tools/edge_directionnel.py XAUUSD XAGUSD BTCUSD ETHUSD --bars 4000 --step 4 --draws 20 --horizon 200 --output results/edge_directionnel_alt.json
.venv/Scripts/python.exe tools/edge_directionnel.py NAS100.fs US500 UKOIL BRENT.fs --bars 4000 --step 4 --draws 20 --horizon 200 --output results/edge_directionnel_cfd.json
.venv/Scripts/python.exe results/_aggregate_edge_directionnel.py
```

## 3. Résultat global descriptif

| Population | N | Résolus | P(+1R avant -1R) | Censurés |
|---|---:|---:|---:|---:|
| Signal V14 | 1 784 | 1 782 | 54,60 % | 0,11 % |
| Contrôle aléatoire apparié | 35 680 | 35 670 | 49,75 % | 0,03 % |
| Écart descriptif brut | | | **+4,85 points** | |

Cet agrégat masque une divergence majeure entre classes. Il ne doit donc pas être utilisé pour ouvrir globalement le système.

## 4. Résultats par classe

| Classe | N signal | P signal | Delta apparié vs hasard | IC bootstrap 95 % | Lecture |
|---|---:|---:|---:|---:|---|
| FX | 524 | 46,76 % | -3,33 pts | [-7,58 ; +1,04] | Ne bat pas le hasard |
| Crypto | 323 | 55,73 % | +6,06 pts | [+0,30 ; +11,55] | Positif au niveau classe, fragile par strate |
| Métaux | 312 | 62,50 % | +11,75 pts | [+6,23 ; +17,08] | Avantage directionnel observé |
| Indices | 325 | 49,85 % | +1,48 pts | [-4,02 ; +6,97] | Ne bat pas le hasard |
| Énergie | 300 | 64,09 % | +14,45 pts | [+8,91 ; +19,80] | Avantage directionnel observé |

Conclusion de classe : **FX et indices n'ont pas d'edge directionnel démontré** dans cet échantillon. Métaux et énergie montrent un avantage net face au contrôle. Crypto est positif agrégé, mais la preuve se dilue dans les sous-strates.

## 5. Résultats croisés classe × piliers

| Strate | N | P signal | Delta apparié | IC 95 % | Verdict local |
|---|---:|---:|---:|---:|---|
| FX · 3p | 448 | 46,43 % | -3,85 pts | [-8,59 ; +0,98] | Non démontré |
| FX · 4p | 76 | 48,68 % | -0,26 pt | [-11,84 ; +11,51] | Inconclusif |
| Crypto · 3p | 260 | 54,62 % | +4,58 pts | [-1,71 ; +10,85] | Inconclusif |
| Crypto · 4p | 63 | 60,32 % | +12,17 pts | [-0,37 ; +24,85] | Prometteur, insuffisant |
| Métaux · 3p | 268 | 61,19 % | +10,62 pts | [+4,72 ; +16,68] | Positif observé |
| Métaux · 4p | 43 | 69,77 % | +18,02 pts | [+4,07 ; +31,40] | Positif, petit N |
| Métaux · 5p | 1 | 100 % | +45,00 pts | [+45 ; +45] | Non interprétable |
| Énergie · 3p | 252 | 66,00 % | +16,12 pts | [+10,18 ; +22,10] | Positif observé |
| Énergie · 4p | 48 | 54,17 % | +5,73 pts | [-8,33 ; +19,90] | Inconclusif |
| Indices · 3p | 282 | 49,65 % | +1,08 pt | [-4,93 ; +7,06] | Non démontré |
| Indices · 4p | 43 | 51,16 % | +4,07 pts | [-10,58 ; +18,49] | Inconclusif |

Le nombre de piliers n'est pas monotone toutes classes confondues. Il ne faut pas conclure que « plus de piliers = meilleur signal » sans tenir compte de la classe et de la taille d'échantillon.

## 6. Excursions MFE/MAE

Les distributions complètes Q25/médiane/Q75 par lot, classe et piliers sont stockées dans les trois JSON sources inclus dans `results/edge_directionnel_65bb599.json`.

Repères globaux par lot :

| Lot | MFE médiane signal | MAE médiane signal | MFE médiane contrôle | MAE médiane contrôle |
|---|---:|---:|---:|---:|
| FX | +0,918 R | -1,026 R | +1,001 R | -0,997 R |
| Métaux + crypto | +1,050 R | -0,650 R | +1,003 R | -0,988 R |
| CFD indices + énergie | +1,047 R | -0,786 R | +0,976 R | -1,007 R |

La lecture est cohérente avec le test de barrière : sur métaux/crypto et énergie, le signal atteint plus souvent +1 R avec une MAE médiane moins profonde que le contrôle. En FX, la MFE médiane est inférieure et la MAE médiane légèrement pire que le hasard.

## 7. Ce que l'étude prouve — et ne prouve pas

### Prouvé sur cet échantillon

1. Le signal n'a pas un comportement uniforme selon la classe d'actif.
2. FX et indices ne battent pas le contrôle apparié.
3. Métaux et énergie battent le contrôle sur cette mesure de direction brute.
4. Le réglage global des sorties serait méthodologiquement mauvais : il mélangerait des populations opposées.

### Non prouvé

1. **Aucune rentabilité nette.** Le test +1R/-1R mesure la direction brute, pas le PnL après spread, commission, swap, slippage et gestion.
2. **Aucune preuve OOS.** Les 4 000 barres constituent un échantillon historique exploratoire, pas un walk-forward scellé ni un test natif MT5.
3. **Aucune promotion.** Les résultats walk-forward antérieurs et le journal DÉMO restent négatifs ; cette étude ne les annule pas.
4. **Aucun seuil optimal.** Aucune recherche de seuil n'a été faite.
5. **Pas d'indépendance parfaite.** Des fenêtres de prix peuvent se chevaucher entre observations et contrôles ; l'IC bootstrap apparié corrige la pseudo-réplication des contrôles mais pas toute dépendance temporelle.
6. Les quartiles de volatilité sont calculés sur la distribution du lot historique. C'est acceptable pour apparier un contrôle descriptif, mais pas comme transformation de production sans validation causale.
7. Le décalage historique de trois heures relevé dans le journal live n'affecte pas cette étude, qui reconstruit ses entrées depuis les index de bougies MT5 et n'utilise pas les `ts_open` du journal live. Il faut néanmoins vérifier explicitement la convention horaire dans toute réplication native.

## 8. Décision recommandée

- **NO-GO** : modification globale du breakeven, trailing, quorum ou nombre de piliers.
- **NO-GO** : promotion live ou affirmation de rentabilité.
- **GO** : test OOS préenregistré, séparé par classe, en priorité :
  1. énergie 3p ;
  2. métaux 3p ;
  3. métaux 4p avec prudence sur le petit N ;
  4. crypto comme réplication, sans conclure avant stabilité des strates.
- **HOLD / diagnostic entrée** : FX et indices. Un réglage de sortie ne doit pas servir à masquer l'absence de supériorité directionnelle.

Critère minimal de prochaine validation : fenêtre future scellée, mêmes règles +1R/-1R, mêmes actifs et mêmes strates, contrôle apparié préspécifié, coûts exacts séparés, puis confirmation native MT5. Tant que cette réplication n'existe pas, le statut reste **observation exploratoire PAPER/DÉMO**.

## 9. Vérifications techniques

- GitNexus : index `65bb599`, contexte exact `titanium.backtest.rejouer`, appel entrant depuis `tools/edge_directionnel.py:analyse` observé.
- Tests ciblés : `32 passed in 0.85s` (`edge_directionnel`, `stop_temporel`, `contrefactuel_breakeven`, conventions de coût).
- Tests propres au nouvel outil : `8 passed`.
- `git diff --check` : succès.
- Tête Git restée `65bb599eaca01ee44f252692b13dbef41f933968` pendant la validation.
- GitNexus indique l'index à jour sur ce commit ; le statut de synchronisation du wrapper reste toutefois `synced=false`, réserve conservée.
