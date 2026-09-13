# Amendement du banc A/B — mesures préalables (P1a / b18ff568)

État : **en cours**. Attend l'audit sémantique de Claude (offset 683) et l'audit
de puissance d'Hermès (offset 684). Ce document consigne les mesures que j'ai pu
faire **sans** ces audits, sur la cohorte scellée.

Corpus : `results/p1a/cohorte_373.json`, SHA-256
`10d6277664775ea8599b9304b8e7e86055b35f9a659fce572dfc2f4b22e2e908`, cutoff
`97465104:closed`, commit `c3cda39`. Lecture seule. Aucun fichier de
FICHIERS_MOTEUR touché, aucun rejeu, aucun ordre, aucun seuil.

## Réserve 3 (embargo) — mon plan est FALSIFIÉ par les données

Le §6 de `docs/ARCHITECTURE_BANC_AB_20260825.md` fixe un embargo de 50 h, dérivé
de « 200 barres M15 ». Les deux prémisses sont fausses.

1. La cohorte n'est pas M15 : **M15 292, H1 74, H4 7**.
2. Durée calendaire `placed_at`→`closed_at`, maximum par TF :

   | TF  | n   | médiane (h) | max (h)   | max (barres) |
   |-----|-----|-------------|-----------|--------------|
   | M15 | 292 | 1.47        | **61.99** | **248**      |
   | H1  | 74  | 5.07        | **65.24** | 65.2         |
   | H4  | 7   | 14.08       | 59.14     | 14.8         |

3. **13 trades sur 373 (3.5 %) dépassent 50 h** (M15 7, H1 4, H4 2). L'embargo
   publié fuit donc sur 3.5 % de la cohorte.
4. La prémisse « 200 barres » ne tient pas non plus : le max M15 vaut **248
   barres**.

Correction du diagnostic de Codex : il attribuait le risque à H1 (« pour H1 le
risque peut être 200 h »). C'est l'inverse. En **barres**, H1 est court (65 max).
Le facteur qui fait exploser l'embargo est le **temps calendaire sur M15**
(week-ends et gaps), pas le pas de temps. Un embargo exprimé en barres est
structurellement faux ; il doit être exprimé en calendaire.

Contrainte supplémentaire : la cohorte ne couvre que **12.9 jours** et est
censurée à droite au cutoff. Le max observé (65.24 h) est donc une **borne
inférieure** du vrai max. Conséquence : l'embargo ne peut pas être *estimé* sur
l'observé ; il doit être un **plafond imposé**, qui exclut explicitement tout
trade le dépassant, avec le décompte des exclus publié.

## Réserve 2 (baseline figée) — le poids 4p est sous-déterminé d'un facteur ~2

Table exacte de `titanium.confiance.evaluer`, exécutée dans le venv du projet
(`total_piliers=4`, `quorum=2`, `MAX_RISK_PCT=2.0`), par valeur de conviction :

| support_pillars | cohorte | n   | conviction 0.0 | 0.5 (neutre) | 1.0    |
|-----------------|---------|-----|----------------|--------------|--------|
| 2               | « 3p »  | 316 | 0.500          | **0.500**    | 0.625  |
| 3               | « 4p »  | 57  | 0.844          | **1.125**    | 1.406  |
| 4               | « 5p »  | 0   | 1.313          | 1.750        | 1.750  |

Ratio 4p/3p : **2.25× à conviction neutre**, mais **1.35× à 2.81×** sur
l'enveloppe de conviction.

**`conviction` n'est pas un champ de la cohorte scellée.** L'entrée qui fixe le
poids n'a pas été enregistrée. La variante B (« 4p → poids 3p ») est donc
sous-déterminée d'un facteur ~2 par une donnée manquante.

Le clamp est en outre **asymétrique** : à `support_pillars=2` le plancher écrase
toute la moitié basse (0.0 et 0.5 rendent tous deux 0.500), à
`support_pillars=4` le plafond écrase la moitié haute. Fixer `conviction=0.5`
n'est donc pas un choix neutre : il biaise les deux strates dans des sens
différents. Cette hypothèse doit être déclarée dans la préspécification, pas
enfouie dans le code.

## Suite

Ces deux points sont indépendants des deux audits attendus et sont acquis. Les
réserves 1, 4 et 5 seront traitées dans l'amendement complet, une fois les
audits de Claude et d'Hermès reçus.
