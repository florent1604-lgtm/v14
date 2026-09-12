# Pour Codex Astra — reprise du 07/09/2026

Claude est intervenu sur V14 pendant que tu étais arrêté. **Reprends ton travail
là où tu l'avais laissé**, mais lis d'abord ce qui suit : trois de ces
modifications changent le comportement du cortex, et une quatrième change la
façon de lancer les tests.

Rien n'a été commité. Tout est dans l'arbre de travail.

---

## Ce qui a changé, et pourquoi

### 1. Le TTL des politiques suit maintenant la durée de barre

`titanium/organism/cortex.py`

Il valait **300 s pour tout horizon au-dessus de M5**. Mesuré : sur H1 cela
laissait une fenêtre de fraîcheur de 5 minutes par heure, sur H4 de 5 minutes
sur 240. Sur 40 demandes consécutives, **zéro** n'atteignait Hermès.

```
M1    60 s (plancher hérité)     H1     900 s
M5   120 s (plancher hérité)     H4   3 600 s
M15  225 s                       D1  21 600 s
```

Le TTL vaut un quart de la barre, borné par la barre elle-même — une politique
ne survit jamais à la condition qui l'a produite. Trois endroits ont dû bouger
ensemble, car `CORTEX_POLICY_TTL_S` servait à la fois de défaut, de plafond de
construction et de borne de revalidation. Le défaut est désormais `None` et se
résout depuis le contexte.

`BARRE_MINUTES` est devenue une table unique, lue par `policy_ttl_s` **et** par
`market_observed_at` — elles en avaient deux, qui auraient fini par diverger.

**Conséquence pour toi :** les `decision_ref` et les fenêtres de validité ne
sont plus les mêmes. Si tu avais des mesures en cours sur la fraîcheur des
politiques, elles sont à refaire.

### 2. Une erreur d'API n'est plus masquée

`titanium/hermes_cortex.py`

Le CLI Hermès rend `returncode 0` **même quand l'API refuse** : le motif réel
est du texte sur stdout. Le cortex ne remontait que « réponse sans JSON
valide », ce qui perdait la cause et faisait appliquer au disjoncteur un
backoff de 60 s à un problème de quota qui en demande 600.

Le message brut est maintenant conservé dans l'exception. C'est ce correctif
qui a permis de voir `HTTP 400: Your credit balance is too low`.

### 3. Un chronomètre faisait exploser le volume d'appels — 95 % de gaspillage

`titanium/organism/contracts.py`

**C'est la modification la plus importante.** Mesure : 869 demandes d'avis pour
seulement **31 couples (symbole, barre) distincts**. `AAVE-USD` sur la barre de
12:00 a été soumis **87 fois** au cortex. La barre n'ayant pas changé, la
réponse ne pouvait pas changer.

Cause : sur les 108 clés du panel scellées dans l'identité de décision, **une
seule** variait entre deux demandes de la même barre — `jepa_latency_ms`, le
temps d'inférence de Market-JEPA (0,925 ms puis 0,874 ms). Elle suffisait à
faire dériver le `decision_ref` et à annuler toute déduplication.

C'est le défaut d'idempotence du 07/08 sous une autre forme : *une clé s'ancre
sur l'évènement observé, jamais sur l'instant ni sur le coût du calcul.*

```python
MESURES_INSTRUMENTALES = frozenset({"jepa_latency_ms"})
```

Mesuré sur les demandes réelles : **833 appels Hermès évités sur 880, soit
95 %.** Le volume passe d'environ 350 appels/heure à moins de 20.

**Conséquence pour toi :** les `decision_ref` d'avant ce correctif ne
correspondent plus à ceux d'après. Les politiques en mémoire deviennent
orphelines — elles expirent d'elles-mêmes par TTL, ne les purge pas à la main.

### 4. Les tests se lancent différemment

Smart App Control refuse de charger `_xxhash.cp312-win_amd64.pyd`, non signé et
sans réputation ISG. **30 fichiers de tests étaient devenus incollectables** via
la chaîne `tradingagents → langchain → langsmith → xxhash`.

