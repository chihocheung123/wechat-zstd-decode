"""LLDB module v6: roam_migration __TEXT/__DATA + rw- heap scan while STOPPED (no Continue when BP off)."""
from __future__ import annotations

from pathlib import Path

import lldb
import os
import re
import struct
import subprocess
import sys
import time
from datetime import datetime

REPO_ROOT = Path(os.environ.get("WECHAT_ZSTD_REPO", Path(__file__).resolve().parent.parent))
EXPORT = str(Path(os.environ.get("WECHAT_ZSTD_WORKSPACE", str(REPO_ROOT / "data"))))
APP_LABEL = os.environ.get("MIGRATION_CAPTURE_APP_LABEL", "WeChat")
APP_PATH = os.environ.get("MIGRATION_CAPTURE_APP_PATH", "")
ENABLE_BP = os.environ.get("MIGRATION_ENABLE_BP", "0") not in ("0", "false", "False", "no")
MAGIC5 = b"\x37\xa4\x30\xec\x05\x00\x00\x00"
DICT_SIZE = 112640
SCAN_SECONDS = 90
SCAN_INTERVAL = 2
CHUNK_SIZE = 64 * 1024
PROGRESS_EVERY = 2 * 1024 * 1024
MAX_REGION_PHASE2 = 50 * 1024 * 1024
SCAN_SECTIONS = ("__TEXT", "__DATA")
BP_SYMBOL_NAMES = (
    "ZSTD_decompress",
    "ZSTD_DDict",
    "ZSTD_DDict_create",
    "ZSTD_decompressDCtx",
    "ZSTD_decompress_usingDDict",
)
BP_MAX = 10
VALIDATE_SCRIPT = os.environ.get(
    "WECHAT_ZSTD_VALIDATE_SCRIPT",
    str(REPO_ROOT / "scripts" / "validate_dict5.py"),
)
LOG_PATH = os.path.join(EXPORT, "migration_capture.log")

_STATE: dict = {
    "seen": set(),
    "captured": [],
    "bp_hits": 0,
    "phase2_regions": [],
    "phase2_idx": 0,
    "phase2_offset": 0,
}


def _log(msg: str) -> None:
    line = f"[{datetime.now().isoformat()}] {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _clear_err(err: lldb.SBError) -> None:
    err.Clear()


def read_memory_safe(proc: lldb.SBProcess, addr: int, size: int, err: lldb.SBError) -> bytes | None:
    _clear_err(err)
    data = proc.ReadMemory(addr, size, err)
    if not err.Fail() and len(data) == size:
        return data

    parts: list[bytes] = []
    offset = 0
    chunk = min(CHUNK_SIZE, size)
    while offset < size:
        want = min(chunk, size - offset)
        _clear_err(err)
        piece = proc.ReadMemory(addr + offset, want, err)
        if err.Fail() or not piece:
            if chunk > 4096:
                chunk //= 2
                continue
            return None
        parts.append(piece)
        offset += len(piece)
    merged = b"".join(parts)
    return merged if len(merged) == size else None


def roam_migration_ranges(debugger: lldb.SBDebugger) -> list[tuple[int, int, str]]:
    """Only roam_migration __TEXT and __DATA segments (~4MB, not full WeChat)."""
    target = debugger.GetSelectedTarget()
    ranges: list[tuple[int, int, str]] = []
    seen: set[tuple[int, str]] = set()

    for mod in target.module_iter():
        path = mod.file.fullpath or mod.file.GetFilename() or ""
        if "roam_migration" not in path:
            continue
        for sect in mod.section_iter():
            name = sect.GetName() or ""
            if name not in SCAN_SECTIONS:
                continue
            base = sect.GetLoadAddress(target)
            if base == lldb.LLDB_INVALID_ADDRESS:
                continue
            size = sect.GetByteSize()
            if size <= 0:
                continue
            key = (base, name)
            if key in seen:
                continue
            seen.add(key)
            ranges.append((base, base + size, f"roam_migration:{name}"))

    if not ranges:
        res = lldb.SBCommandReturnObject()
        ci = debugger.GetCommandInterpreter()
        ci.HandleCommand("image list -o -f roam_migration", res)
        for line in (res.GetOutput() or "").splitlines():
            if "roam_migration" not in line:
                continue
            for token in line.strip().split():
                if token.startswith("0x"):
                    try:
                        base = int(token, 16)
                    except ValueError:
                        continue
                    ranges.append((base, base + 4 * 1024 * 1024, "roam_migration:fallback"))
                    break

    ranges.sort(key=lambda r: (0 if ":__TEXT" in r[2] else 1, r[0]))
    return ranges


