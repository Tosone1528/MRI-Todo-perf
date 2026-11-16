#!/bin/bash
# Contrôle : temps brut des deux requêtes de F5 (statistiques), sans HTTP, dans chaque environnement.
# Usage : bash scripts/control_sqlite.sh  (résultat : data/control_sqlite.txt)
cd "$(dirname "$0")/.." || exit 1
cp data/seed.db /tmp/ctl.db
{
python3 - <<'PY'
import sqlite3,time
c=sqlite3.connect('/tmp/ctl.db')
q1="SELECT status,COUNT(*) FROM tasks GROUP BY status"
q2="SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status='done'),0) FROM categories c LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id"
for _ in range(20): c.execute(q1).fetchall(); c.execute(q2).fetchall()
t=time.perf_counter()
for _ in range(300): c.execute(q1).fetchall(); c.execute(q2).fetchall()
print("python",sqlite3.sqlite_version,round((time.perf_counter()-t)/300*1000,3),"ms/stats")
PY
node --disable-warning=ExperimentalWarning -e '
const {DatabaseSync}=require("node:sqlite");const db=new DatabaseSync("/tmp/ctl.db");
const a=db.prepare("SELECT status,COUNT(*) AS n FROM tasks GROUP BY status"),b=db.prepare("SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status=\x27done\x27),0) FROM categories c LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id");
for(let i=0;i<20;i++){a.all();b.all()}
const t=process.hrtime.bigint();for(let i=0;i<300;i++){a.all();b.all()}
console.log("node",process.versions.sqlite,(Number(process.hrtime.bigint()-t)/1e6/300).toFixed(3),"ms/stats")'
php -r '$p=new PDO("sqlite:/tmp/ctl.db");$q1="SELECT status,COUNT(*) FROM tasks GROUP BY status";$q2="SELECT c.id,c.name,COUNT(t.id),COALESCE(SUM(t.status=\x27done\x27),0) FROM categories c LEFT JOIN tasks t ON t.category_id=c.id GROUP BY c.id ORDER BY c.id";
for($i=0;$i<20;$i++){$p->query($q1)->fetchAll();$p->query($q2)->fetchAll();}
$t=microtime(true);for($i=0;$i<300;$i++){$p->query($q1)->fetchAll();$p->query($q2)->fetchAll();}
echo "php ",$p->query("select sqlite_version()")->fetchColumn()," ",round((microtime(true)-$t)/300*1000,3)," ms/stats\n";'
} | tee data/control_sqlite.txt
