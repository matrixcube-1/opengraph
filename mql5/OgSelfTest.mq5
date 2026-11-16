//+------------------------------------------------------------------+
//| OgSelfTest.mq5 — tests du moteur OgCore dans le Strategy Tester   |
//| Protocole identique a InjectTest.java :                           |
//|  livre coherent -> net<0 ; injection -> net>0 sur le bon chemin.  |
//| Sortie : lignes "TEST <nom> PASS|FAIL <detail>" dans le journal.  |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.00"
#include "OgCore.mqh"

int gPass=0, gFail=0;

void check(bool cond,const string name,const string detail)
{
   if(cond){ gPass++; PrintFormat("TEST %s PASS %s",name,detail); }
   else    { gFail++; PrintFormat("TEST %s FAIL %s",name,detail); }
}

// 28 paires Annexe J.2
string FX[]={
   "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
   "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY",
   "GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD",
   "AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"};

// prix USD par unite de devise (livre coherent)
double U[8] = {1.0, 1.10, 1.25, 1.0/150.0, 1.0/0.90, 1.0/1.36, 0.65, 0.60};

void fillBook(COgEngine &e,long t,double relSpr)
{
   for(int i=0;i<e.nSym;i++)
   {
      double mid=U[e.symA[i]]/U[e.symB[i]];
      double bid=mid*(1.0-relSpr/2.0), ask=mid*(1.0+relSpr/2.0);
      e.setQuote(i,bid,ask,t);
   }
}

void fillMed(const COgEngine &e,double &med[],double relSpr)
{
   for(int i=0;i<e.nSym;i++)
   {
      double mid=U[e.symA[i]]/U[e.symB[i]];
      med[i]=mid*relSpr;
   }
}

int symIdx(const COgEngine &e,const string def)
{
   for(int i=0;i<e.nSym;i++)
      if(StringSubstr(e.symName[i],StringLen(e.symName[i])-6,6)==def) return i;
   return -1;
}

//+------------------------------------------------------------------+
int OnInit()
{
   long T=1700000000000;
   OgParams P;
   P.notional=10000.0; P.commPer100k=4.0; P.commScale=1.10;
   P.spreadCap=1.76; P.freshMs=8000; P.maxLegs=3;
   double med[OG_MAX_SYMS];

   //----- T0 : comptes de chemins (parite Java : 2072 / 392)
   COgEngine e0;
   for(int i=0;i<28;i++) e0.addSym(FX[i],FX[i]);
   e0.buildPaths(3);
   check(e0.nPaths==2072,"paths_L3",IntegerToString(e0.nPaths));
   e0.buildPaths(2);
   check(e0.nPaths==392,"paths_L2",IntegerToString(e0.nPaths));

   //----- T1 : livre coherent -> net < 0 (plancher cout)
   COgEngine e;
   for(int i=0;i<28;i++) e.addSym(FX[i],FX[i]);
   e.buildPaths(3);
   double relSpr=1e-4;                       // ~1 pip
   fillBook(e,T,relSpr); fillMed(e,med,relSpr);
   double net,ddd; int pi;
   bool r=e.evalAll(T,P,med,net,pi,ddd);
   check(r && net<0,"coherent_neg",StringFormat("net=%.4f pi=%d",net,pi));
   double netCoh=net;

   //----- T2 : injection +0.75 JPY sur EURJPY -> net > 0
   int ij=symIdx(e,"EURJPY");
   double b=e.bidP[ij],a=e.askP[ij];
   e.setQuote(ij,b+0.75,a+0.75,T);
   r=e.evalAll(T,P,med,net,pi,ddd);
   check(r && net>0,"inject_pos",StringFormat("net=%.4f pi=%d",net,pi));
   bool viaEJ=false;
   if(pi>=0)
      for(int li=0;li<e.pL[pi];li++)
         if(e.pE[pi][li]==ij) viaEJ=true;
   check(viaEJ,"inject_path",e.pathStr(pi));
   e.setQuote(ij,b,a,T);   // restaure

   //----- T3 : stale EURJPY -> ses chemins exclus, net retombe <0
   e.setQuote(ij,b+0.75,a+0.75,T-9000);      // injecte mais stale
   r=e.evalAll(T,P,med,net,pi,ddd);
   check(r && MathAbs(net-netCoh)<1e-6,"stale_gate",
         StringFormat("net=%.4f attendu~%.4f",net,netCoh));
   e.setQuote(ij,b,a,T);

   //----- T4 : spread blowout EURJPY -> gate spread rejette
   double midEj=U[1]/U[3];
   e.setQuote(ij,midEj*0.985,midEj*1.015,T); // spread 3% ~30x mediane
   r=e.evalAll(T,P,med,net,pi,ddd);
   bool viaEJ2=false;
   if(pi>=0)
      for(int li=0;li<e.pL[pi];li++)
         if(e.pE[pi][li]==ij) viaEJ2=true;
   check(r && !viaEJ2 && net<0,"spread_gate",
         StringFormat("net=%.4f viaEJ=%s",net,viaEJ2?"1":"0"));
   e.setQuote(ij,b,a,T);

   //----- T5 : comm 0 -> plancher remonte ; comm 4 -> enfonce
   OgParams P0=P; P0.commPer100k=0.0; P0.commScale=0.0;
   r=e.evalAll(T,P0,med,net,pi,ddd);
   double net0=net;
   check(r && net0>netCoh,"zero_fees",
         StringFormat("net0=%.4f netCoh=%.4f",net0,netCoh));

   //----- T6 : skew inter-jambes -> artefact lead-lag rejete
   // EURJPY disloque a T, tout le reste du livre a T-500 : skew=500>300
   // -> les chemins via EURJPY exclus, net retombe au plancher coherent
   e.setQuote(ij,b+0.75,a+0.75,T);
   for(int i=0;i<e.nSym;i++) if(i!=ij) e.setQuote(i,e.bidP[i],e.askP[i],T-500);
   P.skewMs=300;
   r=e.evalAll(T,P,med,net,pi,ddd);
   check(r && MathAbs(net-netCoh)<1e-6,"skew_gate",
         StringFormat("net=%.4f attendu~%.4f",net,netCoh));
   P.skewMs=0;                                   // gate off -> detecte a nouveau
   r=e.evalAll(T,P,med,net,pi,ddd);
   check(r && net>0,"skew_off",StringFormat("net=%.4f",net));
   e.setQuote(ij,b,a,T); fillBook(e,T,relSpr);

   //----- T7 : unwind floor = max(spread, mediane)
   // tous les spreads serres x10 ; medianes conservees -> le cout de
   // retraversee plancher a la mediane (anti quote stale a spread serre)
   fillBook(e,T,relSpr/10.0);
   r=e.evalAll(T,P,med,net,pi,ddd);          // floor actif
   double netFloor=net;
   double med0[OG_MAX_SYMS];
   for(int i=0;i<e.nSym;i++) med0[i]=0;      // med=0 -> floor off
   r=e.evalAll(T,P,med0,net,pi,ddd);
   check(r && net-netFloor>1.0,"unwind_floor",
         StringFormat("sans=%.4f avec=%.4f",net,netFloor));
   fillBook(e,T,relSpr);                     // restaure livre coherent

   PrintFormat("RESULT %d PASS / %d FAIL",gPass,gFail);
   return INIT_SUCCEEDED;
}

void OnTick(){}
//+------------------------------------------------------------------+
