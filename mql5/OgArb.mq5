//+------------------------------------------------------------------+
//| OgArb.mq5 â€” EA open-path FX : wrapper venue sur OgCore.mqh        |
//| Detect-only par defaut (InpAllowLive=false).                      |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.20"

#include <Trade\Trade.mqh>
#include "OgCore.mqh"

input group "=== Univers ==="
input string InpSymbols      = "";            // Paires (vide = 28 Annexe J.2), virgule
input string InpSymPrefix    = "";            // Prefixe broker (ex. "pro.")
input string InpSymSuffix    = "";            // Suffixe broker (ex. ".r", "m")
input int    InpMaxLegs      = 3;             // Jambes FX max par chemin (1..3)

input group "=== Economie (Convention A) ==="
input double InpNotionalUSD  = 10000.0;       // Notionnel USD par panier
input double InpCommPer100K  = 3.5;           // Commission $/lot/cote (IC Raw 3.5)
input double InpCommScale    = 1.10;          // Multiplicateur commission
input double InpDddMin       = 0.27;          // Seuil detection net ($)

input group "=== Gates ==="
input long   InpFreshMs      = 8000;          // Fraicheur quote max (ms)
input long   InpSkewMs       = 300;           // Skew max entre jambes panier (ms, 0=off)
input double InpSpreadCap    = 1.76;          // Cap spread x mediane
input int    InpMedWindow    = 256;           // Fenetre mediane spread (ticks, 16..256)
input int    InpWarmupTicks  = 100;           // Ticks/sym avant evaluation
input int    InpDeadSec      = 90;            // Sym sans tick apres N s = "dead" (exclu)

input group "=== Cadence ==="
input int    InpEvalMs       = 500;           // Intervalle evaluation (ms)
input int    InpHbSec        = 60;            // Heartbeat (s)
input int    InpSessFromUTC  = -1;            // Session debut (h UTC, -1 = tout)
input int    InpSessToUTC    = -1;            // Session fin (h UTC)

input group "=== Execution ==="
input bool   InpAllowLive    = false;         // AUTORISE les ordres reels
input bool   InpAllowHedgedAcct = false;      // tolere un compte HEDGING en live
input int    InpMode         = 1;             // 0=detect 1=panier 2=scalp jambe disloquee
input double InpTauSlipBp    = 0.69;          // Slippage IOC tolere (bp)
input double InpQMin         = 0.5;           // Fraction fill min acceptee
input long   InpMinSeqGapMs  = 2000;          // Gap min entre sequences
input int    InpHoldSec      = 45;            // Maintien panier puis unwind (s)
input int    InpMinHoldMs    = 1500;          // Latence min avant exits convergence (ms)
input bool   InpConvExit     = true;          // Exits par re-evaluation du chemin
input double InpDivStop      = 1.5;           // Panier: stop divergence net ($)
input double InpUnwindFloor  = 0.5;           // Panier: exit floor net < -x ($)
input double InpTrailBack    = 0.3;           // Panier: trailing depuis creux ($)
input double InpScalpDdd     = 1.0;           // Scalp: ddd min ($/10k)
input double InpScalpStop    = 2.0;           // Scalp: stop perte flottante ($)
input double InpScalpNetMin  = 0.05;          // Scalp: marge nette cible au-dessus de la commission A/R ($)
input int    InpStalePxMs    = 1000;          // Scalp: quote dormante >= seuil (ms sans changement de prix)
input int    InpBreakerFails = 5;             // Breaker apres N echecs
input long   InpMagic        = 20261112;      // Magic number

input group "=== Journal ==="
input string InpLogFile      = "ogarb_journal.jsonl";  // Fichier journal
input bool   InpLogTicks     = false;                  // Log chaque eval

// Annexe J.2
string FX_DEF[] = {
   "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
   "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY",
   "GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD",
   "AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"
};

COgEngine eng;

// mediane spread par symbole (maintenue par l'EA)
double sprBuf[OG_MAX_SYMS][256];
int    sprLen[OG_MAX_SYMS];
int    sprPos[OG_MAX_SYMS];
double medArr[OG_MAX_SYMS];
int    symSeen[OG_MAX_SYMS];
bool   symDead[OG_MAX_SYMS];   // symbole sans flux (exclu du gate warmup)
long   pxTick[OG_MAX_SYMS];    // time_msc du dernier changement de PRIX (pas re-tick)
int    medWin = 256;

// stats
long   gEvals=0, gDetects=0, gTicks=0;
double gMaxNet=-1e18;
int    gMaxPath=-1;
datetime gLastHb=0;
datetime gStartSec=0;
long   gLastSeq=0;
int    gSkipPi=-1;         // pi exclu (SCALPSKIP) -> argmax essaie le suivant
long   gSkipUntil=0;
int    gFails=0;
int    gLogH=INVALID_HANDLE;
int    gWarmMin=1;         // InpWarmupTicks clampe >=1
int    gBreakMin=1;        // InpBreakerFails clampe >=1
double gSc=1.0;            // seuils $ x min(eq*2.5,notional)/10k
// horloge flux : "maintenant" = dernier time_msc + ecoulement local depuis
// (corrige le bug tMs=TimeCurrent()*1000 tronque a la seconde qui excluait
//  toute quote intra-seconde comme "future")
long   gFeedMs=0;
ulong  gFeedTick=0;
CTrade gTrade;

// panier vivant : en HEDGING chaque jambe est une position a clore par ticket.
// 1 panier max a la fois -> exposition bornee, audit lineaire.
#define OG_MAX_BASKET_LEGS 8
struct OgBasket
{
   ulong  tkt[OG_MAX_BASKET_LEGS];
   ulong  allTkt[OG_MAX_BASKET_LEGS]; // tous les tickets du cycle (P&L complet)
   int    n;
   int    allN;
   int    pi;
   long   openFeedMs;   // horloge feed (sim-safe en tester) pour held
   int    closeFails;   // retries PositionClose echoues -> borne anti-spam
   bool   open;
   bool   scalp;      // 1 jambe (snipe lead-lag) vs panier complet
   bool   full;       // panier complet rempli (filled==need)
   double ein;        // ddd a l'entree
   double minNet;     // creux net observe (trailing)
   int    badEvals;   // streak evalPath==false
   int    closedN;    // jambes fermees cumulees (multi-ticks)
   bool   seenOpen;   // au moins un ticket vu ouvert -> "gone" autorise
   int    rescanN;    // ticks ecoules a chercher la position (tkt=0)
   string legSym;     // symbole de la jambe scalp (rescan ticket)
   ulong  legDeal;    // deal d'entree du scalp (rescan ticket)
   int    legE;       // index sym jambe tenue (-1=aucune)
   int    legFrom;    // devise from (sens de la jambe)
   double relIn;      // rel au fill = baseline edge (0=inconnu)
   string path;
};
OgBasket gB;

// ATTENTION : les globales MQL5 PERSISTENT entre les re-init (F7,
// changement timeframe). Tout l'etat doit etre remis a zero ici,
// sinon les symboles s'empilent : 28 -> 56 -> cap 64 -> chemins tronques.
void resetState()
{
   eng.reset();
   for(int i=0;i<OG_MAX_SYMS;i++)
   {
      sprLen[i]=0; sprPos[i]=0; medArr[i]=0;
      symSeen[i]=0; symDead[i]=false; pxTick[i]=0;
      for(int j=0;j<256;j++) sprBuf[i][j]=0;
   }
   medWin=256;
   gEvals=0; gDetects=0; gTicks=0;
   gMaxNet=-1e18; gMaxPath=-1;
   gLastHb=0; gStartSec=TimeGMT();
   gLastSeq=0; gFails=0;
   gFeedMs=0; gFeedTick=0;
   gB.open=false; gB.n=0; gB.allN=0; gB.pi=-1; gB.path="";
   gB.scalp=false; gB.full=false; gB.ein=0; gB.minNet=0; gB.badEvals=0;
   gB.closeFails=0; gB.openFeedMs=0; gB.closedN=0;
   gB.seenOpen=false; gB.rescanN=0; gB.legSym=""; gB.legDeal=0;
   gB.legE=-1; gB.legFrom=-1; gB.relIn=0;
}

