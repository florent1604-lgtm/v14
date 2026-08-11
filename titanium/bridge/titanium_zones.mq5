//+------------------------------------------------------------------+
//|  titanium_zones.mq5                                              |
//|  Trace sur le graphique MT5 les zones que Titanium V14 a REGARDÉES |
//|  pour décider — pas une reconstruction faite pour l'affichage.    |
//+------------------------------------------------------------------+
//
//  INSTALLATION
//    1. Copier ce fichier dans  <terminal>/MQL5/Indicators/
//    2. MetaEditor → F7 (compiler)
//    3. Glisser « titanium_zones » sur le graphique de l'instrument
//
//  V14 écrit  <terminal>/MQL5/Files/titanium_zones.json  à chaque barre.
//  Cet indicateur le relit et crée les objets correspondants.
//
//  DEUX GARANTIES
//    · Il ne touche QU'AUX objets préfixés TITANIUM_. Vos tracés manuels,
//      vos autres indicateurs et leurs objets ne sont jamais effacés.
//    · Il ne redessine que si le contenu du fichier a changé. Relire et
//      recréer des dizaines d'objets à chaque tick ferait ramer le terminal.
//
//  Il ne passe AUCUN ordre et ne lit aucun compte : affichage seul.
//+------------------------------------------------------------------+
#property copyright "Titanium V14"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

#define PREFIXE   "TITANIUM_"
#define FICHIER   "titanium_zones.json"
#define SCHEMA_OK 1

//: Marqueur de séparation entre blocs. DOIT rester identique à `MARQUEUR`
//: dans `titanium/bridge/mt5_zones.py`.
//:
//: Sans saut de ligne, délibérément : `LireFichier` lit en FILE_TXT, qui
//: RETIRE les fins de ligne. Un séparateur contenant un saut de ligne ne
//: serait jamais retrouvé après lecture ; `ExtraireBloc` rendrait alors le
//: fichier entier et chaque graphique dessinerait les zones du PREMIER
//: bloc. Défaut constaté le 07/08/2026 — dix fenêtres, aucun tracé juste.
#define MARQUEUR "===TITANIUM==="

input bool   AfficherZones     = true;
input bool   AfficherPlan      = true;   // entrée / SL / TP
input bool   AfficherVerdict   = true;   // bandeau texte
input int    OpaciteMax        = 55;     // 0-100, pour la zone la plus forte
input color  CouleurSR         = C'77,216,255';    // cyan   — structure
input color  CouleurOTE        = C'61,230,140';    // vert   — golden zone
input color  CouleurFVG        = C'255,92,114';    // rouge  — déséquilibre
input color  CouleurVPOC       = C'255,200,87';    // ambre  — juste prix
input color  CouleurEntree     = C'230,236,248';
input color  CouleurSL         = C'255,92,114';
input color  CouleurTP         = C'61,230,140';

string g_dernier = "";     // empreinte du dernier contenu tracé

