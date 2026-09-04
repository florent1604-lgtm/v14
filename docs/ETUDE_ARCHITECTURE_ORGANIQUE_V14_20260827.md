# Étude Hermès — architecture organique de Titanium V14

**Date :** 27 août 2026  
**Statut :** étude d’architecture, sans câblage runtime  
**Mandat :** Florent, transmis par Codex puis repris directement par Florent  
**Périmètre :** FRED, EIA, moteurs V14 et LLM local de famille Llama  
**Sécurité :** PAPER/DEMO ONLY ; aucun ordre, seuil, sizing, service ou configuration modifié

## 1. Verdict exécutif

La vision est réalisable, mais la bonne cible n’est pas « un LLM placé au milieu de tous les appels ». Cette forme ferait du modèle local un point unique de panne, de latence et de non-déterminisme.

La cible recommandée est un **organisme à deux étages cognitifs** :

1. un **tronc cérébral déterministe**, qui maintient les invariants, la fraîcheur, le risque, le mode PAPER/DEMO et les protections même si toute IA est arrêtée ;
2. un **cortex associatif local**, servi par Llama, qui relie les sorties des organes, formule des hypothèses et explique les désaccords, mais ne crée pas d’ordre et ne modifie aucune politique.

Hermès reste le **cerveau exécutif/orchestrateur identifiable** : il observe les organes et le cortex local, organise les recherches et propose des expériences. Llama est un organe cognitif interne, pas l’autorité de trading. Claude reste gouverneur technique, Codex auditeur indépendant et Florent autorité humaine.

La structure V14 possède déjà plusieurs organes et une première auscultation. Il lui manque surtout :

- un système nerveux durable et typé ;
- une mémoire causale commune ;
- une séparation nette entre faits, interprétations et décisions ;
- une identité/version/health explicite pour chaque organe ;
- une ingestion FRED/EIA qui respecte publications et révisions historiques ;
- un port unique et modèle-agnostique vers le LLM local.

## 2. Ce que l’étude V12 avait correctement découvert

Les documents V12 `AUDIT_ARCHITECTURE_V12.md`, `CONTRE_REVUE_ARCHITECTURE_V12_CODEX_2026-07-19.md` et `CONTRAT_FUSION_HERMES_EVENTPLANE_COMMANDGATEWAY_2026-07-19.md` convergent :

- les moteurs fonctionnaient comme des boucles autonomes ;
- le cerveau était surtout un observateur périphérique ;
- le bus existant était utile à la télémétrie mais trop faible pour devenir un plan de contrôle ;
- le LLM ne devait jamais entrer directement dans le chemin critique ;
- la cible sûre était déjà `EventPlane -> projections -> cerveau -> Proposal -> PolicyKernel` ;
- la mémoire devait être une projection horodatée, jamais une vérité absolue ;
- l’apprentissage live autonome devait rester interdit.

L’étude V12 n’a donc pas échoué intellectuellement. Elle s’est arrêtée avant la transformation de ses contrats en architecture incrémentale vérifiée. V14 peut reprendre ces fondations sans recopier le monolithe de V12.

## 3. Cartographie de V14 aujourd’hui

| Élément actuel | Analogie organique | État architectural |
|---|---|---|
| Catalogue MT5 et données de marché | sens externes | actif, autoritaire pour le marché/broker |
| `titanium/features/` | aires sensorielles | déterministe |
| portes de confluence | réflexes appris/cortex moteur déterministe | décide de l’éligibilité mécanique |
| `titanium/emotion/` | système limbique | observation à deux axes ; ne doit pas devenir un faux RSI émotionnel |
| `titanium/fundamental_intelligence.py` | organe macro/endocrinien embryonnaire | collecte FRED/EIA mais provenance temporelle insuffisante |
| `titanium/live_memory.py` | mémoire épisodique statistique | scellée en partie, mais actuellement utilisée comme garde d’admission |
| `titanium/avis.py` + `tools/analystes.py` | pont vers cortex local | asynchrone, mais fichiers NDJSON et contrats faibles |
| `titanium/risk/riskgate.py` | homéostasie | déterministe, doit rester souverain |
| mur DEMO/réel et exécuteur | moelle/muscles | frontière critique à isoler absolument |
| `titanium/web/medecin.py` | médecin/système proprioceptif | bonne base d’auscultation des organes et artères |
| `titanium/orchestrator.py` | chaîne sens→décision→action | mélange encore délibération, sizing et exécution conceptuelle |
| Hub Claude/Codex/Hermès | cognition externe et gouvernance | traçable, hors chemin critique |

