//+------------------------------------------------------------------+
//| OgExecTest.mq5 - tests de la couche execution stateful d'OgArb    |
//| Script OnStart : aucune venue requise (tester "Math calculations" |
//| ou chart - les tests sensibles a l'environnement sont gardes).    |
//| Protocole identique a OgSelfTest : "TEST <nom> PASS|FAIL detail". |
//|                                                                 |
//| OgArb.mq5 est un EA : ses handlers sont renommes a l'include      |
//| (OgArb_*) pour appeler ses fonctions REELLES sans executer son    |
//| OnInit (journal/timer/adoption). sendLeg/execBasket/snipeScalp    |
//| ne sont testes que via leurs gardes - jamais le chemin d'envoi    |
//| d'ordre : si la venue repondait, un ordre reel partirait.         |
//|                                                                 |
//| Couvert en REEL : resetState, registerBasket, basketPL (vide),    |
//| findLegPos (vide), ticketOf (vide), closeBasket (finalise+reset), |
//| legFail (routage ENV: vs faute -> breaker), medianOf, jsonEsc,    |
//| sessionOk, pathQuotes, evalAll, legRel, legAttribOK, scalpEdge    |
//| (croise dormant), snipeScalp (garde filling), scalpFire/          |
//| execBasket (gardes live), poll (no-feed).                         |
//|                                                                 |
//| NB : gB.tkt[]/allTkt[] stockent des POSITION_IDENTIFIER           |
//| (=DEAL_POSITION_ID) ; ticketOf() resout le ticket courant.        |
//|                                                                 |
//| Couvert en MIROIR (blocs broker-dependants copies a l'identique,  |
//| prefixe m_) :                                                   |
//|  (a) basketPL : un deal ne compte que si DEAL_POSITION_ID matche  |
//|      un allTkt NON NUL            (OgArb.mq5 l.382-384)           |
//|  (b) closeBasket : compaction tkt -> keep ; allTkt conserve le    |
//|      P&L integral du cycle        (OgArb.mq5 l.402-416)           |
//|  (c) watcher scalp found==0 : tkt=0 jamais "gone" avant           |
//|      rescanN>=20 ; seenOpen -> gone direct (OgArb.mq5 l.919-931)  |
//|  (d) clamps OnInit : gWarmMin/gBreakMin>=1, medWin in [16,256],   |
//|      gSc=min(max(eq*2.5,2500),notional)/1e4 (OgArb.mq5 l.763-774) |
//|                                                                 |
//| REFACTOR MINIMAL PROPOSE dans OgArb.mq5 - remplacer ensuite les   |
//| miroirs m_* par ces helpers (signatures exactes) :                |
//|   bool ogBasketOwns(const ulong &allTkt[],int allN,long pid);     |
//|     <- boucle "ours" de basketPL (l.383-384), garde allTkt[k]!=0  |
//|   int  ogCloseCompact(const ulong &tkt[],int n,                   |
//|        const bool &alive[],const bool &closeOk[],bool attempt,    |
//|        ulong &keep[],int &closed);                                |
//|     <- boucle jambes de closeBasket (l.404-416) ; alive[k] =      |
//|        (ticketOf(tkt[k])!=0), closeOk[k] = PositionClose(ct)      |
//|   bool ogCloseAttempt(long closeFails); // l.403 cf<40||%120==0   |
//|   int  ogScalpRescanStep(bool seenOpen,int &rescanN,              |
//|        ulong relinkTkt,int maxRescan);    // l.919-931            |
//|     + #define OG_RESCAN_MAX 20 a la place du litteral 20          |
//|   int  ogClampMin1(int v);                  // l.764-765          |
//|   int  ogClampMedWin(int v);                // l.763              |
//|   double ogScaleScalp(double eq,double notional); // l.773-774    |
//|   Optionnel (refactor plus large, exits du watcher) :             |
//|   int  ogScalpExit(const OgBasket &b,double fp,double commRT,     |
//|        long held,long holdMs,int minHoldMs,double netMin,         |
//|        double relNow,bool relOk,bool sessOk,int &badEvals);       |
//|     <- decisions stop/hold/session/tpnet/conv/invalid (l.934-956) |
//|   int  ogBasketExit(const OgBasket &b,long held,double nNow,      |
//|        double dNow,double cX,bool evalOk,bool sessOk,             |
//|        int &badEvals,double unwFloor,double divStop,              |
//|        double trailBack);                       // l.958-977      |
//+------------------------------------------------------------------+
#property copyright "OpenGraph"
#property version   "1.00"

