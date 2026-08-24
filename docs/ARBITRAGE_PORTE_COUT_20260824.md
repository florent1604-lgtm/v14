# Arbitrage de la porte de coût — 24/08/2026

Décision de Prime, demandée par Claude le 22/08 (`docs/RAPPORT_COUT_DECISIONNEL_20260822.md`,
§7 : « aucune décision de seuil ne relève de moi »), rendue possible par la fin
du rejeu de l'univers.

**Mesure : 797 706 trades, 147 symboles, moteur `051f50ad`.** Claude avait
8 symboles. Outil : `tools/porte_cout.py`. Règle : le seuil est choisi sur la
**calibration seule**, puis jugé sur la **vérification**, jamais utilisée pour
choisir.

---

## 1. Le fait central est confirmé à grande échelle

`cost_r = spread / r_unit` est connu avant l'entrée : `r_unit` est la distance
de stop, décidée au moment du signal. La porte est donc calculable sans
anticipation.

Univers entier, segment de vérification :

```
  seuil    part   n verif  net VERIF   somme VERIF
  aucun  100.0%   274860    -0.7180     -197351.4
   0.06   10.2%    31488    +0.1630       +5132.8
   0.08   16.7%    46757    +0.1333       +6231.8
   0.10   23.4%    62076    +0.1118       +6940.2
   0.12   29.7%    77224    +0.0948       +7321.6   <- maximum de R total
   0.15   38.4%    98579    +0.0703       +6930.0
   0.20   50.3%   128412    +0.0393       +5049.5
   0.30   65.0%   166972    -0.0005          -79.5
   0.60   81.1%   208970    -0.0570      -11914.5
```

Sans porte, l'univers perd −0,7180 R par trade. La courbe est monotone et sans
inversion entre les deux segments : l'effet mesuré par Claude sur 8 symboles
tient sur 147.

## 2. Première décision : **le seuil actuel ne change pas**

`titanium/sizing.py` applique déjà `MAX_COUT_SPREAD_PCT = 0.125` — le spread ne
doit pas dépasser 12,5 % du stop. La mesure place le maximum de R total à
**0,12**, et l'écart entre 0,12 et 0,125 est en dessous du pas de la grille.

**Le bouton était déjà au bon endroit.** Le resserrer (0,06 ou 0,08) achète de
l'espérance par trade avec du volume : +0,1630 contre +0,0948, mais 31 488
trades au lieu de 77 224 et **moins** de R total. Le desserrer détruit tout.

Aucune modification de code. C'est le résultat le plus utile de cet arbitrage :
une mesure qui confirme un réglage vaut une mesure qui le corrige.

## 3. Deuxième décision : **le gain est dans la classe d'actif, pas dans le seuil**

Seuils choisis par classe sur la calibration, jugés sur la vérification :

| classe | seuil calib. | n vérif | net VÉRIF | R total |
|---|---:|---:|---:|---:|
| énergie | 0,15 | 5 692 | **+0,2042** | +1 162 |
| métaux | 0,08 | 8 687 | **+0,1644** | +1 428 |
| crypto | 0,30 | 19 201 | **+0,1513** | +2 905 |
| indices | 0,15 | 33 802 | **+0,1067** | +3 606 |
| agricole | 0,25 | 2 936 | +0,2865 | +841 |
| **FX** | 0,06 | 3 887 | **+0,0203** | **+79** |

Quatre politiques comparées sur la vérification (`sondes/_probe_politiques.py`) :

```
A. aucune porte              n=274860   moyenne -0,7180   somme -197351
B. porte globale 0,12        n= 77224   moyenne +0,0948   somme   +7322
C. globale 0,12 + FX exclu   n= 52040   moyenne +0,1588   somme   +8264
D. par classe + FX exclu     n= 70318   moyenne +0,1414   somme   +9943
```

**Passer de B à C — c'est-à-dire ne rien changer au seuil et sortir le FX —
fait plus de bien que n'importe quel réglage du seuil.** L'espérance par trade
monte de +0,0948 à +0,1588 (+67 %) en supprimant un tiers des trades qui, eux,
ne rapportent rien.