def _is_rw_region(region: lldb.SBMemoryRegionInfo) -> bool:
    try:
        return region.IsReadable() and region.IsWritable() and not region.IsExecutable()
    except Exception:
        return False


def _rw_regions_lldb(proc: lldb.SBProcess) -> list[tuple[int, int, str]]:
    ranges: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    addr = 0
    while addr < (1 << 64) - 1:
        region = lldb.SBMemoryRegionInfo()
        if not proc.GetMemoryRegionInfo(addr, region):
            break
        base = region.GetRegionBase()
        end = region.GetRegionEnd()
        size = end - base
        name = region.GetName() or ""
        if (
            _is_rw_region(region)
            and size > 0
            and size <= MAX_REGION_PHASE2
            and "__LINKEDIT" not in name
        ):
            key = (base, end)
            if key not in seen:
                seen.add(key)
                label = name.strip() if name else f"rw:0x{base:x}"
                ranges.append((base, end, label))
        if end <= addr:
            break
        addr = end
    ranges.sort(key=lambda r: r[0])
    return ranges


_VMAP_LINE = re.compile(
    r"^(?P<start>0x[0-9a-fA-F]+)-(?P<end>0x[0-9a-fA-F]+)\s+(?P<perm>\S+)(?:\s+(?P<rest>.*))?$"
)


def _rw_regions_vmmap(pid: int) -> list[tuple[int, int, str]]:
    out = ""
    for cmd in (["vmmap", "-noback", str(pid)], ["vmmap", str(pid)]):
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.stdout:
                out = proc.stdout
                break
        except Exception as exc:
            _log(f"VMMAP_TRY_FAIL {' '.join(cmd)} {exc}")
            continue
    if not out:
        return []

    ranges: list[tuple[int, int, str]] = []
    seen: set[tuple[int, int]] = set()
    for line in out.splitlines():
        m = _VMAP_LINE.match(line.strip())
        if not m:
            continue
        if m.group("perm") != "rw-":
            continue
        rest = (m.group("rest") or "").strip()
        if "__LINKEDIT" in rest or "__LINKEDIT" in line:
            continue
        try:
            base = int(m.group("start"), 16)
            end = int(m.group("end"), 16)
        except ValueError:
            continue
        size = end - base
        if size <= 0 or size > MAX_REGION_PHASE2:
            continue
        key = (base, end)
        if key in seen:
            continue
        seen.add(key)
        label = rest if rest else f"vmmap:0x{base:x}"
        ranges.append((base, end, label))
    ranges.sort(key=lambda r: r[0])
    return ranges


def rw_memory_regions(proc: lldb.SBProcess, pid: int) -> list[tuple[int, int, str]]:
    """All rw- regions via LLDB memory regions, vmmap fallback. Skips >50MB and __LINKEDIT."""
    ranges = _rw_regions_lldb(proc)
    if ranges:
        _log(f"PHASE2_REGIONS source=lldb count={len(ranges)}")
        return ranges
    ranges = _rw_regions_vmmap(pid)
    _log(f"PHASE2_REGIONS source=vmmap count={len(ranges)}")
    return ranges


