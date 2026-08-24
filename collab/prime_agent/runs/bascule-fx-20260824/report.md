# Suspension du FX — bascule du 24/08/2026

Decision de Florent : suspendre le FX temporairement et verifier la hausse du R
sur les prochains trades.

## Instant de bascule

```
2026-08-24T06:22:09Z   redemarrage de la boucle avec FX_SUSPENDU = True
```

## Ce qui est en place

| Element | Etat |
|---|---|
| `tools/live_demo.py` : `FX_SUSPENDU = True` | actif, refus journalise sous son propre code |
| `tools/suivi_bascule.py` | mesure avant/apres sur les trades CLOS |
| veille `--veiller` (pid 9164) | publie `suivi.md` des 20 trades clos apres la bascule |
| reference figee | `reference_avant_bascule.txt` |

Preuve que la suspension mord, 3 tours apres le redemarrage :

```
refus : FX_SUSPENDU 6 | MAX_PAR_SYMBOLE 6 | GRAPPE 2 | EXECUTION 2 | COUT_SPREAD 1
```

## L etat AVANT, fige (347 trades clos)

```
GLOBAL          347 @ -0.0747
temoin hors FX  227 @ -0.0051
fx              120 @ -0.2063     <- ce qui est suspendu
crypto           28 @ +0.2945
energie          44 @ +0.1631
metaux           33 @ +0.0203
indices         117 @ -0.1564
```

Le compte dit la meme chose que le rejeu, par un chemin independant : le FX
coute -0,2063 R par trade quand le reste de l univers est a -0,0051.

## Ce qui sera verifie, et comment

**Attendu mecanique** : le global doit remonter de -0,0747 vers l ordre de
grandeur du hors-FX, sans que le hors-FX bouge.

Trois precautions, sans lesquelles la verification mentirait :

1. **plancher de 20 trades clos** apres la bascule. En dessous, le verdict est
   INDECIS et rien d autre — une moyenne sur cinq trades decrit le hasard.
2. **temoin hors FX**. Si le temoin monte autant que le global, la hausse vient
   du marche, pas de la suspension. C est le seul controle qui distingue les
   deux, et il est publie a chaque ligne.
3. **erreur type**. Un ecart inferieur a deux erreurs types est ecrit
   INDISTINGUABLE, jamais "hausse".

## Reserve honnete

Deux changements coexistent depuis ce matin : la levee de l anti-fade (06:00Z)
et la suspension du FX (06:22Z). Le global melange donc deux effets. La lecture
par FAMILLE les separe : `reversal` n existait pas avant aujourd hui, il mesure
la levee ; le `temoin hors FX` mesure ce qui n a rien a voir avec le FX. Aucun
verdict ne sera rendu sur le global seul.

## Revenir en arriere

`FX_SUSPENDU = False` dans `tools/live_demo.py`, puis `RELANCER_BOUCLE.bat`.
Un mot, un redemarrage. Le flux FX ecarte reste comptable dans
`results/refus_live.ndjson`, donc le cout de la suspension sera mesurable lui
aussi.