#include "OgCore.mqh"

// handlers de l'EA inclus -> fonctions ordinaires : rien ne s'auto-execute.
// OgArb.mq5 definit OnInit/OnDeinit/OnTimer (pas d'OnTick ; le rename est
// preventif pour le jour ou il en ajoute un).
#define OnInit    OgArb_OnInit
#define OnDeinit  OgArb_OnDeinit
#define OnTimer   OgArb_OnTimer
#define OnTick    OgArb_OnTick
#include "OgArb.mq5"
#undef OnInit
#undef OnDeinit
#undef OnTimer
#undef OnTick

int gPass=0,gFail=0;

void check(bool cond,const string name,const string detail)
{
   if(cond){ gPass++; PrintFormat("TEST %s PASS %s",name,detail); }
   else    { gFail++; PrintFormat("TEST %s FAIL %s",name,detail); }
}

//=== miroirs purs (a remplacer par les og* ci-dessus apres refactor) ===

// (a) appartenance d'un deal au panier : OgArb.mq5 basketPL l.382-384.
//     La garde allTkt[k]!=0 empeche un deal DEAL_POSITION_ID=0 (operations
//     hors position : balance, credit, deal non lie) d'etre accapare par
//     un identifiant non resolu (0) - bug vecu : P&L gonfle par deals
//     etrangers. allTkt[] stocke des POSITION_IDENTIFIER = DEAL_POSITION_ID.
bool mPlOwns(const ulong &allTkt[],int allN,long pid)
{
   for(int k=0;k<allN;k++)
      if(allTkt[k]!=0 && (ulong)pid==allTkt[k]) return true;
   return false;
}
// accumulateur = corps de basketPL une fois l'historique itere
void mBasketPL(const ulong &allTkt[],int allN,
               const long &pid[],const double &pf[],const double &cm[],
               const double &sw[],double &pl,double &comm,double &swap,int &nd)
{
   pl=0; comm=0; swap=0; nd=0;
   for(int i=0;i<ArraySize(pid);i++)
   {
      if(!mPlOwns(allTkt,allN,pid[i])) continue;
      pl+=pf[i]; comm+=cm[i]; swap+=sw[i]; nd++;
   }
}

// (b) throttle + compaction de closeBasket : OgArb.mq5 l.402-416.
//     alive[k]  = ticketOf(tkt[k])!=0        (position encore vivante)
//     closeOk[k]= gTrade.PositionClose(ct)   (ct = ticket courant)
//     Les jambes disparues tombent de tkt MAIS restent dans allTkt :
//     c'est ce qui preserve le P&L des jambes deja fermees.
bool mCloseAttempt(long cf){ return cf<40 || cf%120==0; }   // l.403
int mCloseCompact(const ulong &tkt[],int n,
                  const bool &alive[],const bool &closeOk[],bool attempt,
                  ulong &keep[],int &closed)
{
   int remain=0; closed=0;
   for(int k=0;k<n;k++)
   {
      ulong t=tkt[k];
      if(t==0) continue;             // identifiant jamais lie -> rien a faire
      if(!alive[k]) continue;        // deja ferme -> sort de tkt (reste dans allTkt)
      if(attempt && closeOk[k]) closed++;
      else keep[remain++]=t;         // retry au prochain tick
   }
   return remain;                     // si >0 : gB.tkt=keep, gB.n=remain
}

