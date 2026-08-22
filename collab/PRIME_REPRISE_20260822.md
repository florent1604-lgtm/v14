# Reprise Prime — 22/08/2026

Note ecrite par Claude a la demande de Florent, apres la mort de ton kernel.
Elle existe comme **fichier** et non comme message de hub : un fichier se lit
sans reseau, et c'est justement le reseau qui te manquait.

Le hub, lui, **fonctionne** — HTTP 200, 568 messages, deux consommateurs. Ce
n'est pas lui qui etait inaccessible, c'est ton kernel qui ne pouvait plus rien
appeler.

---

## 1. ⚠️ URGENT — une regression detruit les artefacts

La v2 du rejeu, celle qui ecrit `results/rejeu_univers_brut/` avec le manifeste
scelle, **rend zero trade** et **ecrase les resultats valides**.

```
rejeux a zero trade : 6
   AAVE-USD, ADAUSD, AUDCAD, AUDCHF, AUDJPY, AUDNZD
rejeu_univers_brut  : 6 dossiers, 0 avec des trades
rejeux exploitables : 142  (etaient 148)
```

Detail sur AAVE-USD, ecrit a 08:10:38 :

```
barres_evaluees   99 751
n_enter                0
erreurs                0
calibration        n = 0
global             n = 0
trades.ndjson      0 octet
```

Le moteur lit 99 751 barres, ne declenche jamais, et **ne signale aucune
erreur**. C'est le pire cas : il rend un resultat qui a la forme d'un resultat
valide et qui est vide. Rien dans le fichier ne dit qu'il ne faut pas s'en
servir.

AAVE-USD avait avant cette passe une calibration a n=3 773 et un global a
n=5 806. Ces chiffres sont perdus.

**Deux processus de rejeu tournent encore.** Au rythme d'un lot, la v2 va
detruire les 142 restants un par un.

Codex l'a vu de son cote — son dernier message au hub (#568) dit
« Rejeu historique actif: 115/149 au dernier controle, **raw=0** » — mais rien
n'a ete arrete.

Le format v2 est bon : manifeste SHA-256, version de schema, empreinte du
moteur. C'est exactement ce qui manquait. C'est son declenchement qui est
casse.

---

## 2. Ce que Codex a publie, et que tu n'as pas pu lire

Source : hub offsets 559 a 568. Resume fidele, pas une reformulation.

**Porte stricte OOS — 13 candidats retenus** sur calibration>0, verification>0,
n>=60, PF>1, IC95 naif>0, BH-FDR<5 %, derive <=0,10R :

```
USTECH · UKOIL · BTCUSD · ETHUSD · FRA40 · BTC-JPY · NAS100.fs
COFFEE.fs · BRENT.fs · BNB-USD · US500 · S&P.fs · DJ30.fs
```

Ce ne sont **pas 13 edges independants mais environ 5 blocs** : indices US,
petrole, crypto, indices UE, et COFFEE en liste de surveillance. FX : aucune
preuve robuste, sign-flips, NO-GO. Metaux : NO-GO.

**Execution reelle, cycle de vie des limites** — 525 intentions, 268 fills,
51,0 % [IC95 46,8 ; 55,3], aucun symbole au-dela de 21 intentions :

- `limit_passive` candidat fort : BTCUSD 80 % de fill +5,50R ; BRENT 80 %
  +3,21R ; USOIL 81,8 % +4,48R ; WTI 69,2 % +2,93R ; DJ30 100 %.
  USTECH remplit a 100 % mais n'economise que 0,016R pour -0,79R d'alpha.
- `adaptive` / `cancel-replace` en SHADOW : BTC-JPY 50 % +3,07R ; COFFEE 60 %
  +3,10R ; AUDUSD 43,8 % +3,74R ; XAGUSD 50 % +1,79R.
- passif inadapte : ETHUSD 10 % de fill malgre un edge rejoue fort ; CN50
  5,9 % ; AAVE 9,1 % ; EURSGD 11,1 % ; COPPER 14,3 %.

Par classe : energie 81,3 % de fill, metaux 66,7 %, indices 63,3 %, FX 39,2 %,
crypto 30,6 %.

**Correlations** : aucune avance-retard exploitable a M15, meilleur lag = 0,
gain maximal +0,009 soit du bruit, sur 870 couples et 3 folds. Les correlations
servent au **risque et a la deduplication**, une exposition par grappe. 30 alias
restent au-dela de |r| > 0,90 en IS et OUT.

**Verdict Codex** : GO instrumentation et PAPER/SHADOW sur les 5 blocs.
NO-GO reel, NO-GO lead-lag, NO-GO politique definitive par symbole avant A/B
paire.

**Ses commits** : `1547468` plafond de risque correle, `c49a7c7` qualite et
trades bruts, `8d68d16` gate post-sizing, `dbfdeb2` snapshot transitif,
`2ec6152` puis `78f78ae` validateur A/B SHADOW. Hermes a donne GO rechargement
PAPER/DEMO, NO-GO reel.

---

## 3. Mon apport, et une correction que je me fais

**Le classement par actif est un classement de couts.** Spearman entre esperance
et cout normalise en ATR : **−0,867** sur 148 symboles.

```
cout median des 38 survivants  : 0,1534 ATR
cout median des 110 autres     : 0,4286 ATR
```

Les huit actifs les moins chers en ATR sont gagnants huit fois sur huit :
GER40, XAUUSD, USTECH, US30, BTCUSD, GBPJPY, XAUAUD, EURJPY.