//+------------------------------------------------------------------+
// echappe les chaines injectees dans le JSON (err broker, symboles)
string jsonEsc(const string s)
{
   string r=s;
   StringReplace(r,"\\","\\\\");
   StringReplace(r,"\"","\\\"");
   StringReplace(r,"\n","\\n");
   StringReplace(r,"\r","\\r");
   StringReplace(r,"\t","\\t");
   return r;
}

// ts = ms depuis demarrage programme (monotone, pas wall-clock)
// wts = epoch secondes GMT : ancre absolue pour corrÃ©lation forensique
void jlog(const string type,const string msg)
{
   string line=StringFormat("{\"type\":\"%s\",\"ts\":%I64d,\"wts\":%I64d,%s}",
                type,(long)(GetMicrosecondCount()/1000),(long)TimeGMT(),msg);
   if(gLogH!=INVALID_HANDLE){ FileWriteString(gLogH,line+"\n"); FileFlush(gLogH); }
   Print(line);
}

// dump bid/ask des symboles utilises par un chemin (entree+jambes+sortie)
// -> preuve rejouable dans chaque OPP
string pathQuotes(int pi)
{
   string q=""; int used[]; int nu=0;
   if(eng.pS[pi]!=0 && eng.usdSym[eng.pS[pi]]>=0){ ArrayResize(used,nu+1); used[nu++]=eng.usdSym[eng.pS[pi]]; }
   for(int li=0;li<eng.pL[pi];li++){ ArrayResize(used,nu+1); used[nu++]=eng.pE[pi][li]; }
   if(eng.pEnd[pi]!=0 && eng.usdSym[eng.pEnd[pi]]>=0){ ArrayResize(used,nu+1); used[nu++]=eng.usdSym[eng.pEnd[pi]]; }
   for(int k=0;k<nu;k++)
   {
      int i=used[k];
      q+=StringFormat("%s%s:%.5f/%.5f",k>0?"|":"",eng.symName[i],eng.bidP[i],eng.askP[i]);
   }
   return q;
}

double medianOf(int si)
{
   int n=sprLen[si];
   if(n<=0) return 0;
   double tmp[];
   ArrayResize(tmp,n);                      // ne trier QUE les echantillons ecrits
   for(int i=0;i<n;i++) tmp[i]=sprBuf[si][i];
   ArraySort(tmp);
   return (n%2==1)?tmp[n/2]:0.5*(tmp[n/2-1]+tmp[n/2]);
}

void poll()
{
   MqlTick tk;
   for(int i=0;i<eng.nSym;i++)
   {
      if(!SymbolInfoTick(eng.symName[i],tk)){ eng.bidP[i]=0; eng.askP[i]=0; continue; }
      double nb=tk.bid,na=tk.ask;
      if(na<nb||nb<=0) continue;
      // NB : symSeen compte TOUT tick valide (warmup = flux vivant), pas les
      // changements de prix â€” un book fige-mais-vivant doit quand meme
      // atteindre InpWarmupTicks, sinon deadlock "warmed<nSym" eternel.
      symSeen[i]++;
      if(nb!=eng.bidP[i]||na!=eng.askP[i])
      {
         pxTick[i]=tk.time_msc;   // prix REELLEMENT change (dormance = tMs-pxTick)
         double sp=na-nb;
         sprBuf[i][sprPos[i]]=sp;
         sprPos[i]=(sprPos[i]+1)%medWin;      // borne : pas de debordement int
         if(sprLen[i]<medWin) sprLen[i]++;
      }
      eng.bidP[i]=nb; eng.askP[i]=na;
      eng.lastT[i]=tk.time_msc;
   }
}

//+------------------------------------------------------------------+
// tkt (out) : ticket de la position ouverte par le deal d'entree â€” rempli
// meme sur partial fill <QMin pour ne pas perdre le residuel.
bool sendLeg(int e,int from,double qtyFrom,double &qtyTo,string &err,ulong &tkt)
{
   tkt=0;
   int a=eng.symA[e],b=eng.symB[e];
   bool sellBase=(from==a);
   string sym=eng.symName[e];
   //--- gardes symbole
   long tmode=SymbolInfoInteger(sym,SYMBOL_TRADE_MODE);
   // gardes "ENV:" : refus environnemental -> ne doit pas alimenter le breaker
   if(tmode==SYMBOL_TRADE_MODE_DISABLED||tmode==SYMBOL_TRADE_MODE_CLOSEONLY)
   { err=StringFormat("ENV:%s trade_mode=%ld",sym,tmode); return false; }
   if(!sellBase&&eng.askP[e]<=0){ err="ENV:"+sym+" ask<=0"; return false; }
   //--- volume demande -> lots (1 lot = 100k base)
   double lots=sellBase? qtyFrom/100000.0 : (qtyFrom/eng.askP[e])/100000.0;
   double vmin=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double vstep=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   double vmax=SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   lots=MathFloor(lots/vstep)*vstep;
   if(lots<vmin-1e-9){ err=StringFormat("ENV:%s lots %.4f<vmin %.2f",sym,lots,vmin); return false; } // pas de sur-livraison
   lots=MathMin(vmax,lots);
   lots=NormalizeDouble(lots,2);
   //--- slippage calibre par symbole : tau bp -> points (px-dependant)
   double pt=SymbolInfoDouble(sym,SYMBOL_POINT);
   double px=sellBase?eng.bidP[e]:eng.askP[e];
   long devPts=(pt>0&&px>0)?(long)MathRound(InpTauSlipBp*1e-4*px/pt):10;
   gTrade.SetDeviationInPoints(devPts);
   //--- filling policy lue depuis le symbole (IOC prefere, FOK sinon, RETURN repli)
   long fm=SymbolInfoInteger(sym,SYMBOL_FILLING_MODE);
   if((fm&2)!=0)      gTrade.SetTypeFilling(ORDER_FILLING_IOC);
   else if((fm&1)!=0) gTrade.SetTypeFilling(ORDER_FILLING_FOK);
   else               gTrade.SetTypeFilling(ORDER_FILLING_RETURN);
   bool sent=sellBase
      ? gTrade.Sell(lots,sym,0.0,0.0,0.0,"ogLeg")
      : gTrade.Buy (lots,sym,0.0,0.0,0.0,"ogLeg");
   uint rc=gTrade.ResultRetcode();
   if(!sent||(rc!=TRADE_RETCODE_DONE&&rc!=TRADE_RETCODE_DONE_PARTIAL))
   {
      err=StringFormat("%s %s %.2f : %s",sellBase?"SELL":"BUY",
           sym,lots,gTrade.ResultRetcodeDescription());
      return false;
   }
   //--- quantite EXECUTEE (jamais la demandee) : partial fill gere
   double execLots=gTrade.ResultVolume();
   if(execLots<=0) execLots=lots;                  // repli si broker ne rapporte pas
   //--- ticket exact : deal d'entree -> DEAL_POSITION_ID (plus fiable que
   //    le scan sym+magic qui peut adopter un orphelin du meme symbole)
   tkt=findLegPos(sym,gTrade.ResultDeal());
   if(tkt==0) jlog("EVT","\"msg\":\"sendLeg tkt=0 : position non retrouvee\"");
   if(execLots<lots*InpQMin-1e-9)
   {
      err=StringFormat("%s %s partial %.4f/%.4f <QMin",sellBase?"SELL":"BUY",sym,execLots,lots);
      return false;                               // residuel journalise via err
   }
   double fpx=gTrade.ResultPrice();
   // sell base -> quote = base*px ; buy base <- quote : base obtenue = execLots*100k
   qtyTo=sellBase? execLots*100000.0*fpx : execLots*100000.0;
   // dormMs = age depuis dernier CHANGEMENT de prix, qAgeMs = age du dernier
   // tick recu (audit 16/11 : la dormance n'etait loggee que sur le scalp)
   long tNow=gFeedMs+(long)(GetTickCount64()-gFeedTick);
   jlog("FILL",StringFormat("\"sym\":\"%s\",\"side\":\"%s\",\"lots\":%.4f,\"px\":%.5f,\"qtyTo\":%.2f,"
        "\"dormMs\":%I64d,\"qAgeMs\":%I64d",
        jsonEsc(sym),sellBase?"SELL":"BUY",execLots,fpx,qtyTo,
        tNow-pxTick[e],tNow-eng.lastT[e]));
   err="";
   return true;
}

