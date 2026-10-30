# mc_sim.py — Monte Carlo réaliste : chemins ouverts + protocole séquentiel E1-E9
# - 10 paires, quotes live Yahoo + calibration vol/corr sur 3 mois horaires réels
# - mids : GBM multivarié corrélé (Cholesky) ; croix déduites (graphe toujours cohérent)
# - venue : quote = mid × exp(δ), δ OU + sauts « stale » dimensionnés par le spread
# - exécution : fills IOC, latence, mid+δ qui bougent pendant le fill (E3),
#   partiel → resize/abort (E5), continuation E4, unwind (E6), impact : la quote se consomme
# - sortie : marque DDD nette (convention doc) ET net « réalisé » (résiduel reconverti)
import json, os
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = os.path.dirname(os.path.abspath(__file__))
RNG = np.random.default_rng(20261027)

PAIRS = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD',
         'AUDUSD', 'NZDUSD', 'EURGBP', 'EURJPY', 'GBPJPY']
USD_QUOTED = ['EURUSD', 'GBPUSD', 'USDJPY', 'USDCHF', 'USDCAD', 'AUDUSD', 'NZDUSD']
CURR = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'CAD', 'AUD', 'NZD']
CIDX = {c: i for i, c in enumerate(CURR)}

HS_BP = {'EURUSD': 0.35, 'GBPUSD': 0.45, 'USDJPY': 0.45, 'USDCHF': 0.55, 'USDCAD': 0.60,
         'AUDUSD': 0.50, 'NZDUSD': 0.75, 'EURGBP': 0.65, 'EURJPY': 0.85, 'GBPJPY': 1.20}
COMM_LEG = 3.50
SLIP_RES = 1.50
NOTIONAL = 1e5

OU_HL_S = 2.0
OU_SD_BP = 0.20
STALE_RATE = 0.002      # saut par seconde et par paire (~1 / 8 min)
STALE_MULT = 3.0        # amplitude = 3 × demi-spread de la paire

TAU_SLIP = 0.8e-4
# live : 1 ms aller-retour OTC ECN + matching venue ~1,5 ms ; jitter interne 10 µs (négligeable)
LAT_MU, LAT_SD = 0.0025, 0.0008
Q_MIN = 0.25
M_ABORT = 2.0
IMPACT = 0.35           # δ consommé par nos propres fills (×(1-IMPACT) par jambe remplie)
P_COMPETE = 0.35        # part des sauts stale disputés par des HFT plus rapides
FAST_HL_S = 0.030       # demi-vie de résorption quand la concurrence a détecté l'écart
FAST_WIN_S = (0.05, 0.30)
COOLDOWN = 1.0
T_SIM, DT = 3600.0, 0.10
N_REP = 3

data = {k.replace('=X', ''): v for k, v in
        json.load(open(os.path.join(BASE, 'live_quotes.json'))).items()}

EDGES = []
for pi, p in enumerate(PAIRS):
    a, b = CIDX[p[:3]], CIDX[p[3:]]
    EDGES += [(a, b, pi, +1), (b, a, pi, -1)]
EDGE_IDX = {e: i for i, e in enumerate(EDGES)}

def enum_paths(max_legs=3):
    out = []
    for s in range(8):
        stack = [(s, [], {s})]
        while stack:
            n, legs, seen = stack.pop()
            if legs and n != s:
                out.append(list(legs))
            if len(legs) < max_legs:
                for ei, (a, b, pi, d) in enumerate(EDGES):
                    if a == n and b not in seen:
                        stack.append((b, legs + [ei], seen | {b}))
    return out

def cross_mid(sym, m):
    if sym == 'EURGBP': return m['EURUSD'] / m['GBPUSD']
    if sym == 'EURJPY': return m['EURUSD'] * m['USDJPY']
    if sym == 'GBPJPY': return m['GBPUSD'] * m['USDJPY']
    return m[sym]

def hourly_cov():
    closes = {s: np.array([c for c in data[s]['closes'] if c], float) for s in USD_QUOTED}
    m = min(map(len, closes.values()))
    R = np.column_stack([np.diff(np.log(closes[s][-m:])) for s in USD_QUOTED])
    return np.cov(R.T)

