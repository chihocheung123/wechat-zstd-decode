#!/bin/bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="${WECHAT_ZSTD_WORKSPACE:-$REPO_ROOT/data}"
mkdir -p "$WORKSPACE"
set -euo pipefail
EXPORT="$WORKSPACE"
PID=$(pgrep -fl 'WeChat-Debug.app/Contents/MacOS/WeChat' | grep -v WeChatAppEx | grep -v crashpad | awk 'NR==1{print $1}')
if [[ -z "${PID:-}" ]]; then echo "No WeChat-Debug PID"; exit 1; fi
export WECHAT_PID="$PID" EXPORT_DIR="$EXPORT" LLDB_BATCH=1 LLDB_RUN_SEC=45
echo "batch lldb pid=$PID for ${LLDB_RUN_SEC}s" | tee -a "$EXPORT/lldb_breakpoint_hits.log"
# Python-driven lldb batch (more reliable than partial script above)
python3 << 'PY'
import os, subprocess, time, re, struct, sys
export = os.environ['EXPORT_DIR']
pid = int(os.environ['WECHAT_PID'])
log = open(os.path.join(export, 'lldb_breakpoint_hits.log'), 'a')
sec = int(os.environ.get('LLDB_RUN_SEC', '45'))

lldb_cmds = f'''
attach -p {pid}
image list -o roam_migration
settings set target.process.stop-on-sharedlibrary-events false
'''
proc = subprocess.run(['lldb', '-b'], input=lldb_cmds, text=True, capture_output=True)
out = proc.stdout + proc.stderr
log.write(out[-4000:] + '\n')
slide = None
for line in out.splitlines():
    if 'roam_migration' in line:
        m = re.findall(r'0x[0-9a-fA-F]+', line)
        if m:
            slide = int(m[0], 16)
            break
if slide is None:
    log.write('FAILED: no slide\n'); sys.exit(2)
offs = [('udf_dict_resolve',0x256a20),('udf_alt_1e78e8',0x1e78e8),('udf_entry_226728',0x226728)]
bp_lines = []
for name, off in offs:
    addr = slide + off
    bp_lines.append(f'breakpoint set -a 0x{addr:x} -N {name}')
    bp_lines.append(f'breakpoint command add -N {name} -o "script lldb.debugger.HandleCommand(\\"register read x0 x1 x2 x3 x4 x5 x6\\")" -o "script lldb.debugger.HandleCommand(\\"memory read -c 64 $x1\\")" -o "script open(\\"{log.name}\\",\\"a\\").write(\\"HIT {name} pc=0x{{frame.pc:x}}\\\\n\\")" -o "continue"')
script_body = '\n'.join(bp_lines) + f'\ncontinue\nprocess detach\nquit\n'
proc2 = subprocess.Popen(['lldb', '-b'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
stdout, _ = proc2.communicate(input=f'attach -p {pid}\n' + script_body, timeout=sec+30)
log.write(stdout[-8000:])
print('lldb batch done, slide=0x%x' % slide)
PY
