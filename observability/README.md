# Supervision Prometheus + Grafana de V14

V14 est une application **Python** locale sous Windows. Le moteur utilise le
terminal MT5 natif : il reste donc hors Docker. Le Compose lance uniquement
Prometheus et Grafana ; Prometheus rejoint l'exporteur V14 Windows via
`host.docker.internal:9108`.

## Démarrage

Prérequis : V14 actif, Docker Desktop lancé et le port `9108` autorisé par le
pare-feu Windows pour le réseau Docker.

```powershell
cd C:\Users\flore\Desktop\V14
docker compose -f observability\docker-compose.yml up -d
```

Adresses :

- métriques brutes : <http://127.0.0.1:9108/metrics>
- Prometheus : <http://127.0.0.1:9090/targets>
- Grafana : <http://127.0.0.1:3000>
- identifiants initiaux Grafana : `admin` / `admin`

Le datasource, le dashboard **V14 Bot — Temps réel** et les règles d'alerte
sont provisionnés automatiquement. Aucun import JSON manuel n'est nécessaire.

## Notifications Discord

Ne stocke pas le webhook dans Git ni dans `.env`. Définis-le seulement dans la
session qui lance Compose, puis recrée Grafana :

```powershell
$env:DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/ID/JETON"
docker compose -f observability\docker-compose.yml up -d --force-recreate grafana
```

Sans webhook réel, les règles existent mais les notifications Discord
échouent volontairement sur le placeholder. Slack ou email peuvent être
ajoutés dans `grafana/provisioning/alerting/contact-points.yml`.

## Contrôles

```powershell
Invoke-WebRequest http://127.0.0.1:9108/metrics -UseBasicParsing
docker compose -f observability\docker-compose.yml ps
docker compose -f observability\docker-compose.yml logs prometheus grafana
```

Dans Prometheus, `up{job="bot-v14"}` doit valoir `1`. L'alerte **No HTTP
Traffic** est informative : elle peut se déclencher si personne ne consulte
le dashboard, même lorsque la boucle de trading tourne. **Bot Down** repose sur
le scrape Prometheus et reste le signal de panne fiable.

## Arrêt et conservation

```powershell
docker compose -f observability\docker-compose.yml down
```

Les volumes conservent les séries et les réglages. Ajouter `-v` supprimerait
ces données ; ne l'utilise que pour une remise à zéro explicitement voulue.

## Installation sans Docker

L'exporteur V14 fonctionne déjà en bare metal. L'installation portable Windows
utilise `%LOCALAPPDATA%\V14-Observability` et ne touche pas au moteur MT5.

```powershell
powershell -ExecutionPolicy Bypass -File observability\windows\start-observability.ps1
powershell -ExecutionPolicy Bypass -File observability\windows\status-observability.ps1
```

Pour l'enregistrer au démarrage de la session Windows :

```powershell
powershell -ExecutionPolicy Bypass -File observability\windows\register-autostart.ps1
```

Pour arrêter uniquement la supervision, sans toucher à V14 ou MT5 :

```powershell
powershell -ExecutionPolicy Bypass -File observability\windows\stop-observability.ps1
```

La tâche planifiée se nomme `V14-Observability`. Les données Prometheus et
Grafana sont conservées dans `%LOCALAPPDATA%\V14-Observability\data`.
