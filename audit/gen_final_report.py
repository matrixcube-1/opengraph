#!/usr/bin/env python3
"""Rapport PoC institutionnel — Arbitrage FX chemins ouverts, Convention A.
Figures matplotlib + PDF reportlab. Usage : python gen_final_report.py
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np

OUT = Path(__file__).parent / "rapport_final"
OUT.mkdir(exist_ok=True)

NAVY = "#0f2a4a"; STEEL = "#3a6ea5"; GREY = "#8a97a5"; RED = "#b3402a"
GREEN = "#2a7a4a"; GOLD = "#b3862a"; LIGHT = "#eef2f6"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9.5,
    "axes.edgecolor": GREY, "axes.labelcolor": NAVY,
    "axes.titlesize": 10.5, "axes.titleweight": "bold",
    "xtick.color": NAVY, "ytick.color": NAVY,
    "figure.dpi": 160, "savefig.bbox": "tight",
    "axes.grid": True, "grid.color": "#d8dfe7", "grid.linewidth": 0.6,
    "axes.axisbelow": True,
})


def save(fig, name):
    fig.savefig(OUT / name, facecolor="white")
    plt.close(fig)


# ---------- Figure 1 : PnL par jour (in-sample vs holdout) ----------
fig, ax = plt.subplots(figsize=(6.4, 3.0))
days = ["22 oct.\n(in-sample)", "23 oct.\n(in-sample)",
        "26 oct.\n(in-sample)", "27 oct.\nHOLDOUT"]
pnl = [48023.06, -630.42, -54.68, -60.65]
cols = [STEEL, STEEL, STEEL, GOLD]
b = ax.bar(days, pnl, color=cols, width=0.62, edgecolor="white")
for r, v in zip(b, pnl):
    ax.annotate(f"{v:+,.0f} $".replace(",", " "),
                (r.get_x() + r.get_width() / 2, v + (1500 if v > 0 else -3400)),
                ha="center", fontsize=9, fontweight="bold",
                color=NAVY if v > 0 else RED)
ax.axhline(0, color=NAVY, lw=0.8)
ax.set_ylabel("PnL net (USD)")
ax.set_title("PnL journalier — configuration officielle (IOC réel, 5 ms, file 0,1×1,0)")
ax.set_ylim(-9000, 56000)
save(fig, "fig_pnl_jours.png")

# ---------- Figure 2 : décomposition des frictions (holdout) ----------
fig, ax = plt.subplots(figsize=(6.4, 3.1))
steps = ["Signal brut\n(interne, lat 0)", "+ Venue IOC\n+ latence 5 ms",
         "+ File pessimiste\n0,1 × 1,0"]
vals = [128.28, 98.33, -60.65]
cols2 = [GREEN, STEEL, RED]
b = ax.bar(steps, vals, color=cols2, width=0.55, edgecolor="white")
for r, v in zip(b, vals):
    ax.annotate(f"{v:+.2f} $", (r.get_x() + r.get_width() / 2,
                                v + (7 if v > 0 else -22)),
                ha="center", fontsize=9.5, fontweight="bold", color=NAVY)
ax.axhline(0, color=NAVY, lw=0.8)
ax.annotate("−30 $ (transport)", (1, 118), ha="center", fontsize=8, color=GREY)
ax.annotate("−159 $ (file)", (2, 60), ha="center", fontsize=8, color=GREY)
ax.set_ylabel("PnL net cumulé (USD)")
ax.set_title("Holdout 27/10 — cascade des frictions (134 exécutions identiques)")
ax.set_ylim(-110, 165)
save(fig, "fig_decomp.png")

# ---------- Figure 3 : PedAnova ----------
fig, ax = plt.subplots(figsize=(6.4, 2.9))
params = ["cooldown_s", "spread_cap", "ddd_min", "tau_slip_bp",
          "q_min", "lat_ms", "fresh_ms", "comm_scale"]
imp = [0.307, 0.156, 0.144, 0.116, 0.096, 0.084, 0.071, 0.025]
cols3 = [RED if i == 0 else STEEL for i in range(len(params))]
ax.barh(params[::-1], imp[::-1], color=cols3[::-1], height=0.62,
        edgecolor="white")
for i, v in enumerate(imp[::-1]):
    ax.text(v + 0.005, i, f"{v:.3f}", va="center", fontsize=9,
            fontweight="bold", color=NAVY)
ax.set_xlabel("Importance PedAnova (cible : maximiser le PnL)")
ax.set_title("Importance des hyperparamètres — 74 trials Optuna complets")
ax.set_xlim(0, 0.36)
save(fig, "fig_pedanova.png")

# ---------- Figure 4 : entonnoir opportunités ----------
fig, ax = plt.subplots(figsize=(6.4, 3.0))
x = np.arange(2)
w = 0.27
evals = [17267, 13136]
execs = [3735, 134]
wins = [None, 46]
b1 = ax.bar(x - w, evals, w, label="Évaluations/jour", color=STEEL)
b2 = ax.bar(x, execs, w, label="Séquences exécutées", color=NAVY)
b3 = ax.bar(x + w, [np.nan, 46], w, label="Gagnantes (file pess.)",
            color=GREEN)
for xi, v in zip(x - w, evals):
    ax.annotate(f"{v:,}".replace(",", " "), (xi, v * 1.12), ha="center",
                fontsize=9, fontweight="bold", color=NAVY)
for xi, v in zip(x, execs):
    ax.annotate(f"{v:,}".replace(",", " "), (xi, v * 1.12), ha="center",
                fontsize=9, fontweight="bold", color=NAVY)
ax.annotate("46", (x[1] + w, 46 * 1.6), ha="center", fontsize=9,
            fontweight="bold", color=GREEN)
ax.set_yscale("log")
ax.set_xticks(x, ["In-sample (moy./jour)", "Holdout 27/10"])
ax.set_ylabel("Volume (échelle log)")
ax.set_title("Effondrement du flux d'opportunités — journée pré-banques centrales")
ax.legend(loc="upper right", fontsize=8.5, frameon=False)
save(fig, "fig_funnel.png")

# ---------- Figure 5 : latence ----------
fig, ax = plt.subplots(figsize=(6.4, 3.0))
labels = ["p50", "p95", "p99", "max"]
calc = [23.8, 43.3, 56.4, 440.9]
e2e = [None, None, 640, 669]
x = np.arange(len(labels))
ax.bar(x - 0.2, calc, 0.38, label="eval_at (calcul)", color=STEEL)
ax.bar(x + 0.2, [np.nan, np.nan, 640, 669], 0.38,
       label="e2e tick→ordre (IOC)", color=GOLD)
ax.axhline(1000, color=RED, ls="--", lw=1.1)
ax.text(3.45, 1150, "gate p99 = 1 ms", color=RED, fontsize=8.5, ha="right")
ax.axhline(5000, color=GREY, ls="--", lw=1.1)
ax.text(3.45, 5600, "gate e2e = 5 ms", color=GREY, fontsize=8.5, ha="right")
for xi, v in zip(x - 0.2, calc):
    ax.annotate(f"{v:.0f}", (xi, v * 1.25), ha="center", fontsize=8.5,
                fontweight="bold", color=NAVY)
ax.annotate("640", (x[2] + 0.2, 800), ha="center", fontsize=8.5,
            fontweight="bold", color=NAVY)
ax.annotate("669", (x[3] + 0.2, 840), ha="center", fontsize=8.5,
            fontweight="bold", color=NAVY)
ax.set_yscale("log")
ax.set_xticks(x, labels)
ax.set_ylabel("Latence (µs, échelle log)")
ax.set_ylim(10, 30000)
ax.set_title("Latences mesurées (perf_counter_ns, gc.freeze) — toutes < gates")
ax.legend(loc="upper left", fontsize=8.5, frameon=False)
save(fig, "fig_latency.png")

# ---------- Figure 6 : coût des frictions in-sample ----------
fig, ax = plt.subplots(figsize=(6.4, 3.0))
cfg = ["Référence\n(interne lat 0)", "Pile complète\nIOC 5 ms + file"]
v = [69340.85, 47337.96]
b = ax.bar(cfg, v, color=[GREEN, STEEL], width=0.45, edgecolor="white")
for r, x_ in zip(b, v):
    ax.annotate(f"{x_:+,.0f} $".replace(",", " "),
                (r.get_x() + r.get_width() / 2, x_ + 1800),
                ha="center", fontsize=10, fontweight="bold", color=NAVY)
ax.annotate("−31,7 % — coût total des frictions",
            (0.5, 60000), ha="center", fontsize=9.5, color=RED,
            fontweight="bold")
ax.set_ylabel("PnL net 3 jours (USD)")
ax.set_title("In-sample nettoyé — résistance économique aux frictions")
ax.set_ylim(0, 80000)
save(fig, "fig_frictions.png")

# ---------- Figure 7 : résiduel inventaire ----------
fig, (a1, a2) = plt.subplots(1, 2, figsize=(6.4, 2.6))
runs = ["In-sample\n(3 jours)", "Holdout\n(1 jour)"]
notion = [1120488720 / 1e6, 13400376 / 1e6]
drift = [-3058166 / 1e3, -22357 / 1e3]
b1 = a1.bar(runs, notion, color=STEEL, width=0.5)
for r, v in zip(b1, notion):
    a1.annotate(f"{v:,.1f} M$".replace(",", " "),
                (r.get_x() + r.get_width() / 2, v * 1.02),
                ha="center", fontsize=9, fontweight="bold", color=NAVY)
a1.set_title("Notionnel résiduel cumulé")
a1.set_ylabel("M$")
a1.set_ylim(0, 1350)
b2 = a2.bar(runs, drift, color=RED, width=0.5)
for r, v in zip(b2, drift):
    a2.annotate(f"{v:,.0f} k$".replace(",", " "),
                (r.get_x() + r.get_width() / 2, v - 320),
                ha="center", fontsize=9, fontweight="bold", color=RED)
a2.axhline(0, color=NAVY, lw=0.8)
a2.set_title("Dérive mark-to-market")
a2.set_ylabel("k$")
a2.set_ylim(-3700, 400)
fig.subplots_adjust(top=0.78, wspace=0.35)
fig.suptitle("Exposition d'inventaire Convention A",
             fontsize=10.5, fontweight="bold", color=NAVY, y=0.97)
save(fig, "fig_resid.png")

print("figures ok:", sorted(p.name for p in OUT.glob("*.png")))
