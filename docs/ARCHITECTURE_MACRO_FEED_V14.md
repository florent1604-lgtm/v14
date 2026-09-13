# Flux macroéconomique externe — architecture et mise en service

> Ce document décrit une brique **livrée et branchée** : `titanium/macro/`, le
> service qui la démarre dans le processus qui trade, ses trois points
> d'injection, les jauges du tableau de bord, et la procédure exacte pour
> l'allumer. Il ne décrit aucune intention non codée ; ce qui reste à faire est
> en §10, sous une rubrique séparée et assumée comme telle.

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
   ┌───────────────────────────────────────────────────────────────────────┐
   │ service.py — MacroService     QUI demarre, et qui arrete             │
   │ un fil daemon, sa propre boucle asyncio, un arret interruptible       │
   └───────────────────────────────┬───────────────────────────────────────┘
                                   │ demarre
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
              feats["macro"] -> confluence_gate.evaluate          macro_publication()
              PolicyContext.macro -> build_features                     │
                                                              ┌─────────┴─────────┐
                                                              ▼                   ▼
                                                    battre() publie      state.macro()
                                                    dans le battement    (sonde)
                                                              │                   │
                                                              └──── relit ─────────┘
                                                           results/loop_heartbeat.json
                                                                                  │
                                                                          /ui/macro
                                                                          jauges HTML
