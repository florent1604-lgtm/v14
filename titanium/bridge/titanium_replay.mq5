//+------------------------------------------------------------------+
//|                                            titanium_replay.mq5   |
//|   Rejoue les décisions de Titanium V14 dans le testeur MT5.      |
//+------------------------------------------------------------------+
//
//  CE ROBOT NE DÉCIDE RIEN.
//
//  Il lit un fichier de signaux produit par V14 (`titanium/backtest.py`)
//  et se contente de les exécuter sur ticks réels. C'est délibéré : une
//  seconde implémentation de la chaîne de décision divergerait de la
//  première, et le testeur optimiserait alors une stratégie que V14 ne
//  joue pas. V12 est mort de cette faute — 845 lignes divergentes entre
//  son moteur Python et son EA.
//
//  Ce qui est optimisé ici, ce sont donc UNIQUEMENT les paramètres de
//  GESTION : multiple de stop, R:R visé, breakeven, trailing, stop
//  temporel. Le point d'entrée et le sens viennent du fichier et ne
//  sont jamais remis en cause.
//
//  Limite à garder en tête : l'optimiseur ne peut rien dire sur la
//  qualité des entrées elles-mêmes. Il répond à « comment mieux gérer
//  ces entrées », pas à « faut-il les prendre ».
//
#property copyright "Titanium V14"
#property version   "1.00"
#property strict

// Le testeur recopie ce fichier dans le bac à sable de chaque agent.
// Le nom doit être un littéral de compilation : un seul fichier porte
// donc TOUS les symboles, et l'EA filtre sur `_Symbol`.
#property tester_file "titanium_signals.csv"

#include <Trade\Trade.mqh>

//--- Risque ---------------------------------------------------------
input double InpRiskPct      = 1.0;   // Risque par trade (% du solde)

//--- Stop : le point que Florent veut vérifier ----------------------
input double InpSLmult       = 1.0;   // Multiple de la distance de stop V14
input double InpRR           = 2.0;   // Objectif, en R (0 = pas de TP)

//--- Gestion en cours de route --------------------------------------
input double InpBEatR        = 0.0;   // Breakeven à +N R (0 = jamais)
input double InpTrailStartR  = 0.0;   // Départ du trailing, en R (0 = jamais)
input double InpTrailDistR   = 1.0;   // Distance du trailing, en R

//--- Stop temporel ---------------------------------------------------
input int    InpStopBars     = 0;     // Sortie après N barres (0 = jamais)
input double InpStopMinR     = 0.0;   // ...si le trade n'a pas atteint +N R

//--- Garde-fou statistique -------------------------------------------
input int    InpMinTrades    = 30;    // En deçà, la passe est déclarée nulle

//+------------------------------------------------------------------+
struct Signal
  {
   datetime          bar;        // clôture de la barre de décision
   int               side;       // +1 long, -1 short
   double            entry;      // prix de référence V14
   double            r_unit;     // distance entrée↔stop, en prix
  };

Signal   g_sig[];
int      g_next   = 0;           // curseur : les signaux sont triés
CTrade   g_trade;

datetime g_last_bar    = 0;
datetime g_pos_open    = 0;      // barre d'ouverture de la position courante
double   g_pos_r       = 0.0;    // 1 R en prix, figé à l'entrée
double   g_pos_peak_r  = 0.0;    // meilleure excursion atteinte
bool     g_pos_be      = false;  // breakeven déjà posé

