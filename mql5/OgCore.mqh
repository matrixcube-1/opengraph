//+------------------------------------------------------------------+
//| OgCore.mqh — moteur open-path FX (port Java Engine/PathTables)    |
//| Pur : aucun appel terminal sauf Print. Testable hors-venue.       |
//| Convention A : panier complet [USD->s0]+jambes+[end->USD],        |
//| bid/ask executables, comm x2 + unwind par position.               |
//+------------------------------------------------------------------+
#ifndef OG_CORE_MQH
#define OG_CORE_MQH

#define OG_N_CURR    8
#define OG_MAX_LEGS  3
#define OG_MAX_PATHS 8192
#define OG_MAX_SYMS  64

struct OgParams
{
   double notional;      // USD par panier
   double commPer100k;   // $/cote/100K USD
   double commScale;     // multiplicateur
   double spreadCap;     // cap spread x mediane
   long   freshMs;       // fraicheur max (ms)
   long   skewMs;        // skew max inter-jambes (ms, 0=off) — anti lead-lag
   int    maxLegs;       // jambes max
};

class COgEngine
{
public:
   string CURR[OG_N_CURR];
   int    nCurr;

   // symboles
   string symName[OG_MAX_SYMS];
   int    symA[OG_MAX_SYMS], symB[OG_MAX_SYMS];
   int    nSym;

   // quotes courantes (poussees par l'appelant)
   double bidP[OG_MAX_SYMS], askP[OG_MAX_SYMS];
   long   lastT[OG_MAX_SYMS];        // epoch ms du dernier tick

   // chemins
   int    pS[OG_MAX_PATHS];
   int    pEnd[OG_MAX_PATHS];
   int    pL[OG_MAX_PATHS];
   int    pE[OG_MAX_PATHS][OG_MAX_LEGS];
   int    pDir[OG_MAX_PATHS][OG_MAX_LEGS];
   int    nPaths;

   // paire USD par devise
   int    usdSym[OG_N_CURR];
   int    usdDir[OG_N_CURR];         // 1 = cUSD (direct), 0 = USDc

   void COgEngine()
   {
      CURR[0]="USD"; CURR[1]="EUR"; CURR[2]="GBP"; CURR[3]="JPY";
      CURR[4]="CHF"; CURR[5]="CAD"; CURR[6]="AUD"; CURR[7]="NZD";
      nCurr=OG_N_CURR;
      nSym=0; nPaths=0;
      for(int i=0;i<OG_MAX_SYMS;i++){ bidP[i]=0; askP[i]=0; lastT[i]=0; }
      for(int c=0;c<OG_N_CURR;c++){ usdSym[c]=-1; usdDir[c]=0; }
   }

   int ci(const string c)
   {
      for(int i=0;i<nCurr;i++) if(CURR[i]==c) return i;
      return -1;
   }

   // remet le moteur a zero (obligatoire en OnInit : les globales
   // MQL5 PERSISTENT entre les re-init F7/timeframe)
   void reset()
   {
      nSym=0; nPaths=0;
      for(int i=0;i<OG_MAX_SYMS;i++)
      {
         symName[i]=""; symA[i]=0; symB[i]=0;
         bidP[i]=0; askP[i]=0; lastT[i]=0;
      }
      for(int c=0;c<OG_N_CURR;c++){ usdSym[c]=-1; usdDir[c]=0; }
   }

   // enregistre une paire def "EURUSD" + nom broker resolu
   // retour : index, ou l'index existant si la paire (a,b) est deja la
   int addSym(const string def, const string resolved)
   {
      int a=ci(StringSubstr(def,0,3)), b=ci(StringSubstr(def,3,3));
      if(a<0||b<0||a==b) return -1;
      for(int i=0;i<nSym;i++)                    // dedup sur la paire devise
         if(symA[i]==a&&symB[i]==b) return i;
      if(nSym>=OG_MAX_SYMS) return -1;
      int i=nSym++;
      symName[i]=resolved; symA[i]=a; symB[i]=b;
      if(b==0){ usdSym[a]=i; usdDir[a]=1; }
      if(a==0){ usdSym[b]=i; usdDir[b]=0; }
      return i;
   }

   void setQuote(int i,double bid,double ask,long tMs)
   {
      if(i<0||i>=nSym) return;
      bidP[i]=bid; askP[i]=ask; lastT[i]=tMs;
   }

