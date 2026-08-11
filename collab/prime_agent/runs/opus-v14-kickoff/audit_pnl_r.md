# Audit — `pnl_r` aberrants et `cost_r = 0.0` dans `results/trades.ndjson.avant_purge`

**Mission** : Prime Agent, audit READ-ONLY, PAPER/DEMO only.
**Date d'analyse** : session `opus-v14-kickoff`.
**Périmètre lu** : `tools/live_demo.py`, `titanium/execution/position_manager.py`,
`titanium/edge.py`, `titanium/backtest.py`, `titanium/analysis/reconciliation.py`,
`titanium/analysis/promotion.py`, `tests/test_journal_live.py`,
`results/trades.ndjson.avant_purge`, `results/excursions.ndjson.avant_purge`,
`results/reconciliation_mt5.json`, `results/positions_avant_strate_20260807_1338.json`,
`docs/LECONS.md`, `V14_export.json` (contient un instantané du code d'époque).
**Aucun fichier du projet n'a été modifié.** Seul ce rapport a été écrit.
Interpréteur utilisé pour toute exécution : `C:/Users/flore/Desktop/V14/.venv/Scripts/python.exe`.

---

## 0. Résumé exécutif

1. **La cause racine des `pnl_r` à 10^8 n'est PAS un `r` (distance de stop) trop petit.**
   Les trois trades incriminés ont un `r/entry` de 3,0 à 6,3 × 10⁻⁴ — parfaitement
   plausible, et **à l'intérieur** des bornes `R_MIN_RELATIF..R_MAX_RELATIF` posées après
   coup. La cause racine est un **prix de sortie appartenant à un AUTRE symbole** :
   les trois lignes ont été calculées avec le prix de clôture du **GER40 (26 333,85)**,
   alors qu'elles portent sur EURGBP (0,857), EURUSD (1,152) et AUDUSD (0,703).
   Le commentaire de `position_manager.py:396-402` et la leçon `E8` de `docs/LECONS.md`
   attribuent l'incident à un « `r` résiduel infinitésimal » : **ce diagnostic est faux**,
   et il a orienté le correctif vers un garde-fou qui ne touche pas la cause.
2. **`cost_r` vaut 0.0 partout pour deux raisons cumulées** : (a) à l'époque de ces lignes,
   `manage_once` appelait `journaliser_cloture` **sans** l'argument `cost_r` (valeur par
   défaut `0.0`) ; (b) même dans le code d'aujourd'hui, `cost_r` ne contient **que**
   commission + swap + fee, et le compte démo Axi les rend **toutes à 0.00** sur les
   54 positions du rapprochement. Le **spread**, seul coût réellement payé, n'entre
   jamais dans `cost_r` côté live — alors qu'il y entre côté backtest.
3. **`titanium/edge.py` ne filtre rien** : le seul rejet est `math.isfinite` (ligne 135).
   Une valeur de 10⁸ R est finie, donc acceptée, moyennée, et **retourne un `edge_ok=True`**.
   Preuve exécutée plus bas : 20 trades à −0,5 R + 1 trade corrompu ⇒ `edge_ok = True`
   avec un winrate de 4,8 %.

---

## 1. Les données brutes

### 1.1 Le journal (`results/trades.ndjson.avant_purge`, 7 lignes)

```json
{"context": "ETHUSD|?|?|0p",                "pnl_r": 0.3031,          "exit_reason": "init",      "cost_r": 0.0, "ticket": "live:86630768"}
{"context": "EURUSD|long|continuation|4p",  "pnl_r": 0.4694,          "exit_reason": "trailing",  "cost_r": 0.0, "ticket": "live:86755928"}
{"context": "AUDUSD|long|continuation|3p",  "pnl_r": 0.1087,          "exit_reason": "breakeven", "cost_r": 0.0, "ticket": "live:86755936"}
{"context": "GER40|long|continuation|3p",   "pnl_r": 0.4811,          "exit_reason": "init",      "cost_r": 0.0, "ticket": "live:86755945"}
{"context": "EURGBP|long|continuation|3p",  "pnl_r": 101280739.7308,  "exit_reason": "init",      "cost_r": 0.0, "ticket": "live:86776744"}
{"context": "EURUSD|long|continuation|3p",  "pnl_r": 52665394.46,     "exit_reason": "init",      "cost_r": 0.0, "ticket": "live:86793654"}
{"context": "AUDUSD|long|continuation|3p",  "pnl_r": 59848059.2727,   "exit_reason": "init",      "cost_r": 0.0, "ticket": "live:86798362"}
```