def scan_range(
    proc: lldb.SBProcess,
    start: int,
    end: int,
    err: lldb.SBError,
    section_name: str,
    start_offset: int = 0,
    deadline: float | None = None,
) -> tuple[list[int], int, bool]:
    """Scan [start,end) in 64KB chunks; progress every 2MB. Returns (hits, bytes_scanned, completed)."""
    hits: list[int] = []
    size = end - start
    if size <= 0:
        return hits, 0, True

    pos = max(0, min(start_offset, size))
    last_progress = pos
    total_mb = size / (1024 * 1024)

    while pos < size:
        if deadline is not None and time.time() >= deadline:
            return hits, pos, False

        n = min(CHUNK_SIZE, size - pos)
        _clear_err(err)
        data = proc.ReadMemory(start + pos, n, err)
        if err.Fail() or not data:
            pos += n
        else:
            idx = 0
            while True:
                i = data.find(MAGIC5, idx)
                if i < 0:
                    break
                hits.append(start + pos + i)
                idx = i + 1
            pos += len(data)

        scanned = pos
        if scanned - last_progress >= PROGRESS_EVERY or scanned >= size:
            at_mb = scanned / (1024 * 1024)
            _log(f"SCAN_PROGRESS {section_name} at_mb={at_mb:.2f} total_mb={total_mb:.2f}")
            last_progress = scanned

    return hits, pos, True


def _collect_new_hits(hits: list[int]) -> list[int]:
    new_hits: list[int] = []
    for h in hits:
        if h not in _STATE["seen"]:
            _STATE["seen"].add(h)
            new_hits.append(h)
    return new_hits


def scan_phase1(proc: lldb.SBProcess, ranges: list[tuple[int, int, str]], err: lldb.SBError) -> list[int]:
    all_hits: list[int] = []
    for base, end, name in ranges:
        mod_hits, _, _ = scan_range(proc, base, end, err, name)
        all_hits.extend(mod_hits)
    return _collect_new_hits(all_hits)


def scan_phase2(
    proc: lldb.SBProcess,
    err: lldb.SBError,
    deadline: float,
) -> tuple[list[int], int]:
    """Incremental rw- scan; resumes across rounds via _STATE phase2_idx/offset."""
    regions = _STATE["phase2_regions"]
    if not regions:
        return [], 0

    all_hits: list[int] = []
    idx = _STATE["phase2_idx"]
    offset = _STATE["phase2_offset"]
    regions_touched = 0

    while idx < len(regions):
        if time.time() >= deadline:
            break
        base, end, name = regions[idx]
        mod_hits, new_offset, completed = scan_range(
            proc, base, end, err, f"phase2:{name}", start_offset=offset, deadline=deadline
        )
        all_hits.extend(mod_hits)
        regions_touched += 1
        if completed:
            idx += 1
            offset = 0
        else:
            offset = new_offset
            break

    if idx >= len(regions):
        idx = 0
        offset = 0

    _STATE["phase2_idx"] = idx
    _STATE["phase2_offset"] = offset
    return _collect_new_hits(all_hits), regions_touched


def check_addr_for_magic5(proc: lldb.SBProcess, addr: int, err: lldb.SBError, source: str) -> int | None:
    if addr == 0:
        return None
    header = read_memory_safe(proc, addr, 8, err)
    if header is None or header[:8] != MAGIC5:
        return None
    if addr in _STATE["seen"]:
        return None
    _STATE["seen"].add(addr)
    _log(f"MAGIC5_{source} addr=0x{addr:x}")
    return addr


def check_registers(frame: lldb.SBFrame, proc: lldb.SBProcess, err: lldb.SBError) -> list[int]:
    found: list[int] = []
    for reg in ("x0", "x1", "x2"):
        rv = frame.FindRegister(reg)
        if not rv.IsValid():
            continue
        addr = rv.GetValueAsUnsigned()
        hit = check_addr_for_magic5(proc, addr, err, f"BP_{reg}")
        if hit is not None:
            found.append(hit)
    return found


def _bp_symbol_name(bp_loc: lldb.SBBreakpointLocation, frame: lldb.SBFrame) -> str:
    try:
        addr = bp_loc.GetAddress()
        if addr.IsValid():
            sym = addr.GetSymbol()
            if sym.IsValid():
                name = sym.GetName()
                if name:
                    return name
    except Exception:
        pass
    try:
        load = bp_loc.GetAddress().GetLoadAddress(frame.GetThread().GetProcess().GetTarget())
        return f"0x{load:x}"
    except Exception:
        return "?"


