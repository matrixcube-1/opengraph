#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# calibrate.py -- calibration du scalp dormant OgArb a partir du journal JSONL.
#
# Journal : %Common%\Files\ogarb_journal.jsonl  (une ligne JSON par evenement)
#   FILL   {sym,side,lots,px[,qtyTo|ddd,dormMs,slip,scalp]}
#   EXEC   {path,filled,usdEnd|ddd,scalp,p}
#   CLOSE  {pi,legs[,allN],deals,pl,comm,swap,net,why,p}
#   OPP    {net,pi,ddd,p[,q]}   EXECSKIP {pi,leg,err,p}   SCALPSKIP {pi,err,p}
#   HB/EVT/ABORT : contexte seulement.
#
# Tolerance : anciennes lignes FILL sans wts/dormMs/ddd (ddd est alors recupere
# sur l'EXEC{scalp:1,ddd} adjacent, sinon l'OPP declencheur) ; lignes malformees
# comptees puis ignorees.
#
# Appariement FILL->CLOSE : l'EA impose un seul panier/scalp ouvert a la fois
# (garde gB.open), donc les fills entre deux CLOSE forment un episode. Clefs
# explicites (posId/tkt/pi sur FILL) utilisees si un jour elles existent ;
# sinon regroupement temporel + validation sym dans le chemin CLOSE.p.
#
# Sorties : E[capture|dormant] + IC bootstrap, distribution dormMs, lambda
# fills/h par heure serveur (EET), seuils recommandes (stalePxMs, k*comm,
# holdSec), falsification des previsions figees de mc_scalp_results.json.
# stdlib seul.  Usage :  python calibrate.py [--journal J] [--mc M] [--out O]
#                        [--dormant-ms 1000] [--boot 10000] [--selftest]
import argparse, json, math, random, re, statistics, sys, time
from collections import Counter, defaultdict

JOURNAL_DEF = r"C:\Users\EVROSIS\AppData\Roaming\MetaQuotes\Terminal\Common\Files\ogarb_journal.jsonl"
MC_DEF      = r"C:\arb_work\og-mql5\mc_scalp_results.json"
OUT_DEF     = r"C:\arb_work\og-mql5\calibrate_results.json"
DORMANT_MS_DEF = 1000        # aligne InpStalePxMs (OgArb.mq5)
K_CUR          = 1.5         # gate actuel ddd >= 1.5*commLeg (snipeScalp)
HOLD_CUR       = 45          # InpHoldSec
STALE_CUR      = 1000        # InpStalePxMs
TZ_EET_H       = 2           # serveur EET = GMT+2 (hiver) ; EEST=+3 en ete
SEED           = 20261115

# Previsions figees du MC (mc_scalp.py lignes 86-92) : {tag:(lam/sem, mu_d)}
# mu_d = E[capture|dormant] du scenario ; lam = fills dormants eligibles /sem.
MC_FROZEN = {"NOEDGE": (15.0, None),
             "PESS":   ( 5.0, 0.20),
             "BASE":   (15.0, 0.70),
             "OPT":    (40.0, 1.10)}

# ---------------------------------------------------------------- utilitaires
def qtile(xs, p):
    """Quantile type-7 (interpolation lineaire), xs non vide."""
    xs = sorted(xs); n = len(xs)
    if n == 1: return xs[0]
    h = (n - 1) * p; i = int(h); f = h - i
    return xs[i] + f * (xs[i + 1] - xs[i]) if i + 1 < n else xs[i]

def describe(xs):
    """Resume distributionnel standard."""
    if not xs:
        return dict(n=0)
    d = dict(n=len(xs), mean=statistics.fmean(xs),
             sd=(statistics.stdev(xs) if len(xs) > 1 else 0.0),
             min=min(xs), p5=qtile(xs, .05), p25=qtile(xs, .25),
             p50=qtile(xs, .50), p75=qtile(xs, .75), p95=qtile(xs, .95),
             max=max(xs), sum=sum(xs))
    return d

def boot_ci(xs, B=10000, seed=SEED):
    """IC bootstrap percentile 95% sur la moyenne. Retourne (lo,hi,dist_mean)."""
    n = len(xs)
    if n == 0: return None, None, None
    rng = random.Random(seed)
    means = []
    for _ in range(B):
        s = statistics.fmean(xs[rng.randrange(n)] for _ in range(n))
        means.append(s)
    return qtile(means, .025), qtile(means, .975), statistics.fmean(means)

def norm_ci(xs):
    """IC normal 95% sur la moyenne (repere ; bootstrap fait foi)."""
    n = len(xs)
    if n < 2: return None, None
    m = statistics.fmean(xs); se = statistics.stdev(xs) / math.sqrt(n)
    return m - 1.96 * se, m + 1.96 * se

