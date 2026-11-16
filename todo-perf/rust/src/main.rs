// Version Rust : bibliothèque standard uniquement (serveur HTTP/1.1 minimal, un thread par connexion)
// + SQLite via FFI (libsqlite3 du système), 4 connexions en réserve.
use std::ffi::{c_char, c_int, c_void, CString};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::ptr;
use std::sync::{Arc, Condvar, Mutex};

#[link(name = "sqlite3")]
extern "C" {
    fn sqlite3_open_v2(f: *const c_char, db: *mut *mut c_void, flags: c_int, vfs: *const c_char) -> c_int;
    fn sqlite3_prepare_v2(db: *mut c_void, sql: *const c_char, n: c_int, st: *mut *mut c_void, tail: *mut *const c_char) -> c_int;
    fn sqlite3_step(st: *mut c_void) -> c_int;
    fn sqlite3_finalize(st: *mut c_void) -> c_int;
    fn sqlite3_bind_int64(st: *mut c_void, i: c_int, v: i64) -> c_int;
    fn sqlite3_bind_text(st: *mut c_void, i: c_int, t: *const c_char, n: c_int, d: isize) -> c_int;
    fn sqlite3_column_count(st: *mut c_void) -> c_int;
    fn sqlite3_column_type(st: *mut c_void, i: c_int) -> c_int;
    fn sqlite3_column_int64(st: *mut c_void, i: c_int) -> i64;
    fn sqlite3_column_text(st: *mut c_void, i: c_int) -> *const u8;
    fn sqlite3_column_bytes(st: *mut c_void, i: c_int) -> c_int;
    fn sqlite3_last_insert_rowid(db: *mut c_void) -> i64;
    fn sqlite3_changes(db: *mut c_void) -> c_int;
}

const SQLITE_ROW: c_int = 100;
const SQLITE_DONE: c_int = 101;
const SQLITE_INTEGER: c_int = 1;
const SQLITE_TEXT: c_int = 3;
const OPEN_RW_NOMUTEX: c_int = 0x2 | 0x8000;
const COLS: &str = "id,title,description,status,priority,category_id,due_date";

// ---------- SQLite ----------
struct Db(*mut c_void);
unsafe impl Send for Db {}

#[derive(Clone, Debug)]
enum Val {
    Int(i64),
    Text(String),
    Null,
}
impl Val {
    fn i(&self) -> i64 { if let Val::Int(n) = self { *n } else { 0 } }
    fn s(&self) -> String { if let Val::Text(t) = self { t.clone() } else { String::new() } }
}
enum Arg<'a> {
    I(i64),
    S(&'a str),
}

impl Db {
    fn open(path: &str) -> Db {
        let cp = CString::new(path).unwrap();
        let mut h: *mut c_void = ptr::null_mut();
        let rc = unsafe { sqlite3_open_v2(cp.as_ptr(), &mut h, OPEN_RW_NOMUTEX, ptr::null()) };
        assert!(rc == 0, "open failed");
        let d = Db(h);
        for p in ["PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL", "PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000"] {
            d.run(p, &[]).expect("pragma");
        }
        d
    }

