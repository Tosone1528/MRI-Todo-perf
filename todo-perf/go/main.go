package main

// Version Go : net/http de la bibliothèque standard + SQLite via cgo (4 connexions).

import (
	"encoding/json"
	"log"
	"math"
	"net/http"
	"os"
	"regexp"
	"strconv"
)

var statuses = map[string]bool{"todo": true, "doing": true, "done": true}

const cols = "id,title,description,status,priority,category_id,due_date"

var (
	pool   chan *DB
	intRe  = regexp.MustCompile(`^[0-9]{1,9}$`)
	taskRe = regexp.MustCompile(`^/tasks/([0-9]+)$`)
	catRe  = regexp.MustCompile(`^/categories/([0-9]+)/tasks$`)
)

type obj = map[string]interface{}

type CatStat struct {
	ID    int64  `json:"id"`
	Name  string `json:"name"`
	Total int64  `json:"total"`
	Done  int64  `json:"done"`
}

func send(w http.ResponseWriter, status int, v interface{}) {
	if v == nil {
		w.WriteHeader(status)
		return
	}
	b, _ := json.Marshal(v)
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Content-Length", strconv.Itoa(len(b)))
	w.WriteHeader(status)
	w.Write(b)
}
func bad(w http.ResponseWriter)      { send(w, 400, obj{"error": "invalid input"}) }
func notFound(w http.ResponseWriter) { send(w, 404, obj{"error": "not found"}) }

// Task : structure typée (encodage JSON plus rapide qu'avec des map).
type Task struct {
	ID          int64  `json:"id"`
	Title       string `json:"title"`
	Description string `json:"description"`
	Status      string `json:"status"`
	Priority    int64  `json:"priority"`
	CategoryID  int64  `json:"category_id"`
	DueDate     string `json:"due_date"`
}
type TaskWithCat struct {
	Task
	CategoryName string `json:"category_name"`
}

func toTask(r []interface{}) Task {
	return Task{r[0].(int64), r[1].(string), r[2].(string), r[3].(string), r[4].(int64), r[5].(int64), r[6].(string)}
}
func toTasks(rows [][]interface{}) []Task {
	out := make([]Task, 0, len(rows))
	for _, r := range rows {
		out = append(out, toTask(r))
	}
	return out
}

// intParam : (valeur, ok). Absent -> défaut ; non entier -> ok=false.
func intParam(q map[string][]string, name string, def int) (int, bool) {
	v, present := q[name]
	if !present {
		return def, true
	}
	if !intRe.MatchString(v[0]) {
		return 0, false
	}
	n, _ := strconv.Atoi(v[0])
	return n, true
}

func asInt(v interface{}) (int, bool) {
	f, ok := v.(float64)
	if !ok || f != math.Trunc(f) || math.Abs(f) > 1e15 {
		return 0, false
	}
	return int(f), true
}

func decodeObject(r *http.Request) map[string]interface{} {
	var d map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&d); err != nil {
		return nil
	}
	return d
}