// routage echec de jambe : ENV = refus environnemental (symbole ferme,
// quote nulle, lots<vmin) -> skipExec SANS breaker ; le reste = faute de
// jambe reelle -> abortLeg + breaker.
void legFail(int pi,const string leg,const string err)
{
   if(StringFind(err,"ENV:",0)==0) skipExec(pi,leg,err);
   else abortLeg(pi,leg,err);
}

void abortLeg(int pi,const string leg,const string err)
{
   onFail();
   jlog("ABORT",StringFormat("\"pi\":%d,\"leg\":\"%s\",\"err\":\"%s\",\"p\":\"%s\"",
        pi,leg,jsonEsc(err),jsonEsc(eng.pathStr(pi))));
}

// refus environnemental AVANT envoi d'ordre : pas une faute de jambe,
// ne doit pas alimenter le breaker (AutoTrading off, marge, bornes)
void skipExec(int pi,const string leg,const string err)
{
   jlog("EXECSKIP",StringFormat("\"pi\":%d,\"leg\":\"%s\",\"err\":\"%s\",\"p\":\"%s\"",
        pi,leg,jsonEsc(err),jsonEsc(eng.pathStr(pi))));
}

// ticket de la position ouverte par une jambe : d'abord le lien exact
// deal d'entree -> DEAL_POSITION_ID ; repli : scan symbole+magic en
// excluant les tickets deja stockes (entry/exit peuvent partager un symbole)
// POSITION_TICKET peut changer au swap re-open ; IDENTIFIER jamais.
// gB.tkt[] stocke des identifiants (DEAL_POSITION_ID) -> ticket courant ici.
ulong ticketOf(ulong pid)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong t=PositionGetTicket(i);
      if(t!=0&&(ulong)PositionGetInteger(POSITION_IDENTIFIER)==pid) return t;
   }
   return 0;
}

ulong findLegPos(const string sym,ulong deal=0)
{
   if(deal>0 && HistoryDealSelect(deal))
   {
      long pid=HistoryDealGetInteger(deal,DEAL_POSITION_ID);
      if(pid>0) return (ulong)pid;
   }
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong t=PositionGetTicket(i);
      if(t==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=sym) continue;
      if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
      ulong pid=(ulong)PositionGetInteger(POSITION_IDENTIFIER);
      for(int k=0;k<gB.n;k++) if(gB.tkt[k]==pid){ pid=0; break; }
      if(pid!=0) return pid;
   }
   return 0;
}

// enregistre le panier (meme partiel) pour que l'unwind le clore
void registerBasket(int pi,double ein,bool scalp,bool full,long tMs)
{
   gB.pi=pi; gB.openFeedMs=tMs;
   gB.path=eng.pathStr(pi); gB.open=true;
   gB.scalp=scalp; gB.full=full; gB.ein=ein; gB.minNet=0; gB.badEvals=0;
   gB.closeFails=0; gB.closedN=0;
   gB.seenOpen=false; gB.rescanN=0;   // leg* remplis au fill, reset a la close
   // allTkt : liste complete des tickets du panier pour le P&L â€” conserve
   // les jambes deja fermees (gB.tkt ne garde que les tickets a retry).
   for(int k=0;k<gB.n;k++) gB.allTkt[k]=gB.tkt[k];
   gB.allN=gB.n;
}

// P&L realise du panier : deals dont DEAL_POSITION_ID est un ticket de
// jambe (in+out, commissions et swap inclus). Le broker peut remettre
// DEAL_MAGIC a zero -> on matche sur le lien exact position<->deals.
void basketPL(double &pl,double &comm,double &swap,int &nd)
{
   pl=0; comm=0; swap=0; nd=0;
   // NB: les deals sont horodates en HEURE SERVEUR (EET=GMT+2/3), pas GMT.
   // Fenetre large en temps serveur ; le filtre position_id borne deja aux jambes.
   if(!HistorySelect(0,TimeTradeServer()+60)) return;
   for(int i=0;i<HistoryDealsTotal();i++)
   {
      ulong d=HistoryDealGetTicket(i);
      if(d==0) continue;
      long pid=HistoryDealGetInteger(d,DEAL_POSITION_ID);
      bool ours=false;
      for(int k=0;k<gB.allN;k++)
         if(gB.allTkt[k]!=0&&(ulong)pid==gB.allTkt[k]){ ours=true; break; }
      if(!ours) continue;
      pl  +=HistoryDealGetDouble(d,DEAL_PROFIT);
      comm+=HistoryDealGetDouble(d,DEAL_COMMISSION);
      swap+=HistoryDealGetDouble(d,DEAL_SWAP);
      nd++;
   }
}

// unwind : clot chaque jambe par ticket puis journalise le P&L realise.
// En hedging PositionClose emet l'ordre inverse ; en netting idem.
void closeBasket(const string why)
{
   if(!gB.open) return;
   int closed=0,remain=0;
   ulong keep[OG_MAX_BASKET_LEGS];
   // throttle : apres 40 tentatives (~20s) retry ~1/60s pour ne pas flooder
   // le broker sur un rejet structurel (marche ferme, OFF_QUOTES, etc.)
   gB.closeFails++;
   bool attempt=(gB.closeFails<40 || gB.closeFails%120==0);
   for(int k=0;k<gB.n;k++)
   {
      ulong t=gB.tkt[k];
      if(t==0) continue;
      ulong ct=ticketOf(t); if(ct==0) continue;     // deja ferme (SL/TP/main)
      if(attempt&&gTrade.PositionClose(ct)) closed++;
      else
      {
         if(attempt)
            jlog("EVT",StringFormat("\"msg\":\"close fail tkt=%I64u : %s\"",
                 t,jsonEsc(gTrade.ResultRetcodeDescription())));
         keep[remain++]=t;                          // retry au prochain tick
      }
   }
   gB.closedN+=closed;
   if(remain>0)
   {
      if(gB.closeFails==40)
         jlog("EVT",StringFormat("\"msg\":\"close degrade : %d jambe(s) resistantes, retry ~60s\"",remain));
      for(int k=0;k<remain;k++) gB.tkt[k]=keep[k];
      gB.n=remain;                                  // panier reste ouvert
      return;
   }
   double pl,comm,swap; int nd;
   basketPL(pl,comm,swap,nd);
   jlog("CLOSE",StringFormat("\"pi\":%d,\"legs\":%d,\"allN\":%d,\"deals\":%d,\"pl\":%.2f,"
        "\"comm\":%.2f,\"swap\":%.2f,\"net\":%.2f,\"why\":\"%s\",\"p\":\"%s\"",
        gB.pi,gB.closedN,gB.allN,nd,pl,comm,swap,pl+comm+swap,why,jsonEsc(gB.path)));
   gB.open=false; gB.n=0; gB.allN=0; gB.pi=-1; gB.path="";
   gB.scalp=false; gB.full=false; gB.ein=0; gB.minNet=0; gB.badEvals=0;
   gB.closeFails=0; gB.openFeedMs=0; gB.closedN=0;
   gB.seenOpen=false; gB.rescanN=0; gB.legSym=""; gB.legDeal=0;
   gB.legE=-1; gB.legFrom=-1; gB.relIn=0;
}