//+------------------------------------------------------------------+
//| Lecture du fichier de signaux.                                   |
//| Format : symbol;epoch;side;entry;sl;tp;r_unit                    |
//+------------------------------------------------------------------+
bool ChargerSignaux()
  {
   int h = FileOpen("titanium_signals.csv",
                    FILE_READ | FILE_CSV | FILE_ANSI, ';');
   if(h == INVALID_HANDLE)
     {
      PrintFormat("titanium_replay: fichier de signaux introuvable (%d)",
                  GetLastError());
      return false;
     }

   ArrayResize(g_sig, 0);
   int lignes = 0;

   while(!FileIsEnding(h))
     {
      string sym = FileReadString(h);
      if(FileIsLineEnding(h) && sym == "")
         continue;

      // En-tête ou ligne vide : on saute proprement.
      if(sym == "symbol" || sym == "")
        {
         while(!FileIsLineEnding(h) && !FileIsEnding(h))
            FileReadString(h);
         continue;
        }

      long   epoch  = (long)  StringToInteger(FileReadString(h));
      int    side   = (int)   StringToInteger(FileReadString(h));
      double entry  =         StringToDouble(FileReadString(h));
      /* sl  */               FileReadString(h);
      /* tp  */               FileReadString(h);
      double r_unit =         StringToDouble(FileReadString(h));

      // Le reste de la ligne (contexte, piliers, famille) ne sert pas
      // à l'exécution ; il est conservé côté Python pour l'analyse.
      while(!FileIsLineEnding(h) && !FileIsEnding(h))
         FileReadString(h);

      lignes++;

      if(sym != _Symbol)      continue;
      if(side != 1 && side != -1) continue;
      if(r_unit <= 0.0)       continue;

      int n = ArraySize(g_sig);
      ArrayResize(g_sig, n + 1);
      g_sig[n].bar    = (datetime) epoch;
      g_sig[n].side   = side;
      g_sig[n].entry  = entry;
      g_sig[n].r_unit = r_unit;
     }

   FileClose(h);
   PrintFormat("titanium_replay: %d signaux pour %s (%d lignes lues)",
               ArraySize(g_sig), _Symbol, lignes);
   return ArraySize(g_sig) > 0;
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   g_trade.SetExpertMagicNumber(14000);
   g_trade.SetTypeFillingBySymbol(_Symbol);
   g_trade.SetAsyncMode(false);

   if(!ChargerSignaux())
      return INIT_FAILED;

   g_next     = 0;
   g_last_bar = 0;
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Lot à partir du risque et de la distance de stop.                |
//+------------------------------------------------------------------+
double CalculerLot(double dist_prix)
  {
   if(dist_prix <= 0.0)
      return 0.0;

   double tick_val = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_VALUE);
   double tick_sz  = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(tick_val <= 0.0 || tick_sz <= 0.0)
      return 0.0;

   double risque = AccountInfoDouble(ACCOUNT_BALANCE) * InpRiskPct / 100.0;
   double perte_lot = (dist_prix / tick_sz) * tick_val;   // perte pour 1 lot
   if(perte_lot <= 0.0)
      return 0.0;

   double lot  = risque / perte_lot;
   double pas  = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double mini = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxi = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);

   if(pas > 0.0)
      lot = MathFloor(lot / pas) * pas;    // on arrondit VERS LE BAS

   // Sous le lot minimum, on ne "force" pas : prendre le minimum ferait
   // porter au trade un risque supérieur à la consigne, silencieusement.
   if(lot < mini)
      return 0.0;
   if(lot > maxi)
      lot = maxi;

   return NormalizeDouble(lot, 2);
  }

//+------------------------------------------------------------------+
bool PositionOuverte()
  {
   return PositionSelect(_Symbol)
          && PositionGetInteger(POSITION_MAGIC) == 14000;
  }

//+------------------------------------------------------------------+
void Ouvrir(const Signal &s)
  {
   double dist = s.r_unit * InpSLmult;
   if(dist <= 0.0)
      return;

   double lot = CalculerLot(dist);
   if(lot <= 0.0)
      return;

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int    dig = (int) SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   double prix = (s.side > 0) ? ask : bid;
   double sl   = (s.side > 0) ? prix - dist : prix + dist;
   double tp   = 0.0;
   if(InpRR > 0.0)
      tp = (s.side > 0) ? prix + dist * InpRR : prix - dist * InpRR;

   sl = NormalizeDouble(sl, dig);
   tp = NormalizeDouble(tp, dig);

   bool ok = (s.side > 0)
             ? g_trade.Buy(lot, _Symbol, 0.0, sl, tp, "titanium")
             : g_trade.Sell(lot, _Symbol, 0.0, sl, tp, "titanium");

   if(ok)
     {
      g_pos_open   = iTime(_Symbol, PERIOD_CURRENT, 0);
      g_pos_r      = dist;
      g_pos_peak_r = 0.0;
      g_pos_be     = false;
     }
  }

