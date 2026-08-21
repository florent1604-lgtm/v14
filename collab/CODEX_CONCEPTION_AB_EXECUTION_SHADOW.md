# A/B SHADOW d'execution V14 — conception et verdict de disponibilite

Date d'audit : 21/08/2026. Perimetre strictement offline, sans ordre, service
ou configuration d'environnement.

## Verdict

**NO-GO pour calculer aujourd'hui des fills ou une performance comparee.**
Produire des taux de fill, savings ou markouts maintenant obligerait a inventer
des evenements que les archives ne contiennent pas. Le nouveau validateur
`tools/evalue_ab_execution_shadow.py` refuse donc fail-closed et laisse toutes
les metriques a `null`.

Inventaire mesure au debut du lot :

- 141 symboles et 437 fichiers de quotes broker, environ 7,05 Go, du 15 au
  21/08/2026 ;
- 103 resumes de rejeu ;
- **0 artefact brut scelle** dans `results/rejeu_univers_brut`.

## Blocages causaux

1. Le brut actuel porte `bar_entree`, index de la barre M15. Or le moteur entre
   a la cloture de la barre. Sans `decision_at` explicite, apparier une quote au
   signal decalerait potentiellement chaque intention de quinze minutes.
2. Les quotes sont du L1 (`bid`, `ask`, parfois `last` et `volume`). Elles ne
   portent ni tailles bid/ask, ni sequence de carnet, ni transaction qualifiee,
   ni cote agresseur. Le passage d'un prix au niveau d'une limite ne prouve pas
   que notre ordre fictif aurait obtenu la priorite ou un fill.
3. Les intentions brutes ne portent pas encore `quantity` ni `asset_class`.
   Sans elles, le net intention-to-trade et l'agregation par classe ne sont pas
   definis proprement.

## Contrat de l'evaluateur futur

Chaque intention devra etre dupliquee, sans selection a posteriori, vers trois
bras deterministes : `market`, `limit_passive`, `adaptive`. Les trois bras
partagent `intent_id`, symbole, sens, quantite, instant d'arrivee et snapshot.
L'ordre de lecture est strictement croissant et une decision ne peut consulter
aucun evenement posterieur a son instant courant.

Metriques obligatoires par symbole et classe : fill rate, delai, slippage,
saving, markout a horizons fixes, cout d'opportunite des non-fills et resultat
net intention-to-trade. Les expirations restent dans le denominateur ; elles
ne peuvent jamais disparaitre du PnL par filtrage.

## Donnees minimales avant implementation

- artefacts bruts lies au resume et manifestes, avec `decision_at`, `quantity`
  et `asset_class` ;
- quotes broker horodatees UTC couvrant chaque fenetre intention → expiration
  → dernier horizon de markout ;
- pour un fill passif causal : sequence, tailles de file/profondeur et
  transactions avec prix, taille et cote agresseur ;
- politique explicite de latence, expiration, frais maker/taker et fallback ;
- snapshot de chaque tranche de quotes consommee et manifeste final du rapport.

Une simulation conservative « cross-through uniquement » pourra servir de
borne basse distincte, mais ne devra jamais etre etiquetee comme taux de fill
passif observe. Le bras market pourra employer la premiere quote executable
apres `decision_at`, avec un plafond de latence fixe a priori.

## Prochaine etape recommandee

1. Laisser le rejeu regenerer les artefacts bruts scelles.
2. Ajouter `decision_at`, `quantity` normalisee et `asset_class` au prochain
   schema brut, avec migration explicite de version.
3. Etendre la collecte si une source broker de transactions/profondeur existe ;
   sinon homologuer seulement une borne cross-through clairement nommee.
4. Relancer le validateur. N'implementer l'evaluateur A/B que lorsque son statut
   devient `READY_FOR_EVALUATOR`.

Ce document ne recommande aucune politique d'execution en production.
