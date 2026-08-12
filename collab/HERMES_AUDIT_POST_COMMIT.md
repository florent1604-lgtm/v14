# Audit Hermes post-commit — chemin critique V14

**Date :** 12/08/2026  
**Mission :** P0 `46c06912-a79d-4d40-b818-0f5d2612eff3`  
**Rôle :** Hermes C1 consultatif  
**Périmètre :** post-commit `5c5884e`, état courant final relu `master@59edde5` ; PAPER/DÉMO uniquement ; aucun ordre, aucun redémarrage, aucun seuil modifié.

## Verdict exécutif

**NO-GO promotion, GO collecte DÉMO sous gardes inchangées.**

Le chemin statique `portabilité → features/piliers → confluence → orchestrateur/RiskGate → gardes post-ENTER → ordre limite → contexte pending` est bien câblé. Sur le run initial post-commit, le relevé était **11 112 évaluations portables, 1 857 ENTER et 0 ordre envoyé**. Après un rechargement effectué par un autre acteur pendant l'audit, le heartbeat frais a montré **1 limite naturellement placée** en 3 tours / 28 ENTER. Le placement est donc désormais prouvé, mais pas encore le fill, l'adoption du contexte pending ni l'économie réalisée.

La cohorte live demeure défavorable : **29 clôtures, -9,6826 R, -0,3339 R/trade, PF 0,436, drawdown 13,6666 R, coûts exacts 0/29**. Aucun contexte ne dépasse trois clôtures. Rien ne justifie de desserrer quorum, RiskGate, concentration ou réserve S3.

| Actif | Walk-forward net du spread | Verdict mesuré |
|---|---:|---|
| XAUUSD | 753 trades, 34 fenêtres OOS, 30 positives / 4 négatives, espérance OOS moyenne +0,2299 R | **GO DÉMO ciblé**, **NO-GO PROD** tant que MT5 natif/live et coûts exacts ne confirment pas |
| ETHUSD | 637 trades, 28 fenêtres, 17 positives / 11 négatives, moyenne +0,1002 R mais dernière fenêtre -0,4220 R | **NO-GO promotion** ; régime récent dégradé, coût élevé et instabilité |
| AUDUSD | 653 trades, 29 fenêtres, 10 positives / 19 négatives, moyenne -0,0422 R | **NO-GO** ; contrôle négatif confirmé |

## 1. Sources et preuves exécutées

- GitNexus : `context(tour)`, `context(place_limit_order)`, `context(run_once)`, `context(confluence_gate.evaluate)`, `context(RiskGate.evaluate)` sur `titanium-v14`.
- État runtime : `results/loop_heartbeat.json` lu pendant l'audit.
- Cohorte live : `results/trades.ndjson` et `collab/CODEX_EDGE_SNAPSHOT.md`.
- Walk-forward réellement rejoué :

```text
.venv/Scripts/python.exe tools/walk_forward.py XAUUSD --output results/walk_forward_hermes_59edde5
.venv/Scripts/python.exe tools/walk_forward.py ETHUSD --output results/walk_forward_hermes_59edde5
.venv/Scripts/python.exe tools/walk_forward.py AUDUSD --output results/walk_forward_hermes_59edde5
```

Le premier appel groupé a dépassé 600 s après avoir produit XAUUSD et ETHUSD. Après le commit concurrent `59edde5`, les trois actifs ont été **rejoués séparément avec le même défaut de 12 000 barres** dans `results/walk_forward_hermes_59edde5/`. Ce second lot homogène est celui qui fait foi dans les tableaux ci-dessous.

- Tests ciblés réellement exécutés :

```text
.venv/Scripts/python.exe -m pytest \
  tests/test_walk_forward.py tests/test_limit_orders.py \
  tests/test_cost_conventions.py tests/test_live_demo_telemetry.py -q
# 18 passed in 2.00s
```