```

Le chemin critique (la décision) ne touche que le bas de ce schéma : une lecture
de cache sous verrou, puis une fonction pure. Le réseau est en haut, dans un
autre fil — et ce fil a un propriétaire : `MacroService`, et lui seul.

**Pourquoi le verdict traverse le processus par le battement.** Le tableau de
bord est un second processus, en lecture seule. Il ne peut pas voir le cache de
la boucle, et lui donner son propre flux produirait deux vérités : une jauge
« calme » pendant que la boucle refuse. La boucle publie donc son verdict dans
le battement qu'elle écrit déjà (`results/loop_heartbeat.json`, celui que
`state.loop()` relit), et la sonde le préfère à tout calcul local. À défaut de
battement — boucle arrêtée — elle calcule en local et le DIT (`source`).

## 3. Un propriétaire par question

| Module | Lignes | Question dont il est LE propriétaire | Ce qu'il ne fait jamais |
|---|---:|---|---|
| `contracts.py` | 299 | Quelle est la forme d'un fait macro, et qu'est-ce qui est « sain » ? | Aucune E/S, aucune politique |
| `policy.py` | 144 | Quelles bornes numériques, et quelles clés de config sont légales ? | Aucun seuil caché ailleurs |
| `cache.py` | 147 | Que croit savoir ce processus, et qu'est-il arrivé au dernier essai ? | N'attend jamais le réseau, ne lève jamais |
| `sources.py` | 263 | Comment lit-on un fournisseur ? | Ne décide pas, ne met rien en cache |
| `risk.py` | 205 | Faut-il du risque neuf, maintenant, pour ce symbole ? | Aucune E/S, aucune horloge implicite |
| `feed.py` | 117 | Quand relit-on, et avec quel repli ? | Ne décide pas de la prudence, ne démarre rien |
| `service.py` | 274 | **Qui** relit, dans ce processus, et quand cela s'arrête | Ne décide rien du risque |
| `gate.py` | 66 | Sous quelle forme le verdict entre-t-il dans une décision ? | Ne connaît ni la porte ni l'interface |
| `telemetry.py` | 94 | Comment l'interface voit-elle le verdict ? | Ne lit pas de texte libre |
| `__init__.py` | 151 | Le cache du processus, la politique courante, le point d'entrée | — |

Trois conséquences directes :

* **Un seuil change** → `policy.py`, ou mieux `config/macro.json` (versionné, donc
  comparable). Aucun `if` dans le code de décision.
* **Une source change** → `sources.py`, et rien d'autre. Remplacer un fichier par
  un endpoint ne touche ni la politique ni la porte.
* **La sémantique de prudence change** → `risk.py` seul, et elle est rejouable
  sur un calendrier historique puisqu'elle est pure.
* **Le rythme de relecture change** → `policy.py` (`refresh_s`, `max_backoff_s`),
  et `service.py` ne fait que l'executer : c'est le SEUL endroit qui cree le fil,
  et il n'en cree aucun quand le flux est eteint.

### 3.1 La règle : aucun état sans lecteur

**Tout ce qui est produit est lu par quelque chose de nommable.** Un champ, un
fichier, un outil dont le seul lecteur est ce document n'existe pas : il est un
mensonge en attente, parce que rien ne le contredira jamais.

Ce que la règle a fait tomber, et pourquoi :

* **Trois champs macro sur le vecteur d'arrivée** (`macro_allows_new_risk`,
  `macro_conservative`, `macro_score`). Le premier était `True` par
  construction ; les deux autres n'étaient lus par personne. Or la seule
  information que le macro apporte à ce vecteur est « on a le droit de
  planifier » : elle est portée par le fait que `build_features` rend un vecteur
  plutôt que `None`. Les recopier ne les rendait pas vrais, seulement
  invérifiables. Le **refus** (bloc incomplet ou `allows_new_risk` faux ⇒ `None`)
  reste, intact — c'est lui qui satisfait le besoin.
* **`tools/macro_status.py`**, dont le seul lecteur était ce document. La
  vérification « la source répond-elle » est faite par la boucle elle-même au
  démarrage, dans le processus qui trade — le contrôle préalable en ligne de
  commande ne partageait pas son cache et affichait donc l'état d'un *autre*
  processus.
* **`build_feed()` et `MacroFeed.refresh_blocking()`**, qui n'avaient d'autre
  appelant que ce CLI. Les supprimer ensemble est la conséquence directe : les
  garder aurait recreé exactement l'état sans lecteur qu'on venait d'enlever.
* **Le second filet des constructeurs de bloc.** La boucle et la sonde du tableau
  de bord refaisaient chacune `try: macro_publication() except:
  macro_bloc_indisponible(...)`. Il ne subsiste qu'un exemplaire,
  `macro_bloc_pour_publication()`, et les deux appelants s'y adressent : les deux
  ne peuvent plus raconter deux histoires du même calendrier.

Ce qui *n'est pas* tombé, et pourquoi : les champs de trace (`state`, `score`,
`next_event`, `source_digest`) restent dans le bloc parce que **la porte les
cite** dans ses motifs — un `WAIT` qui ne nomme pas la publication qui l'a causé
oblige à relire le calendrier à la main, donc à ne pas le faire.

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

`PolicyContext` gagne un champ `macro`, **en dernier et avec un défaut `None`** :
toute construction positionnelle existante reste valide. `build_features` refuse
de planifier (`None`) quand le calendrier interdit le risque neuf, et ne fait que
cela : c'est la seule information que le macro apporte à ce vecteur, et elle est
portée par le fait qu'un vecteur est rendu.

Défaut `None` ⇒ **comportement d'avant, au bit près** : c'est ce qui rend la
non-régression de la matrice adaptative démontrable plutôt que promise, et c'est
vérifié sur le vecteur ENTIER (`test_le_contexte_d_arrivee_reste_neutre_sans_macro`
compare un contexte portant `macro=None` à un contexte construit sans le champ),
pas sur quelques copies de champs.

### 6.4 Le propriétaire du service — `titanium/macro/service.py`

Trois coutures ne suffisaient pas : le flux ne **démarrait nulle part**. Un
processus armé avec `enabled=true` restait donc sur `UNKNOWN`, donc sur un refus,
sans que rien ne le signale. `MacroService` ferme ce trou et n'a qu'un métier :

```python
service = MacroService()          # politique + cache du processus
if service.start():               # False si enabled=false : aucun fil cree
    ...                           # une premiere lecture est immediate
