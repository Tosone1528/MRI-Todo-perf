'use strict';
// Version Node.js : module http natif + node:sqlite (SQLite intégré), un seul processus mono-thread.
const http = require('node:http');
const { DatabaseSync } = require('node:sqlite');

const DB_PATH = process.env.DB_PATH || 'tasks.db';
const PORT = parseInt(process.env.PORT || '8000', 10);
const STATUSES = ['todo', 'doing', 'done'];
const COLS = 'id,title,description,status,priority,category_id,due_date';

const db = new DatabaseSync(DB_PATH);
db.exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000');

const q = {
  insert: db.prepare('INSERT INTO tasks(title,description,status,priority,category_id,due_date) VALUES (?,?,?,?,?,?)'),
  count: db.prepare('SELECT COUNT(*) AS n FROM tasks'),
  list: db.prepare(`SELECT ${COLS} FROM tasks ORDER BY id LIMIT ? OFFSET ?`),
  countSearch: db.prepare('SELECT COUNT(*) AS n FROM tasks WHERE title LIKE ?'),
  search: db.prepare(`SELECT ${COLS} FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 20`),
  countSearchSt: db.prepare('SELECT COUNT(*) AS n FROM tasks WHERE title LIKE ? AND status=?'),
  searchSt: db.prepare(`SELECT ${COLS} FROM tasks WHERE title LIKE ? AND status=? ORDER BY id LIMIT 20`),
  get: db.prepare(`SELECT ${COLS} FROM tasks WHERE id=?`),
  update: db.prepare('UPDATE tasks SET status=? WHERE id=?'),
  del: db.prepare('DELETE FROM tasks WHERE id=?'),
  cat: db.prepare('SELECT id,name FROM categories WHERE id=?'),
  catTasks: db.prepare('SELECT t.id,t.title,t.description,t.status,t.priority,t.category_id,t.due_date,' +
    'c.name AS category_name FROM tasks t JOIN categories c ON c.id=t.category_id WHERE c.id=? ORDER BY t.id LIMIT ?'),
  byStatus: db.prepare('SELECT status,COUNT(*) AS n FROM tasks GROUP BY status'),
  byCat: db.prepare('SELECT c.id AS id,c.name AS name,COUNT(t.id) AS total,COALESCE(SUM(t.status=\'done\'),0) AS done ' +
    'FROM categories c LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id'),
};

const INT_RE = /^[0-9]{1,9}$/;
const isInt = (v) => typeof v === 'number' && Number.isInteger(v);

function send(res, status, obj) {
  if (obj === undefined) {
    res.writeHead(status);
    res.end();
    return;
  }
  const body = JSON.stringify(obj);
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
}
const bad = (res) => send(res, 400, { error: 'invalid input' });
const notFound = (res) => send(res, 404, { error: 'not found' });

const toTask = (r) => ({ id: r.id, title: r.title, description: r.description, status: r.status,
  priority: r.priority, category_id: r.category_id, due_date: r.due_date });

function intParam(params, name, def) {
  const v = params.get(name);
  if (v === null) return def;
  return INT_RE.test(v) ? parseInt(v, 10) : null;
}

function parseBody(body) {
  try { return JSON.parse(body); } catch (e) { return undefined; }
}

function handle(req, res, body) {
  const url = new URL(req.url, 'http://x');
  const path = url.pathname;
  const p = url.searchParams;
  const m = req.method;
  let g;

  if (path === '/tasks' && m === 'POST') {
    const d = parseBody(body);
    if (!(d && typeof d === 'object' && !Array.isArray(d)
      && typeof d.title === 'string' && d.title.length > 0 && d.title.length <= 200
      && typeof d.description === 'string' && STATUSES.includes(d.status)
      && isInt(d.priority) && d.priority >= 1 && d.priority <= 3
      && isInt(d.category_id) && typeof d.due_date === 'string')) return bad(res);
    let r;
    try {
      r = q.insert.run(d.title, d.description, d.status, d.priority, d.category_id, d.due_date);
    } catch (e) { return bad(res); }
    return send(res, 201, { id: Number(r.lastInsertRowid), title: d.title, description: d.description,
      status: d.status, priority: d.priority, category_id: d.category_id, due_date: d.due_date });
  }
  if (path === '/tasks' && m === 'GET') {
    let page = intParam(p, 'page', 1), size = intParam(p, 'size', 20);
    if (page === null || size === null || page < 1 || size < 1) return bad(res);
    size = Math.min(size, 100);
    const total = q.count.get().n;
    const rows = q.list.all(size, (page - 1) * size);
    return send(res, 200, { page, size, total, items: rows.map(toTask) });
  }
  if (path === '/tasks/search' && m === 'GET') {
    const word = p.get('q') || '';
    const status = p.get('status');
    if (word === '' || (status !== null && !STATUSES.includes(status))) return bad(res);
    const like = '%' + word + '%';
    let total, rows;
    if (status === null) { total = q.countSearch.get(like).n; rows = q.search.all(like); }
    else { total = q.countSearchSt.get(like, status).n; rows = q.searchSt.all(like, status); }
    return send(res, 200, { total, items: rows.map(toTask) });
  }
  if ((g = /^\/tasks\/([0-9]+)$/.exec(path))) {
    const id = parseInt(g[1], 10);
    if (m === 'GET') {
      const r = q.get.get(id);
      return r ? send(res, 200, toTask(r)) : notFound(res);
    }
    if (m === 'PUT') {
      const d = parseBody(body);
      if (!(d && typeof d === 'object' && !Array.isArray(d) && STATUSES.includes(d.status))) return bad(res);
      q.update.run(d.status, id);
      const r = q.get.get(id);
      return r ? send(res, 200, toTask(r)) : notFound(res);
    }
    if (m === 'DELETE') {
      return q.del.run(id).changes ? send(res, 204) : notFound(res);
    }
  }
  if ((g = /^\/categories\/([0-9]+)\/tasks$/.exec(path)) && m === 'GET') {
    const cid = parseInt(g[1], 10);
    let limit = intParam(p, 'limit', 20);
    if (limit === null || limit < 1) return bad(res);
    limit = Math.min(limit, 100);
    const cat = q.cat.get(cid);
    if (!cat) return notFound(res);
    const items = q.catTasks.all(cid, limit).map((r) => ({ ...toTask(r), category_name: r.category_name }));
    return send(res, 200, { category: { id: cat.id, name: cat.name }, count: items.length, items });
  }
  if (path === '/stats' && m === 'GET') {
    const by_status = { todo: 0, doing: 0, done: 0 };
    for (const r of q.byStatus.all()) by_status[r.status] = r.n;
    const by_category = q.byCat.all().map((r) => ({ id: r.id, name: r.name, total: r.total, done: r.done }));
    return send(res, 200, { total: by_status.todo + by_status.doing + by_status.done, by_status, by_category });
  }
  return notFound(res);
}

const server = http.createServer((req, res) => {
  if (req.method === 'POST' || req.method === 'PUT') {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => handle(req, res, Buffer.concat(chunks).toString('utf8')));
  } else {
    req.resume();
    handle(req, res, '');
  }
});
server.keepAliveTimeout = 120000;
server.listen(PORT, '127.0.0.1');
