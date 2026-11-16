#!/usr/bin/env python3
"""Test d'équivalence fonctionnelle : le même scénario est rejoué sur chaque version.

Pour chaque requête on enregistre (code HTTP, corps JSON analysé) ; les versions sont équivalentes
si toutes les réponses sont identiques. Résultat détaillé : data/equivalence/<tech>.json
"""
import hashlib
import http.client
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402

LONG_TITLE = "x" * 201
OK_TITLE = "y" * 200

# (méthode, chemin, corps ou None) — corps : dict (JSON) ou str (envoyé tel quel)
SCENARIO = [
    ("GET", "/stats", None),
    ("GET", "/tasks", None),
    ("GET", "/tasks?page=3&size=5", None),
    ("GET", "/tasks?page=500&size=20", None),
    ("GET", "/tasks?page=100000&size=100", None),
    ("GET", "/tasks?size=1000", None),
    ("GET", "/tasks?page=0", None),
    ("GET", "/tasks?size=abc", None),
    ("GET", "/tasks?page=-1", None),
    ("GET", "/tasks?page=", None),
    ("GET", "/tasks/1", None),
    ("GET", "/tasks/10000", None),
    ("GET", "/tasks/10001", None),
    ("GET", "/tasks/abc", None),
    ("GET", "/tasks/search?q=rapport", None),
    ("GET", "/tasks/search?q=rapport&status=done", None),
    ("GET", "/tasks/search?q=BUDGET&status=todo", None),
    ("GET", "/tasks/search?q=zzzzzz", None),
    ("GET", "/tasks/search?q=urgent+mensuel", None),
    ("GET", "/tasks/search?q=rendez", None),
    ("GET", "/tasks/search", None),
    ("GET", "/tasks/search?q=", None),
    ("GET", "/tasks/search?q=rapport&status=autre", None),
    ("GET", "/tasks/search?q=rapport&status=", None),
    ("GET", "/categories/1/tasks", None),
    ("GET", "/categories/20/tasks?limit=5", None),
    ("GET", "/categories/7/tasks?limit=1000", None),
    ("GET", "/categories/21/tasks", None),
    ("GET", "/categories/0/tasks", None),
    ("GET", "/categories/3/tasks?limit=0", None),
    ("GET", "/categories/3/tasks?limit=x", None),
    ("POST", "/tasks", {"title": "Tache A", "description": "desc", "status": "todo", "priority": 2,
                        "category_id": 3, "due_date": "2026-12-31"}),
    ("POST", "/tasks", {"title": "Accents é à ü 日本 \"guillemets\" \\ slash", "description": "",
                        "status": "doing", "priority": 1, "category_id": 20, "due_date": "2027-01-01"}),
    ("POST", "/tasks", {"title": OK_TITLE, "description": "d", "status": "done", "priority": 3,
                        "category_id": 1, "due_date": "2027-02-02", "extra": [1, {"a": None}]}),
    ("POST", "/tasks", {"title": LONG_TITLE, "description": "d", "status": "todo", "priority": 1,
                        "category_id": 1, "due_date": "2027-02-02"}),
    ("POST", "/tasks", {"title": "", "description": "d", "status": "todo", "priority": 1,
                        "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"description": "d", "status": "todo", "priority": 1, "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "nope", "priority": 1,
                        "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "todo", "priority": 4,
                        "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "todo", "priority": "2",
                        "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "todo", "priority": True,
                        "category_id": 1, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "todo", "priority": 1,
                        "category_id": 999, "due_date": "x"}),
    ("POST", "/tasks", {"title": "t", "description": "d", "status": "todo", "priority": 1,
                        "category_id": 1}),
    ("POST", "/tasks", "pas du json"),
    ("POST", "/tasks", "[1,2,3]"),
    ("POST", "/tasks", ""),
    ("GET", "/tasks/10001", None),
    ("GET", "/tasks/10002", None),
    ("GET", "/tasks/10003", None),
    ("PUT", "/tasks/10001", {"status": "done"}),
    ("PUT", "/tasks/10001", {"status": "bof"}),
    ("PUT", "/tasks/10001", {}),
    ("PUT", "/tasks/10001", "xx"),
    ("PUT", "/tasks/99999", {"status": "done"}),
    ("PUT", "/tasks/99999", {"status": "zzz"}),
    ("GET", "/tasks/10001", None),
    ("DELETE", "/tasks/10001", None),
    ("DELETE", "/tasks/10001", None),
    ("GET", "/tasks/10001", None),
    ("DELETE", "/tasks/99999", None),
    ("GET", "/tasks/search?q=Accents", None),
    ("GET", "/tasks?page=1&size=3", None),
    ("GET", "/categories/3/tasks?limit=2", None),
    ("GET", "/stats", None),
    ("GET", "/inconnu", None),
    ("GET", "/", None),
    ("DELETE", "/stats", None),
    ("POST", "/stats", {}),
    ("GET", "/tasks/1/extra", None),
]


def call(conn, method, path, body):
    headers = {}
    data = None
    if body is not None:
        data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    raw = r.read()
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = {"__non_json__": raw.decode("utf-8", "replace")}
    return r.status, parsed


def run_tech(tech):
    db = common.fresh_db(f"equiv-{tech}")
    port = 19000 + list(common.TECHS).index(tech)
    proc, _ = common.start_server(tech, port, db)
    results = []
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        for method, path, body in SCENARIO:
            status, parsed = call(conn, method, path, body)
            results.append({"method": method, "path": path, "status": status, "body": parsed})
    finally:
        common.stop_server(proc)
    return results


def digest(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def main():
    techs = sys.argv[1:] or list(common.TECHS)
    out_dir = os.path.join(common.ROOT, "data", "equivalence")
    os.makedirs(out_dir, exist_ok=True)
    all_res = {}
    for t in techs:
        all_res[t] = run_tech(t)
        with open(os.path.join(out_dir, f"{t}.json"), "w", encoding="utf-8") as f:
            json.dump(all_res[t], f, ensure_ascii=False, indent=1, sort_keys=True)
        print(f"{t}: {len(all_res[t])} requêtes rejouées")
    ref_name = techs[0]
    ref = all_res[ref_name]
    ok = True
    for t in techs[1:]:
        diffs = [i for i, (a, b) in enumerate(zip(ref, all_res[t])) if digest(a) != digest(b)]
        if diffs:
            ok = False
            print(f"DIFFÉRENCES {ref_name} vs {t} : {len(diffs)}")
            for i in diffs[:8]:
                print("  #", i, ref[i]["method"], ref[i]["path"])
                print("    ", ref_name, ref[i]["status"], json.dumps(ref[i]["body"], ensure_ascii=False)[:200])
                print("    ", t, all_res[t][i]["status"], json.dumps(all_res[t][i]["body"], ensure_ascii=False)[:200])
        else:
            print(f"{t}: identique à {ref_name} sur les {len(ref)} requêtes")
    # résumé des codes HTTP du scénario (contrôle de bon sens)
    codes = {}
    for e in ref:
        codes[e["status"]] = codes.get(e["status"], 0) + 1
    print("codes HTTP du scénario :", dict(sorted(codes.items())))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
