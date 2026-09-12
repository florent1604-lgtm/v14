# Flux macroéconomique externe — architecture et mise en service

> Ce document décrit une brique **livrée** : `titanium/macro/`, ses trois points
> d'injection, et la procédure exacte pour l'allumer. Il ne décrit aucune
> intention non codée ; ce qui reste à faire est en §10, sous une rubrique
> séparée et assumée comme telle.

## 1. Le problème, posé précisément

V14 décide sur ce que le carnet **montre** : tendance, niveaux, sweep, OTE,
bougie. Rien dans cette chaîne ne sait qu'une décision de taux tombe dans
quatre minutes. Le système réagit donc au choc — au mieux en payant le spread
élargi, au pire en entrant juste avant.

Le manque n'est pas « plus d'indicateurs ». C'est un **fait externe horodaté**
qu'aucun calcul sur les bougies ne peut produire. Trois questions en découlent,
et chacune a un propriétaire distinct :

1. *Que dit le calendrier, et à quel point y croit-on ?* → `contracts`, `cache`
2. *Faut-il prendre du risque neuf maintenant ?* → `risk`
3. *Comment cette réponse entre-t-elle dans une décision ?* → `gate`

## 2. Vue d'ensemble

```
   source externe (fichier JSON | endpoint HTTP)
            │  fetch()  — bloquant, dans un FIL d'execution separe
            ▼
   ┌────────────────────┐        MacroFeed          ┌──────────────────────┐
   │ sources.py         │ ───────────────────────▶ │ cache.py             │
   │ un fournisseur,     │   publish() / record_    │ etat partage,        │
   │ aucune decision     │   failure()              │ thread-safe          │
   └────────────────────┘                          └──────────┬───────────┘
                                                              │ view()  (gele)
                                                              ▼
                                                   ┌──────────────────────┐
                                                   │ risk.py              │
                                                   │ PUR : vue + instant  │
                                                   │  -> MacroRisk        │
                                                   └──────────┬───────────┘
                                                              │
                                    ┌─────────────────────────┴──────────────────┐
                                    ▼                                            ▼
                        ┌──────────────────────┐                  ┌──────────────────────┐
                        │ gate.py              │                  │ telemetry.py         │
                        │ 2 booleens           │                  │ jauges 0-100 %       │
                        └──────────┬───────────┘                  └──────────┬───────────┘
                                   ▼                                         ▼
              feats["macro"] -> confluence_gate.evaluate          state.macro() -> interface
              PolicyContext.macro -> build_features
```

Le chemin critique (la décision) ne touche que le bas de ce schéma : une lecture
de cache sous verrou, puis une fonction pure. Le réseau est en haut, dans un
autre fil.

## 3. Un propriétaire par question

| Module | Lignes | Question dont il est LE propriétaire | Ce qu'il ne fait jamais |
|---|---:|---|---|
| `contracts.py` | 299 | Quelle est la forme d'un fait macro, et qu'est-ce qui est « sain » ? | Aucune E/S, aucune politique |
| `policy.py` | 139 | Quelles bornes numériques, et quelles clés de config sont légales ? | Aucun seuil caché ailleurs |
| `cache.py` | 147 | Que croit savoir ce processus, et qu'est-il arrivé au dernier essai ? | N'attend jamais le réseau, ne lève jamais |
| `sources.py` | 263 | Comment lit-on un fournisseur ? | Ne décide pas, ne met rien en cache |
| `risk.py` | 205 | Faut-il du risque neuf, maintenant, pour ce symbole ? | Aucune E/S, aucune horloge implicite |
| `feed.py` | 100 | Quand relit-on, et avec quel repli ? | Ne décide pas de la prudence |
| `gate.py` | 66 | Sous quelle forme le verdict entre-t-il dans une décision ? | Ne connaît ni la porte ni l'interface |
| `telemetry.py` | 94 | Comment l'interface voit-elle le verdict ? | Ne lit pas de texte libre |
| `__init__.py` | 156 | Le cache du processus, la politique courante, le point d'entrée | — |

