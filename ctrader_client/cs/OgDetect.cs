// OgDetect.cs — cBot detect-only : port fidele d'opengraph eval_at
// Convention A, chemins ouverts FX <= 3 jambes, univers Annexe J.
//
// SECURITE : ce fichier ne contient AUCUN appel d'ordre — detection et
// journalisation uniquement (structurellement incapable de trader).
//
// Journal : <JournalDir>/YYYY-MM-DD.jsonl — memes lignes que le client
//   Rust : {"ev":"tick","e":N,"bid":..,"ask":..,"ts":..}  (lu tel quel
//   par audit/ticks_to_poc.py) + {"ev":"opportunity",...} + {"ev":"system",...}
//
// Divergences declarees vs moteur Rust (telemetrie de detection, pas de
// trading) :
//   - fraicheur : ts de dernier CHANGEMENT bid/ask (Automate ne fournit
//     pas le timestamp venue par symbole) ;
//   - mediane de spread : fenetre glissante de 512 ticks (vs historique
//     complet) — converge apres warm-up, s'adapte au regime ;
//   - eval a chaque tick du symbole hote (~10-15/s) — poll des 28.
//   - aucune execution : les opportunites ne sont que mesurees.
//
// Parametres declares dans config.json (source-generes : JournalDir,
// FreshMs, SpreadCap, CommScale, DddMin) — trial #87 du calibrage.

using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;

namespace cAlgo.Robots;

public partial class OgDetect : Robot
{
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
    const double NOTIONAL = 100_000.0, COMM = 3.00;
    const int WARMUP = 100, MED_WIN = 512;

    // etat par paire : quote, dernier changement, spreads (mediane fenetree)
    readonly Symbol[] _sym = new Symbol[NPAIR];
    double[] _bid = new double[NPAIR], _ask = new double[NPAIR];
    long[] _lastChg = new long[NPAIR];
    readonly int[] _nTicks = new int[NPAIR];
    readonly List<double>[] _sprWin = new List<double>[NPAIR];
    double[] _med = new double[NPAIR];
    bool _warmLogged = false;

    // tables de chemins (port de paths.rs)
    readonly List<int[]> _paths = new();      // aretes par chemin
    readonly List<int> _pS = new();           // noeud de depart
    readonly List<int> _pEnd = new();         // noeud terminal
    readonly List<int[]> _pDir = new();       // +1 vente base / -1 achat
    readonly List<int[]> _pBase = new();      // devise de base (comm)
    readonly int[,] _pairNodes = new int[NPAIR, 2];
    readonly List<int>[] _adj = new List<int>[NCURR];
    readonly int[] _upSym = new int[NCURR];   // paire USD -> slot, -1
    readonly bool[] _upDirect = new bool[NCURR]; // cUSD ? USDc ?

    StreamWriter _j = null; string _jDay = "";

