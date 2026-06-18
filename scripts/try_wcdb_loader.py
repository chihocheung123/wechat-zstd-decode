#!/usr/bin/env python3
"""PoC: dlopen WeChat roam frameworks and attempt wcdb_decompress via system sqlite3."""
import ctypes
import ctypes.util
import os
import sqlite3
import sys
import traceback

WECHAT_FW = "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks"
WECHAT_FW_VERSIONS = os.path.join(WECHAT_FW, "Versions")  # unused fallback
ROAM_SERVER = os.path.join(WECHAT_FW, "roam_server.framework/Versions/A/roam_server")
USB_FW = "/Users/patrickchiho/Applications/WeChat-Debug.app/Contents/Frameworks/usb.framework/Versions/A"
ROAM_MIGRATION = os.path.join(WECHAT_FW, "roam_migration.framework/Versions/A/roam_migration")
DB_PATH = "str(WORKSPACE)/db/Documents/48d99549ba8c5780b0908193c1fab6fd/DB/message_2.sqlite"
CHAT = "Chat_11d8637ec8a3730380f6691705f8a23c"
MES_ID = 4134

RTLD_NOW = 0x2
RTLD_GLOBAL = 0x8
RTLD_LOCAL = 0x4

libdl = ctypes.CDLL(ctypes.util.find_library("dl"))
libdl.dlopen.argtypes = [ctypes.c_char_p, ctypes.c_int]
libdl.dlopen.restype = ctypes.c_void_p
libdl.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
libdl.dlsym.restype = ctypes.c_void_p
libdl.dlerror.restype = ctypes.c_char_p


def dlopen(path: str, mode=RTLD_NOW | RTLD_GLOBAL) -> ctypes.c_void_p:
    h = libdl.dlopen(path.encode(), mode)
    if not h:
        err = libdl.dlerror()
        raise OSError(f"dlopen failed {path}: {err.decode() if err else 'unknown'}")
    print(f"dlopen OK: {path} handle={h}")
    return h


def try_dlsym(handles, names):
    for name in names:
        for label, h in handles:
            p = libdl.dlsym(h, name.encode()) if h else libdl.dlsym(ctypes.c_void_p(0), name.encode())
            if p:
                print(f"dlsym {name} from {label} -> {p}")
            else:
                pass


def run_sql_queries(tag: str):
    uri = f"file:{DB_PATH}?mode=ro&immutable=1"
    queries = [
        ("wcdb_decompress(Message)", f'SELECT wcdb_decompress(Message) FROM "{CHAT}" WHERE MesLocalID={MES_ID}'),
        ("wcdb_decompress(Message, WCDB_CT_Message)", f'SELECT wcdb_decompress(Message, WCDB_CT_Message) FROM "{CHAT}" WHERE MesLocalID={MES_ID}'),
        ("hex+len", f'SELECT hex(substr(Message,1,8)), length(Message), WCDB_CT_Message FROM "{CHAT}" WHERE MesLocalID={MES_ID}'),
    ]
    for qname, sql in queries:
        try:
            con = sqlite3.connect(uri, uri=True)
            row = con.execute(sql).fetchone()
            con.close()
            print(f"[{tag}] {qname}: OK type={type(row[0])}")
            val = row[0]
            if isinstance(val, bytes):
                preview = val[:500]
                try:
                    text = preview.decode("utf-8")
                except Exception:
                    text = repr(preview)
            else:
                text = str(val)[:500]
            print(f"  preview: {text[:500]}")
        except Exception as e:
            print(f"[{tag}] {qname}: FAIL {e}")



def try_embedded_sqlite(handle):
    """Use sqlite3 embedded in roam_migration (still needs WCDB init for wcdb_decompress)."""
    import ctypes
    DB = b"str(WORKSPACE)/db/Documents/48d99549ba8c5780b0908193c1fab6fd/DB/message_2.sqlite"
    SQL = (
        b'SELECT wcdb_decompress(Message, WCDB_CT_Message) FROM "'
        + b"Chat_11d8637ec8a3730380f6691705f8a23c"
        + b'" WHERE MesLocalID=4134;'
    )
    libdl = ctypes.CDLL(ctypes.util.find_library("dl"))
    libdl.dlsym.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    libdl.dlsym.restype = ctypes.c_void_p
    sqlite3_open = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p))(
        libdl.dlsym(handle, b"sqlite3_open")
    )
    sqlite3_prepare_v2 = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_char_p),
    )(libdl.dlsym(handle, b"sqlite3_prepare_v2"))
    sqlite3_errmsg = ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_void_p)(libdl.dlsym(handle, b"sqlite3_errmsg"))
    sqlite3_close = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)(libdl.dlsym(handle, b"sqlite3_close"))
    db = ctypes.c_void_p()
    rc = sqlite3_open(DB, ctypes.byref(db))
    print(f"embedded sqlite3_open rc={rc}")
    stmt = ctypes.c_void_p()
    tail = ctypes.c_char_p()
    rc = sqlite3_prepare_v2(db, SQL, -1, ctypes.byref(stmt), ctypes.byref(tail))
    print(f"embedded prepare rc={rc} err={sqlite3_errmsg(db)}")
    sqlite3_close(db)



