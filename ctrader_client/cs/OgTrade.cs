// OgTrade.cs — cBot EXECUTION REELLE (demo NETTED uniquement).
// Port d'OgDetect + pipeline d'execution sequentielle IOC (exec.rs E1-E9).
//
// SECURITE : trades REELS sur le compte attache. Garde-fous :
//   - AllowLive=false -> refuse de tourner sur compte live (demo only)
//   - compte plat exige avant chaque sequence
//   - une seule sequence a la fois, breaker sur echecs consecutifs
//   - unwind immediat des jambes remplies sur abort
//   - detection continue OTC lun-ven ; pause ven. >=21h Zurich (gap week-end)
//
// Execution : ExecuteMarketOrder synchrone (IOC natif : fill immediat
//   top-of-book a 10K — MarketRangeOrder n'existe qu'en async dans
//   cette API). Detection pilotee par Symbol.Tick sur les 28 paires
//   (eval des qu'une quote bouge) — OnTick chart + Timer 250 ms en
//   fallback : pas de latence wire donnees, detection et envoi lies.
//
// SEMANTIQUE MARGE (correctif 11/11 — bug panier) : sur compte NETTED
//   chaque conversion est une POSITION. Un chemin s0->...->end se trade
//   comme un panier COMPLET [USD->s0] + jambes + [end->USD] : exposition
//   residuelle ~0. Sans les jambes entree/sortie, la structure executee
//   ne correspondait pas a la structure pricee (edge fantome, prouve
//   nuit 10-11/11 : 7 sequences legs=1, -13,63 $). Le panier est TENU
//   jusqu'a resorption de l'incoherence (target/stop/timeout) — c'est
//   la realignement qui cristallise l'edge, pas la cloture immediate.
//
// Journal : <JournalDir>/YYYY-MM-DD.jsonl — tick + opportunity +
//   leg_send/leg_fill/leg_rej/settle (source de verite reconciliation).
//
// Parametres : economie mesuree 10/11 — comm 4,00/100K (0,40/jambe a
//   10K), fresh 8000 (gaps inter-ticks mesures), ddd 0,27 (2,66@100K scale).

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
    // maintien du panier : cible = TargetFrac x net detecte ; stop =
    // -MaxLossUsd ; timeout = MaxHoldMs. M2M des positions via NetProfit.
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
    readonly Dictionary<string, int> _symIdx = new Dictionary<string, int>();
    long _evtTick = 0, _evtSymTick = 0, _evtTimer = 0, _lastEvtLog = 0;
    long _lastOppT = 0; double _lastOppNet = 0;

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
        // pilotage evenementiel : Symbol.Tick sur les 28 symboles (la quote
        // bouge -> eval immediate), OnTick du chart + Timer 250 ms en
        // fallback. Les callbacks d'un meme algo sont serialises par la
        // plateforme ; _busy protege RunSeq quand meme.
        for (int e = 0; e < NPAIR; e++)
        {
            _symIdx[FX[e]] = e;
            _sym[e].Tick += OnSymbolTick;
        }
        Timer.Start(TimeSpan.FromMilliseconds(250));
        LogSys(string.Format(CultureInfo.InvariantCulture,
            "boot TRADE paths=2072 ddd={0} range_bp={1} warm={2} live={3} basket=full hold={4}ms",
            DddMin, MaxRangeBp, WARMUP, Account.IsLive, MaxHoldMs));
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
    // une quote a change -> mise a jour carnet + warm-up ; retourne false si no-op
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
    {   // resync depuis les proprietes live de la plateforme
        for (int e = 0; e < NPAIR; e++) HandleQuote(e, t, _sym[e].Bid, _sym[e].Ask);
    }

    void TryDetect(long t)
    {
        if (!_warmLogged || !InSession(t)) return;
        if (ShouldFlatten(t)) { FlattenOg(); return; }
        if (_busy || t - _lastSeq < MinSeqGapMs) return;
        var r = EvalAt(t);
        if (r == null) return;
        if (r.Value.Net > 0 &&
            (r.Value.Net > _lastOppNet + 0.05 || t - _lastOppT > 5000))
        {   // journalise les fenetres positives (dedup : 1er +0 / nouveau
            // pic +0.05 / heartbeat 5 s) — les misses sous seuil servent
            // au recalibrage ddd (combien d'edge laisse-t-on)
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
    {   // declenche sur CHAQUE tick symbole — la dislocation n'attend pas le chart
        try
        {
            int e;
            if (!_symIdx.TryGetValue(a.SymbolName, out e)) return;
            _evtSymTick++;
            HandleQuote(e, Now(), a.Bid, a.Ask);
            TryDetect(Now());
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
        }
        catch (Exception ex) { LogSys("ex ontick: " + ex.GetType().Name); }
    }

    protected override void OnTimer()
    {   // heartbeat fallback 250 ms + stats d'evenements (preuve du canal actif)
        try
        {
            _evtTimer++;
            long t = Now();
            RefreshAll(t);
            TryDetect(t);
            _j?.Flush();   // buffer tick -> disque toutes les 250 ms
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
    // Prix le PANIER tel qu'il sera execute : [USD->s0] + jambes +
    // [end->USD] — chaque conversion est une position marge reelle.
    // Toutes les traversees aux prix executables (jamais de mid) et tous
    // les couts comptes (comm x2 + re-traversee spread par position).
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
            int se = (s0 != 0) ? _upSym[s0] : -1;     // paire entree USD->s0
            int xe = (end != 0) ? _upSym[end] : -1; // paire sortie end->USD
            if (se >= 0 && !fxOk[se]) continue;
            if (xe >= 0 && !fxOk[xe]) continue;

            double rate = 1.0, comm = 0.0, unwind = 0.0; bool valid = true;
            if (se >= 0)
            {   // entree USD->s0 : direct = achat cUSD a l'ask, inverse = vente USDc au bid
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
            {   // sortie end->USD : position reelle (direct = vente cUSD
                // au bid, inverse = achat USDc a l'ask) — pas un marquage
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

    // cout de re-traversee du spread a la cloture d'une position ~10K
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
    // Panier complet [USD->s0] + jambes + [end->USD] : exposition
    // residuelle ~0 (chaque conversion = une position marge). IOC
    // sequentiel, qty chainee aux fills reels, abort -> unwind.
    // Maintien jusqu'a resorption de l'incoherence (tp/sl/timeout).
    void RunSeq(int pi, long t0, double net0)
    {
        _busy = true;
        try
        {
            RunSeqCore(pi, t0, net0);
        }
        catch (Exception ex)
        {   // jamais de mort silencieuse a 3 h du matin : log + flatten
            try { LogSys("ex seq: " + ex.GetType().Name); FlattenOg(); }
            catch { }
        }
        finally { _busy = false; }
    }

    void RunSeqCore(int pi, long t0, double net0)
    {
        try
        {
            if (!FlatCheck()) { LogSys("seq skip: compte non plat"); return; }
            int s0 = _pS[pi], end = _pEnd[pi];
            // ordres du panier : {paire, noeud_depart} — entree, jambes, sortie
            var ord = new List<int[]>();
            if (s0 != 0 && _upSym[s0] >= 0) ord.Add(new[] { _upSym[s0], 0 });
            int node = s0;
            foreach (int e in _paths[pi]) { ord.Add(new[] { e, node }); node = Other(e, node); }
            if (end != 0 && _upSym[end] >= 0) ord.Add(new[] { _upSym[end], end });
            if (ord.Count == 0) return;

            var opened = new List<Position>();
            bool aborted = false; string why = "";
            double qty = NOTIONAL;                    // on demarre en USD

            for (int li = 0; li < ord.Count; li++)
            {
                int e = ord[li][0], from = ord[li][1];
                int a = _pairNodes[e, 0], b = _pairNodes[e, 1];
                int d = (from == a) ? 1 : -1;
                var sym = _sym[e];
                double px = (d == 1) ? _bid[e] : _ask[e];
                if (!(px > 0)) { why = "no_quote"; aborted = true; break; }
                // volume en unites de base de la paire
                double volBase = (d == 1) ? qty : qty / px;
                double vol = sym.NormalizeVolumeInUnits(volBase, RoundingMode.Down);
                if (vol < sym.VolumeInUnitsMin) { why = "vol<min"; aborted = true; break; }
                // tolerance IOC logicielle : _bid/_ask figes a t0 mais
                // sym.Bid/Ask sont live — ecart adverse > MaxRangeBp
                // => abort (meme semantique que tau_slip dans exec_seq).
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
                if (!res.IsSuccessful)
                {
                    LogLeg(t0, "leg_rej", e, side.ToString(), vol, px);
                    why = "rej"; aborted = true; break;
                }
                var pos = res.Position;
                if (pos == null)
                {   // ordre execute sans position resultante : cloture par
                    // netting (jambe inverse d'une position deja ouverte —
                    // panier degenere). Fill au px de la quote, qty approchee.
                    LogLeg(t0, "leg_fill", e, side.ToString(), vol, px);
                    qty = (d == 1) ? vol * px : vol;
                    continue;
                }
                bool merge = opened.Any(x => x.Id == pos.Id);
                double filled = merge ? vol : pos.VolumeInUnits;
                if (filled / vol < QMin)
                {   // partiel insuffisant : ce fill entre a l'unwind
                    if (!merge) opened.Add(pos);
                    LogLeg(t0, "leg_rej", e, "q<q_min", filled, pos.EntryPrice);
                    why = "q<q_min"; aborted = true; break;
                }
                if (!merge) opened.Add(pos);
                LogLeg(t0, "leg_fill", e, side.ToString(), filled, pos.EntryPrice);
                // qty avance au taux reel d'execution (pas la quote)
                qty = (d == 1) ? filled * pos.EntryPrice : filled;
            }

            // maintien : le panier est ~neutre — son M2M monte quand
            // l'incoherence se resorbe (la vraie capture de l'edge).
            long holdMs = 0; string exitHow = aborted ? "abort" : "timeout";
            if (!aborted && opened.Count > 0)
            {
                long tOpen = Now(), deadline = tOpen + MaxHoldMs;
                double target = TargetFrac * net0;
                while (Now() < deadline)
                {
                    double m2m = 0.0; int liveCnt = 0;
                    foreach (var p in opened)
                    {
                        try { if (Positions.Any(x => x.Id == p.Id)) { m2m += p.NetProfit; liveCnt++; } }
                        catch { }
                    }
                    if (liveCnt == 0) { exitHow = "netted"; break; }
                    if (m2m >= target) { exitHow = "tp"; break; }
                    if (m2m <= -MaxLossUsd) { exitHow = "sl"; break; }
                    System.Threading.Thread.Sleep(50);
                }
                holdMs = Now() - tOpen;
            }

            // unwind : cloture des positions encore ouvertes + PnL des
            // positions deja absorbees par le netting (dans History)
            double pnl = 0.0; int unwound = 0; bool residOpen = false;
            foreach (var p in opened)
            {
                var live = Positions.FirstOrDefault(x => x.Id == p.Id);
                if (live == null)
                {   // position absorbee par le netting : PnL deja realise
                    double rr = 0.0;
                    int lo = Math.Max(0, History.Count - 50);
                    for (int i = History.Count - 1; i >= lo; i--)
                        if (History[i].PositionId == p.Id) { rr = History[i].NetProfit; break; }
                    pnl += rr; unwound++;
                    continue;
                }
                var r = CloseWithRetry(live, t0, out double realized);
                if (r) { unwound++; pnl += realized; }
                else residOpen = true;
            }
            if (Positions.Any(x => Array.IndexOf(FX, x.SymbolName) >= 0))
                FlattenOg();   // filet de securite : rien ne doit rester
            _lastSeq = Now();
            if (aborted || residOpen) _consecFail++; else _consecFail = 0;
            LogSettle(t0, pi, aborted,
                residOpen ? "resid_open" : (aborted ? why : exitHow),
                pnl, opened.Count, unwound, holdMs);
            if (_consecFail >= MaxConsecFail)
            { LogSys("breaker: " + _consecFail + " echecs consecutifs"); Stop(); }
        }
        finally { _busy = false; }   // idempotent avec le wrapper RunSeq
    }

    // Cloture IOC avec retry x3 (M6 debouclage) ; false si toujours ouverte.
    // PnL REALISE depuis History (commissions incluses — norme audit) ;
    // fallback sur le NetProfit pre-cloture si l'entree n'est pas encore indexee.
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
                int lo = Math.Max(0, History.Count - 30);
                for (int i = History.Count - 1; i >= lo; i--)
                    if (History[i].PositionId == pid) { realized = History[i].NetProfit; break; }
                LogLeg(t0, "unwind_fill", -1, sn, pv, realized);
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
        _j = new StreamWriter(Path.Combine(JournalDir, day + ".jsonl"), append: true);
        // AutoFlush OFF : les ticks (corpus) restent en buffer — la latence
        // disque ne doit pas rentrer dans le chemin de detection. Les events
        // d'audit (opp/legs/settle/sys) flush explicitement ; OnTimer flush
        // le buffer tick toutes les 250 ms (perte max ~1 s de corpus au crash).
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
    void LogLeg(long t0, string ev, int e, string side, double vol, double px)
    {
        EnsureJ(Now());
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"{0}\",\"t0\":{1},\"e\":{2},\"side\":\"{3}\",\"vol\":{4:R},\"px\":{5:R},\"ts\":{6}}}",
            ev, t0, e, side, vol, px, Now()));
        _j.Flush();
    }
    void LogSettle(long t0, int pi, bool aborted, string why, double pnl, int filled, int unwound, long holdMs)
    {
        EnsureJ(Now());
        _j.WriteLine(string.Format(CultureInfo.InvariantCulture,
            "{{\"ev\":\"settle\",\"t0\":{0},\"pi\":{1},\"aborted\":{2},\"why\":\"{3}\",\"pnl_usd\":{4:R},\"filled\":{5},\"unwound\":{6},\"hold_ms\":{7},\"ts\":{8}}}",
            t0, pi, aborted ? "true" : "false", why, pnl, filled, unwound, holdMs, Now()));
        _j.Flush();
    }
    void LogSys(string msg)
    {
        EnsureJ(Now());
        _j.WriteLine("{\"ev\":\"system\",\"msg\":\"" + msg.Replace("\"", "'") + "\",\"ts\":" + Now() + "}");
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
            // meme panier 3 positions (USDJPY+CADJPY+USDCAD) : le DFS s0=USD
            // enregistre [USDJPY,CADJPY] avant [CADJPY]@JPY — parite eval.rs.
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
        FlattenOg();   // ne jamais laisser de position Og ouverte a l'arret
        LogSys("stop");
        _j?.Dispose();
    }
}