### 1.2 Le fichier jumeau `results/excursions.ndjson.avant_purge` — la preuve directe

C'est lui qui porte les entrées/sorties, et il désigne le coupable :

| ticket | symbole | `entry` | `exit` **journalisé** | `r_unit` | `pnl_r` |
|---|---|---|---|---|---|
| 86755945 | **GER40** | 26 313,65 | **26 333,85** | 41,99 | 0,4811 |
| 86776744 | EURGBP | 0,85767 | **26 333,85** ⟵ | 0,00026 | 101 280 739,73 |
| 86793654 | EURUSD | 1,15277 | **26 333,85** ⟵ | 0,00050 | 52 665 394,46 |
| 86798362 | AUDUSD | 0,70392 | **26 333,85** ⟵ | 0,00044 | 59 848 059,27 |

Les quatre lignes portent en outre **exactement le même `ts_exit` : `2026-08-07T13:16:17+00:00`**,
celui du GER40.

Reproduction arithmétique exacte (formule `position_manager.py:424`) :

```
(26333.85 - 0.85767) / 0.000260000000000038 * 1 = 101280739.73075442   ✓ = 101280739.7308
(26333.85 - 1.15277) / 0.000500000000000167 * 1 =  52665394.45998241   ✓ =  52665394.4600
(26333.85 - 0.70392) / 0.00043999999999999595 * 1 = 59848059.27272782  ✓ =  59848059.2727
```

La formule n'est pas en cause : **son entrée `prix_sortie` est celle d'une autre position**.

### 1.3 La vérité MT5 (`results/reconciliation_mt5.json`) contredit le journal

```
86776744 EURGBP  ouvert 12:38:49  clos 2026-08-07T13:16:14+00:00  net +7.74  CLIENT  2 deals
86798362 AUDUSD  ouvert 13:14:19  clos 2026-08-07T13:16:15+00:00  net +3.38  CLIENT  2 deals
86793654 EURUSD  ouvert 13:03:55  clos 2026-08-07T13:16:16+00:00  net +1.98  CLIENT  2 deals
86755945 GER40   ouvert 11:59:16  clos 2026-08-07T13:16:17+00:00  net +8.08  CLIENT  2 deals
```

Quatre clôtures **manuelles** (`close_reason: CLIENT`) en quatre secondes : 13:16:**14**,
**15**, **16**, **17**. Le journal a horodaté les quatre à **13:16:17** et leur a donné le
prix du dernier deal du lot — celui du GER40. Le vrai résultat de EURGBP était **+7,74 USD**
sur ~24 USD de risque, soit **≈ +0,32 R**, pas +101 280 739 R.

---

## 2. Cause racine n°1 — le prix de sortie n'était pas filtré par ticket

### 2.1 Le chemin de calcul (code actuel, `titanium/execution/position_manager.py`)

```python
686 |    for tk in [t for t in etat if t not in vivants]:
687 |        st = etat[tk]
688 |        prix, quand, frais, net = _cloture_depuis_historique(mt5, tk)
...
696 |        ecrit = journaliser_cloture(
697 |            st, tk, prix_sortie=prix, ts_exit=quand,
698 |            journal_path=cible_journal, cost_r=cout_r,
699 |            net_devise=net if frais or net else None,
700 |            diagnostic=diagnostic,
701 |        )
```

```python
420 |        elif prix_sortie is not None and st.entry > 0:
421 |            # ── Repli : reconstruction par les prix. Le sens vient de l'ÉTAT,
422 |            #    figé à l'ouverture. Le déduire du résultat
423 |            #    (`1 if entry < sortie else -1`) rendrait tout trade gagnant.
424 |            pnl_r = (prix_sortie - st.entry) / st.r * st.side
```

`prix_sortie` vient donc **entièrement** de `_cloture_depuis_historique` :