Une première commande incluait par erreur `tests/test_pending_context.py`, fichier absent ; elle n'a exécuté aucun test. La commande corrigée ci-dessus est celle qui fait foi.

### Réserve GitNexus

Le `sync` obligatoire a rencontré une incohérence FTS (`file_fts`, termes manquants `overview` puis `testgrapp`) et `--repair-fts` a refusé car l'index était marqué en récupération incrémentale. Malgré cela, les requêtes CLI structurelles ont répondu sur l'index au commit alors courant `b4ed9be` et ont retrouvé les symboles exacts. Un commit concurrent `59edde5` a ensuite rendu cet index stale. Le statut d'équipe reste `synced=false` et le service MCP était arrêté au relevé : **la partie graphe était exploitable pour le chemin préexistant, mais la santé FTS et l'indexation du dernier commit ne sont pas validées**. Aucun succès GitNexus complet n'est revendiqué.

## 2. Audit du chemin critique

### 2.1 Portabilité

Le heartbeat observé pendant l'audit indiquait :

- 263 tours ;
- 11 112 évaluations portables cumulées ;
- 1 857 ENTER, soit **16,71 %** des portables ;
- 0 ordre envoyé sur le run post-commit ;
- equity DÉMO observée : 4 407,66 EUR.

Les refus de portabilité sont nommés (`COUT_SPREAD`, `MARCHE_FERME`, `ATR_INDISPONIBLE`). La portabilité est un filtre binaire préalable ; elle ne choisit pas l'ordre d'allocation entre survivants.

### 2.2 Piliers et confluence

Sur le même relevé :

- G4 OTE/OB manquant : **91,65 %** des portables ;
- G5 bougie manquante : **90,63 %** ;
- verdicts ENTER : 1 857 ; BLOCK : majorité du flux ;
- la porte EXPLORE conserve son quorum de deux piliers.

GitNexus confirme que `tour()` appelle le calcul des piliers puis `run_once()`, et que `confluence_gate.evaluate()` reste la porte déterministe. Les taux G4/G5 élevés réduisent le débit mais ne prouvent pas un défaut à corriger par assouplissement. Avec l'edge live négatif, **ne pas baisser le quorum** est la décision la moins risquée.

### 2.3 Orchestrateur, RiskGate et post-ENTER

`run_once()` applique les portes, la délibération bornée et le RiskGate ; le LLM ne peut ni inverser le sens ni sauver un setup refusé. Le RiskGate demeure souverain.

Refus post-ENTER au relevé :

| Motif | Nombre | Part des 1 857 ENTER |
|---|---:|---:|
| RISKGATE_DENY | 844 | 45,45 % |
| EXECUTION | 650 | 35,00 % |
| MAX_PAR_SYMBOLE | 190 | 10,23 % |
| DERIVE | 169 | 9,10 % |
| COUT_SPREAD | 4 | 0,22 % |

Ces compteurs sont des occurrences par tour, pas des opportunités indépendantes. Les mêmes candidats peuvent être recomptés. Ils ne justifient donc pas d'estimer « 650 trades perdus » ni d'assouplir les gardes.

Codex a ajouté la ventilation `execution_refusal.<reason>` / `execution_gate_failed.<gate>`, mais elle exige un rechargement contrôlé avant d'expliquer les 650 refus futurs. Les refus historiques agrégés ne sont pas reconstruisibles honnêtement.

### 2.4 Exécution limite et contexte pending

Le câblage exact est confirmé :

1. `tour()` appelle `place_limit_order()` après toutes les portes et gardes ;
2. `place_limit_order()` réapplique le mur DÉMO, calcule le lot, construit une limite passive, envoie un ordre pending expirant et n'accepte que `PLACED`/`DONE` ;
3. en cas de succès, `_memoriser_contexte_limit()` appelle `save_pending_context()` ;
4. au tour suivant, `reconcile_pending_contexts()` rattache le contexte au fill ou purge l'expiration.

