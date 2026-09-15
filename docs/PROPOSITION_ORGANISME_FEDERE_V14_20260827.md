# Proposition V2 — Titanium comme organisme fédéré

**Date :** 27 août 2026  
**Statut :** nouvelle étude demandée par Florent ; aucune modification runtime  
**Remplace comme cible :** la proposition centralisée EventPlane/blackboard/HSM  
**Invariants :** PAPER/DEMO ONLY, aucun lien direct LLM→MT5, aucune modification autonome du risque

## 1. Pourquoi la première proposition ne convient pas

> **Backend cognitif désormais disponible :** `glm4:9b` est installé et vérifié dans Ollama. Dans cette V2, GLM est le premier tissu cortical local ; le `CortexPort` reste volontairement interchangeable pour comparer ultérieurement GLM, Llama ou un autre modèle sans modifier les organes.

La première proposition utilisait la biologie comme vocabulaire posé sur une architecture informatique classique : toutes les informations montaient vers un tableau central, puis un centre décidait. Cela reste une topologie hub-and-spoke.

Un corps humain réel ne fonctionne pas ainsi :

- le cœur, le foie, les reins, les poumons et l’intestin maintiennent leur propre état ;
- le système nerveux autonome coordonne sans demander une décision consciente à chaque cycle ;
- les hormones diffusent lentement et n’agissent que sur les organes possédant les bons récepteurs ;
- des réflexes locaux protègent le corps avant toute réflexion consciente ;
- le cerveau reçoit des résumés et intervient surtout pour les situations nouvelles, conflictuelles ou intentionnelles ;
- plusieurs formes de mémoire et de contrôle coexistent ;
- une défaillance locale dégrade le corps sans obligatoirement l’arrêter entièrement.

La nouvelle cible est donc **polycentrique** : chaque organe V14 devient un acteur autonome, avec mémoire locale, contrat, santé, récepteurs et boîte aux lettres. Le cerveau ne lit pas un état global mutable et ne microgère pas les organes.

## 2. Principe fondateur

> **Autonomie locale, coordination physiologique, conscience centrale seulement quand nécessaire.**

Titanium devient une fédération d’organes :

```text
organe autonome
  ├── possède son état local
  ├── reçoit uniquement les signaux auxquels il est sensible
  ├── produit des pulsations factuelles
  ├── maintient sa propre santé
  ├── se dégrade explicitement
  └── ne connaît pas l’implémentation des autres organes
```

Il n’existe plus de « grand objet état » modifiable par tous. Le `BodyMap` est une photographie reconstruite des pulsations ; il n’est jamais une source autoritaire.

## 3. Les quatre systèmes de communication

### 3.1 Circulation — transporter les faits

La circulation transporte les éléments nécessaires au fonctionnement général :

- pulsations des organes ;
- observations de marché ;
- provenance ;
- santé ;
- résultats d’action ;
- nutriments informationnels FRED/EIA.

Elle ne transporte aucune commande libre. Les messages sont immuables, hachés, versionnés et expirables.

```text
OrganPulse
  organ_id
  instance_id
  pulse_type
  occurred_at
  expires_at
  body_scope
  payload_schema
  payload
  evidence_refs
  health
  sequence
```

### 3.2 Nerfs — messages ciblés et rapides

Un signal nerveux est point-à-point : il relie un émetteur à un récepteur connu.

Exemples :

- l’œil marché signale une rupture de cotation au tronc cérébral ;
- le RiskGate envoie une contraction défensive à l’organe moteur ;
- le cerveau demande au foie-données de détailler une anomalie ;
- l’exécuteur confirme le résultat d’une intention motrice.

Un `NeuralImpulse` possède : origine, destination, type allowlisté, TTL, priorité, causalité et schéma exact. Un organe sans récepteur correspondant ignore le signal.

### 3.3 Endocrinien — moduler lentement

FRED et EIA ne doivent pas agir comme des ordres. Ils deviennent le système endocrinien : ils publient un contexte macro lent, dont l’effet décroît avec le temps.

Exemples de « hormones » déterministes :

- `liquidity_stress` ;
- `usd_pressure` ;
- `rates_restriction` ;
- `inflation_impulse` ;
- `energy_supply_stress` ;
- `inventory_surprise` ;
- `macro_uncertainty`.