Trois conséquences directes :

* **Un seuil change** → `policy.py`, ou mieux `config/macro.json` (versionné, donc
  comparable). Aucun `if` dans le code de décision.
* **Une source change** → `sources.py`, et rien d'autre. Remplacer un fichier par
  un endpoint ne touche ni la politique ni la porte.
* **La sémantique de prudence change** → `risk.py` seul, et elle est rejouable
  sur un calendrier historique puisqu'elle est pure.

## 4. Contrats et invariants

Tous les objets publics sont **gelés** (`@dataclass(frozen=True)`) et validés à
la construction : horodatage porteur de fuseau, devise non vide, impact dans le
barème, score dans `[0, 1]`. Un fait publié ne bouge plus.

Invariants qui ont coûté quelque chose, donc qui sont écrits :

* **`MacroCalendar.digest()` exclut `fetched_at`.** Il mesure le *contenu*, pas
  l'instant de lecture. L'inclure ferait diverger l'empreinte à chaque
  rafraîchissement et interdirait toute déduplication en aval — c'est le défaut
  `jepa_latency_ms` de l'identité de décision Hermes, qui reviendrait par une
  autre porte.
* **Un impact inconnu lève, il ne retombe pas sur `LOW`.** Sous-estimer une
  publication est exactement la faute que le module existe pour empêcher.
* **Une seule ligne illisible invalide tout le calendrier.** Sauter la ligne
  cassée produirait un calendrier plus *vide* — donc moins d'alertes — et la
  publication perdue serait justement celle qu'un changement de format vient de
  casser.
* **Un échec de lecture ne remplace pas la dernière lecture connue.** L'ancienne
  reste visible, et c'est la *fraîcheur* qui la juge : « périmé » et « jamais eu
  de données » sont deux conclusions différentes, donc deux états différents.
* **Un horodatage naïf n'est jamais supposé UTC.** Il est localisé par
  `naive_tz`. Lire « 14:30 » comme 14:30 UTC quand la source publie en heure de
  New York décale la fenêtre de gel de quatre heures, dans le sens qui laisse
  passer la publication.
* **Un symbole dont on ne peut pas déduire les devises n'est pas filtré.** Un
  ensemble de devises vide signifie « considère tout », jamais « rien ne
  compte » : le contraire fabriquerait un faux calme depuis une table
  incomplète.

## 5. Matrice d'échec fermé

C'est le tableau à relire avant de toucher à la politique.

| Situation | État publié | `allows_new_risk` | Effet sur une entrée |
|---|---|---|---|
| Flux désactivé (`enabled=false`) | `CLEAR` | oui | **aucun** — comportement d'avant |
| Calendrier frais, rien dans l'horizon | `CLEAR` | oui | aucun |
| Publication majeure dans `elevated_within_s` | `ELEVATED` | oui | `WAIT` |
| Dans `[-blackout_after, +blackout_before]` | `BLACKOUT` | **non** | `BLOCK` |
| Lecture plus vieille que `ttl_s` | `STALE` | **non** | `BLOCK` |
| Horodatage de lecture dans le futur (> 60 s) | `STALE` | **non** | `BLOCK` |
| Aucune lecture, jamais | `UNKNOWN` | **non** | `BLOCK` |
| Bloc de features présent mais incomplet | — | — | `BLOCK_MACRO_UNAVAILABLE` |
| Source qui plante, endpoint mort | état précédent, vieilli | selon fraîcheur | `BLOCK` dès péremption |

`UNKNOWN` et `CLEAR` ne sont **pas** équivalents, et c'est le cœur du contrat.
Un calendrier qu'on ne peut pas lire n'est pas un calendrier vide.

## 6. Les trois points d'injection

### 6.1 Le vecteur de features de la porte — `titanium/features/builder.py`

`build_feats(..., symbol=sym)` ajoute `feats["macro"]` **uniquement** si le flux
est allumé *et* que le symbole est nommé. C'est la règle qui borne le rayon
d'action : un appelant qui ne parle pas de macro obtient le dict exact d'avant.

