# gen_statement.py — relevé de transactions (statement) des exécutions PoC.
# Entrée : trades_extract.json produit sur le serveur (extraction des JSONs de run).
# Sortie : statement_trades.csv (flux complet) + statement_flow.pdf (relevé graphique).
import json
import csv
import collections
import datetime as dt
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

HERE = Path(__file__).parent
TRADES = json.loads((HERE / "trades_extract.json").read_text())


def path_str(tr):
    """Reconstruit la chaîne de devises : start -> ... -> end."""
    chain = [tr["start"]]
    cur = tr["start"]
    for sym in tr.get("legs") or []:
        b, q = sym[:3], sym[3:]
        cur = b if cur == q else (q if cur == b else "?")
        chain.append(cur)
    return "->".join(chain)


for tr in TRADES:
    tr["dt"] = dt.datetime.fromtimestamp(tr["t"] / 1000, dt.UTC)
    tr["h_paris"] = tr["dt"].hour + 1  # CET = UTC+1 à fin octobre
    tr["day"] = tr["dt"].strftime("%Y-%m-%d")
    tr["path"] = path_str(tr)

# ---------- CSV : flux complet ----------
with open(HERE / "statement_trades.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["#", "day", "time_utc", "hour_paris", "path", "legs",
                "result", "pnl_usd", "cum_pnl_day", "fills", "parts",
                "dur_ms", "resid_usd"])
    n = 0
    for day in sorted({t["day"] for t in TRADES}):
        cum = 0.0
        for tr in sorted([t for t in TRADES if t["day"] == day],
                         key=lambda x: x["t"]):
            n += 1
            cum += tr["pnl"]
            resid = sum(a for a, _ in tr.get("resid") or [])
            w.writerow([n, day, tr["dt"].strftime("%H:%M:%S.%f")[:-3],
                        tr["h_paris"], tr["path"],
                        "+".join(tr.get("legs") or []), tr["res"],
                        f"{tr['pnl']:.4f}", f"{cum:.4f}",
                        tr.get("fills", ""), tr.get("parts", ""),
                        tr.get("dur", ""), f"{resid:.0f}"])

# ---------- agrégats ----------
days = sorted({t["day"] for t in TRADES})
byday = {d: sorted([t for t in TRADES if t["day"] == d],
                   key=lambda x: x["t"]) for d in days}
pcnt = collections.Counter(t["path"] for t in TRADES)
ppnl = collections.defaultdict(float)
for t in TRADES:
    ppnl[t["path"]] += t["pnl"]

# ---------- figures ----------
FIG = HERE / "statement_figs"
FIG.mkdir(exist_ok=True)
plt.rcParams.update({"figure.dpi": 150, "font.size": 8})

# 1) equity curve globale (4 jours concaténés, discontinuités marquées)
fig, ax = plt.subplots(figsize=(8.2, 3.4))
cum, xs, ys = 0.0, [], []
for d in days:
    for tr in byday[d]:
        cum += tr["pnl"]
        xs.append(tr["dt"]); ys.append(cum)
ax.plot(xs, ys, lw=1.0, color="#0b3d91")
ax.axhline(0, color="#888", lw=0.5)
for d in days[1:]:
    ax.axvline(dt.datetime.fromisoformat(d).replace(tzinfo=dt.UTC),
               color="#ccc", lw=0.6, ls="--")
for i, d in enumerate(days):
    sub = [t for t in byday[d]]
    p = sum(t["pnl"] for t in sub)
    mid = sub[len(sub)//2]["dt"]
    ax.annotate(f"{d[5:]}\n{p:+.0f} $", (mid, ax.get_ylim()[1]*0.92),
                ha="center", fontsize=7,
                color="#0b3d91" if p >= 0 else "#a33")
ax.set_title("Courbe cumulée du PnL — toutes exécutions, file 0,1×1,0, "
             "comm 6 $ RT", fontsize=9)
ax.set_ylabel("PnL cumulé (USD)")
ax.xaxis.set_major_formatter(mdates.DateFormatter("%d/%m %Hh"))
fig.tight_layout(); fig.savefig(FIG / "equity.png"); plt.close(fig)

# 2) par jour : scatter PnL par trade + barres horaires
fig, axes = plt.subplots(len(days), 2, figsize=(8.2, 2.1 * len(days)),
                         squeeze=False)
for i, d in enumerate(days):
    sub = byday[d]
    ax = axes[i][0]
    cols = ["#0b3d91" if t["pnl"] >= 0 else "#a33" for t in sub]
    ax.scatter([t["dt"] for t in sub], [t["pnl"] for t in sub],
               s=6, c=cols)
    ax.axhline(0, color="#888", lw=0.5)
    ax.set_title(f"{d} — PnL par exécution (n={len(sub)})", fontsize=8)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Hh"))
    ax.set_ylabel("USD")
    ax = axes[i][1]
    hh = collections.defaultdict(float)
    for t in sub:
        hh[t["h_paris"]] += t["pnl"]
    hs = sorted(hh)
    ax.bar([str(h) for h in hs], [hh[h] for h in hs],
           color=["#0b3d91" if hh[h] >= 0 else "#a33" for h in hs])
    ax.axhline(0, color="#888", lw=0.5)
    ax.set_title(f"{d} — PnL par heure (Paris)", fontsize=8)
    ax.tick_params(axis="x", labelsize=6, rotation=90)
fig.tight_layout(); fig.savefig(FIG / "perday.png"); plt.close(fig)

# 3) top chemins : fréquence + PnL
fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.0))
top_c = pcnt.most_common(12)
ax = axes[0]
ax.barh([p for p, _ in top_c][::-1], [c for _, c in top_c][::-1],
        color="#0b3d91")
