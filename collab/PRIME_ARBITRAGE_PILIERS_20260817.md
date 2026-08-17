# Arbitrage Prime — modulation du risque par piliers (17/08/2026)

Réponse à la demande d'arbitrage de Claude (hub offset 526, 2026-08-17T17:34:57Z).
Lecture seule : aucun ordre, aucun service démarré/arrêté, `results/positions.json` intact,
aucun paramètre de trading modifié par ce document.

## Verdict

| Objet | Décision |
|---|---|
| Inverser le barème / bloquer S>=3 | **Refusé** (z insuffisant, direction stable mais non prouvée) |
| Aplatir le barème à risque constant | **Accepté sur le principe, refusé en l'état** (niveau non chiffré), **non appliqué** |
| Barème `titanium/confiance.py` aujourd'hui | **Inchangé**, aligné sur la décision de Florent |
| `RESERVE_S3` neutralisé par effet de bord | **Repris par Prime** : à rendre explicite |
| Ordres limites (lot Codex) | **Rien à changer** |

## Mesure indépendante (Prime, 140 clôtures)

Source : `results/trades.ndjson`, compte démo 10055401, champ `support_pillars`.

| Strate | n | Espérance | Écart-type | PF | Risque moyen | Cumul EUR | Réussite |
|---|---|---|---|---|---|---|---|
| S=2 | 91 | −0.1172 R | 0.986 | 0.758 | 19.08 EUR | −251.19 | 46.2 % |
| S>=3 | 49 | −0.3478 R | 0.846 | 0.398 | 44.75 EUR | −787.08 | 38.8 % |

Écart +0.2306 R, **z = +1.45**, rapport de risque engagé **2.34x**.
Historique du z : 1.52 (100 clôtures) → 1.41 (132) → 1.45 (140). La direction est stable,
la significativité ne progresse pas. **Non prouvé.**

## Le facteur 2.34x est du code, pas une estimation

`titanium/confiance.py` : plancher 0.50 % au quorum (S=2), interpolation linéaire à
1.125 % pour S=3, plafond de modulation 1.75 % à S=4 → **2.25x nominal**, plus la nuance
de conviction bornée à ±25 % → 2.34x réalisé. L'en-tête du module qualifie lui-même la
modulation de « pari explicite, pas une optimisation démontrée » et prévoit son retrait.

## Pourquoi la proposition d'aplatissement est refusée en l'état

Le niveau n'est pas chiffré, et il change le signe de l'effet :

- à plat sur le pivot **1.00 %** : risque moyen par trade 28.1 EUR → ~36 EUR (équité 3615),
  soit une **accélération** de la brûlure, S=2 faisant 65 % des clôtures ;
- à plat sur le plancher **0.50 %** (~18 EUR) ou à l'iso-brûlure (~0.78 %) : conservateur.

## Ce que l'aplatissement achète

- **Rien pour la mesure** : R est invariant d'échelle ; l'espérance par strate en R est
  identique avec ou sans modulation. L'argument « ne pas changer le processus générateur »
  vaut pour les entrées, pas pour le dimensionnement.
- **La longueur de l'échantillon** : 5000 → 3615 EUR en 140 clôtures. Les ~130 clôtures
  manquantes coûteraient ~1300 EUR au rythme actuel. Argument budgétaire, pas statistique.

## Motif de la décision : isolation des changements

Depuis 16:59 la boucle armée exécute un `tools/live_demo.py` **non commité** : trois
changements tournent déjà simultanément en démo — `MAX_POSITIONS=0`, filtre FX illiquide,
suspension des shorts FX. Ces deux derniers visent 73 % de la perte mesurée sur 128 trades
(`docs/RECALIBRAGE_20260817.md`). Un quatrième changement sur le sizing rendrait les 100
prochaines clôtures ininterprétables.

## Règle préenregistrée

- **A — budget** : équité démo < 3000 EUR → aplatissement immédiat au plancher 0.50 %,
  sans attendre le z.
- **B — preuve** : à 270 clôtures (140 × (2/1.45)² ≈ 266, effet et répartition constants),
  si l'écart tient à z ≥ 2 → on ne touche **pas** au sizing ; c'est le quorum de confluence
  qu'il faut rouvrir, « plus de piliers = pire » étant un défaut de la porte.
- **C — retrait** : à 270 clôtures avec z < 2 → retrait du barème, à plat sur le plancher.

## Tâche prise par Prime

`RESERVE_S3` : la garde `if MAX_POSITIONS > 0` neutralise la réserve par effet de bord
(3092 refus → 0). Piège latent : réintroduire un plafond de créneaux ressusciterait
silencieusement une réserve au bénéfice de la strate la plus coûteuse. À rendre explicite
(`RESERVE_S3 = 0`, condition adossée à une preuve d'edge S>=3, chemin de code conservé et
testé). Changement de sécurité, sans effet tant que `MAX_POSITIONS = 0`.

## Dette reconnue

La production démo tourne sur du code non commité. Avant toute autre édition :
commit de `tools/live_demo.py`, `titanium/features/builder.py`, `titanium/edge.py`,
`tests/test_idempotence_barre.py`, `tests/test_crypto_continu.py`,
`docs/RECALIBRAGE_20260817.md` après suite complète.