def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None

# ------------------------------------------------------------------- parsing
def parse_journal(path):
    """Lit le JSONL ligne a ligne ; tolerant aux lignes anciennes/malformees."""
    evs, bad = [], 0
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line: continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                bad += 1; continue
            if not isinstance(e, dict) or "type" not in e:
                bad += 1; continue
            evs.append(e)
    counts = Counter(e["type"] for e in evs)
    return evs, counts, bad

# -------------------------------------------------------------- episodes fills
def _ts_of(e):
    """Horodatage monotone : wts (epoch s) prioritaire car ts reset par session."""
    return fnum(e.get("wts")), fnum(e.get("ts"))

def group_episodes(evs):
    """Regroupe FILL -> EXEC -> CLOSE en episodes (invariant : 1 panier ouvert).

    Retourne [{fills, exec, opp, close, opened_wts}]. Un CLOSE isole produit un
    episode vide (orphelin) ; un episode non ferme en fin/fond de session garde
    close=None."""
    eps, cur = [], None
    last_opp = None                      # dernier OPP vu (quel que soit pi)
    for e in evs:
        t = e["type"]
        if t == "OPP":
            last_opp = e
        elif t == "EVT" and "start" in str(e.get("msg", "")):
            if cur and cur["fills"]:     # session morte panier ouvert
                cur["session_end"] = True; eps.append(cur)
            cur = None
        elif t == "FILL":
            if cur is None:
                cur = dict(fills=[], exec=None, opp=last_opp, close=None,
                           session_end=False)
            # clef explicite si un jour journalisee (posId/tkt/pi sur FILL)
            cur["fills"].append(e)
        elif t == "EXEC":
            if cur is not None and cur["exec"] is None:
                cur["exec"] = e          # 1er EXEC apres fills = le notre
            # EXEC orphelin (basket refuser apres fills?) : ignore
        elif t == "CLOSE":
            if cur is None:
                cur = dict(fills=[], exec=None, opp=None, close=e,
                           session_end=False, orphan_close=True)
            else:
                cur["close"] = e
            eps.append(cur); cur = None
    if cur is not None:
        eps.append(cur)
    return eps

def _sym_in_path(sym, p):
    """Verifie que la jambe figure dans 's0=USD ->USDCAD+ ->GBPCAD- end=GBP'."""
    if not sym or not p: return None
    return sym in re.findall(r"[A-Z]{6}", p)

def episode_records(ep):
    """Un enregistrement par fill : jointure close + exec + opp + metriques."""
    close, ex, opp = ep.get("close"), ep.get("exec"), ep.get("opp")
    pi = None
    if ex:    pi = ex.get("path", ex.get("pi"))
    if pi is None and close: pi = close.get("pi")
    if pi is None and opp:   pi = opp.get("pi")
    ddd_ep = None
    if ex  and fnum(ex.get("ddd")):  ddd_ep = fnum(ex["ddd"])      # EXEC scalp
    if opp and fnum(opp.get("ddd")): ddd_opp = fnum(opp["ddd"])
    else:                          ddd_opp = None
    recs = []
    for f in ep["fills"]:
        r = dict(sym=f.get("sym"), side=f.get("side"), lots=fnum(f.get("lots")),
                 px=fnum(f.get("px")), qty_to=fnum(f.get("qtyTo")),
                 scalp=(f.get("scalp") == 1) or bool(ex and ex.get("scalp")),
                 kind="basket", pi=pi, pos_id=f.get("posId", f.get("tkt")),
                 dorm_ms=fnum(f.get("dormMs")), slip=fnum(f.get("slip")))
        r["kind"] = "scalp" if r["scalp"] else "basket"
        # ddd : champ FILL nouveau format > EXEC scalp > OPP declencheur
        r["ddd"] = fnum(f.get("ddd"))
        if r["ddd"] is None: r["ddd"] = ddd_ep
        if r["ddd"] is None: r["ddd"] = ddd_opp
        r["ddd_src"] = ("fill" if fnum(f.get("ddd")) is not None else
                        "exec" if ddd_ep is not None else
                        "opp"  if ddd_opp is not None else None)
        fw, ft = _ts_of(f)
        r["wts"], r["ts"] = fw, ft
        if close:
            cw, ct = _ts_of(close)
            r["close_wts"] = cw
            # duree : wts monotone inter-sessions ; ts en sec sinon
            if fw is not None and cw is not None:
                r["dur_s"] = cw - fw
            elif ft is not None and ct is not None:
                r["dur_s"] = (ct - ft) / 1000.0
            else:
                r["dur_s"] = None
            for k in ("pl", "comm", "swap", "net"):
                r[k] = fnum(close.get(k))
            r["why"]   = close.get("why")
            r["deals"] = close.get("deals")
            r["path"]  = close.get("p")
            r["sym_in_path"] = _sym_in_path(r["sym"], r["path"])
            r["capture"]     = (r["pl"]  / r["ddd"]
                                if r["ddd"] and r["pl"]  is not None else None)
            r["capture_net"] = (r["net"] / r["ddd"]
                                if r["ddd"] and r["net"] is not None else None)
            r["win"] = (r["net"] > 0) if r["net"] is not None else None
        else:
            r.update(close_wts=None, dur_s=None, pl=None, comm=None, swap=None,
                     net=None, why=None, deals=None, path=None,
                     sym_in_path=None, capture=None, capture_net=None, win=None)
        recs.append(r)
    return recs