```python
feats = build_feats(ltf, htf, marche_continu=..., symbol=sym)   # live_demo
verdict = confluence_gate.evaluate(feats)                        # inchange
```

### 6.2 La porte de confluence — `titanium/gates/confluence_gate.py`

`evaluate()` reste **pure** : elle ne connaît pas `titanium.macro`. Elle lit deux
booléens, comme elle lit déjà `emotion` et `cost`.

```python
macro_actif = ("macro" in feats) if require_macro is None else require_macro
```

Le défaut `require_macro=None` est ce qui rend l'ajout non intrusif : le macro
n'est évalué que si le vecteur en porte un bloc. `GATE_VERSION` passe de `2.0.0`
à `2.1.0`, conformément à la règle du module.

Ordre d'évaluation, qui n'est pas interchangeable : les piliers d'abord, puis
les modérateurs (émotion, coût, **macro**), puis le timing émotionnel. Un gel
macro est un *refus*, il domine donc un « pas encore prêt » émotionnel ; inversé,
un `WAIT` émotionnel masquerait un `BLOCK` macro et le journal raconterait la
mauvaise raison.

### 6.3 Le contexte d'arrivée de l'exécution — `titanium/execution_sim/`

`PolicyContext` gagne un champ `macro` **en dernier, avec un défaut** : toute
construction positionnelle existante reste valide. `build_features` refuse de
planifier (`None`) quand le calendrier interdit le risque neuf, et transmet
l'attitude conservatrice dans `AdaptiveFeatures` (`macro_conservative`,
`macro_score`) sans qu'aucune des 17 techniques ne change de comportement.

Valeurs **neutres** par défaut (`allows=True`, `conservative=False`, `score=0.0`) :
absentes, elles donnent le comportement d'avant, ce qui rend la non-régression
démontrable plutôt que promise.

## 7. Mise en service — la procédure exacte

```powershell
# 1. Copier l'exemple, puis RENSEIGNER la source. Le defaut reste eteint.
Copy-Item config\macro.example.json config\macro.json

# 2. Fournisseur fichier (sans reseau ni secret) : pointer vers le calendrier.
#    Ou fournisseur http : renseigner 'url' et mettre la cle dans l'environnement
#    (jamais dans le fichier) :
#      $env:V14_MACRO_API_KEY = "..."

# 3. Allumer. C'est le SEUL interrupteur : 'enabled': true.
#    Il n'existe pas de troisieme booleen « obligatoire » — la semantique est
#    deja portee par l'etat : flux allume sans donnee fraiche => STALE => refus.

# 4. Verifier AVANT d'armer.
.\.venv\Scripts\python.exe tools\macro_status.py --refresh --upcoming 3
#    code de sortie 0 = risque neuf autorise, 1 = refuse. Utilisable comme porte.

# 5. Le tableau de bord lit deja la sonde : titanium.web.state.macro().
```

Forme du calendrier attendu (le fournisseur `file` lit exactement ceci) :

```json
{"events": [
  {"title": "FOMC — décision de taux", "currency": "USD",
   "scheduled_at": "2026-09-17T18:00:00+00:00", "impact": "High",
   "forecast": 4.25, "previous": 4.50}
]}
```

`event_id` est facultatif : à défaut, l'identifiant est une empreinte du contenu
seul, donc stable d'une lecture à l'autre.

## 8. Preuve de non-régression

Mesuré, pas affirmé. Deux exécutions complètes — l'une sur `origin/main`
(`24d1dde`), l'autre sur cette brique — puis comparaison ligne à ligne :

| Artefact | Lignes | Lignes identiques | Colonnes qui diffèrent |
|---|---:|---:|---|
| Matrice historique, 15 politiques | 12 960 | **12 960 / 12 960** | **aucune** |
| Famille adaptative, 17 techniques | 15 552 | **15 552 / 15 552** | **aucune** |

