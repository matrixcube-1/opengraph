#property version "1.00"
#include "OgDemoFilters.mqh"

int passed=0,failed=0;
void check(bool ok,string name)
{
   if(ok) passed++; else failed++;
   PrintFormat("DEMO_FILTER_TEST %s %s",name,ok?"PASS":"FAIL");
}

void OnStart()
{
   OgScalpPolicy p=ogScalpPolicy(false,ACCOUNT_TRADE_MODE_DEMO,0,0.70,1000);
   check(!p.relaxed&&p.entryAllowed&&p.minDdd==0.70&&p.staleMs==1000&&p.attribFloor==1.05&&p.commissionScale==1,"normal_unchanged");
   p=ogScalpPolicy(true,ACCOUNT_TRADE_MODE_DEMO,0,0.70,1000);
   check(p.relaxed&&p.entryAllowed&&p.minDdd==0.30&&p.staleMs==250&&p.attribFloor==0.01&&p.commissionScale==0,"demo_relaxed");
   p=ogScalpPolicy(true,ACCOUNT_TRADE_MODE_REAL,0,0.70,1000);
   check(!p.relaxed&&!p.entryAllowed&&p.commissionScale==1,"real_refused");
   p=ogScalpPolicy(true,ACCOUNT_TRADE_MODE_CONTEST,0,0.70,1000);
   check(!p.relaxed&&!p.entryAllowed,"contest_refused");
   p=ogScalpPolicy(true,ACCOUNT_TRADE_MODE_DEMO,3,0.70,1000);
   check(!p.entryAllowed,"fourth_attempt_refused");
   p=ogScalpPolicy(true,ACCOUNT_TRADE_MODE_DEMO,0,0.10,100);
   check(p.minDdd==0.10&&p.staleMs==100,"lower_existing_threshold_preserved");
   string key=StringFormat("OgDemoFiltersTest.%I64d.%I64u",ChartID(),GetMicrosecondCount());
   check(!ogReserveDemoAttempt(key,ACCOUNT_TRADE_MODE_REAL)&&!GlobalVariableCheck(key),"real_cannot_reserve");
   check(ogReserveDemoAttempt(key,ACCOUNT_TRADE_MODE_DEMO),"reserve_first");
   check(ogReserveDemoAttempt(key,ACCOUNT_TRADE_MODE_DEMO),"reserve_second");
   check(ogReserveDemoAttempt(key,ACCOUNT_TRADE_MODE_DEMO),"reserve_third");
   check(!ogReserveDemoAttempt(key,ACCOUNT_TRADE_MODE_DEMO)&&ogDemoAttempts(key)==3,"persistent_budget");
   if(GlobalVariableCheck(key)) GlobalVariableDel(key);
   PrintFormat("DEMO_FILTER_RESULT %d PASS / %d FAIL",passed,failed);
}
