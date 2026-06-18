"""LLDB capture: roam_migration slide + WCDB dict breakpoints (import via absolute path)."""
from __future__ import annotations

from pathlib import Path

import os
import re
import struct
import subprocess
import time
from datetime import datetime, timezone

import lldb

EXPORT_DIR = str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data"))))
HITS_LOG = os.path.join(EXPORT_DIR, "lldb_capture_hits.log")
SLIDE_INFO = os.path.join(EXPORT_DIR, "lldb_capture_slide.txt")
ATTACH_ERR = os.path.join(EXPORT_DIR, "lldb_capture_attach_denied.txt")
HIT_COUNT_FILE = os.path.join(EXPORT_DIR, "lldb_capture_hit_count.txt")
ZSTD_DICT_MAGIC = b"\x37\xa4\x30\xec"
ZSTD_DICT_SIZE = 112640
MAX_SCAN_REGION = 500 * 1024 * 1024
OFFSETS = [
    ("udf_dispatch", 0x226728),
    ("ctx_table", 0x1E77A0),
    ("user_data", 0x1E78E8),
    ("dict_resolve", 0x256A20),
    ("argc_teardown", 0x1E7958),
]

# Extra roam_migration VAs from static_0x1e78e8_report.txt call graph (route-1 argc=1).
AGGRESSIVE_OFFSETS = [
    ("dict_resolve_caller", 0x271758),  # bl 0x256a20
    ("decompress_entry", 0x2715E0),
    ("decompress_framing", 0x285ED0),  # payload offset / framing scanner
    ("dict_type_map", 0x251EC0),  # maps value type via table 0x3527fc
    ("sqlite_value_path", 0x25208C),
    ("compress_ctx_bridge", 0x2712BC),
    ("udf_dispatch_inner", 0x226770),
    ("wrapper_detect", 0x272528),
    ("zstd_expand", 0x270A78),
    ("dict_lookup_alt", 0x256B20),
]

# Tactic 1 symbols (wcdb_decompress is stripped — not exported from roam_migration).
SYMBOL_HUNT_NAMES = (
    "wcdb_decompress",
    "ZSTD_decompress_usingDict",
    "ZSTD_createDDict",
)

_CAPTURE_STATE: dict = {}


def _session_state(internal_dict):
    """LLDB script commands often pass internal_dict=None; use module state."""
    if internal_dict:
        if isinstance(internal_dict, dict):
            _CAPTURE_STATE.update(internal_dict)
    return _CAPTURE_STATE


MAIN_EXE_RE = re.compile(r"/WeChat-Debug\.app/Contents/MacOS/WeChat(?:\s|$)")


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(line: str) -> None:
    with open(HITS_LOG, "a", encoding="utf-8") as f:
        f.write(line if line.endswith("\n") else line + "\n")


def _ptr_ok(addr: int) -> bool:
    return 0x100000000 < addr < 0x8000000000