service.stop()                    # reveille la boucle, et la joint
```

Deux points de ce contrat ont ete mesures, parce que le premier etait faux dans
la premiere version :

* **La demande arrive AVANT que le fil n'arme son evenement, et elle survit.**
  Entre `Thread.start()` et l'instant ou le fil cree le sien, un `stop()`
  immediat ne reveillait rien : il rendait `False` apres son delai en laissant
  le fil vivant — 40 essais sur 40. La demande est desormais memorisee sous
  verrou, et relue par le fil en armant. Une demande arrivee avant la premiere
  lecture fait donc sortir la boucle **sans jamais lire**.
* **Le delai d'arret couvre la lecture que la politique autorise.** Un fil
  bloque dans un `fetch` ne peut pas etre tue : demander qu'il meure plus vite
  que le `timeout_s` qu'on lui a accorde est un contrat impossible a tenir. Le
  delai vaut donc `max(5 s, timeout_s + 1 s)`. Un `timeout_s` explicite a
  `stop(timeout_s=...)` reste prioritaire.

Le cycle de vie n'a qu'un proprietaire : `MacroService` cree, arme, reveille et
joint le fil. `MacroFeed` execute la boucle qu'on lui donne et n'expose plus de
`stop()`, qui dupliquait la responsabilite sans aucun appelant.

C'est `tools/live_demo.py` qui l'instancie : `_demarrer_macro()` avant la boucle,
`_arreter_macro()` dans le `finally`, et `battre()` publie le verdict normalisé
dans le battement de cœur. Le processus qui trade est le seul à relever ; le
processus qui affiche ne relève pas.

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

# 4. Demarrer la boucle. C'est ELLE qui valide la source, dans son propre
#    processus : elle affiche 'macro : flux actif' si le calendrier est lisible,
#    et publie son verdict dans results/loop_heartbeat.json a chaque tour.
.\.venv\Scripts\python.exe tools\live_demo.py            # observation d'abord

# 5. Lire la jauge. Elle affiche le verdict DE LA BOUCLE, et le dit :
#    'Verdict de la BOUCLE (il y a 3 s)' ; sans battement, 'Verdict LOCAL'.
```

**Ce qui rend la procedure lisible.** Il n'y a qu'un proprietaire par maillon :
le fichier de configuration dit *si* on relève, la boucle *releve*, le battement
*porte* le verdict, la sonde le *relit*. Rien ne dépend d'un ordre de démarrage
particulier : armée sans source fraîche, la boucle refuse (c'est-à-dire ne trade
pas) au lieu d'ignorer, et le tableau de bord le montre en rouge.

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

Mesuré, pas affirmé. Quatre exécutions complètes, deux campagnes de mesure, et
une comparaison cellule par cellule :

| Campagne | Base comparée | Matrice 15 politiques | Famille adaptative |
|---|---|---:|---:|
| Brique initiale (bibliothèque) | `origin/main` (`24d1dde`) | **12 960 / 12 960**, 0 cellule | **15 552 / 15 552**, 0 cellule |
| Fonctionnalité complète (boucle + jauges) | `local/adaptive-execution-session` (`1832b0b`) | **12 960 / 12 960**, 0 cellule | **15 552 / 15 552**, 0 cellule |

Classement adaptatif recalculé depuis les lignes brutes du lot : 17 techniques,
**10 gagnants distincts**, `adapt_midpoint_aggressive` +0,2446 en tête, aucun
couple de techniques identique sur l'ensemble des colonnes, `axes_inertes`
vide. Les chiffres publiés dans `docs/PLAN_EXECUTION_ADAPTATIVE_V14.md` restent
exacts — l'écart avec la base est nul sur les deux arènes.

L'empreinte de paquet (`engine_version`) change, par construction : des fichiers
de plus dans `titanium/execution_sim/` suffisent — et **modifier** une source du
moteur la déplace tout autant, sans ajouter un seul fichier. C'est le cas du
nettoyage décrit en §3.1, dont l'empreinte passe de `0a43328a…` à `70b26be0…`
alors que toutes les lignes de mesure, tous les classements et les deux rapports
restent identiques. C'est le seul écart attendu, et il est exclu de la
comparaison cellule par cellule.