def dormant_records(recs, thr_ms):
    """Filtre dormant : dormMs>=thr, ou scalp:1 (le gate EA dormance>staleMs a
    deja trie ; les anciennes lignes n'ont pas dormMs -> preuve implicite)."""
    out = []
    for r in recs:
        if r["close_wts"] is None and r["why"] is None:   # fill sans close
            r = dict(r, open_unclosed=True)
        d = r["dorm_ms"]
        r["dormant"] = (d is not None and d >= thr_ms) or r["scalp"]
        # borne inferieure censuree : un scalp:1 a survecu au gate staleMs
        r["dorm_ms_lb"] = d if d is not None else (thr_ms if r["scalp"] else None)
        out.append(r)
    return [r for r in out if r["dormant"]]

# ----------------------------------------------------------------- statistiques
def rate_report(dormant, evs, tz_h):
    """lambda dormant-fills/h : sur heures actives (bucket contenant >=1 event)
    et par heure serveur (EET). IC Poisson approx-normal sur le comptage."""
    active = set()                 # (date_utc, heure_serveur) observes
    for e in evs:
        w = fnum(e.get("wts"))
        if w is None: continue
        d = time.gmtime(w + tz_h * 3600)
        active.add((d.tm_year, d.tm_yday, d.tm_hour))
    active_h = len(active)
    ws = [fnum(e.get("wts")) for e in evs if fnum(e.get("wts")) is not None]
    span_h = (max(ws) - min(ws)) / 3600.0 if len(ws) > 1 else 0.0
    per_h = Counter(); act_per_h = Counter()
    for (y, dy, h) in active:
        act_per_h[h] += 1
    for r in dormant:
        if r["wts"] is None: continue
        h = time.gmtime(r["wts"] + tz_h * 3600).tm_hour
        per_h[h] += 1
    n = len(dormant)
    lam_h = n / active_h if active_h else None
    lam_wk = lam_h * 24 * 7 if lam_h is not None else None
    # IC Poisson ~n +/- 1.96*sqrt(n) sur le comptage -> translate en taux
    ci_wk = (None, None)
    if n > 0 and active_h:
        lo_n = max(0.0, n - 1.96 * math.sqrt(n))
        hi_n = n + 1.96 * math.sqrt(n)
        ci_wk = (lo_n / active_h * 168, hi_n / active_h * 168)
    table = [dict(hour_srv=h, n_fills=per_h.get(h, 0), hours_obs=act_per_h[h],
                  lam=per_h.get(h, 0) / act_per_h[h])
             for h in sorted(act_per_h)]
    return dict(active_hours=active_h, span_hours=span_h, n_fills=n,
                lambda_h=lam_h, lambda_week=lam_wk,
                lambda_week_ci95=list(ci_wk), per_hour=table)

def leader_dorm_samples(evs):
    """dormMs cote leader : SCALPSKIP err='px vivant Xms' = age du prix quand
    vivant -> echantillon de dormance des jambes NON dormantes."""
    vals, unlabeled = [], 0
    for e in evs:
        if e["type"] != "SCALPSKIP": continue
        err = str(e.get("err", ""))
        m = re.search(r"px vivant\s*(\d+)\s*ms", err)
        if m:   vals.append(float(m.group(1)))
        elif "px vivant" in err: unlabeled += 1
    return vals, unlabeled