   void buildPaths(int maxLegs)
   {
      maxLegs=MathMin(MathMax(maxLegs,1),OG_MAX_LEGS);   // defense interne
      int legs[OG_MAX_LEGS];
      nPaths=0;
      for(int s=0;s<nCurr;s++)
         dfs(s,1<<s,0,legs,s,maxLegs);
      if(nPaths>=OG_MAX_PATHS)
         PrintFormat("OgCore: WARN chemins tronques au cap %d",OG_MAX_PATHS);
      for(int pi=0;pi<nPaths;pi++)
      {
         int node=pS[pi];
         for(int li=0;li<pL[pi];li++)
         {
            int e=pE[pi][li];
            int a=symA[e],b=symB[e];
            pDir[pi][li]=(node==a)?1:-1;
            node=(node==a)?b:a;
         }
      }
   }

   //--- eval : med[] = spread mediane par symbole (0 = gate off)
   bool evalAll(long tMs,const OgParams &P,const double &med[],
                double &bestNet,int &bestPi,double &bestDdd,int skipPi=-1)
   {
      double commPerSide=P.commPer100k;   // $/lot/cote -> par jambe dans pathNet
      bool ok[OG_MAX_SYMS],fxOk[OG_MAX_SYMS];
      double spc[OG_MAX_SYMS];
      gates(tMs,P,med,ok,fxOk,spc);
      bestNet=-1e18; bestPi=-1; bestDdd=0;
      for(int pi=0;pi<nPaths;pi++)
      {
         if(pi==skipPi) continue;        // pi recemment skipe -> argmax suivant
         double net,ddd,unw; int wf;
         pathNet(pi,P,ok,fxOk,spc,commPerSide,net,ddd,unw,wf);
         if(wf!=0) continue;
         if(net>bestNet){ bestNet=net; bestPi=pi; bestDdd=ddd; }
      }
      return bestPi>=0;
   }

   // Evalue UN chemin aux quotes courantes (meme semantique Convention A
   // que evalAll, etat inchange). Sert a surveiller un panier ouvert.
   //   net      : net aller+retour estime = usdOut - notional - commRt - unw
   //   ddd      : brut = usdOut - notional
   //   exitCost : cout estime de fermeture = comm/jambe + unwind
   //   whyFail  : bit0 stale/absent, bit1 spread cap, bit2 skew,
   //              bit3 usdSym absent, bit4 NaN
   bool evalPath(int pi,long tMs,const OgParams &P,const double &med[],
                 double &net,double &ddd,double &exitCost,int &whyFail)
   {
      double commPerSide=P.commPer100k;   // $/lot/cote -> par jambe dans pathNet
      bool ok[OG_MAX_SYMS],fxOk[OG_MAX_SYMS];
      double spc[OG_MAX_SYMS];
      gates(tMs,P,med,ok,fxOk,spc);
      double unw;
      pathNet(pi,P,ok,fxOk,spc,commPerSide,net,ddd,unw,whyFail);
      if(whyFail!=0) return false;
      double cl=0;
      if(pS[pi]!=0)   cl+=commLegUsd(usdSym[pS[pi]],P.notional,commPerSide);
      for(int li=0;li<pL[pi];li++) cl+=commLegUsd(pE[pi][li],P.notional,commPerSide);
      if(pEnd[pi]!=0) cl+=commLegUsd(usdSym[pEnd[pi]],P.notional,commPerSide);
      exitCost=cl*P.commScale+unw;
      return true;
   }



   string pathStr(int pi)
   {
      string s="s0="+CURR[pS[pi]];
      for(int li=0;li<pL[pi];li++)
         s+=" ->"+symName[pE[pi][li]]+(pDir[pi][li]==1?"+":"-");
      s+=" end="+CURR[pEnd[pi]];
      return s;
   }

   // commission totale $/cote du panier au taux perLotSide ($/lot-base) —
   // expose pour le gate fail-closed mode-1 (execBasket) : meme evalue avec
   // commPer100k=0, l'execution doit couvrir la commission REELLE mesuree.
   double basketCommUsd(int pi,double N,double perLotSide)
   {
      double c=0;
      if(pS[pi]!=0)   c+=commLegUsd(usdSym[pS[pi]],N,perLotSide);
      for(int li=0;li<pL[pi];li++) c+=commLegUsd(pE[pi][li],N,perLotSide);
      if(pEnd[pi]!=0) c+=commLegUsd(usdSym[pEnd[pi]],N,perLotSide);
      return c;
   }

private:
   // gates communs : ok=fraicheur, fxOk=ok+spread cap, spc=cout unwind
   // (plancher mediane : quote stale = spread "normal" mais hors-marche)
   void gates(long tMs,const OgParams &P,const double &med[],
              bool &ok[],bool &fxOk[],double &spc[])
   {
      for(int i=0;i<nSym;i++)
      {
         ok[i]=lastT[i]>0 && lastT[i]<=tMs && (tMs-lastT[i])<=P.freshMs
               && bidP[i]>0 && askP[i]>=bidP[i];
         fxOk[i]=ok[i] && !(med[i]>0 && (askP[i]-bidP[i])>P.spreadCap*med[i]);
         double mid=0.5*(bidP[i]+askP[i]);
         double sp=askP[i]-bidP[i];
         if(med[i]>0&&sp<med[i]) sp=med[i];
         spc[i]=(mid>0)?sp/mid*P.notional:EMPTY_VALUE;
      }
   }