    fn run(&self, sql: &str, args: &[Arg]) -> Result<Vec<Vec<Val>>, c_int> {
        let cs = CString::new(sql).unwrap();
        let mut st: *mut c_void = ptr::null_mut();
        let rc = unsafe { sqlite3_prepare_v2(self.0, cs.as_ptr(), -1, &mut st, ptr::null_mut()) };
        if rc != 0 { return Err(rc); }
        for (k, a) in args.iter().enumerate() {
            let i = (k + 1) as c_int;
            unsafe {
                match a {
                    Arg::I(v) => { sqlite3_bind_int64(st, i, *v); }
                    // SQLITE_TRANSIENT = -1 : SQLite copie le texte.
                    Arg::S(t) => { sqlite3_bind_text(st, i, t.as_ptr() as *const c_char, t.len() as c_int, -1); }
                }
            }
        }
        let ncol = unsafe { sqlite3_column_count(st) };
        let mut rows = Vec::new();
        loop {
            let rc = unsafe { sqlite3_step(st) };
            if rc == SQLITE_ROW {
                let mut row = Vec::with_capacity(ncol as usize);
                for j in 0..ncol {
                    unsafe {
                        match sqlite3_column_type(st, j) {
                            SQLITE_INTEGER => row.push(Val::Int(sqlite3_column_int64(st, j))),
                            SQLITE_TEXT => {
                                let n = sqlite3_column_bytes(st, j) as usize;
                                let p = sqlite3_column_text(st, j);
                                let b = std::slice::from_raw_parts(p, n);
                                row.push(Val::Text(String::from_utf8_lossy(b).into_owned()));
                            }
                            _ => row.push(Val::Null),
                        }
                    }
                }
                rows.push(row);
            } else if rc == SQLITE_DONE {
                unsafe { sqlite3_finalize(st) };
                return Ok(rows);
            } else {
                unsafe { sqlite3_finalize(st) };
                return Err(rc);
            }
        }
    }
    fn last_id(&self) -> i64 { unsafe { sqlite3_last_insert_rowid(self.0) } }
    fn changes(&self) -> i32 { unsafe { sqlite3_changes(self.0) } }
}

struct Pool {
    conns: Mutex<Vec<Db>>,
    cv: Condvar,
}
impl Pool {
    fn get(&self) -> Db {
        let mut g = self.conns.lock().unwrap();
        loop {
            if let Some(d) = g.pop() { return d; }
            g = self.cv.wait(g).unwrap();
        }
    }
    fn put(&self, d: Db) {
        self.conns.lock().unwrap().push(d);
        self.cv.notify_one();
    }
}

// ---------- JSON ----------
enum J {
    Str(String),
    Int(i64),
    Arr(Vec<J>),
    Obj(Vec<(&'static str, J)>),
}
fn esc(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}
impl J {
    fn write(&self, out: &mut String) {
        match self {
            J::Str(s) => esc(s, out),
            J::Int(n) => out.push_str(&n.to_string()),
            J::Arr(a) => {
                out.push('[');
                for (i, x) in a.iter().enumerate() {
                    if i > 0 { out.push(','); }
                    x.write(out);
                }
                out.push(']');
            }
            J::Obj(o) => {
                out.push('{');
                for (i, (k, v)) in o.iter().enumerate() {
                    if i > 0 { out.push(','); }
                    esc(k, out);
                    out.push(':');
                    v.write(out);
                }
                out.push('}');
            }
        }
    }
    fn render(&self) -> String { let mut s = String::new(); self.write(&mut s); s }
}

#[derive(Debug)]
enum P {
    Str(String),
    Int(i64),
    Other,
    Obj(Vec<(String, P)>),
}
struct Parser<'a> { b: &'a [u8], i: usize }
impl<'a> Parser<'a> {
    fn ws(&mut self) { while self.i < self.b.len() && matches!(self.b[self.i], b' ' | b'\t' | b'\n' | b'\r') { self.i += 1; } }
    fn lit(&mut self, w: &str) -> Option<P> {
        if self.b[self.i..].starts_with(w.as_bytes()) { self.i += w.len(); Some(P::Other) } else { None }
    }
    fn hex4(&mut self) -> Option<u32> {
        if self.i + 4 > self.b.len() { return None; }
        let s = std::str::from_utf8(&self.b[self.i..self.i + 4]).ok()?;
        self.i += 4;
        u32::from_str_radix(s, 16).ok()
    }
    fn string(&mut self) -> Option<String> {
        if self.b.get(self.i) != Some(&b'"') { return None; }
        self.i += 1;
        let mut buf: Vec<u8> = Vec::new();
        loop {
            let c = *self.b.get(self.i)?;
            self.i += 1;
            match c {
                b'"' => return String::from_utf8(buf).ok(),
                b'\\' => {
                    let e = *self.b.get(self.i)?;
                    self.i += 1;
                    let ch = match e {
                        b'"' => '"', b'\\' => '\\', b'/' => '/', b'b' => '\u{8}', b'f' => '\u{c}',
                        b'n' => '\n', b'r' => '\r', b't' => '\t',
                        b'u' => {
                            let mut u = self.hex4()?;
                            if (0xD800..0xDC00).contains(&u) {
                                if self.b.get(self.i) == Some(&b'\\') && self.b.get(self.i + 1) == Some(&b'u') {
                                    self.i += 2;
                                    let lo = self.hex4()?;
                                    u = 0x10000 + ((u - 0xD800) << 10) + (lo.wrapping_sub(0xDC00));
                                } else { return None; }
                            }
                            char::from_u32(u)?
                        }
                        _ => return None,
                    };
                    let mut t = [0u8; 4];
                    buf.extend_from_slice(ch.encode_utf8(&mut t).as_bytes());
                }
                c if c < 0x20 => return None,
                c => buf.push(c),
            }
        }
    }
    fn value(&mut self) -> Option<P> {
        self.ws();
        match *self.b.get(self.i)? {
            b'"' => Some(P::Str(self.string()?)),
            b'{' => {
                self.i += 1;
                let mut items = Vec::new();
                self.ws();
                if self.b.get(self.i) == Some(&b'}') { self.i += 1; return Some(P::Obj(items)); }
                loop {
                    self.ws();
                    let k = self.string()?;
                    self.ws();
                    if self.b.get(self.i) != Some(&b':') { return None; }
                    self.i += 1;
                    let v = self.value()?;
                    items.push((k, v));
                    self.ws();
                    match self.b.get(self.i)? {
                        b',' => self.i += 1,
                        b'}' => { self.i += 1; return Some(P::Obj(items)); }
                        _ => return None,
                    }
                }
            }
            b'[' => {
                self.i += 1;
                self.ws();
                if self.b.get(self.i) == Some(&b']') { self.i += 1; return Some(P::Other); }
                loop {
                    self.value()?;
                    self.ws();
                    match self.b.get(self.i)? {
                        b',' => self.i += 1,
                        b']' => { self.i += 1; return Some(P::Other); }
                        _ => return None,
                    }
                }
            }
            b't' => self.lit("true"),
            b'f' => self.lit("false"),
            b'n' => self.lit("null"),
            b'-' | b'0'..=b'9' => {
                let st = self.i;
                if self.b[self.i] == b'-' { self.i += 1; }
                let ds = self.i;
                while self.i < self.b.len() && self.b[self.i].is_ascii_digit() { self.i += 1; }
                if self.i == ds { return None; }
                let mut float = false;
                if self.b.get(self.i) == Some(&b'.') {
                    float = true; self.i += 1;
                    while self.i < self.b.len() && self.b[self.i].is_ascii_digit() { self.i += 1; }
                }
                if matches!(self.b.get(self.i), Some(b'e') | Some(b'E')) {
                    float = true; self.i += 1;
                    if matches!(self.b.get(self.i), Some(b'+') | Some(b'-')) { self.i += 1; }
                    while self.i < self.b.len() && self.b[self.i].is_ascii_digit() { self.i += 1; }
                }
                let t = std::str::from_utf8(&self.b[st..self.i]).ok()?;
                if float { return Some(P::Other); }
                Some(t.parse::<i64>().map(P::Int).unwrap_or(P::Other))
            }
            _ => None,
        }
    }
}
fn parse_obj(body: &[u8]) -> Option<Vec<(String, P)>> {
    let mut p = Parser { b: body, i: 0 };
    let v = p.value()?;
    p.ws();
    if p.i != body.len() { return None; }
    if let P::Obj(o) = v { Some(o) } else { None }
}
fn field<'a>(o: &'a [(String, P)], k: &str) -> Option<&'a P> {
    o.iter().find(|(n, _)| n == k).map(|(_, v)| v)
}

// ---------- HTTP ----------
struct Req { method: String, path: String, query: Vec<(String, String)>, body: Vec<u8>, close: bool }
struct Resp { status: u16, body: Option<String> }

fn pct_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => { out.push(b' '); i += 1; }
            b'%' if i + 2 < b.len() => {
                let hex = std::str::from_utf8(&b[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok());
                if let Some(v) = hex { out.push(v); i += 3; } else { out.push(b'%'); i += 1; }
            }
            c => { out.push(c); i += 1; }
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}
fn parse_query(q: &str) -> Vec<(String, String)> {
    q.split('&').filter(|p| !p.is_empty()).map(|p| match p.split_once('=') {
        Some((k, v)) => (pct_decode(k), pct_decode(v)),
        None => (pct_decode(p), String::new()),
    }).collect()
}
fn qget<'a>(q: &'a [(String, String)], k: &str) -> Option<&'a str> {
    q.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str())
}
fn int_param(q: &[(String, String)], k: &str, def: i64) -> Option<i64> {
    match qget(q, k) {
        None => Some(def),
        Some(v) if !v.is_empty() && v.len() <= 9 && v.bytes().all(|c| c.is_ascii_digit()) => v.parse().ok(),
        _ => None,
    }
}

fn read_request(r: &mut BufReader<TcpStream>) -> Option<Req> {
    let mut line = String::new();
    if r.read_line(&mut line).ok()? == 0 { return None; }
    let mut parts = line.trim_end().split(' ');
    let method = parts.next()?.to_string();
    let target = parts.next()?.to_string();
    let mut clen = 0usize;
    let mut close = false;
    loop {
        let mut h = String::new();
        if r.read_line(&mut h).ok()? == 0 { return None; }
        let h = h.trim_end();
        if h.is_empty() { break; }
        if let Some((k, v)) = h.split_once(':') {
            let k = k.to_ascii_lowercase();
            if k == "content-length" { clen = v.trim().parse().ok()?; }
            if k == "connection" && v.trim().eq_ignore_ascii_case("close") { close = true; }
        }
    }
    let mut body = vec![0u8; clen];
    r.read_exact(&mut body).ok()?;
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p.to_string(), parse_query(q)),
        None => (target, Vec::new()),
    };
    Some(Req { method, path, query, body, close })
}

fn status_text(s: u16) -> &'static str {
    match s { 200 => "OK", 201 => "Created", 204 => "No Content", 400 => "Bad Request", _ => "Not Found" }
}

fn json(status: u16, j: J) -> Resp { Resp { status, body: Some(j.render()) } }
fn bad() -> Resp { json(400, J::Obj(vec![("error", J::Str("invalid input".into()))])) }
fn not_found() -> Resp { json(404, J::Obj(vec![("error", J::Str("not found".into()))])) }

fn task_json(r: &[Val]) -> Vec<(&'static str, J)> {
    vec![("id", J::Int(r[0].i())), ("title", J::Str(r[1].s())), ("description", J::Str(r[2].s())),
         ("status", J::Str(r[3].s())), ("priority", J::Int(r[4].i())), ("category_id", J::Int(r[5].i())),
         ("due_date", J::Str(r[6].s()))]
}
fn valid_status(s: &str) -> bool { matches!(s, "todo" | "doing" | "done") }
fn digits(s: &str) -> Option<i64> {
    if !s.is_empty() && s.len() <= 18 && s.bytes().all(|c| c.is_ascii_digit()) { s.parse().ok() } else { None }
}