def reco_stale(dormant, leaders, cur=STALE_CUR):
    """stalePxMs optimal = quantile separant dormant/leader.
    t* = sqrt(P95_leader * P5_dormant) borne par (max_leader, min_dormant)."""
    d_obs = [r["dorm_ms"] for r in dormant if r["dorm_ms"] is not None]
    d_lb  = [r["dorm_ms_lb"] for r in dormant if r["dorm_ms_lb"] is not None]
    out = dict(current=cur, leader_obs=leaders, n_dormant_known=len(d_obs),
               n_dormant_censored=len(d_lb) - len(d_obs))
    if not leaders and not d_obs:
        out.update(recommended=cur, rule="pas de donnees : conserver le seuil")
        return out
    hi = max(leaders) if leaders else 0.0
    lo = min(d_obs) if d_obs else (min(d_lb) if d_lb else cur)
    if leaders and (d_obs or d_lb):
        p95l = qtile(leaders, .95)
        p5d  = qtile(d_obs, .05) if d_obs else lo
        t = math.sqrt(p95l * p5d) if p95l > 0 and p5d > 0 else cur
        t = min(max(t, hi), lo)                    # rester dans la separation
        out.update(recommended=round(t),
                   rule=("geometrique P95leader=%.0f x P5dormant=%.0f, bornee "
                         "dans [maxLeader=%.0f, minDormant=%.0f]"
                         % (p95l, p5d, hi, lo)))
    elif leaders:
        out.update(recommended=round(max(hi * 4, cur)),
                   rule="que des leaders : garder marge x4 au-dessus du max")
    else:
        out.update(recommended=round(max(lo * 0.5, 50)),
                   rule="que des dormants : descendre a ~P5/2 pour capter plus")
    return out

def reco_kcomm(dormant, k_lo=0.5, k_hi=4.0, step=0.25, cur=K_CUR):
    """Grille k : retient fills avec ddd >= k*commRT ; maximise E[net|pass].
    commRT = |comm| du CLOSE (aller+retour), replie sur 7$/lot si absent."""
    grid = []
    k = k_lo
    while k <= k_hi + 1e-9:
        sel = []
        for r in dormant:
            comm = abs(r["comm"]) if r["comm"] is not None else \
                   (7.0 * r["lots"] if r["lots"] else None)
            if r["ddd"] is None or comm in (None, 0): continue
            if r["ddd"] >= k * comm: sel.append(r)
        nets = [r["net"] for r in sel if r["net"] is not None]
        caps = [r["capture"] for r in sel if r["capture"] is not None]
        grid.append(dict(k=round(k, 2), n_pass=len(sel),
                         sum_net=round(sum(nets), 4) if nets else None,
                         mean_net=statistics.fmean(nets) if nets else None,
                         p_win=(sum(1 for x in nets if x > 0) / len(nets)
                                if nets else None),
                         mean_cap=(statistics.fmean(caps) if caps else None)))
        k += step
    best = None
    feas = [g for g in grid if g["n_pass"] >= 1 and
            g["mean_net"] is not None and g["mean_net"] > 0]
    if feas:
        best = feas[0]["k"]                        # plus petit k profitable
    return dict(current=cur, grid=grid, recommended=best,
                note=("aucun k ne rend E[net]>0 sur l'echantillon -> l'edge "
                      "capture n'est pas demontre, pas un probleme de seuil"
                      if best is None else
                      "plus petit k avec E[net|pass]>0"))

CONV_WHY = {"conv", "tpnet"}                       # sorties = convergence realisee
def reco_hold(dormant, cur=HOLD_CUR):
    """holdSec : quantile de la duree jusqu'a convergence (why conv/tpnet).
    Signale aussi les sorties 'hold' avec pl>0 (trunquees avant convergence)."""
    conv = [r["dur_s"] for r in dormant
            if r["why"] in CONV_WHY and r["dur_s"] is not None]
    cut  = [r for r in dormant if r["why"] == "hold"
            and r["pl"] is not None and r["pl"] > 0]
    out = dict(current=cur, conv_durations=conv,
               n_hold_truncated_pospl=len(cut))
    if conv:
        rec = max(math.ceil(qtile(conv, .90)), math.ceil(max(conv)))
        out.update(recommended=int(rec),
                   rule="ceil(P90 duree convergence) ; n=%d" % len(conv))
    else:
        out.update(recommended=cur,
                   rule="pas de convergence observee : conserver")
    if cut:
        out["note"] = ("%d sortie(s) 'hold' avec pl brut>0 : la convergence "
                       "n'etait pas finie a %ds" % (len(cut), cur))
    return out

