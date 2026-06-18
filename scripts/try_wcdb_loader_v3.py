from pathlib import Path
#!/usr/bin/env python3
"""Track 2: dlopen roam_server + roam_migration with usb.framework DYLD paths."""
import ctypes
import ctypes.util
import os
import sqlite3
import sys
import traceback
from datetime import datetime, timezone

EXPORT = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
LOG_PATH = os.path.join(EXPORT, "try_wcdb_loader_v3.log")

WECHAT_DEBUG = "/Users/patrickchiho/Applications/WeChat-Debug.app"
WECHAT_RELEASE = "/Applications/WeChat.app"
FW_DEBUG = os.path.join(WECHAT_DEBUG, "Contents/Frameworks")
FW_RELEASE = os.path.join(WECHAT_RELEASE, "Contents/Frameworks")

DB_PATH = os.path.join(
    EXPORT,
    "db/Documents/48d99549ba8c5780b0908193c1fab6fd/DB/message_2.sqlite",
)
CHAT = "Chat_11d8637ec8a3730380f6691705f8a23c"
MES_ID = 4134
SQL = f'SELECT wcdb_decompress(Message) FROM "{CHAT}" WHERE MesLocalID={MES_ID}'

RTLD_NOW = 0x2
RTLD_GLOBAL = 0x8

_log_fh = None

def log(msg):
    global _log_fh
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


def find_usb_frameworks():
    found = []
    for label, fw in [("WeChat-Debug", FW_DEBUG), ("WeChat", FW_RELEASE)]:
        p = os.path.join(fw, "usb.framework")
        if os.path.isdir(p):
            found.append((label, p))
            log(f"usb.framework [{label}]: {p}")
        else:
            log(f"usb.framework [{label}]: MISSING {p}")
    return found


def build_dyld_paths(usb_entries):
    parts = [FW_DEBUG]
    for _, usb_path in usb_entries:
        parts.append(usb_path)
        parts.append(os.path.dirname(usb_path))
    parts.extend([
        os.path.join(FW_DEBUG, "roam_server.framework/Versions/A"),
        os.path.join(FW_DEBUG, "roam_migration.framework/Versions/A"),
    ])
    # dedupe preserve order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return os.pathsep.join(out)


def dlopen_chain(libdl, paths):
    handles = []
    for path in paths:
        if not os.path.exists(path):
            log(f"dlopen skip missing: {path}")
            continue
        h = libdl.dlopen(path.encode(), RTLD_NOW | RTLD_GLOBAL)
        if not h:
            err = libdl.dlerror()
            log(f"dlopen FAIL {path}: {err.decode() if err else '?'}")
        else:
            log(f"dlopen OK {path} handle=0x{h:x}")
            handles.append((path, h))
    return handles


def embedded_sqlite_query(libdl, mig_handle):
    DB = DB_PATH.encode()
    SQLB = SQL.encode()
    for sym in (b"sqlite3_open", b"sqlite3_prepare_v2", b"sqlite3_step", b"sqlite3_column_blob",
                b"sqlite3_column_bytes", b"sqlite3_finalize", b"sqlite3_close", b"sqlite3_errmsg"):
        p = libdl.dlsym(mig_handle, sym)
        if not p:
            log(f"dlsym missing {sym.decode()}")
            return False
    sqlite3_open = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))(
        libdl.dlsym(mig_handle, b"sqlite3_open")
    )
    sqlite3_prepare_v2 = ctypes.CFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_char_p),
    )(libdl.dlsym(mig_handle, b"sqlite3_prepare_v2"))
    sqlite3_step = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(libdl.dlsym(mig_handle, b"sqlite3_step"))
    sqlite3_column_blob = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)(
        libdl.dlsym(mig_handle, b"sqlite3_column_blob")
    )
    sqlite3_column_bytes = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)(
        libdl.dlsym(mig_handle, b"sqlite3_column_bytes")
    )
    sqlite3_finalize = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(libdl.dlsym(mig_handle, b"sqlite3_finalize"))
    sqlite3_close = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(libdl.dlsym(mig_handle, b"sqlite3_close"))
    sqlite3_errmsg = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p)(libdl.dlsym(mig_handle, b"sqlite3_errmsg"))

    db = ctypes.c_void_p()
    rc = sqlite3_open(DB, ctypes.byref(db))
    log(f"embedded sqlite3_open rc={rc}")
    if rc != 0:
        log(f"embedded open err: {sqlite3_errmsg(db)}")
        return False
    stmt = ctypes.c_void_p()
    tail = ctypes.c_char_p()
    rc = sqlite3_prepare_v2(db, SQLB, -1, ctypes.byref(stmt), ctypes.byref(tail))
    log(f"embedded prepare rc={rc} err={sqlite3_errmsg(db)}")
    ok = False
    if rc == 0 and stmt.value:
        rc = sqlite3_step(stmt)
        log(f"embedded step rc={rc}")
        if rc == 100:  # SQLITE_ROW
            blob = sqlite3_column_blob(stmt, 0)
            n = sqlite3_column_bytes(stmt, 0)
            if blob and n > 0:
                data = ctypes.string_at(blob, n)
                log(f"embedded wcdb_decompress row bytes={n}")
                try:
                    text = data[:800].decode("utf-8")
                except Exception:
                    text = repr(data[:200])
                log(f"preview: {text[:500]}")
                ok = ("笙歌" in text) or ("appmsg" in text)
        sqlite3_finalize(stmt)
    sqlite3_close(db)
    return ok


