// OgTrade.cs — cBot EXECUTION REELLE (demo NETTED uniquement).
// Port d'OgDetect + pipeline d'execution sequentielle IOC (exec.rs E1-E9).
//
// SECURITE : trades REELS sur le compte attache. Garde-fous :
//   - AllowLive=false -> refuse de tourner sur compte live (demo only)
//   - compte plat exige avant chaque sequence
//   - une seule sequence a la fois, breaker sur echecs consecutifs
//   - unwind immediat des jambes remplies sur abort
//   - flatten des positions Og avant fin de session (21h Zurich -15min)
//
// Execution : ExecuteMarketOrder synchrone (IOC natif : fill immediat
//   top-of-book a 10K — MarketRangeOrder n'existe qu'en async dans
//   cette API) — pas de latence wire donnees : detection et envoi dans
//   le meme OnTick.
//
// Journal : <JournalDir>/YYYY-MM-DD.jsonl — tick + opportunity +
//   leg_send/leg_fill/leg_rej/settle (source de verite reconciliation).
//
// Parametres : economie mesuree 10/11 — comm 4,00/100K (0,40/jambe a
//   10K), fresh 8000 (send-on-change), ddd 0,27 (2,66@100K scale).

// Parametres via [Parameter] (mecanisme standard cAlgo — independant
// du SourceGenerators) : MaxRangeBp = tol IOC 2,08 bp (3xtau, M7) ;
// QMin = fill partiel min ; MinSeqGapMs = cooldown ; MaxConsecFail =
// breaker Mo4 ; AllowLive = FATAL si compte live sans flag.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Robots;

[Robot(AccessRights = AccessRights.FullAccess)]
public partial class OgTrade : Robot
{
    [Parameter("JournalDir", DefaultValue = "C:\\arb_og_trade")]
    public string JournalDir { get; set; }
    [Parameter("FreshMs", DefaultValue = 8000.0)]
    public double FreshMs { get; set; }
    [Parameter("SpreadCap", DefaultValue = 1.76)]
    public double SpreadCap { get; set; }
    [Parameter("CommScale", DefaultValue = 1.10)]
    public double CommScale { get; set; }
    [Parameter("DddMin", DefaultValue = 0.4)]
    public double DddMin { get; set; }
    [Parameter("MaxRangeBp", DefaultValue = 2.08)]
    public double MaxRangeBp { get; set; }
    [Parameter("QMin", DefaultValue = 0.5)]
    public double QMin { get; set; }
    [Parameter("MinSeqGapMs", DefaultValue = 2000)]
    public int MinSeqGapMs { get; set; }
    [Parameter("MaxConsecFail", DefaultValue = 5)]
    public int MaxConsecFail { get; set; }
    [Parameter("AllowLive", DefaultValue = false)]
    public bool AllowLive { get; set; }

    // ---------------- univers Annexe J (ORDRE IDENTIQUE universe::FX)
    static readonly string[] FX = {
        "EURUSD","GBPUSD","USDJPY","USDCHF","AUDUSD","USDCAD","NZDUSD",
        "EURGBP","EURJPY","EURCHF","EURAUD","EURCAD","EURNZD",
        "GBPJPY","GBPCHF","GBPAUD","GBPCAD","GBPNZD",
        "AUDJPY","AUDCHF","AUDCAD","AUDNZD",
        "CADJPY","CADCHF","CHFJPY",
        "NZDJPY","NZDCAD","NZDCHF"
    };
    static readonly string[] CURR = { "USD","EUR","GBP","JPY","CHF","CAD","AUD","NZD" };
    const int NPAIR = 28, NCURR = 8;
    const double NOTIONAL = 10_000.0;      // USD — parite fees.rs NOTIONAL_USD
    const double COMM_100K = 4.00;         // USD/100K/cote — max fills reels
    static readonly double COMM =
        COMM_100K * NOTIONAL / 100_000.0;  // 0,40 $/jambe = Rust comm_usd()
    const int WARMUP = 100, MED_WIN = 512;
    const string LABEL = "OgTrade";