HS = np.array([HS_BP[p] for p in PAIRS]) * 1e-4
STALE = HS * STALE_MULT

def world(mids7):
    """mids des 10 paires + usd_price par devise à partir des 7 taux USD."""
    m = {s: mids7[i] for i, s in enumerate(USD_QUOTED)}
    up = {'USD': 1.0}
    for c in CURR[1:]:
        up[c] = m[c + 'USD'] if c + 'USD' in m else 1.0 / m['USD' + c]
    return np.array([cross_mid(p, m) for p in PAIRS]), np.array([up[c] for c in CURR]), up

def leg_rate(q, pi, d):
    return q * (1 - HS[pi]) if d == +1 else 1.0 / (q * (1 + HS[pi]))

def run_sequence(path, t0, delta, mids7, Lch, sig):
    """Séquence E1-E9 : le monde (mids + δ) évolue pendant la latence de chaque fill."""
    legs = [EDGES[ei] for ei in path]
    _, _, up = world(mids7)
    COMMS = np.array([COMM_LEG / up[p[:3]] for p in PAIRS])   # §8.7 : 3,50/P_base, compte USD
    qty = NOTIONAL / up[CURR[legs[0][0]]]
    comm_cum, t, cash = 0.0, t0, 0.0
    filled = []                       # (pair_idx, notionnel_usd_de_la_jambe)
    kappa = np.log(2) / OU_HL_S
    mids7 = mids7.copy()
    for k, (a, b, pi, d) in enumerate(legs):
        lat = max(0.001, RNG.normal(LAT_MU, LAT_SD))        # plancher = RTT 1 ms
        mids_pair, _, _ = world(mids7)
        q_send = mids_pair[pi] * np.exp(delta[pi])          # quote protégée à l'envoi
        n_sub = max(1, int(np.ceil(lat / 0.01)))            # sous-pas sommant exactement lat
        dt_sub = lat / n_sub
        for _ in range(n_sub):
            mids7 *= np.exp((Lch @ RNG.standard_normal(7)) * np.sqrt(dt_sub / 3600.0) - 0.5 * sig**2 * (dt_sub / 3600.0))
            delta += (-kappa * delta * dt_sub
                      + OU_SD_BP * 1e-4 * np.sqrt(2 * kappa * dt_sub) * RNG.standard_normal(len(PAIRS)))
        t += lat
        mids_pair, usd_arr, up = world(mids7)
        q_now = mids_pair[pi] * np.exp(delta[pi])
        move = abs(q_now / q_send - 1.0)
        # E3/E4 : espérance du résiduel restant vs coût d'unwind des jambes remplies
        q_res, rem_cost = qty, 0.0
        for (a2, b2, pi2, d2) in legs[k:]:
            q2 = mids_pair[pi2] * np.exp(delta[pi2])
            q_res *= leg_rate(q2, pi2, d2)
            rem_cost += COMMS[pi2] + SLIP_RES
        e_resid = q_res * usd_arr[legs[-1][1]] - qty * usd_arr[a] - rem_cost
        c_unwind = sum(nl * HS[p2] + 2 * COMMS[p2] for (_, p2, nl) in filled)
        if e_resid < -(c_unwind + M_ABORT):
            return dict(res='abort', why='E4', pnl=-c_unwind - RNG.uniform(0, 0.5) * len(filled),
                        dur=t - t0, partiel=False, consume=k)
        if move <= TAU_SLIP:
            qty *= leg_rate(q_now, pi, d)
            filled.append((k, pi, qty * usd_arr[b]))
            comm_cum += COMMS[pi]
        elif move <= 3 * TAU_SLIP and RNG.random() < 0.6:   # partiel à prix protégé
            frac = RNG.uniform(0.3, 0.9)
            cash += qty * (1 - frac) * usd_arr[a]          # reliquat non converti, conservé
            qty *= leg_rate(q_send, pi, d) * frac
            filled.append((k, pi, qty * usd_arr[b]))
            comm_cum += COMMS[pi]
            if frac < Q_MIN:
                return dict(res='abort', why='E5 q<q_min', pnl=-c_unwind - RNG.uniform(0, 0.5),
                            dur=t - t0, partiel=True, consume=k)
        else:
            return dict(res='abort', why='kill IOC', pnl=-c_unwind - RNG.uniform(0, 0.5) * len(filled),
                        dur=t - t0, partiel=False, consume=k)
    pnl = qty * usd_arr[legs[-1][1]] + cash - NOTIONAL - comm_cum - len(legs) * SLIP_RES
    # net « réalisé » : reconvertir le résiduel en USD = ~1 jambe de coût
    end = legs[-1][1]
    flat = 0.0 if end == CIDX['USD'] else HS[usd_pair_of(end)] * NOTIONAL + COMMS[usd_pair_of(end)]
    if cash > 0:                                            # reliquat en devise de départ à reconvertir
        a0 = legs[0][0]
        flat += 0.0 if a0 == CIDX['USD'] else HS[usd_pair_of(a0)] * cash + COMMS[usd_pair_of(a0)]
    return dict(res='done', pnl=float(pnl), pnl_flat=float(pnl - flat),
                dur=t - t0, partiel=False, consume=len(legs))