```python
309 | def _cloture_depuis_historique(
310 |         mt5, ticket: str) -> tuple[float | None, str, float, float]:
...
327 |        fin = datetime.now(timezone.utc) + timedelta(days=1)
328 |        debut = fin - timedelta(days=30)
329 |        deals = mt5.history_deals_get(debut, fin, position=int(ticket))
330 |        if not deals:
331 |            return None, "", 0.0, 0.0
...
346 |            # DEAL_ENTRY_OUT == 1. Plus sûr que « le dernier deal » : une
347 |            # position peut être clôturée en plusieurs fois, et un deal de
348 |            # correction postérieur porterait un prix qui n'est pas la sortie.
349 |            if int(getattr(d, "entry", -1) or -1) == 1:
350 |                sortie = d
351 |
352 |        if sortie is None:
353 |            sortie = max(deals, key=lambda d: getattr(d, "time", 0))
```

### 2.2 Ce que faisait le code AU MOMENT de l'incident

`V14_export.json` conserve un instantané de la version d'époque de la même fonction.
La sélection du deal de sortie y est **uniquement temporelle**, sans filtre `DEAL_ENTRY_OUT` :

```python
     # Le dernier deal de la position est celui qui la ferme.
     sortie = max(deals, key=lambda d: getattr(d, "time", 0))
     quand = datetime.fromtimestamp(
         float(getattr(sortie, "time", 0) or 0), tz=timezone.utc).isoformat()
     return float(getattr(sortie, "price", 0.0) or 0.0), quand, frais
```

Le commentaire « **le dernier deal de la position** » est une hypothèse non vérifiée par
le code : si `deals` n'est pas restreint à la position — filtre `position=` absent, ignoré
par le terminal, ou `ticket` non convertible — alors `max(deals, key=time)` rend
**le deal le plus récent de TOUT le compte**.

C'est exactement la signature observée :

- 86755928 (EURUSD, clos 13:03:26) et 86755936 (AUDUSD, clos 13:14:02) ont un prix de
  sortie **correct** : au moment où la boucle les a purgés, leur propre deal **était** le
  plus récent du compte ;
- les quatre positions closes manuellement entre 13:16:14 et 13:16:17 ont été purgées
  **dans le même passage**, alors que le deal le plus récent du compte était celui du
  GER40 (13:16:17). Les trois autres ont donc hérité de **son prix et de son horodatage**.

C'est une contamination **inter-symboles**, structurelle : elle ne se déclenche que
lorsque plusieurs positions se ferment entre deux passages de `manage_once`, ce qui est
précisément le cas d'une fermeture groupée ou d'un décrochage MT5.

### 2.3 Pourquoi les garde-fous ajoutés APRÈS ne visent pas la cause — vérifié par exécution

```python
287 | #: Bornes de plausibilité du R, en fraction du prix d'entrée.
294 | R_MIN_RELATIF = 1e-5
295 | R_MAX_RELATIF = 0.5
297 | #: Au-delà, le résultat est incohérent quelle que soit l'origine.
298 | PNL_R_MAX = 50.0
...
396 |        # ── Le R doit être une distance PLAUSIBLE au regard du prix d'entrée.
397 |        #    Un `r` résiduel (stop normalisé sur le prix, arrondi flottant)
398 |        #    reste positif mais infinitésimal, et la division produit alors des
399 |        #    résultats aberrants — +101 280 739 R observés le 07/08/2026, qui
400 |        #    ont écrasé toute mesure d'edge du registre.
403 |        if st.entry > 0:
404 |            ratio = st.r / abs(st.entry)
405 |            if not (R_MIN_RELATIF <= ratio <= R_MAX_RELATIF):
406 |                if diagnostic is not None:
407 |                    diagnostic.update(reason="R_HORS_BORNES", permanent=True)
408 |                return False
```

Rejeu des trois cas réels avec le code actuel
(`.venv/Scripts/python.exe`, journal écrit dans un dossier temporaire hors projet) :

```
R_MIN_RELATIF 1e-05  R_MAX_RELATIF 0.5  PNL_R_MAX 50.0
EURGBP: r/entry=3.031e-04  dans bornes R=True | pnl_r brut=101280739.7308 | journalise=False diag={'reason': 'PNL_R_HORS_BORNES', 'permanent': True}
EURUSD: r/entry=4.337e-04  dans bornes R=True | pnl_r brut= 52665394.4600 | journalise=False diag={'reason': 'PNL_R_HORS_BORNES', 'permanent': True}
AUDUSD: r/entry=6.251e-04  dans bornes R=True | pnl_r brut= 59848059.2727 | journalise=False diag={'reason': 'PNL_R_HORS_BORNES', 'permanent': True}
```