def bp_handler(frame: lldb.SBFrame, bp_loc: lldb.SBBreakpointLocation, extra_args, internal_dict) -> bool:
    _STATE["bp_hits"] += 1
    thread = frame.GetThread()
    proc = thread.GetProcess()
    err = lldb.SBError()
    sym = _bp_symbol_name(bp_loc, frame)
    try:
        addr = bp_loc.GetAddress().GetLoadAddress(proc.GetTarget())
    except Exception:
        addr = 0
    _log(f"BP_HIT #{_STATE['bp_hits']} sym={sym} at=0x{addr:x}")

    hits = check_registers(frame, proc, err)
    for h in hits:
        out = dump_dict5(proc, h, err)
        if out:
            _STATE["captured"].append(out)
            run_validate(out)
    return False


def _roam_migration_load_ranges(target: lldb.SBTarget) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for mod in target.module_iter():
        path = mod.file.fullpath or mod.file.GetFilename() or ""
        if "roam_migration" not in path:
            continue
        for sect in mod.section_iter():
            base = sect.GetLoadAddress(target)
            if base == lldb.LLDB_INVALID_ADDRESS:
                continue
            size = sect.GetByteSize()
            if size > 0:
                ranges.append((base, base + size))
    return ranges


def _addr_in_roam(load_addr: int, roam_ranges: list[tuple[int, int]]) -> bool:
    for start, end in roam_ranges:
        if start <= load_addr < end:
            return True
    return False


def _parse_lookup_line(line: str, default_name: str) -> tuple[int, str] | None:
    if "Address:" not in line or "roam_migration" not in line:
        return None
    idx = line.find("Address:")
    rest = line[idx + len("Address:"):].strip()
    token = rest.split()[0] if rest else ""
    if "[" not in token or "]" not in token:
        return None
    off = token.split("[", 1)[1].rstrip("]")
    if not off.startswith("0x"):
        return None
    try:
        load_addr = int(off, 16)
    except ValueError:
        return None
    sym_name = rest.split("(", 1)[-1].rstrip(")") if "(" in rest else default_name
    if "`" in sym_name:
        sym_name = sym_name.rsplit("`", 1)[-1]
    return load_addr, sym_name


def lookup_breakpoint_addresses(debugger: lldb.SBDebugger, target: lldb.SBTarget) -> list[tuple[int, str]]:
    addresses: list[tuple[int, str]] = []
    seen: set[int] = set()
    res = lldb.SBCommandReturnObject()
    ci = debugger.GetCommandInterpreter()
    roam_ranges = _roam_migration_load_ranges(target)
    scope = "roam_migration"

    def add_from_output(output: str, default_name: str) -> None:
        if len(addresses) >= BP_MAX:
            return
        for line in output.splitlines():
            parsed = _parse_lookup_line(line, default_name)
            if parsed is None:
                continue
            load_addr, sym_name = parsed
            if roam_ranges and not _addr_in_roam(load_addr, roam_ranges):
                continue
            if load_addr in seen:
                continue
            seen.add(load_addr)
            addresses.append((load_addr, sym_name))
            if len(addresses) >= BP_MAX:
                return

    for sym in BP_SYMBOL_NAMES:
        if len(addresses) >= BP_MAX:
            break
        ci.HandleCommand(f"image lookup -n {sym} {scope}", res)
        add_from_output(res.GetOutput() or "", sym)

    if len(addresses) < BP_MAX:
        ci.HandleCommand(f"image lookup -n decompress {scope}", res)
        for line in (res.GetOutput() or "").splitlines():
            if len(addresses) >= BP_MAX:
                break
            if "WCDB" not in line and "wcdb" not in line.lower():
                continue
            parsed = _parse_lookup_line(line, "decompress")
            if parsed is None:
                continue
            load_addr, sym_name = parsed
            if roam_ranges and not _addr_in_roam(load_addr, roam_ranges):
                continue
            if load_addr in seen:
                continue
            seen.add(load_addr)
            addresses.append((load_addr, sym_name))

    return addresses[:BP_MAX]


