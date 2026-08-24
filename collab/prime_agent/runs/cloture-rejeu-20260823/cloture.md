# Cloture du backfill de rejeu — epoque 051f50adf179177e

Publie le 2026-08-24T01:25:01.842487+00:00 par `tools/cloture_backfill_rejeu.py`.
Arret : **termine**.

## Etat de l'univers

```
cible          149
termines       147
hors univers   2 USDCOP, USDUSC
restants       0 
sentinelle     False
```

## Audit des artefacts

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\audit_rejeu_artefacts.py` — code 0, 125.0 s

```
artefacts acceptes 148/149 | legacy 0 | invalides 0 | manquants 1
ALERTE: empreintes moteur mixtes: 2 generations
ALERTE: 1 artefact(s) scelles par une autre generation que le moteur courant 051f50adf179177e: USDCOP
```

## Prediction de la porte de granularite

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\valider_predictions_granularite.py` — code 0, 0.9 s

```
validation granularite : PARTIEL (moteur 051f50adf179177e)
  change 5 | en_attente 1 | hors_univers 1 | identique 142
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
  IT40 [attendu] 21 ecart(s)
      n_enter: 3624 -> 3488
      barres_evaluees: 38825 -> 37749
      coupure: 2021-12-15T01:20:00+00:00 -> 2023-11-05T09:20:00+00:00
      global.n: 3624 -> 3488
      global.esperance_r: 0.167796 -> 0.174767
      global.ecart_type_r: 1.164081 -> 1.165477
  SPA35 [attendu] 21 ecart(s)
      n_enter: 5559 -> 5391
      barres_evaluees: 50667 -> 49464
      coupure: 2021-06-23T03:10:00+00:00 -> 2023-11-25T10:50:00+00:00
      global.n: 5559 -> 5391
      global.esperance_r: 0.006256 -> 0.009249
      global.ecart_type_r: 1.143497 -> 1.145875
  USDCLP [attendu] 14 ecart(s)
      n_enter: 674 -> 507
      barres_evaluees: 6853 -> 5095
      global.n: 674 -> 507
      global.esperance_r: 0.063145 -> 0.050795
      global.ecart_type_r: 1.227517 -> 1.223705
      global.winrate: 0.406528 -> 0.402367
ALERTE: prediction trop large, ces symboles attendus n'ont pas bouge: GER40
```

## Classement de l'univers

`C:\Users\flore\Desktop\V14\.venv\Scripts\python.exe -X utf8 C:\Users\flore\Desktop\V14\tools\analyse_rejeu_univers.py` — code 0, 50.9 s

```
rejeu : 147 symboles termines a l'epoque courante (moteur 051f50adf179177e)
  1 artefacts d'une AUTRE generation ecartes ['16e79f53a610da42'] — un backfill est probablement en cours

esperance globale : mediane -0.1040R | positifs 44/147
positifs EN CALIBRATION ET EN GLOBAL : 38/147   <- les seuls defendables

symbole            calib    global    ecart     win     PF      n
UKOIL            +0.1870   +0.1792  -0.0078   56.3%   1.46   5541
IT40             +0.1892   +0.1748  -0.0144   53.0%   1.42   3488
BTCUSD           +0.1839   +0.1744  -0.0095   60.7%   1.45   5848
USTECH           +0.1559   +0.1604  +0.0045   60.7%   1.40   5017
BTC-JPY          +0.1648   +0.1584  -0.0064   58.3%   1.41   5651
WTI.fs           +0.1478   +0.1504  +0.0026   54.5%   1.38   5565
USOIL            +0.1379   +0.1498  +0.0119   54.4%   1.37   5520
BRENT.fs         +0.1574   +0.1420  -0.0154   54.7%   1.36   5681
ETHUSD           +0.1337   +0.1419  +0.0082   51.7%   1.35   5471
FRA40            +0.1318   +0.1404  +0.0086   59.5%   1.34   5484
HK50             +0.1350   +0.1311  -0.0040   56.5%   1.31   5574
US30             +0.1087   +0.1174  +0.0088   58.8%   1.28   4887
SOL-USD          +0.1805   +0.1174  -0.0631   48.9%   1.28   5561
XAUUSD           +0.0845   +0.1173  +0.0328   60.0%   1.29   5501
NAS100.fs        +0.1030   +0.1122  +0.0092   56.5%   1.27   5111
GER40            +0.0990   +0.1062  +0.0072   59.3%   1.26   5283
COFFEE.fs        +0.0549   +0.1058  +0.0509   47.6%   1.24   4404
US500            +0.0810   +0.0864  +0.0054   55.7%   1.20   5019
JPN225           +0.0433   +0.0799  +0.0367   52.0%   1.19   5555
BNB-USD          +0.0681   +0.0791  +0.0110   47.4%   1.18   6010
HSI.fs           +0.0729   +0.0700  -0.0028   45.4%   1.15   5542
NETH25           +0.0966   +0.0698  -0.0267   47.6%   1.16   5419
DJ30.fs          +0.0615   +0.0696  +0.0082   54.4%   1.16   4878
LNKUSD           +0.1533   +0.0673  -0.0860   46.4%   1.16   5751
US2000           +0.0453   +0.0640  +0.0187   53.2%   1.14   4996

correlations H1 entre les 38 survivants (rendements log, depuis la borne utile)
  paires evaluees : 703 | redondantes (|rho| >= 0.8) : 31
    HK50         HSI.fs       rho +0.996  sur 22520 barres
    USDJPC       USDJPY       rho +0.995  sur 6451 barres
    S&P.fs       US500        rho +0.989  sur 47593 barres
    BCH-JPY      BCHUSD       rho +0.972  sur 36466 barres
    ETH-JPY      ETHUSD       rho +0.960  sur 36451 barres
    BTC-JPY      BTCUSD       rho +0.957  sur 36458 barres
    US30         US500        rho +0.953  sur 47927 barres
    BRENT.fs     UKOIL        rho +0.944  sur 45066 barres
    S&P.fs       US30         rho +0.943  sur 47597 barres
    US500        USTECH       rho +0.929  sur 47921 barres
    S&P.fs       USTECH       rho +0.919  sur 47589 barres
    USOIL        WTI.fs       rho +0.906  sur 47606 barres
    NAS100.fs    USTECH       rho +0.900  sur 47816 barres
    BRENT.fs     USOIL        rho +0.896  sur 44511 barres
    FRA40        NETH25       rho +0.892  sur 30270 barres

  -> 38 actifs se reduisent a 21 paris independants
    grappe de 7 : DJ30.fs, NAS100.fs, S&P.fs, US2000, US30, US500, USTECH   -> garder USTECH
    grappe de 4 : BRENT.fs, UKOIL, USOIL, WTI.fs   -> garder UKOIL
    grappe de 4 : BTC-JPY, BTCUSD, ETH-JPY, ETHUSD   -> garder BTCUSD
    grappe de 3 : FRA40, GER40, NETH25   -> garder FRA40
    grappe de 2 : BCH-JPY, BCHUSD   -> garder BCH-JPY
    grappe de 2 : HK50, HSI.fs   -> garder HK50
    grappe de 2 : USDJPC, USDJPY   -> garder USDJPC

rapport : C:\Users\flore\Desktop\V14\results\analyse_rejeu.json
```

## Ce que cette cloture ne fait pas

Elle ne relance aucun lot, ne reecrit aucun artefact, ne modifie aucun
seuil ni parametre de risque, et n'a aucune autorite d'execution.
