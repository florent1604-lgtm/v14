# Architecture V14 — Hermès, organes et Market-JEPA

## Chemin live

```text
MT5 DEMO -> barres/carnet -> features/portes -> candidat ENTER
                                        |
                     mémoire edge + politique Hermès fraîche
                                        |
                               RiskGate déterministe
                                        |
                              exécuteur MT5 DEMO
```

Le chemin chaud ne contacte aucun LLM. Une politique Hermès fraîche est lue
dans SQLite/WAL par actif, sens, contexte, modèle et prompt. L'avis exact de la
barre reste prioritaire. Toute absence, expiration, corruption ou divergence
fait `WAIT`.

## Chemin cognitif asynchrone

```text
faits scellés -> file -> sources publiques + cortex local -> proposition exacte
                                      |                  -> politique TTL 300 s
                                      +-> Hermès/Opus 5 (professeur/relecteur)
```

Opus 5 ne peut pas garantir une réponse en millisecondes. Il sert donc à
auditer les politiques, analyser les résultats et proposer des versions. Le
réflexe local mesuré est Market-JEPA : contexte M15 de 64 barres, horizon 4,
prédiction de volatilité/impulsion seulement. La direction est exclue car les
simulations V14 n'ont pas montré d'edge directionnel.

## Mesures locales du 5 septembre 2026

- 64 779 paires causales d'entraînement ;
- 13 têtes par actif et une tête globale de repli ;
- 500 inférences BTCUSD : p50 0,745 ms, p95 0,893 ms, p99 1,118 ms ;
- artefact JSON sans pickle, manifeste SHA-256 obligatoire ;
- 32 tests ciblés verts, dont corruption, TTL et absence de champs d'exécution.

Ces chiffres valident la latence et l'intégrité, pas la rentabilité. Le dernier
bilan live disponible reste négatif ; Market-JEPA est un organe de régime, pas
une preuve d'edge.
