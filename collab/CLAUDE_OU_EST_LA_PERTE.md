# Analyse des pertes — cohorte scellée et propre

> Rapport généré par `tools/analyse_pertes.py`. Aucun nombre analytique n’est saisi manuellement.

Artefact SHA-256 : `dd35fd15744f462965e4bd521edd9217878697df99524fb54825d5b609aa1490`.
Source scellée : `excursions.ndjson` (`467a0659939e209cc5bde099c378c81c714a62e91420ce322591c3ce3d8c766f`).
Borne du sceau : `live:89506157`.

## Trous runtime fournis

| Début UTC | Fin UTC |
|---|---|
| 2026-08-12T22:15:25.770935+00:00 | 2026-08-13T05:17:17.751153+00:00 |

## Cohorte

Lignes scellées : 44. Trajectoires exclues car elles traversent un trou runtime : 2. Cohorte propre : 42.

| Sortie | n | Cumul | Moyenne |
|---|---:|---:|---:|
| init | 25 | -24.3519 R | -0.9741 R |
| breakeven | 10 | -0.3117 R | -0.0312 R |
| trailing | 7 | +8.1294 R | +1.1613 R |

## MFE des stops initiaux

| Bin | n |
|---|---:|
| < 0.05 R | 12 |
| 0.05 à < 0.20 R | 3 |
| 0.20 à < 0.50 R | 5 |
| >= 0.50 R | 5 |
| Somme vérifiée (`sum(bins) == n`) | 25 |

## Conclusion

Les pertes observées sont concentrées dans les stops initiaux ; l’hypothèse prioritaire concerne la sélection/entrée.

Cette observation est descriptive, non causale. Les groupes sont définis par leur issue ; ils ne constituent pas un contrefactuel permettant d’attribuer le résultat à la politique de sortie.

Le champ source `censored` est conservé séparément de `runtime_gap_censored` et n’est jamais utilisé comme substitut à la censure par trou runtime.