### 3.1 Contradiction critique existante

La documentation V14 affirme que le LLM ne peut influencer que la taille. Pourtant :

- `fundamental_intelligence.analyse()` demande au Qwen local `ALLOW|WAIT|BLOCK` ;
- `tools/analystes.py` persiste cette action ;
- `titanium.avis.autorisation_pour()` la transforme en autorisation ;
- `tools/live_demo.py::_garde_intelligente()` refuse l’entrée si l’action n’est pas `ALLOW`.

Le LLM local possède donc actuellement un **droit d’admission/véto indirect**, contrairement au README et aux invariants V12. Cette contradiction doit être traitée avant toute extension Llama. La cible proposée retire l’autorité au texte génératif : un éventuel veto macro doit provenir d’un moteur déterministe de faits et de règles versionnées.

## 4. Modèle organique cible

### 4.1 Identité des composants

Chaque organe doit publier une carte d’identité stable :

```text
organ_id           ex. macro.fred, macro.eia, emotion.circumplex
organ_version      version du contrat et du code
instance_id        UUID du boot
policy_version     version des règles déterministes
model_id           seulement pour les organes LLM
model_digest       hash du modèle/quantification/template
input_schema       version
output_schema      version
health             HEALTHY | DEGRADED | STALE | FAILED | UNKNOWN
last_heartbeat_at  UTC
last_success_at    UTC
lag_ms             retard mesuré
```

Le tableau de bord/ORBE doit montrer ces identités et leur santé réelle. Une animation ne prouve jamais qu’un organe fonctionne.

### 4.2 Anatomie proposée

| Corps humain | Composant V14 cible | Autorité |
|---|---|---|
| Sens | adaptateurs marché, FRED, EIA, CFTC, news | produisent des observations, jamais des ordres |
| Nerfs afférents | EventPlane durable et typé | faits append-only, replayables |
| Thalamus | normaliseur/router déterministe | contrôle schéma, identité instrument et fraîcheur |
| Organes spécialisés | moteurs technique, structure, émotion, macro, énergie, edge | sorties structurées et bornées |
| Sang | enveloppes de faits avec provenance/temps/version | transporte des faits vérifiables |
| Mémoire sensorielle | cache TTL par source | éphémère, jamais une preuve historique |
| Mémoire épisodique | journal de décisions/résultats scellé | append-only, causal |
| Mémoire sémantique | documentation et connaissances validées | versionnée, sans état live ni secret |
| Tronc cérébral | DataQualityKernel + PolicyKernel + RiskGate | déterministe, fail-closed |
| Cortex associatif | Llama local asynchrone | interprète et propose, aucune autorité directe |
| Cerveau exécutif | Hermès | orchestre l’étude et les propositions gouvernées |
| Système immunitaire | validation de schéma, détection d’anomalie, circuit breakers | rejette/quarantaine |
| Homéostasie | risque, exposition, mur DEMO/réel, kill switch | souverain |
| Nerfs efférents | CommandGateway fermé | aucune capacité d’ordre en phase d’étude/shadow |
| Muscles | exécuteur MT5 | isolé ; ne reçoit jamais de texte LLM |
| Médecin | `medecin.py` enrichi | observe seulement |

### 4.3 Flux recommandé

