# Revue de cohérence — entrée live, anti-fade, suspension FX

Tâche `45f92b79`, P0, publiée par Codex au CollabHub offset 602.
Livrable demandé : ACCEPT / AMEND / BLOCK, fichiers et lignes, mesures
reproduites, ordre de correction **sans redémarrage ni promotion**.

Revue en lecture seule. Aucun fichier de code modifié, aucun seuil touché,
aucun service redémarré, aucun ordre.

---

## Verdict : **AMEND**

Un défaut de fond, une faiblesse de conception confirmée par la mesure, trois
vérifications concluantes, et deux hypothèses que j'ai formées puis retirées
parce que la mesure les a réfutées.

Le défaut ne casse rien aujourd'hui et ne justifie aucun arrêt. Il rend
**illisible le résultat de l'expérience que Florent a autorisée**, ce qui est
un autre genre de problème : la boucle prend les trades contre-tendance pour
les calibrer, et rien ne permettra de les isoler à la clôture.

---

## 1. Ce qui est cohérent

### 1.1 Suspension du FX — conforme

| | |
|---|---|
| Code | `tools/live_demo.py:178` `FX_SUSPENDU = True` |
| Commit | `d417046`, 24/08 08:21:28 |
| Boucle active | démarrée 24/08 20:36:22, donc postérieure |
| Motif de refus | `FX_SUSPENDU`, 912 occurrences, de 06:22:23 à 19:19:32 UTC |

Le premier refus (06:22:23 UTC) suit d'une minute le démarrage de la boucle qui
porte le drapeau. Depuis, **aucune entrée FX** : les 14 positions ouvertes au
moment de la revue sont des indices, matières premières, métaux et crypto.

**Fausse alerte que j'ai levée puis retirée.** Une position AUDUSD était ouverte
à midi, ce qui semblait contredire la suspension. Chronologie de son ordre
(`results/limit_lifecycle.ndjson`, ticket 96746649) :

```
06:00:43 UTC  placed
06:01:48 UTC  filled
14:43:31 UTC  closed
```

Posée **21 minutes avant** que la suspension ne prenne effet, par la boucle
précédente. La suspension filtre les décisions d'entrée, pas les limites déjà
en carnet. Comportement correct ; à noter comme angle mort connu si une
suspension devait un jour être immédiate.

### 1.2 Levée de l'anti-fade — effective

| | |
|---|---|
| Code | `titanium/risk/riskgate.py:63` `ANTI_FADE = ANTI_FADE_AUTORISE` |
| Commit | `f0d69a2`, 24/08 07:55:51, autorisé par Florent |
| Dernier refus `CONTRE_TENDANCE` | 05:43:17 UTC |

Le dernier veto précède la levée de douze minutes ; aucun ensuite. La règle
n'est pas supprimée mais devenue une politique, réarmable par un mot
(`riskgate.py:43-44`), et le test `test_anti_fade_bloque_le_contre_tendance_quand_il_est_arme`
protège le comportement d'origine. Conception saine.

Effet mesuré : 22 ordres de famille `reversal` posés aujourd'hui, **tous après
la levée**, zéro avant. Le veto portait bien 100 % du flux reversal.

### 1.3 Porte de coût — active