```text
HormoneSignal
  hormone_type
  concentration        0..1
  direction            -1..1
  released_at
  half_life_seconds
  expires_at
  receptor_classes
  evidence_refs
  calculator_version
  freshness_state
```

Les valeurs et directions sont calculées par des règles déterministes versionnées. Llama peut les interpréter, mais il ne fabrique pas leur concentration. Chaque organe déclare ses récepteurs. Par exemple, l’organe énergie reçoit `inventory_surprise`, tandis que l’organe crypto peut recevoir `liquidity_stress` mais pas une donnée EIA sans relation prouvée.

### 3.4 Système immunitaire — reconnaître et isoler

Le système immunitaire est distribué :

- validation de schéma à l’entrée de chaque organe ;
- détection de données impossibles, révisions, duplications et injections ;
- quarantaine d’une source ou d’un organe ;
- propagation d’un signal inflammatoire `DATA_INTEGRITY_ALERT` ;
- impossibilité pour une donnée externe de devenir une instruction.

L’immunité ne décide pas un trade. Elle protège l’intégrité du corps.

## 4. Anatomie fonctionnelle de V14

| Anatomie | Composant V14 | Autonomie réelle |
|---|---|---|
| yeux/oreilles | MT5, marché, news, CFTC | observent et publient ; ne décident pas |
| poumons | collecteurs et cadence de rafraîchissement | maintiennent l’apport de données et leur débit |
| foie | normalisation, unités, symboles, provenance | nettoie/détoxifie ; rejette les payloads invalides |
| reins | fraîcheur, valeurs manquantes, qualité, révisions | filtre et marque `STALE/UNKNOWN` |
| système endocrinien | FRED/ALFRED et EIA | produit des hormones lentes avec demi-vie |
| système limbique | moteur émotion valence/arousal | produit peur/avidité + énergie ; aucun indicateur mécanique déguisé |
| hippocampe | décisions, épisodes et résultats scellés | mémoire épisodique causale |
| cervelet | rejeu, calibration, erreur de prédiction | compare intention/résultat hors ligne ; ne promeut rien seul |
| ganglions de la base | portes de confluence et sélection d’action | choisit parmi les intentions admissibles |
| tronc cérébral | heartbeat, kill switch, mode, sessions, fraîcheur vitale | maintient la survie sans LLM |
| système nerveux autonome | supervision, budgets, exposition, rythme | coordonne les fonctions permanentes |
| cortex local | Llama | synthèse, scénarios, conflits, intention consciente |
| cortex préfrontal/identité | Hermès | continuité de mission, objectifs, gouvernance et dialogue |
| système immunitaire | schémas, redaction, anomaly/quarantine | protège chaque frontière |
| moelle épinière | réflexes de sûreté et CommandGateway | applique uniquement des arcs réflexes codés |
| muscles | exécuteur MT5 DEMO | reçoit seulement une intention validée |
| peau/proprioception | ORBE + `medecin.py` | expose santé, douleur, débit et coordination |

## 5. Un organe logiciel autonome

L’autonomie ne signifie pas un microservice par fichier. En phase initiale, un organe peut être un acteur logique dans le même processus. Il doit néanmoins respecter un contrat d’isolation :

```text
Organ
  identity()       -> OrganIdentity
  receptors()      -> set[SignalType]
  receive(signal)  -> Ack
  pulse()          -> OrganPulse
  health()         -> OrganHealth
  snapshot()       -> OrganSnapshot
  shutdown()       -> ShutdownReceipt
```

Chaque organe :

1. possède sa boîte aux lettres bornée ;
2. traite les messages séquentiellement ;
3. conserve un état local versionné ;
4. refuse les schémas inconnus ;
5. publie un heartbeat ;
6. annonce explicitement saturation, retard ou panne ;
7. n’appelle jamais directement un autre organe par import métier ;
8. ne partage pas de global mutable ;
9. peut être remplacé par une doublure de replay ;
10. peut tomber sans corrompre les autres.

La technologie adaptée est un **modèle d’acteurs supervisés**, pas une forêt immédiate de microservices.

## 6. Le cerveau identifiable

### 6.1 Une identité, plusieurs tissus