**Le test de plausibilité du `r` laisse passer les trois cas** (`dans bornes R=True`).
Seul `PNL_R_MAX` les arrête — c'est-à-dire un filet posé sur le **symptôme**, en aval.
Conséquence concrète, déjà payée : le trade est refusé **définitivement**
(`MOTIF_REFUS_DEFINITIF`, lignes 302-306), son état est conservé, et c'est l'interblocage
`JOURNAL_GAP` documenté en `E8` de `docs/LECONS.md`. Sur la donnée réelle, ce garde-fou
**jette une mesure qui était récupérable** : MT5 donnait +7,74 USD pour EURGBP.

> **Correction à apporter à `docs/LECONS.md` (E8) et au commentaire 396-402** : la valeur
> `+101 280 739 R` ne vient pas d'un `r` infinitésimal mais d'un **prix de sortie
> emprunté à une autre position**. Un `r` de 2,6 pips sur EURGBP est normal.

### 2.4 Le test ne pouvait pas voir la faute

```python
284 |    def history_deals_get(self, *a, **k):
285 |        class D:
286 |            time = 1_770_000_000
287 |            price = 1.1150
288 |        return [D()]
```
(`tests/test_journal_live.py`, également lignes 442 et 607 :
`lambda *a, **k: deals`)

Le double de MT5 **avale `*a, **k` et ignore `position=`**. Aucun test de la suite ne peut
donc détecter l'absence, la faute ou l'inefficacité du filtre par ticket, ni une
contamination inter-positions. La suite est verte quel que soit le filtre.

### 2.5 Le rapprochement n'a pas non plus vu la faute

```python
183 |        risk_money = _number(getattr(trade, "risk_money", 0.0))
184 |        if not bool(getattr(trade, "exact_cost", False)):
185 |            exact_cost_missing.append(position_id)
186 |        if risk_money <= 0:
187 |            continue
188 |        expected_r = mt5_row.net_currency / risk_money
189 |        journal_r = _number(getattr(trade, "pnl_r", 0.0))
190 |        delta_r = journal_r - expected_r
191 |        if abs(delta_r) > 0.02:
192 |            pnl_mismatches.append({...})
```
(`titanium/analysis/reconciliation.py`)

Le contrôle croisé MT5 ↔ journal aurait détecté l'écart (0,32 R attendu contre 10⁸ R
journalisé), **mais il est court-circuité ligne 187** : les lignes d'époque ne portent pas
`risk_money` (relecture confirmée : `risk_money=0.0` pour les 7 lignes). `pnl_mismatches`
reste donc vide. Un `pnl_r` absurde doit être détectable **sans** dépendre d'un champ
optionnel.

---

## 3. Cause racine n°2 — `cost_r` est structurellement nul en live

### 3.1 Le calcul, aujourd'hui

```python
690 |        # Frais en devise → en R. Sans le risque engagé la conversion est
691 |        # impossible : on journalise alors zéro, ce qui est faux mais visible,
692 |        cout_r = 0.0
693 |        if st.risque_devise > 0 and frais:
694 |            cout_r = abs(frais) / st.risque_devise
```

```python
336 |        for d in deals:
337 |            for champ in ("commission", "swap", "fee"):
338 |                try:
339 |                    frais += float(getattr(d, champ, 0.0) or 0.0)
```

```python
433 |        cost_r = abs(float(cost_r or 0.0))
434 |        if not exact:
435 |            # Sur la voie exacte, les frais sont DÉJÀ dans le net : les
436 |            # retrancher une seconde fois les compterait double.
437 |            pnl_r -= cost_r
```

`cost_r` = (commission + swap + fee) / risque_devise. **Rien d'autre.**

### 3.2 Trois raisons cumulées pour lesquelles il vaut 0.0

1. **À l'époque des lignes auditées, l'argument n'était pas passé.** La revue de conception
   archivée dans `V14_export.json` le dit noir sur blanc :
   « `manage_once` (`position_manager.py:480`) appelle `journaliser_cloture` **sans**
   `cost_r`, qui vaut donc 0.0 pour tous les trades live ». La signature d'époque
   (`journal_path: Path, cost_r: float = 0.0`) portait le défaut ; le
   `_cloture_depuis_historique` d'époque ne rendait même pas les frais.
