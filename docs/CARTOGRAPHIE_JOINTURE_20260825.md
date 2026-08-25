# Cartographie de jointure et plan de diagnostic — cohorte limite gelée

Mission `629` de Codex : revue fonctionnelle et cartographie causale, en
préparation de la mesure du PnL aval négatif.

**Inspection structurelle uniquement.** Aucun résultat de performance n'est
calculé ni publié : le STOP de l'offset 630 tient jusqu'à la livraison du P0
`441bfea8`. Ce document compte des clés, des orphelins et des champs présents —
il ne décompose aucun R.

Aucun code modifié, aucun changement live, aucun ordre.

---

## 0. Le résultat qui change la donne

Hermes écrit à l'offset 633, point B :

> « Un contrefactuel STRICT “par decision_id, même sortie” n'est pas
> identifiable sur les 690 avec ces seuls fichiers. Une jointure
> symbole/side/temps/contexte serait heuristique. »

**La première phrase est exacte, la conclusion ne l'est pas.** Il n'existe
effectivement aucun `decision_id`. Mais il existe une clé meilleure qu'une
heuristique : le **ticket de position attribué par le courtier**, présent des
deux côtés.

```
limit_lifecycle.position_ticket   89347153     (entier)
trades.ndjson.ticket              live:89347153 (chaîne préfixée)
excursions.ndjson.ticket          live:89347153
```

Le préfixe `live:` est la seule raison pour laquelle la jointure paraissait
impossible. Une jointure naïve rend **0 correspondance sur 373 × 455** ; après
normalisation du préfixe :

| | |
|---|---:|
| limites **closes** | 372 |
| dont jointes à un trade | **372 — 100 %** |
| dont jointes à une excursion | **372 — 100 %** |
| orphelins (limite close sans trade) | **0** |
| collisions côté `trades` | **0** |
| collisions côté `lifecycle` | **0** |

La clé est **unique et complète**. C'est l'audit de reconstructabilité
qu'Hermes exige avant P1 ; il est fourni ici.

---

## 1. Schéma de jointure

```
                    ┌─ placed   (690)  order_ticket, PAS de position_ticket
limit_lifecycle ────┼─ expired  (315)  order_ticket, PAS de position_ticket
                    ├─ filled   (375)  position_ticket  ✔
                    └─ closed   (372)  position_ticket  ✔
                                              │
                            norm(ticket) = split(':')[-1]
                                              │
                          ┌───────────────────┴───────────────────┐
                          ▼                                       ▼
             trades.ndjson.ticket                    excursions.ndjson.ticket
             pnl_r, cost_r, exit_reason,             entry, exit, sl_initial,
             quorum, support_pillars,                tp_initial, mae_r, mfe_r,
             context, mode, asset_class,             censored, indicators,
             timeframe, exact_cost                   entry_levels, horizon_excursions

positions.json  (clé = position_ticket)  porte limit_order_ticket,
limit_planned_price, limit_market_reference_price, limit_target_saving_r,
limit_realized_saving_r, limit_slippage_r → boucle le lien ordre ↔ position.
```

Sur cette cohorte, `order_ticket == position_ticket` : MT5 réutilise le ticket
de l'ordre en attente pour la position issue de son remplissage. Vérifié sur
l'échantillon ; à ne pas supposer vrai en général.

### Contrôles d'intégrité à imposer à tout consommateur

1. **Normaliser avant de joindre.** `norm(t) = str(t).split(':')[-1]`. Une
   jointure sans normalisation rend zéro et ressemble à une absence de données.
2. **Refuser toute jointure heuristique** symbole/temps/side. Elle n'est pas
   nécessaire : la clé exacte existe.
3. **Vérifier la complétude à chaque passe** — 372/372 aujourd'hui. Toute
   valeur inférieure signale une régression de journalisation, pas un manque de
   données.
4. **Compter les collisions**, aujourd'hui nulles des deux côtés. Une collision
   invaliderait l'unicité et donc tout appariement.

---

## 2. Ce qui reste non joignable, et pourquoi c'est structurel

### Les 315 expirations n'ont aucun aval

```
événement    total   avec position_ticket
placed        690            0
expired       315            0
filled        375          375
closed        372          372
```

C'est mécanique : un ordre expiré n'est jamais devenu une position, donc il n'a
ni entrée, ni sortie, ni excursion. **Aucune jointure ne peut créer un aval qui
n'a pas eu lieu.**

Conséquence directe pour P1 : le contrefactuel « qu'aurait fait une entrée
marché à t0 » sur ces 315 décisions **n'est pas une jointure, c'est une
simulation**. Elle exige le chemin L1 depuis `at` jusqu'à la sortie, avec le
même moteur de sortie rejoué. C'est faisable — les quotes sont archivées — mais
cela ne relève pas de cette cartographie.

### Le lien vers le rejeu est impossible, et le restera

Les artefacts de rejeu portent `trade_id = "bt:v2:<sha256>"`, dérivé du
contenu de la décision simulée. Le live porte des tickets courtier. Ces deux
espaces d'identifiants n'ont aucune intersection **par construction** : l'un
nomme une décision reconstituée sur historique, l'autre une exécution réelle.

Il faut donc distinguer deux contrefactuels que la discussion confond parfois :

| | joignable ? |
|---|---|
| **limite vs marché sur la même décision live** | pas par jointure — simulation L1 requise |
| **live vs rejeu sur le même setup** | jamais — espaces d'identifiants disjoints |

Seul le premier est un objectif atteignable. Le second doit être abandonné
explicitement, comme le modèle de file l'a été.

---

## 3. Décomposition proposée du PnL aval

