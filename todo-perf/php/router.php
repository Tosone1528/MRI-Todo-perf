<?php
// Version PHP : serveur intégré (php -S, 4 processus) + PDO SQLite (connexion persistante).
const STATUSES = ['todo', 'doing', 'done'];
const COLS = 'id,title,description,status,priority,category_id,due_date';

function send(int $status, $obj = null): void {
    http_response_code($status);
    if ($obj !== null) {
        header('Content-Type: application/json');
        echo json_encode($obj, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    }
    exit;
}
function bad(): void { send(400, ['error' => 'invalid input']); }
function notFound(): void { send(404, ['error' => 'not found']); }
function toTask(array $r): array {
    return ['id' => (int)$r['id'], 'title' => $r['title'], 'description' => $r['description'],
        'status' => $r['status'], 'priority' => (int)$r['priority'],
        'category_id' => (int)$r['category_id'], 'due_date' => $r['due_date']];
}
function intParam(string $name, int $def): ?int {
    if (!isset($_GET[$name])) return $def;
    $v = $_GET[$name];
    return (is_string($v) && preg_match('/^[0-9]{1,9}$/', $v)) ? (int)$v : null;
}

$pdo = new PDO('sqlite:' . getenv('DB_PATH'), null, null, [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_PERSISTENT => true,
]);
$pdo->exec('PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000');

function all(PDO $pdo, string $sql, array $args = []): array {
    $st = $pdo->prepare($sql);
    $st->execute($args);
    return $st->fetchAll(PDO::FETCH_ASSOC);
}

$method = $_SERVER['REQUEST_METHOD'];
$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

if ($path === '/tasks' && $method === 'POST') {
    $d = json_decode(file_get_contents('php://input'), true);
    if (!(is_array($d)
        && isset($d['title']) && is_string($d['title']) && $d['title'] !== '' && mb_strlen($d['title']) <= 200
        && isset($d['description']) && is_string($d['description'])
        && isset($d['status']) && in_array($d['status'], STATUSES, true)
        && isset($d['priority']) && is_int($d['priority']) && $d['priority'] >= 1 && $d['priority'] <= 3
        && isset($d['category_id']) && is_int($d['category_id'])
        && isset($d['due_date']) && is_string($d['due_date']))) bad();
    try {
        $st = $pdo->prepare('INSERT INTO tasks(title,description,status,priority,category_id,due_date) VALUES (?,?,?,?,?,?)');
        $st->execute([$d['title'], $d['description'], $d['status'], $d['priority'], $d['category_id'], $d['due_date']]);
    } catch (PDOException $e) { bad(); }
    send(201, ['id' => (int)$pdo->lastInsertId(), 'title' => $d['title'], 'description' => $d['description'],
        'status' => $d['status'], 'priority' => $d['priority'], 'category_id' => $d['category_id'],
        'due_date' => $d['due_date']]);
}
if ($path === '/tasks' && $method === 'GET') {
    $page = intParam('page', 1); $size = intParam('size', 20);
    if ($page === null || $size === null || $page < 1 || $size < 1) bad();
    $size = min($size, 100);
    $total = (int)all($pdo, 'SELECT COUNT(*) AS n FROM tasks')[0]['n'];
    $rows = all($pdo, 'SELECT ' . COLS . ' FROM tasks ORDER BY id LIMIT ? OFFSET ?', [$size, ($page - 1) * $size]);
    send(200, ['page' => $page, 'size' => $size, 'total' => $total, 'items' => array_map('toTask', $rows)]);
}
if ($path === '/tasks/search' && $method === 'GET') {
    $word = $_GET['q'] ?? '';
    $status = $_GET['status'] ?? null;
    if (!is_string($word) || $word === '' || ($status !== null && !in_array($status, STATUSES, true))) bad();
    $like = '%' . $word . '%';
    if ($status === null) {
        $total = (int)all($pdo, 'SELECT COUNT(*) AS n FROM tasks WHERE title LIKE ?', [$like])[0]['n'];
        $rows = all($pdo, 'SELECT ' . COLS . ' FROM tasks WHERE title LIKE ? ORDER BY id LIMIT 20', [$like]);
    } else {
        $total = (int)all($pdo, 'SELECT COUNT(*) AS n FROM tasks WHERE title LIKE ? AND status=?', [$like, $status])[0]['n'];
        $rows = all($pdo, 'SELECT ' . COLS . ' FROM tasks WHERE title LIKE ? AND status=? ORDER BY id LIMIT 20', [$like, $status]);
    }
    send(200, ['total' => $total, 'items' => array_map('toTask', $rows)]);
}
if (preg_match('#^/tasks/([0-9]+)$#', $path, $g)) {
    $id = (int)$g[1];
    if ($method === 'GET') {
        $r = all($pdo, 'SELECT ' . COLS . ' FROM tasks WHERE id=?', [$id]);
        $r ? send(200, toTask($r[0])) : notFound();
    }
    if ($method === 'PUT') {
        $d = json_decode(file_get_contents('php://input'), true);
        if (!(is_array($d) && isset($d['status']) && in_array($d['status'], STATUSES, true))) bad();
        $pdo->prepare('UPDATE tasks SET status=? WHERE id=?')->execute([$d['status'], $id]);
        $r = all($pdo, 'SELECT ' . COLS . ' FROM tasks WHERE id=?', [$id]);
        $r ? send(200, toTask($r[0])) : notFound();
    }
    if ($method === 'DELETE') {
        $st = $pdo->prepare('DELETE FROM tasks WHERE id=?');
        $st->execute([$id]);
        $st->rowCount() ? send(204) : notFound();
    }
}
if (preg_match('#^/categories/([0-9]+)/tasks$#', $path, $g) && $method === 'GET') {
    $cid = (int)$g[1];
    $limit = intParam('limit', 20);
    if ($limit === null || $limit < 1) bad();
    $limit = min($limit, 100);
    $cat = all($pdo, 'SELECT id,name FROM categories WHERE id=?', [$cid]);
    if (!$cat) notFound();
    $rows = all($pdo, 'SELECT t.id,t.title,t.description,t.status,t.priority,t.category_id,t.due_date,'
        . 'c.name AS category_name FROM tasks t JOIN categories c ON c.id=t.category_id WHERE c.id=? ORDER BY t.id LIMIT ?',
        [$cid, $limit]);
    $items = array_map(function ($r) { $t = toTask($r); $t['category_name'] = $r['category_name']; return $t; }, $rows);
    send(200, ['category' => ['id' => (int)$cat[0]['id'], 'name' => $cat[0]['name']],
        'count' => count($items), 'items' => $items]);
}
if ($path === '/stats' && $method === 'GET') {
    $by = ['todo' => 0, 'doing' => 0, 'done' => 0];
    foreach (all($pdo, 'SELECT status,COUNT(*) AS n FROM tasks GROUP BY status') as $r) $by[$r['status']] = (int)$r['n'];
    $cats = array_map(fn($r) => ['id' => (int)$r['id'], 'name' => $r['name'], 'total' => (int)$r['total'], 'done' => (int)$r['done']],
        all($pdo, "SELECT c.id AS id,c.name AS name,COUNT(t.id) AS total,COALESCE(SUM(t.status='done'),0) AS done "
            . 'FROM categories c LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id'));
    send(200, ['total' => array_sum($by), 'by_status' => $by, 'by_category' => $cats]);
}
notFound();