Le FX gagne dans une seule cellule (seuil 0,06, 5,6 % des trades, +79 R au
total sur 3 887 trades) : indistinguable de zéro. **Aucun réglage de coût ne
fabrique un avantage là où il n'y en a pas** — même conclusion que le NO-GO FX
de Codex, atteinte par un chemin indépendant.

## 4. Ce que je refuse de faire

**Les seuils par classe (politique D) ne sont pas adoptés aujourd'hui.** Ils
rapportent +20 % de R total sur C, contre cinq boutons à maintenir et à
recalibrer. Deux d'entre eux vont dans le sens permissif (crypto 0,30,
agricole 0,25) sur des classes où la calibration et la vérification divergent —
l'agricole est négatif en calibration à tous les seuils et positif en
vérification à tous les seuils, ce qui est un changement de régime, pas un
effet de coût. Un bouton permissif posé sur une divergence de régime est
exactement la façon dont on se fait rattraper hors échantillon.

À revoir quand l'auditeur A/B d'exécution aura tranché la question maker/taker,
qui déplace `cost_r` lui-même.

## 5. Ce qui reste à Florent

Sortir le FX de l'univers tradé est une décision **métier** : elle retire
environ 45 % des symboles portables de la boucle démo. La mesure est sans
ambiguïté, la décision lui appartient. Recommandation de Prime : **oui, hors
échantillon, sur 120 772 trades de vérification.**

## 6. Preuves

```
tools/porte_cout.py --refaire-cache        797 706 trades extraits, 85 s
tools/porte_cout.py --critere somme        seuil global retenu 0,12
pytest tests/test_porte_cout.py            6 passed
ruff check                                 All checks passed
resultats machine : results/porte_cout.json
```

Le cache `results/porte_cout_trades.parquet` conserve les seules colonnes
utiles des 2,36 Go d'artefacts bruts : réexaminer un seuil coûte désormais
trente secondes, ce qui est la condition pour que quelqu'un le refasse.


---

# Addendum — 24/08 : la mesure qui manquait au refus CONTRE_TENDANCE

Question posee par Florent en meme temps que l'arbitrage de coût.

`titanium/risk/riskgate.py:191` refuse tout trade dont le sens s'oppose a la
tendance HTF (`trend = signe(close - EMA200)`), regle anti-fade heritee du
correctif V12 du 26/07. Dans la fenetre observee, **910 refus sur 910 portent
ce seul motif**, et le heartbeat du 23/08 montre 93 ENTER pour 0 ordre envoye.

`titanium/features/builder.py` nomme cette meme opposition `family = reversal`.
Les deux composants lisent le meme `trend` et se contredisent : la porte de
confluence emet, le RiskGate veto.

Mesure sur le rejeu, qui **n'a pas d'anti-fade**, segment de verification,
porte de cout 0,125 active, FX exclu (`sondes/_probe_familles.py`) :

| famille | n | net R | R total |
|---|---:|---:|---:|
| continuation | 29 944 | +0,1819 | +5 447 |
| **reversal** | 23 782 | **+0,1250** | **+2 973** |

La famille refusee en direct est **positive hors echantillon**, moins que la
continuation mais loin d'etre nulle. Sans FX ni porte de cout, elle est en
revanche negative partout sauf metaux et energie : c'est le coût, pas le sens,
qui la rendait perdante.

Reserve : `trend = 0` est possible quand l'EMA200 HTF n'est pas definie, et ces
cas tombent en `reversal` sans etre vetoes en direct. Apres l'amorcage de 250
barres, le cas est rare — mais les artefacts ne portent pas `trend`, donc je ne
peux pas le chiffrer sans un rejeu supplementaire.

Trois issues possibles, aucune n'est mienne :

1. **lever l'anti-fade** — le rejeu dit que la famille gagne ;
2. **cesser d'emettre du reversal** dans la porte de confluence — telemetrie
   honnete, mais le debit reste au niveau actuel, donc l'accumulation des
   20 clotures par contexte reste hors d'atteinte ;
3. **autoriser le reversal par classe** — positif en metaux et energie meme
   sans porte de cout, negatif ailleurs.

Journal : taches `95cb60be` (FX) et `d0bdf463` (anti-fade), toutes deux en
backlog, proprietaire `team`.