    // etat par paire
    readonly Symbol[] _sym = new Symbol[NPAIR];
    double[] _bid = new double[NPAIR], _ask = new double[NPAIR];
    long[] _lastChg = new long[NPAIR];
    readonly int[] _nTicks = new int[NPAIR];
    readonly List<double>[] _sprWin = new List<double>[NPAIR];
    double[] _med = new double[NPAIR];
    bool _warmLogged = false;

    // tables de chemins (port de paths.rs)
    readonly List<int[]> _paths = new();
    readonly List<int> _pS = new();
    readonly List<int> _pEnd = new();
    readonly List<int[]> _pDir = new();
    readonly List<int[]> _pBase = new();
    readonly int[,] _pairNodes = new int[NPAIR, 2];
    readonly List<int>[] _adj = new List<int>[NCURR];
    readonly int[] _upSym = new int[NCURR];
    readonly bool[] _upDirect = new bool[NCURR];

    // execution
    bool _busy = false;
    long _lastSeq = 0;   // 0, pas MinValue : t-MinValue overflowerait -> gate ferme a jamais
    int _consecFail = 0;

    StreamWriter _j = null; string _jDay = "";

    // =========================================================== boot
    protected override void OnStart()
    {
        Directory.CreateDirectory(JournalDir);   // avant tout LogSys possible
        if (Account.IsLive && !AllowLive)
        {   // garde absolue : jamais de reel sans flag explicite
            Print("[OgTrade] FATAL: compte LIVE — AllowLive=false requis");
            Stop(); return;
        }
        for (int i = 0; i < NCURR; i++) { _adj[i] = new List<int>(); _upSym[i] = -1; }
        for (int e = 0; e < NPAIR; e++)
        {
            _sprWin[e] = new List<double>(MED_WIN + 1);
            _bid[e] = _ask[e] = double.NaN; _lastChg[e] = long.MinValue;
            int a = Ci(FX[e].Substring(0, 3)), b = Ci(FX[e].Substring(3, 3));
            _pairNodes[e, 0] = a; _pairNodes[e, 1] = b;
            _adj[a].Add(e); _adj[b].Add(e);
            _sym[e] = Symbols.GetSymbol(FX[e]);
            if (_sym[e] == null)
            {
                Print("[OgTrade] FATAL: symbole absent {0}", FX[e]);
                Stop(); return;
            }
        }
        for (int c = 0; c < NCURR; c++)
        {
            if (CURR[c] == "USD") continue;
            int d = Array.IndexOf(FX, CURR[c] + "USD");
            if (d >= 0) { _upSym[c] = d; _upDirect[c] = true; continue; }
            d = Array.IndexOf(FX, "USD" + CURR[c]);
            if (d >= 0) { _upSym[c] = d; _upDirect[c] = false; }
        }
        BuildPaths();
        if (_paths.Count != 2072)
        {
            Print("[OgTrade] FATAL: {0} chemins (attendu 2072)", _paths.Count);
            Stop(); return;
        }
        if (!SelfTest())
        { Print("[OgTrade] FATAL: self-test divergent"); Stop(); return; }
        LogSys(string.Format(CultureInfo.InvariantCulture,
            "boot TRADE paths=2072 ddd={0} range_bp={1} warm={2} live={3}",
            DddMin, MaxRangeBp, WARMUP, Account.IsLive));
        Print("[OgTrade] pret — EXECUTION REELLE, notionnel {0} USD", NOTIONAL);
    }

    static int Ci(string c) { return Array.IndexOf(CURR, c); }

    void BuildPaths()
    {
        for (int s0 = 0; s0 < NCURR; s0++)
        {
            var vis = new bool[NCURR]; vis[s0] = true;
            Dfs(s0, s0, vis, new List<int>(), new List<int>(), new List<int>());
        }
    }
    void Dfs(int s0, int node, bool[] vis, List<int> path, List<int> dir, List<int> bas)
    {
        foreach (int e in _adj[node])
        {
            int a = _pairNodes[e, 0], b = _pairNodes[e, 1];
            int nxt = (node == a) ? b : a;
            if (vis[nxt]) continue;
            path.Add(e); dir.Add(node == a ? 1 : -1); bas.Add(a);
            _paths.Add(path.ToArray()); _pS.Add(s0); _pEnd.Add(nxt);
            _pDir.Add(dir.ToArray()); _pBase.Add(bas.ToArray());
            if (path.Count < 3) { vis[nxt] = true; Dfs(s0, nxt, vis, path, dir, bas); vis[nxt] = false; }
            path.RemoveAt(path.Count - 1); dir.RemoveAt(dir.Count - 1); bas.RemoveAt(bas.Count - 1);
        }
    }