def usd_pair_of(ci):
    c = CURR[ci]
    p = c + 'USD' if c + 'USD' in PAIRS else 'USD' + c
    return PAIRS.index(p)

def run(verbose=True):
    cov = hourly_cov()
    L = np.linalg.cholesky(cov + np.eye(7) * 1e-14)
    sig = np.sqrt(np.diag(cov))
    paths = enum_paths()
    legmat = np.full((len(paths), 3), -1)
    endcur = np.zeros(len(paths), int); startcur = np.zeros(len(paths), int)
    ppairs = [set() for _ in paths]
    for i, pth in enumerate(paths):
        for j, ei in enumerate(pth):
            legmat[i, j] = ei
            ppairs[i].add(EDGES[ei][2])
        startcur[i] = EDGES[pth[0]][0]; endcur[i] = EDGES[pth[-1]][1]
    mask = legmat >= 0
    nl = mask.sum(1)
    if verbose:
        vols = {s: np.sqrt(cov[i, i] * 24 * 365) for i, s in enumerate(USD_QUOTED)}
        print(f'{len(paths)} chemins ouverts (≤3 jambes). Vols annuelles : '
              + ', '.join(f'{k}={v:.0%}' for k, v in vols.items()))
        c = cov / np.outer(sig, sig)
        print(f'corr EURUSD-GBPUSD={c[0,1]:+.2f}  EURUSD-USDCHF={c[0,3]:+.2f}  USDJPY-USDCAD={c[2,4]:+.2f}')

    _, _, up0 = world(np.array([data[s]['live'] for s in USD_QUOTED], float))
    COMMS_D = np.array([COMM_LEG / up0[p[:3]] for p in PAIRS])
    COMMS_E = np.array([COMMS_D[EDGES[ei][2]] for ei in range(len(EDGES))])
    n_steps = int(T_SIM / DT)
    kappa = np.log(2) / OU_HL_S
    all_res = []
    samp = {'t': [], 'mid': [], 'd': [], 'net': []}
    for rep in range(N_REP):
        mids7 = np.array([data[s]['live'] for s in USD_QUOTED], float)
        delta = np.zeros(len(PAIRS))
        fast_until = np.zeros(len(PAIRS))
        last_send = np.full(len(paths), -1e9)
        busy = -1.0
        res = dict(sent=0, done=0, abort=0, pnl=[], pnl_flat=[], why={})
        for st in range(n_steps):
            t = st * DT
            mids7 *= np.exp((L @ RNG.standard_normal(7)) * np.sqrt(DT / 3600.0) - 0.5 * sig**2 * (DT / 3600.0))
            delta += -kappa * delta * DT + OU_SD_BP * 1e-4 * np.sqrt(2 * kappa * DT) * RNG.standard_normal(len(PAIRS))
            jmp = RNG.random(len(PAIRS)) < STALE_RATE * DT
            delta[jmp] += RNG.choice([-1., 1.], jmp.sum()) * STALE[jmp]
            comp = jmp & (RNG.random(len(PAIRS)) < P_COMPETE)
            fast_until[comp] = t + RNG.uniform(*FAST_WIN_S, comp.sum())
            kap = np.where(t < fast_until, np.log(2) / FAST_HL_S, kappa)
            delta += -(kap - kappa) * delta * DT
            mids_pair, usd_arr, _ = world(mids7)
            q = mids_pair * np.exp(delta)
            r_edge = np.array([leg_rate(q[EDGES[ei][2]], EDGES[ei][2], EDGES[ei][3])
                               for ei in range(len(EDGES))])
            rl = np.where(mask, r_edge[np.clip(legmat, 0, 19)], 1.0)
            ddd = np.prod(rl, 1) * usd_arr[endcur] / usd_arr[startcur] * NOTIONAL - NOTIONAL
            net = ddd - np.where(mask, COMMS_E[np.clip(legmat, 0, len(EDGES) - 1)] + SLIP_RES, 0.0).sum(1)
            if rep == 0 and st % 20 == 0:
                samp['t'].append(t); samp['mid'].append(mids7[0])
                samp['d'].append(delta[0]); samp['net'].append(net.max())
            if t < busy:
                continue
            cand = np.where((net > 0) & (t - last_send > COOLDOWN))[0]
            if len(cand):
                pi_ = cand[np.argmax(net[cand])]
                last_send[pi_] = t
                res['sent'] += 1
                out = run_sequence(paths[pi_], t, delta.copy(), mids7, L, sig)
                res['pnl'].append(out['pnl'])
                res['pnl_flat'].append(out.get('pnl_flat', out['pnl']))
                w = out.get('why', 'done')
                res['why'][w] = res['why'].get(w, 0) + 1
                res['done'] += out['res'] == 'done'
                res['abort'] += out['res'] == 'abort'
                for p2 in ppairs[pi_]:
                    delta[p2] *= (1 - IMPACT)          # nos fills consomment la déviation
                busy = t + out['dur'] + 0.001
        all_res.append(res)
        if verbose:
            print(f'  rep{rep}: envoyés={res["sent"]}  complétés={res["done"]}  '
                  f'aborts={res["abort"]} {res["why"]}  P&L marque={sum(res["pnl"]):+.0f}  '
                  f'réalisé={sum(res["pnl_flat"]):+.0f} USD')
    return all_res, samp

