#!/usr/bin/env python3
"""Campagne de tests de charge.

Pour chaque (fonctionnalité, niveau de charge, essai) et pour chaque technologie (ordre tiré au sort,
graine fixe, afin que la dérive de la machine ne favorise pas toujours la même technologie) :
  1. copie neuve de la base de départ, démarrage du serveur (épinglé sur le cœur 0) ;
  2. 100 requêtes de chauffe (non comptées) ;
  3. essai mesuré : R requêtes envoyées par N utilisateurs simultanés (générateur épinglé sur le cœur 1) ;
  4. relevé du temps CPU consommé par le serveur et de sa mémoire ; arrêt du serveur.
Chaque essai ajoute une ligne à data/raw/results.csv (reprise possible : les lignes existantes sont ignorées).
"""
import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

LOADGEN = os.path.join(common.ROOT, "loadgen", "loadgen")
FIELDS = ["tech", "func", "users", "trial", "requests", "errors", "elapsed_s", "rps", "mean_ms", "p50_ms",
          "p95_ms", "p99_ms", "max_ms", "cpu_ms_per_req", "rss_peak_mb", "pss_end_mb", "startup_ms",
          "idle_pss_mb"]
REQUESTS = {"create": 1000, "list": 1000, "category": 1000, "search": 500, "stats": 500}


def tree(pid):
    try:
        p = psutil.Process(pid)
        return [p] + p.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def cpu_s(pid):
    t = 0.0
    for p in tree(pid):
        try:
            c = p.cpu_times()
            t += c.user + c.system
        except psutil.NoSuchProcess:
            pass
    return t


def pss_mb(pid):
    t = 0
    for p in tree(pid):
        try:
            t += p.memory_full_info().pss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return t / 1048576


def peak_rss_mb(pid):
    t = 0
    for p in tree(pid):
        try:
            with open(f"/proc/{p.pid}/status") as f:
                for line in f:
                    if line.startswith("VmHWM:"):
                        t += int(line.split()[1]) * 1024
        except OSError:
            pass
    return t / 1048576


def loadgen(port, fn, users, n, offset=0):
    r = subprocess.run(["taskset", "-c", "1", LOADGEN, "-url", f"http://127.0.0.1:{port}", "-func", fn,
                        "-c", str(users), "-n", str(n), "-offset", str(offset)],
                       capture_output=True, text=True, timeout=300)
    return json.loads(r.stdout)


def one_trial(tech, fn, users, trial, port):
    db = common.fresh_db(f"bench-{tech}")
    proc, startup_ms = common.start_server(tech, port, db, cpu=0)
    try:
        time.sleep(0.05)
        idle = pss_mb(proc.pid)
        loadgen(port, fn, users, 100)  # chauffe, non comptée
        c0 = cpu_s(proc.pid)
        res = loadgen(port, fn, users, REQUESTS[fn], offset=100)
        c1 = cpu_s(proc.pid)
        row = {"tech": tech, "func": fn, "users": users, "trial": trial, "requests": res["requests"],
               "errors": res["errors"], "elapsed_s": round(res["elapsed_s"], 4), "rps": round(res["rps"], 2),
               "mean_ms": round(res["mean_ms"], 4), "p50_ms": round(res["p50_ms"], 4),
               "p95_ms": round(res["p95_ms"], 4), "p99_ms": round(res["p99_ms"], 4),
               "max_ms": round(res["max_ms"], 4),
               "cpu_ms_per_req": round((c1 - c0) * 1000 / res["requests"], 4),
               "rss_peak_mb": round(peak_rss_mb(proc.pid), 2), "pss_end_mb": round(pss_mb(proc.pid), 2),
               "startup_ms": round(startup_ms, 2), "idle_pss_mb": round(idle, 2)}
    finally:
        common.stop_server(proc)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--levels", type=int, nargs="+", default=[10, 50, 100])
    ap.add_argument("--funcs", nargs="+", default=["create", "list", "search", "category", "stats"])
    ap.add_argument("--techs", nargs="+", default=list(common.TECHS))
    ap.add_argument("--out", default=os.path.join(common.ROOT, "data", "raw", "results.csv"))
    a = ap.parse_args()

    done = set()
    exists = os.path.exists(a.out)
    if exists:
        with open(a.out, newline="") as f:
            for r in csv.DictReader(f):
                done.add((r["tech"], r["func"], int(r["users"]), int(r["trial"])))
    f = open(a.out, "a", newline="")
    w = csv.DictWriter(f, fieldnames=FIELDS)
    if not exists:
        w.writeheader()

    total = len(a.funcs) * len(a.levels) * a.trials * len(a.techs)
    n = len(done)
    t_start = time.time()
    port = 21000
    for fn in a.funcs:
        for users in a.levels:
            for trial in range(1, a.trials + 1):
                order = list(a.techs)
                random.Random(f"{fn}-{users}-{trial}").shuffle(order)
                for tech in order:
                    if (tech, fn, users, trial) in done:
                        continue
                    port = 21000 + (port - 21000 + 1) % 3000
                    row = one_trial(tech, fn, users, trial, port)
                    w.writerow(row)
                    f.flush()
                    n += 1
                    if n % 25 == 0:
                        el = time.time() - t_start
                        print(f"[{n}/{total}] {fn} u={users} essai={trial} ; écoulé {el/60:.1f} min", flush=True)
    print("terminé", flush=True)


if __name__ == "__main__":
    main()