Le runtime initial ne fournissait aucune preuve dynamique. Un heartbeat ultérieur, après rechargement externe, indique `envoyes=1` et `limites_placees=1` : l'acceptation naturelle d'une limite par le courtier est désormais prouvée. Aucun fichier `pending_limits.json` n'était cependant visible au relevé suivant et aucun compteur `limites_executees` n'était publié. Le fill, l'adoption du contexte pending, le taux d'expiration et l'économie réalisée restent donc non prouvés.

## 3. Walk-forward par actif

Configuration fixe : M15/H4, fenêtres de 60 trades IS + 20 OOS, pas 20, spread courtier inclus dans `pnl_r`.

| Actif | Trades | Fenêtres | OOS + / - | Espérance OOS moyenne | Médiane OOS | PF OOS médian | Dernière OOS / Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| XAUUSD | 753 | 34 | 30 / 4 | +0,2299 R | +0,1930 R | 1,548 | +0,4363 R / +0,373 |
| ETHUSD | 637 | 28 | 17 / 11 | +0,1002 R | +0,1088 R | 1,267 | -0,4220 R / -0,465 |
| AUDUSD | 653 | 29 | 10 / 19 | -0,0422 R | -0,0648 R | 0,855 | -0,0576 R / -0,071 |

### Interprétation

- **XAUUSD** est le seul candidat robuste dans ce test Python : 88 % des fenêtres OOS sont positives. Réserve majeure : un ancien écart Python↔MT5 est documenté dans V14 ; le testeur natif et la cohorte live doivent confirmer avant toute promotion.
- **ETHUSD** a une moyenne positive, mais 39 % des fenêtres sont négatives et la dernière est fortement négative. Le coût moyen est élevé ; le résultat est instable et sensible au régime.
- **AUDUSD** échoue : 66 % de fenêtres négatives et moyenne négative. Il sert de contrôle montrant que le pipeline ne rend pas tous les actifs artificiellement gagnants.

Les fenêtres se chevauchent : elles ne constituent pas 34, 28 ou 19 expériences indépendantes. Aucun p-value, DSR/PBO ni bootstrap par blocs n'est fourni par ce pipeline ; les métriques sont donc diagnostiques.

## 4. Quantification de l'impact des ordres limites

### Formule réellement codée

Pour un spread relatif `c = spread / R` :

```text
économie visée en R = c + min(0,05 ; max(0 ; c - 0,05))
```

- si `c ≤ 5 %R`, l'économie visée est un spread complet ;
- au-delà, la limite demande jusqu'à 5 %R supplémentaires ;
- ce chiffre est un **gain conditionnel au fill**, pas une espérance réalisée.

En appliquant cette formule aux coûts OOS observés :

| Actif | Coût OOS moyen | Économie limite visée moyenne, conditionnelle au fill | Espérance OOS + économie théorique maximale* |
|---|---:|---:|---:|
| XAUUSD | 0,0112 R | 0,0112 R | +0,2412 R |
| ETHUSD | 0,1512 R | 0,2012 R | +0,3014 R |
| AUDUSD | 0,0901 R | 0,1265 R | +0,0843 R |

\* Borne contrefactuelle optimiste : suppose 100 % des limites remplies, mêmes trades, mêmes sorties et aucune sélection adverse. **Ce n'est pas une prévision.**

### Conclusion sur les limites

- XAUUSD : le gain théorique est faible (~0,011 R/trade) ; les limites ne sont pas la source principale de l'edge observé.
- ETHUSD/AUDUSD : le gain théorique paraît grand parce que leur spread relatif est grand. C'est précisément là que la probabilité de non-fill et la sélection adverse sont les plus importantes. Ajouter mécaniquement l'économie au PnL surestime donc fortement l'effet.
- Pour AUDUSD, la borne optimiste peut rendre la moyenne légèrement positive, mais ce résultat n'est pas exploitable : le pipeline remplacerait des entrées au marché par un sous-échantillon de fills inconnus.