```text
FRED / EIA / marché / CFTC / news
                |
                v
      Adaptateurs de capture brute
                |
                v
   FactStore causal + manifestes + hashes
                |
                v
 Normalisation / fraîcheur / identité instrument
                |
                v
      EventPlane factuel durable
                |
       +--------+---------+----------+
       |                  |          |
       v                  v          v
 moteur macro       moteur énergie   moteurs V14 existants
 déterministe       déterministe     technique/émotion/edge
       |                  |          |
       +------------ OrganSnapshots-+
                          |
                          v
              BrainView read-only scellé
                          |
              +-----------+-----------+
              |                       |
              v                       v
      Tronc cérébral              Cortex Llama
 DataQuality/Policy/Risk          async, structuré
      souverain                       |
              |                       v
              |                 ProposalStore
              |                 (non autoritaire)
              +-----------+-----------+
                          |
                     Audit/ORBE

Aucun lien Cortex Llama -> MT5.
```

## 5. Données fondamentales FRED

### 5.1 Ce que V14 possède déjà

V14 dispose d’un client FRED avec séries macro curées : taux Fed, Treasuries, inflation, PIB, emploi, VIX, dollar, sentiment et logement. `fundamental_intelligence._fred()` choisit quelques indicateurs selon la classe d’actif.

### 5.2 Défauts actuels

1. `date.today()` et `observation_end` limitent les dates économiques, mais ne garantissent pas que la valeur révisée aujourd’hui était connue historiquement.
2. Les réponses sont réduites à du Markdown puis reparsées par recherche de lignes.
3. Le timestamp de publication réel n’est pas conservé.
4. Les vintages/revisions ne sont pas stockés.
5. `Evidence.observed_at` peut rester vide.
6. Les bytes bruts, paramètres de requête et métadonnées HTTP ne sont pas scellés.
7. La fraîcheur est affirmée dans le prompt sans être calculée.

### 5.3 Contrat requis

FRED expose officiellement :

- `fred/series/observations` avec `realtime_start`, `realtime_end` et `vintage_dates` ;
- `fred/series/vintagedates`, qui liste les dates où une valeur a été publiée ou révisée.

Pour chaque observation, conserver au minimum :

```text
source = FRED
series_id
observation_date
value
units
frequency
seasonal_adjustment
realtime_start
realtime_end
vintage_date
release_id éventuel
published_at si prouvable
first_fetched_at
fetched_at
request_fingerprint
raw_payload_sha256
license_note / source_notes
```

Règle causale : pour une décision à `T`, une valeur n’est admissible que si son vintage était disponible à `T`. Si l’heure exacte de publication n’est pas prouvée, utiliser `first_fetched_at` comme borne conservatrice. Une valeur révisée actuelle ne doit jamais être injectée dans un backtest comme si elle avait été connue à la date économique.

### 5.4 Licence

Les conditions FRED précisent que certaines séries appartiennent à des tiers et peuvent être protégées. Le registre de séries V14 doit donc stocker la note/source/copyright de chaque série et interdire une redistribution non vérifiée. La clé API reste hors logs, événements, prompts et rapports.

## 6. Données fondamentales EIA

### 6.1 Ce que V14 possède déjà

Le code utilise l’API EIA v2 via la route de compatibilité `seriesid` pour :

- WTI `PET.RWTC.D` ;
- Brent `PET.RBRTE.D` ;
- Henry Hub `NG.RNGWHHD.D`.

Il conserve période, valeur, unités et description.

### 6.2 Défauts actuels

1. `period` décrit la période économique, pas l’heure où la donnée est devenue disponible.
2. Il n’existe pas de journal de première observation ni de révision.
3. La route de compatibilité masque les facettes et métadonnées riches des routes v2 natives.
4. Les valeurs EIA v2 sont des chaînes ; la conversion, les valeurs manquantes et unités ne sont pas normalisées par contrat.
5. La clé est placée dans l’URL ; elle ne doit jamais apparaître dans les logs/exceptions/provenance.
6. Aucun calendrier de publication n’est corrélé aux données.
7. Le même TTL de 15 minutes est appliqué à des séries de fréquences différentes.

### 6.3 Contrat requis

Conserver :

```text
source = EIA
route_id / series_id
facets exactes
frequency
period
value_raw
value_normalized
units
published_at ou first_fetched_at
scheduled_release_at si disponible
fetched_at
revision_number ou snapshot_digest
raw_payload_sha256
query sans api_key
```