def roam_bp_hit(frame, bp_loc, extra_args, internal_dict):
    name = (extra_args or "").strip() or "unknown"
    pc = frame.GetPC()
    proc = frame.GetThread().GetProcess()
    _log(f"{_ts()} HIT bp={name} pc=0x{pc:x}")
    parts = []
    for r in ("x0", "x1", "x2", "x3", "x4", "x5", "x6"):
        v = frame.FindRegister(r).GetValueAsUnsigned()
        parts.append(f"{r}=0x{v:x}")
    _log("  register read: " + " ".join(parts))
    x0 = frame.FindRegister("x0").GetValueAsUnsigned()
    if _ptr_ok(x0):
        err = lldb.SBError()
        data = proc.ReadMemory(x0, 256, err)
        if err.Success() and data:
            path = os.path.join(EXPORT_DIR, f"x0_mem_{name}_pc{pc:x}.bin")
            with open(path, "wb") as wf:
                wf.write(data)
            _log(f"  memory read -c 256 -f x $x0 -> {path}")
    x6 = frame.FindRegister("x6").GetValueAsUnsigned()
    x5 = frame.FindRegister("x5").GetValueAsUnsigned()
    if x6 == 112640 or (100000 <= x6 <= 120000):
        if _ptr_ok(x5):
            err = lldb.SBError()
            data = proc.ReadMemory(x5, 112640, err)
            if err.Success() and data:
                out = os.path.join(EXPORT_DIR, f"real_dict_5_{name}.bin")
                with open(out, "wb") as wf:
                    wf.write(data)
                _log(f"  memory read x5 count 112640 -> {out}")
    if (
        "dict_resolve" in name
        or "decompress" in name
        or "sqlite_value" in name
        or "dict_lookup" in name
        or name == "dict_resolve"
    ):
        for reg in ("x1", "x2"):
            ptr = frame.FindRegister(reg).GetValueAsUnsigned()
            if not _ptr_ok(ptr):
                continue
            err = lldb.SBError()
            data = proc.ReadMemory(ptr, ZSTD_DICT_SIZE, err)
            if err.Success() and data and data[:4] == ZSTD_DICT_MAGIC:
                out = os.path.join(EXPORT_DIR, f"real_dict_5_{name}_{reg}.bin")
                with open(out, "wb") as wf:
                    wf.write(data)
                _log(f"  ZSTD dict magic @ {reg}=0x{ptr:x} -> {out}")
            elif err.Success() and data:
                out = os.path.join(EXPORT_DIR, f"dict_from_{reg}_{name}_pc{pc:x}.bin")
                with open(out, "wb") as wf:
                    wf.write(data)
                _log(f"  memory read {reg} count {ZSTD_DICT_SIZE} -> {out}")
    n = 0
    if os.path.exists(HIT_COUNT_FILE):
        try:
            n = int(open(HIT_COUNT_FILE, encoding="utf-8").read().strip() or "0")
        except ValueError:
            n = 0
    with open(HIT_COUNT_FILE, "w", encoding="utf-8") as cf:
        cf.write(str(n + 1))
    return False


def sqlite_bp_hit(frame, bp_loc, extra_args, internal_dict):
    name = (extra_args or "").strip() or "sqlite"
    pc = frame.GetPC()
    _log(f"{_ts()} HIT bp={name} pc=0x{pc:x}")
    parts = []
    for r in ("x0", "x1", "x2", "x3"):
        v = frame.FindRegister(r).GetValueAsUnsigned()
        parts.append(f"{r}=0x{v:x}")
    _log("  register read: " + " ".join(parts))
    return False


def symbol_bp_hit(frame, bp_loc, extra_args, internal_dict):
    name = (extra_args or "").strip() or "symbol"
    pc = frame.GetPC()
    _log(f"{_ts()} HIT bp=sym:{name} pc=0x{pc:x}")
    parts = []
    for r in ("x0", "x1", "x2", "x3", "x4", "x5", "x6"):
        v = frame.FindRegister(r).GetValueAsUnsigned()
        parts.append(f"{r}=0x{v:x}")
    _log("  register read: " + " ".join(parts))
    return False


def _aggressive_enabled() -> bool:
    return os.environ.get("LLDB_CAPTURE_AGGRESSIVE", "").strip() not in ("", "0")


def _find_pid() -> int | None:
    try:
        lines = subprocess.check_output(
            ["pgrep", "-lf", "WeChat-Debug.app/Contents/MacOS/WeChat"],
            text=True,
        ).splitlines()
    except (subprocess.CalledProcessError, ValueError):
        return None
    for line in lines:
        if "WeChatAppEx" in line or "crashpad" in line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        pid_s, cmd = parts[0], parts[1]
        if not pid_s.isdigit():
            continue
        if MAIN_EXE_RE.search(cmd):
            return int(pid_s)
    return None


def _parse_roam_slide(interp, target) -> int | None:
    res = lldb.SBCommandReturnObject()
    interp.HandleCommand("image list -o -f roam_migration", res)
    txt = (res.GetOutput() or "") + (res.GetError() or "")
    for line in txt.splitlines():
        if "roam_migration" not in line:
            continue
        m = re.match(r"\[\s*\d+\]\s+(0x[0-9a-fA-F]+)", line.strip())
        if m:
            return int(m.group(1), 16)
    m = re.search(r"\[\s*\d+\]\s+(0x[0-9a-fA-F]+)", txt)
    if m:
        return int(m.group(1), 16)
    for i in range(target.GetNumModules()):
        mod = target.GetModuleAtIndex(i)
        fn = mod.GetFileSpec().GetFilename() or ""
        if "roam_migration" in fn:
            addr = mod.GetObjectFileHeaderAddress().GetLoadAddress(target)
            if addr != lldb.LLDB_INVALID_ADDRESS:
                return addr
    return None