def setup_breakpoints(debugger: lldb.SBDebugger, target: lldb.SBTarget) -> int:
    if not ENABLE_BP:
        _log("BP_SKIP MIGRATION_ENABLE_BP=0 (default) — scan-only, no proc.Continue()")
        return 0

    addrs = lookup_breakpoint_addresses(debugger, target)
    count = 0
    for load_addr, sym_name in addrs:
        bp = target.BreakpointCreateByAddress(load_addr)
        bp.SetScriptCallbackFunction("_migration_dict5_scan_v6.bp_handler")
        bp.SetAutoContinue(True)
        count += 1
        _log(f"BP_SET 0x{load_addr:x} {sym_name}")
    if count == 0:
        _log("BP_NONE no ZSTD/WCDB decompress symbols in roam_migration")
    else:
        _log(f"BP_TOTAL={count}")
    return count


def maybe_continue(proc: lldb.SBProcess) -> None:
    """Resume process only in breakpoint mode; scan-only keeps process stopped."""
    if not ENABLE_BP:
        return
    if proc.GetState() == lldb.eStateStopped and proc.GetStopReason() != lldb.eStopReasonBreakpoint:
        proc.Continue()


_DICT_SIZE_MIN = 50_000
_DICT_SIZE_MAX = 200_000


def dump_dict5(proc: lldb.SBProcess, addr: int, err: lldb.SBError) -> str | None:
    data = read_memory_safe(proc, addr, DICT_SIZE, err)
    if data is None or len(data) < DICT_SIZE:
        _log(f"DUMP_FAIL addr=0x{addr:x} err={err.GetCString()}")
        return None

    actual_size = len(data)
    if not (_DICT_SIZE_MIN <= actual_size <= _DICT_SIZE_MAX):
        _log(f"DICT_SIZE_SANITY_FAIL addr=0x{addr:x} size={actual_size} "
             f"expected={_DICT_SIZE_MIN}..{_DICT_SIZE_MAX} — skipping")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_name = f"real_dict_5_{ts}.bin"
    out_path = os.path.join(EXPORT, out_name)
    with open(out_path, "wb") as f:
        f.write(data)

    link_path = os.path.join(EXPORT, "real_dict_5.bin")
    try:
        if os.path.islink(link_path) or os.path.exists(link_path):
            os.remove(link_path)
        os.symlink(out_name, link_path)
    except OSError as exc:
        _log(f"SYMLINK_WARN {exc}")

    did = struct.unpack("<I", data[4:8])[0] if len(data) >= 8 else -1
    _log(f"MAGIC5_HIT addr=0x{addr:x} dict_id={did} wrote={out_path}")
    print(f"CAPTURE_OK {out_path}", flush=True)
    return out_path


def run_validate(path: str) -> None:
    if not os.path.isfile(VALIDATE_SCRIPT):
        _log(f"VALIDATE_SKIP missing {VALIDATE_SCRIPT}")
        return
    try:
        proc = subprocess.run(
            [sys.executable, VALIDATE_SCRIPT, path],
            capture_output=True,
            text=True,
            timeout=120,
        )
        _log("--- validate_dict5.py on hit ---")
        for line in (proc.stdout + proc.stderr).splitlines():
            _log(line)
        if proc.returncode == 0:
            print("VALIDATE_OK", flush=True)
        else:
            print(f"VALIDATE_FAIL rc={proc.returncode}", flush=True)
    except Exception as exc:
        _log(f"VALIDATE_ERROR {exc}")