    // ========================================================== tick
    protected override void OnTick()
    {
        long t = Now();
        for (int e = 0; e < NPAIR; e++)
        {
            double b = _sym[e].Bid, a = _sym[e].Ask;
            if (!(a > 0 && b > 0) || a < b) continue;
            if (b == _bid[e] && a == _ask[e]) continue;
            _bid[e] = b; _ask[e] = a; _lastChg[e] = t;
            PushSpread(e, a - b);
            LogTick(t, e, b, a);
            if (_nTicks[e] < WARMUP)
            {
                _nTicks[e]++;
                if (!_warmLogged && _nTicks.All(n => n >= WARMUP))
                { _warmLogged = true; LogSys("warm-up complet (" + WARMUP + " ticks/symbole)"); }
            }
        }
        if (!_warmLogged || !InSession(t)) return;
        if (ShouldFlatten(t)) { FlattenOg(); return; }
        if (_busy || t - _lastSeq < MinSeqGapMs) return;
        var r = EvalAt(t);
        if (r == null) return;
        if (r.Value.Net >= DddMin)
        {
            LogOpp(t, r.Value.Pi, r.Value.Net, r.Value.Ddd);
            RunSeq(r.Value.Pi, t);
        }
    }

    // ====================================================== eval_at
    (int Pi, double Net, double Ddd)? EvalAt(long t)
    {
        var ok = new bool[NPAIR];
        for (int i = 0; i < NPAIR; i++)
            ok[i] = _lastChg[i] != long.MinValue && t - _lastChg[i] <= FreshMs;
        double[] up = new double[NCURR], upLiq = new double[NCURR], commLiq = new double[NCURR];
        for (int c = 0; c < NCURR; c++) { up[c] = upLiq[c] = commLiq[c] = double.NaN; }
        up[0] = 1.0; upLiq[0] = 1.0; commLiq[0] = 0.0;
        for (int c = 1; c < NCURR; c++)
        {
            int sj = _upSym[c]; if (sj < 0 || !ok[sj]) continue;
            double mid = 0.5 * (_bid[sj] + _ask[sj]);
            up[c] = _upDirect[c] ? mid : 1.0 / mid;
            upLiq[c] = _upDirect[c] ? _bid[sj] : 1.0 / _ask[sj];
            commLiq[c] = _upDirect[c] ? COMM / mid : COMM;
        }
        var fxOk = new bool[NPAIR];
        for (int i = 0; i < NPAIR; i++)
            fxOk[i] = ok[i] && (_ask[i] - _bid[i]) <= SpreadCap * _med[i];

        double bestNet = double.NegativeInfinity; int bestPi = 0; double bestDdd = 0;
        for (int pi = 0; pi < _paths.Count; pi++)
        {
            double rate = 1.0, comm = 0.0, unwind = 0.0; bool valid = true;
            int[] legs = _paths[pi], dir = _pDir[pi], bas = _pBase[pi];
            for (int li = 0; li < legs.Length; li++)
            {
                int e = legs[li];
                if (!fxOk[e]) { valid = false; break; }
                double ub = up[bas[li]];
                comm += (ub > 0 && !double.IsNaN(ub)) ? COMM / ub : double.NaN;
                rate *= (dir[li] == 1) ? _bid[e] : 1.0 / _ask[e];
                // cloture reelle de la position marge : re-traversee complete
                // du spread + 1 comm (meme formule que l'ouverture)
                double mid = 0.5 * (_bid[e] + _ask[e]);
                unwind += (mid > 0) ? (_ask[e] - _bid[e]) / mid * NOTIONAL : double.NaN;
            }
            if (!valid) continue;
            double upS = up[_pS[pi]];
            double qty0 = (upS > 0 && !double.IsNaN(upS)) ? NOTIONAL / upS : double.NaN;
            double usdOut = qty0 * rate * upLiq[_pEnd[pi]];
            // comm aller-retour : ouverture + cloture par jambe (les clotures
            // SONT la liquidation — pas d'ordre de liq separe, commLiq omise)
            double commRt = 2.0 * comm;
            // la traversee de la liquidation modelisee (terminal != USD) est
            // deja pricee dans usdOut via upLiq -> retrancher ~1 traversee
            int se2 = _upSym[_pEnd[pi]];
            if (se2 >= 0)
            {
                double mid2 = 0.5 * (_bid[se2] + _ask[se2]);
                if (mid2 > 0)
                    unwind -= (_ask[se2] - _bid[se2]) / mid2 * NOTIONAL;
            }
            double commFx = commRt * CommScale;
            if (double.IsNaN(usdOut) || double.IsInfinity(usdOut) || double.IsNaN(commFx) || double.IsInfinity(commFx) || double.IsNaN(unwind))
                continue;
            double net = usdOut - NOTIONAL - commFx - unwind;
            if (net > bestNet) { bestNet = net; bestPi = pi; bestDdd = usdOut - NOTIONAL; }
        }
        if (double.IsNegativeInfinity(bestNet)) return null;
        return (bestPi, bestNet, bestDdd);
    }