bool execBasket(int pi,long tMs,double ein)
{
   if(!InpAllowLive||gB.open||InpMode!=1) return false;
   //--- permissions live verifiees a l'execution (init lit avant la connexion)
   if(!MQLInfoInteger(MQL_TESTER))
   {
      long tta=TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
      long ate=AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
      if(!tta||!ate)
      { skipExec(pi,"precheck",StringFormat("tta=%ld expert=%ld",tta,ate)); return false; }
   }
   //--- pre-check marge : ~3 jambes x notional/leverage + coussin
   double free=AccountInfoDouble(ACCOUNT_MARGIN_FREE);
   long   lev =AccountInfoInteger(ACCOUNT_LEVERAGE);
   if(lev>0 && free<InpNotionalUSD*3.0/(double)lev*1.2)
   { skipExec(pi,"precheck","marge insuffisante"); return false; }
   int s0=eng.pS[pi],end=eng.pEnd[pi];
   int need=eng.pL[pi]+((s0!=0)?1:0)+((end!=0)?1:0);
   if(need<1||need>OG_MAX_BASKET_LEGS)
   { skipExec(pi,"precheck","panier hors bornes"); return false; }
   //--- fail-closed (audit 16/11) : l'edge declare doit couvrir 1.5x la
   //    commission REELLE A/R ($/lot-base), independamment de
   //    InpCommPer100K=0 dans l'eval. 43 paniers du 13/11 entres a net
   //    moyen +0.24$ vs cout reel -2.83$ = -119$ : plus jamais un net
   //    ex-commission ne valide un panier.
   double commReq=2.0*eng.basketCommUsd(pi,InpNotionalUSD,3.5);  // 3.5$/lot/side mesure
   if(ein<1.5*commReq)
   { skipExec(pi,"precheck",StringFormat("net %.2f<1.5xcommReq %.2f",ein,1.5*commReq)); return false; }
   double qty=InpNotionalUSD,qtyTo=qty; string err;
   gB.n=0;
   int filled=0;
   bool ok=true;
   if(s0!=0)
   {
      int e=eng.usdSym[s0]; ulong t=0;
      if(e<0){ abortLeg(pi,"entry","usdSym absent"); ok=false; }
      else if(!sendLeg(e,0,qty,qtyTo,err,t)){ if(t!=0) gB.tkt[gB.n++]=t; legFail(pi,"entry",err); ok=false; }
      else { gB.tkt[gB.n++]=t; qty=qtyTo; filled++; }
   }
   int node=s0;
   for(int li=0;ok&&li<eng.pL[pi];li++)
   {
      int e=eng.pE[pi][li]; ulong t=0;
      node=(li==0)?s0:((eng.pDir[pi][li-1]==1)?eng.symB[eng.pE[pi][li-1]]:eng.symA[eng.pE[pi][li-1]]);
      if(!sendLeg(e,node,qty,qtyTo,err,t)){ if(t!=0) gB.tkt[gB.n++]=t; legFail(pi,IntegerToString(li),err); ok=false; break; }
      gB.tkt[gB.n++]=t; qty=qtyTo; filled++;
   }
   if(ok&&end!=0)
   {
      int e=eng.usdSym[end]; ulong t=0;
      if(e<0){ abortLeg(pi,"exit","usdSym absent"); ok=false; }
      else if(!sendLeg(e,end,qty,qtyTo,err,t)){ if(t!=0) gB.tkt[gB.n++]=t; legFail(pi,"exit",err); ok=false; }
      else { gB.tkt[gB.n++]=t; qty=qtyTo; filled++; }
   }
   // gB.n>0 couvre le residuel partial<QMin : la position existe et doit
   // etre unwound meme si aucune jambe n'est "complete"
   if(filled>0||gB.n>0) registerBasket(pi,ein,false,filled>=need,tMs);
   if(!ok||filled<need) return false;
   gFails=0;
   jlog("EXEC",StringFormat("\"path\":%d,\"filled\":%d,\"usdEnd\":%.2f,\"p\":\"%s\"",
        pi,filled,qty,jsonEsc(eng.pathStr(pi))));
   return true;
}

void onFail()
{
   if(++gFails>=gBreakMin)
   {
      jlog("EVT","\"msg\":\"breaker - halt\"");
      ExpertRemove();
   }
}

//+------------------------------------------------------------------+
// Mode scalp : l'edge mesuree est un lag de quote sur le CROISE
// (la jambe non-USD du chemin). On tire UNIQUEMENT cette jambe, a prix
// cape sur la quote stale -> fill = edge probable ; rejet "prix" = miss
// gratis, rejet dur (no money/marche ferme) = breaker.
// Cout ~1 jambe (~0.85$) vs ~2-4.5$ pour le panier complet.

// jambe disloquee d'un chemin : jambe non-USD incidente (le CROISE, jamais
// une paire d'ancrage USD) dont la quote est dormante (tMs-pxTick>=staleMs).
// La dormance identifie le laggard : son prix n'a pas suivi le leader ->
// c'est lui qui sera reprice (convergence). Pour s0=USD le croise est en
// jambes 1..nl-1 ; pour s0!=USD c'est la jambe 0. Scan deepest-first.
// rel dislocation de e vu depuis `from` via ancres USD : >0 = edge residuelle.
// Prix juste EXECUTABLE de e via ses ancres (independantes de e ; jamais via la
// fermeture du chemin : tautologique a ddd). Ancres fraiches ET non dormantes
// exigees (une ancre dormante = vrai laggard ailleurs -> fair contamine).
bool legRel(int e,int from,double &rel,double &F,long tMs)
{
   rel=0; F=0;
   int a=eng.symA[e], b=eng.symB[e];
   if(a==0||b==0) return false;                       // jambe USD : pas de synthese
   int xsA=eng.usdSym[a], xsB=eng.usdSym[b];
   if(xsA<0||xsB<0) return false;
   if(eng.bidP[e]<=0||eng.askP[e]<eng.bidP[e]) return false; // quote e valide
   if(eng.bidP[xsA]<=0||eng.askP[xsA]<eng.bidP[xsA]) return false;
   if(eng.bidP[xsB]<=0||eng.askP[xsB]<eng.bidP[xsB]) return false;
   if(eng.lastT[xsA]<=0||tMs-eng.lastT[xsA]>InpFreshMs) return false;
   if(eng.lastT[xsB]<=0||tMs-eng.lastT[xsB]>InpFreshMs) return false;
   if(tMs-pxTick[xsA]>=InpStalePxMs) return false;    // ancre dormante = suspecte
   if(tMs-pxTick[xsB]>=InpStalePxMs) return false;
   double sellA=(eng.usdDir[a]==1)? eng.bidP[xsA]     : 1.0/eng.askP[xsA];
   double buyA =(eng.usdDir[a]==1)? eng.askP[xsA]     : 1.0/eng.bidP[xsA];
   double sellB=(eng.usdDir[b]==1)? eng.bidP[xsB]     : 1.0/eng.askP[xsB];
   double bPerU =(eng.usdDir[b]==1)? 1.0/eng.askP[xsB]: eng.bidP[xsB];
   if(sellA<=0||buyA<=0||sellB<=0||bPerU<=0) return false;
   if((eng.askP[xsA]-eng.bidP[xsA])/eng.bidP[xsA]>0.002) return false;  // ancre A : spread >20bp
   if((eng.askP[xsB]-eng.bidP[xsB])/eng.bidP[xsB]>0.002) return false;  // ancre B : spread >20bp
   double fairBid=sellA*bPerU, fairAsk=buyA/sellB;
   F=(fairAsk>0)? fairBid/fairAsk : 0;               // marge synthetique bid/ask
   if(from==a) rel=eng.bidP[e]/fairBid-1.0;
   else        rel=fairAsk/eng.askP[e]-1.0;
   return true;
}

