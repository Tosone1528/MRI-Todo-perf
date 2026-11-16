"""Définitions communes : technologies, démarrage/arrêt des serveurs."""
import os
import shutil
import signal
import subprocess
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_DB = os.path.join(ROOT, "data", "seed.db")
WORK_DIR = os.environ.get("TODO_WORK", "/tmp/todo-perf-work")

# nom -> (libellé, répertoire, fonction(port) -> commande, variables d'environnement supplémentaires)
TECHS = {
    "python": ("Python (Flask)", "python", lambda p: ["python3", "server.py"], {}),
    "node": ("Node.js (http)", "node",
             lambda p: ["node", "--disable-warning=ExperimentalWarning", "server.js"], {}),
    "php": ("PHP (php -S)", "php", lambda p: ["php", "-S", f"127.0.0.1:{p}", "router.php"],
            {"PHP_CLI_SERVER_WORKERS": "4"}),
    "go": ("Go (net/http)", "go", lambda p: ["./todo-go"], {}),
    "rust": ("Rust (std)", "rust", lambda p: ["./target/release/todo-rust"], {}),
}


def fresh_db(tag):
    """Copie la base de départ dans un fichier de travail neuf et renvoie son chemin."""
    os.makedirs(WORK_DIR, exist_ok=True)
    path = os.path.join(WORK_DIR, f"{tag}.db")
    for ext in ("", "-wal", "-shm"):
        try:
            os.remove(path + ext)
        except FileNotFoundError:
            pass
    shutil.copyfile(SEED_DB, path)
    return path


def start_server(tech, port, db_path, cpu=None):
    """Lance le serveur (éventuellement épinglé sur un cœur) ; renvoie (Popen, durée_démarrage_ms)."""
    _, subdir, cmdf, extra = TECHS[tech]
    cmd = cmdf(port)
    if cpu is not None:
        cmd = ["taskset", "-c", str(cpu)] + cmd
    env = dict(os.environ, PORT=str(port), DB_PATH=db_path, **extra)
    t0 = time.perf_counter()
    proc = subprocess.Popen(cmd, cwd=os.path.join(ROOT, subdir), env=env, start_new_session=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/categories/1/tasks?limit=1"
    deadline = t0 + 15
    while time.perf_counter() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{tech}: le serveur s'est arrêté au démarrage")
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                r.read()
                if r.status == 200:
                    return proc, (time.perf_counter() - t0) * 1000.0
        except Exception:
            time.sleep(0.002)
    proc.kill()
    raise RuntimeError(f"{tech}: démarrage trop long")


def stop_server(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass
