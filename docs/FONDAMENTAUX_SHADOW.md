# Fondamentaux V14 — intégration SHADOW

## Verdict d’architecture

L’analyse fondamentale est intégrée comme **variable de recherche explicable**,
pas comme sixième pilier ni comme permission d’exécution. La première phase ne
modifie ni `build_feats`, ni `risk_context_from`, ni `RiskGate`, ni la boucle
MT5. GitNexus classe ces coutures CRITIQUES ; leur activation nécessitera une
preuve hors échantillon et une décision humaine distincte.

Le moteur déterministe est `titanium/fundamentals.py`. Le producteur de
photographies est `tools/fundamentals_shadow.py`.

## Pourquoi le texte initial ne peut pas devenir directement du code

Les relations « inflation en hausse = devise en hausse » ou « bon PIB = indice
en hausse » sont des hypothèses, pas des lois. Le marché réagit surtout à la
**surprise par rapport au consensus**, aux révisions et à la réaction attendue
de la banque centrale. Une même surprise peut soutenir le dollar mais pénaliser
les valeurs de croissance.

V14 exige donc que chaque adaptateur de données publie explicitement ses effets
par facteur, par exemple :

```json
{
  "schema_version": 1,
  "record_id": "us-cpi-2026-08-vintage-1",
  "kind": "release",
  "category": "inflation",
  "event_at": "2026-08-20T12:30:00Z",
  "available_at": "2026-08-20T12:30:03Z",
  "factors": {"USD": 0.7, "US_TECH": -0.5, "GOLD": -0.4},
  "confidence": 0.8,
  "half_life_hours": 24,
  "source": "source-et-vintage-identifiables"
}
```

Une révision est une nouvelle ligne avec un nouvel identifiant. Aucune ligne
historique n’est réécrite.

## Deux sorties séparées

1. `direction_score` : biais vu du côté LONG de l’actif, entre −1 et +1 ;
2. `event_risk` : proximité d’une annonce importante, sans inventer de sens.

`unknown` est distinct de `neutral`. Une panne de fournisseur ou une couverture
absente ne devient jamais artificiellement un score nul.

Pour le FX, le score est différentiel : EURUSD reçoit l’exposition `EUR - USD`.
Pour les indices, matières premières, métaux et cryptos, les alias corrélés sont
ramenés aux mêmes facteurs économiques.

## Utilisation

Le journal attendu est `results/fundamentals_observations.ndjson` : une ligne
JSON par fait, schéma 1. Production d’une photographie :

```powershell
.venv\Scripts\python.exe tools\fundamentals_shadow.py `
  --symbols EURUSD USTECH XAUUSD USOIL
```

La sortie `results/fundamentals_shadow.json` porte toujours :

```json
{"mode": "SHADOW_ONLY", "would_block": false, "would_reduce": false}
```

## Protocole avant activation

1. Choisir des sources point-in-time et archiver publication, consensus,
   révision, disponibilité et vintage.
2. Collecter sans influencer les ordres.
3. Joindre causalement chaque entrée V14 à la dernière photographie disponible.
4. Comparer l’espérance nette OOS par classe, régime, sens et tranche de score.
5. Corriger la multiplicité et regrouper les alias/corrélations de Prime.
6. Tester séparément le veto événementiel et le biais directionnel.
7. N’envisager un branchement `fundamentals_reduce` qu’après amélioration nette,
   stable et reproductible. `fundamentals_block` exige une preuve encore plus
   forte et une validation humaine explicite.

## Données à prioriser

- FX : surprises de taux, inflation, emploi, PIB et PMI **en différentiel** ;
- indices : croissance, taux réels, révisions de bénéfices et breadth ;
- pétrole/gaz : stocks, production, OPEP et demande ;
- métaux : taux réels, dollar, inflation anticipée et flux refuge ;
- crypto : liquidité globale, dollar, taux réels et événements réglementaires.

Les données d’entreprise du `Fundamentals Analyst` hérité restent utiles aux
actions et à certaines composantes d’indices, mais elles ne constituent pas un
fondamental valide pour EURUSD, l’or ou le pétrole.
