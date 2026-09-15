# Déployer Titanium V14 avec K3s sous Windows

K3s ne prend pas en charge les nœuds Windows natifs. Cette installation lance
K3s dans WSL2, garde MetaTrader 5 sur Windows et place entre eux un adaptateur
DEMO fermé par défaut. Référence : <https://docs.k3s.io/faq#does-k3s-support-windows>.

## 1. Borner WSL2

Créer `%UserProfile%\.wslconfig`, puis exécuter `wsl --shutdown` :

```ini
[wsl2]
memory=6GB
processors=4
swap=2GB
localhostForwarding=true
```

Ces limites protègent Windows. Les limites Kubernetes protègent ensuite chaque
conteneur. Sur une machine de moins de 12 Go de RAM, commencer à 4 Go pour WSL2
et réduire la limite du worker DeepSeek après mesure.

## 2. Installer K3s dans WSL2

Dans une distribution Linux WSL2 avec systemd activé :

```bash
curl -sfL https://get.k3s.io | sh -
sudo k3s kubectl get nodes
mkdir -p ~/.kube
sudo k3s kubectl config view --raw > ~/.kube/config
chmod 600 ~/.kube/config
```

Prérequis officiels : <https://docs.k3s.io/installation/requirements>.

## 3. Construire et importer l'image

Depuis la copie Linux du dépôt :

```bash
docker build -f deploy/k3s/Dockerfile -t titanium-v14:deepseek-k3s .
docker save titanium-v14:deepseek-k3s | sudo k3s ctr images import -
```

## 4. Créer les données sensibles hors Git

La clé déjà présente dans `.env` ne doit pas être copiée dans un manifeste. La
saisir silencieusement dans WSL2 :

```bash
kubectl create namespace titanium-v14 --dry-run=client -o yaml | kubectl apply -f -
read -rsp 'DEEPSEEK_API_KEY: ' DEEPSEEK_API_KEY; echo
kubectl -n titanium-v14 create secret generic titanium-deepseek \
  --from-literal=api-key="$DEEPSEEK_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
unset DEEPSEEK_API_KEY
```

Créer deux jetons indépendants pour le saut adaptateur puis le pont Windows :

```bash
read -rsp 'MT5_ADAPTER_TOKEN: ' MT5_ADAPTER_TOKEN; echo
read -rsp 'MT5_BRIDGE_TOKEN: ' MT5_BRIDGE_TOKEN; echo
kubectl -n titanium-v14 create secret generic titanium-mt5-bridge \
  --from-literal=adapter-token="$MT5_ADAPTER_TOKEN" \
  --from-literal=bridge-token="$MT5_BRIDGE_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
unset MT5_ADAPTER_TOKEN MT5_BRIDGE_TOKEN
```

## 5. Publier l'adresse changeante de Windows

WSL2 recrée souvent son réseau. Calculer l'adresse de l'hôte au déploiement :

```bash
WINDOWS_HOST_IP=$(ip route show default | awk '{print $3; exit}')
kubectl -n titanium-v14 create configmap titanium-runtime \
  --from-literal=mt5-bridge-url="http://${WINDOWS_HOST_IP}:8769" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Le pont natif doit écouter uniquement sur l'interface WSL privée, exiger
`MT5_BRIDGE_TOKEN`, refuser tout compte autre que DEMO et refaire le mur V14
avant chaque appel MT5. L'adaptateur Linux n'importe jamais `MetaTrader5`.

## 6. Appliquer et contrôler

Après le namespace et les objets hors Git créés aux étapes précédentes :

```bash
kubectl apply -f deploy/k3s/titanium-v14.yaml --server-side
kubectl -n titanium-v14 rollout status deployment/titanium-api
kubectl -n titanium-v14 rollout status deployment/titanium-deepseek-worker
kubectl -n titanium-v14 rollout status deployment/titanium-mt5-demo-adapter
kubectl -n titanium-v14 get pods,svc
kubectl -n titanium-v14 top pods
```

Pour afficher le tableau de bord sans l'exposer au réseau :

```bash
kubectl -n titanium-v14 port-forward service/titanium-api 8080:8080
```

Ouvrir `http://127.0.0.1:8080`. Le flux SSE est
`http://127.0.0.1:8080/stream` et la santé est
`http://127.0.0.1:8080/api/sante`.

## 7. Cache et diagnostic DeepSeek

DeepSeek active automatiquement son cache de contexte. V14 garde les
instructions stables au début des requêtes et journalise uniquement dans
`results/deepseek_usage.ndjson` : durée, jetons d'entrée/sortie, hits et misses.
La clé, le prompt et la réponse n'y figurent jamais.

```bash
kubectl -n titanium-v14 logs deployment/titanium-deepseek-worker --tail=100
kubectl -n titanium-v14 describe resourcequota titanium-v14-quota
```

Une panne DeepSeek, un secret absent ou un pont inaccessible produit WAIT ou
UNKNOWN. Cela ne contourne jamais RiskGate, le sizing, l'idempotence ou le mur
DEMO.

## 8. Revenir en arrière

```bash
kubectl delete namespace titanium-v14
```

Cette commande supprime les workloads K3s. Elle ne touche ni au terminal MT5
Windows, ni aux SL/TP déjà enregistrés chez le courtier DEMO.
