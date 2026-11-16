# MC scalp-laggard — budget $1000
# Specs: 10 agents (freq/dormance/capture/couts/sizing/adverse/calendrier/
# sorties/metriques/archi). stdlib seul. Posterior-predictif par run:
# n=6 fills seulement -> bootstrap pur interdit, on tire les params par run.
import random, math, statistics, json, sys

CAP0   = 1000.0      # budget initial
RUIN   = 500.0       # kill-switch gouvernance (-50%)
WEEKS  = 4           # horizon: 4 semaines de marche
NRUNS  = 20000

# --- modeles -----------------------------------------------------------------
def lots(n, aToUsd=1.0):
    return max(0.01, math.floor(n/aToUsd/100000.0/0.01)*0.01)

def comm_rt(n):                      # $7/lot A/R mesure IC Raw
    return 7.0*lots(n)

# P&L net d'un fill au sizing n : net = c_capture x ddd_n - comm_n,
# borne par le stop fixe (-2$ + gap). ddd post-gate ~ U(0.7,1.5) a 10k.
CAPT = dict(dormant=lambda r: r.gauss(0.85,0.45),       # t~approx n=1 -> N
            pseudo =lambda r: r.gauss(-0.15,0.30),
            leader =lambda r: (r.gauss(-0.05,0.30) if r.random()<0.6
                               else r.gauss(-2.7,0.35)))
def pnl_fill(kind, n, rng, mu_d=0.85, v=1.0):
    c    = CAPT[kind](rng)
    if kind=='dormant': c = rng.gauss(mu_d,0.45)
    ddd  = rng.uniform(0.7,1.5)*v*n/10000.0              # taille disloc ~ vol
    net  = c*ddd - comm_rt(n)
    return max(net, -(2.0*n/10000.0 + comm_rt(n)))     # stop+gap approx

# --- une trajectoire ----------------------------------------------------------
def run_path(rng, lam_week, p_dorm, eta_attr, p_contam, mu_d=0.85, v=1.0):
    eq = CAP0; peak = eq; maxdd = 0.0; mineq = eq; n_tr = 0
    for week in range(WEEKS):
        # semaine: remplissages dormant eligibles ~ Poisson(lam*Gamma regime)
        lam = lam_week*(v**0.7)*rng.gammavariate(2.0, 0.5)  # freq ~ vol^0.7
        n_ev = int(rng.gammavariate(lam, 1.0)+0.5)        # ~Poisson (mean=var=lam)
        p_d  = min(p_dorm/(v**0.35), 0.97)                # books repricent + vite
        for _ in range(n_ev):
            # gate legAttribOK elimine p_contam*eta_attr des pseudo-dormants
            r = rng.random()
            if r < p_contam*eta_attr:        continue    # rejete, miss gratis
            if r < p_contam:                 kind='pseudo'
            elif r < p_contam + (1-p_contam)*(1-p_d):    kind='leader'
            else:                            kind='dormant'
            n = min(max(eq*2.5, 2500.0), 10000.0)        # sizing fractionnel borne
            eq += pnl_fill(kind, n, rng, mu_d, v);  n_tr += 1
            if eq < mineq: mineq = eq
            if eq < RUIN:  return eq, mineq, maxdd, n_tr, True
            if eq > peak:  peak = eq
            dd = peak - eq
            if dd > maxdd: maxdd = dd
    return eq, mineq, maxdd, n_tr, False