// Attribution : la jambe dormante e porte-t-elle le ddd ? attribUsd>=k*commLeg.
// why (audit 16/11) : 1 = legRel ko (ancres contaminees/quotes), 2 = attrib
// sous le plancher — la sentinelle seule confondait les trois causes.
bool legAttribOK(int pi,int e,int from,double k,double &attribUsd,double &F,long tMs,int &why)
{
   attribUsd=-1e18; F=0; why=0;
   int a=eng.symA[e];
   double rel;
   if(!legRel(e,from,rel,F,tMs)){ why=1; return false; }
   int xsA=eng.usdSym[a];
   double aToUsd=(eng.usdDir[a]==1)? eng.bidP[xsA] : 1.0/eng.askP[xsA];
   double vstep=SymbolInfoDouble(eng.symName[e],SYMBOL_VOLUME_STEP);
   if(vstep<=0) vstep=0.01;
   double lots=MathFloor(InpNotionalUSD/aToUsd/100000.0/vstep)*vstep;
   attribUsd=InpNotionalUSD*rel;
   if(!(attribUsd>=k*lots*7.0)){ why=2; return false; }
   return true;
}

bool scalpEdge(int pi,long tMs,long staleMs,int &e,int &from,double &attribUsd,double &F,string &why)
{
   attribUsd=-1e18; F=0;                // sentinelle : aucune attribution calculable
   int nl=eng.pL[pi];
   if(nl<1||nl>3){ why="pL invalide"; return false; }   // s0!=USD pL=1 : le croise est la jambe 0
   double bestA=1.5*7.0*0.1;      // seuil minimal k=1.5 x ~0.1 lot typique
   int be=-1, bf=-1;
   long maxDorm=0; int nDorm=0,nAnchKo=0; double bestFail=0;
   for(int i=nl-1;i>=0;i--)
   {
      int ei=eng.pE[pi][i];
      if(eng.symA[ei]==0||eng.symB[ei]==0) continue;  // jambe USD d'ancrage, pas un croise
      long dorm=tMs-pxTick[ei];
      if(dorm>maxDorm) maxDorm=dorm;
      if(dorm<staleMs) continue;                      // jambe vivante = leader, skip
      nDorm++;
      int f;
      if(i==0) f=eng.pS[pi];                          // entre depuis s0
      else
      {
         int ep=eng.pE[pi][i-1];
         f=(eng.pDir[pi][i-1]==1)?eng.symB[ep]:eng.symA[ep];
      }
      if(f<0) continue;
      double aUsd,fX; int lwhy=0;
      if(!legAttribOK(pi,ei,f,1.0,aUsd,fX,tMs,lwhy))
      {
         if(aUsd>attribUsd){ attribUsd=aUsd; F=fX; }
         if(lwhy==1) nAnchKo++;
         else if(aUsd>bestFail) bestFail=aUsd;
         continue;
      }
      if(aUsd>bestA){ bestA=aUsd; be=ei; bf=f; F=fX; }  // argmax : le plus ecarte
   }
   if(be<0)
   {
      if(nDorm==0)       why=StringFormat("vivant maxDorm=%I64dms",maxDorm);
      else if(nAnchKo>0) why="dormant ancres contaminees";
      else               why=StringFormat("dormant attrib<floor %.2f",bestFail);
      return false;
   }
   e=be; from=bf; attribUsd=bestA; return true;
}

