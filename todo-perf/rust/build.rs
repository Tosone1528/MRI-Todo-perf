fn main() {
    // Lie la bibliothèque SQLite du système (aucune caisse externe n'est nécessaire).
    println!("cargo:rustc-link-lib=sqlite3");
}
