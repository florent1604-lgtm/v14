# Manifeste scellé de la cohorte P1a

## Verdict

La cohorte canonique P1a contient **373 clôtures** et s'arrête exactement à
`event_id = 97465104:closed`, ligne 1755 de `results/limit_lifecycle.ndjson`.
Elle ne dépend donc pas de la fin mouvante des journaux PAPER/DEMO.

- Artefact : `results/p1a/cohorte_373.json`
- Manifeste : `results/p1a/cohorte_373.manifest.json`
- SHA-256 de l'artefact : `10d6277664775ea8599b9304b8e7e86055b35f9a659fce572dfc2f4b22e2e908`
- Dernière clôture : `2026-08-25T11:52:37+00:00`

Chaque ligne a été reliée sans heuristique :

- `closed.position_ticket` vers `trade.ticket` ;
- `closed.position_ticket` vers `excursion.ticket` ;
- `closed.order_ticket` vers `placed.order_ticket`.

Toute absence, duplication ou incohérence de symbole, sens, contexte, PnL ou
raison de sortie fait échouer la génération.

## Résolution de l'ambiguïté 191 / 195

Les nombres désignent deux populations différentes :

| Population | Nombre |
|---|---:|
| `exit_reason = init` | 195 |
| `init` avec `pnl_r < 0` | 187 |
| `init` avec `pnl_r > 0` | 8 |
| Toutes les clôtures avec `pnl_r < 0` | 191 |
| `breakeven` avec `pnl_r < 0` | 4 |
| `breakeven` avec `pnl_r > 0` | 59 |
| `trailing` avec `pnl_r > 0` | 115 |

Il est donc incorrect d'appeler les **191** pertes des « sorties au stop
initial ». Le champ `exit_reason=init` signifie que la gestion de stop est
restée au stade `init`; il ne préjuge pas du signe du PnL.

## Convention des piliers

La convention est figée dans l'artefact :

| Contexte | `support_pillars` | Libellé diagnostic | Nombre |
|---|---:|---|---:|
| suffixe `3p` | 2 | 2 piliers de support | 316 |
| suffixe `4p` | 3 | 3 piliers de support | 57 |

Les 373 observations ont `quorum=2`. Aucun contexte `5p` n'appartient à cette
cohorte.

## Reproduction et vérification

```powershell
.\.venv\Scripts\python.exe tools\sceller_cohorte_p1a.py `
  --through-closed-event-id 97465104:closed `
  --expected-count 373
```

Le scelleur lit chaque source une seule fois, refuse une mutation pendant la
lecture, écrit par remplacement atomique, puis vérifie le SHA-256 et la
cardinalité. Les empreintes des lignes sources sélectionnées sont conservées
dans le manifeste.

## Usage des audits

Claude, Hermès et Prime doivent charger `cohorte_373.json` et citer son SHA-256
dans tout résultat dérivé. Toute autre cohorte ou tout autre seuil doit être
présenté comme une nouvelle analyse exploratoire, jamais comme une validation
hors échantillon indépendante.
