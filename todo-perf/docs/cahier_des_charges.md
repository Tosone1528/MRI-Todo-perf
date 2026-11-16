# Cahier des charges — Gestionnaire de tâches (API REST + SQLite)

Toutes les versions (Python/Flask, Node.js, PHP, Go, Rust) doivent respecter exactement ce document.
Elles écoutent sur `127.0.0.1:$PORT`, lisent la base dans `$DB_PATH`, parlent HTTP/1.1 avec connexions persistantes
(keep-alive) et répondent en JSON (UTF-8).

## Données

- `categories(id, name)` : 20 catégories.
- `tasks(id, title, description, status, priority, category_id, due_date)` :
  - `status` ∈ {`todo`, `doing`, `done`} ; `priority` entier de 1 à 3 ;
  - `category_id` référence `categories(id)` (clé étrangère activée) ; `due_date` chaîne `AAAA-MM-JJ`.
- Index : `tasks(status)` et `tasks(category_id)`.
- Base de départ commune : 20 catégories, 10 000 tâches (`seed/seed.py`, graine fixe).
- Réglages SQLite identiques : `journal_mode=WAL`, `synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.
- Chaque version utilise 4 connexions SQLite (Node.js : une seule, car mono-thread).

## Objet « tâche » (JSON)

`{"id":int,"title":str,"description":str,"status":str,"priority":int,"category_id":int,"due_date":str}`

## Fonctionnalités (routes)

| # | Fonctionnalité | Route | Succès | Erreurs |
|---|----------------|-------|--------|---------|
| F1 | Créer une tâche (CRUD, écriture) | `POST /tasks` corps JSON : `title` (texte non vide, ≤ 200), `description` (texte), `status`, `priority`, `category_id` (entier existant), `due_date` (texte) | `201` + tâche créée | `400 {"error":"invalid input"}` |
| F2 | Lister avec pagination | `GET /tasks?page=P&size=S` (défaut 1 et 20 ; S plafonné à 100) | `200 {"page","size","total","items":[tâches triées par id]}` | `400` si P ou S n'est pas un entier ≥ 1 |
| F3 | Recherche et filtre | `GET /tasks/search?q=MOT&status=ST` (`status` facultatif) | `200 {"total":n,"items":[20 premières tâches triées par id]}` ; recherche : `title LIKE '%MOT%'` | `400` si `q` absent/vide ou `status` invalide |
| F4 | Tâches d'une catégorie (jointure) | `GET /categories/ID/tasks?limit=L` (défaut 20, plafonné à 100) | `200 {"category":{"id","name"},"count":n,"items":[{tâche + "category_name"}]}` | `404` catégorie inconnue ; `400` limit invalide |
| F5 | Statistiques (agrégation) | `GET /stats` | `200 {"total","by_status":{"todo","doing","done"},"by_category":[{"id","name","total","done"}]}` | — |
| — | Lire une tâche | `GET /tasks/ID` | `200` tâche | `404 {"error":"not found"}` |
| — | Changer le statut | `PUT /tasks/ID` corps `{"status":...}` | `200` tâche mise à jour | `400` (corps invalide, vérifié avant), `404` |
| — | Supprimer | `DELETE /tasks/ID` | `204` sans corps | `404` |

Toute autre route ou méthode : `404 {"error":"not found"}`.
Les nombres entiers de la requête (page, size, limit) ne contiennent que des chiffres ASCII.

## Données mesurées au chargement (F1 à F5)

Le test de charge appelle F1 à F5 (les routes « — » sont vérifiées par le test d'équivalence seulement).