Deux pièces posées :

- `tests/compat/xxhash.py` — substitut pur Python chargé **uniquement si** le
  paquet natif échoue, limité aux digests déterministes dont langsmith et
  langgraph ont besoin, et qui **lève sur toute autre surface**. Il ne s'active
  pas sur une machine saine.
- `pyproject.toml` — `-p no:langsmith_plugin` dans `addopts`, car ce plugin se
  charge avant tout conftest.

La suite repasse : **2 683 passed, 2 skipped**.

⚠️ La cause est structurelle et te concerne : le venv contient **419 extensions
natives, toutes non signées** — numpy, pandas, scipy, sklearn comprises. Elles
ne passent aujourd'hui que par réputation. Signer ne sert à rien : SAC ne
consulte pas le magasin de certificats de la machine. Un étage `tests` a donc
été ajouté au `Dockerfile` existant :

```
docker build --target tests -t v14-tests .
docker run --rm v14-tests
```

Il n'a **pas été construit** — Docker n'est pas installé sur ce poste. Il est
écrit et cohérent, pas éprouvé.

---

## Ce qui reste ouvert, et que je n'ai pas résolu

**Hermès reçoit par intermittence `HTTP 400: Your credit balance is too low`,
alors que l'abonnement Claude fonctionne.** Le même prompt échoue 5 fois de
suite puis passe 4 fois de suite dix minutes plus tard, sans que rien n'ait été
rechargé. Trois hypothèses ont été testées et **réfutées** : crédit épuisé,
taille du prompt, contenu du prompt.

Le mécanisme n'est pas déterminé. Le correctif n°3 devrait rendre ces refus
rares en divisant le volume par vingt, mais ce n'est pas une explication.

**Ne repars pas de l'hypothèse « il faut acheter du crédit » : elle a été
testée et elle est fausse.**

---

## Ordre de reprise

1. **Reprends ta tâche là où tu l'avais laissée.** Ces modifications ne
   remplacent pas ton plan, elles changent le terrain sous tes pieds.
2. **Relis les trois fichiers du cortex avant de toucher à quoi que ce soit
   dans cette zone** — `titanium/organism/cortex.py`,
   `titanium/organism/contracts.py`, `titanium/hermes_cortex.py`. Chaque
   changement porte en commentaire la mesure qui l'a imposé.
3. **Lance la suite avant et après ton lot** : `pytest tests/ -q` doit rendre
   2 683 passed minimum, `bash tools/lint_gate.sh` doit être propre.
4. **Refais toute mesure de fraîcheur ou de volume d'appels** faite avant
   aujourd'hui : les TTL et les `decision_ref` ont changé, tes chiffres
   d'avant ne sont plus comparables.
5. **Rien n'est commité.** Si tu commites, sépare tes lots des miens.

Contexte complet du chantier et méthode attendue :
`collab/PROMPT_CODEX6_AUDIT_V14.md`.

---

## Portée exacte

```
Dockerfile                     |  41 +      étage de test
pyproject.toml                 |   7 +-     exclusion du plugin langsmith
tests/conftest.py              |  15 +      substitut xxhash conditionnel
tests/compat/xxhash.py         | nouveau    substitut pur Python
tests/test_cortex_authority.py | 125 +-     TTL + identité de décision
tests/test_hermes_cortex.py    |  28 +      erreur d'API non masquée
titanium/hermes_cortex.py      |  12 +-     motif d'erreur préservé
titanium/organism/contracts.py |  29 +-     filtre des mesures instrumentales
titanium/organism/cortex.py    |  89 +-     TTL proportionnel à la barre
```

`tools/collecteur_microstructure.py` et `tests/test_collector_scheduling.py`
sont également modifiés dans l'arbre, **mais ils ne viennent pas de moi** — ne
me les attribue pas et vérifie qui les a touchés avant d'intervenir dessus.

Aucun fichier moteur scellé n'a été modifié : les artefacts de rejeu restent
valides.