bool snipeScalp(int pi,double dddIn,long tMs,string &err,double &attribUsd,double &F)
{
   int e,from;
   if(!scalpEdge(pi,tMs,InpStalePxMs,e,from,attribUsd,F,err)) return false;
   string sym=eng.symName[e];
   bool sellBase=(from==eng.symA[e]);
   //--- filling : IOC/FOK requis (un rejet RETURN pendrait l'ordre)
   long fm=SymbolInfoInteger(sym,SYMBOL_FILLING_MODE);
   int fill;
   if((fm&2)!=0)      fill=ORDER_FILLING_IOC;
   else if((fm&1)!=0) fill=ORDER_FILLING_FOK;
   else { err="filling ni IOC ni FOK"; return false; }
   //--- taille : notional USD -> base de e via la paire USD
   int a=eng.symA[e];
   int xs=eng.usdSym[a];
   if(xs<0){ err="pas de taux base->USD"; return false; }
   double aToUsd=(eng.usdDir[a]==1)?eng.bidP[xs]:1.0/eng.askP[xs];
   if(aToUsd<=0){ err="taux base nul"; return false; }
   double vmin=SymbolInfoDouble(sym,SYMBOL_VOLUME_MIN);
   double vstep=SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP);
   double vmax=SymbolInfoDouble(sym,SYMBOL_VOLUME_MAX);
   double lots=MathFloor(InpNotionalUSD/aToUsd/100000.0/vstep)*vstep;
   if(lots<vmin-1e-9){ err=StringFormat("lots %.3f<vmin %.2f",lots,vmin); return false; }
   lots=NormalizeDouble(MathMin(vmax,lots),2);
   //--- gate comm-aware : le ddd doit couvrir k fois le cout A/R de la jambe.
   //    CADJPY/AUDJPY (0.14 lot -> ~0.98$) exigent ddd>=1.47 ; GBPCAD (0.07) >=0.74.
   double commLeg=lots*7.0;
   if(dddIn<1.5*commLeg)
   { err=StringFormat("ddd %.2f<1.5xcomm %.2f",dddIn,1.5*commLeg); return false; }
   //--- prix CAP sur la quote stale (indicatif — market-exec ne garantit
   //    pas le cap ; le garde-fou est le re-poll adverse plus bas)
   int dig=(int)SymbolInfoInteger(sym,SYMBOL_DIGITS);
   double cap=NormalizeDouble(sellBase?eng.bidP[e]:eng.askP[e],dig);
   if(cap<=0){ err="quote nulle"; return false; }
   //--- SL/TP en prix : dist $ -> px via tick value
   double tv=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_VALUE);
   double ts=SymbolInfoDouble(sym,SYMBOL_TRADE_TICK_SIZE);
   if(!(tv>0&&ts>0)){ err="SL incalculable tv/ts"; return false; }  // jamais nu
   long stopsLv=SymbolInfoInteger(sym,SYMBOL_TRADE_STOPS_LEVEL);
   double pt=SymbolInfoDouble(sym,SYMBOL_POINT);
   double slPx=0,tpPx=0;
   if(lots>0)
   {
      double slD=InpScalpStop*gSc*ts/(tv*lots);
      double minD=(stopsLv+2)*pt;
      if(slD<minD) slD=minD;
      slPx=NormalizeDouble(sellBase?cap+slD:cap-slD,dig);
      tpPx=0;   // pas de TP broker : le watcher sort au plancher net (tpnet)
   }
   MqlTradeRequest req; ZeroMemory(req);
   MqlTradeResult  res; ZeroMemory(res);
   req.action=TRADE_ACTION_DEAL; req.symbol=sym; req.magic=InpMagic;
   req.volume=lots; req.type=sellBase?ORDER_TYPE_SELL:ORDER_TYPE_BUY;
   req.price=cap; req.deviation=0;
   req.type_filling=(ENUM_ORDER_TYPE_FILLING)fill;
   req.type_time=ORDER_TIME_GTC;
   req.sl=slPx; req.tp=tpPx;
   req.comment="ogScalp";
   //--- dernier controle : sous market-exec le cap n'est PAS une garantie
   //    serveur — on re-poll la quote et on abort si elle a deja converge
   //    dans le sens adverse entre la detection et l'envoi.
   MqlTick tk0;
   if(SymbolInfoTick(sym,tk0))
   {
      double cur=sellBase?tk0.bid:tk0.ask;
      double adv=sellBase?(cap-cur):(cur-cap);   // adverse : SELL bid baisse, BUY ask monte
      if(adv>pt){ err="repriced adverse avant envoi"; return false; }
   }
   if(!OrderSend(req,res)||(res.retcode!=TRADE_RETCODE_DONE&&res.retcode!=TRADE_RETCODE_DONE_PARTIAL))
   {
      // rejets "prix" = quote deja convergee -> miss gratis ; retcodes
      // durs (NO_MONEY, TRADE_DISABLED, REJECT, freeze...) = faute -> breaker
      uint rc=res.retcode;
      bool benign=(rc==TRADE_RETCODE_REQUOTE||rc==TRADE_RETCODE_PRICE_CHANGED
                ||rc==TRADE_RETCODE_PRICE_OFF);
      err=StringFormat("%s %s %.2f@%.5f : %s",sellBase?"SELL":"BUY",sym,lots,cap,
           rc>0?res.comment:"OrderSend fail");
      if(!benign) onFail();
      return false;
   }
   double execLots=res.volume>0?res.volume:lots;
   double fpx=res.price>0?res.price:cap;
   ulong t=findLegPos(sym,res.deal);
   if(t==0) jlog("EVT","\"msg\":\"scalp tkt=0 : position non retrouvee, rescan arme\"");
   gB.tkt[gB.n++]=t;
   gB.legSym=sym; gB.legDeal=res.deal;   // rescan si la position a ete remplie en async
   gB.legE=e; gB.legFrom=from;
   if(!legRel(e,from,gB.relIn,F,tMs)) gB.relIn=0;      // F = marge synth. re-mesuree au fill
   //--- SL ancre sur le prix REEL de fill : si fill < cap (amelioration),
   //    le SL calcule sur cap serait mal place voire cote oppose.
   if(slPx!=0&&gB.n>0&&gB.tkt[0]!=0&&tv>0&&ts>0&&execLots>0)
   {
      double slD=InpScalpStop*gSc*ts/(tv*execLots);
      double minD=(stopsLv+2)*pt;
      if(slD<minD) slD=minD;
      double slFix=NormalizeDouble(sellBase?fpx+slD:fpx-slD,dig);
      if(MathAbs(slFix-slPx)>pt*0.5)
         if(!gTrade.PositionModify(gB.tkt[0],slFix,0))
            jlog("EVT",StringFormat("\"msg\":\"SL fix fail tkt=%I64u : %s\"",
                 gB.tkt[0],jsonEsc(gTrade.ResultRetcodeDescription())));
   }
   jlog("FILL",StringFormat("\"sym\":\"%s\",\"side\":\"%s\",\"lots\":%.4f,"
        "\"px\":%.5f,\"ddd\":%.4f,\"dormMs\":%I64d,\"slip\":%.5f,\"attrib\":%.2f,"
        "\"F\":%.5f,\"scalp\":1",
        jsonEsc(sym),sellBase?"SELL":"BUY",execLots,fpx,dddIn,tMs-pxTick[e],
        sellBase?cap-fpx:fpx-cap,attribUsd,F));   // slip >0 = adverse
   return true;
}

bool scalpFire(int pi,double ddd,long tMs)
{
   if(!InpAllowLive||gB.open) return true;   // position deja ouverte = reel
   if(ddd<InpScalpDdd*gSc)
   { skipExec(pi,"scalp",StringFormat("ddd %.2f<%.2f",ddd,InpScalpDdd*gSc)); return false; }
   if(!MQLInfoInteger(MQL_TESTER))
   {
      long tta=TerminalInfoInteger(TERMINAL_TRADE_ALLOWED);
      long ate=AccountInfoInteger(ACCOUNT_TRADE_EXPERT);
      if(!tta||!ate){ skipExec(pi,"precheck",StringFormat("tta=%ld expert=%ld",tta,ate)); return true; }
   }
   string err; double attribUsd,F=0;
   if(!snipeScalp(pi,ddd,tMs,err,attribUsd,F))
   { gSkipPi=pi; gSkipUntil=tMs+10000;      // argmax brule -> exclure 10s
     // attrib omis quand sentinelle (-1e18 = non calculable, pas un chiffre)
     string attr=(attribUsd>-1e17)?StringFormat(",\"attrib\":%.2f",attribUsd):"";
     jlog("SCALPSKIP",StringFormat("\"pi\":%d,\"err\":\"%s\"%s,\"F\":%.5f,\"p\":\"%s\"",
        pi,jsonEsc(err),attr,F,jsonEsc(eng.pathStr(pi)))); return false; }
   registerBasket(pi,ddd,true,true,tMs);
   jlog("EXEC",StringFormat("\"path\":%d,\"filled\":1,\"scalp\":1,\"ddd\":%.4f,\"p\":\"%s\"",
        pi,ddd,jsonEsc(eng.pathStr(pi))));
   return true;
}

bool sessionOk()
{
   if(InpSessFromUTC<0||InpSessToUTC<0) return true;
   if(InpSessFromUTC==InpSessToUTC) return true;   // from==to = 24h (pas session morte)
   MqlDateTime dt; TimeToStruct(TimeGMT(),dt);
   int h=dt.hour;
   if(InpSessFromUTC<InpSessToUTC) return h>=InpSessFromUTC&&h<InpSessToUTC;
   return h>=InpSessFromUTC||h<InpSessToUTC;
}

