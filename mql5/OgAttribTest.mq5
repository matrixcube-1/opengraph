//+------------------------------------------------------------------+
//| OgAttribTest.mq5 — tests du garde anti-adverse legAttribOK +      |
//| scalpEdge (OgArb.mq5, mode scalp snipe lead-lag).                 |
//|                                                                   |
//| POURQUOI UN MIROIR : legAttribOK/scalpEdge vivent dans l'EA, pas  |
//| dans OgCore.mqh. Ils lisent pxTick[] (globale OgArb.mq5:78, ecrite|
//| par poll() au changement de PRIX), les inputs InpFreshMs/         |
//| InpStalePxMs/InpNotionalUSD et SymbolInfoDouble(sym,VOLUME_STEP). |
//| Non testables hors EA -> copie MIROIR ci-dessous, etat            |
//| entierement injectable par le test (setQ pousse bid/ask/lastT +   |
//| pxTick independamment, comme le font les vrais flux).             |
//|                                                                   |
//| DIVERGENCE miroir <-> EA (unique) :                               |
//|  - SymbolInfoDouble(eng.symName[e],SYMBOL_VOLUME_STEP) est        |
//|    remplace par gVstep=0.01 : hors Market Watch la valeur n'est   |
//|    pas deterministe. La ligne EA est conservee en commentaire.    |
//| Tout le reste est verbatim OgArb.mq5:504-559.                     |
//|                                                                   |
//| Sortie : "TEST <nom> PASS|FAIL <detail>" + RESULT p/f.            |
//| Les tests div_* documentent des TROUS du garde trouves a la       |
//| lecture (ils PASSENT tant que le trou existe) — si un div_* FAIL, |
//| l'EA a ete corrige : resynchroniser le miroir.                    |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.00"
#include "OgCore.mqh"

COgEngine eng;                                   // meme nom global que OgArb.mq5:69
int gPass=0, gFail=0;

//--- etat EA mirrorie (injectable) -------------------------------------
// MIROIR de OgArb.mq5:78 — time_msc du dernier changement de PRIX.
// En live seul poll() l'ecrit ; le test le pousse via setQ().
long   pxTick[OG_MAX_SYMS];
// MIROIR des inputs EA lus par le garde (defauts OgArb.mq5)
long   InpFreshMs     = 8000;                    // OgArb.mq5:24  input long
int    InpStalePxMs   = 1000;                    // OgArb.mq5:53  input int
double InpNotionalUSD = 10000.0;                 // OgArb.mq5:18  input double
// STUB de SymbolInfoDouble(sym,SYMBOL_VOLUME_STEP) — voir en-tete
double gVstep = 0.01;

void check(bool cond,const string name,const string detail)
{
   if(cond){ gPass++; PrintFormat("TEST %s PASS %s",name,detail); }
   else    { gFail++; PrintFormat("TEST %s FAIL %s",name,detail); }
}

//+------------------------------------------------------------------+
// MIROIR de OgArb.mq5:494-532 (commentaire + corps legAttribOK)
//+------------------------------------------------------------------+
// MIROIRS resynchronises 16/11 sur OgArb.mq5:522-611 (legRel +
// legAttribOK + scalpEdge) — verbatim, sauf vstep (stub gVstep, voir
// en-tete). Verifie a chaque resync : diff OgArb.mq5 vs ce bloc.
//+------------------------------------------------------------------+
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

bool legAttribOK(int pi,int e,int from,double k,double &attribUsd,double &F,long tMs,int &why)
{
   attribUsd=-1e18; F=0; why=0;
   int a=eng.symA[e];
   double rel;
   if(!legRel(e,from,rel,F,tMs)){ why=1; return false; }
   int xsA=eng.usdSym[a];
   double aToUsd=(eng.usdDir[a]==1)? eng.bidP[xsA] : 1.0/eng.askP[xsA];
   //--- DIVERGENCE TEST : la ligne EA est
   // double vstep=SymbolInfoDouble(eng.symName[e],SYMBOL_VOLUME_STEP);
   double vstep=gVstep;                               // STUB (voir en-tete)
   if(vstep<=0) vstep=0.01;
   double lots=MathFloor(InpNotionalUSD/aToUsd/100000.0/vstep)*vstep;
   attribUsd=InpNotionalUSD*rel;
   if(!(attribUsd>=k*lots*7.0)){ why=2; return false; }
   return true;
}

