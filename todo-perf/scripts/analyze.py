#!/usr/bin/env python3
"""Analyse des données brutes : statistiques de synthèse, tests statistiques et graphiques.

Entrée  : data/raw/results.csv (une ligne par essai)
Sorties : data/summary.csv, data/stats.txt, report/figures/*.pdf, report/table_*.tex
"""
import itertools
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw", "results.csv")
FIG = os.path.join(ROOT, "report", "figures")
os.makedirs(FIG, exist_ok=True)

TECHS = ["rust", "go", "php", "node", "python"]
LABEL = {"rust": "Rust", "go": "Go", "php": "PHP", "node": "Node.js", "python": "Python"}
FUNCS = ["create", "list", "search", "category", "stats"]
FLABEL = {"create": "F1 créer", "list": "F2 lister", "search": "F3 chercher", "category": "F4 jointure",
          "stats": "F5 stats"}
MARK = {"rust": "o", "go": "s", "php": "^", "node": "D", "python": "v"}
LS = {"rust": "-", "go": "--", "php": ":", "node": "-.", "python": (0, (5, 1, 1, 1, 1, 1))}
HATCH = {"rust": "", "go": "//", "php": "..", "node": "xx", "python": "\\\\"}
GRAY = {"rust": "0.15", "go": "0.35", "php": "0.55", "node": "0.75", "python": "0.92"}

plt.rcParams.update({
    "font.family": "serif", "font.serif": ["Nimbus Roman", "Liberation Serif", "Times New Roman", "DejaVu Serif"],
    "font.size": 8, "axes.labelsize": 8, "axes.titlesize": 8, "legend.fontsize": 7,
    "xtick.labelsize": 7, "ytick.labelsize": 7, "axes.linewidth": 0.6, "pdf.fonttype": 42,
})

df = pd.read_csv(RAW)
n_rows = len(df)
levels = sorted(df.users.unique())
ref_level = 50 if 50 in levels else levels[len(levels) // 2]


def ci95(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return np.nan
    return stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))


METRICS = ["rps", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "cpu_ms_per_req", "pss_end_mb", "rss_peak_mb",
           "startup_ms", "idle_pss_mb", "errors"]
rows = []
for (t, f, u), g in df.groupby(["tech", "func", "users"]):
    r = {"tech": t, "func": f, "users": u, "n": len(g)}
    for m in METRICS:
        r[m + "_mean"] = g[m].mean()
        r[m + "_sd"] = g[m].std(ddof=1)
        r[m + "_ci95"] = ci95(g[m])
        r[m + "_median"] = g[m].median()
    rows.append(r)
summ = pd.DataFrame(rows)
summ.to_csv(os.path.join(ROOT, "data", "summary.csv"), index=False, float_format="%.4f")


def S(t, f, u, m, k="mean"):
    r = summ[(summ.tech == t) & (summ.func == f) & (summ.users == u)]
    return float(r[f"{m}_{k}"].iloc[0]) if len(r) else np.nan


out = []
P = out.append
P(f"Essais analysés : {n_rows}; essais par cas : min {summ.n.min()} max {summ.n.max()}")
P(f"Total d'erreurs HTTP/transport : {int(df.errors.sum())}")
P(f"Niveau de référence : {ref_level} utilisateurs")

# ---- Tests statistiques (Mann-Whitney, deux à deux, correction de Holm) sur le débit ----
P("\n== Comparaisons deux à deux du débit (Mann-Whitney U, Holm) au niveau de référence ==")
for f in FUNCS:
    tests = []
    for a, b in itertools.combinations(TECHS, 2):
        xa = df[(df.tech == a) & (df.func == f) & (df.users == ref_level)].rps
        xb = df[(df.tech == b) & (df.func == f) & (df.users == ref_level)].rps
        if len(xa) > 1 and len(xb) > 1:
            u, p = stats.mannwhitneyu(xa, xb, alternative="two-sided")
            tests.append((a, b, p, xa.median() / xb.median()))
    order = np.argsort([t[2] for t in tests])
    m = len(tests)
    adj = {}
    run = 0.0
    for rank, idx in enumerate(order):
        run = max(run, min(1.0, tests[idx][2] * (m - rank)))
        adj[idx] = run
    nonsig = [f"{tests[i][0]}-{tests[i][1]} (p={adj[i]:.3g})" for i in range(m) if adj[i] >= 0.05]
    P(f"{f}: {m - len(nonsig)}/{m} paires différentes (p<0,05). Non significatives : "
      + (", ".join(nonsig) if nonsig else "aucune"))