Classement adaptatif recalculé depuis les lignes brutes du lot : 17 techniques,
**10 gagnants**, `adapt_midpoint_aggressive` +0,2446 en tête, aucun couple de
techniques identique sur l'ensemble des colonnes. Les chiffres publiés dans
`docs/PLAN_EXECUTION_ADAPTATIVE_V14.md` restent exacts.

L'empreinte de paquet (`engine_version`) change, par construction : deux fichiers
de plus dans `titanium/execution_sim/` suffisent. C'est le seul écart attendu.

## 9. Tests livrés

`tests/test_macro_feed.py` — 53 cas, dont la non-régression en tête de fichier
parce que c'est elle qu'on casse en premier :

* flux éteint ⇒ aucune clé `macro`, verdict **identique** à avant ;
* dérive d'horloge, péremption, source en panne, fichier absent, JSON cassé,
  impact inconnu, fuseau inconnu, devise absente ;
* gel avant **et** après la publication, événement le plus proche retenu ;
* filtrage par devise du symbole, et symbole non devinable ⇒ aucune publication
  ignorée ;
* cache concurrent (8 fils × 50 itérations) ;
* lecture **dans un autre fil** que la boucle d'événements (preuve de non-blocage) ;
* les onze cas d'échec fermé du §5 ;
* la sonde du tableau de bord ne lève jamais, même configuration illisible.

Le fichier échoue si l'on retire le veto macro, si l'on rend `CLEAR` à un
calendrier périmé, ou si l'on remet la lecture réseau sur le fil appelant.

## 10. Ce qui n'est **pas** fait

Assumé, et pas seulement reporté :

* **Agrégateur de volatilité cross-exchange.** Le point d'accroche est net — un
  frère de `MacroSource` qui publie dans le même cache — mais rien n'a été écrit,
  parce qu'aucune source de volatilité n'est configurée ici et qu'un module qui
  ne peut pas être exercé ne peut pas être vérifié. Livrer la couture sans le
  contrat aurait été du décor.
* **Brique de volatilité dans `risk.py`.** Elle suppose la précédente.
* **Panneau visuel.** Ce lot livre le **contrat de télémétrie** (`macro_telemetry`
  : état, `score_pct`, `freshness_pct`, `imminence_pct`, sévérité, motifs) et la
  sonde `titanium.web.state.macro()`. Le rendu HTML vit dans le tableau de bord
  FastAPI/HTMX qui n'est **pas encore sur `main`** — l'ajouter ici l'aurait
  dupliqué puis fait diverger. Le panneau consomme `state.macro()` tel quel.
* **Atteinte du mode fantôme (`titanium/shadow.py`).** Le veto macro y est
  inactif tant que `build_feats` n'y reçoit pas de symbole.
* **Une technique adaptative sensible au macro.** Les champs existent
  (`macro_conservative`, `macro_score`), aucune des 17 ne les lit. C'est
  délibéré : en faire lire une changerait les nombres publiés, donc exige un
  nouveau lot de mesure complet, pas un ajout discret.

## 11. Limites connues et honnêteté

* **La fraîcheur mesure l'âge de la LECTURE, pas la distance à l'événement.** Un
  `ttl_s` de 900 s signifie « je crois ce que j'ai lu il y a moins de 15 min ».
  Un calendrier lu à 8 h et valable toute la journée doit donc avoir un `ttl_s`
  large, sinon le système refuse — ce qui est le sens correct, mais qu'il faut
  régler consciemment dans `config/macro.json`.
* **Le filtrage par devise est une heuristique.** Elle lit des codes de trois
  lettres dans le symbole. Un symbole non analysable conserve **toutes** les
  publications : c'est prudent, mais bruyant sur un indice.
* **La fenêtre de gel est déclarative, pas mesurée.** `blackout_before_s = 900`
  est une valeur de départ raisonnable, pas un résultat empirique. La caler
  demande de corréler le slippage observé à la proximité d'une publication —
  c'est un travail de mesure, et il n'est pas fait.
* **L'encodage console sous Windows.** `tools/macro_status.py` affiche des
  accents ; un terminal en page de code 1252 les rend mal. La sortie `--json`
  est correcte et machine-lisible.
