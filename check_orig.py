import subprocess, sys
out = subprocess.run(['git', 'show', 'HEAD:scalp_engine.py'], capture_output=True, text=True, encoding='utf-8')
lines = out.stdout.splitlines(keepends=True)
for i, l in enumerate(lines):
    if '_vol_src_dominant = "binance_ws"' in l:
        with open('_orig_ctx.txt','w',encoding='utf-8') as f:
            for j in range(i-5, i+5):
                f.write(f'{j+1} {repr(lines[j])}\n')
        break