Le cerveau V14 n’est pas seulement Llama. Il est composé de tissus aux rôles distincts :

```text
Identité consciente : Hermès
Substrat associatif local : Llama
Mémoire de travail : contexte d’un épisode
Hippocampe : épisodes scellés
Cervelet : erreurs et rejeux
Ganglions de la base : sélection déterministe
Tronc cérébral : fonctions vitales
Moelle : réflexes codés
```

Hermès donne la continuité : qui est Titanium, quel est son objectif, quelles limites Florent a fixées, quelles hypothèses sont en cours. Llama fournit la capacité locale de synthèse et de simulation. Si Llama est remplacé ou redémarré, l’identité, les règles et l’historique ne disparaissent pas.

### 6.2 Ce que le cerveau reçoit

Le cerveau ne reçoit pas tous les ticks. Chaque organe effectue une compression locale et transmet :

- changement significatif ;
- conflit ;
- douleur/alerte ;
- besoin de coordination ;
- demande consciente ;
- résumé périodique.

C’est l’équivalent du filtrage sensoriel biologique. Le cerveau n’est réveillé que lorsqu’un seuil déterministe est franchi ou qu’une intention consciente est demandée.

### 6.3 Ce que Llama produit

Llama produit une `MotorIntent`, pas une décision broker :

```json
{
  "intent_id": "uuid",
  "goal": "OBSERVE|PREPARE|ABSTAIN|REQUEST_REVIEW",
  "instrument_id": "canonical-or-null",
  "hypotheses": [],
  "supporting_organs": [],
  "dissenting_organs": [],
  "evidence_refs": [],
  "uncertainties": [],
  "invalidators": [],
  "valid_until": "UTC",
  "brain_identity": {
    "hermes_policy": "version",
    "llama_model_digest": "sha256",
    "prompt_digest": "sha256"
  }
}
```

En shadow/advisory, `goal` ne contient même pas `ENTER`. Le cerveau apprend d’abord à percevoir, expliquer et s’abstenir.

### 6.4 De l’intention au mouvement

```text
MotorIntent Llama/Hermès
          ↓
Ganglions de la base déterministes
  (portes, stratégie, admissibilité)
          ↓
Système autonome / RiskGate
  (risque, exposition, mode, compte)
          ↓
Moelle / réflexes de sûreté
  (TOCTOU, idempotence, mur DEMO)
          ↓
Muscle / exécuteur MT5 DEMO
```

Chaque niveau peut refuser. Aucun niveau LLM ne peut forcer le suivant.

## 7. Les arcs réflexes

La biologie conserve des réflexes qui n’attendent pas le cerveau. V14 doit faire de même.

### Réflexes autorisés

- bloquer une nouvelle entrée si donnée vitale stale ;
- refuser un compte non DEMO ;
- refuser une exposition ou un lot invalide ;
- maintenir un stop déjà posé selon une politique codée ;
- kill switch ;
- quarantaine d’une source corrompue ;
- marquer un résultat inconnu sans retry aveugle.

### Réflexes interdits

- créer une nouvelle stratégie ;
- augmenter le risque ;
- réarmer automatiquement un circuit breaker ;
- déplacer un stop dans un sens plus risqué ;
- transformer une hypothèse LLM en ordre ;
- modifier ses propres récepteurs ou règles.

## 8. Coordination entre organes

### Exemple 1 — choc énergétique

1. EIA est ingérée par le foie et filtrée par les reins.
2. L’organe endocrinien calcule `inventory_surprise=0.72`, demi-vie six heures.
3. Seuls les organes ayant le récepteur correspondant reçoivent le signal.
4. L’organe énergie ajuste son contexte local et publie une pulsation.
5. Le système limbique peut signaler une forte énergie de foule indépendamment.
6. Le cerveau reçoit le conflit ou la convergence, puis explique les scénarios.
7. Les portes et le risque restent seuls capables de valider une éventuelle action.

### Exemple 2 — FRED révisé

1. Un nouveau vintage diffère du précédent.
2. Le système immunitaire publie `REVISION_DETECTED`.
3. L’hippocampe conserve les deux versions et leurs temps de connaissance.
4. Les organes concernés recalculent uniquement leur état futur ; aucun événement passé n’est réécrit.
5. Le cervelet peut rejouer l’erreur causée par l’ancienne valeur, hors ligne.
6. Llama explique l’impact, sans modifier la politique.

