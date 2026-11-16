package main

/*
#cgo LDFLAGS: -lsqlite3
#include <sqlite3.h>
#include <stdlib.h>
static int bind_text(sqlite3_stmt *s, int i, const char *t, int n) {
	return sqlite3_bind_text(s, i, t, n, SQLITE_TRANSIENT);
}
*/
import "C"

import (
	"errors"
	"unsafe"
)

// Fine couche d'accès à SQLite via cgo (aucune dépendance externe : on lie libsqlite3 du système).

type DB struct{ p *C.sqlite3 }

var ErrConstraint = errors.New("constraint")

func openDB(path string) (*DB, error) {
	cp := C.CString(path)
	defer C.free(unsafe.Pointer(cp))
	var h *C.sqlite3
	if rc := C.sqlite3_open_v2(cp, &h, C.SQLITE_OPEN_READWRITE|C.SQLITE_OPEN_NOMUTEX, nil); rc != C.SQLITE_OK {
		return nil, errors.New("open failed")
	}
	d := &DB{p: h}
	for _, s := range []string{"PRAGMA journal_mode=WAL", "PRAGMA synchronous=NORMAL",
		"PRAGMA foreign_keys=ON", "PRAGMA busy_timeout=5000"} {
		if _, err := d.run(s); err != nil {
			return nil, err
		}
	}
	return d, nil
}

// run exécute une requête et renvoie toutes les lignes (valeurs int64, string ou nil).
func (d *DB) run(sql string, args ...interface{}) ([][]interface{}, error) {
	cs := C.CString(sql)
	defer C.free(unsafe.Pointer(cs))
	var st *C.sqlite3_stmt
	if rc := C.sqlite3_prepare_v2(d.p, cs, -1, &st, nil); rc != C.SQLITE_OK {
		return nil, errors.New("prepare failed")
	}
	defer C.sqlite3_finalize(st)
	for i, a := range args {
		switch v := a.(type) {
		case int:
			C.sqlite3_bind_int64(st, C.int(i+1), C.sqlite3_int64(v))
		case int64:
			C.sqlite3_bind_int64(st, C.int(i+1), C.sqlite3_int64(v))
		case string:
			ct := C.CString(v)
			C.bind_text(st, C.int(i+1), ct, C.int(len(v)))
			C.free(unsafe.Pointer(ct))
		}
	}
	var rows [][]interface{}
	ncol := int(C.sqlite3_column_count(st))
	for {
		rc := C.sqlite3_step(st)
		if rc == C.SQLITE_ROW {
			row := make([]interface{}, ncol)
			for j := 0; j < ncol; j++ {
				switch C.sqlite3_column_type(st, C.int(j)) {
				case C.SQLITE_INTEGER:
					row[j] = int64(C.sqlite3_column_int64(st, C.int(j)))
				case C.SQLITE_TEXT:
					n := int(C.sqlite3_column_bytes(st, C.int(j)))
					row[j] = C.GoStringN((*C.char)(unsafe.Pointer(C.sqlite3_column_text(st, C.int(j)))), C.int(n))
				}
			}
			rows = append(rows, row)
		} else if rc == C.SQLITE_DONE {
			return rows, nil
		} else if rc == C.SQLITE_CONSTRAINT || (rc&0xff) == C.SQLITE_CONSTRAINT {
			return nil, ErrConstraint
		} else {
			return nil, errors.New("step failed")
		}
	}
}

func (d *DB) lastInsertID() int64 { return int64(C.sqlite3_last_insert_rowid(d.p)) }
func (d *DB) changes() int64      { return int64(C.sqlite3_changes(d.p)) }