//+------------------------------------------------------------------+
int OnInit()
{
   IndicatorSetString(INDICATOR_SHORTNAME, "Titanium V14 · zones");
   NettoyerNos(ChartID());
   g_dernier = "";
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   // On retire nos objets en partant : un indicateur qui laisse des traces
   // après suppression est une nuisance.
   NettoyerNos(ChartID());
   ChartRedraw(ChartID());
}

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total, const int prev_calculated,
                const datetime &time[], const double &open[],
                const double &high[], const double &low[],
                const double &close[], const long &tick_volume[],
                const long &volume[], const int &spread[])
{
   // ⚠️ `0` comme identifiant de graphique ne désigne PAS de façon fiable
   //    le graphique portant l'indicateur : les objets partaient tous sur un
   //    seul graphique, et les autres restaient vides. Constaté le
   //    07/08/2026 — NZDJPY (~95) et COFFEE (~350) rapportaient la même
   //    fenêtre de prix, celle d'USTECH. On adresse désormais le nôtre.
   long moi = ChartID();

   string js = LireFichier();
   if(js == "")            return(rates_total);
   if(js == g_dernier)     return(rates_total);   // rien de neuf : on ne touche à rien
   g_dernier = js;

   if(LireEntier(js, "schema") != SCHEMA_OK)
   {
      Print("[TITANIUM] schéma inconnu — indicateur trop ancien pour ce V14. ",
            "Recompiler la version fournie avec le bot.");
      return(rates_total);
   }

   // Le fichier porte UN BLOC PAR SYMBOLE, séparés par une ligne dédiée.
   // Chaque graphique extrait le sien. Avant cette découpe, le fichier ne
   // contenait qu'un symbole et un seul graphique dessinait : les autres
   // restaient vides par construction, quelle que soit leur configuration.
   int taille = StringLen(js);
   js = ExtraireBloc(js, _Symbol);
   NettoyerNos(moi);
   if(js == "")
   {
      // Trace explicite : sans elle, « rien ne s'affiche » est
      // indiscernable de « le symbole n'est pas suivi ». Une seule ligne
      // par changement de fichier, jamais par tick.
      Print("[TITANIUM] ", _Symbol, " absent du fichier (",
            taille, " octets lus) — hors univers, ou boucle arrêtée.");
      ChartRedraw(moi);
      return(rates_total);
   }
   Print("[TITANIUM] ", _Symbol, " : bloc de ", StringLen(js),
         " octets, verdict ", LireTexte(js, "verdict"));

   // Fenêtre de prix visible : une zone en dehors est créée mais invisible.
   // Sans ce repère, « aucun tracé » et « tracé hors champ » se ressemblent.
   double vue_bas = ChartGetDouble(moi, CHART_PRICE_MIN);
   double vue_haut = ChartGetDouble(moi, CHART_PRICE_MAX);

   datetime t1 = TempsDebut();
   datetime t2 = TempsFin();

   if(AfficherZones)  TracerZones(moi, js, t1, t2);
   if(AfficherPlan)   TracerPlan(moi, js, t1, t2);
   if(AfficherVerdict) TracerVerdict(moi, js);

   // Combien d'objets ont réellement été créés, et sont-ils dans le champ ?
   // C'est la seule façon de distinguer « rien n'est dessiné » de « tout est
   // dessiné hors de la fenêtre de prix affichée ».
   int nos = 0;
   for(int i = ObjectsTotal(moi, -1, -1) - 1; i >= 0; i--)
      if(StringFind(ObjectName(moi, i, -1, -1), PREFIXE) == 0)
         nos++;
   Print("[TITANIUM] ", _Symbol, " (chart ", moi, ") : ", nos,
         " objets créés · vue ",
         DoubleToString(vue_bas, _Digits), " → ",
         DoubleToString(vue_haut, _Digits));

   ChartRedraw(moi);
   return(rates_total);
}

//+------------------------------------------------------------------+
//| Ne supprime QUE nos objets — jamais ceux de l'utilisateur.        |
//+------------------------------------------------------------------+
void NettoyerNos(const long moi)
{
   for(int i = ObjectsTotal(moi, -1, -1) - 1; i >= 0; i--)
   {
      string nom = ObjectName(moi, i, -1, -1);
      if(StringFind(nom, PREFIXE) == 0)
         ObjectDelete(moi, nom);
   }
}

