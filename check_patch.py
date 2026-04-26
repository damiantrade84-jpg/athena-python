with open('scalp_engine.py','r',encoding='utf-8') as f:
    lines=f.readlines()
for i,l in enumerate(lines):
    if '_funnel["fail_reasons"].append(' in l:
        print(i+1, repr(l.strip()))