def _attach_target(debugger, interp) -> tuple[lldb.SBTarget, int] | None:
    res = lldb.SBCommandReturnObject()
    target = debugger.GetSelectedTarget()
    pid = None
    if target.IsValid() and target.process.IsValid():
        pid = target.process.GetProcessID()
        print(f"Using existing target pid={pid}")
        return target, pid

    pid = _find_pid()
    if pid is None:
        msg = f"{_ts()} attach_denied: no WeChat-Debug main PID (pgrep -lf)\n"
        open(ATTACH_ERR, "w", encoding="utf-8").write(msg)
        print(msg.strip())
        return None

    interp.HandleCommand(f"process attach --pid {pid}", res)
    if not res.Succeeded():
        msg = f"{_ts()} attach_denied: {res.GetError() or res.GetOutput()}\n"
        open(ATTACH_ERR, "w", encoding="utf-8").write(msg)
        print(msg.strip())
        return None
    target = debugger.GetSelectedTarget()
    if not target.IsValid():
        print("ERROR: no target after attach")
        return None
    return target, pid


def _install_roam_offset_bp(interp, name: str, addr: int, *, auto_continue: bool = True) -> bool:
    res = lldb.SBCommandReturnObject()
    interp.HandleCommand(f"breakpoint set -a 0x{addr:x} -N {name}", res)
    if not res.Succeeded():
        print(f"WARN: breakpoint {name} @ 0x{addr:x}: {res.GetError()}")
        return False
    cont = ' -o "continue"' if auto_continue else ""
    interp.HandleCommand(
        f'breakpoint command add -N {name} -o '
        f'"script lldb_capture_setup.roam_bp_hit(frame, bp_loc, \\"{name}\\", None)"'
        f"{cont}",
        res,
    )
    if not res.Succeeded():
        print(f"WARN: bp command {name}: {res.GetError()}")
        return False
    print(f"breakpoint {name} @ 0x{addr:x}")
    return True


def _install_symbol_breakpoints(interp, slide: int) -> int:
    """Try roam_migration symbol / regex breakpoints (wcdb_decompress, ZSTD_*)."""
    res = lldb.SBCommandReturnObject()
    count = 0
    _log(f"{_ts()} SYMBOL_HUNT_START slide=0x{slide:x}")
    for sym in SYMBOL_HUNT_NAMES:
        interp.HandleCommand(f"image lookup -r -n {sym} roam_migration", res)
        out = (res.GetOutput() or "") + (res.GetError() or "")
        for line in out.splitlines():
            trimmed = line.strip()
            if trimmed:
                _log(f"  lookup {sym}: {trimmed}")
        interp.HandleCommand(f'breakpoint set -r "^{re.escape(sym)}$" -s roam_migration -N sym_{sym}', res)
        if not res.Succeeded():
            interp.HandleCommand(f"breakpoint set -n {sym} -s roam_migration -N sym_{sym}", res)
        if not res.Succeeded():
            _log(f"  sym_bp {sym}: unresolved")
            continue
        bp_name = f"sym_{sym}"
        interp.HandleCommand(
            f'breakpoint command add -N {bp_name} -o '
            f'"script lldb_capture_setup.symbol_bp_hit(frame, bp_loc, \\"{sym}\\", None)" '
            f'-o "continue"',
            res,
        )
        if res.Succeeded():
            count += 1
            print(f"symbol breakpoint {bp_name} (roam_migration)")
            _log(f"  sym_bp {sym}: installed")
        else:
            _log(f"  sym_bp {sym}: command add failed")
    _log(f"{_ts()} SYMBOL_HUNT_END count={count}")
    return count


def _install_dict_resolve_bp(interp, slide: int, *, auto_continue: bool) -> bool:
    """Single dict_resolve breakpoint; optional auto-continue for batch-style runs."""
    res = lldb.SBCommandReturnObject()
    name = "dict_resolve"
    off = 0x256A20
    addr = slide + off
    interp.HandleCommand(f"breakpoint set -a 0x{addr:x} -N {name}", res)
    if not res.Succeeded():
        print(res.GetError() or res.GetOutput())
        return False
    bp_cmds = (
        '-o "register read x0 x1 x2 x3 x4 x5 x6" '
        '-o "memory read -c 256 `$x0" '
        '-o "disassemble -p" '
        '-o "script lldb_capture_setup.roam_bp_hit(frame, bp_loc, \\"dict_resolve\\", None)"'
    )
    if auto_continue:
        interp.HandleCommand(f"breakpoint command add -N {name} {bp_cmds} -o continue", res)
    else:
        interp.HandleCommand(f"breakpoint command add -N {name} {bp_cmds}", res)
    if not res.Succeeded():
        print(res.GetError() or res.GetOutput())
        return False
    return True