    // ================================================== execution
    // IOC sequentiel : jambes dans l'ordre, unwind sur abort,
    // liquidation du residuel en fin de chemin (Convention A).
    void RunSeq(int pi, long t0)
    {
        _busy = true;
        try
        {
            if (!FlatCheck()) { LogSys("seq skip: compte non plat"); return; }
            double upS = Up(_pS[pi]);
            if (!(upS > 0) || double.IsNaN(upS)) { LogSys("seq skip: up[start] NaN"); return; }
            double qty = NOTIONAL / upS;          // devise de depart
            int node = _pS[pi];
            var opened = new List<Position>();
            bool aborted = false; string why = "";
            int[] legs = _paths[pi];

            for (int li = 0; li < legs.Length; li++)
            {
                int e = legs[li];
                int a = _pairNodes[e, 0], b = _pairNodes[e, 1];
                int d = (node == a) ? 1 : -1;
                var sym = _sym[e];
                double px = (d == 1) ? _bid[e] : _ask[e];
                if (!(px > 0)) { why = "no_quote"; aborted = true; break; }
                // volume en unites de base de la paire
                double volBase = (d == 1) ? qty : qty / px;
                double vol = sym.NormalizeVolumeInUnits(volBase, RoundingMode.Down);
                if (vol < sym.VolumeInUnitsMin) { why = "vol<min"; aborted = true; break; }
                // tolerance IOC logicielle : OnTick est serialise donc _bid/_ask
                // n'evoluent pas pendant la sequence, mais sym.Bid/Ask sont
                // des proprietes live mises a jour par la plateforme. Apres
                // ~300 ms de fill de la jambe k-1, on compare le prix live au
                // prix du tick t0 — ecart adverse > MaxRangeBp => abort
                // (meme semantique que tau_slip dans exec_seq).
                if (li > 0)
                {
                    double live = (d == 1) ? sym.Bid : sym.Ask;
                    double devBp = (d == 1) ? (px - live) / px * 1e4
                                            : (live - px) / px * 1e4;
                    if (live > 0 && devBp > MaxRangeBp)
                    {
                        LogLeg(t0, "leg_rej", e,
                               "stale:" + devBp.ToString("F2"), vol, live);
                        why = "stale"; aborted = true; break;
                    }
                }
                var side = (d == 1) ? TradeType.Sell : TradeType.Buy;
                LogLeg(t0, "leg_send", e, side.ToString(), vol, px);
                var res = ExecuteMarketOrder(side, sym.Name, vol, LABEL);
                if (!res.IsSuccessful || res.Position == null)
                {
                    LogLeg(t0, "leg_rej", e, side.ToString(), vol, px);
                    why = "rej"; aborted = true; break;
                }
                var pos = res.Position;
                double filled = pos.VolumeInUnits;
                double frac = filled / vol;
                if (frac < QMin)
                {   // partiel insuffisant : ce fill entre a l'unwind
                    opened.Add(pos);
                    LogLeg(t0, "leg_rej", e, "q<q_min", filled, pos.EntryPrice);
                    why = "q<q_min"; aborted = true; break;
                }
                opened.Add(pos);
                LogLeg(t0, "leg_fill", e, side.ToString(), filled, pos.EntryPrice);
                // qty avance au taux reel d'execution (pas la quote)
                qty = (d == 1) ? filled * pos.EntryPrice : filled;
                node = (node == a) ? b : a;
            }

            // liquidation residuel / unwind : cloture IOC des positions Og
            double pnl = 0.0; int unwound = 0; bool residOpen = false;
            foreach (var p in opened)
            {
                double np = p.NetProfit;   // marque flottante pre-cloture
                var r = CloseWithRetry(p, t0, np);
                if (r) { unwound++; pnl += np; }
                else residOpen = true;   // position restante : pnl non realise
            }
            _lastSeq = Now();
            if (aborted || residOpen) _consecFail++; else _consecFail = 0;
            LogSettle(t0, pi, aborted, residOpen ? "resid_open" : why,
                pnl, opened.Count, unwound);
            if (_consecFail >= MaxConsecFail)
            { LogSys("breaker: " + _consecFail + " echecs consecutifs"); Stop(); }
        }
        finally { _busy = false; }
    }

