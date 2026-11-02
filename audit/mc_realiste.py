#!/usr/bin/env python3
"""MC réaliste — chemins ouverts Convention A sur générateur cohérent.

Remplace `mc_sim.py` (jugé non falsifiable en revue critique C5 : bruit
indépendant par paire -> arbitrages fantômes, pas de coût de sortie,
métriques non définies).

Falsifiabilité par construction :
  - les mids dérivent d'un état latent USD par devise : mid(a,b) =
    up[a]/up[b] -> cohérence triangulaire EXACTE, aucun edge synthétique ;
  - la SEULE source d'opportunité est la désynchronisation temporelle
    des quotes (horloges de tick Poisson propres à chaque paire -> le
    livre publié est transitoirement incohérent alors que le latent
    reste cohérent) — le mécanisme réel de l'edge ;
  - le contrôle `sync` (horloge commune -> état publié toujours
    cohérent) DOIT produire ~0 trade gagnant ; sinon le générateur ou
    le moteur est invalide et le MC est rejeté ;
  - exécution par le moteur Rust de production (`opengraph`) : bid/ask
    exécutables, fraîcheur bilatérale, fills séquentiels, kill IOC,
    partiels 0.6, résiduel liquidé Convention A, commissions, coût
    d'abort par arête (post-D2) — aucune formule re-codée.

Métriques (définies, cf. rapport) :
  opportunities = évals dont best_net > 0 ; candidates = net > thresh
  (-30, sur-ensemble calibration) ; sent = candidates passant ddd_min
  et le verrou t_free ; trades = exec_seq non-None ; pnl par trade.

Usage : python mc_realiste.py [n_sessions] [dur_min] [mode]
  mode = async | sync | lat_sweep | spread_sweep | all   (defaut: all)
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import opengraph

sys.path.insert(0, str(Path(__file__).parent))
import poc_engine as pe  # noqa: E402  (FX, CURR — tables canoniques)

FX = pe.FX
CURR = pe.CURR
N = len(FX)

# ---------------- paramètres marché (documentés dans le rapport) ---------
# Valeurs USD de départ du latent (ordre CURR).
UP0 = np.array([1.0, 1.10, 1.30, 1 / 150.0, 1 / 0.90, 1 / 1.35, 0.65, 0.60])
# Vol annuelle par devise (USD = 0) convertie en sigma/seconde.
ANN_VOL = np.array([0.0, 0.07, 0.08, 0.07, 0.06, 0.07, 0.09, 0.09])
SIG_S = ANN_VOL / np.sqrt(365.0 * 86400.0)
# Bêta au facteur global (risk-on/-off) — corrélations inter-devises.
BETA = np.array([0.0, 0.35, 0.40, -0.15, -0.20, 0.25, 0.55, 0.50])
SIG_F = 0.05 / np.sqrt(365.0 * 86400.0)
# Sauts latents : Poisson par devise par heure, amplitude gaussienne ~25 bp.
# C'est LE mécanisme d'edge : un saut repriced instantanément sur le latent,
# chaque paire de la devise ne le reflète qu'à son prochain tick — fenêtre
# de désynchronisation = l'opportunité exécutable réelle.
JUMP_LAMBDA_H = 0.8
JUMP_SIG = 0.0018

# Taux de tick par paire (ticks/s, ordre FX) — profil ECN : majors rapides,
# croisés exotiques lents. Source d'estimation : ordres de grandeur IC Raw.
LAMBDA = np.array([
    12.0, 7.0, 9.0, 4.0, 5.0, 4.0, 3.0,        # USD jambes
    5.0, 8.0, 4.0, 3.0, 3.0, 1.5,             # EUR croisés
    6.0, 3.0, 2.0, 2.0, 1.0,                  # GBP croisés
    4.0, 2.0, 1.5, 1.5,                       # AUD croisés
    3.0, 1.5, 3.0,                            # CAD/CHF croisés
    1.5, 0.8, 0.8,                            # NZD croisés
])
# Spread complet médian par paire en fraction (bp -> x1e-4), ordre FX.
SPREAD = np.array([
    0.4, 0.6, 0.5, 0.7, 0.6, 0.7, 0.9,
    0.6, 0.8, 0.9, 1.2, 1.1, 1.8,
    1.1, 1.3, 1.6, 1.6, 2.2,
    1.0, 1.2, 1.4, 1.6,
    1.0, 1.4, 1.1,
    1.5, 1.8, 1.9,
]) * 1e-4

DT_MS = 10            # grille latente
BASE_TS = 1_800_000_000_000  # ms epoch arbitraire (cohérence interne seule)

# Paramètres du moteur = meilleur essai Optuna #87 (= défauts du document).
P = dict(fresh_ms=500.0, spread_cap=1.7, comm_scale=1.0, ddd_min=0.0,
         lat_ms=2.5, tau_slip_bp=0.8, q_min=0.5, cooldown_s=0.0)
THRESH = -30.0        # sur-ensemble de détection de la calibration

PAIR_NODE = [(CURR.index(s[:3]), CURR.index(s[3:])) for s in FX]


def gen_latent(rng, n_steps):
    """8 chemins log-normaux corrélés (facteur global + idiosyncrasie + sauts).
    Retourne up[c, k] — cohérent par construction : tout mid dérivé."""
    dt = DT_MS / 1000.0
    dF = rng.normal(0.0, SIG_F * np.sqrt(dt), n_steps)
    dW = rng.normal(0.0, np.sqrt(dt), (8, n_steps))
    dL = BETA[:, None] * dF + SIG_S[:, None] * dW
    # sauts latents rares (nouvelles) — c'est eux qui ouvrent les vraies
    # fenêtres de désynchronisation exécutables
    n_jumps = rng.poisson(JUMP_LAMBDA_H * n_steps * dt / 3600.0, (8,))
    for c in range(1, 8):
        for _ in range(n_jumps[c]):
            k = int(rng.integers(0, n_steps))
            dL[c, k] += rng.normal(0.0, JUMP_SIG)
    return UP0[:, None] * np.exp(np.cumsum(dL, axis=1))


def gen_session(seed, dur_min, mode="async", spread_mult=1.0):
    """Ticks d'une session : dict {edge: (ts, bid, ask, vol)} — quotes
    COHÉRENTES au latent (le désynchronisme vient uniquement des horloges).
    mode='sync' : horloge unique partagée -> état publié toujours cohérent
    (contrôle de falsification : aucun edge ne doit survivre aux coûts)."""
    rng = np.random.default_rng(seed)
    n_steps = int(dur_min * 60_000 / DT_MS) + 1
    up = gen_latent(rng, n_steps)
    t_grid = np.arange(n_steps) * DT_MS

    # fenêtres d'élargissement de spread (news) : 0-3 par session
    n_bursts = int(rng.poisson(1.2))
    bursts = []
    for _ in range(n_bursts):
        e = int(rng.integers(0, N))
        t0 = int(rng.integers(0, max(1, n_steps - 1500)) * DT_MS)
        bursts.append((e, t0, t0 + int(rng.integers(5_000, 20_000)),
                       float(rng.uniform(2.5, 4.0))))

    if mode == "sync":
        lam_sync = LAMBDA.sum()
        tt = np.cumsum(rng.exponential(1000.0 / lam_sync, int(dur_min * 60 * lam_sync)))
        tt = tt[tt < dur_min * 60_000]
        clocks = {e: tt for e in range(N)}
    else:
        clocks = {e: np.cumsum(rng.exponential(1000.0 / LAMBDA[e],
                                             int(dur_min * 60 * LAMBDA[e] * 1.3)))
                  for e in range(N)}
        clocks = {e: tt[tt < dur_min * 60_000] for e, tt in clocks.items()}

    out = {}
    for e, (a, b) in enumerate(PAIR_NODE):
        tt = np.asarray(clocks[e], dtype=np.int64)
        k = np.clip(tt // DT_MS, 0, n_steps - 1)
        mid = up[a, k] / up[b, k]
        half = SPREAD[e] * 0.5 * spread_mult * rng.lognormal(0.0, 0.08, len(tt))
        for (be, bt0, bt1, mult) in bursts:
            if be == e:
                half[(tt >= bt0) & (tt <= bt1)] *= mult
        out[e] = (BASE_TS + tt, mid * (1 - half), mid * (1 + half),
                  np.ones(len(tt)))
    return out, t_grid


def run_session(seed, dur_min, mode="async", params=None, spread_mult=1.0):
    """Une session : ingestion (phase A) puis replay événementiel (phase B)
    sur le moteur Rust — même sémantique que scan()+replay() du POC."""
    p = dict(P, **(params or {}))
    ticks, _ = gen_session(seed, dur_min, mode, spread_mult)
    eng = opengraph.Engine.live()

    # phase A : charge la série complète + médiane (on_tick ignore l'eval)
    events = []
    for e in range(N):
        ts, bid, ask, vol = ticks[e]
        for i in range(len(ts)):
            eng.on_tick(e, int(ts[i]), float(bid[i]), float(ask[i]),
                        float(vol[i]), p["fresh_ms"], p["spread_cap"],
                        p["comm_scale"])
            events.append((int(ts[i]), e))
    events.sort()  # stable : ordre FX à ts égal — comme ev.sort stable

    # phase B : eval par timestamp unique (à ts égal l'état complet <= t est
    # identique — dédoublonnage ; en mode sync ça fait du contrôle le test
    # le plus strict : même les états intermédiaires intra-ts sont éliminés).
    # Verrou "une quote = un trade" : après un fill sur une arête, celle-ci
    # est consommée jusqu'à sa prochaine cotation — sinon le même quote
    # stale serait frappé en boucle (modèle optimiste irréaliste : le
    # premier fill épuise la profondeur exploitable / les arbs concurrents).
    tick_ts = {e: ticks[e][0] for e in range(N)}
    lock_until = np.full(N, -1e18)
    cands = n_pos = evals = consumed = 0
    trades = []
    t_free = -1e18
    last_t = -1
    for t, _e in events:
        if t == last_t:
            continue
        last_t = t
        evals += 1
        ev = eng.eval_at(t, p["fresh_ms"], p["spread_cap"], p["comm_scale"])
        if ev is None:
            continue
        pi, net, ddd = ev
        if net > 0.0:
            n_pos += 1
        if net <= THRESH:
            continue
        cands += 1
        if t < t_free or net < p["ddd_min"]:
            continue
        legs = [FX.index(s) for s in eng.legs(pi)]
        if any(lock_until[e] > t for e in legs):
            consumed += 1  # opportunité déjà arbitrée sur une de ses arêtes
            continue
        r = eng.exec_seq(t, pi, p["lat_ms"], p["tau_slip_bp"], p["q_min"], False)
        if r is None:
            continue
        res, pnl, filled, parts, dur = r
        trades.append(dict(t=t, pi=pi, res=res, pnl=pnl, filled=filled,
                           parts=parts, dur=dur, net_send=net, ddd=ddd))
        for e in legs:
            j = int(np.searchsorted(tick_ts[e], t, "right"))
            lock_until[e] = tick_ts[e][j] if j < len(tick_ts[e]) else 1e18
        t_free = t + max(dur, 1.0) + p["cooldown_s"] * 1000.0

    pnls = np.array([tr["pnl"] for tr in trades])
    return dict(
        seed=seed, mode=mode, events=len(events), evals=evals,
        cands=cands, pos=n_pos, consumed=consumed,
        trades=len(trades),
        aborts=int(sum(tr["res"].startswith("abort") for tr in trades)),
        abort_reasons={r: sum(tr["res"] == r for tr in trades)
                       for r in {tr["res"] for tr in trades} if r != "done"},
        wins=int((pnls > 0).sum()) if len(pnls) else 0,
        pnl=float(pnls.sum()) if len(pnls) else 0.0,
        pnl_mean=float(pnls.mean()) if len(pnls) else 0.0,
        dur_mean=float(np.mean([tr["dur"] for tr in trades])) if trades else 0.0,
        trades_detail=trades,
    )


def summarize(results):
    """Agrégat campagne — métriques définies du rapport."""
    tr = [t for r in results for t in r["trades_detail"]]
    pnls = np.array([t["pnl"] for t in tr])
    sess_pnl = np.array([r["pnl"] for r in results])
    n_tr = len(tr)
    tot_ev = max(1, sum(r["evals"] for r in results))
    return dict(
        sessions=len(results),
        events=int(sum(r["events"] for r in results)),
        evals=int(sum(r["evals"] for r in results)),
        candidates=int(sum(r["cands"] for r in results)),
        pos_evals=int(sum(r["pos"] for r in results)),
        consumed=int(sum(r["consumed"] for r in results)),
        trades=n_tr,
        aborts=int(sum(r["aborts"] for r in results)),
        abort_reasons={k: sum(r["abort_reasons"].get(k, 0) for r in results)
                       for k in {"abort:no quote", "abort:kill IOC", "abort:q<q_min"}},
        send_rate=float(n_tr / max(1, sum(r["cands"] for r in results))),
        opp_per_meval=float(sum(r["pos"] for r in results) / tot_ev * 1e6),
        wr=float((pnls > 0).mean()) if n_tr else 0.0,
        p_loss=float((pnls < 0).mean()) if n_tr else 0.0,
        pnl_total=float(pnls.sum()) if n_tr else 0.0,
        pnl_mean=float(pnls.mean()) if n_tr else 0.0,
        p05=float(np.percentile(pnls, 5)) if n_tr else 0.0,
        p50=float(np.percentile(pnls, 50)) if n_tr else 0.0,
        p95=float(np.percentile(pnls, 95)) if n_tr else 0.0,
        sess_pnl_mean=float(sess_pnl_mean := sess_pnl.mean()) if len(sess_pnl) else 0.0,
        sess_wr=float((sess_pnl > 0).mean()) if len(sess_pnl) else 0.0,
        dur_mean=float(np.mean([t["dur"] for t in tr])) if n_tr else 0.0,
    )


def main():
    n_sessions = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    dur_min = float(sys.argv[2]) if len(sys.argv) > 2 else 45.0
    mode = sys.argv[3] if len(sys.argv) > 3 else "all"
    ss = np.random.SeedSequence(20261102)
    seeds = [int(s.generate_state(1)[0]) for s in ss.spawn(n_sessions + 8)]
    t0 = time.time()
    out = {"seed_master": 20261102, "n_sessions": n_sessions,
           "dur_min": dur_min, "params": P, "scenarios": {}}

    def campaign(name, seeds, **kw):
        rs = [run_session(sd, dur_min, **kw) for sd in seeds]
        s = summarize(rs)
        out["scenarios"][name] = dict(summary=s,
                                      sessions=[{k: v for k, v in r.items()
                                                 if k != "trades_detail"}
                                                for r in rs])
        print(f"[{name}] sessions={s['sessions']} trades={s['trades']} "
              f"aborts={s['aborts']} wr={s['wr']:.1%} "
              f"pnl/sess={s['sess_pnl_mean']:+.2f}$ "
              f"({time.time() - t0:.0f}s)", flush=True)
        return s

    if mode in ("all", "sync"):
        # contrôle de falsification : horloge commune, état toujours
        # cohérent -> aucune opportunité positive ne doit survivre aux coûts
        s = campaign("sync_control", seeds[:4], mode="sync")
        out["falsified"] = bool(s["trades"] and
                                (s["wr"] > 0.20 or s["pnl_mean"] > 0))
    if mode in ("all", "async"):
        campaign("async_main", seeds[:n_sessions])
    sweep_n = min(20, n_sessions)
    if mode in ("all", "lat_sweep"):
        for lat in (0.5, 2.5, 5.0, 10.0, 25.0):
            campaign(f"lat_{lat}ms", seeds[:sweep_n], params=dict(lat_ms=lat))
    if mode in ("all", "spread_sweep"):
        for sm in (0.5, 1.0, 2.0):
            campaign(f"spread_x{sm}", seeds[:sweep_n], spread_mult=sm)

    out["elapsed_s"] = time.time() - t0
    dst = Path(__file__).parent.parent / "MC" / "mc_realiste_results.json"
    dst.write_text(json.dumps(out, indent=1))
    print(f"-> {dst} ({out['elapsed_s']:.0f}s)")


if __name__ == "__main__":
    main()
