# Plan de rearmement V14 — 10 septembre 2026

## Definition du resultat attendu

Le rearmement final concerne exclusivement le compte DEMO 10055401. Le mot
« rentable » signifie ici : esperance nette positive demontree sur une periode
hors echantillon, apres spread, commission, slippage et swap, avec un drawdown
compatible avec les limites de risque. Ce resultat ne garantit pas les gains
futurs.

## Ordre d'execution

1. **Mettre les nouvelles entrees en quarantaine persistante.**
   - Etat : termine.
   - V14 reste arme et observe le marche, mais aucune phase d'envoi n'est
     accessible.
   - La quarantaine est liee au compte et ne disparait pas lorsque les pertes
     sortent de la fenetre glissante de sept jours.

2. **Retablir un runtime unique et verifiable.**
   - Etat : termine.
   - Une seule boucle, un seul dashboard, un seul service analystes et un seul
     exemplaire de chaque collecteur.
   - Le compte, le serveur DEMO, le journal d'execution et l'absence d'intent
     non resolu sont controles avant toute admission.

3. **Conserver la production de preuves pendant le blocage.**
   - Etat : termine.
   - La phase mecanique continue a produire les observations shadow.
   - Hermes, le sizing et l'execution restent hors du chemin quand la
     quarantaine bloque les entrees.
   - La garde des pertes est relue une seconde fois juste avant l'executor.

4. **Figer le contrat live a reproduire.**
   - Etat : en cours.
   - Contrat actuel : entree marche, R:R 2,0, breakeven actif, trailing coupe,
     sorties adaptatives actives.
   - Le replay promu historique utilise un trailing et un hash de moteur
     different. Il ne peut plus autoriser des ordres.
   - Le nouveau replay doit enregistrer le hash du code, les parametres de
     sortie, le cout par symbole et les bornes temporelles.

5. **Construire l'univers candidat sans fuite d'information.**
   - Etat : candidats identifies, aucune autorisation acquise.
   - Exclure d'abord les instruments sans donnees fraiches, trop chers ou sans
     profondeur suffisante.
   - Utiliser une periode de calibration pour proposer les actifs.
   - Geler cette liste avant de lire la verification et le holdout final.
   - Les actifs elimines restent en observation et ne peuvent recevoir aucun
     ordre.

6. **Valider chaque actif et l'ensemble du portefeuille.**
   - La cle d'autorisation est `(symbole, classe, sens, timeframe, famille)`.
     Une validation du symbole seul est insuffisante.
   - OOS scelle du code courant : au moins 500 clotures par symbole et 100 par
     cle, esperance nette strictement positive, profit factor au moins 1,20,
     drawdown au plus 30 R et serie de pertes au plus 12.
   - Resultat encore positif avec couts stresses a 150 %.
   - Stabilite sur plusieurs segments temporels et absence de dependance a un
     seul trade ou a une seule journee.
   - Forward DEMO distinct : au moins 50 clotures par symbole et 30 par cle,
     couts exacts reconcilies, esperance nette positive, profit factor au moins
     1,10 et drawdown au plus 10 R.
   - Toute fenetre de sept jours avec au moins 10 clotures et un profit factor
     inferieur a 0,80 maintient le BLOCK.

7. **Reduire effectivement l'univers live.**
   - N'admettre que les symboles qui franchissent l'etape 6 sur des donnees
     non utilisees pour les choisir.
   - Recalculer les grappes de correlation et les budgets sur cette liste.
   - Mesurer le temps d'un tour complet et reduire `LOT_PAR_TOUR` si la liste
     restreinte permet une couverture plus rapide sans augmenter le risque.

8. **Qualifier Hermes sur le materiel cible.**
   - Contexte Ollama alloue : au moins 65 536 tokens, prouve par `ollama ps`.
   - Recuperation correcte d'informations au debut, au milieu et a la fin d'un
     contexte long.
   - Trois lots consecutifs de production, dont un lot de deux et un lot de
     huit, sans timeout et avec une politique fraiche acceptee.
   - Toute indisponibilite, lenteur excessive ou politique incoherente donne
     WAIT.

9. **Faire un burn-in DEMO en observation puis en risque minimal.**
   - Observation seule jusqu'a obtenir un echantillon frais reconciliable.
   - Acquittement humain de la quarantaine seulement apres les preuves des
     etapes 4 a 8.
   - Premiere cohorte DEMO bornee, sans ordre reel, avec arret automatique a la
     premiere violation d'un seuil.

10. **Promouvoir ou revenir en observation.**
    - Promotion uniquement si la cohorte fraiche confirme l'esperance nette,
      le profit factor, les couts et le drawdown annonces.
    - Dans tous les autres cas, V14 reste arme sous quarantaine et accumule des
      observations sans ordre.

## Diagnostic et univers restreint

Le journal exact du compte DEMO contient 791 clotures pour -97,93 R, avec un
profit factor de 0,753. Sur les sept derniers jours analyses, 98 clotures ont
produit -15,33 R, un profit factor de 0,619 et environ 21,41 R de drawdown.
Les shorts, le M15, les indices et le FX concentrent la plus grande partie de
la perte. Les six pertes consecutives du 9 septembre n'etaient pas marquees
countertrend : lever l'anti-fade ne suffit donc pas a expliquer ni corriger la
serie.

Les 147 anciens artefacts OOS sont intacts, mais tous sont perimes par rapport
au code courant sur sept fichiers moteur. Les clotures live n'ont par ailleurs
pas encore `exact_cost=true`. En consequence, aucune cle n'est actuellement
ALLOW.

Priorite pour un nouveau replay et un nouveau forward, sans statut de
promotion :

