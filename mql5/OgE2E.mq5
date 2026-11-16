//+------------------------------------------------------------------+
//| OgE2E.mq5 — simulation horodatee bout-en-bout (Strategy Tester)   |
//| Scenario : flux coherent 28 paires, puis dislocation EURJPY       |
//| (+0.75). Le moteur evalue les 2072 chemins, detecte, "execute"    |
//| le panier ordre par ordre (qty chainee, slip, comm), solde final. |
//| Chaque ligne = preuve chiffree d'une etape.                       |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.00"
#include "OgCore.mqh"

string FX[]={
   "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
   "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY",
   "GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD",
   "AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"};

double U0[8]={1.0,1.10,1.25,1.0/150.0,1.0/0.90,1.0/1.36,0.65,0.60};
COgEngine eng;
OgParams  gP;
double    gMed[OG_MAX_SYMS];

string fmtT(long tMs)
{
   long s=tMs/1000; int ms=(int)(tMs%1000);
   MqlDateTime dt; TimeToStruct((datetime)s,dt);
   return StringFormat("%02d:%02d:%02d.%03d",dt.hour,dt.min,dt.sec,ms);
}
int idx(const string def){ for(int i=0;i<eng.nSym;i++) if(eng.symName[i]==def) return i; return -1; }

void book(long t,double disloc)
{
   for(int i=0;i<eng.nSym;i++)
   {
      double m=U0[eng.symA[i]]/U0[eng.symB[i]];
      double spr=m*1e-4;
      gMed[i]=spr;
      double bid=m-spr/2,ask=m+spr/2;
      if(eng.symName[i]=="EURJPY"){ bid+=disloc; ask+=disloc; }
      eng.setQuote(i,bid,ask,t);
   }
}

// execution simulee : fill au quote executable +/- slip (tau bp),
// qty chainee, comm par cote en USD
double execOrder(int e,int from,double qty,double slipBp,double commSide,
                 long tFill,string tag)
{
   int a=eng.symA[e],b=eng.symB[e];
   bool sell=(from==a);
   double px=(sell? eng.bidP[e]*(1-slipBp*1e-4)
                 : eng.askP[e]*(1+slipBp*1e-4));
   double out= sell? qty*px : qty/px;
   PrintFormat("%s ORDER  %s %s %-6s qty=%.2f %s -> fill %.5f -> %.2f %s  comm -%.2f$",
               fmtT(tFill),tag,sell?"SELL":"BUY",eng.symName[e],
               qty,eng.CURR[from],px,out,eng.CURR[sell?b:a],commSide);
   return out;
}

int OnInit()
{
   for(int i=0;i<28;i++) eng.addSym(FX[i],FX[i]);
   eng.buildPaths(3);
   gP.notional=10000.0; gP.commPer100k=4.0; gP.commScale=1.10;
   gP.spreadCap=1.76; gP.freshMs=8000; gP.maxLegs=3;
   double commSide=gP.commPer100k*gP.notional/100000.0; // 0.40$
   double slip=0.69;

   long T0=1700000000000;
   Print("================ SCENARIO : 28 paires RT ================");
   PrintFormat("%s TICK   flux coherent 28 paires (EURJPY mid=165.00)",fmtT(T0));
   book(T0,0.0);
   double net,ddd; int pi;
   eng.evalAll(T0,gP,gMed,net,pi,ddd);
   PrintFormat("%s EVAL   2072 chemins scannes -> maxNet=%.4f$ path=%s",
               fmtT(T0),net,eng.pathStr(pi));
   PrintFormat("%s        net %.4f < seuil 0.27 -> rien",fmtT(T0),net);

   long T1=T0+1234;
   book(T1,0.75);
   PrintFormat("%s TICK   EURJPY disloque : bid/ask +0.75 (165.74/165.76)",fmtT(T1));
   long T2=T1+266;
   eng.evalAll(T2,gP,gMed,net,pi,ddd);
   PrintFormat("%s EVAL   2072 chemins -> maxNet=%.4f$  path=%s",
               fmtT(T2),net,eng.pathStr(pi));
   if(net<0.27){ Print("aucune detection - fin"); return INIT_FAILED; }
   PrintFormat("%s OPP    net=%.4f >= 0.27 -> DETECTION panier ouvert",fmtT(T2),net);

   // ---- decode panier : [USD->s0]+jambes+[end->USD]
   int s0=eng.pS[pi],end=eng.pEnd[pi];
   int nOrd=(s0!=0)+(end!=0)+eng.pL[pi];
   PrintFormat("%s EXEC   panier %d ordres : s0=%s end=%s (lat 60ms/ordre)",
               fmtT(T2),nOrd,eng.CURR[s0],eng.CURR[end]);

   double qty=gP.notional; long tf=T2; int oi=0;
   if(s0!=0){
      int e=eng.usdSym[s0]; tf+=60;
      qty=execOrder(e,0,qty,slip,commSide,tf,"entry"); oi++;
   }
   int node=s0;
   for(int li=0;li<eng.pL[pi];li++){
      int e=eng.pE[pi][li]; tf+=60;
      qty=execOrder(e,node,qty,slip,commSide,tf,
                    StringFormat("leg%d",li+1)); oi++;
      node=(eng.pDir[pi][li]==1)?eng.symB[e]:eng.symA[e];
   }
   if(end!=0){
      int e=eng.usdSym[end]; tf+=60;
      qty=execOrder(e,end,qty,slip,commSide,tf,"exit "); oi++;
   }
   double commTot=oi*commSide*2*gP.commScale;
   double realized=qty-gP.notional-commTot;
   PrintFormat("%s SETTLE balance : USD out=%.2f - notional=%.2f - comm x2=%.2f",
               fmtT(tf),qty,gP.notional,commTot);
   PrintFormat("%s P&L    realise=%.4f$  (detecte=%.4f$ ; ecart = slip %.2f bp + arron-dis)",
               fmtT(tf),realized,net,slip);
   Print("==========================================================");
   PrintFormat("E2E VERDICT %s", realized>0?"PASS profit positif":"FAIL");
   return INIT_SUCCEEDED;
}
void OnTick(){}
//+------------------------------------------------------------------+