def migration_capture_90s_v6(
    debugger: lldb.SBDebugger,
    command: str,
    result: lldb.SBCommandReturnObject,
    internal_dict,
) -> None:
    proc = debugger.GetSelectedTarget().GetProcess()
    if not proc.IsValid():
        print("NO_PROCESS")
        return

    target = debugger.GetSelectedTarget()
    pid = proc.GetProcessID()
    open(LOG_PATH, "w").close()
    _STATE["seen"] = set()
    _STATE["captured"] = []
    _STATE["bp_hits"] = 0
    _STATE["phase2_idx"] = 0
    _STATE["phase2_offset"] = 0

    phase1_ranges = roam_migration_ranges(debugger)
    phase1_bytes = sum(e - s for s, e, _ in phase1_ranges)
    _STATE["phase2_regions"] = rw_memory_regions(proc, pid)
    phase2_bytes = sum(e - s for s, e, _ in _STATE["phase2_regions"])
    round_n = 0
    bp_count = 0

    _log(
        f"V6 app={APP_LABEL} path={APP_PATH or '?'} PID={pid} "
        f"SCAN_SECONDS={SCAN_SECONDS} INTERVAL={SCAN_INTERVAL}s "
        f"chunk={CHUNK_SIZE} progress_every={PROGRESS_EVERY} "
        f"phase1_sections={','.join(SCAN_SECTIONS)} phase2_max_region={MAX_REGION_PHASE2} "
        f"enable_bp={ENABLE_BP} phase1_ranges={len(phase1_ranges)} phase1_bytes={phase1_bytes} "
        f"phase2_ranges={len(_STATE['phase2_regions'])} phase2_bytes={phase2_bytes} "
        f"state={proc.GetState()} scan_while_stopped={not ENABLE_BP}"
    )
    for s, e, name in phase1_ranges:
        _log(f"PHASE1_RANGE {name} 0x{s:x}-0x{e:x} ({e - s} bytes)")
    for s, e, name in _STATE["phase2_regions"][:20]:
        _log(f"PHASE2_RANGE {name} 0x{s:x}-0x{e:x} ({e - s} bytes)")
    if len(_STATE["phase2_regions"]) > 20:
        _log(f"PHASE2_RANGE ... +{len(_STATE['phase2_regions']) - 20} more")

    try:
        bp_count = setup_breakpoints(debugger, target)
        err = lldb.SBError()
        start = time.time()

        if ENABLE_BP and proc.GetState() == lldb.eStateStopped:
            _log("BP_MODE Continue() to run until breakpoint")
            proc.Continue()

        while time.time() - start < SCAN_SECONDS:
            round_n += 1
            round_start = time.time()
            elapsed = int(round_start - start)
            round_deadline = min(round_start + SCAN_INTERVAL, start + SCAN_SECONDS)

            phase1_hits = scan_phase1(proc, phase1_ranges, err)
            phase2_hits, p2_regions = scan_phase2(proc, err, round_deadline)

            _log(
                f"ROUND={round_n} phase1_magic5={len(phase1_hits)} "
                f"phase2_magic5={len(phase2_hits)} phase2_regions={p2_regions} "
                f"phase2_cursor={_STATE['phase2_idx']} elapsed={elapsed}s"
            )

            for h in phase1_hits + phase2_hits:
                out = dump_dict5(proc, h, err)
                if out:
                    _STATE["captured"].append(out)
                    run_validate(out)

            maybe_continue(proc)

            remaining = SCAN_SECONDS - (time.time() - start)
            sleep_for = min(SCAN_INTERVAL, remaining)
            if sleep_for > 0:
                time.sleep(sleep_for)

    finally:
        _log(
            f"DONE rounds={round_n} captures={len(_STATE['captured'])} "
            f"bp_hits={_STATE['bp_hits']} breakpoints={bp_count} "
            f"phase2_cursor_final={_STATE['phase2_idx']}/{len(_STATE['phase2_regions'])}"
        )
        if _STATE["captured"]:
            print(f"TOTAL_CAPTURES={len(_STATE['captured'])}", flush=True)
        else:
            print("NO_CAPTURE", flush=True)


def __lldb_init_module(debugger: lldb.SBDebugger, internal_dict) -> None:
    debugger.HandleCommand(
        "command script add -f _migration_dict5_scan_v6.migration_capture_90s_v6 migration_capture_90s_v6"
    )