- `HK50`, `XAGUSD`, `BTCUSD`, `COFFEE.fs`, `FRA40`, `USOIL` ;
- tout le FX reste suspendu ;
- `US2000`, `NAS100.fs`, `NETH25`, `NK225.fs`, `COCOA.fs`, `XLMUSD`,
  `XAUEUR`, `UK100`, `JPN225`, `US30`, `UKOIL`, `DJ30.fs`, `ETHUSD`,
  `GER40`, `COPPER.fs` et `USTECH` restent exclus de l'execution ;
- les autres actifs restent en observation seule jusqu'a preuve fraiche.

Un BLOCK est immediat si le hash moteur ou source differe, si l'artefact n'est
pas scelle, si le cout exact manque, si l'alias ou les specifications sont
ambigus, si la classe est suspendue ou si l'echantillon est insuffisant. Aucun
reglage ne sera optimise sur la fenetre forward apres son ouverture.

Le replay diagnostique du code courant, aligne sur le trailing desactive, garde
un signal ancien favorable pour `USOIL` (130 trades, +0,275 R/trade, PF 1,807),
`XAGUSD` (144, +0,180 R/trade, PF 1,539) et `COFFEE.fs` (116, +0,174 R/trade,
PF 1,472). `BTCUSD` reste sous la porte PF 1,20; `HK50` et `FRA40` restent sous
les portes de moyenne et de PF. Cette mesure se termine le 19 aout et les six
actifs ont ete choisis en connaissance des anciens resultats : elle sert a
ordonner le travail, pas a promouvoir un actif.

## Qualification Hermes et contexte local

Ollama alloue effectivement 65 536 tokens a `qwen3.5:2b`, confirme par
`ollama ps`. Un test de 60 078 tokens a retrouve exactement les marqueurs du
debut, du milieu et de la fin. La lecture du prompt a toutefois pris 1 340,38 s
et l'appel complet 1 354,87 s sur le materiel cible. Le contexte 64k est donc
correct et fiable pour une analyse hors ligne, mais trop lent pour le TTL live
de 225 a 300 secondes.

Le chemin cortex reel de V14, sans outil et avec le role systeme Hermes, a rendu
`BLOCK` en 11,3 s face a une quarantaine persistante et a zero autorisation OOS.
Le role persistant est aligne dans `config/hermes_cortex_role.md`. Les prompts
live doivent rester compacts; un contexte 64k ne doit jamais etre rempli sur le
chemin d'entree.

## Cohorte d'execution DEMO autorisee le 10 septembre

L'operateur a explicitement demande la reprise de l'execution DEMO et l'ajout de
Bitcoin et Solana. La rotation du 11 septembre conserve `USOIL`, `XAGUSD`,
`COFFEE.fs`, `BTCUSD`, `SOL-USD` et `XAUUSD`, puis remplace `US30` et `SWI20`
par `HK50` et `FRA40`. `US30` reste exclu par le diagnostic; le contexte SWI20
courant est BLOCK. HK50 et FRA40 restent des candidats forward sans statut de
promotion. SOL-USD reste observe mais sa porte de cout peut le rendre non
portable. Cette cohorte sert a produire la nouvelle preuve forward; elle ne
transforme pas les resultats diagnostiques en preuve de rentabilite. Le mur
DEMO, Hermes, RiskGate, les couts, la microstructure, l'idempotence et les
limites d'exposition restent obligatoires.

La nouvelle fenetre de pertes commence le 10 septembre 2026 a 14:55 UTC. Le
journal historique reste intact et la quarantaine precedente est archivee. Les
seuils de -2 R sur le jour UTC et -6 R sur sept jours glissants s'appliquent a
toute cloture posterieure a ce debut de cohorte.

## Axiom Trade

L'integration Axiom est une phase ulterieure et independante. La documentation
publique actuelle decrit une application et un portefeuille non custodial,
mais ne publie pas de contrat officiel d'API de trading pour les robots. Aucun
SDK communautaire fonde sur les cookies, l'OTP ou le contournement Cloudflare
ne sera place sur le chemin d'execution.

Conditions minimales avant implementation : API officielle documentee,
authentification destinee aux machines, flux temps reel versionne, idempotence
des ordres, reconciliation des transactions, environnement testnet/paper et
autorisation explicite des automatismes. Sans ces elements, le verdict reste
incompatible pour l'execution V14. Une integration directe Solana peut etre
etudiee separement, sans usurper le nom ni le compte Axiom.

Verification documentaire du 10 septembre 2026 :

- index officiel : https://docs.axiom.trade/llms.txt ;
- inscription interactive et depot SOL :
  https://docs.axiom.trade/getting-started/signup.md ;
- portefeuille Solana et FAQ : https://docs.axiom.trade/faqs.md ;
- interface de marche : https://docs.axiom.trade/axiom/swap/market.md ;
- frais Axiom : https://docs.axiom.trade/getting-started/fees/axiom-fees.md ;
- frais Solana, slippage et protection MEV :
  https://docs.axiom.trade/getting-started/fees/solana-fees.md ;
- conditions d'utilisation : https://axiom.trade/terms.

La documentation officielle inventoriee ne contient ni reference d'API de
trading, ni SDK officiel, ni schema OpenAPI, ni contrat WebSocket, ni
authentification machine, ni limites de taux, ni environnement testnet. La
connexion d'execution V14 vers Axiom reste donc BLOCK. Une simulation locale
Solana en shadow peut etre construite sans portefeuille ni signature, puis
remplacee par un adaptateur officiel si Axiom publie ces contrats et autorise
explicitement l'automatisation.
