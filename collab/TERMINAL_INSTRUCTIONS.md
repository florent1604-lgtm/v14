# V14 Collab Terminal — Instructions pour les agents

## URL : http://127.0.0.1:8097

Le terminal de collaboration V14 est actif. Tous les messages du chat sont
routes vers le Hub MCP (port 8770) et le bus fichier.

## Pour chaque agent

### Claude (dans VSCode ou CLI)
Demande a Claude : "Lis les derniers messages du collab_hub avec collab_read et reponds dans le terminal V14"

### Codex (dans VSCode ou CLI)  
Demande a Codex : "Lis le collab_hub (collab_read after_offset=370) et reponds aux messages de Florent"

### Hermes
Hermes est un bridge MCP, il ne repond pas spontanement.
Pour qu'il reponde, un LLM doit le piloter via son MCP.

### Copilot
Copilot est une extension d'assistance inline, il n'a pas de canal de chat.

## Commande pour publier une reponse depuis n'importe quel agent

Via le Hub MCP (collab_publish) :
```
principal: "<agent_name>"
target: "florent"  
kind: "status"
content: "<votre reponse>"
idempotency_key: "<uuid>"
```

Via le terminal HTTP :
```
POST http://127.0.0.1:8097/api/chat
Content-Type: application/json
{"from": "<agent>", "to": "florent", "content": "<message>"}
```