//+------------------------------------------------------------------+
int OnInit()
{
   resetState();
   if(StringLen(InpSymbols)>0)
   {
      string parts[];
      int k=StringSplit(InpSymbols,',',parts);
      for(int i=0;i<k;i++)
      {
         StringTrimLeft(parts[i]); StringTrimRight(parts[i]);
         string nm=InpSymPrefix+parts[i]+InpSymSuffix;
         if(!SymbolSelect(nm,true)){ PrintFormat("OgArb: %s absent",nm); continue; }
         if(eng.addSym(parts[i],nm)<0)
            PrintFormat("OgArb: %s ignore (devise hors table 8)",parts[i]);
      }
   }
   else
      for(int i=0;i<ArraySize(FX_DEF);i++)
      {
         string nm=InpSymPrefix+FX_DEF[i]+InpSymSuffix;
         if(!SymbolSelect(nm,true)){ PrintFormat("OgArb: %s absent",nm); continue; }
         eng.addSym(FX_DEF[i],nm);
      }
   if(eng.nSym==0){ Print("OgArb: aucun symbole"); return INIT_FAILED; }
   //--- mode de marge : netting requis en live (Convention compte NETTED)
   long mm=AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   string mmS=(mm==ACCOUNT_MARGIN_MODE_RETAIL_NETTING)?"NETTING":
              (mm==ACCOUNT_MARGIN_MODE_EXCHANGE)?"EXCHANGE":"HEDGING";
   PrintFormat("OgArb: ACCOUNT_MARGIN_MODE=%s",mmS);
   if(mm==ACCOUNT_MARGIN_MODE_RETAIL_HEDGING && InpAllowLive && !InpAllowHedgedAcct)
   {
      Print("OgArb: compte HEDGING refuse en mode live (NETTED requis)");
      return INIT_FAILED;
   }
   if(mm==ACCOUNT_MARGIN_MODE_EXCHANGE && InpAllowLive)
      Print("OgArb: WARN mode EXCHANGE - filling policy a verifier");
   medWin=(int)MathMin(MathMax((double)InpMedWindow,16.0),256.0);
   gWarmMin=(int)MathMax(1,InpWarmupTicks);
   gBreakMin=(int)MathMax(1,InpBreakerFails);
   if(InpWarmupTicks<1||InpBreakerFails<1)
      PrintFormat("OgArb: WARN clamps warm=%d break=%d",gWarmMin,gBreakMin);
   if(InpSessFromUTC<-1||InpSessFromUTC>24||InpSessToUTC<-1||InpSessToUTC>24||
      (InpSessFromUTC<0&&InpSessToUTC>=0)||(InpSessToUTC<0&&InpSessFromUTC>=0))
      PrintFormat("OgArb: WARN session invalide/partielle from=%d to=%d",
                  InpSessFromUTC,InpSessToUTC);
   //--- seuils $ calibres a 10k -> scaling a la taille reellement tradee
   gSc=MathMin(MathMax(AccountInfoDouble(ACCOUNT_EQUITY)*2.5,2500.0),
               (double)InpNotionalUSD)/10000.0;
   eng.buildPaths(MathMin(MathMax(InpMaxLegs,1),3));
   if(eng.nPaths<=0){ Print("OgArb: 0 chemin construit"); return INIT_FAILED; }
   PrintFormat("OgArb: %d syms, %d chemins (max %d jambes)",eng.nSym,eng.nPaths,InpMaxLegs);
   gTrade.SetExpertMagicNumber(InpMagic);
   gTrade.SetAsyncMode(false);
   //--- garde-fous plateforme (live seulement ; detect-only les ignore)
   if(InpAllowLive)
   {
      if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
         Print("OgArb: WARN MQL_TRADE_ALLOWED=false - autotrading EA bloque");
      if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
         Print("OgArb: WARN TERMINAL_TRADE_ALLOWED=false - bouton AutoTrading off");
      if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
         Print("OgArb: WARN ACCOUNT_TRADE_ALLOWED=false");
      if(!AccountInfoInteger(ACCOUNT_TRADE_EXPERT))
         Print("OgArb: WARN ACCOUNT_TRADE_EXPERT=false - EA interdit sur ce compte");
   }
   gLogH=FileOpen(InpLogFile,FILE_READ|FILE_WRITE|FILE_TXT|FILE_ANSI|FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_COMMON);
   if(gLogH!=INVALID_HANDLE) FileSeek(gLogH,0,SEEK_END);
   else PrintFormat("OgArb: WARN journal %s non ouvert (err %d)",InpLogFile,GetLastError());
   //--- adoption orphelins : positions magic residuelles (kill mid-hold)
   //    -> cloture immediate, flat start garanti
   if(InpAllowLive)
   {
      int orph=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong ct=PositionGetTicket(i);                        // ticket = close
         ulong pid=(ulong)PositionGetInteger(POSITION_IDENTIFIER); // = tracking
         if(ct==0) continue;
         if(PositionGetInteger(POSITION_MAGIC)!=InpMagic) continue;
         string ps=PositionGetString(POSITION_SYMBOL);
         double ppl=PositionGetDouble(POSITION_PROFIT);
         if(gTrade.PositionClose(ct))
            jlog("EVT",StringFormat("\"msg\":\"ORPHAN closed tkt=%I64u sym=%s pl=%.2f\"",
                 ct,jsonEsc(ps),ppl));
         else
         {
            jlog("EVT",StringFormat("\"msg\":\"ORPHAN close FAIL tkt=%I64u sym=%s : %s\"",
                 ct,jsonEsc(ps),jsonEsc(gTrade.ResultRetcodeDescription())));
            // orphelin echoue -> confie au watcher (retry borne + suivi)
            // au lieu de rester nu et non tracke jusqu'au prochain restart
            if(gB.n<OG_MAX_BASKET_LEGS) gB.tkt[gB.n++]=pid;
            else jlog("EVT",StringFormat("\"msg\":\"ORPHAN overflow tkt=%I64u sym=%s - au-dela du cap, non tracke\"",
                     ct,jsonEsc(ps)));
         }
         orph++;
      }
      if(orph>0) PrintFormat("OgArb: %d positions orphelines traitees",orph);
      if(gB.n>0)
      {
         // panier "synthetique" pi=-1 : pas de chemin -> exits hold/session
         // uniquement (garde pi<0 dans le watcher), retry borne en place.
         gB.pi=-1; gB.openFeedMs=0;
         gB.path="orphan"; gB.open=true;
         gB.scalp=false; gB.full=false; gB.ein=0; gB.minNet=0;
         gB.badEvals=0; gB.closeFails=0; gB.closedN=0;
         gB.seenOpen=true; gB.rescanN=0; gB.legSym=""; gB.legDeal=0;
         for(int k=0;k<gB.n;k++) gB.allTkt[k]=gB.tkt[k];
         gB.allN=gB.n;
      }
   }
   if(!EventSetMillisecondTimer(MathMax(50,InpEvalMs)))
   { Print("OgArb: EventSetMillisecondTimer refuse"); return INIT_FAILED; }
   jlog("EVT",StringFormat("\"msg\":\"OgArb start %d syms %d paths live=%s acct=%s\"",
        eng.nSym,eng.nPaths,InpAllowLive?"true":"false",mmS));
   jlog("EVT",StringFormat("\"msg\":\"params notional=%.0f comm=%.2f commScale=%.2f "
        "dddMin=%.2f freshMs=%I64d skewMs=%I64d spreadCap=%.2f medWin=%d warm=%d "
        "deadSec=%d evalMs=%d sess=%d..%d maxLegs=%d slipBp=%.2f qMin=%.2f "
        "seqGap=%I64d holdSec=%d mode=%d scalpDdd=%.2f scalpStop=%.2f "
        "convExit=%d minHoldMs=%d netMin=%.2f stalePx=%d breaker=%d magic=%I64d\"",
        InpNotionalUSD,InpCommPer100K,InpCommScale,InpDddMin,InpFreshMs,InpSkewMs,
        InpSpreadCap,(int)InpMedWindow,InpWarmupTicks,InpDeadSec,InpEvalMs,
        InpSessFromUTC,InpSessToUTC,InpMaxLegs,InpTauSlipBp,InpQMin,
        InpMinSeqGapMs,InpHoldSec,InpMode,InpScalpDdd,InpScalpStop,
        (int)InpConvExit,InpMinHoldMs,InpScalpNetMin,InpStalePxMs,InpBreakerFails,InpMagic));
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   jlog("EVT",StringFormat("\"msg\":\"OgArb stop reason=%d\"",reason));
   if(gLogH!=INVALID_HANDLE){ FileClose(gLogH); gLogH=INVALID_HANDLE; }
}

