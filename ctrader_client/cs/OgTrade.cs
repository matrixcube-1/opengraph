// OgTrade.cs — cBot EXECUTION REELLE (demo NETTED uniquement).
// VERSION 2.0 — patch post-MC (12/11/2025).
//
// Correctifs vs v1 (valides par MC paired, 5 scenarios × 0.5h × 5 configs) :
//
//   [PATCH A — CRITIQUE] Hold phase non bloquante : RunSeqCore n'execute
//   que la phase d'ouverture (synchronous, ~50 ms par jambe). La phase de
//   maintien est deleguee a une machine a etats pilotee par OnTimer —
//   les callbacks Symbol.Tick/OnTick/OnTimer ne sont plus bloques 5 s
//   par sequence. MC: +6,4 k$/scenario (paired), 0% breaker vs 100%.
//
//   [PATCH B — DEFENSIF] Stale check sur jambe 0 : la premiere jambe
//   etait envoyee a l'aveugle (li > 0). Desormais _bid/_ask figures a
//   t0 sont compares a sym.Bid/Ask live avant chaque jambe, jambe 0
//   incluse. Le MC ne declenche pas ce check en regime simule (slippage
//   < MaxRangeBp) mais le cout marginal est nul et la couverture est
//   reelle en cas de pic de volatilite entre EvalAt et ExecuteMarketOrder.
//
//   [PATCH D — CORRECTNESS] PnL History : le break apres la premiere
//   entree trouvee etait faux sur compte NETTED avec clotures
//   partielles successives (une PositionId peut generer N HistoryItem).
//   Desormais on somme tous les HistoryItem de meme PositionId.
//
//   [PATCH E1] _busy volatile : visibilite inter-threads garantie meme
//   si cTrader serialise en pratique.
//   [PATCH E2] Double finally supprime dans RunSeqCore.
//   [PATCH E3] Now() cached dans LogLeg (atomique par event).
//   [PATCH E4] _j null after Dispose (idempotent OnStop).
//   [PATCH E5] FlattenOg utilise CloseWithRetry (x3) au lieu de
//   ClosePosition one-shot.
//   [PATCH E6] ExecuteMarketOrder avec slippage explicite en pips
//   (MaxRangeBp converti par symbole : bp*px*1e-4/PipSize — la formule
//   bp/10 du patch source etait ~10x trop serree) au lieu du defaut broker.
//
// NON APPLIQUE (MC infirme) :
//   [PATCH C] Breaker market-only — MC montre que ne pas compter
//   stale/rej dans consecFail laisse passer plus de bad trades et
//   reduit le PnL. Le breaker agressif est en fait PROTECTEUR en
//   regime volatil. Conserve tel quel.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;
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
    [Parameter("MaxHoldMs", DefaultValue = 5000)]
    public int MaxHoldMs { get; set; }
    [Parameter("TargetFrac", DefaultValue = 0.5)]
    public double TargetFrac { get; set; }
    [Parameter("MaxLossUsd", DefaultValue = 3.0)]
    public double MaxLossUsd { get; set; }

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
    const double NOTIONAL = 10_000.0;
    const double COMM_100K = 4.00;
    static readonly double COMM =
        COMM_100K * NOTIONAL / 100_000.0;
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

    // tables de chemins
    readonly List<int[]> _paths = new();
    readonly List<int> _pS = new();
    readonly List<int> _pEnd = new();
    readonly List<int[]> _pDir = new();
    readonly List<int[]> _pBase = new();
    readonly int[,] _pairNodes = new int[NPAIR, 2];
    readonly List<int>[] _adj = new List<int>[NCURR];
    readonly int[] _upSym = new int[NCURR];
    readonly bool[] _upDirect = new bool[NCURR];

    // execution — volatile pour visibilite inter-threads (PATCH E1)
    volatile bool _busy = false;
    long _lastSeq = 0;
    int _consecFail = 0;
    readonly Dictionary<string, int> _symIdx = new Dictionary<string, int>();
    long _evtTick = 0, _evtSymTick = 0, _evtTimer = 0, _lastEvtLog = 0;
    long _lastOppT = 0; double _lastOppNet = 0;

    // PATCH A — panier actif pour la machine a etats (hold non bloquant)
    class ActiveBasket
    {
        public long T0;                 // ms, sequence start
        public int Pi;
        public double Net0;
        public long TOpen;              // ms, post-open
        public long Deadline;          // TOpen + MaxHoldMs
        public double Target;          // TargetFrac * Net0
        public List<BasketLeg> Legs = new();
        public bool Aborted;
        public bool ResidOpen;          // une jambe n'a pas pu etre cloturee
        public string Why = "";
        public string ExitHow = "timeout";  // tp/sl/timeout/netted
    }
    class BasketLeg
    {
        public int E;                   // paire index
        public long PositionId;
        public double Vol;
        public double FillPx;
        public int D;                   // 1=Sell, -1=Buy
        public bool NettedDuringHold;   // absorbee par netting pendant le hold
    }
    ActiveBasket _active = null;        // non-null pendant la phase de maintien

    StreamWriter _j = null; string _jDay = "";

    // =========================================================== boot
    protected override void OnStart()
    {
        Directory.CreateDirectory(JournalDir);
        if (Account.IsLive && !AllowLive)
        {
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
        for (int e = 0; e < NPAIR; e++)
        {
            _symIdx[FX[e]] = e;
            _sym[e].Tick += OnSymbolTick;
        }
        Timer.Start(TimeSpan.FromMilliseconds(250));
        LogSys(string.Format(CultureInfo.InvariantCulture,
            "boot TRADE v2 paths=2072 ddd={0} range_bp={1} warm={2} live={3} hold_sm={4}ms [PATCH A-F]",
            DddMin, MaxRangeBp, WARMUP, Account.IsLive, MaxHoldMs));
        Print("[OgTrade v2] pret — EXECUTION REELLE, notionnel {0} USD", NOTIONAL);
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
    bool HandleQuote(int e, long t, double b, double a)
    {
        if (!(a > 0 && b > 0) || a < b) return false;
        if (b == _bid[e] && a == _ask[e]) return false;
        _bid[e] = b; _ask[e] = a; _lastChg[e] = t;
        PushSpread(e, a - b);
        LogTick(t, e, b, a);
        if (_nTicks[e] < WARMUP)
        {
            _nTicks[e]++;
            if (!_warmLogged && _nTicks.All(n => n >= WARMUP))
            { _warmLogged = true; LogSys("warm-up complet (" + WARMUP + " ticks/symbole)"); }
        }
        return true;
    }

    void RefreshAll(long t)
    {
        for (int e = 0; e < NPAIR; e++) HandleQuote(e, t, _sym[e].Bid, _sym[e].Ask);
    }

    void TryDetect(long t)
    {
        if (!_warmLogged || !InSession(t)) return;
        if (ShouldFlatten(t)) { FlattenOg(); return; }
        // PATCH A — _busy ou _active bloquent les nouvelles sequences
        if (_busy || _active != null) return;
        if (t - _lastSeq < MinSeqGapMs) return;
        var r = EvalAt(t);
        if (r == null) return;
        if (r.Value.Net > 0 &&
            (r.Value.Net > _lastOppNet + 0.05 || t - _lastOppT > 5000))
        {
            _lastOppNet = r.Value.Net; _lastOppT = t;
            LogOpp(t, r.Value.Pi, r.Value.Net, r.Value.Ddd,
                   r.Value.Net >= DddMin);
        }
        if (r.Value.Net >= DddMin)
        {
            RunSeq(r.Value.Pi, t, r.Value.Net);
        }
    }

    void OnSymbolTick(SymbolTickEventArgs a)
    {
        try
        {
            int e;
            if (!_symIdx.TryGetValue(a.SymbolName, out e)) return;
            _evtSymTick++;
            long t = Now();
            HandleQuote(e, t, a.Bid, a.Ask);
            TryDetect(t);
            // PATCH A — manage active basket on each tick too
            ManageActiveBasket(t);
        }
        catch (Exception ex) { LogSys("ex symtick: " + ex.GetType().Name); }
    }

    protected override void OnTick()
    {
        try
        {
            _evtTick++;
            long t = Now();
            RefreshAll(t);
            TryDetect(t);
            ManageActiveBasket(t);
        }
        catch (Exception ex) { LogSys("ex ontick: " + ex.GetType().Name); }
    }

    protected override void OnTimer()
    {
        try
        {
            _evtTimer++;
            long t = Now();
            RefreshAll(t);
            TryDetect(t);
            ManageActiveBasket(t);          // PATCH A — poll M2M
            _j?.Flush();
            if (t - _lastEvtLog >= 300_000)
            {
                _lastEvtLog = t;
                LogSys(string.Format(CultureInfo.InvariantCulture,
                    "evt onTick={0} symTick={1} timer={2}", _evtTick, _evtSymTick, _evtTimer));
            }
        }
        catch (Exception ex) { try { LogSys("ex timer: " + ex.GetType().Name); } catch { } }
    }

    // ====================================================== eval_at
    (int Pi, double Net, double Ddd)? EvalAt(long t)
    {
        var ok = new bool[NPAIR];
        for (int i = 0; i < NPAIR; i++)
            ok[i] = _lastChg[i] != long.MinValue && t - _lastChg[i] <= FreshMs;
        var fxOk = new bool[NPAIR];
        for (int i = 0; i < NPAIR; i++)
            fxOk[i] = ok[i] && (_ask[i] - _bid[i]) <= SpreadCap * _med[i];

        double bestNet = double.NegativeInfinity; int bestPi = 0; double bestDdd = 0;
        for (int pi = 0; pi < _paths.Count; pi++)
        {
            int[] legs = _paths[pi], dir = _pDir[pi];
            int s0 = _pS[pi], end = _pEnd[pi];
            int se = (s0 != 0) ? _upSym[s0] : -1;
            int xe = (end != 0) ? _upSym[end] : -1;
            if (se >= 0 && !fxOk[se]) continue;
            if (xe >= 0 && !fxOk[xe]) continue;

            double rate = 1.0, comm = 0.0, unwind = 0.0; bool valid = true;
            if (se >= 0)
            {
                rate *= _upDirect[s0] ? 1.0 / _ask[se] : _bid[se];
                unwind += SpreadCost(se); comm += COMM;
            }
            for (int li = 0; li < legs.Length; li++)
            {
                int e = legs[li];
                if (!fxOk[e]) { valid = false; break; }
                rate *= (dir[li] == 1) ? _bid[e] : 1.0 / _ask[e];
                unwind += SpreadCost(e); comm += COMM;
            }
            if (!valid) continue;
            if (xe >= 0)
            {
                rate *= _upDirect[end] ? _bid[xe] : 1.0 / _ask[xe];
                unwind += SpreadCost(xe); comm += COMM;
            }
            double usdOut = NOTIONAL * rate;
            double commFx = 2.0 * comm * CommScale;
            if (double.IsNaN(usdOut) || double.IsInfinity(usdOut) || double.IsNaN(commFx) || double.IsInfinity(commFx) || double.IsNaN(unwind))
                continue;
            double net = usdOut - NOTIONAL - commFx - unwind;
            if (net > bestNet) { bestNet = net; bestPi = pi; bestDdd = usdOut - NOTIONAL; }
        }
        if (double.IsNegativeInfinity(bestNet)) return null;
        return (bestPi, bestNet, bestDdd);
    }

    double SpreadCost(int e)
    {
        double mid = 0.5 * (_bid[e] + _ask[e]);
        return (mid > 0) ? (_ask[e] - _bid[e]) / mid * NOTIONAL : 0.0;
    }

    int Other(int e, int node)
    {
        return node == _pairNodes[e, 0] ? _pairNodes[e, 1] : _pairNodes[e, 0];
    }

    // ================================================== execution
    // PATCH A : RunSeqCore n'execute QUE la phase d'ouverture. La phase
    // de maintien est deleguee a ManageActiveBasket (appelee par OnTimer
    // et OnSymbolTick). Les callbacks ne sont plus bloques 5 s.
    void RunSeq(int pi, long t0, double net0)
    {
        _busy = true;
        try
        {
            RunSeqOpen(pi, t0, net0);
        }
        catch (Exception ex)
        {
            try { LogSys("ex seq: " + ex.GetType().Name); FlattenOg(); }
            catch { }
        }
        finally { _busy = false; }
    }

    void RunSeqOpen(int pi, long t0, double net0)
    {
        if (!FlatCheck()) { LogSys("seq skip: compte non plat"); return; }
        int s0 = _pS[pi], end = _pEnd[pi];
        var ord = new List<int[]>();
        if (s0 != 0 && _upSym[s0] >= 0) ord.Add(new[] { _upSym[s0], 0 });
        int node = s0;
        foreach (int e in _paths[pi]) { ord.Add(new[] { e, node }); node = Other(e, node); }
        if (end != 0 && _upSym[end] >= 0) ord.Add(new[] { _upSym[end], end });
        if (ord.Count == 0) return;

        var basket = new ActiveBasket { T0 = t0, Pi = pi, Net0 = net0 };
        bool aborted = false; string why = "";
        double qty = NOTIONAL;

        for (int li = 0; li < ord.Count; li++)
        {
            int e = ord[li][0], from = ord[li][1];
            int a = _pairNodes[e, 0], b = _pairNodes[e, 1];
            int d = (from == a) ? 1 : -1;
            var sym = _sym[e];
            double px = (d == 1) ? _bid[e] : _ask[e];
            if (!(px > 0)) { why = "no_quote"; aborted = true; break; }
            double volBase = (d == 1) ? qty : qty / px;
            double vol = sym.NormalizeVolumeInUnits(volBase, RoundingMode.Down);
            if (vol < sym.VolumeInUnitsMin) { why = "vol<min"; aborted = true; break; }

            // PATCH B — stale check sur TOUTES les jambes (jambe 0 incluse)
            double live = (d == 1) ? sym.Bid : sym.Ask;
            double devBp = (d == 1) ? (px - live) / px * 1e4
                                    : (live - px) / px * 1e4;
            if (live > 0 && devBp > MaxRangeBp)
            {
                LogLeg(t0, "leg_rej", e, "stale:" + devBp.ToString("F2"), vol, live);
                why = (li == 0) ? "stale0" : "stale"; aborted = true; break;
            }

            var side = (d == 1) ? TradeType.Sell : TradeType.Buy;
            LogLeg(t0, "leg_send", e, side.ToString(), vol, px);
            // PATCH E6 — IOC natif : ExecuteMarketRangeOrder (fill dans la
            // fourchette autour de basePrice=px, reliquat tue). MaxRangeBp
            // (bp relatifs) -> pips PAR SYMBOLE : tol_abs = px*bp*1e-4 ;
            // /PipSize. (le patch source faisait bp/10 -> 0.208 pips =
            // ~10x trop serre -> rejets en masse ; ex: EURUSD ~2.3,
            // USDJPY ~3.1 pips. L'API 5.10 n'a pas de slippage sur
            // ExecuteMarketOrder : la tolerance va sur basePrice.)
            double slipPips = MaxRangeBp * px * 1e-4 / sym.PipSize;
            var res = ExecuteMarketRangeOrder(side, sym.Name, vol, slipPips, px, LABEL);
            if (!res.IsSuccessful)
            {
                LogLeg(t0, "leg_rej", e, side.ToString(), vol, px);
                why = "rej"; aborted = true; break;
            }
            var pos = res.Position;
            if (pos == null)
            {
                LogLeg(t0, "leg_fill", e, side.ToString(), vol, px);
                qty = (d == 1) ? vol * px : vol;
                continue;
            }
            bool merge = basket.Legs.Any(x => x.PositionId == pos.Id);
            double filled = merge ? vol : pos.VolumeInUnits;
            if (filled / vol < QMin)
            {
                if (!merge) basket.Legs.Add(new BasketLeg { E = e, PositionId = pos.Id, Vol = pos.VolumeInUnits, FillPx = pos.EntryPrice, D = d });
                LogLeg(t0, "leg_rej", e, "q<q_min", filled, pos.EntryPrice);
                why = "q<q_min"; aborted = true; break;
            }
            if (!merge) basket.Legs.Add(new BasketLeg { E = e, PositionId = pos.Id, Vol = pos.VolumeInUnits, FillPx = pos.EntryPrice, D = d });
            LogLeg(t0, "leg_fill", e, side.ToString(), filled, pos.EntryPrice);
            qty = (d == 1) ? filled * pos.EntryPrice : filled;
        }

        basket.Aborted = aborted;
        basket.Why = why;
        basket.TOpen = Now();
        basket.Deadline = basket.TOpen + MaxHoldMs;
        basket.Target = TargetFrac * net0;

        if (aborted)
        {
            // unwind immediat + settle journalise (le PnL d'abort est un
            // cout reel — le patch source perdait cette ligne d'audit)
            FinalizeBasket(basket, why);
            return;
        }
        // PATCH A — deleguer le hold a ManageActiveBasket
        _active = basket;
        LogSys(string.Format(CultureInfo.InvariantCulture,
            "seq_open pi={0} legs={1} net0={2:F2} target={3:F2} deadline_in={4}ms",
            pi, basket.Legs.Count, net0, basket.Target, MaxHoldMs));
    }

    // PATCH A — machine a etats du hold, non bloquante. Appelee par
    // OnTimer (250 ms) et OnSymbolTick (chaque tick). Sort des qu'une
    // condition de sortie est atteinte (tp/sl/timeout/netted).
    void ManageActiveBasket(long t)
    {
        if (_active == null) return;
        var b = _active;
        try
        {
            double m2m = 0.0; int liveCnt = 0;
            foreach (var leg in b.Legs)
            {
                try
                {
                    var live = Positions.FirstOrDefault(x => x.Id == leg.PositionId);
                    if (live == null) { leg.NettedDuringHold = true; continue; }
                    m2m += live.NetProfit; liveCnt++;
                }
                catch { /* position disposee */ }
            }
            if (liveCnt == 0) { FinalizeBasket(b, "netted"); return; }
            if (m2m >= b.Target) { FinalizeBasket(b, "tp"); return; }
            if (m2m <= -MaxLossUsd) { FinalizeBasket(b, "sl"); return; }
            if (t >= b.Deadline) { FinalizeBasket(b, "timeout"); return; }
        }
        catch (Exception ex)
        {
            LogSys("ex manage: " + ex.GetType().Name);
            FinalizeBasket(b, "ex:" + ex.GetType().Name);
        }
    }

    void FinalizeBasket(ActiveBasket b, string exitHow)
    {
        UnwindBasket(b, exitHow);
        long holdMs = Now() - b.TOpen;
        _lastSeq = Now();
        // breaker : aborts + resid_open + sl comptent (perte reel/echec).
        // PATCH C (NON APPLIQUE) — le MC a montre que ne pas compter les
        // market fails est nuisible (laisse passer plus de bad trades)
        if (b.Aborted || b.ResidOpen || exitHow == "sl") _consecFail++; else _consecFail = 0;
        LogSettle(b.T0, b.Pi, b.Aborted,
            b.ResidOpen ? "resid_open" : exitHow,
            _lastSettlePnl, b.Legs.Count, _lastSettleUnwound, holdMs);
        _active = null;
        if (_consecFail >= MaxConsecFail)
        { LogSys("breaker: " + _consecFail + " echecs consecutifs"); Stop(); }
    }

    double _lastSettlePnl = 0; int _lastSettleUnwound = 0;

    // Unwind : cloture avec retry x3 (PATCH E5 pour FlattenOg) + PnL History
    // sommation sur PositionId (PATCH D — corrige le bug du break).
    void UnwindBasket(ActiveBasket b, string exitHow)
    {
        double pnl = 0.0; int unwound = 0; bool residOpen = false;
        foreach (var leg in b.Legs)
        {
            var live = Positions.FirstOrDefault(x => x.Id == leg.PositionId);
            if (live == null)
            {
                // PATCH D — sommer TOUS les HistoryItem de meme PositionId
                // (le break a la premiere entree etait faux sur NETTED avec
                // clotures partielles successives)
                double rr = 0.0;
                int lo = Math.Max(0, History.Count - 50);
                for (int i = lo; i < History.Count; i++)
                    if (History[i].PositionId == leg.PositionId)
                        rr += History[i].NetProfit;
                pnl += rr; unwound++;
                continue;
            }
            var r = CloseWithRetry(live, b.T0, out double realized);
            if (r) { unwound++; pnl += realized; }
            else residOpen = true;
        }
        if (Positions.Any(x => Array.IndexOf(FX, x.SymbolName) >= 0))
            FlattenOg();
        _lastSettlePnl = pnl; _lastSettleUnwound = unwound;
        b.ResidOpen = residOpen;
        if (residOpen) LogSys("warn: resid_open apres unwind");
    }

    bool CloseWithRetry(Position p, long t0, out double realized)
    {
        string sn = p.SymbolName; double pv = p.VolumeInUnits; long pid = p.Id;
        realized = 0.0;
        for (int k = 0; k < 3; k++)
        {
            var r = ClosePosition(p);
            if (r.IsSuccessful)
            {
                try { realized = p.NetProfit; } catch { realized = 0.0; }
                // PATCH D — sommer tous les HistoryItem de cette position
                int lo = Math.Max(0, History.Count - 30);
                double sum = 0.0; bool found = false;
                for (int i = lo; i < History.Count; i++)
                    if (History[i].PositionId == pid) { sum += History[i].NetProfit; found = true; }
                if (found) realized = sum;
                LogLeg(t0, "unwind_fill", -1, sn, pv, realized);
                return true;
            }
            LogLeg(t0, "unwind_fail", -1, sn, pv, 0.0);
        }
        return false;
    }

    bool FlatCheck()
    {
        foreach (var p in Positions)
            if (Array.IndexOf(FX, p.SymbolName) >= 0) return false;
        return true;
    }

    // PATCH E5 — utilise CloseWithRetry (x3) pour OnStop/Flatten
    void FlattenOg()
    {
        foreach (var p in Positions.ToArray())
            if (Array.IndexOf(FX, p.SymbolName) >= 0)
                CloseWithRetry(p, Now(), out _);
    }

    void PushSpread(int e, double s)
    {
        var w = _sprWin[e];
        w.Add(s); if (w.Count > MED_WIN) w.RemoveAt(0);
        var v = w.OrderBy(x => x).ToArray();
        int m = v.Length / 2;
        _med[e] = (v.Length % 2 == 1) ? v[m] : 0.5 * (v[m - 1] + v[m]);
    }

    static readonly TimeZoneInfo ZRH =
        TimeZoneInfo.FindSystemTimeZoneById("W. Europe Standard Time");
    static bool InSession(long tMs)
    {
        var z = TimeZoneInfo.ConvertTimeFromUtc(
            DateTimeOffset.FromUnixTimeMilliseconds(tMs).UtcDateTime, ZRH);
        var wd = z.DayOfWeek;
        return wd != DayOfWeek.Saturday && wd != DayOfWeek.Sunday;
    }
    static bool ShouldFlatten(long tMs)
    {
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
        _j = new StreamWriter(Path.Combine(JournalDir, day + ".jsonl"), append: true);
    }
    void LogTick(long t, int e, double bid, double ask)
    {
        EnsureJ(t);
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"tick\",\"e\":{0},\"bid\":{1:R},\"ask\":{2:R},\"ts\":{3}}}", e, bid, ask, t));
    }
    void LogOpp(long t, int pi, double net, double ddd, bool traded)
    {
        EnsureJ(t);
        var legs = string.Join(",", _paths[pi].Select(e => "\"" + FX[e] + "\""));
        int npos = _paths[pi].Length + (_pS[pi] != 0 ? 1 : 0) + (_pEnd[pi] != 0 ? 1 : 0);
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"opportunity\",\"t0\":{0},\"pi\":{1},\"net_usd\":{2:R},\"ddd_usd\":{3:R},\"start\":\"{4}\",\"end\":\"{5}\",\"legs\":{6},\"npos\":{7},\"path\":[{8}],\"traded\":{9}}}",
            t, pi, net, ddd, CURR[_pS[pi]], CURR[_pEnd[pi]], _paths[pi].Length,
            npos, legs, traded ? "true" : "false"));
        _j.Flush();
    }
    // PATCH E3 — Now() cached (atomicite par event)
    void LogLeg(long t0, string ev, int e, string side, double vol, double px)
    {
        long ts = Now();
        EnsureJ(ts);
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"{0}\",\"t0\":{1},\"e\":{2},\"side\":\"{3}\",\"vol\":{4:R},\"px\":{5:R},\"ts\":{6}}}",
            ev, t0, e, side, vol, px, ts));
        _j.Flush();
    }
    void LogSettle(long t0, int pi, bool aborted, string why, double pnl, int filled, int unwound, long holdMs)
    {
        long ts = Now();
        EnsureJ(ts);
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"settle\",\"t0\":{0},\"pi\":{1},\"aborted\":{2},\"why\":\"{3}\",\"pnl_usd\":{4:R},\"filled\":{5},\"unwound\":{6},\"hold_ms\":{7},\"ts\":{8}}}",
            t0, pi, aborted ? "true" : "false", why, pnl, filled, unwound, holdMs, ts));
        _j.Flush();
    }
    void LogSys(string msg)
    {
        long ts = Now();
        EnsureJ(ts);
        _j.WriteLine("{\"ev\":\"system\",\"msg\":\"" + msg.Replace("\"", "'") + "\",\"ts\":" + ts + "}");
        _j.Flush();
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
            if (legs.Length != 2 || FX[legs[0]] != "USDJPY" || FX[legs[1]] != "CADJPY"
                || _pDir[r2.Value.Pi][0] != 1 || _pDir[r2.Value.Pi][1] != -1
                || _pS[r2.Value.Pi] != 0 || _pEnd[r2.Value.Pi] != 5)
            { Print("[selftest] chemin gagnant inattendu"); return false; }
            if (r2.Value.Net <= 0) { Print("[selftest] net<=0 sur distorsion"); return false; }
            return true;
        }
        finally { _bid = savedBid; _ask = savedAsk; _lastChg = savedChg; _med = savedMed; }
    }

    protected override void OnStop()
    {
        Timer.Stop();
        for (int e = 0; e < NPAIR; e++)
            if (_sym[e] != null) _sym[e].Tick -= OnSymbolTick;
        // attendre que le panier actif se resorbe ou le forcer
        if (_active != null) FinalizeBasket(_active, "stop");
        FlattenOg();
        // attendre brievement la resorption effective (PATCH E5)
        for (int i = 0; i < 20 && Positions.Any(x => Array.IndexOf(FX, x.SymbolName) >= 0); i++)
            Thread.Sleep(50);
        LogSys("stop");
        _j?.Dispose();
        _j = null;       // PATCH E4 — null after Dispose (idempotent OnStop)
    }
}