### Exemple 3 — cerveau indisponible

1. Llama cesse de répondre.
2. Le tronc cérébral le marque `UNCONSCIOUS`.
3. Les organes continuent leur homéostasie et leurs pulsations.
4. Les protections et la gestion codée des positions continuent.
5. Aucune nouvelle intention consciente n’est produite.
6. Le corps passe en mode déterministe conservateur, sans fausse analyse.

## 9. Topologies comparées

### A. Corps monolithique centralisé

Tous les organes écrivent dans un état commun et le cerveau le lit.

**Rejet :** ce serait la première proposition renommée. Les organes ne seraient pas autonomes.

### B. Microservices complets

Chaque organe possède processus, base et API réseau.

**Différer :** fidèle à l’isolation, mais trop coûteux et fragile pour la machine actuelle. Le réseau ajouterait une panne à chaque artère.

### C. Organisme fédéré par acteurs supervisés

Chaque organe est un acteur autonome avec mailbox, état local et contrat. Les organes lourds — Llama et ingestion — peuvent être isolés en processus. Les communications physiologiques sont typées.

**Recommandé :** autonomie réelle sans explosion opérationnelle.

## 10. Structure de développement proposée

Cette arborescence est une cible, pas du code déjà validé :

```text
titanium/
  soma/
    contracts.py          # enveloppes communes
    organ.py              # interface et cycle de vie
    mailbox.py            # boîte bornée/idempotence
    supervisor.py         # arbre de supervision
    circulation.py        # pulsations factuelles
    nervous.py            # impulsions ciblées
    endocrine.py          # hormones, récepteurs, demi-vie
    immune.py             # validation/quarantaine
    body_registry.py      # identités et santé
    body_map.py           # projection read-only
  brain/
    identity.py           # identité Hermès/politique
    cortex_port.py        # Ollama/llama.cpp interchangeable
    sensory_filter.py     # résumés significatifs
    working_memory.py     # épisode courant
    hippocampus.py        # mémoire épisodique scellée
    cerebellum.py         # rejeu et erreur de prédiction
    motor_intent.py       # schéma de proposition
  organs/
    market/
    technical/
    emotion/
    macro/
    energy/
    edge/
    risk/
    execution/
```

Les modules V14 existants ne sont pas déplacés immédiatement. Les premiers organes sont des adaptateurs shadow autour d’eux.

## 11. Nouvelle feuille de route

### Étape 0 — autopsie fonctionnelle

- inventorier les responsabilités, états globaux et appels directs ;
- identifier quelles fonctions appartiennent réellement à chaque organe ;
- cartographier les artères actuelles dans `medecin.py` ;
- documenter les récepteurs et signaux attendus ;
- traiter le veto Qwen contradictoire avant toute extension.

**Aucun comportement modifié.**

### Étape 1 — squelette Soma

- définir `OrganIdentity`, `OrganPulse`, `NeuralImpulse`, `HormoneSignal`, `OrganHealth` ;
- créer mailbox bornée et superviseur ;
- tests de saturation, duplication, ordre et extinction ;
- aucune connexion à MT5.

### Étape 2 — trois organes pilotes

Envelopper sans réécrire :

1. émotion ;
2. macro FRED/EIA ;
3. mémoire edge.

Ils publient en shadow et leurs sorties sont comparées aux comportements actuels. Aucune consommation décisionnelle.

### Étape 3 — endocrinien causal

- vintages FRED/ALFRED ;
- snapshots EIA append-only ;
- calculateurs hormonaux déterministes ;
- demi-vie et récepteurs ;
- tests de révision et disponibilité intrajournalière.

### Étape 4 — système nerveux autonome

- tronc cérébral ;
- arbre de supervision ;
- heartbeat ;
- modes `AWAKE`, `DROWSY`, `UNCONSCIOUS`, `PAIN`, `HALTED` ;
- dégradation locale et récupération contrôlée.

### Étape 5 — cerveau Llama/Hermès shadow

