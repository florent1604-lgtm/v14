# Strategie reelle observee — lecture seule

## Perimetre et limites

Analyse hors ligne des journaux MT5 locaux du compte reel masque `6026…1188`.
Aucune connexion au compte, aucun ordre et aucune modification de position n'ont ete
effectues. Les journaux utiles couvrent surtout les 1–9 mai 2026 et le 4 aout 2026 ;
ils ne constituent donc pas un historique comptable exhaustif.

## Geste discretionnaire observe en mai

- Actif dominant : `BTCUSD`.
- Entrees fractionnees : plusieurs tickets sont ouverts dans le meme sens en quelques
  secondes ou minutes, au lieu d'une position monolithique.
- Taille adaptee par tranche : exemples `0.20`, `0.10`, `0.09`, `0.08` lot dans une
  meme sequence vendeuse.
- Gestion en panier : quatre positions vendeuses sont fermees simultanement le 2 mai ;
  cinq positions acheteuses sont fermees en moins de 35 secondes le 8 mai.
- Retournement rapide : apres liquidation du panier acheteur le 8 mai, trois ventes
  sont ouvertes dans les 2 minutes suivantes.
- Execution opportuniste : marche majoritaire, avec quelques essais `limit`/`stop`
  annules rapidement quand le prix ne vient pas. Sur l'echantillon visible : 15
  ouvertures au marche, 10 fermetures explicites, 3 limites, 1 stop et 4 annulations.

Cette signature correspond a une these par actif, geree au niveau du panier : entrer
par paliers, renforcer si le prix ou la conviction s'ameliore, sortir ensemble quand
la these s'epuise, puis accepter un retournement sans attendre un TP statique.

## Regime automatise observe le 4 aout

Les 16 sequences multi-actifs du 4 aout ont une autre signature : ordre au marche,
SL et TP poses immediatement, ratio cible souvent proche de 2,5 R, puis remontee
mecanique du SL sur certains gagnants. Resultat reconstitue hors frais : 5 gagnantes,
11 perdantes, taux de gain 31,2 %, moyenne -0,47 R, mediane -1,00 R. Ce regime est
donc separe du geste discretionnaire et ne doit pas servir seul de modele.

## Contraintes a transmettre a V14

1. Maintenir une **these de panier par actif**, pas seulement une decision par ticket.
2. Autoriser plusieurs positions uniquement si la nouvelle entree ameliore le prix,
   la conviction ou la couverture du panier, avec un risque total plafonne.
3. Calculer en continu l'etat `CONFIANT / PRUDENT / PEUR / INVALIDEE` a partir du
   marche, des artefacts, du fondamental et de la memoire du panier.
4. Fermer ou reduire activement le panier lorsque la these passe durablement a
   `PEUR` ou `INVALIDEE`; ne pas attendre mecaniquement TP/SL.
5. Choisir l'execution selon spread et urgence : limite passive si le temps le permet,
   annulation/remplacement bref, puis marche si la these risque de partir sans fill.
6. Conserver le SL courtier comme protection ultime; une intelligence locale ne doit
   jamais le supprimer ni le desserrer.

## Donnees encore necessaires

Pour valider statistiquement ce profil, il faut un export MT5 `Historique du compte`
en CSV/HTML ou une seconde instance MT5 connectee avec un mot de passe investisseur.
Cela permettra de mesurer PnL net, duree, MFE/MAE, renforcements et sorties partielles
sur une periode plus longue sans toucher au terminal DEMO de V14.
