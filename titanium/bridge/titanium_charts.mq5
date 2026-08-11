//+------------------------------------------------------------------+
//|                                            titanium_charts.mq5   |
//|   Ouvre et ferme les fenêtres de graphique pour le compte de V14. |
//+------------------------------------------------------------------+
//
//  POURQUOI CET EA EXISTE
//
//  L'API Python de MT5 ne sait pas ouvrir un graphique : `ChartOpen`,
//  `ChartClose` et `ChartIndicatorAdd` n'existent que côté MQL5. V14 ne
//  peut donc pas montrer ses tracés lui-même — il lui faut un relais qui
//  vit dans le terminal.
//
//  Le protocole est volontairement rustique : V14 écrit la liste des
//  symboles qu'il veut voir, cet EA fait converger l'état des fenêtres
//  vers cette liste. Un fichier, un sens de lecture, aucun dialogue.
//
//  IL N'ENVOIE AUCUN ORDRE. Un EA d'affichage qui pourrait trader serait
//  un second chemin d'exécution, invisible depuis Python — un test côté
//  Python interdit d'ailleurs `OrderSend` dans ce fichier.
//
#property copyright "Titanium V14"
#property version   "1.00"
#property strict

input string InpFichier   = "titanium_charts.txt"; // liste des symboles voulus
input string InpIndicateur = "titanium_zones";     // indicateur à poser
input ENUM_TIMEFRAMES InpPeriode = PERIOD_M15;     // période des fenêtres
input int    InpMaxFenetres = 8;                   // plafond dur
input int    InpIntervalleMs = 3000;               // relecture du fichier

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetMillisecondTimer(InpIntervalleMs);
   Converger();
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

void OnTimer()
  {
   Converger();
  }

//+------------------------------------------------------------------+
//| Lit la liste voulue. Une ligne = un symbole.                     |
//+------------------------------------------------------------------+
bool LireVoulus(string &voulus[])
  {
   ArrayResize(voulus, 0);

   int h = FileOpen(InpFichier, FILE_READ | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return false;

   while(!FileIsEnding(h))
     {
      string s = FileReadString(h);
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "" || StringGetCharacter(s, 0) == '#')
         continue;
      if(ArraySize(voulus) >= InpMaxFenetres)
         break;                       // le plafond prime sur le fichier
      int n = ArraySize(voulus);
      ArrayResize(voulus, n + 1);
      voulus[n] = s;
     }
   FileClose(h);
   return true;
  }

//+------------------------------------------------------------------+
bool Contient(const string &tab[], const string valeur)
  {
   for(int i = 0; i < ArraySize(tab); i++)
      if(tab[i] == valeur)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
//| Fait converger les fenêtres ouvertes vers la liste voulue.       |
//+------------------------------------------------------------------+
void Converger()
  {
   string voulus[];
   if(!LireVoulus(voulus))
      return;                          // pas de fichier : on ne touche à rien

   // ── 1. Fermer ce qui n'est plus demandé.
   //    On ne ferme QUE des graphiques portant notre indicateur : fermer
   //    une fenêtre ouverte à la main par l'utilisateur serait une prise
   //    de contrôle qu'il n'a pas demandée.
   long id = ChartFirst();
   while(id >= 0)
     {
      long suivant = ChartNext(id);          // avant toute fermeture
      if(id != ChartID())                    // jamais notre propre hôte
        {
         string sym = ChartSymbol(id);
         if(!Contient(voulus, sym) && ANous(id))
            ChartClose(id);
        }
      id = suivant;
     }

   // ── 2. Ouvrir ce qui manque.
   for(int i = 0; i < ArraySize(voulus); i++)
     {
      if(DejaOuvert(voulus[i]))
         continue;

      // Sans sélection dans l'observateur, `ChartOpen` échoue en silence.
      if(!SymbolInfoInteger(voulus[i], SYMBOL_SELECT))
         if(!SymbolSelect(voulus[i], true))
            continue;

      long nouveau = ChartOpen(voulus[i], InpPeriode);
      if(nouveau == 0)
         continue;

      int h = iCustom(voulus[i], InpPeriode, InpIndicateur);
      if(h != INVALID_HANDLE)
         ChartIndicatorAdd(nouveau, 0, h);

      ChartSetInteger(nouveau, CHART_SHOW_GRID, false);
      ChartSetInteger(nouveau, CHART_MODE, CHART_CANDLES);
     }
  }

//+------------------------------------------------------------------+
//| Ce graphique porte-t-il notre indicateur ?                       |
//+------------------------------------------------------------------+
bool ANous(const long id)
  {
   int total = ChartIndicatorsTotal(id, 0);
   for(int i = 0; i < total; i++)
      if(StringFind(ChartIndicatorName(id, 0, i), InpIndicateur) >= 0)
         return true;
   return false;
  }

//+------------------------------------------------------------------+
bool DejaOuvert(const string symbole)
  {
   long id = ChartFirst();
   while(id >= 0)
     {
      if(ChartSymbol(id) == symbole)
         return true;
      id = ChartNext(id);
     }
   return false;
  }
//+------------------------------------------------------------------+