2. **Le courtier ne facture aucune commission sur ce compte démo.** Sur les
   **54 positions** de `results/reconciliation_mt5.json` :
   `commission == swap == fee == 0.0` pour **toutes**, et `net_currency == profit` pour
   toutes. Donc `frais == 0` ⇒ ligne 693 fausse ⇒ `cout_r = 0.0`, **par construction**,
   même avec le code d'aujourd'hui.
3. **`risque_devise` peut être absent.** Il n'est renseigné qu'à l'envoi de l'ordre par
   `tools/live_demo.py` :
   ```python
   502 | def _attacher_contexte(ticket, sym: str, feats: dict, out, res,
   503 |                        risque_devise: float = 0.0) -> None:
   ...
   537 |            risque_devise=float(risque_devise or 0.0),
   ```
   ```python
   917 |            _attacher_contexte(res.ticket, sym, feats, out, res,
   918 |                               risque_devise=budget.risk_money)
   ```
   Une position découverte par `manage_once` sans passer par `_attacher_contexte`
   (redémarrage, ordre manuel, état purgé — cf. `E5`) est créée lignes 640-648 **sans**
   `risque_devise` ⇒ `cout_r = 0.0` **et** voie « exacte » impossible (ligne 413).

### 3.3 Le vrai coût — le spread — n'est jamais journalisé en live

Côté backtest, la convention est explicite :

```python
305 |        # Spread payé une seconde fois à la sortie.
306 |        prix_sortie -= side * spread / 2.0
307 |        brut_r = (prix_sortie - entree) * side / r_unit
308 |        cout_r = spread / r_unit if r_unit > 0 else 0.0
```
(`titanium/backtest.py`)

Le backtest **retranche** le spread du prix (donc `pnl_r` est net) **et** l'expose dans
`cost_r` à des fins de traçabilité. Côté live, le spread est bien subi (il est dans le prix
de remplissage), mais il n'est **jamais mesuré** : `cost_r` live ne contient que des frais
qui valent zéro chez ce courtier.

**Les deux `cost_r` ne mesurent donc pas la même chose et ne sont pas comparables.**
C'est exactement la faute `E3` de `docs/LECONS.md` (« faire concorder toute mesure avec la
convention du backtest »), reproduite dans l'autre sens : un champ commun, deux définitions.

Sur EURGBP, `docs/LECONS.md` (E2/E3) chiffre le spread à **21 % du risque**, soit
`cost_r ≈ 0,21`. Le journal live écrit `0.0`. L'écart n'est pas un arrondi, c'est un cinquième
du risque rendu invisible.

---

## 4. Impact sur la mesure d'edge — `titanium/edge.py` ne protège rien

### 4.1 Le seul filtre existant

```python
132 |            try:
133 |                d = json.loads(ligne)
134 |                pnl = float(d["pnl_r"])
135 |                if not math.isfinite(pnl):
136 |                    continue
```
(`TradeJournal.read_all`)

`101280739.7308` est un flottant **fini**. Il passe. Aucune borne, aucune détection de
valeur extrême, aucun contrôle de cohérence avec `risk_money`.

### 4.2 Le verdict est une moyenne arithmétique nue

```python
175 |    def _verdict(self, cle: str, trades: list[ClosedTrade]) -> EdgeVerdict:
176 |        n = len(trades)
177 |        esperance = sum(t.pnl_r for t in trades) / n
178 |        gagnants = sum(1 for t in trades if t.pnl_r > 0)
179 |        cout = sum(t.cost_r for t in trades) / n
...
185 |        if n < self.min_samples:
186 |            v.edge_ok = None
188 |        elif esperance >= self.threshold_r:
189 |            v.edge_ok = True
```

Une moyenne n'a **aucune** résistance à une valeur aberrante : un seul trade à 10⁸ R
domine n'importe quel échantillon.

### 4.3 Preuve exécutée (`.venv/Scripts/python.exe`)

Relecture réelle du fichier auditté par `edge.TradeJournal` — **les 7 lignes sont acceptées** :

