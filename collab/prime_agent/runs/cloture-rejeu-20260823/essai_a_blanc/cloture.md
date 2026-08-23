# Cloture du backfill de rejeu — epoque 051f50adf179177e

Publie le 2026-08-23T14:00:13.746624+00:00 par `tools/cloture_backfill_rejeu.py`.
Arret : **manuel**.

## Etat de l'univers

```
cible          149
termines       40
hors univers   0 
restants       109 EURCAD, EURCHF, EURCZK, EURGBP, EURHUF, EURJPY, EURNOK, EURNZD, EURPLN, EURSEK, EURSGD, EURUSD
sentinelle     False
```

## Audit des artefacts

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\audit_rejeu_artefacts.py` — code 0, 100.7 s

```
artefacts acceptes 148/149 | legacy 0 | invalides 0 | manquants 1
ALERTE: empreintes moteur mixtes: 2 generations
ALERTE: 108 artefact(s) scelles par une autre generation que le moteur courant 051f50adf179177e: EURCAD, EURCHF, EURCZK, EURGBP, EURHUF, EURJPY, EURNOK, EURNZD, EURPLN, EURSEK, EURSGD, EURUSD
```

## Prediction de la porte de granularite

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\valider_predictions_granularite.py` — code 0, 0.5 s

```
validation granularite : PARTIEL (moteur 051f50adf179177e)
  change 2 | en_attente 109 | identique 38
  COCOA.fs [attendu] 21 ecart(s)
      n_enter: 4326 -> 4110
      barres_evaluees: 37462 -> 36036
      coupure: 2020-08-05T17:10:00+00:00 -> 2023-09-11T18:30:00+00:00
      global.n: 4326 -> 4110
      global.esperance_r: 0.017741 -> 0.026284
      global.ecart_type_r: 1.180764 -> 1.185211
  COFFEE.fs [attendu] 21 ecart(s)
      n_enter: 4574 -> 4404
      barres_evaluees: 38348 -> 36895
      coupure: 2020-04-06T19:50:00+00:00 -> 2023-09-25T18:50:00+00:00
      global.n: 4574 -> 4404
      global.esperance_r: 0.108749 -> 0.105795
      global.ecart_type_r: 1.138849 -> 1.140608
```

## Classement de l'univers

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\analyse_rejeu_univers.py --min-symboles 999` — code 2, 0.6 s

```
rejeu : 40 symboles termines a l'epoque courante (moteur 051f50adf179177e)
  108 artefacts d'une AUTRE generation ecartes ['16e79f53a610da42'] — un backfill est probablement en cours

REFUS: 40 symboles a l'epoque courante, plancher 999. Un classement sur cet echantillon decrirait l'ordre des lots, pas l'univers. Attendre la fin du backfill, ou forcer avec --min-symboles.
```

## Ce que cette cloture ne fait pas

Elle ne relance aucun lot, ne reecrit aucun artefact, ne modifie aucun
seuil ni parametre de risque, et n'a aucune autorite d'execution.