def _install_offset_breakpoints(interp, slide: int, offsets: list[tuple[str, int]], *, label: str) -> int:
    res = lldb.SBCommandReturnObject()
    bp_count = 0
    for name, off in offsets:
        addr = slide + off
        bp_name = f"{label}_{name}" if label else name
        interp.HandleCommand(f"breakpoint set -a 0x{addr:x} -N {bp_name}", res)
        if not res.Succeeded():
            print(f"WARN: breakpoint {bp_name} @ 0x{addr:x}: {res.GetError()}")
            _log(f"{_ts()} BP_FAIL name={bp_name} addr=0x{addr:x} err={res.GetError()}")
            continue
        bp_count += 1
        interp.HandleCommand(
            f'breakpoint command add -N {bp_name} -o '
            f'"script lldb_capture_setup.roam_bp_hit(frame, bp_loc, \\"{bp_name}\\", None)" '
            f'-o "continue"',
            res,
        )
        print(f"breakpoint {bp_name} @ 0x{addr:x} (slide+0x{off:x})")
    return bp_count


def _install_breakpoints(debugger, interp, target, slide: int) -> int:
    res = lldb.SBCommandReturnObject()
    aggressive = _aggressive_enabled()
    bp_count = _install_offset_breakpoints(interp, slide, OFFSETS, label="")
    if aggressive:
        print("LLDB_CAPTURE_AGGRESSIVE=1 — installing expanded roam_migration offsets")
        _log(f"{_ts()} AGGRESSIVE_OFFSETS_START count={len(AGGRESSIVE_OFFSETS)}")
        bp_count += _install_offset_breakpoints(interp, slide, AGGRESSIVE_OFFSETS, label="agg")
    if aggressive or os.environ.get("LLDB_CAPTURE_SYMBOL_HUNT", "").strip() not in ("", "0"):
        bp_count += _install_symbol_breakpoints(interp, slide)
    if os.environ.get("LLDB_CAPTURE_SQLITE", "").strip() not in ("", "0"):
        for sym in ("sqlite3_column_blob", "sqlite3_step"):
            interp.HandleCommand(f"breakpoint set --name {sym}", res)
            if res.Succeeded():
                bp_count += 1
                interp.HandleCommand(
                    f'breakpoint command add --name {sym} -o '
                    f'"script lldb_capture_setup.sqlite_bp_hit(frame, bp_loc, \"{sym}\", None)" '
                    f'-o "continue"',
                    res,
                )
                print(f"breakpoint {sym} (libsqlite3)")
    res = lldb.SBCommandReturnObject()
    interp.HandleCommand("breakpoint list", res)
    blist = (res.GetOutput() or "") + (res.GetError() or "")
    _log(f"{_ts()} BREAKPOINTS_READY count={bp_count}")
    for line in blist.splitlines():
        _log("  " + line)
    return bp_count


def _continue_with_timeout(debugger, target, seconds: int) -> None:
    """Run process continue and pump LLDB events until timeout (batch-safe)."""
    process = target.GetProcess()
    _log(f"{_ts()} CONTINUE_START seconds={seconds}")
    print(f"CONTINUE_START ({seconds}s) — scroll 米迷 chat now")
    try:
        if not process.IsValid():
            _log(f"{_ts()} CONTINUE_ERROR invalid process")
            print("ERROR: invalid process for continue")
            return

        listener = debugger.GetListener()
        broadcaster = process.GetBroadcaster()
        mask = lldb.SBProcess.eBroadcastBitStateChanged | lldb.SBProcess.eBroadcastBitInterrupt
        broadcaster.AddListener(listener, mask)

        debugger.SetAsync(True)
        res = lldb.SBCommandReturnObject()
        debugger.GetCommandInterpreter().HandleCommand("process continue", res)
        if not res.Succeeded():
            _log(f"{_ts()} CONTINUE_ERROR {res.GetError() or res.GetOutput()}")
            print(f"WARN: process continue: {res.GetError() or res.GetOutput()}")

        deadline = time.time() + seconds
        logged_event_err = False
        while time.time() < deadline:
            remaining = max(0, int(deadline - time.time()))
            wait_sec = min(1, remaining) if remaining else 0
            event = lldb.SBEvent()
            got_event = False
            try:
                if wait_sec > 0:
                    got_event = listener.WaitForEventForBroadcasterWithType(
                        wait_sec, broadcaster, mask, event
                    )
                if got_event:
                    debugger.HandleEvent(event)
            except Exception as exc:
                if not logged_event_err:
                    _log(f"{_ts()} CONTINUE_EVENT_ERR {exc}")
                    logged_event_err = True
            state = process.GetState()
            if state == lldb.eStateExited:
                _log(f"{_ts()} CONTINUE_NOTE process exited (waiting until timeout)")
            elif state == lldb.eStateStopped:
                for ti in range(process.GetNumThreads()):
                    thread = process.GetThreadAtIndex(ti)
                    if thread.GetStopReason() == lldb.eStopReasonBreakpoint:
                        process.Continue()
                        break
    finally:
        _log(f"{_ts()} CONTINUE_END seconds={seconds}")
        print(f"CONTINUE_END ({seconds}s elapsed)")