// (c) watcher scalp, branche found==0 : OgArb.mq5 l.919-931.
//     seenOpen empeche de liberer un panier dont le fill async n'a pas
//     encore ete relie (tkt=0) : on rescane jusqu'a maxRescan (=20).
#define OG_SCAN_WAIT  0
#define OG_SCAN_ADOPT 1                  // ticket retrouve -> tkt/allTkt, seenOpen
#define OG_SCAN_GONE  2                  // closeBasket("gone")
int mRescanStep(bool seenOpen,int &rescanN,ulong relinkTkt,int maxRescan)
{
   if(seenOpen)                 return OG_SCAN_GONE;
   if(relinkTkt!=0)             return OG_SCAN_ADOPT;
   if(++rescanN>=maxRescan)     return OG_SCAN_GONE;
   return OG_SCAN_WAIT;
}

// (d) clamps OnInit : OgArb.mq5 l.763-765 et scaling gSc l.773-774
int mClamp1(int v){ return (int)MathMax(1,v); }
int mClampWin(int v){ return (int)MathMin(MathMax((double)v,16.0),256.0); }
double mGsc(double eq,double notional)
{ return MathMin(MathMax(eq*2.5,2500.0),notional)/10000.0; }

//=== fixtures (identiques a OgSelfTest) ===
string FX[]={
   "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
   "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD","GBPJPY",
   "GBPCHF","GBPAUD","GBPCAD","GBPNZD","AUDJPY","AUDCHF","AUDCAD",
   "AUDNZD","CADJPY","CADCHF","CHFJPY","NZDJPY","NZDCAD","NZDCHF"};
double U[8] = {1.0, 1.10, 1.25, 1.0/150.0, 1.0/0.90, 1.0/1.36, 0.65, 0.60};

void fillBook(long t,double relSpr)
{
   for(int i=0;i<eng.nSym;i++)
   {
      double mid=U[eng.symA[i]]/U[eng.symB[i]];
      eng.setQuote(i,mid*(1.0-relSpr/2.0),mid*(1.0+relSpr/2.0),t);
   }
}
int symIdx(const string def)
{
   for(int i=0;i<eng.nSym;i++)
      if(StringSubstr(eng.symName[i],StringLen(eng.symName[i])-6,6)==def) return i;
   return -1;
}

