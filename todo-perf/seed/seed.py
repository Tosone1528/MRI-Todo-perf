#!/usr/bin/env python3
"""Génère la base SQLite de départ, identique pour toutes les versions.

Usage : python3 seed.py [chemin_sortie] [nombre_de_taches]
La génération est déterministe (graine fixe) : deux exécutions donnent le même contenu.
"""
import random
import sqlite3
import sys
from datetime import date, timedelta

OUT = sys.argv[1] if len(sys.argv) > 1 else "seed.db"
N_TASKS = int(sys.argv[2]) if len(sys.argv) > 2 else 10000

CATEGORIES = ["Travail", "Etudes", "Maison", "Sante", "Finances", "Courses", "Projets",
              "Loisirs", "Famille", "Voyages", "Sport", "Lecture", "Admin", "Reunions",
              "Code", "Reseau", "Cuisine", "Jardin", "Auto", "Divers"]
VERBS = ["Preparer", "Envoyer", "Corriger", "Reviser", "Appeler", "Acheter", "Planifier",
         "Verifier", "Ecrire", "Classer"]
NOUNS = ["rapport", "budget", "contrat", "facture", "projet", "serveur", "cours", "examen",
         "message", "dossier", "tableau", "calendrier"]
ADJS = ["urgent", "mensuel", "final", "partage", "prioritaire", "ancien", "nouveau", "simple"]
FILL = ["avant la fin de la semaine", "avec toute l'equipe", "pour le client", "selon les consignes",
        "en suivant la procedure", "puis archiver le resultat", "sans oublier les details",
        "et informer le responsable"]

SCHEMA = """
CREATE TABLE categories (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
CREATE TABLE tasks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  title       TEXT NOT NULL,
  description TEXT NOT NULL,
  status      TEXT NOT NULL CHECK (status IN ('todo','doing','done')),
  priority    INTEGER NOT NULL CHECK (priority BETWEEN 1 AND 3),
  category_id INTEGER NOT NULL REFERENCES categories(id),
  due_date    TEXT NOT NULL
);
CREATE INDEX idx_tasks_status   ON tasks(status);
CREATE INDEX idx_tasks_category ON tasks(category_id);
"""


def main():
    rnd = random.Random(42)
    con = sqlite3.connect(OUT)
    con.executescript("PRAGMA journal_mode=DELETE;" + SCHEMA)
    con.executemany("INSERT INTO categories(id,name) VALUES (?,?)",
                    [(i + 1, n) for i, n in enumerate(CATEGORIES)])
    base = date(2026, 1, 1)
    rows = []
    for _ in range(N_TASKS):
        title = f"{rnd.choice(VERBS)} {rnd.choice(NOUNS)} {rnd.choice(ADJS)}"
        desc = f"{title} {rnd.choice(FILL)} {rnd.choice(FILL)}"
        status = rnd.choices(["todo", "doing", "done"], weights=[40, 20, 40])[0]
        rows.append((title, desc, status, rnd.randint(1, 3), rnd.randint(1, len(CATEGORIES)),
                     (base + timedelta(days=rnd.randint(0, 364))).isoformat()))
    con.executemany("INSERT INTO tasks(title,description,status,priority,category_id,due_date)"
                    " VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.execute("PRAGMA journal_mode=WAL")
    con.commit()
    con.execute("VACUUM")
    con.close()
    print(f"{OUT}: {len(CATEGORIES)} categories, {N_TASKS} taches")


if __name__ == "__main__":
    main()
