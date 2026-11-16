//+------------------------------------------------------------------+
//| OgMC.mq5 — Monte Carlo sur OgCore (Strategy Tester, math mode)    |
//| N livres synthetiques : marches aleatoires des 8 devises -> 28    |
//| paires derivees coherentes + spreads aleatoires. Injection d'une  |
//| dislocation executable sur un croise aleatoire avec proba p.      |
//| Verif : coherent -> net<0 ; injecte -> detect + chemin contient   |
//| la paire injectee (multi-jambes verifie par l'argmax).            |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.00"
#include "OgCore.mqh"

input int    InpTrials    = 3000;    // nb de livres tires
input double InpInjectP   = 0.30;    // proba d'injection par tirage
input int    InpSeed      = 42;      // graine RNG (reproductible)
input double InpMaxInjBp  = 60.0;    // injection max (bp relatif)

string FX[]={
   "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
   "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY",
   "GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD",
   "AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"};

double U0[8] = {1.0, 1.10, 1.25, 1.0/150.0, 1.0/0.90, 1.0/1.36, 0.65, 0.60};
double U[8];

double rnd(){ return (double)MathRand()/32767.0; }
double logn(double sigma)
{
   // N(0,sigma) approx par somme d'uniformes
   double s=0; for(int i=0;i<6;i++) s+=rnd();
   return (s-3.0)/MathSqrt(0.5)*sigma;
}

COgEngine eng;
OgParams  gP;
double    gMed[OG_MAX_SYMS];

// tirage d'un livre coherent : U[c] = U0[c]*exp(logn(1%)), spreads 0.5-3e-4
// optionnel : injection sur la paire injIdx de delta relatif
void drawBook(long t,int injIdx,double injDelta)
{
   for(int c=0;c<8;c++) U[c]=U0[c]*MathExp(logn(0.01));
   for(int i=0;i<eng.nSym;i++)
   {
      double m=U[eng.symA[i]]/U[eng.symB[i]];
      double spr=(0.5e-4+2.5e-4*rnd())*m;      // spread 0.5-3e-4 rel.
      gMed[i]=spr;
      double bid=m-spr/2.0, ask=m+spr/2.0;
      if(i==injIdx){ bid*=1.0+injDelta; ask*=1.0+injDelta; }
      eng.setQuote(i,bid,ask,t);
   }
}

//+------------------------------------------------------------------+
int OnInit()
{
   MathSrand(InpSeed);
   gP.notional=10000.0; gP.commPer100k=4.0; gP.commScale=1.10;
   gP.spreadCap=1.76; gP.freshMs=3600000; gP.maxLegs=3;

   for(int i=0;i<ArraySize(FX);i++) eng.addSym(FX[i],FX[i]);
   eng.buildPaths(3);
   PrintFormat("MC start : %d sym, %d paths, trials=%d seed=%d",
               eng.nSym,eng.nPaths,InpTrials,InpSeed);

   long T=1700000000000;
   int nCoh=0,nInj=0;
   int cohPos=0;                     // violations : net>=0 coherent
   double cohMax=-1e18,cohSum=0;
   int injDet=0,injGoodPath=0;       // injections detectees / sur bon chemin
   int injPathNoPair=0;
   double injMaxNet=-1e18,injMinNet=1e18;
   int legHist[4]={0,0,0,0};         // argmax L distribution (injectes detectes)
   double injNetSum=0;

   for(int k=0;k<InpTrials;k++)
   {
      bool inject = rnd()<InpInjectP;
      int  ij = inject? (int)(rnd()*eng.nSym) : -1;
      double dlt = inject ? (0.0005+rnd()*(InpMaxInjBp*1e-4-0.0005))
                          * (rnd()<0.5?1.0:-1.0) : 0.0;
      drawBook(T,ij,dlt);
      double net,ddd; int pi;
      eng.evalAll(T,gP,gMed,net,pi,ddd);
      if(!inject)
      {
         nCoh++; cohSum+=net; if(net>cohMax) cohMax=net;
         if(net>=0) cohPos++;
      }
      else
      {
         nInj++; injNetSum+=net;
         if(net>injMaxNet) injMaxNet=net;
         if(net<injMinNet) injMinNet=net;
         if(net>=0.27)
         {
            injDet++;
            if(pi>=0){ int L=eng.pL[pi]; if(L>=1&&L<=3) legHist[L]++; }
            bool has=false;
            if(pi>=0)
            {
               for(int li=0;li<eng.pL[pi];li++)
                  if(eng.pE[pi][li]==ij) has=true;
               // paire injectee employee comme jambe USD d'entree/sortie
               if(!has && eng.pS[pi]!=0  && eng.usdSym[eng.pS[pi]]==ij)   has=true;
               if(!has && eng.pEnd[pi]!=0 && eng.usdSym[eng.pEnd[pi]]==ij) has=true;
            }
            if(has) injGoodPath++; else injPathNoPair++;
         }
      }
   }

   PrintFormat("COHERENT  n=%d  mean=%.4f  max=%.4f  violations(net>=0)=%d",
               nCoh,cohSum/MathMax(1,nCoh),cohMax,cohPos);
   PrintFormat("INJECT    n=%d  det=%d (%.1f%%)  min=%.3f mean=%.3f max=%.3f",
               nInj,injDet,100.0*injDet/MathMax(1,nInj),
               injMinNet,injNetSum/MathMax(1,nInj),injMaxNet);
   PrintFormat("INJPATH   argmax contient paire injectee : %d/%d ; "
               "L1=%d L2=%d L3=%d",
               injGoodPath,injDet,legHist[1],legHist[2],legHist[3]);

   // verdict
   bool ok = cohPos==0 && injDet>0 && injGoodPath>=injDet*0.95;
   PrintFormat("MC VERDICT %s  (cohPos=%d det=%d good=%d/%d)",
               ok?"PASS":"FAIL",cohPos,injDet,injGoodPath,injDet);
   return INIT_SUCCEEDED;
}
void OnTick(){}
//+------------------------------------------------------------------+