```
lignes relues par edge.TradeJournal: 7
  live:86776744  EURGBP|long|continuation|3p  pnl_r= 101280739.7308  cost_r=0.0  risk_money=0.0  exact=False
  ...
VERDICT AUDUSD|long|continuation|3p {'samples': 2, 'expectancy_r': 29924029.6907, 'win_rate': 1.0, 'mean_cost_r': 0.0, ...}
VERDICT EURGBP|long|continuation|3p {'samples': 1, 'expectancy_r': 101280739.7308, ...}
VERDICT EURUSD|long|continuation|3p {'samples': 1, 'expectancy_r': 52665394.46, ...}
```

Ici seul `MIN_SAMPLES = 20` (edge.py:40) sauve la mise (`edge_ok = None`). Ce n'est pas une
protection, c'est un sursis. Avec l'échantillon requis :

```
sans le trade corrompu: {'samples': 20, 'expectancy_r': -0.5,        'win_rate': 0.0,    'edge_ok': False}
avec  le trade corrompu: {'samples': 21, 'expectancy_r': 4822891.8919,'win_rate': 0.0476, 'edge_ok': True }
```

**20 trades perdants à −0,5 R, un seul trade corrompu, et le contexte devient « porteur ».**
`edge_ok = True` est la valeur que la porte de confluence consomme pour autoriser le mode
PROD. Un seul ticket contaminé suffit à ouvrir la porte que tout le dispositif V14 existe
pour tenir fermée. Les contextes touchés (`EURGBP|long|continuation|3p`,
`EURUSD|long|continuation|3p`, `AUDUSD|long|continuation|3p`) sont les plus fréquemment
réutilisés du catalogue.

### 4.4 Impact du `cost_r = 0.0` sur la promotion

```python
 43 | PART_COUT_MAX = 0.40         # le coût ne doit pas manger 40 % de l'espérance
...
204 |        v.mean_cost_r = sum(float(getattr(t, "cost_r", 0.0)) for t in lot) / n
...
234 |        if v.expectancy_r > 0 and v.mean_cost_r / v.expectancy_r > PART_COUT_MAX:
235 |            v.bloquants.append(
236 |                f"C8_COUT_RELATIF({v.mean_cost_r / v.expectancy_r:.0%})")
```
(`titanium/analysis/promotion.py`)

Avec `mean_cost_r = 0.0` en permanence, `0.0 / expectancy > 0.40` est **toujours faux** :
le critère **C8_COUT_RELATIF ne peut jamais bloquer une cellule live**. Le garde-fou censé
protéger du « tueur dominant » (le coût) est neutralisé par une donnée manquante — la même
famille de faute que `E2` (« un garde-fou écrit mais non branché ne protège de rien »).

De même, `reconciliation.exact_cost_missing` (ligne 184-185) signalera **toutes** les lignes
d'époque (`exact_cost=False`), ce qui fait échouer `ok` — mais sans jamais dire que le
`pnl_r` lui-même est faux.

---

## 5. Correctif proposé — NON APPLIQUÉ

Ordre de priorité décroissante. Aucun de ces changements n'a été écrit.

### C1 — Rendre impossible la contamination inter-positions *(cause racine)*

`titanium/execution/position_manager.py`, `_cloture_depuis_historique` (309-363) :

1. **Vérifier l'appartenance de chaque deal**, au lieu de faire confiance au filtre du
   terminal : ne conserver que les deals dont `position_id` (à défaut `position`) vaut
   `int(ticket)`. Si le filtre a été ignoré, on s'en aperçoit ici.
2. **Vérifier la cohérence du symbole** : `deal.symbol != st.symbol` ⇒ refus explicite,
   motif `SORTIE_AUTRE_SYMBOLE`. C'est le contrôle qui aurait arrêté l'incident du
   07/08/2026 à sa source.
3. **Vérifier la plausibilité du prix** : `abs(prix_sortie - st.entry) / st.entry` doit
   rester dans une bande large (par ex. ≤ 50 %). Un prix d'indice sur une paire FX est
   rejeté immédiatement.
4. Si aucun deal ne satisfait ces conditions ⇒ `prix = None` et `quand = ""`, et laisser
   le repli existant (`peak_fav_r` / `mae_r`, ligne 426) jouer son rôle.

### C2 — Ne plus jeter une mesure récupérable