La mesure valide à collecter est : `placed → filled/expired → fill_rate → saving_r réalisé → slippage → pnl_r`, par actif et régime. Sans ce dénominateur, l'ordre limite est une hypothèse de réduction de coût, pas un edge.

## 5. Risques classés

| Rang | Risque | Niveau | Preuve / conséquence |
|---|---|---|---|
| R1 | Edge live négatif | **CRITIQUE promotion** | 29 clôtures, -0,3339 R, PF 0,436, DD 13,67 R |
| R2 | Coûts exacts absents | **ÉLEVÉ** | 0/29 ; impossible d'attribuer précisément spread/slippage/commission |
| R3 | Chemin limite seulement partiellement exercé | **ÉLEVÉ** | 1 placement naturel prouvé ; fill/adoption/économie non prouvés |
| R4 | Instabilité ETHUSD / changement de régime | **ÉLEVÉ** | dernière OOS -0,422 R, 11/28 fenêtres négatives |
| R5 | Surapprentissage / fenêtres chevauchantes | **ÉLEVÉ** | absence de PBO/DSR/bootstrap blocs dans ce walk-forward |
| R6 | Rejeu Python ≠ microstructure MT5 | **ÉLEVÉ** | fill intra-barre, non-fill et sélection adverse non modélisés |
| R7 | Débit faible des piliers | **MOYEN** | G4/G5 manquants ~92/91 %, mais desserrer empirerait le risque |
| R8 | GitNexus FTS/récupération incohérente | **MOYEN ingénierie** | structure consultable, sync d'équipe non validée |

### Changement concurrent observé pendant l'audit

Le commit `59edde5` a ajouté un secours `displacement` pour G5, avec A/B annoncé : G5 11,9 %→23,7 % et quorum 19,3 %→24,4 % sur 135 setups, sans toucher au quorum. Un autre acteur a ensuite rechargé la boucle vers 13:41 ; le heartbeat neuf confirme que le nouveau chemin tourne et a placé une limite. Les compteurs cumulés de 263 tours cités plus haut restent ceux de l'ancien processus et ne doivent pas être mélangés avec la nouvelle époque. Le walk-forward final a bien été rejoué sur `59edde5`, mais il n'isole pas causalement l'effet du seul secours G5.

## 6. Décision et critères de réouverture

### Décision immédiate

1. **Maintenir la collecte DÉMO**, sans forcer d'ordre.
2. **Conserver tous les gardes et le quorum**.
3. **Promotion fermée** pour les trois actifs et pour V14 globalement.
4. Priorité de mesure : XAUUSD en DÉMO/MT5 natif ; ETHUSD uniquement en observation de régime ; AUDUSD reste contrôle négatif.

### Gates nécessaires avant reconsidération

- au moins 60 clôtures propres par cellule candidate ;
- au moins 90 % de couverture des coûts selon un contrat versionné bid/ask/fill ;
- réconciliation ticket 100 % ;
- fill rate et expiration des limites mesurés, avec comparaison à un shadow market contrefactuel causal ;
- bootstrap par blocs + purge/embargo ; PBO/DSR ou équivalent préenregistré ;
- confirmation MT5 native de XAUUSD ;
- borne inférieure de l'espérance nette > 0 et PF > 1 après coûts, sans dépendre d'une seule fenêtre ;
- aucune dégradation des murs DÉMO/réel, RiskGate, concentration ou réserve S3.

## Conclusion

Le commit apporte un chemin d'ordre limite cohérent et testable, avec désormais **un placement naturel prouvé**, mais sans preuve de fill ni d'économie réalisée. L'edge live actuel est négatif et statistiquement insuffisant. Le walk-forward identifie **XAUUSD comme seule piste prioritaire**, **ETHUSD comme instable** et **AUDUSD comme no-go**. Les limites peuvent réduire le coût conditionnel au fill ; elles ne transforment pas, à elles seules, une stratégie perdante en stratégie validée.
