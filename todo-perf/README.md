# TP2 — Performance d'une API de gestion de tâches (Rust, Go, PHP, Node.js, Python)

- `docs/cahier_des_charges.md` : API commune, schéma, règles.
- `python/ node/ php/ go/ rust/` : les 5 versions. Build : `cd go && go build -o todo-go .` ; `cd rust && cargo build --release --offline`.
- `seed/seed.py` : base de départ (`python3 seed/seed.py data/seed.db 10000`).
- `loadgen/` : générateur de charge (Go) ; `go build -o loadgen .`
- `scripts/equivalence_test.py` : test d'équivalence ; `scripts/run_benchmark.py` : campagne (2250 essais, ~40 min) ;
  `scripts/analyze.py` : statistiques, graphiques ; `scripts/control_sqlite.sh` : contrôle SQLite.
- `data/raw/results.csv` : données brutes (1 ligne par essai) ; `data/summary.csv`, `data/stats.txt` : synthèses.
- `report/` : source LaTeX (`pdflatex main.tex` deux fois) et figures.
Prérequis : python3 (flask, psutil, pandas, matplotlib, scipy), node 22, php 8 (pdo_sqlite), go, rust, libsqlite3-dev.
Remarque : le noyau de mesure épingle le serveur sur le cœur 0 et le générateur sur le cœur 1 (machine à ≥ 2 cœurs).