fn handle(db: &Db, req: &Req) -> Resp {
    let m = req.method.as_str();
    let path = req.path.as_str();
    let q = &req.query;
    let segs: Vec<&str> = path.split('/').collect(); // "" , a , b ...

    if path == "/tasks" && m == "POST" {
        let o = match parse_obj(&req.body) { Some(o) => o, None => return bad() };
        let title = match field(&o, "title") { Some(P::Str(s)) if !s.is_empty() && s.chars().count() <= 200 => s, _ => return bad() };
        let desc = match field(&o, "description") { Some(P::Str(s)) => s, _ => return bad() };
        let status = match field(&o, "status") { Some(P::Str(s)) if valid_status(s) => s, _ => return bad() };
        let prio = match field(&o, "priority") { Some(P::Int(n)) if (1..=3).contains(n) => *n, _ => return bad() };
        let cat = match field(&o, "category_id") { Some(P::Int(n)) => *n, _ => return bad() };
        let due = match field(&o, "due_date") { Some(P::Str(s)) => s, _ => return bad() };
        if db.run("INSERT INTO tasks(title,description,status,priority,category_id,due_date) VALUES (?,?,?,?,?,?)",
            &[Arg::S(title), Arg::S(desc), Arg::S(status), Arg::I(prio), Arg::I(cat), Arg::S(due)]).is_err() {
            return bad();
        }
        return json(201, J::Obj(vec![("id", J::Int(db.last_id())), ("title", J::Str(title.clone())),
            ("description", J::Str(desc.clone())), ("status", J::Str(status.clone())), ("priority", J::Int(prio)),
            ("category_id", J::Int(cat)), ("due_date", J::Str(due.clone()))]));
    }
    if path == "/tasks" && m == "GET" {
        let (page, size) = match (int_param(q, "page", 1), int_param(q, "size", 20)) {
            (Some(p), Some(s)) if p >= 1 && s >= 1 => (p, s.min(100)),
            _ => return bad(),
        };
        let total = db.run("SELECT COUNT(*) FROM tasks", &[]).unwrap()[0][0].i();
        let rows = db.run(&format!("SELECT {COLS} FROM tasks ORDER BY id LIMIT ? OFFSET ?"),
            &[Arg::I(size), Arg::I((page - 1) * size)]).unwrap();
        return json(200, J::Obj(vec![("page", J::Int(page)), ("size", J::Int(size)), ("total", J::Int(total)),
            ("items", J::Arr(rows.iter().map(|r| J::Obj(task_json(r))).collect()))]));
    }
    if path == "/tasks/search" && m == "GET" {
        let word = qget(q, "q").unwrap_or("");
        let status = qget(q, "status");
        if word.is_empty() || status.map_or(false, |s| !valid_status(s)) { return bad(); }
        let like = format!("%{}%", word);
        let (tot, rows) = match status {
            None => (
                db.run("SELECT COUNT(*) FROM tasks WHERE title LIKE ?", &[Arg::S(&like)]).unwrap(),
                db.run(&format!("SELECT {COLS} FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 20"), &[Arg::S(&like)]).unwrap()),
            Some(s) => (
                db.run("SELECT COUNT(*) FROM tasks WHERE title LIKE ? AND status=?", &[Arg::S(&like), Arg::S(s)]).unwrap(),
                db.run(&format!("SELECT {COLS} FROM tasks WHERE title LIKE ? AND status=? ORDER BY id LIMIT 20"),
                    &[Arg::S(&like), Arg::S(s)]).unwrap()),
        };
        return json(200, J::Obj(vec![("total", J::Int(tot[0][0].i())),
            ("items", J::Arr(rows.iter().map(|r| J::Obj(task_json(r))).collect()))]));
    }
    if segs.len() == 3 && segs[1] == "tasks" && digits(segs[2]).is_some() {
        let id = digits(segs[2]).unwrap();
        match m {
            "GET" => {
                let rows = db.run(&format!("SELECT {COLS} FROM tasks WHERE id=?"), &[Arg::I(id)]).unwrap();
                return if rows.is_empty() { not_found() } else { json(200, J::Obj(task_json(&rows[0]))) };
            }
            "PUT" => {
                let o = parse_obj(&req.body);
                let status = match o.as_ref().and_then(|o| field(o, "status")) {
                    Some(P::Str(s)) if valid_status(s) => s.clone(),
                    _ => return bad(),
                };
                let _ = db.run("UPDATE tasks SET status=? WHERE id=?", &[Arg::S(&status), Arg::I(id)]);
                let rows = db.run(&format!("SELECT {COLS} FROM tasks WHERE id=?"), &[Arg::I(id)]).unwrap();
                return if rows.is_empty() { not_found() } else { json(200, J::Obj(task_json(&rows[0]))) };
            }
            "DELETE" => {
                let _ = db.run("DELETE FROM tasks WHERE id=?", &[Arg::I(id)]);
                return if db.changes() == 0 { not_found() } else { Resp { status: 204, body: None } };
            }
            _ => {}
        }
    }
    if segs.len() == 4 && segs[1] == "categories" && segs[3] == "tasks" && m == "GET" {
        if let Some(cid) = digits(segs[2]) {
            let limit = match int_param(q, "limit", 20) { Some(l) if l >= 1 => l.min(100), _ => return bad() };
            let cat = db.run("SELECT id,name FROM categories WHERE id=?", &[Arg::I(cid)]).unwrap();
            if cat.is_empty() { return not_found(); }
            let rows = db.run("SELECT t.id,t.title,t.description,t.status,t.priority,t.category_id,t.due_date,c.name \
                FROM tasks t JOIN categories c ON c.id=t.category_id WHERE c.id=? ORDER BY t.id LIMIT ?",
                &[Arg::I(cid), Arg::I(limit)]).unwrap();
            let items: Vec<J> = rows.iter().map(|r| {
                let mut t = task_json(r);
                t.push(("category_name", J::Str(r[7].s())));
                J::Obj(t)
            }).collect();
            return json(200, J::Obj(vec![
                ("category", J::Obj(vec![("id", J::Int(cat[0][0].i())), ("name", J::Str(cat[0][1].s()))])),
                ("count", J::Int(items.len() as i64)), ("items", J::Arr(items))]));
        }
    }
    if path == "/stats" && m == "GET" {
        let (mut todo, mut doing, mut done) = (0i64, 0i64, 0i64);
        for r in db.run("SELECT status,COUNT(*) FROM tasks GROUP BY status", &[]).unwrap() {
            match r[0].s().as_str() { "todo" => todo = r[1].i(), "doing" => doing = r[1].i(), "done" => done = r[1].i(), _ => {} }
        }
        let cats = db.run("SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status='done'),0) FROM categories c \
            LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id", &[]).unwrap();
        return json(200, J::Obj(vec![("total", J::Int(todo + doing + done)),
            ("by_status", J::Obj(vec![("todo", J::Int(todo)), ("doing", J::Int(doing)), ("done", J::Int(done))])),
            ("by_category", J::Arr(cats.iter().map(|r| J::Obj(vec![("id", J::Int(r[0].i())), ("name", J::Str(r[1].s())),
                ("total", J::Int(r[2].i())), ("done", J::Int(r[3].i()))])).collect()))]));
    }
    not_found()
}