def run_manual_dict_resolve(debugger, command, result, internal_dict):
    """dict_resolve only — stop on each hit; type 'continue' at (lldb) prompt."""
    debugger.SetAsync(False)
    interp = debugger.GetCommandInterpreter()
    attached = _attach_target(debugger, interp)
    if not attached:
        return
    target, pid = attached
    slide = _parse_roam_slide(interp, target)
    if slide is None:
        print("ERROR: image list -o -f roam_migration — no load address")
        return
    off = 0x256A20
    addr = slide + off
    auto_continue = os.environ.get("LLDB_DICT_RESOLVE_AUTO_CONTINUE", "").strip() not in ("", "0")
    if (command or "").strip().lower() in ("auto", "--auto"):
        auto_continue = True
    with open(SLIDE_INFO, "w", encoding="utf-8") as f:
        f.write(f"pid={pid}\nroam_migration_slide=0x{slide:x}\ndict_resolve=0x{addr:x} (slide+0x{off:x})\n")
    _log(
        f"{_ts()} MANUAL_DICT_RESOLVE pid={pid} slide=0x{slide:x} addr=0x{addr:x} "
        f"auto_continue={auto_continue}"
    )
    if not _install_dict_resolve_bp(interp, slide, auto_continue=auto_continue):
        return
    print(f"dict_resolve @ 0x{addr:x} (slide=0x{slide:x})")
    print(f"Log: {HITS_LOG}")
    if auto_continue:
        print("AUTO continue on each hit — run: process continue")
    else:
        print("NO auto-continue — on each stop inspect output, then type: continue")
    print("")
    print("=== (lldb) prompt — next steps ===")
    print("  process continue    # or: c")
    print("  Scroll 米迷 chat (CT=2 messages); on each stop: continue")
    print("  quit                # detach when done")
    print("")
    print("=== Bash terminal — NOT at (lldb) prompt ===")
    print("  90s batch:  ./run_lldb_capture_90s.sh")
    print("  SQLite batch: quit lldb first, then:")
    print("    LLDB_CAPTURE_SQLITE=1 ./run_lldb_capture_90s.sh")
    res = lldb.SBCommandReturnObject()
    interp.HandleCommand("breakpoint list", res)
    print(res.GetOutput() or res.GetError())


def run_capture_setup(debugger, command, result, internal_dict):
    """Attach + breakpoints only (no continue)."""
    state = _session_state(internal_dict)
    debugger.SetAsync(False)
    interp = debugger.GetCommandInterpreter()
    attached = _attach_target(debugger, interp)
    if not attached:
        return
    target, pid = attached
    slide = _parse_roam_slide(interp, target)
    if slide is None:
        print("ERROR: image list -o -f roam_migration — no load address")
        return
    with open(SLIDE_INFO, "w", encoding="utf-8") as f:
        f.write(f"pid={pid}\nroam_migration_slide=0x{slide:x}\n")
        f.write(f"aggressive={_aggressive_enabled()}\n")
        for name, off in OFFSETS:
            f.write(f"{name}=0x{slide + off:x} (slide+0x{off:x})\n")
        if _aggressive_enabled():
            for name, off in AGGRESSIVE_OFFSETS:
                f.write(f"agg_{name}=0x{slide + off:x} (slide+0x{off:x})\n")
    _log(
        f"{_ts()} SESSION_START pid={pid} slide=0x{slide:x} "
        f"aggressive={_aggressive_enabled()}"
    )
    print(f"TARGET_OK pid={pid} roam_migration_load=0x{slide:x}")
    bp_count = _install_breakpoints(debugger, interp, target, slide)
    print(f"BREAKPOINTS_SET={bp_count}")
    state["lldb_capture_slide"] = slide
    state["lldb_capture_pid"] = pid