//+------------------------------------------------------------------+
void OnTimer()
{
   gTicks++;
   poll();
   //--- horloge "maintenant" en vraies ms : dernier tick serveur + ecoulement
   //    local depuis sa reception. Un flux fige fait vieillir tMs -> staleness
   //    croit -> fail-closed. Plus de blocage intra-seconde (bug tMs=sec*1000).
   long newest=0;
   for(int i=0;i<eng.nSym;i++) if(eng.lastT[i]>newest) newest=eng.lastT[i];
   if(newest>gFeedMs){ gFeedMs=newest; gFeedTick=GetTickCount64(); }
   long tMs=gFeedMs+(long)(GetTickCount64()-gFeedTick);
   //--- dead = staleness : jamais tick apres grace OU silence > InpDeadSec.
   //    Resurrection automatique des que le flux reprend (lastT rafraichi).
   bool grace=(TimeGMT()-gStartSec>InpDeadSec);
   for(int i=0;i<eng.nSym;i++)
   {
      bool was=symDead[i];
      symDead[i]=(eng.lastT[i]==0)? grace : (tMs-eng.lastT[i]>(long)InpDeadSec*1000);
      if(symDead[i]&&!was)
         jlog("EVT",StringFormat("\"msg\":\"DEAD sym %s - pas de flux, exclu du gate\"",jsonEsc(eng.symName[i])));
      else if(!symDead[i]&&was)
         jlog("EVT",StringFormat("\"msg\":\"ALIVE sym %s - flux repris\"",jsonEsc(eng.symName[i])));
   }
   for(int i=0;i<eng.nSym;i++) medArr[i]=medianOf(i);
   int warmed=0;
   for(int i=0;i<eng.nSym;i++) if(symSeen[i]>=gWarmMin||symDead[i]) warmed++;

   OgParams P;
   P.notional=InpNotionalUSD; P.commPer100k=InpCommPer100K;
   P.commScale=InpCommScale; P.spreadCap=InpSpreadCap;
   P.freshMs=InpFreshMs; P.skewMs=InpSkewMs; P.maxLegs=InpMaxLegs;

   //--- watcher panier/scalp : unwind hold/session + exits par convergence.
   //    Place avant le gate warmup : un panier se ferme meme si le flux gicle.
   //    Pw : meme P mais skewMs=0 â€” le skew est un gate d'ENTREE lead-lag,
   //    hors sujet pour pricer une sortie.
   if(gB.open)
   {
      long held=(long)(tMs-gB.openFeedMs);   // horloge feed : sim-safe en tester
      if(gB.scalp)
      {
         //--- P&L flottant evalue a chaque tick, eval sain ou non
         double fp=0,commRT=0; int found=0;
         for(int k=0;k<gB.n;k++)
         {
            ulong ct=ticketOf(gB.tkt[k]);   // tkt[]=identifier -> ticket courant
            if(ct!=0&&PositionSelectByTicket(ct))
            {  found++;
               fp+=PositionGetDouble(POSITION_PROFIT)+PositionGetDouble(POSITION_SWAP);
               commRT+=PositionGetDouble(POSITION_VOLUME)*7.0;   // ~3.5$/lot/side IC Raw
            }
         }
         // fast-path : position disparue (SL broker/main) -> compte flat,
         // on finalise tout de suite sinon gB.open bloque tout nouveau tir.
         // MAIS seulement si la position a DEJA ete vue ouverte : un tkt=0
         // (fill async non lie) ne doit pas liberer le panier, sinon on
         // retirerait en boucle avec une position nue non trackee.
         if(found==0)
         {
            if(gB.seenOpen)                        closeBasket("gone");
            else
            {
               ulong t=(gB.legSym!="")?findLegPos(gB.legSym,gB.legDeal):0;
               if(t!=0){ gB.tkt[0]=t; gB.allTkt[0]=t; gB.seenOpen=true; }
               else if(++gB.rescanN>=20)
               {
                  jlog("EVT","\"msg\":\"scalp gone : aucune position liee apres 20 ticks\"");
                  closeBasket("gone");
               }
            }
         }
         else gB.seenOpen=true;
         if(found>0)
         {
            if(fp<=-InpScalpStop*gSc)             closeBasket("stop");   // toujours arme
            else if(held>=(long)InpHoldSec*1000)  closeBasket("hold");
            else if(!sessionOk())                 closeBasket("session");
            else if(held>=InpMinHoldMs)
            {
               if(fp>=commRT+InpScalpNetMin*gSc)  closeBasket("tpnet");
               else if(gB.legE>=0)
               {
                  //--- conv sur la JAMBE tenue, pas le chemin complet (le ddd
                  //    peut converger via les autres jambes pendant que e perd)
                  double relNow=0,fNow=0;
                  if(legRel(gB.legE,gB.legFrom,relNow,fNow,tMs))
                  {
                     gB.badEvals=0;
                     if(InpConvExit&&(relNow<=0.25*gB.relIn||relNow<=0))
                                                            closeBasket("conv");
                  }
                  else if(++gB.badEvals>=40)      closeBasket("invalid");  // eval morte ~20s
               }
            }
         }
      }
      else if(held>=(long)InpHoldSec*1000)      closeBasket("hold");
      else if(!sessionOk())                     closeBasket("session");
      else if(InpConvExit&&held>=InpMinHoldMs&&gB.pi>=0)
      {
         OgParams Pw=P; Pw.skewMs=0;
         double nNow=0,dNow=1,cX=0; int wf=0;
         bool ok=eng.evalPath(gB.pi,tMs,Pw,medArr,nNow,dNow,cX,wf);
         if(ok)
         {
            gB.badEvals=0;
            if(nNow<gB.minNet) gB.minNet=nNow;
            double R=gB.ein-nNow;                    // convergence capturee
            if(gB.full&&R>=cX)                       closeBasket("conv");
            else if(nNow<=-InpUnwindFloor)           closeBasket("floor");
            else if(nNow>=gB.ein+InpDivStop)         closeBasket("div");
            else if(gB.minNet<=-0.5*InpUnwindFloor
                    &&nNow>=gB.minNet+InpTrailBack)  closeBasket("trail");
         }
         else if(++gB.badEvals>=8)          closeBasket("invalid");
      }
   }

   if(warmed<eng.nSym||!sessionOk()){ heartbeat(warmed); return; }

   double net,ddd; int pi;
   if(!eng.evalAll(tMs,P,medArr,net,pi,ddd,(tMs<gSkipUntil?gSkipPi:-1)))
   { heartbeat(warmed); return; }
   gEvals++;
   if(net>gMaxNet){ gMaxNet=net; gMaxPath=pi; }
   if(InpLogTicks) jlog("EVAL",StringFormat("\"net\":%.4f,\"pi\":%d",net,pi));
   if(net>=InpDddMin*gSc&&tMs>=gLastSeq+InpMinSeqGapMs)
   {
      gDetects++;
      jlog("OPP",StringFormat("\"net\":%.4f,\"pi\":%d,\"ddd\":%.4f,\"p\":\"%s\",\"q\":\"%s\"",
           net,pi,ddd,jsonEsc(eng.pathStr(pi)),pathQuotes(pi)));
      bool real=false;
      if(InpMode==2)      real=scalpFire(pi,ddd,tMs);
      else if(InpMode==1){ execBasket(pi,tMs,net); real=true; }
      gLastSeq=real?tMs:0;   // seqGap sur tentative reelle seulement
   }
   heartbeat(warmed);
}

void heartbeat(int warmed)
{
   datetime now=TimeGMT();
   if(now-gLastHb<InpHbSec) return;
   gLastHb=now;
   int dead=0;
   for(int i=0;i<eng.nSym;i++) if(symDead[i]) dead++;
   jlog("HB",StringFormat("\"ticks\":%I64d,\"warmed\":\"%d/%d\",\"dead\":%d,\"evals\":%I64d,"
        "\"det\":%I64d,\"basket\":%d,\"maxNet\":%.4f,\"path\":%d",
        gTicks,warmed,eng.nSym,dead,gEvals,gDetects,gB.open?gB.n:0,
        gMaxNet>-1e17?gMaxNet:0.0,gMaxPath));
}
//+------------------------------------------------------------------+