fn serve(stream: TcpStream, pool: Arc<Pool>) {
    let _ = stream.set_nodelay(true);
    let mut w = match stream.try_clone() { Ok(w) => w, Err(_) => return };
    let mut r = BufReader::new(stream);
    while let Some(req) = read_request(&mut r) {
        let db = pool.get();
        let resp = handle(&db, &req);
        pool.put(db);
        let body = resp.body.unwrap_or_default();
        let head = if resp.status == 204 {
            format!("HTTP/1.1 204 No Content\r\nConnection: {}\r\n\r\n", if req.close { "close" } else { "keep-alive" })
        } else {
            format!("HTTP/1.1 {} {}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: {}\r\n\r\n",
                resp.status, status_text(resp.status), body.len(), if req.close { "close" } else { "keep-alive" })
        };
        let mut out = head.into_bytes();
        out.extend_from_slice(body.as_bytes());
        if w.write_all(&out).is_err() || req.close { return; }
    }
}

fn main() {
    let db_path = std::env::var("DB_PATH").unwrap_or_else(|_| "tasks.db".into());
    let port = std::env::var("PORT").unwrap_or_else(|_| "8000".into());
    let pool = Arc::new(Pool { conns: Mutex::new((0..4).map(|_| Db::open(&db_path)).collect()), cv: Condvar::new() });
    let listener = TcpListener::bind(format!("127.0.0.1:{port}")).expect("bind");
    for s in listener.incoming().flatten() {
        let p = Arc::clone(&pool);
        let _ = std::thread::Builder::new().stack_size(256 * 1024).spawn(move || serve(s, p));
    }
}