def run_capture_wait(debugger, command, result, internal_dict):
    """Continue with timeout; optional detach+quit when BATCH_CAPTURE_SECONDS set."""
    state = _session_state(internal_dict)
    target = debugger.GetSelectedTarget()
    if not target.IsValid() or not target.process.IsValid():
        print("ERROR: attach first (wcdb_capture_run or wcdb_capture_setup)")
        return
    batch_sec = int(os.environ.get("BATCH_CAPTURE_SECONDS", "0") or "0")
    if batch_sec <= 0:
        batch_sec = int((command or "").strip() or "0")
    if batch_sec <= 0:
        print("Usage: wcdb_capture_wait <seconds>  OR  export BATCH_CAPTURE_SECONDS=90")
        return

    print("")
    print("=== UI TRIGGER ===")
    print("Open 米迷 chat; scroll CT=2 (compressed) messages during the capture window.")
    print("Tips: open quote chain (引用), expand long bubbles, search「haha」/「哈哈」/「笙歌」,")
    print("      tap a message to open detail — forces sqlite read / wcdb_decompress.")
    print(f"Log: {HITS_LOG}")
    print("")

    _continue_with_timeout(debugger, target, batch_sec)

    interp = debugger.GetCommandInterpreter()
    res = lldb.SBCommandReturnObject()
    if os.environ.get("BATCH_CAPTURE_DETACH", "1").strip() not in ("", "0"):
        interp.HandleCommand("process detach", res)
    if os.environ.get("BATCH_CAPTURE_QUIT", "1").strip() not in ("", "0"):
        interp.HandleCommand("quit", res)

    pid = target.process.GetProcessID()
    slide = state.get("lldb_capture_slide")
    if slide is None and os.path.exists(SLIDE_INFO):
        for line in open(SLIDE_INFO, encoding="utf-8"):
            if line.startswith("roam_migration_slide="):
                slide = int(line.split("=", 1)[1].strip(), 16)
                break
    hits = 0
    if os.path.exists(HIT_COUNT_FILE):
        try:
            hits = int(open(HIT_COUNT_FILE, encoding="utf-8").read().strip() or "0")
        except ValueError:
            hits = 0
    summary_path = os.path.join(EXPORT_DIR, "lldb_capture_summary.txt")
    with open(summary_path, "w", encoding="utf-8") as sf:
        if slide:
            sf.write(
                f"pid={pid}\nroam_migration_slide=0x{slide:x}\n"
                f"aggressive={_aggressive_enabled()}\n"
                f"batch_seconds={batch_sec}\nbreakpoint_hits={hits}\n"
            )
        else:
            sf.write(
                f"pid={pid}\naggressive={_aggressive_enabled()}\n"
                f"batch_seconds={batch_sec}\nbreakpoint_hits={hits}\n"
            )


def _symbol_hunt_log_path(pid: int | None) -> str:
    if pid:
        return os.path.join(EXPORT_DIR, f"symbol_hunt_{pid}.txt")
    return os.path.join(EXPORT_DIR, "symbol_hunt.txt")


def _lookup_symbols_sb(target, sym: str, modules: list[str]) -> list[str]:
    """SB API lookup — avoids lldb `image lookup -r` hangs on WeChat."""
    addrs: list[str] = []
    seen: set[str] = set()
    candidates = list(modules) + [None]
    for mod_name in candidates:
        if mod_name:
            mod = target.FindModule(lldb.SBFileSpec(mod_name, False))
            if not mod.IsValid():
                continue
            symbol = mod.FindSymbol(sym)
            if symbol.IsValid():
                la = symbol.GetStartAddress().GetLoadAddress(target)
                if la != lldb.LLDB_INVALID_ADDRESS:
                    a = f"0x{la:x}"
                    if a not in seen:
                        seen.add(a)
                        addrs.append(a)
            num_syms = mod.GetNumSymbols()
            for i in range(num_syms):
                s = mod.GetSymbolAtIndex(i)
                if (s.GetName() or "") != sym:
                    continue
                la = s.GetStartAddress().GetLoadAddress(target)
                if la != lldb.LLDB_INVALID_ADDRESS:
                    a = f"0x{la:x}"
                    if a not in seen:
                        seen.add(a)
                        addrs.append(a)
    return addrs