EIA ne fournit pas un ALFRED universel équivalent pour chaque série. Titanium doit donc construire ses propres vintages append-only à partir de la première ingestion. Pour l’historique antérieur à cette collecte, aucune causalité de publication ne doit être revendiquée sans archive de rapport prouvée.

L’EIA indique que ses données gouvernementales sont généralement dans le domaine public et recommande une attribution avec date de publication. Les éléments tiers et logos restent protégés.

## 7. Enveloppe fondamentale canonique

```json
{
  "schema_version": 1,
  "fact_id": "uuid",
  "source": "FRED|EIA",
  "series_id": "DGS10",
  "instrument_scope": ["US_INDICES", "USD", "XAU"],
  "economic_period": "2026-08-26",
  "value": 4.25,
  "units": "Percent",
  "frequency": "Daily",
  "published_at": null,
  "first_fetched_at": "UTC",
  "fetched_at": "UTC",
  "vintage_at": "UTC-or-null",
  "available_as_of": "UTC",
  "freshness_state": "FRESH|STALE|UNKNOWN",
  "revision_state": "INITIAL|REVISED|UNKNOWN",
  "raw_payload_sha256": "sha256",
  "query_fingerprint": "sha256-without-secret",
  "quality_flags": []
}
```

`available_as_of` est calculé de manière conservatrice. `UNKNOWN` ne devient jamais `FRESH` par défaut.

## 8. Quatre architectures possibles

### Option A — Llama synchrone dans la boucle de trading

**Description :** chaque moteur appelle le LLM et attend son verdict.

| Critère | Évaluation |
|---|---|
| simplicité apparente | élevée |
| latence CPU | mauvaise |
| disponibilité | mauvaise |
| déterminisme/rejeu | mauvais |
| sécurité | mauvaise |
| explicabilité | moyenne |

**Verdict : REJET.** Une génération lente ou bloquée arrêterait la boucle. Le texte deviendrait implicitement décisionnel.

### Option B — Fichiers asynchrones type `avis.py`

**Description :** demandes et avis NDJSON, travailleur LLM séparé.

| Critère | Évaluation |
|---|---|
| migration | facile, déjà présente |
| découplage | correct |
| causalité/replay | faible à moyenne |
| débit à long terme | faible, scans O(N) |
| supervision | faible |
| sécurité | acceptable en shadow seulement |

**Verdict : ACCEPTER comme pont transitoire**, à condition que l’avis soit purement consultatif et que le LLM perde son veto actuel.

### Option C — Blackboard/EventPlane organique

**Description :** chaque organe publie des faits structurés ; une BrainView read-only est reconstruite ; Llama lit un snapshot borné et publie une proposition séparée.

| Critère | Évaluation |
|---|---|
| découplage | excellent |
| causalité/replay | excellent |
| observabilité | excellente |
| coût de mise en place | moyen |
| sécurité | excellente si data/control planes séparés |
| testabilité | excellente |

**Verdict : RECOMMANDÉ.** C’est la maturation naturelle du contrat EventPlane V12.

### Option D — Conseil multi-agents d’organes

**Description :** un agent LLM par organe, débat puis synthèse.

| Critère | Évaluation |
|---|---|
| richesse narrative | élevée |
| latence CPU/RAM | très mauvaise |
| répétabilité | faible |
| double comptage/corrélation | risque élevé |
| prompt injection | surface élevée |
| coût d’exploitation | élevé |

**Verdict : EXPÉRIMENTAL OFFLINE UNIQUEMENT.** Inadapté au Ryzen 7 7730U CPU-only pour le runtime continu.

### Option E — Hiérarchie hybride recommandée

**Description :** moteurs déterministes et BrainView selon l’option C ; petit Llama local rapide comme cortex de synthèse ; Hermès conduit les analyses longues et la gouvernance hors runtime.

**Verdict : CIBLE.** Cette option donne un cerveau identifiable sans rendre le système dépendant du modèle local.

## 9. Port LLM local recommandé

Créer conceptuellement une interface `LocalCortexPort` indépendante du fournisseur :

```text
summarize(snapshot_ref) -> CortexAssessment
compare(hypothesis, evidence_refs) -> CortexAssessment
explain(decision_ref) -> HumanExplanation
health() -> ModelHealth
```

