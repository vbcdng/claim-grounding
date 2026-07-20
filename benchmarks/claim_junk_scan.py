import json, glob, os, re

def classify(c):
    t=c.strip()
    w=t.split()
    tags=[]
    # bare statistic / p-value with no comparison subject
    if re.match(r'^(A |The )?P\s?[<>=]', t) and len(w)<=6:
        tags.append('bare-stat')
    if re.match(r'^(A|An|The)\s+P[<>=]?\s?[0-9<>=.]+\s+value was observed', t):
        tags.append('bare-stat')
    # stat-row fragment: "the intercept was X" / "the slope was X" / "r = X"
    if re.search(r'\b(the intercept was|the slope was)\b', t, re.I) or re.match(r'^For the .+ group, r ?=', t):
        tags.append('stat-fragment')
    # figure/table legend or caption
    if re.search(r'common superscript|open symbol|closed symbol|are plotted|is plotted|line of identity|values in the same row|values? in the same column|shown in (the )?(figure|table)|see (figure|table)|data are presented as mean|are presented as mean ?[±\+]', t, re.I):
        tags.append('caption/legend')
    # dangling/truncated: ends with a function word (no terminal predicate)
    last=re.sub(r'[).\]"]+$','',t).split()[-1].lower() if w else ''
    if last in {'of','the','and','that','to','for','with','in','as','by','a','an','or','on','from','than','between','was','were','is','are'}:
        tags.append('truncated')
    # acknowledgments / funding / disclosure boilerplate
    if re.search(r'supported in part by|wish to express|acknowledge the|gratitude to|conflicts? of interest|disclosure questionnaire|expert peer review|express permission of|approved by the .+ (committee|association)', t, re.I):
        tags.append('boilerplate')
    # too short
    if len(w)<4:
        tags.append('too-short')
    return tags

def scan(d):
    out={}
    for c in d.get('claims',[]):
        tg=classify(c.get('text',''))
        for t in tg:
            out.setdefault(t,[]).append(c['text'])
    return out

print('=== FLASH-LITE (all 21 sources) ===')
tot={}; ncl=0
rows=[]
for f in sorted(glob.glob('data/eggs_run/source_claims/*.json')):
    d=json.load(open(f))
    key=d.get('key') or os.path.basename(f)
    n=len(d.get('claims') or [])
    ncl+=n
    s=scan(d)
    flagged=sum(len(v) for v in s.values())
    rows.append((key,n,flagged,s))
    for k,v in s.items(): tot.setdefault(k,[]).extend(v)
rows.sort(key=lambda r:-r[2])
for key,n,fl,s in rows:
    if fl: print(f'{key:20} {n:4} claims  {fl:3} flagged  {dict((k,len(v)) for k,v in s.items())}')
print(f'\nTOTAL flash: {ncl} claims, {sum(len(v) for v in tot.values())} flagged')
for k,v in sorted(tot.items(),key=lambda x:-len(x[1])):
    print(f'  {k}: {len(v)}')