func handler(w http.ResponseWriter, r *http.Request) {
	path, m, q := r.URL.Path, r.Method, r.URL.Query()
	db := <-pool
	defer func() { pool <- db }()

	switch {
	case path == "/tasks" && m == "POST":
		d := decodeObject(r)
		title, ok1 := d["title"].(string)
		desc, ok2 := d["description"].(string)
		status, ok3 := d["status"].(string)
		prio, ok4 := asInt(d["priority"])
		cat, ok5 := asInt(d["category_id"])
		due, ok6 := d["due_date"].(string)
		if d == nil || !ok1 || !ok2 || !ok3 || !ok4 || !ok5 || !ok6 || title == "" ||
			len([]rune(title)) > 200 || !statuses[status] || prio < 1 || prio > 3 {
			bad(w)
			return
		}
		if _, err := db.run("INSERT INTO tasks(title,description,status,priority,category_id,due_date) VALUES (?,?,?,?,?,?)",
			title, desc, status, prio, cat, due); err != nil {
			bad(w)
			return
		}
		send(w, 201, obj{"id": db.lastInsertID(), "title": title, "description": desc, "status": status,
			"priority": prio, "category_id": cat, "due_date": due})

	case path == "/tasks" && m == "GET":
		page, ok1 := intParam(q, "page", 1)
		size, ok2 := intParam(q, "size", 20)
		if !ok1 || !ok2 || page < 1 || size < 1 {
			bad(w)
			return
		}
		if size > 100 {
			size = 100
		}
		tot, _ := db.run("SELECT COUNT(*) FROM tasks")
		rows, _ := db.run("SELECT "+cols+" FROM tasks ORDER BY id LIMIT ? OFFSET ?", size, (page-1)*size)
		send(w, 200, obj{"page": page, "size": size, "total": tot[0][0], "items": toTasks(rows)})

	case path == "/tasks/search" && m == "GET":
		word := q.Get("q")
		status, hasStatus := q["status"]
		if word == "" || (hasStatus && !statuses[status[0]]) {
			bad(w)
			return
		}
		like := "%" + word + "%"
		var tot, rows [][]interface{}
		if !hasStatus {
			tot, _ = db.run("SELECT COUNT(*) FROM tasks WHERE title LIKE ?", like)
			rows, _ = db.run("SELECT "+cols+" FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 20", like)
		} else {
			tot, _ = db.run("SELECT COUNT(*) FROM tasks WHERE title LIKE ? AND status=?", like, status[0])
			rows, _ = db.run("SELECT "+cols+" FROM tasks WHERE title LIKE ? AND status=? ORDER BY id LIMIT 20", like, status[0])
		}
		send(w, 200, obj{"total": tot[0][0], "items": toTasks(rows)})

	case taskRe.MatchString(path) && (m == "GET" || m == "PUT" || m == "DELETE"):
		id, _ := strconv.Atoi(taskRe.FindStringSubmatch(path)[1])
		switch m {
		case "GET":
			rows, _ := db.run("SELECT "+cols+" FROM tasks WHERE id=?", id)
			if len(rows) == 0 {
				notFound(w)
				return
			}
			send(w, 200, toTask(rows[0]))
		case "PUT":
			d := decodeObject(r)
			status, ok := d["status"].(string)
			if d == nil || !ok || !statuses[status] {
				bad(w)
				return
			}
			db.run("UPDATE tasks SET status=? WHERE id=?", status, id)
			rows, _ := db.run("SELECT "+cols+" FROM tasks WHERE id=?", id)
			if len(rows) == 0 {
				notFound(w)
				return
			}
			send(w, 200, toTask(rows[0]))
		case "DELETE":
			db.run("DELETE FROM tasks WHERE id=?", id)
			if db.changes() == 0 {
				notFound(w)
				return
			}
			send(w, 204, nil)
		}

	case catRe.MatchString(path) && m == "GET":
		cid, _ := strconv.Atoi(catRe.FindStringSubmatch(path)[1])
		limit, ok := intParam(q, "limit", 20)
		if !ok || limit < 1 {
			bad(w)
			return
		}
		if limit > 100 {
			limit = 100
		}
		cat, _ := db.run("SELECT id,name FROM categories WHERE id=?", cid)
		if len(cat) == 0 {
			notFound(w)
			return
		}
		rows, _ := db.run("SELECT t.id,t.title,t.description,t.status,t.priority,t.category_id,t.due_date,"+
			"c.name FROM tasks t JOIN categories c ON c.id=t.category_id WHERE c.id=? ORDER BY t.id LIMIT ?", cid, limit)
		items := make([]TaskWithCat, 0, len(rows))
		for _, r := range rows {
			items = append(items, TaskWithCat{toTask(r), r[7].(string)})
		}
		send(w, 200, obj{"category": obj{"id": cat[0][0], "name": cat[0][1]}, "count": len(items), "items": items})

	case path == "/stats" && m == "GET":
		by := obj{"todo": int64(0), "doing": int64(0), "done": int64(0)}
		var total int64
		rows, _ := db.run("SELECT status,COUNT(*) FROM tasks GROUP BY status")
		for _, r := range rows {
			by[r[0].(string)] = r[1]
			total += r[1].(int64)
		}
		crow, _ := db.run("SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status='done'),0) FROM categories c " +
			"LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id")
		cats := make([]CatStat, 0, len(crow))
		for _, r := range crow {
			cats = append(cats, CatStat{r[0].(int64), r[1].(string), r[2].(int64), r[3].(int64)})
		}
		send(w, 200, obj{"total": total, "by_status": by, "by_category": cats})

	default:
		notFound(w)
	}
}

func main() {
	dbPath := os.Getenv("DB_PATH")
	if dbPath == "" {
		dbPath = "tasks.db"
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8000"
	}
	pool = make(chan *DB, 4)
	for i := 0; i < 4; i++ {
		d, err := openDB(dbPath)
		if err != nil {
			log.Fatal(err)
		}
		pool <- d
	}
	log.Fatal(http.ListenAndServe("127.0.0.1:"+port, http.HandlerFunc(handler)))
}