# ---- Rapports de débit ----
P("\n== Débit moyen (req/s) au niveau de référence ==")
for f in FUNCS:
    P(f"{f}: " + ", ".join(f"{t}={S(t, f, ref_level, 'rps'):.0f}" for t in TECHS))
P("\n== Rapports de débit (technologie / Python) ==")
for f in FUNCS:
    P(f"{f}: " + ", ".join(f"{t}={S(t, f, ref_level, 'rps') / S('python', f, ref_level, 'rps'):.2f}" for t in TECHS))
P("\n== Latence p95 (ms) au niveau de référence ==")
for f in FUNCS:
    P(f"{f}: " + ", ".join(f"{t}={S(t, f, ref_level, 'p95_ms'):.1f}" for t in TECHS))
P("\n== CPU par requête (ms) au niveau de référence ==")
for f in FUNCS:
    P(f"{f}: " + ", ".join(f"{t}={S(t, f, ref_level, 'cpu_ms_per_req'):.3f}" for t in TECHS))
P("\n== Coefficient de variation du débit (écart-type / moyenne, %) ==")
for f in FUNCS:
    P(f"{f}: " + ", ".join(f"{t}={100 * S(t, f, ref_level, 'rps', 'sd') / S(t, f, ref_level, 'rps'):.1f}" for t in TECHS))
P("\n== Mémoire (PSS en fin d'essai, Mo) et démarrage (ms), moyenne sur tous les essais ==")
for t in TECHS:
    g = df[df.tech == t]
    P(f"{t}: pss_fin={g.pss_end_mb.mean():.1f} pic_rss={g.rss_peak_mb.mean():.1f} repos={g.idle_pss_mb.mean():.1f}"
      f" demarrage={g.startup_ms.mean():.0f}")
P("\n== Évolution avec la charge : latence moyenne (ms) par niveau ==")
for f in FUNCS:
    for t in TECHS:
        P(f"{f}/{t}: " + ", ".join(f"{u}->{S(t, f, u, 'mean_ms'):.1f}" for u in levels)
          + " | débit: " + ", ".join(f"{u}->{S(t, f, u, 'rps'):.0f}" for u in levels))