def _parse_lookup_addresses(text: str) -> list[str]:
    addrs: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "Address:" not in line:
            continue
        for m in re.finditer(r"(0x[0-9a-fA-F]+)", line):
            addr = m.group(1)
            if addr not in seen:
                seen.add(addr)
                addrs.append(addr)
    return addrs


def _priority_module_names(target) -> list[str]:
    """Prefer roam/zstd modules — global `image lookup -r` can hang on WeChat."""
    hits: list[str] = []
    for i in range(target.GetNumModules()):
        mod = target.GetModuleAtIndex(i)
        fn = (mod.GetFileSpec().GetFilename() or "").lower()
        if any(k in fn for k in ("roam", "zstd", "wcdb", "migration", "compression")):
            name = mod.GetFileSpec().GetFilename() or ""
            if name and name not in hits:
                hits.append(name)
    return hits


def run_symbol_hunt(debugger, command, result, internal_dict):
    """Tactic 1: image lookup for WCDB/ZSTD symbols; log + optional breakpoints."""
    debugger.SetAsync(False)
    interp = debugger.GetCommandInterpreter()
    target = debugger.GetSelectedTarget()
    pid = None
    if target.IsValid() and target.process.IsValid():
        pid = target.process.GetProcessID()
    if pid is None:
        pid = _find_pid()
    log_path = _symbol_hunt_log_path(pid)
    lines: list[str] = [f"{_ts()} SYMBOL_HUNT pid={pid}\n"]
    all_addrs: list[str] = []
    set_bp = (command or "").strip().lower() not in ("no-bp", "--no-bp")
    modules = _priority_module_names(target) if target.IsValid() else []
    if modules:
        lines.append(f"priority_modules={modules}\n")
    for sym in SYMBOL_HUNT_NAMES:
        sym_addrs: list[str] = []
        if target.IsValid():
            sym_addrs = _lookup_symbols_sb(target, sym, modules)
            if sym_addrs:
                lines.append(f"=== SB lookup {sym} ===\n  -> {', '.join(sym_addrs)}\n")
        if not sym_addrs:
            cmds: list[str] = []
            for mod in modules:
                cmds.append(f"image lookup -n {sym} {mod}")
                cmds.append(f"image lookup -r -n {sym} {mod}")
            for cmd in cmds:
                res = lldb.SBCommandReturnObject()
                interp.HandleCommand(cmd, res)
                out = (res.GetOutput() or "") + (res.GetError() or "")
                lines.append(f"=== {cmd} ===\n{out}\n")
                addrs = _parse_lookup_addresses(out)
                if addrs:
                    sym_addrs.extend(addrs)
                    break
        if sym_addrs:
            lines.append(f"  -> {len(sym_addrs)} address(es): {', '.join(sym_addrs)}\n")
            all_addrs.extend(sym_addrs)
        else:
            lines.append("  -> no matches\n")
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.writelines(lines)
    bp_count = 0
    if set_bp and all_addrs:
        seen_bp: set[str] = set()
        for addr in all_addrs:
            if addr in seen_bp:
                continue
            seen_bp.add(addr)
            res = lldb.SBCommandReturnObject()
            interp.HandleCommand(f"breakpoint set -a {addr} -N sym_{addr}", res)
            if res.Succeeded():
                bp_count += 1
                lines.append(f"breakpoint set -a {addr} OK\n")
            else:
                lines.append(f"breakpoint set -a {addr} FAIL: {res.GetError()}\n")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.writelines(lines[-bp_count * 2 :] if bp_count else [])
    print(f"SYMBOL_HUNT log={log_path} matches={len(all_addrs)} breakpoints={bp_count}")
    if all_addrs:
        print("Found:", ", ".join(all_addrs))
        if set_bp and bp_count:
            print("Breakpoints set — run: process continue  (scroll 米迷 chat)")
    else:
        print("No symbols found — try memory scan (wcdb_memory_dict_scan)")


