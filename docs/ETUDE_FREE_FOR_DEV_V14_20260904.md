# Free-for.dev : sélection utile pour Titanium V14

**Date :** 4 septembre 2026  
**Périmètre :** services gratuits potentiellement utiles à V14  
**Invariant :** PAPER/DEMO ONLY ; aucun fournisseur gratuit ne devient une autorité d'exécution

## Verdict

[free-for.dev](https://free-for.dev/) est un excellent catalogue de découverte,
pas un contrat de disponibilité. Les quotas peuvent changer : chaque service
doit être revérifié sur sa page officielle avant intégration. V14 doit conserver
MT5, ses artefacts scellés et sa mémoire locale comme sources de vérité.

## Priorité immédiate

| Besoin V14 | Service gratuit repéré | Apport | Décision |
|---|---|---|---|
| Métriques et alertes hors PC | [Grafana Cloud](https://grafana.com/products/cloud/) | Prometheus, Loki, dashboards et alertes | **Pilote P0** : miroir des métriques, sans donnée sensible |
| Surveillance de la boucle | [Healthchecks.io](https://healthchecks.io/) | Jusqu'à 20 heartbeats selon le catalogue | **P0** : ping de vie toutes les 10 min |
| Erreurs Python | [Bugsink](https://www.bugsink.com/) ou [Sentry](https://sentry.io/) | Traces et regroupement d'exceptions | **P1** : redaction obligatoire des comptes/tickets |
| Sauvegarde des artefacts | [Cloudflare R2](https://developers.cloudflare.com/r2/) ou [Backblaze B2](https://www.backblaze.com/cloud-storage) | Stockage objet avec palier gratuit | **P1** : manifestes + archives chiffrées |
| CI Windows/Python | [GitHub Actions](https://docs.github.com/actions) | Tests à chaque commit/PR | **P0** : aucun accès à MT5 ni aux secrets live |
| Accès distant au dashboard | [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) | Tunnel sortant et Zero Trust | **P2** : jamais de publication directe du port 8095 |

## Données fondamentales et marché

| Source | Palier annoncé dans free-for.dev | Usage acceptable dans V14 |
|---|---:|---|
| [AlphaAI](https://alphai.io/developers) | 20 req/min, 100 req/jour | Nouvelles financières par ticker et score d'impact, d'abord en SHADOW |
| [Earnings Feed](https://earningsfeed.com/api) | 15 req/min | SEC, insiders et institutions ; utile aux actions/indices uniquement |
| [Financial Data](https://financialdata.net/) | 300 req/jour | Confirmation lente, jamais prix d'exécution |
| [Market Data](https://www.marketdata.app/) | 100 req/jour | Échantillonnage actions/options ; insuffisant pour 49 actifs en live |
| [CurrencyScoop](https://currencyscoop.com/) | 5 000 appels/mois | Taux FX de référence et contrôle de cohérence |
| [CoinMarketCap](https://coinmarketcap.com/api/) | 10 000 crédits/mois | Redondance crypto face à CoinGecko |
| [News API](https://newsapi.org/) | 100 req/jour, délai annoncé de 24 h | Recherche/rejeu seulement, trop tardif pour l'entrée live |
| [finlight](https://finlight.me/) | 5 000 req/mois, nouvelles retardées de 12 h | Backfill et mesure d'impact, pas décision instantanée |
| [Brave Search API](https://brave.com/search/api/) / [Tavily](https://tavily.com/) | Petits crédits gratuits | Recherche des analystes, hors boucle de trading |

Chaque adaptateur retenu doit produire une observation scellée avec
`provider_timestamp`, `ingested_at`, `ttl`, `source`, empreinte SHA-256 et état
`UNKNOWN` distinct de `NEUTRAL`. Une source retardée ne peut jamais devenir
« fraîche » par simple ingestion récente.

## Infrastructure éventuellement utile

- **Oracle Cloud Always Free** : bon candidat pour un témoin externe, une page
  d'état ou une sauvegarde ; mauvais candidat pour MT5, et les VM inactives
  peuvent être récupérées.
- **Google Cloud Run/Pub/Sub** ou **AWS Lambda/SNS/SQS** : adaptés aux alertes et
  traitements asynchrones, mais ajoutent de la complexité inutile au moteur local.
- **InfluxDB Cloud** : pertinent si la volumétrie Prometheus devient trop forte.
- **Turso/Upstash** : utiles à un futur hub distant, jamais en remplacement de la
  mémoire SQLite/WAL causale locale.
- **UptimeRobot/Better Stack** : alternatives simples pour surveiller un endpoint
  exposé de façon authentifiée ; Healthchecks est mieux adapté à la boucle locale.

## Services à écarter du chemin critique

- notebooks gratuits, PaaS qui s'endorment et VM récupérables ;
- scraping non contractuel et relais webhook publics pour les données sensibles ;
- API de nouvelles retardées présentées comme signaux live ;
- LLM gratuits avec quotas variables dans la décision d'entrée ;
- stockage cloud non chiffré des journaux MT5, tickets ou identifiants de compte.

## Plan proposé

1. **P0** — CI GitHub, heartbeat Healthchecks et miroir Grafana Cloud.
2. **P1 SHADOW** — AlphaAI + redondance CoinMarketCap/CoinGecko, avec mesure de
   fraîcheur, disponibilité et corrélation aux mouvements M15/M30.
3. **P1** — sauvegarde quotidienne chiffrée des manifestes sur R2 ou B2.
4. Après 30 jours, ne promouvoir qu'une source ayant une disponibilité mesurée,
   une horloge fiable et un gain hors échantillon démontré. Même promue, elle
   reste une observation : RiskGate et le mur DEMO demeurent souverains.