Les backends interchangeables peuvent être :

- Ollama, déjà utilisé par V14 ;
- serveur `llama.cpp` compatible OpenAI ;
- LM Studio ou autre serveur local compatible, pour les essais seulement.

V14 possède déjà un client `openai_compatible`. Il faut éviter tout appel HTTP ad hoc dispersé dans les moteurs.

### 9.1 Modèle et matériel

La documentation du projet identifie un Ryzen 7 7730U, 15,4 Go de RAM et aucun GPU utilisable. Recommandation :

1. commencer par un Llama Instruct **3B quantifié Q4** pour la synthèse structurée ;
2. comparer un **8B Q4** seulement en worker asynchrone si la RAM et le p95 restent acceptables ;
3. ne choisir aucun modèle sur sa réputation : benchmark local sur les schémas V14 ;
4. température `0` ou très basse ;
5. sortie JSON sous schéma strict ;
6. timeout et circuit breaker ;
7. une génération à la fois sur CPU ;
8. cache par digest exact du snapshot, pas seulement `(symbol, side)`.

Le Qwen 2.5 3B actuel peut servir de témoin A/B. La migration vers Llama doit prouver une meilleure fidélité au schéma, à la provenance et à l’abstention, pas seulement une prose plus agréable.

## 10. Mémoire du cerveau

Séparer quatre mémoires :

1. **mémoire sensorielle** — cache court, remplaçable, TTL par source ;
2. **mémoire de travail** — BrainView d’un cycle, immuable et hachée ;
3. **mémoire épisodique** — événements, propositions, décisions et résultats append-only ;
4. **mémoire sémantique** — règles métier validées, documents et relations stables.

La politique, le sizing, le RiskGate et les seuils ne sont pas une mémoire LLM : ce sont du code/configuration versionnés, revus et promus humainement.

Le LLM ne doit jamais écrire librement dans sa propre mémoire durable. Il peut proposer une note ; un processus déterministe la classe, la relie aux preuves et la place en attente de validation.

## 11. Menaces et parades

| Menace | Parade obligatoire |
|---|---|
| prompt injection dans news/données | données traitées comme non fiables, champs bornés, aucune instruction externe transmise comme autorité |
| hallucination de source | références de faits imposées ; ID inconnu = rejet |
| FRED révisé dans un backtest | vintage ALFRED/realtime exact, sinon analyse bloquée |
| EIA sans heure de publication | `first_fetched_at` conservateur, historique non revendiqué |
| donnée stale présentée fraîche | policy TTL par série + état `UNKNOWN/STALE` explicite |
| timeout Llama | fail-soft informatif ; aucun impact sur protections |
| JSON invalide | rejet du résultat, pas de réparation permissive dans le chemin critique |
| modèle changé silencieusement | model digest, template digest et paramètres dans chaque inférence |
| double comptage des moteurs | moteurs exposent features/provenance ; agrégation déterministe et tests d’ablation |
| fuite de clé API | redaction URL/logs, secret hors événements/prompts/manifeste |
| mémoire auto-modifiée | promotion humaine/versionnée seulement |
| sortie LLM vers MT5 | absence physique de capacité et de route réseau/fonctionnelle |

## 12. Feuille de route incrémentale

### Phase 0 — contrat et vérité actuelle

- documenter les organes et autorités ;
- corriger la contradiction « LLM taille seulement » versus veto fondamental ;
- figer les schémas `FundamentalFact`, `OrganSnapshot`, `BrainView`, `CortexAssessment` ;
- aucun changement runtime.

**Sortie :** contrat revu Claude + Codex + Florent.

### Phase 1 — FRED/EIA en shadow causal

- capture brute append-only ;
- manifestes et hashes ;
- vintages FRED ;
- snapshots locaux EIA ;
- règles de fraîcheur par série ;
- aucun branchement décisionnel.

**Preuve :** replay à une date T ne voit aucune valeur publiée/révisée après T.

### Phase 2 — système nerveux afférent