def run_memory_dict_scan(debugger, command, result, internal_dict):
    """Tactic 2: scan readable regions <500MB for ZSTD dict magic; dump 112640 bytes."""
    debugger.SetAsync(False)
    target = debugger.GetSelectedTarget()
    if not target.IsValid() or not target.process.IsValid():
        print("ERROR: attach first (lldb -p PID)")
        return
    process = target.GetProcess()
    pid = process.GetProcessID()
    scan_log = os.path.join(EXPORT_DIR, f"memory_dict_scan_{pid}.log")
    err = lldb.SBError()
    hits: list[int] = []
    regions_scanned = 0
    bytes_scanned = 0
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not process.GetMemoryRegionInfo(addr, region):
            break
        base = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - base
        if region.IsReadable() and size > 0 and size < MAX_SCAN_REGION:
            chunk = 4 * 1024 * 1024
            off = 0
            while off < size:
                n = min(chunk, size - off)
                data = process.ReadMemory(base + off, n, err)
                if err.Fail():
                    break
                bytes_scanned += len(data)
                start = 0
                while True:
                    i = data.find(ZSTD_DICT_MAGIC, start)
                    if i < 0:
                        break
                    hits.append(base + off + i)
                    start = i + 1
                off += n
            regions_scanned += 1
        if end <= addr:
            break
        addr = end

    dumped: list[str] = []
    lines = [
        f"{_ts()} MEMORY_DICT_SCAN pid={pid}\n",
        f"regions_scanned={regions_scanned} bytes_scanned={bytes_scanned} magic_hits={len(hits)}\n",
    ]
    for h in hits:
        data = process.ReadMemory(h, ZSTD_DICT_SIZE, err)
        if err.Fail() or len(data) < ZSTD_DICT_SIZE:
            lines.append(f"dump_skip 0x{h:x} read_fail={err.GetCString()}\n")
            continue
        out = os.path.join(EXPORT_DIR, f"dict_from_0x{h:x}.bin")
        with open(out, "wb") as wf:
            wf.write(data)
        dumped.append(out)
        hdr = data[:8]
        dict_id = struct.unpack("<I", hdr[4:8])[0] if len(hdr) >= 8 else -1
        lines.append(f"dump_ok path={out} addr=0x{h:x} dict_id={dict_id}\n")

    with open(scan_log, "w", encoding="utf-8") as lf:
        lf.writelines(lines)
    print(f"MEMORY_SCAN regions={regions_scanned} hits={len(hits)} dumped={len(dumped)}")
    print(f"Scan log: {scan_log}")
    for p in dumped:
        print(f"  {p}")
    if not dumped:
        print("No dict dumps — open 米迷 chat and re-run after dict load")


def run_capture(debugger, command, result, internal_dict):
    """Full flow: attach, breakpoints, timed continue (batch), summary."""
    state = _session_state(internal_dict)
    verify_only = os.environ.get("LLDB_CAPTURE_VERIFY", "").strip() not in ("", "0")
    run_capture_setup(debugger, command, result, state)
    target = debugger.GetSelectedTarget()
    if not target.IsValid() or not target.process.IsValid():
        print("ERROR: setup failed — not continuing")
        return
    if verify_only:
        print("LLDB_CAPTURE_VERIFY=1 — skipping continue")
        return

    batch_sec = int(os.environ.get("BATCH_CAPTURE_SECONDS", "0") or "0")
    if batch_sec > 0:
        run_capture_wait(debugger, str(batch_sec), result, state)
    else:
        print("")
        print("=== UI TRIGGER (interactive) ===")
        print("Run: process continue  (or c)")
        print("Scroll 米迷 chat; open quote chain, expand content, search 哈哈")
        print("Then check lldb_capture_hits.log")
        print(f"Or batch: BATCH_CAPTURE_SECONDS=90 wcdb_capture_run")
        print(f"Aggressive batch: LLDB_CAPTURE_AGGRESSIVE=1 ./run_lldb_capture_90s.sh")
        print("")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_capture wcdb_capture_run"
    )
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_capture_setup wcdb_capture_setup"
    )
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_capture_wait wcdb_capture_wait"
    )
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_manual_dict_resolve wcdb_manual_dict_resolve"
    )
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_symbol_hunt wcdb_symbol_hunt"
    )
    debugger.HandleCommand(
        "command script add -f lldb_capture_setup.run_memory_dict_scan wcdb_memory_dict_scan"
    )