def python_sqlite_query(tag):
    uri = f"file:{DB_PATH}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
        row = con.execute(SQL).fetchone()
        con.close()
        val = row[0] if row else None
        if val is None:
            log(f"[{tag}] wcdb_decompress: NULL row")
            return False
        if isinstance(val, bytes):
            try:
                text = val.decode("utf-8")
            except Exception:
                text = ""
            log(f"[{tag}] wcdb_decompress bytes={len(val)} preview={val[:32].hex()}")
        else:
            text = str(val)
            log(f"[{tag}] wcdb_decompress type={type(val).__name__}")
        log(f"[{tag}] text_preview: {text[:400]}")
        return ("笙歌" in text) or ("appmsg" in text)
    except Exception as e:
        log(f"[{tag}] wcdb_decompress FAIL: {e}")
        return False


def main():
    global _log_fh
    with open(LOG_PATH, "w", encoding="utf-8") as _log_fh:
        log("=== try_wcdb_loader_v3.py ===")
        log(f"arch={os.uname().machine}")

        usb = find_usb_frameworks()
        dyld = build_dyld_paths(usb)
        os.environ["DYLD_FRAMEWORK_PATH"] = dyld
        os.environ["DYLD_LIBRARY_PATH"] = dyld
        log(f"DYLD_FRAMEWORK_PATH={dyld}")
        log(f"DYLD_LIBRARY_PATH={dyld}")

        libdl = ctypes.CDLL(ctypes.util.find_library("dl"))
        libdl.dlopen.argtypes = [ctypes.c_char_p, ctypes.c_int]
        libdl.dlopen.restype = ctypes.c_void_p
        libdl.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        libdl.dlsym.restype = ctypes.c_void_p
        libdl.dlerror.restype = ctypes.c_char_p

        roam_server = os.path.join(FW_DEBUG, "roam_server.framework/Versions/A/roam_server")
        roam_migration = os.path.join(FW_DEBUG, "roam_migration.framework/Versions/A/roam_migration")

        log("--- baseline python sqlite3 ---")
        base_ok = python_sqlite_query("baseline")

        handles = dlopen_chain(libdl, [roam_server, roam_migration])
        mig_h = next((h for p, h in handles if "roam_migration" in p), None)

        log("--- after dlopen python sqlite3 ---")
        after_ok = python_sqlite_query("after_dlopen")

        emb_ok = False
        if mig_h:
            log("--- embedded sqlite3 from roam_migration ---")
            emb_ok = embedded_sqlite_query(libdl, mig_h)

        log(f"RESULT baseline_ok={base_ok} after_dlopen_ok={after_ok} embedded_ok={emb_ok}")
        log(f"wcdb_decompress_success={after_ok or emb_ok}")
        return 0 if (after_ok or emb_ok) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        if _log_fh:
            traceback.print_exc(file=_log_fh)
        sys.exit(1)