with open(os.path.join(ROOT, "data", "stats.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print("\n".join(out[:3]))


# ---- Graphiques ----
def save(fig, name):
    fig.savefig(os.path.join(FIG, name), bbox_inches="tight", pad_inches=0.02)
    plt.close(fig)


def grouped_bars(metric, ylabel, fname, logy=True, funcs=FUNCS, level=ref_level):
    fig, ax = plt.subplots(figsize=(3.45, 2.3))
    w = 0.16
    x = np.arange(len(funcs))
    for i, t in enumerate(TECHS):
        vals = [S(t, f, level, metric) for f in funcs]
        errs = [S(t, f, level, metric, "ci95") for f in funcs]
        ax.bar(x + (i - 2) * w, vals, w, yerr=errs, label=LABEL[t], color=GRAY[t], edgecolor="black",
               linewidth=0.5, hatch=HATCH[t], error_kw={"elinewidth": 0.5, "capsize": 1})
    ax.set_xticks(x)
    ax.set_xticklabels([FLABEL[f] for f in funcs])
    ax.set_ylabel(ylabel)
    if logy:
        ax.set_yscale("log")
    ax.legend(ncol=5, frameon=False, loc="lower center", bbox_to_anchor=(0.5, 1.0), columnspacing=0.7,
              handlelength=1.1, handletextpad=0.4)
    ax.grid(axis="y", linewidth=0.3, alpha=0.6)
    ax.set_axisbelow(True)
    save(fig, fname)


def lines(func, metric, ylabel, fname, logy=False):
    fig, ax = plt.subplots(figsize=(3.45, 2.2))
    for t in TECHS:
        y = [S(t, func, u, metric) for u in levels]
        e = [S(t, func, u, metric, "ci95") for u in levels]
        ax.errorbar(levels, y, yerr=e, label=LABEL[t], marker=MARK[t], linestyle=LS[t], color="black",
                    markersize=3.5, linewidth=0.8, markerfacecolor="white" if t in ("go", "node") else "black",
                    elinewidth=0.5, capsize=1.5)
    ax.set_xlabel("Utilisateurs simultanés")
    ax.set_ylabel(ylabel)
    ax.set_xticks(levels)
    if logy:
        ax.set_yscale("log")
    ax.legend(frameon=False, ncol=2, loc="best")
    ax.grid(linewidth=0.3, alpha=0.6)
    ax.set_axisbelow(True)
    save(fig, fname)


grouped_bars("rps", "Débit (requêtes/s, éch. log)", "fig_debit.pdf")
grouped_bars("p95_ms", "Latence p95 (ms, éch. log)", "fig_p95.pdf")
grouped_bars("cpu_ms_per_req", "Temps CPU par requête (ms, log)", "fig_cpu.pdf")
lines("create", "p95_ms", "Latence p95 de F1 (ms)", "fig_charge_create.pdf")
lines("list", "rps", "Débit de F2 (requêtes/s)", "fig_charge_list.pdf")
lines("stats", "p95_ms", "Latence p95 de F5 (ms)", "fig_charge_stats.pdf")

# mémoire et démarrage
fig, axs = plt.subplots(1, 2, figsize=(3.45, 2.0))
mem = [df[df.tech == t].pss_end_mb.mean() for t in TECHS]
mem_e = [ci95(df[df.tech == t].pss_end_mb) for t in TECHS]
st = [df[df.tech == t].startup_ms.mean() for t in TECHS]
st_e = [ci95(df[df.tech == t].startup_ms) for t in TECHS]
for ax, vals, errs, lab in ((axs[0], mem, mem_e, "Mémoire PSS (Mo)"), (axs[1], st, st_e, "Démarrage (ms)")):
    ax.bar(range(len(TECHS)), vals, yerr=errs, color=[GRAY[t] for t in TECHS], edgecolor="black", linewidth=0.5,
           error_kw={"elinewidth": 0.5, "capsize": 1})
    for i, t in enumerate(TECHS):
        ax.patches[i].set_hatch(HATCH[t])
    ax.set_xticks(range(len(TECHS)))
    ax.set_xticklabels([LABEL[t] for t in TECHS], rotation=45, ha="right")
    ax.set_ylabel(lab)
    ax.grid(axis="y", linewidth=0.3, alpha=0.6)
    ax.set_axisbelow(True)
fig.tight_layout(pad=0.3)
save(fig, "fig_memoire_demarrage.pdf")


# ---- Tableaux LaTeX ----
def fmt(v, d=0):
    s = f"{v:,.{d}f}".replace(",", "\\,")
    return s.replace(".", ",")


with open(os.path.join(ROOT, "report", "table_debit.tex"), "w", encoding="utf-8") as fh:
    for t in TECHS:
        cells = []
        for f in FUNCS:
            cells.append(fmt(S(t, f, ref_level, "rps"), 0))
        fh.write(f"{LABEL[t]} & " + " & ".join(cells) + " \\\\\n")
with open(os.path.join(ROOT, "report", "table_synthese.tex"), "w", encoding="utf-8") as fh:
    for t in TECHS:
        g = df[df.tech == t]
        fh.write(f"{LABEL[t]} & {fmt(S(t, 'list', ref_level, 'p95_ms'), 1)} & "
                 f"{fmt(S(t, 'create', ref_level, 'cpu_ms_per_req'), 2)} & "
                 f"{fmt(S(t, 'stats', ref_level, 'cpu_ms_per_req'), 2)} & {fmt(g.pss_end_mb.mean(), 0)} & "
                 f"{fmt(g.startup_ms.mean(), 0)} \\\\\n")
print("analyse terminée")