Aujourd'hui, `PNL_R_HORS_BORNES` est un refus **définitif** (302-306) : la ligne EURGBP a
été perdue alors que MT5 donnait `+7,74 USD` (soit ≈ +0,32 R). Proposition : lorsque la
voie « prix » est invalidée mais que `net_devise` et `risque_devise` sont disponibles,
**basculer sur la voie exacte** (413-419) au lieu de refuser. Le refus définitif reste
réservé au cas où **aucune** voie n'est disponible.

### C3 — Faire du `cost_r` live une grandeur comparable au backtest

1. Ajouter au `TrackedState` le **spread payé** (ou la valeur du point + spread figé à
   l'entrée), déjà connu de la porte de coût, et journaliser
   `cost_r = (spread_aller_retour / r) + (commission + swap + fee) / risque_devise`,
   pour aligner la convention sur `backtest.py:306-308`.
2. Documenter explicitement dans `edge.ClosedTrade.cost_r` (ligne 68) que `cost_r` est le
   coût **total** aller-retour, spread inclus, et que `pnl_r` en est net.
3. Émettre un avertissement visible (compteur de rapport) quand `cost_r == 0.0` sur une
   ligne live : sur un courtier à spread, un coût nul n'est pas une mesure, c'est une
   mesure manquante — exactement l'esprit de `E6`.

### C4 — Rendre `edge.py` robuste aux valeurs aberrantes

1. Dans `TradeJournal.read_all` (132-153), ajouter après `isfinite` un rejet de
   plausibilité : `abs(pnl) > PNL_R_MAX_JOURNAL` (par ex. 50, la même constante que
   `position_manager.PNL_R_MAX`, exposée en un seul endroit), avec **comptage visible**
   des lignes rejetées et non un `continue` muet.
2. Dans `_verdict` (175-194), exiger que le verdict soit **cohérent avec une statistique
   robuste** : par exemple bloquer `edge_ok = True` si la médiane des `pnl_r` est négative,
   ou si un seul trade représente plus d'une fraction donnée de la somme. Une moyenne seule
   ne devrait jamais suffire à ouvrir la porte PROD.
3. Corriger le commentaire `position_manager.py:396-402` et la leçon `E8` de
   `docs/LECONS.md` : le `+101 280 739 R` n'a pas pour cause un `r` infinitésimal.

### C5 — Fermer le trou de test

`tests/test_journal_live.py:284-288, 442, 607` : le double MT5 doit **honorer** le filtre
`position=` (rendre uniquement les deals de la position demandée) et proposer un scénario
« plusieurs positions closes dans le même passage, symboles différents ». Un test
« EURGBP ne doit jamais recevoir le prix du GER40 » verrouillerait durablement la cause
racine ; aucun test actuel ne l'attrape.

### C6 — Rendre la réconciliation capable de voir un `pnl_r` absurde

`titanium/analysis/reconciliation.py:186-187` : le `continue` sur `risk_money <= 0` rend le
contrôle aveugle sur toute ligne ancienne. Proposition : même sans `risk_money`, signaler
`abs(pnl_r) > seuil` **et** un signe opposé à `net_currency` dans une nouvelle rubrique
`pnl_implausible`, incluse dans le calcul de `ok` (ligne 209).

---

## 6. Risques résiduels et limites de cet audit

- Le code source **exact** en exécution le 07/08/2026 n'est pas versionné (V14 n'est pas
  sous Git). La reconstruction s'appuie sur l'instantané contenu dans `V14_export.json`,
  sur les commentaires du code actuel et sur la signature arithmétique des données.
  Cette signature (prix et horodatage du GER40 recopiés sur trois autres symboles) est
  **sans ambiguïté** ; le mécanisme précis d'obtention du mauvais deal (filtre `position=`
  absent d'époque, ou ignoré par le terminal) reste, lui, une inférence.
- `results/live_demo.log` a été rotationné : il ne couvre plus le 07/08/2026 13:16.
- La suite `pytest` du projet **n'a pas été lancée** dans le cadre de cet audit (mission
  READ-ONLY, aucun code modifié). Seuls deux scripts de sonde en lecture ont été exécutés
  avec `.venv/Scripts/python.exe`, écrivant uniquement dans un dossier temporaire hors
  projet.
- Aucun correctif n'a été appliqué. Les sections C1 à C6 sont des propositions à valider
  par Florent avant toute implémentation.
