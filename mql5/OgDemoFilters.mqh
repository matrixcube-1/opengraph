#ifndef OG_DEMO_FILTERS_MQH
#define OG_DEMO_FILTERS_MQH
struct OgScalpPolicy
{
   bool relaxed,entryAllowed;
   double minDdd,attribFloor,commissionScale;
   long staleMs;
};

OgScalpPolicy ogScalpPolicy(bool requested,long accountMode,int attempts,double minDdd,long staleMs,bool continuous=false)
{
   OgScalpPolicy p;
   p.relaxed=requested&&accountMode==ACCOUNT_TRADE_MODE_DEMO;
   p.entryAllowed=!requested||(p.relaxed&&attempts>=0&&(continuous||attempts<3));
   p.minDdd=p.relaxed?MathMin(minDdd,0.30):minDdd;
   p.staleMs=p.relaxed?(long)MathMin((double)staleMs,250.0):staleMs;
   p.attribFloor=p.relaxed?0.01:1.05;
   p.commissionScale=p.relaxed?0.0:1.0;
   return p;
}

int ogDemoAttempts(string key)
{
   if(!GlobalVariableCheck(key)) return 0;
   double n=GlobalVariableGet(key);
   if(!MathIsValidNumber(n)||n<0||n>=INT_MAX||n!=MathFloor(n)) return -1;
   return (int)n;
}

bool ogReserveDemoAttempt(string key,long accountMode,bool continuous=false)
{
   if(accountMode!=ACCOUNT_TRADE_MODE_DEMO) return false;
   if(!GlobalVariableCheck(key)&&GlobalVariableSet(key,0)==0) return false;
   double n=GlobalVariableGet(key);
   if(!MathIsValidNumber(n)||n<0||n>=INT_MAX||(!continuous&&n>=3)||n!=MathFloor(n)) return false;
   if(!GlobalVariableSetOnCondition(key,n+1,n)) return false;
   GlobalVariablesFlush();
   return true;
}
#endif