//+------------------------------------------------------------------+
//| Zones rectangulaires : S/R, FVG non comblées, OTE, VPOC.          |
//+------------------------------------------------------------------+
void TracerZones(const long moi, const string js, const datetime t1, const datetime t2)
{
   int pos = StringFind(js, "\"zones\"");
   if(pos < 0) return;

   int n = 0;
   int curseur = pos;
   while(true)
   {
      int deb = StringFind(js, "{", curseur);
      if(deb < 0) break;
      int fin = StringFind(js, "}", deb);
      if(fin < 0) break;
      string bloc = StringSubstr(js, deb, fin - deb + 1);
      curseur = fin + 1;

      string kind = LireTexte(bloc, "kind");
      if(kind == "") break;                    // sortie du tableau "zones"

      double bas   = LireReel(bloc, "bas");
      double haut  = LireReel(bloc, "haut");
      double force = LireReel(bloc, "force");
      if(bas <= 0 || haut <= 0) continue;

      color  c = CouleurSR;
      if(kind == "ote")  c = CouleurOTE;
      if(kind == "fvg")  c = CouleurFVG;
      if(kind == "vpoc") c = CouleurVPOC;

      string nom = PREFIXE + "Z" + IntegerToString(n++) + "_" + kind;
      if(!ObjectCreate(moi, nom, OBJ_RECTANGLE, 0, t1, bas, t2, haut)) continue;

      ObjectSetInteger(moi, nom, OBJPROP_COLOR, c);
      ObjectSetInteger(moi, nom, OBJPROP_FILL, true);
      ObjectSetInteger(moi, nom, OBJPROP_BACK, true);      // derrière les bougies
      ObjectSetInteger(moi, nom, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(moi, nom, OBJPROP_HIDDEN, true);
      ObjectSetString (0, nom, OBJPROP_TOOLTIP,
                       kind + " · " + LireTexte(bloc, "label"));
      // La force du niveau pilote l'épaisseur du bord : un niveau à 3 touches
      // doit se distinguer d'un niveau à 1 touche sans lire l'infobulle.
      ObjectSetInteger(moi, nom, OBJPROP_WIDTH, force > 0.66 ? 2 : 1);
   }
}

//+------------------------------------------------------------------+
//| Entrée / stop / objectif — le R:R devient visible.                |
//+------------------------------------------------------------------+
void TracerPlan(const long moi, const string js, const datetime t1, const datetime t2)
{
   int p = StringFind(js, "\"plan\"");
   if(p < 0) return;
   string bloc = StringSubstr(js, p, 260);

   double entree = LireReel(bloc, "entry");
   double sl     = LireReel(bloc, "sl");
   double tp     = LireReel(bloc, "tp");
   double rr     = LireReel(bloc, "rr");
   if(entree <= 0) return;

   LigneNiveau(moi, PREFIXE + "ENTREE", entree, CouleurEntree, STYLE_SOLID,
               "entrée " + DoubleToString(entree, _Digits));
   if(sl > 0)
      LigneNiveau(moi, PREFIXE + "SL", sl, CouleurSL, STYLE_DASH,
                  "stop " + DoubleToString(sl, _Digits));
   if(tp > 0)
      LigneNiveau(moi, PREFIXE + "TP", tp, CouleurTP, STYLE_DASH,
                  "objectif " + DoubleToString(tp, _Digits) +
                  (rr > 0 ? "  ·  R:R " + DoubleToString(rr, 2) : ""));

   // Rectangles risque / gain : la proportion se lit d'un coup d'œil.
   if(sl > 0) RectanglePlan(moi, PREFIXE + "RISQUE", t1, t2, entree, sl, CouleurSL);
   if(tp > 0) RectanglePlan(moi, PREFIXE + "GAIN",   t1, t2, entree, tp, CouleurTP);
}

void LigneNiveau(const long moi, const string nom, const double prix,
                 const color c, const int style, const string tip)
{
   if(!ObjectCreate(moi, nom, OBJ_HLINE, 0, 0, prix)) return;
   ObjectSetInteger(moi, nom, OBJPROP_COLOR, c);
   ObjectSetInteger(moi, nom, OBJPROP_STYLE, style);
   ObjectSetInteger(moi, nom, OBJPROP_WIDTH, 1);
   ObjectSetInteger(moi, nom, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(moi, nom, OBJPROP_HIDDEN, true);
   ObjectSetString (moi, nom, OBJPROP_TOOLTIP, tip);
}

void RectanglePlan(const long moi, const string nom, const datetime t1,
                   const datetime t2, const double a, const double b,
                   const color c)
{
   if(!ObjectCreate(moi, nom, OBJ_RECTANGLE, 0, t1, a, t2, b)) return;
   ObjectSetInteger(moi, nom, OBJPROP_COLOR, c);
   ObjectSetInteger(moi, nom, OBJPROP_FILL, true);
   ObjectSetInteger(moi, nom, OBJPROP_BACK, true);
   ObjectSetInteger(moi, nom, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(moi, nom, OBJPROP_HIDDEN, true);
}

//+------------------------------------------------------------------+
//| Bandeau : verdict, piliers, prix. En clair, en haut à gauche.     |
//+------------------------------------------------------------------+
void TracerVerdict(const long moi, const string js)
{
   string verdict = LireTexte(js, "verdict");
   string code    = LireTexte(js, "code");
   string piliers = LireTexte(js, "pillars_line");
   if(verdict == "") return;

   color c = C'147,160,190';
   if(verdict == "ENTER") c = CouleurOTE;
   if(verdict == "BLOCK") c = CouleurFVG;
   if(verdict == "WAIT")  c = CouleurVPOC;

   Etiquette(moi, PREFIXE + "VERDICT", 12, 22, 13, c,
             "TITANIUM " + verdict + "   " + piliers);
   Etiquette(moi, PREFIXE + "CODE", 12, 42, 9, C'147,160,190',
             code + "   D 1 2 3 4 5");

   // ── Panel d'indicateurs et taille retenue. Les lignes arrivent déjà
   //    mises en forme depuis Python : le parseur MQL5 sait extraire une
   //    chaîne, pas parcourir un objet aux clés inconnues d'avance.
   string indics = LireTexte(js, "indicators_line");
   if(indics != "")
      Etiquette(moi, PREFIXE + "INDICS", 12, 60, 9, C'125,140,164', indics);

   string risque = LireTexte(js, "risk_line");
   if(risque != "")
      Etiquette(moi, PREFIXE + "RISQUE", 12, 78, 9, CouleurOTE, risque);
}

void Etiquette(const long moi, const string nom, const int x, const int y,
               const int taille, const color c, const string texte)
{
   if(!ObjectCreate(moi, nom, OBJ_LABEL, 0, 0, 0)) return;
   ObjectSetInteger(moi, nom, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(moi, nom, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(moi, nom, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(moi, nom, OBJPROP_COLOR, c);
   ObjectSetInteger(moi, nom, OBJPROP_FONTSIZE, taille);
   ObjectSetInteger(moi, nom, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(moi, nom, OBJPROP_HIDDEN, true);
   ObjectSetString (0, nom, OBJPROP_FONT, "Consolas");
   ObjectSetString (0, nom, OBJPROP_TEXT, texte);
}

//+------------------------------------------------------------------+
//| Fenêtre temporelle de tracé : les 60 dernières barres, + marge.   |
//+------------------------------------------------------------------+
datetime TempsDebut()
{
   int n = MathMin(60, Bars(_Symbol, _Period) - 1);
   return(iTime(_Symbol, _Period, n));
}

datetime TempsFin()
{
   // On projette 10 barres vers la droite pour que les zones restent
   // lisibles au bord du graphique.
   return(iTime(_Symbol, _Period, 0) + 10 * PeriodSeconds(_Period));
}

//+------------------------------------------------------------------+
//| Extrait le bloc du symbole demandé.                              |
//|                                                                  |
//| Le fichier concatène un bloc JSON par symbole, séparés par une   |
//| ligne « ===TITANIUM=== ». Une ligne entière plutôt qu'un tableau |
//| JSON : ce parseur cherche des sous-chaînes et ne sait pas        |
//| descendre dans une structure imbriquée.                          |
//|                                                                  |
//| Rend "" si le symbole n'est pas dans le fichier — cas normal     |
//| pour un graphique ouvert à la main sur un actif hors univers.    |
//+------------------------------------------------------------------+
string ExtraireBloc(const string js, const string symbole)
{
   string marque = "\"symbol\": \"" + symbole + "\"";
   int p = StringFind(js, marque);
   if(p < 0)
   {
      // Tolérance au formatage : json.dumps peut omettre l'espace.
      marque = "\"symbol\":\"" + symbole + "\"";
      p = StringFind(js, marque);
      if(p < 0) return("");
   }

   // Début du bloc : le séparateur précédent, ou le début du fichier.
   int deb = 0;
   int s = StringFind(js, MARQUEUR);
   while(s >= 0 && s < p)
   {
      deb = s + StringLen(MARQUEUR);
      s = StringFind(js, MARQUEUR, s + 1);
   }

   // Fin du bloc : le séparateur suivant, ou la fin du fichier.
   int fin = StringFind(js, MARQUEUR, p);
   if(fin < 0) fin = StringLen(js);

   return(StringSubstr(js, deb, fin - deb));
}

//+------------------------------------------------------------------+
//| Lecture du fichier — le terminal le cherche dans MQL5/Files.      |
//+------------------------------------------------------------------+
string LireFichier()
{
   int h = FileOpen(FICHIER, FILE_READ | FILE_TXT | FILE_ANSI | FILE_SHARE_READ
                             | FILE_SHARE_WRITE);
   if(h == INVALID_HANDLE) return("");
   string contenu = "";
   while(!FileIsEnding(h)) contenu += FileReadString(h);
   FileClose(h);
   return(contenu);
}

//+------------------------------------------------------------------+
//| Extraction JSON minimale — pas de dépendance externe.             |
//| Volontairement naïve : le producteur est V14, le format est connu |
//| et versionné par "schema".                                        |
//+------------------------------------------------------------------+
string LireTexte(const string js, const string cle)
{
   int p = StringFind(js, "\"" + cle + "\"");
   if(p < 0) return("");
   int d = StringFind(js, ":", p);
   if(d < 0) return("");
   int g1 = StringFind(js, "\"", d);
   if(g1 < 0) return("");
   int g2 = StringFind(js, "\"", g1 + 1);
   if(g2 < 0) return("");
   return(StringSubstr(js, g1 + 1, g2 - g1 - 1));
}

double LireReel(const string js, const string cle)
{
   int p = StringFind(js, "\"" + cle + "\"");
   if(p < 0) return(0.0);
   int d = StringFind(js, ":", p);
   if(d < 0) return(0.0);
   string reste = StringSubstr(js, d + 1, 40);
   StringTrimLeft(reste);
   if(StringFind(reste, "null") == 0) return(0.0);
   return(StringToDouble(reste));
}

int LireEntier(const string js, const string cle)
{
   return((int)LireReel(js, cle));
}
//+------------------------------------------------------------------+