# ------------------------------------------------------------- falsification
def falsify(mc_path, dormant, lam_rep):
    """Compare l'observe aux previsions figees de mc_scalp_results.json.
    mu_d : falsifie si hors IC bootstrap 95% de E[capture|dormant].
    lam  : falsifie si hors IC Poisson 95% du taux hebdomadaire."""
    try:
        mc = json.load(open(mc_path, "r", encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        mc = []
    caps = [r["capture"] for r in dormant if r["capture"] is not None]
    lo, hi, _ = boot_ci(caps) if caps else (None, None, None)
    lam_obs, lam_ci = lam_rep["lambda_week"], lam_rep["lambda_week_ci95"]
    obs = dict(mu_d=statistics.fmean(caps) if caps else None,
               mu_d_ci95=[lo, hi] if lo is not None else None,
               lambda_week=lam_obs, lambda_week_ci95=lam_ci,
               n_dormant=len(dormant))
    rows = []
    for s in mc:
        tag = s.get("tag")
        lam_f, mu_f = MC_FROZEN.get(tag, (None, None))
        v_mu, v_lam = "n/a", "n/a"
        if mu_f is not None and lo is not None:
            v_mu = "FALSIFIED" if not (lo <= mu_f <= hi) else "consistent"
        if lam_f is not None and lam_ci[0] is not None:
            v_lam = "FALSIFIED" if not (lam_ci[0] <= lam_f <= lam_ci[1]) \
                    else "consistent"
        verdict = ("FALSIFIED" if "FALSIFIED" in (v_mu, v_lam)
                   else "consistent" if (v_mu, v_lam) != ("n/a", "n/a")
                   else "n/a")
        rows.append(dict(tag=tag, lam_frozen=lam_f, mu_d_frozen=mu_f,
                         mc_p50=s.get("p50"), mc_pwin=s.get("pwin"),
                         mc_ruin=s.get("ruin"), mc_trades=s.get("trades"),
                         verdict_mu=v_mu, verdict_lam=v_lam, verdict=verdict))
    # implication economique : capture de breakeven = commRT/ddd par fill
    be = []
    for r in dormant:
        comm = abs(r["comm"]) if r["comm"] is not None else \
               (7.0 * r["lots"] if r["lots"] else None)
        if r["ddd"] and comm: be.append(comm / r["ddd"])
    nets = [r["net"] for r in dormant if r["net"] is not None]
    obs["breakeven_capture_mean"] = statistics.fmean(be) if be else None
    obs["e_net_per_fill"] = statistics.fmean(nets) if nets else None
    return dict(observed=obs, scenarios=rows)

# ------------------------------------------------------------------- rapport
def build_report(journal, evs, counts, bad, thr_ms, boot, tz_h, mc_path):
    eps = group_episodes(evs)
    recs = [r for ep in eps for r in episode_records(ep)]
    dorm = dormant_records(recs, thr_ms)
    caps = [r["capture"] for r in dorm if r["capture"] is not None]
    nets = [r["net"] for r in dorm if r["net"] is not None]
    dms  = [r["dorm_ms"] for r in dorm if r["dorm_ms"] is not None]
    leaders, leaders_unlab = leader_dorm_samples(evs)
    lam = rate_report(dorm, evs, tz_h)
    blo, bhi, _ = boot_ci(caps, B=boot) if caps else (None, None, None)
    nlo, nhi = norm_ci(caps) if caps else (None, None)
    nlo_n, nhi_n = norm_ci(nets) if nets else (None, None)
    durs = defaultdict(list)
    for r in dorm:
        if r["dur_s"] is not None: durs[r["why"] or "?"].append(r["dur_s"])
    rep = dict(
        generated_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        journal=journal, dormant_ms=thr_ms, tz_server="GMT+%d" % tz_h,
        n_events=len(evs), n_bad_lines=bad, counts=dict(counts),
        episodes=dict(
            n=len(eps),
            n_scalp=sum(1 for e in eps
                        if any(f.get("scalp") for f in e["fills"])),
            n_basket=sum(1 for e in eps if len(e["fills"]) > 1),
            n_orphan_close=sum(1 for e in eps if e.get("orphan_close")),
            n_open=sum(1 for e in eps if e["fills"] and e["close"] is None)),
        dormant=dict(
            n=len(dorm),
            fills=[{k: r[k] for k in
                    ("sym", "side", "lots", "px", "wts", "ts", "pi", "kind",
                     "ddd", "ddd_src", "dorm_ms", "dorm_ms_lb", "slip", "pl",
                     "comm", "swap", "net", "why", "deals", "dur_s", "capture",
                     "capture_net", "win", "sym_in_path", "path")} for r in dorm],
            capture=dict(describe(caps),
                         ci95_boot=[blo, bhi] if blo is not None else None,
                         ci95_norm=[nlo, nhi] if nlo is not None else None),
            net=dict(describe(nets),
                     ci95_norm=[nlo_n, nhi_n] if nlo_n is not None else None),
            dorm_ms=dict(describe(dms),
                         n_censored=sum(1 for r in dorm
                                        if r["dorm_ms"] is None)),
            outcomes=dict(Counter(r["why"] or "open" for r in dorm)),
            dur_s_by_why={k: describe(v) for k, v in sorted(durs.items())}),
        lambda_=lam,
        leaders=dict(dorm_ms_obs=leaders, n_unlabeled=leaders_unlab,
                     n_scalpskip=counts.get("SCALPSKIP", 0),
                     n_execskip_scalp=sum(
                         1 for e in evs if e["type"] == "EXECSKIP"
                         and e.get("leg") == "scalp")),
        thresholds=dict(stale=reco_stale(dorm, leaders),
                        kcomm=reco_kcomm(dorm),
                        hold=reco_hold(dorm)),
        falsification=falsify(mc_path, dorm, lam))
    return rep

def _f(x, nd=2):
    return ("%.*f" % (nd, x)) if isinstance(x, (int, float)) else str(x)

def render_console(rep):
    L = []
    A = L.append
    A("=" * 78)
    A("CALIBRATION SCALP DORMANT -- %s" % rep["generated_utc"])
    A("journal : %s" % rep["journal"])
    A("%d events (%s) | %d lignes ignorees" %
      (rep["n_events"],
       ",".join("%s:%d" % kv for kv in sorted(rep["counts"].items())),
       rep["n_bad_lines"]))
    A("episodes: %d (scalp:%d basket:%d orphelins:%d ouverts:%d)" %
      (rep["episodes"]["n"], rep["episodes"]["n_scalp"],
       rep["episodes"]["n_basket"], rep["episodes"]["n_orphan_close"],
       rep["episodes"]["n_open"]))
    d = rep["dormant"]
    A("\n-- FILLS DORMANTS (dormMs>=%d ou scalp:1)  n=%d %s" %
      (rep["dormant_ms"], d["n"], "-" * 20))
    A("%-7s %-4s %-5s %-7s %-6s %-7s %-6s %-8s %-5s %-6s" %
      ("sym", "side", "lots", "ddd", "dormMs", "pl", "net", "capture", "why",
       "dur_s"))
    for r in d["fills"]:
        A("%-7s %-4s %-5s %-7s %-6s %-7s %-6s %-8s %-5s %-6s" %
          (r["sym"], r["side"], _f(r["lots"]), _f(r["ddd"]),
           _f(r["dorm_ms"], 0) if r["dorm_ms"] is not None else ">=%d" %
           rep["dormant_ms"], _f(r["pl"]), _f(r["net"]),
           _f(r["capture"]), r["why"], _f(r["dur_s"], 1)))
    c = d["capture"]
    if c.get("n"):
        A("\nE[capture|dormant] = %.3f  sd=%.3f  IC95%% boot=[%.3f, %.3f]"
          "  IC95%% norm=[%.3f, %.3f]" %
          (c["mean"], c["sd"], *(c["ci95_boot"] or [float('nan')] * 2),
           *(c["ci95_norm"] or [float('nan')] * 2)))
        A("capture p5=%.3f p50=%.3f p95=%.3f" % (c["p5"], c["p50"], c["p95"]))
    n = d["net"]
    if n.get("n"):
        A("E[net|dormant]   = %.3f$  sum=%.3f$  IC95%% norm=[%.3f, %.3f]" %
          (n["mean"], n["sum"], *(n["ci95_norm"] or [float('nan')] * 2)))
    dm = d["dorm_ms"]
    if dm.get("n"):
        A("dormMs connus n=%d (+%d censures>=%d) : p5=%.0f p50=%.0f p95=%.0f "
          "max=%.0f" % (dm["n"], dm["n_censored"], rep["dormant_ms"],
                        dm["p5"], dm["p50"], dm["p95"], dm["max"]))
    elif dm.get("n_censored"):
        A("dormMs : aucun champ nouveau format ; %d fills censures >=%d ms"
          % (dm["n_censored"], rep["dormant_ms"]))
    A("outcomes: %s" % ", ".join("%s=%d" % kv for kv in
                                 sorted(d["outcomes"].items())))
    if d["dur_s_by_why"]:
        A("durees s par why : " + "; ".join(
            "%s n=%d p50=%.0f max=%.0f" %
            (k, v["n"], v["p50"], v["max"])
            for k, v in d["dur_s_by_why"].items()))
    la = rep["lambda_"]
    A("\n-- LAMBDA dormant-fills/h (heure serveur %s) %s" %
      (rep["tz_server"], "-" * 30))
    A("heures actives observees=%d span=%.1fh | lambda=%.2f/h -> %.0f/sem "
      "IC95%%=[%.0f, %.0f]" %
      (la["active_hours"], la["span_hours"], la["lambda_h"] or 0,
       la["lambda_week"] or 0, *(la["lambda_week_ci95"] or [0, 0])))
    A("%-4s %-7s %-9s %-6s" % ("h", "fills", "hrs_obs", "lam"))
    for r in la["per_hour"]:
        if r["n_fills"]:
            A("%-4d %-7d %-9d %-6.2f" %
              (r["hour_srv"], r["n_fills"], r["hours_obs"], r["lam"]))
    ld = rep["leaders"]
    A("\nleaders (px vivant) : %s | SCALPSKIP=%d EXECSKIP-scalp=%d" %
      (ld["dorm_ms_obs"] or "[]", ld["n_scalpskip"], ld["n_execskip_scalp"]))
    t = rep["thresholds"]
    A("\n-- SEUILS RECOMMANDES %s" % ("-" * 52))
    s = t["stale"]
    A("stalePxMs : actuel=%d -> recommande=%s | %s" %
      (s["current"], s.get("recommended"), s.get("rule")))
    kg = t["kcomm"]
    A("k*comm    : actuel=%.2f -> recommande=%s | %s" %
      (kg["current"], kg["recommended"], kg["note"]))
    A("%-5s %-6s %-8s %-8s %-6s %-8s" %
      ("k", "n", "sumNet", "E[net]", "Pwin", "E[capt]"))
    for g in kg["grid"]:
        if g["n_pass"]:
            A("%-5.2f %-6d %-8s %-8s %-6s %-8s" %
              (g["k"], g["n_pass"], _f(g["sum_net"]), _f(g["mean_net"]),
               _f(g["p_win"]), _f(g["mean_cap"])))
    h = t["hold"]
    A("holdSec   : actuel=%d -> recommande=%s | %s %s" %
      (h["current"], h["recommended"], h["rule"], h.get("note", "")))
    fz = rep["falsification"]
    ob = fz["observed"]
    A("\n-- FALSIFICATION vs mc_scalp_results.json %s" % ("-" * 27))
    A("observe : mu_d=%s IC95=%s | lam/sem=%s IC95=%s | E[net]=%s$ "
      "breakeven=%s" %
      (_f(ob["mu_d"]), ob["mu_d_ci95"], _f(ob["lambda_week"], 1),
       ob["lambda_week_ci95"], _f(ob["e_net_per_fill"]),
       _f(ob["breakeven_capture_mean"])))
    A("%-7s %-8s %-8s %-7s %-7s %-9s %-9s %-10s" %
      ("tag", "lam_fig", "mu_fig", "mc_p50", "mc_pwin", "v_mu", "v_lam",
       "VERDICT"))
    for r in fz["scenarios"]:
        A("%-7s %-8s %-8s %-7s %-7s %-9s %-9s %-10s" %
          (r["tag"], _f(r["lam_frozen"], 0), _f(r["mu_d_frozen"]),
           _f(r["mc_p50"], 0), _f(r["mc_pwin"], 3), r["verdict_mu"],
           r["verdict_lam"], r["verdict"]))
    return "\n".join(L)

# ------------------------------------------------------------------ selftest
SELFTEST = """\
{"type":"EVT","ts":10,"msg":"OgArb start 28 syms 2072 paths live=false acct=HEDGING"}
{"type":"OPP","ts":1000,"wts":100000,"net":0.9,"pi":5,"ddd":1.0000,"p":"s0=USD ->USDCAD+ ->GBPCAD- end=GBP"}
{"type":"FILL","ts":1500,"wts":100000,"sym":"GBPCAD","side":"BUY","lots":0.07,"px":1.88,"ddd":1.0000,"dormMs":2500,"slip":0.0,"scalp":1}
{"type":"EXEC","ts":1500,"wts":100000,"path":5,"filled":1,"scalp":1,"ddd":1.0000,"p":"s0=USD ->USDCAD+ ->GBPCAD- end=GBP"}
{"type":"CLOSE","ts":8500,"wts":100007,"pi":5,"legs":1,"allN":1,"deals":2,"pl":0.50,"comm":-0.50,"swap":0.0,"net":0.00,"why":"tpnet","p":"s0=USD ->USDCAD+ ->GBPCAD- end=GBP"}
{"type":"OPP","ts":20000,"wts":100020,"net":0.7,"pi":7,"ddd":0.8000,"p":"s0=USD ->USDCAD+ ->EURCAD- end=EUR"}
{"type":"FILL","ts":20500,"wts":100020,"sym":"EURCAD","side":"BUY","lots":0.09,"px":1.57,"scalp":1}
{"type":"EXEC","ts":20500,"wts":100020,"path":7,"filled":1,"scalp":1,"ddd":0.8000,"p":"s0=USD ->USDCAD+ ->EURCAD- end=EUR"}
{"type":"CLOSE","ts":50500,"wts":100050,"pi":7,"legs":1,"allN":1,"deals":2,"pl":-0.40,"comm":-0.64,"swap":0.0,"net":-1.04,"why":"stop","p":"s0=USD ->USDCAD+ ->EURCAD- end=EUR"}
{"type":"FILL","ts":60000,"wts":100060,"sym":"USDCAD","side":"SELL","lots":0.10,"px":1.42,"qtyTo":14200.0}
{"type":"FILL","ts":60400,"wts":100060,"sym":"EURCAD","side":"BUY","lots":0.09,"px":1.57,"qtyTo":9000.0}
{"type":"FILL","ts":60800,"wts":100060,"sym":"EURUSD","side":"SELL","lots":0.09,"px":1.10,"qtyTo":9900.0}
{"type":"EXEC","ts":60800,"wts":100060,"path":1,"filled":3,"usdEnd":9900.0,"p":"s0=USD ->USDCAD+ ->EURCAD- end=EUR"}
{"type":"CLOSE","ts":106000,"wts":100106,"pi":1,"legs":3,"allN":3,"deals":6,"pl":-1.00,"comm":-1.98,"swap":0.0,"net":-2.98,"why":"hold","p":"s0=USD ->USDCAD+ ->EURCAD- end=EUR"}
{"type":"SCALPSKIP","ts":110000,"wts":100110,"pi":9,"err":"px vivant 40ms","p":"s0=USD ->USDCAD+ ->CADJPY+ end=JPY"}
ligne pourrie {{{ pas json
{"type":"HB","ts":120000,"wts":100120,"ticks":240,"warmed":"28/28"}
"""
def run_selftest():
    import tempfile, os
    fd, p = tempfile.mkstemp(suffix=".jsonl"); os.write(fd, SELFTEST.encode()); os.close(fd)
    try:
        evs, counts, bad = parse_journal(p)
    finally:
        os.unlink(p)
    ok = True
    def chk(name, cond):
        nonlocal ok; ok &= bool(cond); print(("PASS" if cond else "FAIL"), name)
    chk("bad=1", bad == 1)
    eps = group_episodes(evs)
    chk("3 episodes", len(eps) == 3)
    recs = [r for ep in eps for r in episode_records(ep)]
    chk("5 fills", len(recs) == 5)
    dorm = dormant_records(recs, 1000)
    chk("2 dormants (scalp:1)", len(dorm) == 2)
    r0 = dorm[0]
    chk("capture=0.5", abs(r0["capture"] - 0.5) < 1e-9)
    chk("dur=7s", abs(r0["dur_s"] - 7) < 1e-9)
    chk("sym_in_path", r0["sym_in_path"] is True)
    r1 = dorm[1]
    chk("ddd recupere d'EXEC", r1["ddd"] == 0.8 and r1["ddd_src"] == "exec")
    chk("dormMs None -> censure", r1["dorm_ms"] is None and r1["dorm_ms_lb"] == 1000)
    chk("capture negative", abs(r1["capture"] + 0.5) < 1e-9)
    chk("why=stop", r1["why"] == "stop")
    leaders, _ = leader_dorm_samples(evs)
    chk("leader=40ms", leaders == [40.0])
    rep = build_report("<selftest>", evs, counts, bad, 1000, 500, 2,
                       "<inexistant>")
    chk("rapport n=2", rep["dormant"]["n"] == 2)
    chk("stale reco dans ]40,2500]",
        40 < rep["thresholds"]["stale"]["recommended"] <= 2500)
    print("SELFTEST", "OK" if ok else "ECHEC")
    return 0 if ok else 1

# ----------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="Calibration scalp dormant OgArb")
    ap.add_argument("--journal", default=JOURNAL_DEF)
    ap.add_argument("--mc", default=MC_DEF)
    ap.add_argument("--out", default=OUT_DEF)
    ap.add_argument("--dormant-ms", type=float, default=DORMANT_MS_DEF)
    ap.add_argument("--boot", type=int, default=10000)
    ap.add_argument("--tz-offset", type=float, default=TZ_EET_H,
                    help="decalage heure serveur vs GMT (EET=2, EEST=3)")
    ap.add_argument("--json", action="store_true", help="JSON sur stdout seul")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    try:
        evs, counts, bad = parse_journal(a.journal)
    except OSError as e:
        print("journal illisible : %s" % e, file=sys.stderr); return 2
    rep = build_report(a.journal, evs, counts, bad, a.dormant_ms, a.boot,
                       a.tz_offset, a.mc)
    txt = render_console(rep)
    if a.json:
        print(json.dumps(rep, indent=1, ensure_ascii=False))
    else:
        print(txt)
    try:
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False)
        print("\n-> %s" % a.out)
    except OSError as e:
        print("ecriture %s impossible : %s" % (a.out, e), file=sys.stderr)
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
