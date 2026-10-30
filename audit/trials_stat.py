import sqlite3

c = sqlite3.connect('/opt/arb/poc/optuna.db')
tr = {r[0]: int(float(r[1])) for r in c.execute(
    "select trial_id,value_json from trial_user_attributes where key='trades'")}
ab = {r[0]: int(float(r[1])) for r in c.execute(
    "select trial_id,value_json from trial_user_attributes where key='aborts'")}
q = ("select t.trial_id,t.number,tv.value from trials t "
     "join trial_values tv on tv.trial_id=t.trial_id "
     "where t.state='COMPLETE' order by tv.value desc")
for tid, n, v in c.execute(q):
    t = tr.get(tid, 0); a = ab.get(tid, 0)
    edge = v / max(t, 1)
    print(f"trial {n}: obj={v:>13,.0f} | trades={t:>6} aborts={a:>5} "
          f"| {edge:>7.2f} USD/trade = {edge/10:>6.2f} bp")