//+------------------------------------------------------------------+
void OnStart()
{
   long T=1700000000000;

   //---------- T0 : resetState (REEL) - le fix "globales persistentes" ---
   gB.open=true; gB.n=4; gB.allN=4; gB.tkt[0]=111; gB.allTkt[0]=111;
   gB.seenOpen=true; gB.rescanN=7; gB.legSym="EURJPY"; gB.legDeal=55;
   gB.legE=3; gB.legFrom=2; gB.relIn=9.9;
   gB.closeFails=3; gB.closedN=2; gFails=9; gFeedMs=123;
   sprLen[0]=5; sprBuf[0][0]=0.1; medArr[0]=0.2; symSeen[0]=77; symDead[0]=true;
   resetState();                                                // REEL
   check(!gB.open && gB.n==0 && gB.allN==0 && gB.pi==-1 && !gB.seenOpen
         && gB.rescanN==0 && gB.legSym=="" && gB.legDeal==0 && gB.closeFails==0
         && gB.closedN==0 && gFails==0 && gFeedMs==0,
         "reset_state","panier + compteurs remis a zero");
   check(gB.legE==-1 && gB.legFrom==-1 && gB.relIn==0,
         "reset_leg","contexte jambe scalp reinitialise");
   check(symSeen[0]==0 && !symDead[0] && sprLen[0]==0 && medArr[0]==0
         && eng.nSym==0,"reset_sym","buffers symboles + moteur vides");

   // univers + chemins reels pour la suite (pur OgCore, pas de venue)
   for(int i=0;i<28;i++) eng.addSym(FX[i],FX[i]);
   eng.buildPaths(3);

   //---------- T1 : registerBasket (REEL) - copie allTkt ----------------
   gB.tkt[0]=101; gB.tkt[1]=0; gB.tkt[2]=303; gB.n=3;   // jambe 1 non liee
   registerBasket(0,1.234,false,true,T);                          // REEL
   check(gB.open && gB.pi==0 && gB.allN==3 &&
         gB.allTkt[0]==101 && gB.allTkt[1]==0 && gB.allTkt[2]==303,
         "register_alltkt","allTkt=copie tkt(ids), trous (0) conserves");
   check(gB.seenOpen==false && gB.rescanN==0 && gB.closeFails==0 &&
         gB.closedN==0 && gB.full==true && gB.scalp==false &&
         MathAbs(gB.ein-1.234)<1e-9 && gB.minNet==0 && gB.badEvals==0,
         "register_fields","compteurs frais a l'enregistrement");

   //---------- T2 (a) : basketPL ignore DEAL_POSITION_ID=0 -------------
   // deals synthetiques : 2 deals pid=0 (balance/credit, +999/+888$),
   // 1 deal etranger (pid 999), 3 deals du panier (101, 303 x2).
   long  pid[]={   0,  101,  303,   0,   999,  303};
   double pf[]={999.0, -1.5, +4.0, 888.0,+2.0, -0.8};
   double cm[]={  0.0, -0.4, -0.4,  0.0, -0.4, -0.4};
   double sw[]={  0.0,  0.0, -0.1,  0.0,  0.0,  0.0};
   double pl,comm,swap; int nd;
   mBasketPL(gB.allTkt,gB.allN,pid,pf,cm,sw,pl,comm,swap,nd);
   check(nd==3 && MathAbs(pl-1.7)<1e-9 && MathAbs(comm+1.2)<1e-9
         && MathAbs(swap+0.1)<1e-9,
         "pl_skip_zero",
         StringFormat("pl=%.2f comm=%.2f swap=%.2f nd=%d - pid=0 ignores",pl,comm,swap,nd));
   // reel : historique vide/inatteignable -> aucun deal compte, pas de crash
   gB.allN=0;
   basketPL(pl,comm,swap,nd);                                     // REEL
   check(nd==0 && pl==0 && comm==0 && swap==0,"pl_empty","allN=0 -> rien");
   gB.allTkt[0]=7777777777777; gB.allN=1;         // pid impossible en vrai
   basketPL(pl,comm,swap,nd);                                     // REEL
   check(nd==0,"pl_nomatch","ticket fictif sans deal -> nd=0");

   //---------- T3 (b) : compaction tkt / P&L conserve via allTkt -------
   ulong tk4[OG_MAX_BASKET_LEGS];
   tk4[0]=5501; tk4[1]=5502; tk4[2]=5503; tk4[3]=5504;
   bool sel4[4]={true,true,true,false};   // 5504 deja ferme (SL/main) -> drop
   bool clo4[4]={true,false,true,false};  // 5502 refuse -> keep retry
   ulong keep[OG_MAX_BASKET_LEGS]; int closed=0;
   int rem=mCloseCompact(tk4,4,sel4,clo4,true,keep,closed);
   check(rem==1 && closed==2 && keep[0]==5502,
         "compact_keep","2 fermees, 1 retry (5502), 5504 purgee de tkt");
   // allTkt n'est JAMAIS retouche par la compaction -> P&L integral
   for(int k=0;k<4;k++) gB.allTkt[k]=tk4[k];
   gB.allN=4;
   long  pid2[]={5501,5501,5502,5502,5503,5503,5504,5504};
   double pf2[]={-0.4,+1.0,-0.4,-2.0,-0.4,+0.6,-0.4,+0.3};
   double cm2[8]={0,0,0,0,0,0,0,0};
   double sw2[8]={0,0,0,0,0,0,0,0};
   mBasketPL(gB.allTkt,gB.allN,pid2,pf2,cm2,sw2,pl,comm,swap,nd);
   check(nd==8 && MathAbs(pl+1.7)<1e-9,
         "compact_pl",
         StringFormat("jambes purgees de tkt toujours dans le P&L : nd=%d pl=%.2f",nd,pl));
   // throttle : attempt=false -> rien ne ferme, tout reste a retry
   rem=mCloseCompact(tk4,4,sel4,clo4,false,keep,closed);
   check(rem==3 && closed==0 && keep[0]==5501 && keep[2]==5503,
         "compact_throttle","attempt=false : tous les tickets vivants gardes");
   check(mCloseAttempt(39) && !mCloseAttempt(40) && !mCloseAttempt(41)
         && !mCloseAttempt(119) && mCloseAttempt(120) && mCloseAttempt(240),
         "close_attempt","cf<40 ou cf%120==0");
   // reel : jambes introuvables -> finalize + reset complet du panier
   gB.open=true; gB.pi=0; gB.n=2; gB.tkt[0]=999999; gB.tkt[1]=0;
   gB.allTkt[0]=999999; gB.allTkt[1]=0; gB.allN=2; gB.path="test";
   closeBasket("test");                                           // REEL
   check(!gB.open && gB.n==0 && gB.allN==0 && gB.pi==-1 && !gB.seenOpen
         && gB.rescanN==0 && gB.closeFails==0 && gB.closedN==0,
         "close_reset","positions introuvables -> finalise + reset");

   //---------- T4 (c) : rescan / seenOpen (litteral 20 du watcher) -----
   int rn=0,act=OG_SCAN_WAIT; bool early=false;
   for(int i=0;i<19;i++)
   { act=mRescanStep(false,rn,0,20); if(act==OG_SCAN_GONE) early=true; }
   check(!early && rn==19 && act==OG_SCAN_WAIT,
         "rescan_wait","tkt=0 : 19 rescans sans declarer gone");
   act=mRescanStep(false,rn,0,20);
   check(act==OG_SCAN_GONE && rn==20,"rescan_gone20","gone au 20e rescan");
   rn=5; act=mRescanStep(false,rn,4242,20);
   check(act==OG_SCAN_ADOPT,"rescan_adopt","position relink via legDeal -> adoption");
   rn=0; act=mRescanStep(true,rn,0,20);
   check(act==OG_SCAN_GONE,"rescan_seen","vu ouvert puis absent -> gone immediat");

   //---------- T5 (d) : clamps ----------------------------------------
   check(mClamp1(0)==1 && mClamp1(-7)==1 && mClamp1(1)==1 && mClamp1(42)==42,
         "clamp_min1","warmup/breaker forces >=1");
   check(mClampWin(4)==16 && mClampWin(64)==64 && mClampWin(256)==256
         && mClampWin(999)==256,
         "clamp_medwin","medWin borne [16,256]");
   check(MathAbs(mGsc(0,10000)-0.25)<1e-9 && MathAbs(mGsc(2000,10000)-0.5)<1e-9
         && MathAbs(mGsc(4000,10000)-1.0)<1e-9 && MathAbs(mGsc(1e6,10000)-1.0)<1e-9,
         "clamp_gsc","scalp scale borne [0.25,notional/1e4]");
   check(gWarmMin==1 && gBreakMin==1 && gSc==1.0,
         "clamp_state","globales bornees a l'init (OgArb_OnInit non lance)");

   //---------- T6 : routage legFail ENV: vs faute (REEL) --------------
   gBreakMin=99;                                   // evite ExpertRemove
   int f0=gFails;
   legFail(0,"entry","ENV:EURJPY trade_mode=0");                    // REEL
   check(gFails==f0,"legfail_env","refus ENV: -> breaker inchange");
   legFail(0,"entry","requote");                                    // REEL
   check(gFails==f0+1,"legfail_real","faute reelle -> breaker++");

   //---------- T7 : helpers purs reels --------------------------------
   check(jsonEsc("a\"b")=="a\\\"b" && jsonEsc("x\\y")=="x\\\\y"
         && jsonEsc("l\nm")=="l\\nm","json_esc","echappement JSON");
   sprLen[9]=0;
   check(medianOf(9)==0,"median_empty","mediane vide=0");
   sprBuf[9][0]=3; sprBuf[9][1]=1; sprBuf[9][2]=2; sprLen[9]=3;
   check(MathAbs(medianOf(9)-2.0)<1e-9,"median_odd","{3,1,2}->2");
   sprBuf[9][3]=4; sprLen[9]=4;
   check(MathAbs(medianOf(9)-2.5)<1e-9,"median_even","{1,2,3,4}->2.5");
   check(sessionOk(),"session_default","from=to=-1 -> toujours");

   //---------- T8 : scalpEdge / legAttribOK (REEL, pur hors SymbolInfo*)
   OgParams P;
   P.notional=10000.0; P.commPer100k=4.0; P.commScale=1.10;
   P.spreadCap=1.76; P.freshMs=8000; P.skewMs=0; P.maxLegs=3;
   fillBook(T,1e-4);
   int ij=symIdx("EURJPY");
   eng.setQuote(ij,eng.bidP[ij]+0.75,eng.askP[ij]+0.75,T);  // dislocation +0.75
   for(int i=0;i<eng.nSym;i++) pxTick[i]=T;                 // tout vivant
   pxTick[ij]=T-2000;                                       // EURJPY dormant (>1000ms)
   double net,ddd; int pi=-1;
   bool ev=eng.evalAll(T,P,medArr,net,pi,ddd);              // medArr=0 -> floor off
   int lidx=-1;
   for(int li=0;ev&&li<eng.pL[pi];li++) if(eng.pE[pi][li]==ij) lidx=li;
   bool via=(lidx>=0);
   check(ev && via,"inject_path",ev?eng.pathStr(pi):"evalAll a echoue");
   // direction attendue : le croise dormant ne porte l'edge que si on le
   // VEND (from=EUR) ; a l'achat (from=JPY) l'attrib est negative -> refus
   int fexp=-1; bool sellExp=false;
   if(via)
   {
      if(lidx==0) fexp=eng.pS[pi];
      else { int ep=eng.pE[pi][lidx-1];
             fexp=(eng.pDir[pi][lidx-1]==1)?eng.symB[ep]:eng.symA[ep]; }
      sellExp=(fexp==eng.symA[ij]);
   }
   int e=-1,from=-1; double attrib=-1e18,fX=0; string seWhy;
   bool se=false;
   if(pi>=0) se=scalpEdge(pi,T,InpStalePxMs,e,from,attrib,fX,seWhy); // REEL
   check(via && se==sellExp && (!se||(e==ij && from==fexp && attrib>0)),
         "scalp_edge",
         StringFormat("se=%d e=%s from=%s attrib=%.2f sellAttendu=%d",
                      se,se?eng.symName[e]:"-",fexp>=0?eng.CURR[fexp]:"-",
                      attrib,sellExp));
   double aUsd=-1; int lwhy=0;
   bool la=legAttribOK(pi,ij,fexp,1.0,aUsd,fX,T,lwhy);      // REEL
   check(la==sellExp && (!la || aUsd>0),
         "leg_attrib",StringFormat("attrib=%.2f$",aUsd));
   // ancre USD dormante = le vrai laggard est ailleurs -> fair contamine -> refus
   int iJ=eng.usdSym[eng.symB[ij]];                          // USDJPY
   pxTick[iJ]=T-2000;
   la=legAttribOK(pi,ij,fexp,1.0,aUsd,fX,T,lwhy);           // REEL
   check(!la && lwhy==1,"leg_attrib_anchor","ancre USDJPY dormante -> refus why=1");
   pxTick[iJ]=T;
   //---------- T8b : legRel (REEL) - prix juste via ancres USD ----------
   double rel=999.0;
   check(!legRel(0,0,rel,fX,T) && rel==0,"legrel_usdleg","jambe USD (EURUSD) -> false");
   if(via)
   {
      double rel1=999.0;
      check(legRel(ij,fexp,rel1,fX,T) && MathAbs(rel1)>0.002,
            "legrel_dir",StringFormat("rel=%.5f",rel1));     // REEL
      int opp=(fexp==eng.symA[ij])?eng.symB[ij]:eng.symA[ij];
      check(legRel(ij,opp,rel,fX,T) && rel1*rel<0,
            "legrel_opp",StringFormat("sens oppose rel=%.5f (vs %.5f)",rel,rel1));
      eng.lastT[iJ]=T-9000;                                 // ancre stale (>InpFreshMs)
      check(!legRel(ij,fexp,rel,fX,T),"legrel_stale","ancre stale -> false");
      eng.lastT[iJ]=T;
      eng.setQuote(ij,-1.0,165.0,T);                        // quote e invalide
      check(!legRel(ij,fexp,rel,fX,T),"legrel_badquote","bid<=0 -> false");
      eng.setQuote(ij,U[1]/U[3]+0.75-5e-5,U[1]/U[3]+0.75+5e-5,T); // restaure ~disloque
   }

   //---------- T9 : gardes (REEL, sans envoi d'ordre) ------------------
   int n0=gB.n;
   scalpFire(pi,5.0,T);                                     // REEL
   check(!gB.open && gB.n==n0,"scalpfire_guard","InpAllowLive=false -> no-op");
   check(!execBasket(pi,T,1.0) && !gB.open,                 // REEL
         "execbasket_guard","detect-only -> refuse avant ordres");
   // snipeScalp : seulement quand le mode de filling garantit la sortie
   // avant tout OrderSend (venue absente -> fm=0 -> ni IOC ni FOK)
   long fm=SymbolInfoInteger(eng.symName[ij],SYMBOL_FILLING_MODE);
   if(pi>=0 && se && (fm&3)==0)
   {
      string serr; double a2=0,f2=0;
      bool so=snipeScalp(pi,5.0,T,serr,a2,f2);              // REEL
      check(!so && StringFind(serr,"filling")>=0,"snipe_guard",
            "filling ni IOC ni FOK -> refus avant envoi");
   }
   else Print("TEST snipe_guard SKIP venue reelle ou direction non porteuse");
   // deterministe seulement sans positions ni historique (le deal 123
   // pourrait exister sur une vraie venue -> pid reel retourne)
   bool histEmpty=!HistorySelect(0,TimeTradeServer()+60)||HistoryDealsTotal()==0;
   if(PositionsTotal()==0)
   {
      check(ticketOf(7777777777777)==0,"ticketof_none","identifier inconnu -> 0"); // REEL
      if(histEmpty)
      {
         check(findLegPos("EURUSD",0)==0,"findleg_empty","scan vide -> 0");
         check(findLegPos("EURUSD",123)==0,"findleg_nodeal","deal absent + scan vide -> 0");
      }
      else Print("TEST findleg_* SKIP historique present");
   }
   else Print("TEST findleg_*/ticketof SKIP positions presentes");
   string q=(pi>=0)?pathQuotes(pi):"";                      // REEL
   check(pi<0 || StringLen(q)>0,"pathquotes",q);

   //---------- T10 : poll sans flux (REEL, garde environnement) -------
   MqlTick tkq;
   if(!SymbolInfoTick(eng.symName[ij],tkq))
   {
      poll();                                              // REEL
      check(eng.bidP[ij]==0 && eng.askP[ij]==0,"poll_nofeed",
            "symbol sans tick -> quote mise a zero (fail-closed)");
   }
   else Print("TEST poll_nofeed SKIP venue live");

   PrintFormat("RESULT %d PASS / %d FAIL",gPass,gFail);
}
//+------------------------------------------------------------------+