- `CortexPort` local ;
- identité et digests ;
- filtre sensoriel ;
- mémoire de travail ;
- `MotorIntent` limitée à observation/abstention/revue ;
- tests de déconnexion et injection.

### Étape 6 — ORBE physiologique

Afficher :

- organes vivants, ralentis, douloureux ou isolés ;
- artères nerveuses actives ;
- hormones et demi-vies ;
- niveau de conscience du cerveau ;
- douleurs/immunité ;
- réflexes actifs ;
- PAPER/DEMO provenant d’une autorité réelle.

### Étape 7 — conseil humain

- intentions Llama visibles mais non consommées ;
- comparaison avec décisions déterministes ;
- mesure des abstentions, erreurs, conflits et citations ;
- revue Claude et red-team Codex.

Aucune étape d’action autonome n’est proposée tant que la valeur du cerveau n’est pas démontrée.

## 12. Tests falsifiables

### Autonomie

- tuer un organe ne corrompt pas l’état des autres ;
- redémarrer un organe reconstruit son état depuis ses preuves ;
- mailbox pleine produit une douleur visible, jamais une perte silencieuse ;
- un organe ne peut envoyer qu’aux récepteurs déclarés.

### Endocrinien

- une hormone décroît selon sa demi-vie ;
- une hormone expirée n’agit plus ;
- un organe sans récepteur reste inchangé ;
- une révision FRED/EIA crée une nouvelle version ;
- une donnée sans temps de connaissance reste `UNKNOWN`.

### Cerveau

- débrancher Llama ne change aucune protection ;
- une réponse sans preuve est rejetée ;
- une instruction cachée dans EIA/news est traitée comme donnée ;
- une `MotorIntent` contenant lot, stop ou ordre est invalide ;
- le modèle et le prompt sont identifiables pour chaque épisode.

### Corps

- les décisions shadow actuelles et Soma sont comparables bit à bit quand aucun nouvel organe n’intervient ;
- aucune route directe cerveau→exécuteur n’existe ;
- les réflexes de réduction de risque survivent à la perte du cerveau ;
- chaque douleur affichée dans l’ORBE possède une preuve mesurée.

## 13. Ce qui change réellement par rapport à V1

| V1 rejetée | V2 proposée |
|---|---|
| tableau central lu par tous | état local dans chaque organe |
| coordination par blackboard | nerfs ciblés + hormones + circulation |
| HSM centrale dominante | réflexes et autonomie distribués |
| cerveau lecteur d’une vue globale | cerveau filtré, réveillé par événements significatifs |
| FRED/EIA comme facts/features | FRED/EIA comme système endocrinien à demi-vie |
| santé supervisée globalement | immunité et santé à chaque frontière |
| composants autour d’un hub | acteurs fédérés avec récepteurs explicites |

## 14. Consolidation après les deux études indépendantes

Les deux revues parallèles confirment l’**organisme polycentrique neuroendocrinien** et rendent la proposition plus exécutable.

### 14.1 Frontières d’autorité concrètes

Les organes cibles ne sont pas de simples noms anatomiques :

```text
MarketSensingOrgan  -> observations et qualité
SignalOrgan:*       -> intentions sans taille
PortfolioOrgan      -> conflits et proposition de portefeuille
RiskOrgan           -> RiskGrant déterministe, borné, expirant, à usage unique
ExecutionOrgan      -> machine d’état d’ordre
BrokerGatewayOrgan  -> traduction MT5 DEMO seulement
AccountingOrgan     -> ledger append-only faisant foi
SurveillanceOrgan   -> immunité, alerte et quarantaine
MemoryOrgan         -> épisodes et modèles versionnés
```

La chaîne motrice complète est :

```text
MarketObservation
  -> TradeIntent
  -> SelectionDecision
  -> PortfolioProposal
  -> RiskGrant
  -> PaperOrderCommand
  -> BrokerAck / Fill
  -> LedgerEntry
```

`TradeIntent` ne contient ni lot ni ordre. `RiskGrant` est mono-usage, borné et expirant. Le gateway refuse toute commande sans grant valide et tout mode autre que PAPER/DEMO.

### 14.2 Circulation artérielle et veineuse

- **artérielle** : faits validés vers les récepteurs ;
- **veineuse** : confirmations, erreurs, déchets, télémétrie et demandes de compensation.