**Correction** : mon premier test donnait −0,399 sur le spread **brut**. Il
etait faux. Le spread brut mesure l'echelle de prix et non le cout — 0,03 sur
UKOIL a 70 contre 0,00006 sur EURUSD a 1,08. Rapporte a l'ATR, la relation
passe de −0,399 a −0,867.

**Correlations, mesure independante** (`tools/analyse_rejeu_univers.py`,
commit `17865c7`) : 38 survivants au double critere se reduisent a
**13 paris independants**, par composantes connexes et non paire a paire —
si A~B et B~C, les trois portent le meme pari. HK50/HSI a 0,996,
USDJPC/USDJPY a 0,995, S&P/US500 a 0,989, cinq indices US en une grappe.

Consequence : `MAX_RISQUE_CUMULE_PCT` compte des **positions**, pas des
**paris**. Le gate correle de Codex (`1547468`) attaque le meme probleme ; vos
deux mesures devraient etre confrontees.

---

## 4. Ce que je te propose, sans l'avoir fait

Je n'ai touche a rien. Trois choses dans cet ordre :

1. **Arreter les deux rejeux qui tournent**, avant que la v2 n'ecrase les 142
   restants. Les 6 deja perdus sont recuperables en les rejouant, mais
   seulement si le declenchement est repare d'abord.
2. **Trouver pourquoi `n_enter = 0`** — quorum, timeframe, ou la borne de barres
   utiles appliquee trop largement. Un rejeu qui rend zero sans erreur devrait
   au minimum echouer bruyamment : c'est un garde-fou a ajouter, pas seulement
   un bug a corriger.
3. **Puis relancer la v2** sur les 6, avec les trades bruts que Codex attend
   pour debloquer l'A/B — il l'a marque BLOCKED faute de
   `decision_at`/`quantity`/`asset_class`.

Etat des services au moment ou j'ecris : les cinq tournent, la boucle est armee,
rien n'a ete redemarre en dehors de toi.

---

# ADDENDUM — 22/08 15:35 : ce que Codex a commence et n'a pas pu finir

Ajout apres une nouvelle panne : ton worker de session etait en ECHEC, pas
inactif. Le client affichait `Cannot list heartbeats while session worker is
failed` et attendait depuis **3 h 32**, avec un prompt accepte qu'il ne pouvait
pas traiter. Les redemarrages precedents ne fermaient que le client, donc le
worker casse survivait. Cette fois l'arbre complet a ete arrete — clients,
demons, kernels — et le preflight a mis en quarantaine le worker `34a132ba9c8e`
et son bail. Zero verrou actif au redemarrage.

## La chaine bloquee, et c'est toi qui la debloques

Codex a livre le validateur A/B SHADOW et l'a durci :

```
8d68d16  gate risque correle post-sizing sur budget.effective_pct
dbfdeb2  snapshot moteur transitif, barre future, schema reconstruit fail-closed
2ec6152  auditeur des prerequis A/B execution shadow
78f78ae  durcissement : quotes scellees en streaming, chronologie, trade_id
         unique, side +/-1, quantite positive, zero intention refuse
```

Hermes a valide : GO rechargement PAPER/DEMO, NO-GO reel. 2052 tests passes.

**Mais l'A/B reste BLOCKED**, et Codex le dit explicitement : il manque les
artefacts bruts portant `decision_at`, `quantity`, `asset_class`, plus les
donnees de file et d'agresseur. Son dernier message au hub (#568) note
« Rejeu historique actif: 115/149 au dernier controle, **raw=0** ».

Autrement dit : **son auditeur est pret et ne peut rien auditer, parce que le
rejeu ne produit aucun trade brut.** La regression `n_enter = 0` n'est pas un
defaut isole, c'est ce qui tient toute la chaine A/B a l'arret.

## Ton propre travail non commite vise deja ca

L'arbre porte, non commite :

```
titanium/backtest.py              modifie
tools/rejeu_univers.py            modifie
tools/rejeu_progression.py        modifie
tools/audit_rejeu_artefacts.py    nouveau
tests/test_backtest_causality.py  nouveau
tests/test_audit_rejeu_artefacts.py nouveau
tests/test_rejeu_univers_raw.py   modifie
```

C'est le lot que ta session precedente avait ouvert pour corriger `raw=0`. Il
n'est ni fini ni commite, et il a survecu a la panne. **Verifie s'il corrige
effectivement le declenchement avant de repartir de zero.**

## Etat des artefacts, mesure a l'instant

```
rejeux a zero trade : 6 / 148   AAVE-USD ADAUSD AUDCAD AUDCHF AUDJPY AUDNZD
rejeu_univers_brut  : 6 dossiers, 0 avec des trades
aucun processus de rejeu ne tourne
```

La destruction s'est arretee a 6. Les 142 autres rejeux sont intacts.

## L'ordre que je propose, sans l'avoir fait

1. Lire ton diff non commite et decider s'il corrige `n_enter = 0`.
2. Ajouter le garde-fou qui manque : un rejeu qui evalue 99 751 barres et rend
   zero entree sans erreur doit **echouer bruyamment**. Aujourd'hui il ecrit un
   artefact qui a la forme d'un resultat valide et qui est vide — rien dans le
   fichier ne previent qu'il ne faut pas s'en servir.
3. Rejouer les 6 detruits, avec les trades bruts que l'auditeur de Codex attend.
4. Rendre la main a Codex pour l'A/B.

Rien n'a ete touche de mon cote : ni code, ni parametre, ni service.
