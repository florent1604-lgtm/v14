# Diagnostic des piliers G2-G5 — 11 août 2026

## Verdict

Les données sont lisibles (`26 913/26 913` évaluations), donc le blocage ne
vient pas d'un flux MT5 absent. La rareté est concentrée sur G4 OTE/OB et G5
bougie. Elle reste visible après déduplication des mêmes barres. Cela ne prouve
pas que les détecteurs sont trop stricts : l'edge DEMO net actuel est négatif,
donc desserrer les portes augmenterait le débit sans preuve de qualité.

## Télémétrie brute de la boucle

- évaluations lisibles : 26 913 ; illisibles : 0 ;
- G2 fair value manquant : 15 480 (57,5 %) ;
- G3 liquidité manquante : 19 069 (70,9 %) ;
- G4 OTE/OB manquant : 25 160 (93,5 %) ;
- G5 bougie manquante : 24 716 (91,8 %) ;
- supports : S0 9 760, S1 12 934, S2 3 798, S3 415, S4 6.

Ces compteurs incluent la réévaluation d'une même barre à chaque tour et ne
doivent pas être lus comme autant d'opportunités indépendantes.

## Analyse dédupliquée

Fenêtre post-redémarrage du 11/08 à 07:01 UTC, journal
`results/shadow_prod.ndjson`, clé `(symbole, bar_time, sens, famille)` :

- 4 220 observations brutes deviennent 384 candidats uniques, soit un facteur
  de répétition de 11,0 ;
- 331 candidats S2 sont bloqués par le quorum PROD ;
- 52 candidats S3 et 1 candidat S4 franchissent le quorum mais restent bloqués
  par l'edge non prouvé ;
- parmi les 331 S2 : OTE/OB manque 293 fois (88,5 %), bougie 258 fois (77,9 %),
  liquidité 75 fois (22,7 %), fair value 36 fois (10,9 %) ;
- familles : continuation 216 candidats (198 S2, 18 S3) ; reversal 168
  candidats (133 S2, 34 S3, 1 S4) ; 65 symboles distincts.

## Décision d'ingénierie

1. Ne pas abaisser le quorum 3/4 et ne pas élargir les tolérances G4/G5 tant
   qu'un échantillon net avec coûts n'établit pas une amélioration OOS.
2. Dédupliquer par barre toute comparaison future de taux de blocage.
3. Conserver G4 et G5 comme variables diagnostiques : comparer leur fréquence
   et l'edge des cellules S2/S3, plutôt que les optimiser sur 26 clôtures.
4. La prochaine amélioration rentable est le classement des candidats rares
   par coût et edge observé, après collecte suffisante ; produire davantage de
   S2 n'est pas un substitut à la sélection.