L’ordre n’est garanti que par agrégat, jamais globalement. Chaque organe reconstruit son état depuis son checkpoint et son suffixe d’événements, sans lire la base privée d’un autre organe.

### 14.3 Système autonome

```text
NORMAL -> CAUTIOUS -> STRESSED -> SAFE/FROZEN -> RECOVERY
```

La branche sympathique réduit les TTL, augmente les contrôles et abaisse les plafonds. Elle n’augmente jamais la prise de risque. Le retour vers NORMAL exige fenêtre stable, réconciliation et hystérésis ; aucun saut direct depuis STRESSED.

### 14.4 GLM comme cerveau intégrateur réel

GLM ne doit pas être un simple résumeur. Sous l’ordonnancement attentionnel d’Hermès, il doit maintenir un état cognitif versionné, conserver les contradictions, produire des hypothèses causales, simuler plusieurs futurs, demander l’organe manquant et réviser sa croyance après une copie d’efférence.

Le partage est :

```text
Hermès choisit quand et sur quoi penser.
GLM construit ce que Titanium pense de la situation.
Les organes possèdent les faits et leur état local.
Les gardes déterministes décident si une intention peut devenir action.
```

Une réponse GLM devient au maximum une `MotorIntent` typée avec preuves, incertitudes, alternatives, prédictions et expiration. Le résultat réel ou simulé revient au cerveau sous forme de `MotorOutcome`; sans cette copie d’efférence, le cerveau apprendrait dans un monde fictif.

### 14.5 Tests qui rendent la biologie falsifiable

La V2 n’est acceptée que si les tests prouvent notamment :

1. la perte d’un moteur n’arrête pas les autres ;
2. la perte d’Hermès/GLM laisse fonctionner le ledger et les réflexes, mais interdit toute nouvelle intention cognitive ;
3. un réflexe ne peut que `CANCEL`, `FREEZE`, `REDUCE` ou `ISOLATE` avec exposition postérieure non supérieure ;
4. une hormone expirée n’a aucun effet ;
5. un organe sans récepteur ignore l’hormone ;
6. un message dupliqué ne crée pas deux effets ;
7. un ancien `RiskGrant` ne peut pas être rejoué ;
8. un organe en quarantaine reste observable mais non éligible ;
9. aucune sortie GLM ne correspond directement au contrat accepté par l’exécuteur ;
10. la valeur `LIVE` est invalide au schéma et absente du gateway ;
11. face à marché favorable + données stale + exposition élevée, GLM représente les trois faits et les contradictions ;
12. une erreur de prédiction modifie l’hypothèse au cycle suivant, pas seulement sa formulation.

### 14.6 Déploiement corrigé

La progression retenue devient :

```text
replay hors ligne
  -> organes shadow dans un seul déploiement
  -> fonctions vitales isolées
  -> réflexes et immunité
  -> homéostasie/endocrinien
  -> GLM shadow passif
  -> GLM shadow contrefactuel
  -> advisory interne
  -> advisory Florent
```

Aucune autonomie d’exécution, même PAPER, n’est incluse dans ce chantier. Elle constituerait un projet distinct après preuves scellées.

## 15. Verdict de développement

**Proposition retenue : organisme fédéré par acteurs supervisés, consolidé comme organisme polycentrique neuroendocrinien.**

Le premier lot ne doit pas encore câbler GLM au runtime. Il doit construire le **Soma** : contrats d’organes, mailboxes, récepteurs, pulsations, hormones, immunité et supervision en shadow. Sans ce corps, ajouter le modèle reproduirait l’échec V12 : un LLM périphérique posé sur des moteurs qui ne parlent pas son langage.

Une fois le Soma prouvé, `glm4:9b` devient le premier tissu cortical local via `CortexPort`, et Hermès l’identité exécutive du système. Le cerveau reçoit des sensations filtrées, forme des intentions et apprend des épisodes, tandis que les organes continuent de vivre, se protéger et coopérer sans être microgérés. Llama reste un backend comparatif possible, pas une dépendance architecturale.

**GO proposé :** spécification exécutable Étapes 0–2, read-only/shadow.  
**NO-GO :** réécriture massive, microservices complets ou branchement cerveau→exécution.