def list_exports_nm(path: str, label: str):
    import subprocess
    print(f"\n=== nm -gU {label} ===")
    if not os.path.exists(path):
        print("missing", path)
        return
    r = subprocess.run(["nm", "-gU", path], capture_output=True, text=True)
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    print(f"global exports: {len(lines)} (wcdb_decompress not exported)")
    for ln in lines[:20]:
        print(ln)
    if len(lines) > 20:
        print("...")


def missing_init_notes():
    print("\n=== Missing init (why wcdb_decompress fails offline) ===")
    print("- WCDB CompressionCenter / builtin dict table not initialized via public API from dlopen alone.")
    print("- roam_* exports are migration/server C++ APIs only; ZSTD dict slot 5 lives in runtime CompressionContext.")
    print("- Need in-process registration (app launch) or lldb capture at roam_migration VA 0x256a20 when dict id 5 resolves.")
    print("- System sqlite3 lacks WCDB UDF; embedded sqlite in roam_migration still needs WCDB global init.")

def main():
    print("=== try_wcdb_loader.py ===")
    print("arch:", os.uname().machine)
    handles = []
    # rpath for @rpath deps
    dyld_fw = os.pathsep.join([
        WECHAT_FW,
        os.path.join(WECHAT_FW, "roam_server.framework/Versions/A"),
        os.path.join(WECHAT_FW, "roam_server.framework"),
        os.path.join(WECHAT_FW, "roam_migration.framework/Versions/A"),
        os.path.join(WECHAT_FW, "roam_migration.framework"),
        os.path.join(WECHAT_FW, "usb.framework/Versions/A"),
        os.path.join(WECHAT_FW, "usb.framework"),
        "/Applications/WeChat.app/Contents/Frameworks",
        "/Applications/WeChat.app/Contents/Frameworks/usb.framework/Versions/A",
    ])
    os.environ["DYLD_FRAMEWORK_PATH"] = dyld_fw
    os.environ["DYLD_LIBRARY_PATH"] = dyld_fw
    print("USB_FW=", USB_FW, "exists=", os.path.exists(USB_FW))
    print("DYLD_FRAMEWORK_PATH=", dyld_fw)
    print("DYLD_LIBRARY_PATH=", dyld_fw)

    list_exports_nm(ROAM_SERVER, 'roam_server')
    list_exports_nm(ROAM_MIGRATION, 'roam_migration')
    missing_init_notes()

    usb_bin = os.path.join(WECHAT_FW, "usb.framework/Versions/A/usb")
    if os.path.exists(usb_bin):
        try:
            handles.append(("usb", dlopen(usb_bin)))
        except OSError as e:
            print("usb preload:", e)

    for path in [ROAM_SERVER, ROAM_MIGRATION]:
        if not os.path.exists(path):
            print("missing:", path)
            continue
        try:
            handles.append((os.path.basename(path), dlopen(path)))
        except OSError as e:
            print(e)

    syms = [
        "wcdb_decompress",
        "sqlite3_open",
        "sqlite3_exec",
        "sqlite3_enable_load_extension",
        "_ZN4WCDB17CompressionCenter5sharedEv",
    ]
    try_dlsym(handles + [("RTLD_DEFAULT", None)], syms)

    print("\n--- before frameworks (baseline) ---")
    run_sql_queries("baseline")

    mig_handles = [h for n,h in handles if "roam_migration" in n]
    if mig_handles:
        print("\n--- embedded sqlite in roam_migration ---")
        try_embedded_sqlite(mig_handles[0])

    print("\n--- after dlopen frameworks ---")
    run_sql_queries("after_dlopen")

    # ctypes sqlite3 from libsqlite3 if linked globally
    try:
        libsql = ctypes.CDLL(ctypes.util.find_library("sqlite3"))
        print("libsqlite3:", libsql)
    except Exception as e:
        print("libsqlite3 load:", e)

    print("\nDone.")
    return 0





if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