if __name__ == '__main__':
    res, samp = run()
    pnls = np.array([p for r in res for p in r['pnl']])
    flats = np.array([p for r in res for p in r['pnl_flat']])
    print('\n=== Synthèse Monte Carlo (chemins ouverts + E1-E9) ===')
    print(f'envoyés={sum(r["sent"] for r in res)}  complétés={sum(r["done"] for r in res)}  '
          f'aborts={sum(r["abort"] for r in res)}')
    if len(pnls):
        print(f'P&L marque/trade : moy={pnls.mean():+.2f}  méd={np.median(pnls):+.2f}  '
              f'p5={np.percentile(pnls,5):+.2f}  p95={np.percentile(pnls,95):+.2f} USD')
        print(f'P&L réalisé/trade : moy={flats.mean():+.2f}  méd={np.median(flats):+.2f} USD '
              f'(résiduel reconverti, ~1 jambe)')
        print(f'totaux (3×1 h) : marque={pnls.sum():+.0f}  réalisé={flats.sum():+.0f} USD ; '
              f'part positive (réalisé) = {(flats > 0).mean() * 100:.0f} %')

    fig, ax = plt.subplots(2, 2, figsize=(11, 7))
    ax[0, 0].plot(samp['t'], samp['mid']); ax[0, 0].set_title('EURUSD mid (GBM calibré)')
    ax[0, 1].plot(samp['t'], np.array(samp['d']) * 1e4)
    ax[0, 1].set_title('δ venue EURUSD (OU + sauts), bp')
    ax[1, 0].plot(samp['t'], samp['net']); ax[1, 0].axhline(0, c='r', ls='--', lw=0.7)
    ax[1, 0].set_title('max DDD net par pas (USD)')
    if len(pnls):
        ax[1, 1].hist(flats, bins=40); ax[1, 1].axvline(0, c='r', ls='--', lw=0.7)
        ax[1, 1].set_title('P&L réalisé par envoi (USD)')
    fig.tight_layout()
    out = os.path.join(BASE, 'mc_sim.png')
    fig.savefig(out, dpi=110)
    print('->', out)