- EventPlane distinct du bus UI ;
- livraison au moins une fois, idempotence, offsets, replay, dead-letter ;
- projections read-only ;
- enrichissement de `medecin.py` avec heartbeat/lag/schema/version.

**Preuve :** arrêt/restart d’un organe sans perte silencieuse ni double effet.

### Phase 3 — cortex Llama shadow

- `LocalCortexPort` ;
- Llama 3B Q4 puis benchmark 8B Q4 ;
- BrainView bornée et hachée ;
- sorties JSON validées ;
- aucun avis consommé par `RiskGate` ou `live_demo`.

**Preuve :** débrancher Llama ne change aucune décision déterministe.

### Phase 4 — conseil visible

- ORBE montre désaccords, fraîcheur et provenance ;
- comparaison Llama/Qwen sur cas scellés ;
- évaluation humaine aveugle et tests d’abstention.

**Preuve :** taux de schéma valide, citations exactes, abstention sur données insuffisantes, p95 CPU.

### Phase 5 — propositions gouvernées

- uniquement si valeur incrémentale démontrée offline/shadow ;
- `ProposalStore` séparé ;
- PolicyKernel déterministe ;
- aucune capacité d’ordre par défaut ;
- toute influence future ouvre une nouvelle cohorte et exige un go Florent distinct.

## 13. Critères d’acceptation scientifiques et opérationnels

### Données

- 100 % des faits portent source, série, unités, période, disponibilité, fetch et hash ;
- aucun secret dans un payload, log ou manifeste ;
- tests de révision FRED et EIA ;
- test causal « décision avant publication » obligatoirement bloqué ;
- état `STALE/UNKNOWN` jamais transformé en `FRESH`.

### Cortex local

- 100 % des sorties acceptées valides sous schéma ;
- toute référence pointe vers un `fact_id` existant ;
- modèle/template/quantification/version enregistrés ;
- zéro route ou outil d’exécution ;
- timeout sans effet sur les décisions déterministes ;
- cache indexé par digest de BrainView complet ;
- benchmark CPU reproductible.

### Organisme

- chaque organe a heartbeat, health, version, lag et owner ;
- perte/gap/replay visibles ;
- protections et gestion de positions continuent sans Llama ;
- BrainView stale produit une abstention du cortex ;
- l’ORBE reflète les mesures, pas une animation optimiste.

### Gouvernance

- Claude valide l’architecture technique ;
- Codex red-team causalité, threat model et pannes ;
- Hermès consolide les preuves ;
- Florent autorise tout passage de phase ;
- aucun redémarrage, armement ou promotion implicite.

## 14. Décision recommandée

**GO pour une spécification exécutable Phase 0/1, shadow et read-only.**  
**NO-GO pour brancher Llama, FRED ou EIA comme autorité d’entrée/sizing/exécution aujourd’hui.**

La prochaine étape utile n’est pas de remplacer Qwen par Llama dans `fundamental_intelligence.py`. C’est de construire d’abord le `FactStore` causal et les contrats d’organes. Ensuite seulement, Llama peut devenir un cortex local propre, identifiable, remplaçable et réellement utile sans mettre le corps en danger.

## 15. Addendum après recherches indépendantes

Deux recherches indépendantes menées en parallèle confirment la cible et ajoutent les précisions suivantes.

### 15.1 FRED/ALFRED

- `series/observations` accepte jusqu’à 100 000 observations par réponse avec pagination par `offset`.
- `series/updates` ne couvre que les séries actualisées pendant les deux dernières semaines.
- Les périodes ALFRED `realtime_start`, `realtime_end` et les `vintage_dates` ont une granularité journalière. Elles ne prouvent donc pas une disponibilité intrajournalière.
- Pour un backtest intrajournalier, seuls les snapshots effectivement reçus par Titanium avant l’instant simulé sont admissibles.
- Pour une donnée journalière sans heure démontrable, la règle conservatrice est une disponibilité au jour suivant.
- Les transformations doivent être calculées localement depuis les niveaux bruts `units=lin` afin de conserver une logique reproductible et versionnée.
- FRED ne publie pas de quota stable par seconde : le client doit utiliser cache, déduplication, backoff exponentiel et jitter, sans inventer de limite contractuelle.
- Toute interface utilisant FRED doit porter la mention officielle : « This product uses the FRED® API but is not endorsed or certified by the Federal Reserve Bank of St. Louis. »

