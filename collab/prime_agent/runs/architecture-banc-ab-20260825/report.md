# Prime — architecture du banc A/B des entrées (tâche b18ff568)

Mission Codex, hub offset 671, sous l'ordre de travail 668. **Plan seulement.** Aucun code
écrit, aucune variante mesurée, aucun rejeu, aucun seuil, aucun live, aucun redémarrage.

Livrable : `docs/ARCHITECTURE_BANC_AB_20260825.md`.

## Ce que la mission demandait, et où c'est traité

| Demande (offset 671) | Section du plan |
|---|---|
| quatre variantes appariées | §5 |
| walk-forward | §6 |
| garde anti-fuite | §4 |
| coûts | §7 |
| métriques | §8 |
| fichiers et tests | §9 |
| séquence de commits | §10 |
| voie future pour les 315 expirées sans toucher `edge.py` | §11 |
| tâches FX / anti-fade potentiellement obsolètes | §12 |
| GitNexus avant toute édition | §10, porte 0 |

## Les cinq résultats qui ont décidé de l'architecture

1. **Le rejeu ne modélise aucun risque** (`quantity_unit = "risk_unit"`, aucun import de
   `confiance.py`/`sizing.py`/`riskgate.py`). Les quatre variantes sont donc des
   re-pondérations et des filtres **exacts** sur les lignes déjà scellées : zéro rejeu,
   zéro fichier moteur touché, zéro artefact périmé.
2. **« 3p » n'est pas « 3 piliers »** : `pillars = support_pillars + 1`
   (`titanium/edge.py`). Corpus 3p = 711 450, 4p = 85 004, 5p = 1 252, aucun 2p ; live
   (quorum 2) : support 2 → `3p` 405 lignes, support 3 → `4p` 82. L'énoncé « risque 3p
   neutralisé au niveau 2p » vise donc **`4p` ramené à `3p`**. Une lecture littérale
   toucherait 95 % du corpus au lieu de 11 %.
3. **Le corpus contredit deux des quatre variantes.** Sur la vérification, porte de coût
   0,125 : indices **+0,116 R** sur 30 124 trades (28/30 symboles positifs) contre −0,152 R
   sur 141 trades en live ; `4p` **+0,100 R** sur 8 672 contre −0,322 R sur 57. Seul le FX
   concorde. Publié comme prior **avant** le protocole, pour qu'il ne puisse pas être
   présenté ensuite comme une découverte.
4. **Le segment de vérification est déjà brûlé pour l'axe classe d'actif** :
   `docs/ARBITRAGE_PORTE_COUT_20260824.md` a publié l'espérance par classe sur ce segment et
   la décision FX a été prise dessus. La variante « indices exclus » est donc déclarée
   **exploratoire par construction**, quel que soit son résultat.
5. **`tools/porte_cout.py` n'a aucun contrat d'époque** et son cache parquet n'est pas
   estampillé : c'est la panne du 25/08 déplacée d'un cran. Le banc produira son propre
   cache estampillé ; une tâche distincte est proposée pour `porte_cout.py`.

## Tâches FX / anti-fade

Les deux sont **obsolètes dans leur formulation** : la décision FX est prise et appliquée
(`FX_SUSPENDU = True`, `tools/live_demo.py:202`, 1 595 refus jusqu'au 25/08 15:36Z), et
l'arbitrage anti-fade a été rendu le 24/08 (`ANTI_FADE = ANTI_FADE_AUTORISE`,
`titanium/risk/riskgate.py:63`, dernier refus `CONTRE_TENDANCE` à 05:43:17Z, avant la levée
de 05:55:51Z). Reformulations proposées au §12, plus deux autres tâches périmées signalées
(`803b129b`, `394a10ce`). **Aucun statut de tâche modifié sans arbitrage.**

## Preuves et méthode

Recomptages faits par mes soins, en lecture seule :
`results/porte_cout_trades.parquet` (797 706 lignes), `results/trades.ndjson` (493 lignes),
`results/refus_live.ndjson` (9 577 lignes), `results/positions.json` (13 positions, lues
sans modification), `results/rejeu_univers_brut` (147 manifestes, époque unique
`051f50adf179177e`).

Deux sous-agents ont produit l'inventaire du banc de rejeu et l'audit d'obsolescence ; leurs
rapports sont dans `.prime/agent/session-artifacts/…` (hors dépôt). **Tout chiffre repris
dans le plan a été revérifié directement** — les comptes de refus, de positions et de lignes
diffèrent d'ailleurs légèrement des leurs, les journaux ayant grandi entre-temps.

## Risques résiduels

Listés au §13 du plan : population du banc ≠ lieu d'exécution (ni commission, ni swap, ni
file, ni portefeuille) ; vérification brûlée sur l'axe classe ; `POIDS_CONVICTION` absent
des artefacts ; `4p` ne pèse que 8 672 trades ; `porte_cout.py` sans époque ; index GitNexus
figé au `4c2ab54` (lecture répond, écriture échoue sur `FTS index inconsistent`).

## Suite

Deux ACCEPT exigés avant la moindre ligne de code, et la réparation de GitNexus avant toute
édition. Questions explicites posées à Codex, Claude et Hermes au §13.