    // =========================================================== boot
    protected override void OnStart()
    {
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
            {   // symbole absent du compte -> echec explicite, jamais silencieux
                Print("[OgDetect] FATAL: symbole absent {0}", FX[e]);
                Stop();
                return;
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
        {   // verrou du document : 2 072 chemins FX purs
            Print("[OgDetect] FATAL: {0} chemins (attendu 2072)", _paths.Count);
            Stop(); return;
        }
        if (!SelfTest())
        { Print("[OgDetect] FATAL: self-test divergent"); Stop(); return; }
        Directory.CreateDirectory(JournalDir);
        LogSys(string.Format(CultureInfo.InvariantCulture,
            "boot detect-only paths=2072 ddd={0} warm={1}", DddMin, WARMUP));
        Print("[OgDetect] pret — detection seule, aucun ordre possible");
    }

    static int Ci(string c) { return Array.IndexOf(CURR, c); }

    // DFS chemins ouverts 1-3 jambes (paths.rs : prefixe enregistre a
    // chaque profondeur, pas de revisite de noeud, adjacence ordre FX)
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
        // poll des 28 : journalise les changements comme ticks reels
        for (int e = 0; e < NPAIR; e++)
        {
            double b = _sym[e].Bid, a = _sym[e].Ask;
            if (!(a > 0 && b > 0) || a < b) continue;     // quote absente/croisee : ignoree
            if (b == _bid[e] && a == _ask[e]) continue;   // inchangee : pas un tick
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
        if (!_warmLogged || !InSession(t) || ShouldFlatten(t)) return;
        var r = EvalAt(t);
        if (r == null) return;
        if (r.Value.Net >= DddMin)
            LogOpp(t, r.Value.Pi, r.Value.Net, r.Value.Ddd);
    }

    // ====================================================== eval_at
    // Port exact de eval.rs / poc_engine.eval_at (Convention A, FX pur).
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
            double rate = 1.0, comm = 0.0; bool valid = true;
            int[] legs = _paths[pi], dir = _pDir[pi], bas = _pBase[pi];
            for (int li = 0; li < legs.Length; li++)
            {
                int e = legs[li];
                if (!fxOk[e]) { valid = false; break; }
                double ub = up[bas[li]];
                comm += (ub > 0 && !double.IsNaN(ub)) ? COMM / ub : double.NaN;
                rate *= (dir[li] == 1) ? _bid[e] : 1.0 / _ask[e];
            }
            if (!valid) continue;
            double upS = up[_pS[pi]];
            double qty0 = (upS > 0 && !double.IsNaN(upS)) ? NOTIONAL / upS : double.NaN;
            double usdOut = qty0 * rate * upLiq[_pEnd[pi]];
            double commFx = (comm + commLiq[_pEnd[pi]]) * CommScale;
            if (double.IsNaN(usdOut) || double.IsInfinity(usdOut) || double.IsNaN(commFx) || double.IsInfinity(commFx))
                continue;
            double net = usdOut - NOTIONAL - commFx;
            if (net > bestNet) { bestNet = net; bestPi = pi; bestDdd = usdOut - NOTIONAL; }
        }
        if (double.IsNegativeInfinity(bestNet)) return null;
        return (bestPi, bestNet, bestDdd);
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
        var z = TimeZoneInfo.ConvertTimeFromUtc(
            DateTimeOffset.FromUnixTimeMilliseconds(tMs).UtcDateTime, ZRH);
        int wd = (int)z.DayOfWeek;                 // 0=dim
        if (wd == 0 || wd == 6) return false;
        return z.Hour >= 7 && z.Hour < 21;
    }
    static bool ShouldFlatten(long tMs)
    {
        if (!InSession(tMs)) return false;
        var z = TimeZoneInfo.ConvertTimeFromUtc(
            DateTimeOffset.FromUnixTimeMilliseconds(tMs).UtcDateTime, ZRH);
        var close = z.Date.AddHours(21);
        return (close - z).TotalMilliseconds <= 15 * 60 * 1000;
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
            "{{\"ev\":\"opportunity\",\"t0\":{0},\"pi\":{1},\"net_usd\":{2:R},\"ddd_usd\":{3:R},\"start\":\"{4}\",\"legs\":{5},\"path\":[{6}],\"traded\":false}}",
            t, pi, net, ddd, CURR[_pS[pi]], _paths[pi].Length, legs));
    }
    void LogSys(string msg)
    {
        EnsureJ(Now());
        _j.WriteLine("{\"ev\":\"system\",\"msg\":\"" + msg.Replace("\"", "'") + "\",\"ts\":" + Now() + "}");
    }

    // ------------------------------------------------- self-test parite
    // Fixture identique a eval.rs : livre coherent (net < 0 attendu)
    // puis distorsion CADJPY -0,5 % (chemin JPY->CAD 1 jambe gagnant).
    // Bloque le boot si l'arithmetique diverge du moteur Rust.
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
        LogSys("stop");
        _j?.Dispose();
    }
}