### 15.2 EIA

- Une réponse JSON EIA v2 est limitée à 5 000 lignes ; XML à 300. La pagination doit vérifier `total`, les chevauchements et les trous.
- Les valeurs JSON sont normalisées par EIA sous forme de chaînes : conversion numérique et contrôle des unités doivent être stricts.
- Pour le Weekly Petroleum Status Report et le Weekly Natural Gas Storage Report, l’arrivée dans l’API peut prendre jusqu’à deux heures après publication. `period` et calendrier ne prouvent donc jamais la disponibilité réelle.
- Les fichiers bulk sont adaptés au bootstrap et à la réconciliation, pas à une source de faible latence. Leur actualisation officielle est annoncée deux fois par jour, avec un délai supplémentaire de mise à disposition.
- Les indications générales EIA de throttling ne sont pas une garantie. Titanium doit viser un débit inférieur, mettre en cache et ouvrir un circuit breaker sur répétition de 429/403/5xx.
- `knowledge_time` doit être `retrieved_at_utc`. Un historique EIA antérieur au début des archives Titanium est `point_in_time_status=unverified` et exclu par défaut des métriques sans look-ahead.

### 15.3 Enveloppe HSM du cortex

La recherche comparative précise utilement le rôle du « tronc cérébral » sous forme de machine à états hiérarchique déterministe :

```text
OFFLINE
└── OBSERVING
    ├── NORMAL
    ├── CAUTIOUS
    │   ├── DATA_DEGRADED
    │   └── SIGNAL_CONFLICT
    ├── DEFENSIVE
    └── HALTED
```

Llama peut recommander `NO_CHANGE`, `CAUTIOUS` ou `DEFENSIVE`, avec preuves et expiration. La HSM décide si la transition est admissible. `HALTED`, le RiskGate, le sizing, les stops et le kill switch restent exclusivement déterministes. Une panne ou sortie invalide maintient l’état ou dégrade vers un état plus prudent ; elle n’augmente jamais l’exposition.

### 15.4 Tool-calling et conseil multi-agent

- Les outils Llama doivent être read-only, déclarés, bornés en temps/nombre/taille et retourner du JSON typé.
- Aucun shell, SQL libre, HTTP libre, écriture arbitraire, ordre, sizing ou stop n’est exposé.
- Le conseil multi-agent est réservé à l’analyse post-trade, aux scénarios rares et à la red-team offline. Un vote, même unanime, n’est jamais une décision d’exécution.
- La progression complète devient : replay offline → shadow → advisory humain → gated non critique. Toute extension gated reste optionnelle et exige Claude, Codex et Florent.

## 16. Sources consultées

### Projet

- `C:/Users/flore/Desktop/v12/collab/AUDIT_ARCHITECTURE_V12.md`
- `C:/Users/flore/Desktop/v12/collab/CONTRE_REVUE_ARCHITECTURE_V12_CODEX_2026-07-19.md`
- `C:/Users/flore/Desktop/v12/collab/CONTRAT_FUSION_HERMES_EVENTPLANE_COMMANDGATEWAY_2026-07-19.md`
- `C:/Users/flore/Desktop/v12/tools/llm_adapt_study.py`
- `titanium/fundamental_intelligence.py`
- `tradingagents/dataflows/fred.py`
- `titanium/avis.py`
- `tools/analystes.py`
- `tools/live_demo.py`
- `titanium/live_memory.py`
- `titanium/orchestrator.py`
- `titanium/risk/riskgate.py`
- `titanium/web/medecin.py`

### Sources officielles

- FRED observations : https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- FRED vintage dates : https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html
- FRED API Terms : https://fred.stlouisfed.org/docs/api/terms_of_use.html
- EIA Open Data : https://www.eia.gov/opendata/index.php
- EIA API documentation : https://www.eia.gov/opendata/documentation.php
- EIA copyrights/reuse : https://www.eia.gov/about/copyrights_reuse.php