ax.set_title("Chemins les plus fréquents (nb exécutions)", fontsize=8)
ax.tick_params(axis="y", labelsize=6.5)
top_p = sorted(ppnl.items(), key=lambda kv: -abs(kv[1]))[:12]
ax = axes[1]
ax.barh([p for p, _ in top_p][::-1], [v for _, v in top_p][::-1],
        color=["#0b3d91" if v >= 0 else "#a33" for _, v in top_p][::-1])
ax.set_title("Chemins dominants par |PnL| (USD)", fontsize=8)
ax.tick_params(axis="y", labelsize=6.5)
fig.tight_layout(); fig.savefig(FIG / "paths.png"); plt.close(fig)

# ---------- PDF : relevé façon broker statement ----------
from matplotlib.backends.backend_pdf import PdfPages

HDR = "Oceandeep Capital Management LP — Relevé des exécutions PoC\n" \
      "Chemins ouverts FX · pile poc-rc · réel IOC lat 5 ms · file 0,1×1,0 " \
      "· commissions 6 $ RT"

with PdfPages(HERE / "statement_flow.pdf") as pdf:
    # page 1 : synthèse
    fig = plt.figure(figsize=(8.27, 11.69))
    fig.text(0.08, 0.94, "RELEVÉ D'EXÉCUTIONS — STATEMENT", fontsize=15,
             fontweight="bold")
    fig.text(0.08, 0.905, HDR, fontsize=8, color="#444")
    rows = []
    for d in days:
        sub = byday[d]
        p = sum(t["pnl"] for t in sub)
        w_ = sum(1 for t in sub if t["pnl"] > 0)
        tag = "in-sample" if d != "2026-10-27" else "holdout"
        rows.append([d, tag, len(sub), f"{p:+,.2f} $",
                     f"{w_}/{len(sub)}", f"{p/len(sub):+.3f} $"])
    tot = sum(t["pnl"] for t in TRADES)
    tbl = plt.table(
        cellText=rows + [["TOTAL", "", len(TRADES), f"{tot:+,.2f} $",
                          "", f"{tot/len(TRADES):+.3f} $"]],
        colLabels=["Jour", "Échantillon", "Execs", "PnL", "Gagnants",
                   "PnL/exec"],
        cellLoc="center", loc="center", bbox=[0.08, 0.62, 0.84, 0.22])
    tbl.auto_set_font_size(False); tbl.set_fontsize(8)
    plt.gca().set_axis_off()
    fig.text(0.08, 0.53,
             f"Univers : 28 paires FX · 2 072 chemins ouverts · notionnel "
             f"~100 k$/séquence\nExécutions : {len(TRADES)} sur "
             f"{len(days)} jours · résidus marqués Convention A\n"
             f"Fenêtre de production visée : 07h–21h Zurich — la nuit "
             f"reste mesurée mais hors périmètre cible",
             fontsize=9)
    pdf.savefig(fig); plt.close(fig)

    # page 2 : equity curve
    fig = plt.figure(figsize=(11.69, 8.27))
    img = plt.imread(FIG / "equity.png")
    plt.imshow(img); plt.axis("off")
    pdf.savefig(fig); plt.close(fig)

    # pages 3+ : détail par jour
    fig = plt.figure(figsize=(8.27, 11.69))
    img = plt.imread(FIG / "perday.png")
    plt.imshow(img); plt.axis("off")
    pdf.savefig(fig); plt.close(fig)

    fig = plt.figure(figsize=(8.27, 11.69))
    img = plt.imread(FIG / "paths.png")
    plt.imshow(img); plt.axis("off")
    pdf.savefig(fig); plt.close(fig)

    # pages finales : table des 134 exécutions du holdout
    hold = byday.get("2026-10-27", [])
    per = 34
    for i in range(0, len(hold), per):
        chunk = hold[i:i + per]
        fig = plt.figure(figsize=(8.27, 11.69))
        fig.text(0.08, 0.96, f"HOLDOUT 2026-10-27 — exécutions "
                             f"{i+1} à {i+len(chunk)}/{len(hold)}",
                 fontsize=11, fontweight="bold")
        cells = [[f"{t['dt'].strftime('%H:%M:%S')}.{t['t']%1000:03d}",
                  t["path"], "+".join(t["legs"]), t["res"],
                  f"{t['pnl']:+.2f}", t.get("dur", ""),
                  f"{sum(a for a,_ in t.get('resid') or []):,.0f}"]
                 for t in chunk]
        tbl = plt.table(cellText=cells,
                        colLabels=["Heure UTC", "Chemin", "Jambes",
                                   "Résultat", "PnL $", "Durée ms",
                                   "Résidu $"],
                        cellLoc="center", loc="upper center",
                        bbox=[0.02, 0.06, 0.96, 0.86])
        tbl.auto_set_font_size(False); tbl.set_fontsize(6.5)
        plt.gca().set_axis_off()
        pdf.savefig(fig); plt.close(fig)

print("CSV: statement_trades.csv |", len(TRADES), "exécutions")
print("PDF: statement_flow.pdf")
print("figs:", [f.name for f in FIG.iterdir()])