`titanium/sizing.py:70` `MAX_COUT_SPREAD_PCT = 0.125`, appliquée en
`live_demo.py:1420`. Motif `COUT_SPREAD` présent dans le journal (104 refus
aujourd'hui, 18 dans la fenêtre courante du heartbeat). Le seuil du code est
celui de l'arbitrage.

### 1.4 Une seule boucle armée

J'ai d'abord cru en voir quatre. Quatre shells `cmd /k … --armer` existent bien
(PID 26224, 11692, 23068, 32472), mais les trois plus anciens n'ont pour enfant
qu'un `conhost.exe` : leur Python est mort et `/k` laisse la fenêtre ouverte.
Seul `32472 → 31624 → 31888` porte un interpréteur vivant.

**Piège à retenir** : un `cmd /k` survit à la mort de sa commande. Dans une
liste de processus, quatre boucles armées apparaissent là où une seule tourne.
Compter les shells est un faux indicateur ; il faut descendre aux enfants.

---

## 2. Le défaut : `Decision.contre_tendance` n'est persisté nulle part

### 2.1 Ce que le commit annonce

> « Le trade qui passe est nommé : `Decision.contre_tendance`, la trace du
> contrôle le dit, et la boucle compte `anti_fade.CONTRE_TENDANCE_AUTORISE`
> dans le heartbeat. »

### 2.2 Ce que la mesure montre

Le drapeau est calculé (`riskgate.py:225-228`) et exposé sur la décision. Il
n'est écrit dans **aucun** artefact :

```
results/shadow_prod.ndjson       0 occurrence de "contre_tendance"
results/positions.json           0
results/candidats_grappe.ndjson  0
```

Le compteur du heartbeat existe bien dans le code
(`tools/live_demo.py:1367`), mais il alimente `stats["tunnel"]`, structure **en
mémoire, remise à zéro à chaque démarrage de boucle**. Au moment de la revue le
heartbeat couvre 41 tours et 398 ENTER, et l'étape `anti_fade` est absente.

**Je n'en conclus pas que le câblage est cassé.** Dans cette même fenêtre, 4
ordres seulement ont été posés, tous `continuation`, zéro `reversal` : il n'y a
simplement pas eu d'occurrence à compter. L'absence du compteur est donc
expliquée, et le code n'est pas mis en cause.

### 2.3 Pourquoi c'est un défaut malgré tout

L'objet de la levée est de **calibrer** les trades contre-tendance. Or à la
clôture, rien ne permettra de les séparer :

- le compteur est volatil et disparaît au redémarrage — il y en a eu au moins
  trois aujourd'hui ;
- aucun journal durable ne porte le drapeau ;
- la clé de contexte porte la **famille de setup** (`…|reversal|…`), que le
  commit propose comme substitut.

Ce substitut ne tient pas : `reversal` est une famille émise par la porte de
confluence, `contre_tendance` est `side == -trend` évalué par le RiskGate. Un
setup de retournement qui prend un repli **dans le sens** de la tendance H4 est
de famille `reversal` sans être contre-tendance. Calibrer sur la famille
reviendrait à mesurer une autre cohorte que celle qu'on a autorisée.

### 2.4 Un second point, de forme

`live_demo.py:1364-1366` **recalcule** la condition au lieu de lire
`out.contre_tendance` que le RiskGate publie déjà :

```python
_tendance = int((feats or {}).get("trend") or 0)
_sens = int(getattr(out, "side", 0) or 0)
if _tendance != 0 and _sens == -_tendance:
```

Les deux expressions coïncident aujourd'hui — `risk_context_from`
(`titanium/features/builder.py:423`) dérive `trend` du même `feats.get("trend")`.
C'est donc correct à cet instant, et je ne signale aucune divergence mesurée.

Mais c'est exactement le motif que Prime a lui-même identifié comme P0-3 dans
`docs/AUDIT_POLITIQUES_EXECUTION_20260824.md` : *« la version 1 réécrivait la
formule de `plan_limit_entry` dans le simulateur. En quelques heures, la copie
divergeait déjà sur trois points. »* Une copie qui coïncide aujourd'hui est une
divergence qui attend son commit.

---

## 2 bis. La notion elle-même : `trend` est un bit là où il faut un continuum

Direction donnée par Florent pendant cette revue : *« la contre-tendance est à
modifier dans le RiskGate, ce n'est plus une valeur fiable, on doit s'adapter
au flux constant. »* La mesure lui donne raison, mais pas pour la raison que
j'avais d'abord supposée.

### Ce que `trend` vaut aujourd'hui

`titanium/features/builder.py:106` — le signe de l'écart à l'EMA200 du HTF :

```python
return 1 if px > ema else (-1 if px < ema else 0)
```

`0` n'arrive qu'avec un historique trop court pour l'EMA. En marche normale,
**toute décision est donc étiquetée** continuation ou reversal, sans état
indéterminé.

### L'hypothèse que j'ai formée puis abandonnée

J'ai supposé que le signe basculait sur le bruit autour de la moyenne. Mesure
sur 4 000 barres H4 par symbole :

| symbole | bascules | soit | barres à < 0,25 ATR de l'EMA200 |
|---|---:|---|---:|
| DJ30.fs | 3 | 1 / 1333 barres | 0,1 % |
| GER40 | 7 | 1 / 571 | 0,1 % |
| FRA40 | 13 | 1 / 308 | 0,2 % |
| BTCUSD | 56 | 1 / 71 | 2,2 % |

Médiane : **0,4 %** des barres sont à moins de 0,25 ATR de l'EMA200. Le signe
est stable, les bascules sont rares. **Mon hypothèse est réfutée.**

### Le vrai défaut : un facteur 46 écrasé dans un bit

Distance |prix − EMA200| rapportée à l'ATR, 40 000 barres H4, 10 symboles :

| | p5 | p25 | p50 | p75 | p90 | p99 |
|---|---:|---:|---:|---:|---:|---:|
| ATR | 2,55 | 13,26 | 27,87 | 49,00 | 85,72 | **118,21** |

Le même `trend = +1` couvre de **2,55 à 118,21 ATR**, soit un rapport de 46.

Conséquence directe sur le RiskGate : `contre_tendance` traite identiquement

- une vente contre une tendance établie depuis 2,5 ATR — on combat une
  cassure fraîche ;
- une vente contre une tendance étirée de 118 ATR — on fade un mouvement à
  bout de souffle.

Ce sont deux trades **opposés**, et l'un des deux est un edge classique. Les
confondre sous un même veto explique qu'il ait porté 910 refus sur 910 : il ne
distinguait pas ce qu'il refusait.

Cela contamine aussi la comptabilité. `_setup_family`
(`builder.py:214`) dérive `continuation` / `reversal` du **même signe**. Les
chiffres qui ont fondé l'arbitrage — +0,1250 R pour reversal contre +0,1819
pour continuation — reposent donc sur cette partition binaire, et ne disent
rien de la distance à la tendance.

### Ce que j'en propose, sans l'avoir fait

Remplacer le prédicat binaire par une **grandeur continue** exposée à côté du
signe, par exemple `trend_ecart_atr = (px − ema) / atr`, et laisser le RiskGate
décider sur elle. Le signe reste disponible pour la compatibilité ; rien n'est
supprimé, comme pour l'anti-fade.

Deux raisons de ne pas trancher ici :

1. La partition par distance doit être **calibrée sur le rejeu**, pas choisie.
   Les artefacts bruts portent les 100 indicateurs par trade, `htf_close_200_sma_atr`
   compris : la mesure est faisable sans relancer le moteur.
2. `builder.py` appartient à `FICHIERS_MOTEUR`. Toute modification périme les
   artefacts et relance les 149 symboles — la quatrième fois. À grouper avec
   les autres changements moteur, pas à faire seule.

---

## 3. Ordre de correction proposé

Sans redémarrage, sans promotion, sans changement de seuil.

1. **Persister le drapeau.** Écrire `contre_tendance` dans le contexte de trade
   attaché à l'entrée — `_attacher_contexte` et `_memoriser_contexte_limit`,
   là où Prime a déjà placé les niveaux structurels le 18/08 (commit
   `ec0c882`). Le champ suit alors le trade jusqu'à sa clôture, et la
   comptabilité d'edge sépare enfin la cohorte autorisée.

2. **Lire la source unique.** Remplacer le recalcul de `live_demo.py:1364-1366`
   par `getattr(out, "contre_tendance", False)`. Une ligne, et la copie
   disparaît avant d'avoir divergé.

3. **Ne rien conclure de la levée avant que 1 ne soit en place.** Tout trade
   contre-tendance clos d'ici là est perdu pour la calibration : il a eu lieu,
   il a coûté ou rapporté, et on ne saura pas le dire.

4. **Mesurer la distance à la tendance avant de redéfinir le prédicat**
   (section 2 bis). Les artefacts bruts portent `htf_close_200_sma_atr` par
   trade : la partition par distance se calibre sur le rejeu, sans relancer le
   moteur. C'est le préalable à toute réécriture de `contre_tendance`, et
   l'ordre importe — persister d'abord, redéfinir ensuite, sinon on remplace un
   prédicat non mesuré par un autre.

Le point 1 est le seul qui presse : chaque heure sans lui est une heure
d'expérience non mesurable. Les points 1 et 2 touchent `live_demo.py`, qui
appartient au chemin d'exécution live — **leur intégration relève de Prime, et
leur mise en service d'un redémarrage que seul Florent décide.**

---

## 4. Ce que cette revue ne couvre pas

- Je n'ai pas rejoué les cohortes du rejeu contre les décisions live : la
  comparaison exigerait un appariement par `decision_id` que seule la mesure
  appariée de Hermes (`f358047`) rend possible, et elle vient d'atterrir.
- Je n'ai pas audité la suspension des shorts FX (`FX_SHORTS_SUSPENDUS`,
  `live_demo.py:157`), qui reste active sous la suspension totale et devient
  redondante — sans effet mesurable tant que `FX_SUSPENDU` tient, mais à
  clarifier si le FX revient.
- La justesse des prix et la conversion heure serveur → UTC sont admises
  d'après l'archiveur, non revérifiées ici.

---

## 5. Reproduire

```bash
git log -1 --format="%h %ci %s" -S "FX_SUSPENDU = True" -- tools/live_demo.py
git log -1 --format="%h %ci %s" -S "ANTI_FADE = ANTI_FADE_AUTORISE" -- titanium/risk/riskgate.py
grep -c contre_tendance results/shadow_prod.ndjson results/positions.json
python -c "import json;h=json.load(open('results/loop_heartbeat.json',encoding='utf-8'));print(list((h['stats']['tunnel']).keys()))"
```

Comptages de refus et cycles de vie : `results/refus_live.ndjson` et
`results/limit_lifecycle.ndjson`, filtrés sur `2026-08-24`.
