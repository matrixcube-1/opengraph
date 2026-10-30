import sqlite3, json
c = sqlite3.connect('/opt/arb/poc/optuna.db')
q = """SELECT t.trial_id,t.number,v.value,t.state
       FROM trials t JOIN trial_values v ON v.trial_id=t.trial_id
       WHERE t.state='COMPLETE' ORDER BY t.number"""
for tid, num, val, st in c.execute(q):
    prms = {n: round(v, 4) for n, v in c.execute("SELECT param_name,param_value FROM trial_params WHERE trial_id=?", (tid,))}
    attrs = {k: json.loads(v) for k, v in c.execute("SELECT key,value_json FROM trial_user_attributes WHERE trial_id=?", (tid,))}
    print(f"T{num} val={val:,.0f} params={prms}")
    print(f"   attrs={attrs}")