# --- campagne -----------------------------------------------------------------
def campaign(tag, lam_week, p_dorm_a, p_dorm_b, eta_attr, p_contam, seed,
             mu_d=0.85, v=1.0):
    rng = random.Random(seed)
    res = [run_path(rng, lam_week,
                    rng.betavariate(p_dorm_a, p_dorm_b), eta_attr, p_contam,
                    mu_d, v)
           for _ in range(NRUNS)]
    fin  = sorted(r[0] for r in res)
    mn   = sorted(r[1] for r in res)
    dd   = sorted(r[2] for r in res)
    ntr  = statistics.mean(r[3] for r in res)
    ruin = sum(1 for r in res if r[4])/NRUNS
    q = lambda a,p: a[int(p*(len(a)-1))]
    print(f"{tag:8s} lam={lam_week:4.0f}/sem pD~B({p_dorm_a},{p_dorm_b}) "
          f"eta={eta_attr:.1f} pC={p_contam:.2f} | "
          f"fin p05={q(fin,.05):7.0f} p50={q(fin,.50):7.0f} p95={q(fin,.95):7.0f} "
          f"| P(>1000)={sum(x>1000 for x in fin)/NRUNS:.2f} "
          f"ruine={ruin:.3f} dd50={q(dd,.5):5.0f} dd95={q(dd,.95):5.0f} "
          f"trades/4sem={ntr:.0f}")
    return dict(tag=tag, p05=q(fin,.05), p50=q(fin,.50), p95=q(fin,.95),
                pwin=sum(x>1000 for x in fin)/NRUNS, ruin=ruin,
                dd50=q(dd,.5), dd95=q(dd,.95), trades=ntr)

SEED = 20261114
print(f"MC scalp-laggard $1000 | {NRUNS} paths x {WEEKS} sem | ruine<{RUIN} "
      f"| sizing N=clamp(eq*2.5,2500,10000)\n")
out = []

# Controle de falsification: quasi pas de dormants -> le MC DOIT montrer
# une equity qui decroit (sinon le moteur est casse)
out.append(campaign("NOEDGE", 15, 1.0, 15.0, 0.0, 0.30, SEED+99))

# Scenarios {pessimiste, base, optimiste} ; mu_d = E[capture|dormant] (spec MC-3:
# PESS t(mu=0.20) / BASE 0.70-0.85 / OPT 1.10) — prior-dominated, n=1.
out.append(campaign("PESS",  5, 3.0, 7.0, 0.4, 0.30, SEED+1, mu_d=0.20))
out.append(campaign("BASE", 15, 7.0, 3.0, 0.6, 0.25, SEED+2, mu_d=0.70))
out.append(campaign("OPT",  40, 8.5, 1.5, 0.8, 0.20, SEED+3, mu_d=1.10))

# Regime vol reel: VIX ven 13/11 close 19.30 (range 17.64-20.92, +3.2%) vs
# ref calme ~17 -> v=1.14. FX 1w impl ~ 0.42*VIX ~ 8.1% ann. Vendredi
# session US/EU: overlap 13-17h UTC (forte liquidite), apres 17h books
# s'amincissent (dormance+ mais prints-), squaring PM = disloc bursts.
# lam=20/sem ~ mesure funnel 0.43/h x ~40h arme, ddd/comm/lots scades par v.
VIX_NOW, VIX_REF = 19.30, 17.0
VF = VIX_NOW/VIX_REF
print(f"VIX={VIX_NOW} (ven 13/11) ref={VIX_REF} -> v={VF:.3f} | "
      f"ddd x{VF:.2f} | lam x{VF**0.7:.2f} | p_dorm /{VF**0.35:.2f}\n")
out.append(campaign("FRI-VIX", 20, 6.5, 3.5, 0.65, 0.28, SEED+4,
                    mu_d=0.80, v=VF))

# Tornado OAT sur BASE (graines appariees)
print("\n--- tornado (BASE, 1 param a la fois) ---")
base = dict(lam=15, pa=7.0, pb=3.0, eta=0.6, pc=0.25, mu=0.70)
for k,(lo,hi) in dict(lam=(5,40), eta=(0.4,0.8), pc=(0.40,0.10),
                     mu=(0.20,1.10)).items():
    for v in (lo,hi):
        b = dict(base); b[k] = v
        campaign(f"{k}={v}", b['lam'], b['pa'], b['pb'], b['eta'], b['pc'],
                 SEED+2, mu_d=b['mu']) and None

json.dump(out, open(r"C:\arb_work\og-mql5\mc_scalp_results.json","w"), indent=1)
print("\n-> mc_scalp_results.json")