   // commission $/cote d'une jambe : le broker facture $/lot (100k BASE)
   // par cote -> perLot x lots_i, lots_i = N/usdPerBase/1e5. Le modele flat
   // N/1e5 est juste pour les jambes USD-base, faux ~20-26% sinon.
   double commLegUsd(int e,double N,double perLot)
   {
      int a=symA[e];
      double lots=N/100000.0;
      if(a!=0)
      {
         int xs=usdSym[a];
         if(xs>=0&&bidP[xs]>0)
         {
            double u=(usdDir[a]==1)?bidP[xs]:1.0/bidP[xs];  // USD par unit base
            if(u>0) lots=N/u/100000.0;
         }
      }
      return perLot*lots;
   }

   // prix+couts d'UN chemin ; whyFail bitmask des gates echoues.
   // commPerLot = $/lot/cote (IC Raw = 3.5).
   void pathNet(int pi,const OgParams &P,const bool &ok[],const bool &fxOk[],
                const double &spc[],double commPerLot,
                double &net,double &ddd,double &unw,int &whyFail)
   {
      double rate=1.0,comm=0.0; unw=0.0;
      int s0=pS[pi],end=pEnd[pi];
      whyFail=0;
      long tMin=LONG_MAX,tMax=0;
      if(s0!=0)
      {
         int xs=usdSym[s0];
         if(xs<0) whyFail|=8;
         else if(!ok[xs]) whyFail|=1;
         else if(!fxOk[xs]) whyFail|=2;
         else{
            rate*=(usdDir[s0]==1)?1.0/askP[xs]:bidP[xs];
            unw+=spc[xs]; comm+=commLegUsd(xs,P.notional,commPerLot);
            if(lastT[xs]<tMin) tMin=lastT[xs];
            if(lastT[xs]>tMax) tMax=lastT[xs];
         }
      }
      if(whyFail==0) for(int li=0;li<pL[pi];li++)
      {
         int e=pE[pi][li];
         if(!ok[e]){ whyFail|=1; break; }
         if(!fxOk[e]){ whyFail|=2; break; }
         rate*=(pDir[pi][li]==1)?bidP[e]:1.0/askP[e];
         unw+=spc[e]; comm+=commLegUsd(e,P.notional,commPerLot);
         if(lastT[e]<tMin) tMin=lastT[e];
         if(lastT[e]>tMax) tMax=lastT[e];
      }
      if(whyFail==0&&end!=0)
      {
         int xe=usdSym[end];
         if(xe<0) whyFail|=8;
         else if(!ok[xe]) whyFail|=1;
         else if(!fxOk[xe]) whyFail|=2;
         else{
            rate*=(usdDir[end]==1)?bidP[xe]:1.0/askP[xe];
            unw+=spc[xe]; comm+=commLegUsd(xe,P.notional,commPerLot);
            if(lastT[xe]<tMin) tMin=lastT[xe];
            if(lastT[xe]>tMax) tMax=lastT[xe];
         }
      }
      // synchronicite : les jambes du panier doivent cotter dans la meme
      // fenetre — sinon l'edge est un artefact lead-lag, pas un prix
      if(whyFail==0&&P.skewMs>0&&tMax-tMin>P.skewMs) whyFail|=4;
      net=-1e18; ddd=0;
      if(whyFail!=0) return;
      double usdOut=P.notional*rate;
      double commFx=2.0*comm*P.commScale;
      if(!(MathIsValidNumber(usdOut)&&MathIsValidNumber(commFx)
           &&MathIsValidNumber(unw))){ whyFail|=16; return; }
      ddd=usdOut-P.notional;
      net=ddd-commFx-unw;
   }

   void dfs(int node,int mask,int depth,int &legs[],int s0,int maxLegs)
   {
      if(depth>0 && nPaths<OG_MAX_PATHS)
      {
         int pi=nPaths;
         pS[pi]=s0; pEnd[pi]=node; pL[pi]=depth;
         for(int li=0;li<depth;li++) pE[pi][li]=legs[li];
         nPaths++;
      }
      if(depth>=maxLegs) return;
      for(int e=0;e<nSym;e++)
      {
         int a=symA[e],b=symB[e];
         int nxt=(node==a)?b:(node==b?a:-1);
         if(nxt<0||(mask&(1<<nxt))!=0) continue;
         legs[depth]=e;
         dfs(nxt,mask|(1<<nxt),depth+1,legs,s0,maxLegs);
      }
   }
};
#endif
//+------------------------------------------------------------------+
