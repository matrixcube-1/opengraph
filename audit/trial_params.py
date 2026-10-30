import sqlite3, json
c = sqlite3.connect('/opt/arb/poc/optuna.db')
cols = [r[1] for r in c.execute("pragma table_info(trial_params)")]
print("cols:", cols)
q = ("select t.number, tp.param_name, tp.param_value from trials t "
     "join trial_values tv on tv.trial_id=t.trial_id "
     "join trial_params tp on tp.trial_id=t.trial_id "
     "where t.state='COMPLETE' and tv.value>3000000 order by t.number,tp.param_name")
cur = None
for n, pn, pv in c.execute(q):
    if n != cur:
        print(f"--- trial {n}")
        cur = n
    print(f"    {pn}={pv}")