//+------------------------------------------------------------------+
//| Gestion : breakeven, trailing, stop temporel.                    |
//+------------------------------------------------------------------+
void Gerer()
  {
   if(!PositionOuverte() || g_pos_r <= 0.0)
      return;

   long   type  = PositionGetInteger(POSITION_TYPE);
   int    side  = (type == POSITION_TYPE_BUY) ? 1 : -1;
   double open  = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl    = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   double cur   = (side > 0)
                  ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                  : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   int    dig   = (int) SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);

   double fav_r = side * (cur - open) / g_pos_r;
   if(fav_r > g_pos_peak_r)
      g_pos_peak_r = fav_r;

   // ── Stop temporel. Testé AVANT de toucher au stop : si le trade doit
   //    sortir sur le temps, déplacer son stop d'abord n'a pas de sens.
   if(InpStopBars > 0 && g_pos_open > 0)
     {
      int barres = iBarShift(_Symbol, PERIOD_CURRENT, g_pos_open, false);
      if(barres >= InpStopBars && fav_r < InpStopMinR)
        {
         g_trade.PositionClose(_Symbol);
         return;
        }
     }

   double neuf = sl;

   // ── Breakeven, une seule fois.
   if(!g_pos_be && InpBEatR > 0.0 && fav_r >= InpBEatR)
     {
      neuf = open;
      g_pos_be = true;
     }

   // ── Trailing, à partir du seuil.
   if(InpTrailStartR > 0.0 && fav_r >= InpTrailStartR)
     {
      double t = (side > 0)
                 ? cur - g_pos_r * InpTrailDistR
                 : cur + g_pos_r * InpTrailDistR;
      // Le stop ne recule jamais.
      if((side > 0 && t > neuf) || (side < 0 && t < neuf))
         neuf = t;
     }

   neuf = NormalizeDouble(neuf, dig);
   if(MathAbs(neuf - sl) > SymbolInfoDouble(_Symbol, SYMBOL_POINT) / 2.0)
     {
      // Un stop qui reculerait est refusé net.
      if((side > 0 && neuf > sl) || (side < 0 && neuf < sl) || sl == 0.0)
         g_trade.PositionModify(_Symbol, neuf, tp);
     }
  }

//+------------------------------------------------------------------+
void OnTick()
  {
   Gerer();

   datetime bar = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(bar == g_last_bar)
      return;
   g_last_bar = bar;

   if(PositionOuverte())
      return;

   // V14 décide à la CLÔTURE d'une barre et entre à l'ouverture de la
   // suivante. On cherche donc un signal daté de la barre précédente.
   datetime precedente = iTime(_Symbol, PERIOD_CURRENT, 1);

   // Les signaux sont triés : on avance le curseur sans jamais revenir.
   while(g_next < ArraySize(g_sig) && g_sig[g_next].bar < precedente)
      g_next++;

   if(g_next < ArraySize(g_sig) && g_sig[g_next].bar == precedente)
     {
      Ouvrir(g_sig[g_next]);
      g_next++;
     }
  }

//+------------------------------------------------------------------+
//| Critère d'optimisation.                                          |
//|                                                                  |
//| Renvoie l'espérance en R, et NON le profit : maximiser le profit |
//| pousse l'optimiseur vers les passes à gros lots, pas vers les    |
//| bons réglages. Une passe sous le seuil de trades est annulée —   |
//| sans quoi l'optimiseur exhibe un « PF 8.0 » tiré de 3 trades.    |
//+------------------------------------------------------------------+
double OnTester()
  {
   int n = (int) TesterStatistics(STAT_TRADES);
   if(n < InpMinTrades)
      return 0.0;

   double gross_p = TesterStatistics(STAT_GROSS_PROFIT);
   double gross_l = MathAbs(TesterStatistics(STAT_GROSS_LOSS));
   if(gross_l <= 0.0)
      return 0.0;      // aucune perte sur l'échantillon : non exploitable

   double perte_moy = gross_l / MathMax(1.0, TesterStatistics(STAT_LOSS_TRADES));
   if(perte_moy <= 0.0)
      return 0.0;

   // Espérance nette exprimée en R, R étant la perte moyenne observée.
   return (gross_p - gross_l) / n / perte_moy;
  }
//+------------------------------------------------------------------+
