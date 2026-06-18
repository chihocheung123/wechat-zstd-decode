from pathlib import Path
#!/usr/bin/env python3
"""Non-interactive lldb: attach, BPs at roam_migration+offsets, continue 45s, log hits, dump on 0x256a20."""
import os
import re
import struct
import subprocess
import sys
import time

EXPORT = os.environ.get("EXPORT_DIR", str(Path(__import__("os").environ.get("WECHAT_ZSTD_WORKSPACE", str(Path(__file__).resolve().parent.parent / "data")))))
RUN_SEC = int(os.environ.get("LLDB_RUN_SEC", "45"))
LOG = os.path.join(EXPORT, "lldb_breakpoint_hits.log")


def find_pid():
    r = subprocess.run(
        ["pgrep", "-fl", "WeChat-Debug.app/Contents/MacOS/WeChat"],
        capture_output=True,
        text=True,
    )
    for line in r.stdout.splitlines():
        if "WeChatAppEx" in line or "crashpad" in line:
            continue
        return int(line.split()[0])
    return None


def get_slide(pid):
    cmd = f"attach -p {pid}\nimage list -o roam_migration\ndetach\nquit\n"
    p = subprocess.run(["lldb", "-b"], input=cmd, capture_output=True, text=True, timeout=120)
    out = p.stdout + p.stderr
    slide = None
    for line in out.splitlines():
        if line.strip().startswith('[') and '0x' in line:
            hexes = re.findall(r"0x[0-9a-fA-F]+", line)
            if hexes:
                slide = int(hexes[0], 16)
                break
    return slide, out


def main():
    pid = find_pid()
    if not pid:
        print("no pid", file=sys.stderr)
        return 2
    slide, attach_out = get_slide(pid)
    with open(LOG, "a") as log:
        log.write(f"\n=== batch {time.strftime('%F %T')} pid={pid} ===\n")
        log.write(attach_out[-2000:] + "\n")
    if slide is None:
        with open(LOG, "a") as log:
            log.write("ERROR: roam_migration slide missing\n")
        return 3

    offsets = [
        ("udf_dict_resolve", 0x256A20),
        ("udf_alt_1e78e8", 0x1E78E8),
        ("udf_entry_226728", 0x226728),
    ]
    dump_cmds = []
    for off in (0, 8, 0x10, 0x18):
        dump_cmds.append(
            f'memory read --binary --outfile {EXPORT}/dict_5_from_context_resolve_x0_{off:x}.bin --count 112640 *(uint64_t*)($x0+{off})'
        )
    dump_cmds += [
        f"memory read --binary --outfile {EXPORT}/dict_5_from_context_resolve_x1.bin --count 112640 $x1",
        f"memory read --binary --outfile {EXPORT}/dict_5_from_context_resolve_x2.bin --count 112640 $x2",
    ]
    resolve_cmds = [
        'register read x0 x1 x2 x3 x4 x5 x6',
        "memory read -c 64 $x1",
        f'script open("{LOG}","a").write("HIT udf_dict_resolve pc=0x{{frame.pc:x}} x0=0x{{frame.reg[x0]:x}} x1=0x{{frame.reg[x1]:x}} x2=0x{{frame.reg[x2]:x}}\\n")',
    ] + dump_cmds + ["continue"]

    lines = [f"attach -p {pid}", "settings set target.process.stop-on-sharedlibrary-events false"]
    for name, off in offsets:
        addr = slide + off
        lines.append(f"breakpoint set -a 0x{addr:x} -N {name}")
    # resolve: dump; others: log+continue
    for i, cmd in enumerate(resolve_cmds):
        o = f' -o "{cmd.replace(chr(34), chr(92)+chr(34))}"' if '"' in cmd else f' -o "{cmd}"'
        if i == 0:
            lines.append("breakpoint command add -N udf_dict_resolve" + "".join(
                (f' -o "{c}"' for c in resolve_cmds)
            ))
        pass
    # build breakpoint command add manually
    bca = "breakpoint command add -N udf_dict_resolve"
    for c in resolve_cmds:
        bca += ' -o "' + c.replace('"', '\\"') + '"'
    lines.append(bca)
    for name, _ in offsets[1:]:
        lines.append(
            f'breakpoint command add -N {name} -o "register read x0 x1 x2 x3" -o "script open(\\"{LOG}\\",\\"a\\").write(\\"HIT {name}\\\\n\\")" -o "continue"'
        )
    lines.append("continue")
    lines.append(f"script import time; time.sleep({RUN_SEC})")
    lines.append("process detach")
    lines.append("quit")

    script = "\n".join(lines) + "\n"
    with open(os.path.join(EXPORT, "lldb_batch_commands.txt"), "w") as f:
        f.write(script)
    try:
        p = subprocess.run(
            ["lldb", "-b"],
            input=script,
            capture_output=True,
            text=True,
            timeout=RUN_SEC + 90,
        )
    except subprocess.TimeoutExpired:
        with open(LOG, "a") as log:
            log.write("lldb subprocess timeout\n")
        return 4
    with open(LOG, "a") as log:
        log.write(f"slide=0x{slide:x}\n")
        log.write((p.stdout + p.stderr)[-12000:] + "\n")
    print("slide=0x%x log=%s" % (slide, LOG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