**Ce que cette mesure ne dit pas.** Elle porte sur les deux arènes
`execution_sim`, pas sur la boucle armée : rien ici ne prouve qu'un ordre réel se
comporte mieux. Le veto macro ne change aucun nombre mesuré — c'est précisément
sa garantie.

## 9. Tests livrés

**75 cas, dont 22 ajoutés avec la fonctionnalité complète** :

| Fichier | Cas | Ce qu'il couvre |
|---|---:|---|
| `tests/test_macro_feed.py` | 57 | contrats, cache, risque, sources — **dont le chemin HTTP**, exerce pour la premiere fois |
| `tests/test_macro_service.py` | 11 | cycle de vie du service, arret immediat et delai derive, publication et relecture |
| `tests/test_web_macro_gauges.py` | 7 | route `/ui/macro`, rendu des jauges, severite, verdict de boucle perime |

`tests/test_macro_feed.py` — la non-régression en tête de fichier parce que
c'est elle qu'on casse en premier :

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

Le chemin **HTTP** était livré mais jamais parcouru : aucun test ne construisait
`HttpMacroSource`. Cinq cas le font désormais, et chacun finit en refus :

* **clé absente** ⇒ erreur nommant la variable, et **aucune requete emise** ;
* **délai dépassé** ⇒ panne de source, la dernière lecture connue survit, et
  c'est la **fraîcheur** qui la juge (perimée ⇒ `STALE` ⇒ refus) ;
* **charge utile illisible** (une ligne sans devise) ⇒ tout le calendrier est
  invalidé, rien n'est publié, le risque neuf est refusé ;
* **erreur serveur (500)** ⇒ panne de source, pas d'exception brute ;
* **calendrier valide** ⇒ `BLACKOUT` puis refus à la porte, et `CLEAR` puis
  `ENTER` quand la publication est lointaine. Un test qui n'exercerait que les
  pannes ne prouverait pas que le chemin marche.

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
* **Atteinte du mode fantôme (`titanium/shadow.py`).** Le veto macro y est
  inactif tant que `build_feats` n'y reçoit pas de symbole.
* **Historique du calendrier.** Le cache ne garde que la dernière lecture :
  impossible de rejouer « ce que le système savait à 14 h 25 ». Le `digest`
  stable existe pour ça, mais rien ne l'archive encore.
* **Une technique adaptative sensible au macro.** Aucune des 17 ne lit le
  macro, et le vecteur n'en porte plus rien (voir §3.1) : seuls le refus de
  planifier et le `WAIT`/`BLOCK` de la porte en dépendent aujourd'hui. En faire
  lire une changerait les nombres publiés, donc exige un nouveau lot de mesure
  complet, pas un ajout discret — et le champ à lui donner est `conservative`,
  qui est déjà publié par la porte.

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
* **L'encodage console sous Windows.** La ligne d'etat de `tools/live_demo.py`
  et ses motifs macro sont accentues ; un terminal en page de code 1252 les rend
  mal. Ce qui decide et s'affiche — le battement et la jauge — est de l'UTF-8
  correct.
* **La jauge a un TTL de 5 s.** Elle peut donc retarder de cinq secondes au plus
  — c'est un choix, pas une mesure : la relire a chaque battement de front
  couterait un calcul pur toutes les deux secondes pour un etat qui change au
  mieux a la minute.
* **Le verdict affiche est celui de la boucle, meme vieux.** Un bloc publie il y
  a une heure est RENDU, marque `perime` et grise : le remplacer par un calcul
  local ferait afficher « calme » a l'instant precis ou la boucle ne dit plus
  rien. Un bloc local n'est affiche que s'il n'y a aucun battement, et il est
  alors annonce comme LOCAL.
