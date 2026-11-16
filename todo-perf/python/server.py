"""Version Python : Flask + sqlite3 (bibliothèque standard)."""
import contextlib
import json
import logging
import os
import queue
import re
import sqlite3

from flask import Flask, Response, request
from werkzeug.serving import WSGIRequestHandler

DB_PATH = os.environ.get("DB_PATH", "tasks.db")
PORT = int(os.environ.get("PORT", "8000"))
POOL_SIZE = 4
STATUSES = ("todo", "doing", "done")
COLS = "id,title,description,status,priority,category_id,due_date"
INT_RE = re.compile(r"[0-9]{1,9}")

app = Flask(__name__)
pool = queue.Queue()
for _ in range(POOL_SIZE):
    c = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=5000")
    pool.put(c)


@contextlib.contextmanager
def db():
    c = pool.get()
    try:
        yield c
    finally:
        pool.put(c)


def js(obj, status=200):
    return Response(json.dumps(obj, separators=(",", ":"), ensure_ascii=False),
                    status=status, mimetype="application/json")


def bad():
    return js({"error": "invalid input"}, 400)


def notfound():
    return js({"error": "not found"}, 404)


def task(r):
    return {"id": r[0], "title": r[1], "description": r[2], "status": r[3],
            "priority": r[4], "category_id": r[5], "due_date": r[6]}


def is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def pint(name, default):
    v = request.args.get(name)
    if v is None:
        return default
    return int(v) if INT_RE.fullmatch(v) else None


@app.errorhandler(404)
@app.errorhandler(405)
def _nf(_e):
    return notfound()


@app.post("/tasks")
def create_task():
    d = request.get_json(silent=True, force=True)
    if not (isinstance(d, dict)
            and isinstance(d.get("title"), str) and 0 < len(d["title"]) <= 200
            and isinstance(d.get("description"), str)
            and d.get("status") in STATUSES
            and is_int(d.get("priority")) and 1 <= d["priority"] <= 3
            and is_int(d.get("category_id"))
            and isinstance(d.get("due_date"), str)):
        return bad()
    with db() as c:
        try:
            cur = c.execute("INSERT INTO tasks(title,description,status,priority,category_id,due_date)"
                            " VALUES (?,?,?,?,?,?)",
                            (d["title"], d["description"], d["status"], d["priority"],
                             d["category_id"], d["due_date"]))
        except sqlite3.IntegrityError:
            return bad()
        tid = cur.lastrowid
    d["id"] = tid
    return js({"id": tid, "title": d["title"], "description": d["description"], "status": d["status"],
               "priority": d["priority"], "category_id": d["category_id"],
               "due_date": d["due_date"]}, 201)


@app.get("/tasks")
def list_tasks():
    page, size = pint("page", 1), pint("size", 20)
    if page is None or size is None or page < 1 or size < 1:
        return bad()
    size = min(size, 100)
    with db() as c:
        total = c.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        rows = c.execute(f"SELECT {COLS} FROM tasks ORDER BY id LIMIT ? OFFSET ?",
                         (size, (page - 1) * size)).fetchall()
    return js({"page": page, "size": size, "total": total, "items": [task(r) for r in rows]})


@app.get("/tasks/search")
def search_tasks():
    q = request.args.get("q", "")
    status = request.args.get("status")
    if q == "" or (status is not None and status not in STATUSES):
        return bad()
    like = "%" + q + "%"
    with db() as c:
        if status is None:
            total = c.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE ?", (like,)).fetchone()[0]
            rows = c.execute(f"SELECT {COLS} FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 20",
                             (like,)).fetchall()
        else:
            total = c.execute("SELECT COUNT(*) FROM tasks WHERE title LIKE ? AND status=?",
                              (like, status)).fetchone()[0]
            rows = c.execute(f"SELECT {COLS} FROM tasks WHERE title LIKE ? AND status=?"
                             " ORDER BY id LIMIT 20", (like, status)).fetchall()
    return js({"total": total, "items": [task(r) for r in rows]})


@app.get("/tasks/<int:tid>")
def get_task(tid):
    with db() as c:
        r = c.execute(f"SELECT {COLS} FROM tasks WHERE id=?", (tid,)).fetchone()
    return js(task(r)) if r else notfound()


@app.put("/tasks/<int:tid>")
def update_task(tid):
    d = request.get_json(silent=True, force=True)
    if not (isinstance(d, dict) and d.get("status") in STATUSES):
        return bad()
    with db() as c:
        c.execute("UPDATE tasks SET status=? WHERE id=?", (d["status"], tid))
        r = c.execute(f"SELECT {COLS} FROM tasks WHERE id=?", (tid,)).fetchone()
    return js(task(r)) if r else notfound()


@app.delete("/tasks/<int:tid>")
def delete_task(tid):
    with db() as c:
        cur = c.execute("DELETE FROM tasks WHERE id=?", (tid,))
        n = cur.rowcount
    return Response(status=204) if n else notfound()


@app.get("/categories/<int:cid>/tasks")
def category_tasks(cid):
    limit = pint("limit", 20)
    if limit is None or limit < 1:
        return bad()
    limit = min(limit, 100)
    with db() as c:
        cat = c.execute("SELECT id,name FROM categories WHERE id=?", (cid,)).fetchone()
        if not cat:
            return notfound()
        rows = c.execute("SELECT t.id,t.title,t.description,t.status,t.priority,t.category_id,"
                         "t.due_date,c.name FROM tasks t JOIN categories c ON c.id=t.category_id"
                         " WHERE c.id=? ORDER BY t.id LIMIT ?", (cid, limit)).fetchall()
    items = []
    for r in rows:
        t = task(r)
        t["category_name"] = r[7]
        items.append(t)
    return js({"category": {"id": cat[0], "name": cat[1]}, "count": len(items), "items": items})


@app.get("/stats")
def stats():
    with db() as c:
        by_status = {s: 0 for s in STATUSES}
        for st, n in c.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status"):
            by_status[st] = n
        cats = c.execute("SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status='done'),0)"
                         " FROM categories c LEFT JOIN tasks t ON t.category_id=c.id"
                         " GROUP BY c.id ORDER BY c.id").fetchall()
    return js({"total": sum(by_status.values()), "by_status": by_status,
               "by_category": [{"id": r[0], "name": r[1], "total": r[2], "done": r[3]} for r in cats]})


if __name__ == "__main__":
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    WSGIRequestHandler.protocol_version = "HTTP/1.1"
    app.run(host="127.0.0.1", port=PORT, threaded=True, use_reloader=False)
