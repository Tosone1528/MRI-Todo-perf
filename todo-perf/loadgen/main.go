package main

// Générateur de charge : N utilisateurs simultanés (connexions HTTP persistantes) envoient
// au total R requêtes d'une même fonctionnalité ; la latence de chaque requête est mesurée.
// Sortie : une ligne JSON (résumé de l'essai).

import (
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

var nouns = []string{"rapport", "budget", "contrat", "facture", "projet", "serveur", "cours", "examen", "message", "dossier", "tableau", "calendrier"}
var statuses = []string{"todo", "doing", "done"}

// build renvoie (méthode, URL relative, corps) de la i-ème requête de la fonctionnalité demandée.
func build(fn string, i int) (string, string, string) {
	switch fn {
	case "create":
		return "POST", "/tasks", fmt.Sprintf(
			`{"title":"Tache de charge %d","description":"Creee pendant le test de charge","status":"%s","priority":%d,"category_id":%d,"due_date":"2027-01-01"}`,
			i, statuses[i%3], 1+i%3, 1+i%20)
	case "list":
		return "GET", fmt.Sprintf("/tasks?page=%d&size=20", 1+(i*7)%400), ""
	case "search":
		u := "/tasks/search?q=" + nouns[i%len(nouns)]
		if i%2 == 1 {
			u += "&status=" + statuses[(i/2)%3]
		}
		return "GET", u, ""
	case "category":
		return "GET", fmt.Sprintf("/categories/%d/tasks?limit=20", 1+i%20), ""
	case "stats":
		return "GET", "/stats", ""
	}
	return "", "", ""
}

type result struct {
	Func      string  `json:"func"`
	Users     int     `json:"users"`
	Requests  int     `json:"requests"`
	Errors    int64   `json:"errors"`
	ElapsedS  float64 `json:"elapsed_s"`
	RPS       float64 `json:"rps"`
	MeanMs    float64 `json:"mean_ms"`
	P50Ms     float64 `json:"p50_ms"`
	P95Ms     float64 `json:"p95_ms"`
	P99Ms     float64 `json:"p99_ms"`
	MaxMs     float64 `json:"max_ms"`
	Bytes     int64   `json:"bytes"`
}

func pct(sorted []float64, p float64) float64 {
	idx := int(p*float64(len(sorted))+0.5) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(sorted) {
		idx = len(sorted) - 1
	}
	return sorted[idx]
}

func main() {
	base := flag.String("url", "http://127.0.0.1:8000", "URL de base")
	fn := flag.String("func", "list", "create|list|search|category|stats")
	users := flag.Int("c", 10, "utilisateurs simultanés")
	n := flag.Int("n", 1000, "nombre total de requêtes")
	offset := flag.Int("offset", 0, "décalage de l'indice des requêtes")
	flag.Parse()
	if m, _, _ := func() (string, string, string) { return build(*fn, 0) }(); m == "" {
		fmt.Fprintln(os.Stderr, "fonction inconnue")
		os.Exit(2)
	}

	tr := &http.Transport{MaxIdleConns: *users, MaxIdleConnsPerHost: *users, MaxConnsPerHost: *users,
		DisableCompression: true, IdleConnTimeout: 60 * time.Second}
	client := &http.Client{Transport: tr, Timeout: 30 * time.Second}

	lat := make([]float64, *n)
	var next, errs, bytes int64
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < *users; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				i := int(atomic.AddInt64(&next, 1)) - 1
				if i >= *n {
					return
				}
				method, rel, body := build(*fn, i+*offset)
				var rdr io.Reader
				if body != "" {
					rdr = strings.NewReader(body)
				}
				req, _ := http.NewRequest(method, *base+rel, rdr)
				if body != "" {
					req.Header.Set("Content-Type", "application/json")
				}
				t0 := time.Now()
				resp, err := client.Do(req)
				if err != nil {
					atomic.AddInt64(&errs, 1)
					lat[i] = float64(time.Since(t0).Microseconds()) / 1000
					continue
				}
				nb, _ := io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
				lat[i] = float64(time.Since(t0).Microseconds()) / 1000
				atomic.AddInt64(&bytes, nb)
				if resp.StatusCode < 200 || resp.StatusCode > 299 {
					atomic.AddInt64(&errs, 1)
				}
			}
		}()
	}
	wg.Wait()
	el := time.Since(start).Seconds()
	sort.Float64s(lat)
	sum := 0.0
	for _, v := range lat {
		sum += v
	}
	r := result{Func: *fn, Users: *users, Requests: *n, Errors: errs, ElapsedS: el, RPS: float64(*n) / el,
		MeanMs: sum / float64(*n), P50Ms: pct(lat, 0.50), P95Ms: pct(lat, 0.95), P99Ms: pct(lat, 0.99),
		MaxMs: lat[len(lat)-1], Bytes: bytes}
	json.NewEncoder(os.Stdout).Encode(r)
}