    double Up(int c)
    {
        if (c == 0) return 1.0;
        int sj = _upSym[c]; if (sj < 0) return double.NaN;
        double mid = 0.5 * (_bid[sj] + _ask[sj]);
        return _upDirect[c] ? mid : 1.0 / mid;
    }

    // Cloture IOC avec retry x3 (M6 debouclage) ; false si toujours ouverte.
    bool CloseWithRetry(Position p, long t0, double np)
    {
        string sn = p.SymbolName; double pv = p.VolumeInUnits; // capture avant cloture
        for (int k = 0; k < 3; k++)
        {
            var r = ClosePosition(p);
            if (r.IsSuccessful)
            {
                LogLeg(t0, "unwind_fill", -1, sn, pv, np);
                return true;
            }
            LogLeg(t0, "unwind_fail", -1, sn, pv, 0.0);
        }
        return false;
    }

    bool FlatCheck()
    {
        // compte plat exige sur nos 28 symboles (baseline Rust M5)
        foreach (var p in Positions)
            if (Array.IndexOf(FX, p.SymbolName) >= 0) return false;
        return true;
    }

    void FlattenOg()
    {
        // toute position sur nos 28 symboles est Og (FlatCheck exige
        // le compte plat avant chaque sequence) — rien d'autre a cloturer
        foreach (var p in Positions.ToArray())
            if (Array.IndexOf(FX, p.SymbolName) >= 0) { ClosePosition(p); }
    }

    // ------------------------------------------------- mediane fenetree
    void PushSpread(int e, double s)
    {
        var w = _sprWin[e];
        w.Add(s); if (w.Count > MED_WIN) w.RemoveAt(0);
        var v = w.OrderBy(x => x).ToArray();
        int m = v.Length / 2;
        _med[e] = (v.Length % 2 == 1) ? v[m] : 0.5 * (v[m - 1] + v[m]);
    }

    // ------------------------------------------------- session Zurich
    static readonly TimeZoneInfo ZRH =
        TimeZoneInfo.FindSystemTimeZoneById("W. Europe Standard Time");
    static bool InSession(long tMs)
    {
        // OTC FX : detection continue lun-ven. Ferme seulement sam/dim
        // (le flux de quotes s'arrete de toute facon au week-end).
        var z = TimeZoneInfo.ConvertTimeFromUtc(
            DateTimeOffset.FromUnixTimeMilliseconds(tMs).UtcDateTime, ZRH);
        var wd = z.DayOfWeek;
        return wd != DayOfWeek.Saturday && wd != DayOfWeek.Sunday;
    }
    static bool ShouldFlatten(long tMs)
    {
        // seule garde horaire : pas de nouvelle sequence vendredi >=21h
        // Zurich — si une jambe reste coincée a la cloture hebdo, elle
        // porterait le gap du week-end.
        var z = TimeZoneInfo.ConvertTimeFromUtc(
            DateTimeOffset.FromUnixTimeMilliseconds(tMs).UtcDateTime, ZRH);
        return z.DayOfWeek == DayOfWeek.Friday && z.Hour >= 21;
    }

