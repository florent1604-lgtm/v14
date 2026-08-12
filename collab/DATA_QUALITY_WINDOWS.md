# Fenêtres de qualité des données V14

Ce document recense les périodes pendant lesquelles la boucle n'était pas en
mesure de scanner le marché ni de gérer dynamiquement les positions. Elles ne
doivent jamais être interprétées comme une absence de signaux.

## 10–11 août 2026 — veille Windows

- information humaine initiale : PC laissé de côté vers 19:00 et repris vers
  06:00, heure de Paris ;
- preuve Windows `Power-Troubleshooter` : veille effective
  `2026-08-10 21:02:04 Europe/Paris` (`19:02:04Z`) ;
- réveil effectif : `2026-08-11 06:11:04 Europe/Paris` (`04:11:04Z`) ;
- durée non observable : environ 9 h 09 min ;
- scans, nouvelles entrées et mises à jour trailing/breakeven : interrompus ;
- SL/TP déjà déposés chez le courtier : toujours exécutables côté serveur.

### Clôtures affectées

- `live:87940036` — USDCHF — clôture affichée `2026-08-11T00:01:00` —
  -0,6445 R — phase `breakeven` ;
- `live:88004818` — NAS100.fs — clôture affichée
  `2026-08-11T02:20:50` — -1,1377 R — phase `init`.

Les horodatages issus de MT5 apparaissent décalés de deux heures et étiquetés
`+00:00` alors qu'ils suivent l'heure courtier/Paris. Ce défaut d'étiquetage doit
être corrigé séparément ; pour cette fenêtre, le rapprochement est fait sur les
heures affichées et la preuve Windows.

### Positions ayant traversé la veille

`CHFSEK`, `AUDSGD`, `EURHUF` et `USDSGD` étaient ouvertes avant la veille et le
sont encore au moment du constat. Leur gestion dynamique a été interrompue ;
leurs clôtures futures doivent être analysées séparément.

### Règle analytique

Toujours publier les métriques brutes et une sensibilité excluant les clôtures
affectées. Ne jamais supprimer ou réécrire le journal append-only. Aucune
promotion ne peut utiliser ces observations comme preuve propre.