Codex demande une décomposition par setup, régime, actif, sortie, excursion,
spread et contre-tendance. Voici la disponibilité **mesurée** des axes sur les
372 trades joints :

| axe | source | couverture |
|---|---|---:|
| setup / piliers | `trades.support_pillars`, `quorum` | 372/372 |
| contexte (actif, sens, famille) | `trades.context` | 372/372 |
| régime | `limit_lifecycle.regime` | 372/372 |
| classe d'actif | `trades.asset_class` | 372/372 |
| motif de sortie | `trades.exit_reason`, `excursions.exit_reason` | 372/372 |
| excursion | `mae_r`, `mfe_r`, `censored` | 372/372 |
| coût | `trades.cost_r`, `exact_cost` | 372/372 |
| spread à l'entrée | `limit_lifecycle.spread_r` | 372/372 |
| économie réalisée | `realized_saving_r`, `slippage_r` | 372/372 |
| indicateurs (100) | `excursions.indicators` | 372/372 |
| niveaux d'entrée | `excursions.entry_levels` | **241/372 — 65 %** |
| excursions à horizon fixe | `excursions.horizon_excursions` | **228/372 — 61 %** |
| **contre-tendance** | — | **0/372** |

### Deux axes demandés ne sont pas disponibles

**`contre_tendance` : couverture nulle.** Le correctif `4c2ab54` qui le persiste
a été livré aujourd'hui ; aucun trade clos ne le porte encore. L'axe se
peuplera pour les trades à venir, mais **les 372 historiques ne peuvent pas
être ventilés dessus**. Toute décomposition par contre-tendance sur cette
cohorte est impossible, et un lecteur pressé lirait un tableau vide comme
« pas d'effet » au lieu de « pas mesuré ».

**`entry_levels` et `horizon_excursions` sont partiels** (65 % et 61 %). Ce
sont les instruments posés par `ec0c882` le 18/08 ; les trades antérieurs en
sont dépourvus. Toute strate qui les emploie doit publier son effectif réel et
traiter l'absence comme une censure, pas comme un zéro.

### Ordre de diagnostic proposé

1. **Séparer d'abord ce qui est censuré.** `censored` marque les sorties au
   stop ; sans ce partage, toute statistique de MFE est biaisée à la baisse.
2. **Décomposer `pnl_r` en brut moins coût**, puis vérifier que
   `réalisé − économie` reproduit bien le chiffre de Prime (−0,1307 R). C'est
   un contrôle de cohérence, pas un résultat.
3. **Ventiler par `exit_reason` en premier**, avant tout axe de setup : si le
   déficit se concentre sur un motif de sortie, le problème est dans la
   gestion, pas dans l'entrée.
4. **Puis par famille et régime**, avec effectif minimum publié par cellule.
5. **Les axes à couverture partielle en dernier**, et jamais comme
   discriminant principal.

Rien de tout cela n'est exécuté ici — le STOP de l'offset 630 tient.

---

## 4. Vérification fonctionnelle de `epoque_corpus`

Le contrat de Codex (offset 627) et les garde-fous d'Hermes (offset 633 A) sont
cohérents et suffisants sur le fond. J'ajoute quatre **cas de refus
fonctionnels** qui ne figurent dans ni l'un ni l'autre :

1. **Corpus demandé vide.** Si le filtre de symboles ne retient rien, le
   résultat n'est pas « corpus homogène de zéro élément » mais un refus : une
   empreinte de l'ensemble vide serait constante et validerait n'importe quoi.
2. **Corpus partiel silencieux.** Si l'appelant demande 147 symboles et que 12
   manquent, l'empreinte des 135 restants est parfaitement homogène. Il faut
   donc sceller **la liste demandée** en plus de la liste retenue, et refuser
   si l'écart n'est pas explicitement accepté.
3. **Manifeste présent mais artefact absent**, ou l'inverse. Le sceau doit
   porter sur le couple, sinon un manifeste orphelin certifie un vide.
4. **Deux passes du même symbole à des époques différentes** dans le même
   dossier. Le cas s'est déjà produit : `results/rejeu_univers` a mélangé trois
   générations. Le refus doit nommer le symbole fautif, pas seulement
   « générations mixtes ».

Sur le point 5 de Codex — ne jamais assimiler `symbols_measured = 0` à un
résultat — je souscris et je durcis : le statut `ANALYSIS_BLOCKED` doit être
**dans le rapport publié**, pas seulement dans le code de sortie. Un rapport
JSON lu par un humain ou un tableau de bord ne voit pas le code de retour du
processus. C'est exactement le mode de panne qui a duré de 08:08 à ce matin
sans que 2301 tests le remarquent.

---

## 5. Ce que je ne fournis pas

- **Aucune décomposition chiffrée** du −0,0431 R. Le STOP tient.
- **Aucun verdict sur le commit P0 de Prime** : il n'est pas encore publié.
  Il viendra en ACCEPT/AMEND/BLOCK dès son handoff.
- Je n'ai pas vérifié que `order_ticket == position_ticket` soit une garantie
  MT5 plutôt qu'une coïncidence de cette cohorte. Tant que la jointure passe
  par `position_ticket`, cela n'a pas d'incidence.

---

## 6. Reproduire

```python
norm = lambda t: str(t).split(":")[-1]
cl = {norm(x["position_ticket"]) for x in lifecycle if x["event"] == "closed"}
T  = {norm(x["ticket"]) for x in trades}
len(cl), len(cl & T), len(cl - T)      # 372, 372, 0
```

Fichiers : `results/limit_lifecycle.ndjson`, `results/trades.ndjson`,
`results/excursions.ndjson`, `results/positions.json`.
