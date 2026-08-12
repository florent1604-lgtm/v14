# Simulation post-ENTER V14 — Codex

Date : 2026-08-11, 21:06 Paris  
Périmètre : moteur MT5 DEMO après redémarrage complet.

## Verdict

**Conserver `MAX_PAR_SYMBOLE=1` et `RESERVE_S3=2`.** Le taux de refus brut ne
mesure pas des opportunités indépendantes : les mêmes candidats sont recomptés
toutes les 60 secondes. Avec 7 positions sur 8 et un edge observé négatif,
relâcher les gardes augmenterait la concentration sans preuve de gain.

## État vérifié

- boucle : **662 tours**, **26 242** évaluations ;
- verdicts ENTER : **4 115** ;
- ordres envoyés : **12**, soit **0,292 %** des ENTER ;
- positions ouvertes : **7/8** ;
- equity observée : environ **4 414 EUR** ;
- PnL clôturé rapproché depuis le début de la cohorte : **-226,48 EUR**.

Refus post-ENTER cumulés :

| Motif | Occurrences |
|---|---:|
| `RESERVE_S3` | 1 500 |
| `RISKGATE_DENY` | 1 375 |
| `MAX_PAR_SYMBOLE` | 1 165 |
| `GRAPPE` | 23 |
| `DERIVE` | 7 |
| `EXECUTION` | 7 |
| `COUT_SPREAD` | 4 |

## Interprétation

- `MAX_PAR_SYMBOLE` protège contre l'empilement répété sur un actif déjà
  ouvert ; son compteur ne représente pas 1 165 nouveaux trades distincts.
- `RESERVE_S3` préserve la dernière place pour un setup S>=3 lorsque le
  portefeuille est presque plein.
- `RISKGATE_DENY` reste souverain et fail-closed.
- Avec une seule place disponible, toute relaxation ne peut ajouter qu'une
  position immédiatement, avant les autres contrôles aval.

## Décision recommandée

Ne modifier aucun seuil actif. La prochaine action rentable à tester est une
instrumentation shadow de l'allocation : pour chaque candidat ENTER, conserver
son rang structurel, son contexte, son `spread_r`, sa famille de corrélation et
la place obtenue/refusée. Une modification de l'ordre de tri ne sera envisagée
qu'après comparaison hors échantillon de l'espérance nette des candidats pris
et laissés.