    // ------------------------------------------------------- journal
    static long Now() { return DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); }
    void EnsureJ(long t)
    {
        string day = DateTimeOffset.FromUnixTimeMilliseconds(t).ToString("yyyy-MM-dd");
        if (day == _jDay && _j != null) return;
        _j?.Dispose();
        _jDay = day;
        _j = new StreamWriter(Path.Combine(JournalDir, day + ".jsonl"), append: true) { AutoFlush = true };
    }
    void LogTick(long t, int e, double bid, double ask)
    {
        EnsureJ(t);
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"tick\",\"e\":{0},\"bid\":{1:R},\"ask\":{2:R},\"ts\":{3}}}", e, bid, ask, t));
    }
    void LogOpp(long t, int pi, double net, double ddd)
    {
        EnsureJ(t);
        var legs = string.Join(",", _paths[pi].Select(e => "\"" + FX[e] + "\""));
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"opportunity\",\"t0\":{0},\"pi\":{1},\"net_usd\":{2:R},\"ddd_usd\":{3:R},\"start\":\"{4}\",\"legs\":{5},\"path\":[{6}],\"traded\":true}}",
            t, pi, net, ddd, CURR[_pS[pi]], _paths[pi].Length, legs));
    }
    void LogLeg(long t0, string ev, int e, string side, double vol, double px)
    {
        EnsureJ(Now());
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"{0}\",\"t0\":{1},\"e\":{2},\"side\":\"{3}\",\"vol\":{4:R},\"px\":{5:R},\"ts\":{6}}}",
            ev, t0, e, side, vol, px, Now()));
    }
    void LogSettle(long t0, int pi, bool aborted, string why, double pnl, int filled, int unwound)
    {
        EnsureJ(Now());
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"settle\",\"t0\":{0},\"pi\":{1},\"aborted\":{2},\"why\":\"{3}\",\"pnl_usd\":{4:R},\"filled\":{5},\"unwound\":{6},\"ts\":{7}}}",
            t0, pi, aborted ? "true" : "false", why, pnl, filled, unwound, Now()));
    }
    void LogSys(string msg)
    {
        EnsureJ(Now());
        _j.WriteLine("{\"ev\":\"system\",\"msg\":\"" + msg.Replace("\"", "'") + "\",\"ts\":" + Now() + "}");
    }

    // ------------------------------------------------- self-test parite
    bool SelfTest()
    {
        double[] upv = { 1.0, 1.10, 1.30, 1.0 / 150.0, 1.0 / 0.90, 1.0 / 1.35, 0.65, 0.60 };
        const double half = 1e-4;
        var savedBid = (double[])_bid.Clone(); var savedAsk = (double[])_ask.Clone();
        var savedChg = (long[])_lastChg.Clone(); var savedMed = (double[])_med.Clone();
        try
        {
            long t0 = 1_000;
            Action<double> set = m =>
            {
                for (int e = 0; e < NPAIR; e++)
                {
                    int a = _pairNodes[e, 0], b = _pairNodes[e, 1];
                    double mid = upv[a] / upv[b];
                    if (FX[e] == "CADJPY") mid *= m;
                    _bid[e] = mid * (1 - half); _ask[e] = mid * (1 + half);
                    _lastChg[e] = t0; _med[e] = _ask[e] - _bid[e];
                }
            };
            set(1.0);
            var r1 = EvalAt(1_000);
            if (r1 == null || r1.Value.Net >= 0) { Print("[selftest] livre coherent : net>=0 inattendu"); return false; }
            set(0.995);
            var r2 = EvalAt(1_000);
            if (r2 == null) { Print("[selftest] livre distordu : aucun chemin"); return false; }
            var legs = _paths[r2.Value.Pi];
            if (legs.Length != 1 || FX[legs[0]] != "CADJPY" || _pDir[r2.Value.Pi][0] != -1)
            { Print("[selftest] chemin gagnant inattendu"); return false; }
            if (r2.Value.Net <= 0) { Print("[selftest] net<=0 sur distorsion"); return false; }
            return true;
        }
        finally { _bid = savedBid; _ask = savedAsk; _lastChg = savedChg; _med = savedMed; }
    }

    protected override void OnStop()
    {
        FlattenOg();   // ne jamais laisser de position Og ouverte a l'arret
        LogSys("stop");
        _j?.Dispose();
    }
}