bool scalpEdge(int pi,long tMs,long staleMs,int &e,int &from,double &attribUsd,double &F,string &why)
{
   attribUsd=-1e18; F=0;
   int nl=eng.pL[pi];
   if(nl<1||nl>3){ why="pL invalide"; return false; }
   double bestA=1.5*7.0*0.1;
   int be=-1, bf=-1;
   long maxDorm=0; int nDorm=0,nAnchKo=0; double bestFail=0;
   for(int i=nl-1;i>=0;i--)
   {
      int ei=eng.pE[pi][i];
      if(eng.symA[ei]==0||eng.symB[ei]==0) continue;
      long dorm=tMs-pxTick[ei];
      if(dorm>maxDorm) maxDorm=dorm;
      if(dorm<staleMs) continue;
      nDorm++;
      int f;
      if(i==0) f=eng.pS[pi];
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
      if(aUsd>bestA){ bestA=aUsd; be=ei; bf=f; F=fX; }
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

//+------------------------------------------------------------------+
//| Harnais d'injection (meme protocole que OgSelfTest.mq5)            |
//+------------------------------------------------------------------+
// prix USD par unite de devise (livre coherent)
double Uv[8] = {1.0, 1.10, 1.25, 1.0/150.0, 1.0/0.90, 1.0/1.36, 0.65, 0.60};

// pousse bid/ask/lastT dans le moteur + pxTick dans l'etat EA mirrorie.
// En live lastT bouge a CHAQUE tick alors que pxTick ne bouge qu'au
// changement de PRIX : le test controle les deux independamment pour
// distinguer "flux vivant" (lastT frais) de "prix dormant" (pxTick vieux).
void setQ(int i,double bid,double ask,long t,long pxT)
{
   eng.setQuote(i,bid,ask,t);
   pxTick[i]=pxT;
}

void setCoh(int i,long t,long pxT,double relSpr)
{
   double mid=Uv[eng.symA[i]]/Uv[eng.symB[i]];
   setQ(i,mid*(1.0-relSpr/2.0),mid*(1.0+relSpr/2.0),t,pxT);
}

void fillBook3(long t,long pxT,double relSpr)
{
   for(int i=0;i<eng.nSym;i++) setCoh(i,t,pxT,relSpr);
}

int symI(const string def)
{
   for(int i=0;i<eng.nSym;i++) if(eng.symName[i]==def) return i;
   return -1;
}
int findPath1(int s0,int e0)
{
   for(int pi=0;pi<eng.nPaths;pi++)
      if(eng.pS[pi]==s0&&eng.pL[pi]==1&&eng.pE[pi][0]==e0) return pi;
   return -1;
}
int findPath2(int s0,int e0,int e1)
{
   for(int pi=0;pi<eng.nPaths;pi++)
      if(eng.pS[pi]==s0&&eng.pL[pi]==2&&eng.pE[pi][0]==e0&&eng.pE[pi][1]==e1)
         return pi;
   return -1;
}

//+------------------------------------------------------------------+
int OnInit()
{
   long   T=1700000000000;
   long   STALE=InpStalePxMs;            // 1000 ms : dormant >= STALE
   double spr=1e-4;                      // ~1 pip relatif

   //--- univers minimal : triangle USD/EUR/JPY --------------------------
   //    mid : EURUSD=1.10, USDJPY=150, EURJPY=165
   eng.addSym("EURUSD","EURUSD");
   eng.addSym("USDJPY","USDJPY");
   eng.addSym("EURJPY","EURJPY");
   eng.buildPaths(3);                    // 3 noeuds -> 12 chemins L<=2
   int iEU=symI("EURUSD"), iUJ=symI("USDJPY"), iEJ=symI("EURJPY");
   int cUSD=eng.ci("USD"), cEUR=eng.ci("EUR"), cJPY=eng.ci("JPY");
   check(eng.nPaths==12,"paths_tri",IntegerToString(eng.nPaths));

   int piEJsell=findPath1(cEUR,iEJ);        // EUR->JPY : sellBase sur EURJPY
   int piEJbuy =findPath1(cJPY,iEJ);        // JPY->EUR : buyBase
   int piUEJ   =findPath2(cUSD,iEU,iEJ);    // USD->EUR->JPY : croise en leg 1
   int piEUJ   =findPath2(cEUR,iEU,iUJ);    // EUR->USD->JPY : 2 jambes USD
   check(piEJsell>=0&&piEJbuy>=0&&piUEJ>=0&&piEUJ>=0,"paths_found",
         StringFormat("%d %d %d %d",piEJsell,piEJbuy,piUEJ,piEUJ));

   int e,from; double at,fX=0; int lwhy=0; string seWhy; bool ok;

   //=== (1) croise dormant + ancres vivantes + bid decale -> ACCEPT =====
   fillBook3(T,T,spr);                      // coherent, tout vivant
   setQ(iEJ,eng.bidP[iEJ]+0.05,eng.askP[iEJ]+0.05,T,T-2*STALE); // EJ dorm+decale
   double fairBid=eng.bidP[iEU]*eng.bidP[iUJ];
   double atExp=InpNotionalUSD*(eng.bidP[iEJ]/fairBid-1.0);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(ok&&at>1.05&&MathAbs(at-atExp)<0.01,"accept_attrib",
         StringFormat("at=%.3f exp=%.3f",at,atExp));
   ok=scalpEdge(piEJsell,T,STALE,e,from,at,fX,seWhy);
   check(ok&&e==iEJ&&from==cEUR,"pick_L1",StringFormat("e=%d from=%d",e,from));
   ok=scalpEdge(piUEJ,T,STALE,e,from,at,fX,seWhy);      // meme cible via chemin L2
   check(ok&&e==iEJ&&from==cEUR,"pick_L2",StringFormat("e=%d from=%d",e,from));
   // 1b : meme dislocation mais croise VIVANT -> leader, pas laggard
   setQ(iEJ,eng.bidP[iEJ],eng.askP[iEJ],T,T);
   ok=scalpEdge(piEJsell,T,STALE,e,from,at,fX,seWhy);
   check(!ok,"live_leg_skip","croise vivant = pas de tir");
   setQ(iEJ,eng.bidP[iEJ],eng.askP[iEJ],T,T-2*STALE);  // re-dort

   //=== (2) ancre USD dormante -> REFUS =================================
   // lastT FRAIS (le flux vit) mais PRIX fige = le vrai laggard est
   // l'ancre -> fair contamine -> refus, sentinelle -1e18 conservee.
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T,T-2*STALE);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"anchorA_dormant",StringFormat("at=%.1f",at));
   ok=scalpEdge(piEJsell,T,STALE,e,from,at,fX,seWhy);
   check(!ok,"anchorA_dormant_pick","scalpEdge refuse aussi");
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T,T);
   setQ(iUJ,eng.bidP[iUJ],eng.askP[iUJ],T,T-2*STALE);   // l'autre ancre
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"anchorB_dormant",StringFormat("at=%.1f",at));
   setQ(iUJ,eng.bidP[iUJ],eng.askP[iUJ],T,T);
   // variante : ancre hors fenetre de fraicheur (lastT vieux, autre ligne)
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T-InpFreshMs-1,T);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"anchorA_staleT",StringFormat("at=%.1f",at));
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T,T);

   //=== (3) jambe USD -> REFUS (pas de synthese possible) ===============
   ok=legAttribOK(-1,iEU,cEUR,1.0,at,fX,T,lwhy);   // EURUSD : symB==USD
   check(!ok&&at<-1e17,"usdleg_eurusd","b==USD");
   ok=legAttribOK(-1,iUJ,cUSD,1.0,at,fX,T,lwhy);   // USDJPY : symA==USD
   check(!ok&&at<-1e17,"usdleg_usdjpy","a==USD");
   // via scalpEdge : les jambes USD sont skippees AVANT le test de
   // dormance — dormantes ou pas, jamais des croises.
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T,T-5*STALE);
   setQ(iUJ,eng.bidP[iUJ],eng.askP[iUJ],T,T-5*STALE);
   setCoh(iEJ,T,T,spr);                        // croise vivant
   ok=scalpEdge(piEUJ,T,STALE,e,from,at,fX,seWhy);
   check(!ok,"usdlegs_skipped",eng.pathStr(piEUJ));
   setQ(iEU,eng.bidP[iEU],eng.askP[iEU],T,T);
   setQ(iUJ,eng.bidP[iUJ],eng.askP[iUJ],T,T);
   setCoh(iEJ,T,T-2*STALE,spr);                // re-dort le croise, coherent

   //=== (4) dormant mais NON porteur -> REFUS ===========================
   // Les ancres ont REPRICE (+4e-4 rel, pxTick frais = elles ont bouge) ;
   // le croise stale affiche encore l'ancien fair -> cote SELL rel<0.
   double s=4e-4;
   setQ(iEU,eng.bidP[iEU]*(1.0+s),eng.askP[iEU]*(1.0+s),T,T);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<0,"noncarrier_sell",StringFormat("at=%.3f",at));
   ok=scalpEdge(piEJsell,T,STALE,e,from,at,fX,seWhy);
   check(!ok,"noncarrier_pick",eng.pathStr(piEJsell));
   // 4b : MEME livre, chemin inverse JPY->EUR : la dislocation existe
   //    bien, du cote BUY (ask stale < fairAsk) — "porteur" depend du
   //    sens d'entree `from`, pas seulement de la dormance.
   ok=scalpEdge(piEJbuy,T,STALE,e,from,at,fX,seWhy);
   check(ok&&e==iEJ&&from==cJPY,"carrier_buyside",
         StringFormat("e=%d from=%d",e,from));
   setQ(iEU,eng.bidP[iEU]/(1.0+s),eng.askP[iEU]/(1.0+s),T,T);   // restaure

   //=== (5) signe de rel : sellBase lit le BID, buyBase lit l'ASK =======
   // 5a croise decale +0.05 : vendre cher = edge ; acheter cher = rel<0
   setQ(iEJ,eng.bidP[iEJ]+0.05,eng.askP[iEJ]+0.05,T,T-2*STALE);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(ok&&at>0,"sign_sell_up",StringFormat("at=%.3f",at));
   ok=legAttribOK(piEJbuy,iEJ,cJPY,1.0,at,fX,T,lwhy);
   check(!ok&&at<0,"sign_buy_up",StringFormat("at=%.3f",at));
   // 5b croise a -0.05 sous coherent : acheter sous fair = edge ;
   //    vendre sous fair = rel<0
   setQ(iEJ,eng.bidP[iEJ]-0.10,eng.askP[iEJ]-0.10,T,T-2*STALE);
   ok=legAttribOK(piEJbuy,iEJ,cJPY,1.0,at,fX,T,lwhy);
   check(ok&&at>0,"sign_buy_dn",StringFormat("at=%.3f",at));
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<0,"sign_sell_dn",StringFormat("at=%.3f",at));
   // 5c coherent : rel sell ~ +5e-5 >0 MAIS < k*commLeg (~0.63$) ->
   //    refus de SEUIL (at>0 conserve), pas de signe
   setQ(iEJ,eng.bidP[iEJ]+0.05,eng.askP[iEJ]+0.05,T,T-2*STALE); // coherent
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at>0&&at<1.0,"threshold_not_sign",StringFormat("at=%.3f",at));

   //=== (6) matrice de gardes ancres (chacun vise une ligne) ============
   setQ(iUJ,0.0,0.0,T,T);
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"guard_bidB0","bidP[xsB]=0");
   setQ(iUJ,149.99,149.98,T,T);                  // ancre book croise
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"guard_crossedB","askP<bidP ancre");
   setQ(iUJ,149.9925,150.0075,0,T);              // jamais ticke
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(!ok&&at<-1e17,"guard_lastT0","lastT=0");
   setCoh(iUJ,T,T,spr);                           // restaure

   //=== (7) DIVERGENCES constatees (trous du garde) ======================
   // d1 : la quote de la JAMBE e n'est jamais validee — askP[e]=0 en
   //    buyBase -> rel=+inf -> attrib +inf : un fantome gagnerait
   //    l'argmax de scalpEdge (snipeScalp le rattrape via "quote nulle",
   //    mais le vrai croise perd son tour sur ce tick).
   setQ(iEJ,eng.bidP[iEJ],0.0,T,T-2*STALE);
   ok=legAttribOK(piEJbuy,iEJ,cJPY,1.0,at,fX,T,lwhy);
   check(ok&&at>1e15,"div_leg_ask0",StringFormat("at=%.3e",at));
   // d2 : book croise (bid>ask) sur la jambe accepte sans broncher.
   double bEJ=eng.bidP[iEJ];
   setQ(iEJ,bEJ+0.10,bEJ,T,T-2*STALE);            // bid > ask
   ok=legAttribOK(piEJsell,iEJ,cEUR,1.0,at,fX,T,lwhy);
   check(ok&&at>0,"div_leg_crossed",StringFormat("at=%.3f",at));
   setCoh(iEJ,T,T-2*STALE,spr);

   PrintFormat("RESULT %d PASS / %d FAIL",gPass,gFail);
   return INIT_SUCCEEDED;
}

void OnTick(){}
//+------------------------------------------------------------------+
