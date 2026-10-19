from pathlib import Path
import html, re, json

root = Path.cwd()
plan = (root/'docs/audit-fix-plan-2026-09-06.md').read_text()
taste = (root/'taste.md').read_text()
sections = {m.group(1): m.group(2).strip() for m in re.finditer(r'^## ([^\n]+)\n(.*?)(?=^## |\Z)', plan, re.M|re.S)}
code_blocks = []
local_links = {
    'audit-2026-09-06.md':'audit-source.md',
    'architecture/decisions/0002-target-portfolio-boundary.md':'portfolio-boundary.md',
    '../taste.md':'#taste',
    'docs/audit-fix-plan-2026-09-06.md':'#packages',
}
for source, dest in [('docs/audit-2026-09-06.md','audit-source.md'),('docs/architecture/decisions/0002-target-portfolio-boundary.md','portfolio-boundary.md')]:
    (root/'.lavish'/dest).write_text((root/source).read_text())

def inline(text):
    text = html.escape(text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', lambda m: '<a class="link" href="'+html.escape(local_links.get(html.unescape(m[2]),html.unescape(m[2])),quote=True)+'">'+m[1]+'</a>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    return re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)

def render(md):
    lines=md.splitlines(); out=[]; i=0
    while i<len(lines):
        line=lines[i]
        if not line.strip(): i+=1; continue
        if line.startswith('```'):
            i+=1; block=[]
            while i<len(lines) and not lines[i].startswith('```'): block.append(lines[i]); i+=1
            ident=f'code-{len(code_blocks)}'
            code_blocks.append({'id':ident,'contents':'\n'.join(block)+'\n'})
            out.append(f'<div class="my-5"><p class="text-sm mb-2 text-base-content/65">Illustrative pseudocode · proposed behavior</p><div id="{ident}" class="code-surface"></div></div>');i+=1;continue
        if line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                cells=[x.strip() for x in lines[i].strip('|').split('|')]
                if not all(re.fullmatch(r'[-: ]+', x) for x in cells): rows.append(cells)
                i+=1
            out.append('<div class="my-5 overflow-x-auto"><table class="table table-zebra"><thead><tr>'+''.join('<th>'+inline(x)+'</th>' for x in rows[0])+'</tr></thead><tbody>')
            for row in rows[1:]: out.append('<tr>'+''.join('<td>'+inline(x)+'</td>' for x in row)+'</tr>')
            out.append('</tbody></table></div>');continue
        match=re.match(r'^(\d+\. |[-] )',line)
        if match:
            numbered=line[0].isdigit(); tag='ol' if numbered else 'ul'
            out.append(f'<{tag} class="my-4 pl-6 space-y-3 '+('list-decimal' if numbered else 'list-disc')+'">')
            while i<len(lines) and re.match(r'^(\d+\. |[-] )',lines[i]):
                out.append('<li>'+inline(re.sub(r'^(\d+\. |[-] )','',lines[i]))+'</li>'); i+=1
            out.append(f'</{tag}>');continue
        if line.startswith('#'):
            out.append('<h3 class="mt-5 mb-3 text-xl font-bold">'+inline(line.lstrip('# '))+'</h3>');i+=1;continue
        para=[]
        while i<len(lines) and lines[i].strip() and not re.match(r'^(#|\||```|\d+\. |[-] )',lines[i]): para.append(lines[i]);i+=1
        out.append('<p class="my-3 leading-7">'+inline(' '.join(para))+'</p>')
    return ''.join(out)

phases=[]
for key,body in sections.items():
    if re.match(r'P\d:',key):
        p=key[:2]
        phases.append(f'<details class="package collapse collapse-arrow bg-base-100 border border-base-content/10" id="{p}"><summary class="collapse-title text-xl font-semibold">{html.escape(key)}</summary><div class="collapse-content prose-body">'+render(body)+'</div></details>')
coverage = render(sections['Coverage ledger: all 33 findings'])
coverage = coverage.replace('<tbody>','<tbody id="finding-rows">',1)
patterns=render(sections['Recurring mistakes and prevention'])
corrections=render(sections['Corrections to the proposed fixes'])
delivery=render(sections['Delivery and verification'])
summary=render(sections['Outcome and order'])
new_findings=[('C1','Historical input leakage','The data gateway can stamp a fresh response with a past virtual time. Separate historical research from live collection.'),('C2','Synthetic fill timing','Research and fills share a made-up instant. Version next-session execution with actual decision-ready time.'),('C3','Spend cap is incomplete','The current limit counts whole graph runs. Reserve actual model-call cost across attempts.'),('C4','Memory measurement is misleading','Cumulative child memory does not identify one job’s peak. Measure concurrent workload before shrinking machines.')]
extra=''.join('<article class="card bg-base-100 border border-base-content/10"><div class="card-body"><span class="badge badge-warning badge-outline">'+n+'</span><h3 class="card-title">'+t+'</h3><p>'+d+'</p></div></article>' for n,t,d in new_findings)
page='''<!doctype html>
<html lang="en" data-theme="luxury"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>TradingAgents · Audit repair plan</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@5.5.19/daisyui.css"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/daisyui@5.5.19/themes.css"><script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4.2.4/dist/index.global.js"></script>
<style>*{box-sizing:border-box}html{scroll-behavior:smooth}body{background:#12100e;color:#eee8dc}:where(.grid,.flex)>*{min-width:0}:where(p,h1,h2,h3,li,td,th,code){overflow-wrap:anywhere}code{font-size:.9em}details{scroll-margin-top:1rem}.eyebrow{letter-spacing:.14em;text-transform:uppercase;font-size:.72rem;opacity:.7}.prose-body{max-width:100%}.table td{vertical-align:top;white-space:normal}.code-surface{min-width:0;max-width:100%;overflow:hidden}summary{cursor:pointer}@media print{button,input,select,.controls{display:none!important}body{background:white;color:black}details::details-content{content-visibility:visible!important}details>*{display:block!important}}</style></head>
<body class="min-h-screen bg-base-200 text-base-content"><main class="mx-auto max-w-7xl p-5 sm:p-8 lg:p-12">
<header class="rounded-box bg-base-100 p-7 lg:p-10 border-t-4 border-primary"><p class="eyebrow">TradingAgents · 6 September 2026 · baseline afbd64f</p><h1 class="mt-3 text-4xl sm:text-5xl font-black tracking-tight">Fix every finding.<br>Prove the whole system.</h1><p class="mt-5 max-w-3xl text-lg leading-8 text-base-content/75">The main mistake was calling the pipeline complete after testing its parts. This plan gives every fix an outcome to prove, and puts those lessons into the repository’s review guidance.</p><div class="mt-6 flex flex-wrap gap-3"><span class="badge badge-warning">Proposed · no runtime changes</span><span class="badge badge-outline">33 audit findings</span><span class="badge badge-outline">4 additional concerns</span><span class="badge badge-outline">7 work packages</span></div><nav class="mt-7 flex flex-wrap gap-2"><a href="#order" class="btn btn-sm btn-primary">Fix order</a><a href="#coverage" class="btn btn-sm btn-outline">All 33 findings</a><a href="#taste" class="btn btn-sm btn-outline">Engineering taste</a><a href="#packages" class="btn btn-sm btn-outline">Implementation details</a></nav></header>
<section class="mt-7 alert alert-warning"><p><strong>A seal is not proof of a valid backtest.</strong> Existing days need a separate replay and temporal-validity assessment. Preserve their records; do not rewrite them to make the checks pass.</p></section>
<section id="order" class="mt-10 rounded-box bg-base-100 p-6 lg:p-8"><p class="eyebrow">Recommended order</p><h2 class="mt-2 text-2xl font-bold">Recoverability → trust → efficiency</h2>'''+summary+'''</section>
<section class="mt-10"><p class="eyebrow">Found while checking the plan</p><h2 class="mt-2 mb-5 text-2xl font-bold">Four more assumptions to fix</h2><div class="grid gap-4 md:grid-cols-2">'''+extra+'''</div></section>
<section id="packages" class="mt-10"><div class="flex flex-wrap items-end justify-between gap-4"><div><p class="eyebrow">Reviewable work packages</p><h2 class="mt-2 text-2xl font-bold">Changes, tradeoffs, and completion gates</h2></div><div class="flex gap-2 controls"><button id="expand" class="btn btn-sm btn-outline">Expand all</button><button id="collapse" class="btn btn-sm btn-ghost">Collapse all</button></div></div><div class="mt-5 space-y-4">'''+''.join(phases)+'''</div></section>
<section id="coverage" class="mt-10 rounded-box bg-base-100 p-6 lg:p-8"><p class="eyebrow">No finding left without a home</p><h2 class="mt-2 text-2xl font-bold">The 33-item coverage ledger</h2><div class="mt-5 flex flex-wrap gap-3 controls"><label class="input flex-1 min-w-48">Find<input id="query" type="search" placeholder="Search findings or proof" aria-label="Search audit findings"></label><select id="phase" class="select w-auto" aria-label="Filter by work package"><option value="">All packages</option>P0</option><option>P1</option><option>P2</option><option>P3</option><option>P4</option><option>P5</option></select><span id="count" class="self-center text-sm" aria-live="polite">33 findings</span></div>'''+coverage+'''</section>
<section class="mt-10 rounded-box bg-base-100 p-6 lg:p-8"><p class="eyebrow">Root causes</p><h2 class="mt-2 text-2xl font-bold">What keeps going wrong</h2>'''+patterns+'''</section>
<section id="taste" class="mt-10 rounded-box bg-base-100 p-6 lg:p-8"><p class="eyebrow">Now linked from AGENTS.md</p><h2 class="mt-2 text-2xl font-bold">taste.md</h2>'''+render('\n'.join(taste.splitlines()[2:]))+'''</section>
<details class="mt-8 collapse collapse-arrow bg-base-100"><summary class="collapse-title text-xl font-semibold">Corrections to the original proposed fixes</summary><div class="collapse-content">'''+corrections+'''</div></details>
<details class="mt-4 collapse collapse-arrow bg-base-100"><summary class="collapse-title text-xl font-semibold">Delivery and verification</summary><div class="collapse-content">'''+delivery+'''</div></details>
<footer class="mt-10 text-sm text-base-content/60 leading-7"><p>Source of truth: docs/audit-fix-plan-2026-09-06.md · Current status: doing.md · Review rules: taste.md</p><p>Design follows this repository’s existing architecture review pages: DaisyUI luxury with Tailwind. Runtime and production state were not changed during this planning pass.</p></footer>
</main><script>
const rows=[...document.querySelectorAll('#finding-rows tr')];
function filter(){const q=document.getElementById('query').value.toLowerCase(),p=document.getElementById('phase').value;let n=0;for(const r of rows){r.hidden=!(r.textContent.toLowerCase().includes(q)&&(!p||r.cells[2].textContent===p));if(!r.hidden)n++}document.getElementById('count').textContent=n+' / 33 findings'}
document.getElementById('query').addEventListener('input',filter);document.getElementById('phase').addEventListener('change',filter);
document.getElementById('expand').onclick=()=>document.querySelectorAll('details.package').forEach(d=>d.open=true);document.getElementById('collapse').onclick=()=>document.querySelectorAll('details.package').forEach(d=>d.open=false);
</script><script type="module">
import { File } from 'https://esm.sh/@pierre/diffs@1.2.10?bundle';
const snippets='''+json.dumps(code_blocks).replace('<','\\u003c')+''';
for(const s of snippets){new File({theme:{light:'github-light',dark:'github-dark'},themeType:'dark',overflow:'wrap'}).render({containerWrapper:document.getElementById(s.id),file:{name:'proposed-behavior.py',contents:s.contents}})}
</script></body></html>'''
(root/'.lavish/audit-fix-plan-2026-09-06.html').write_text(page)
print(json.dumps({'phases':len(phases),'findings':len(re.findall(r'^\| \d+ \|',plan,re.M)),'pseudocode_blocks':len(code_blocks),'html_bytes':len(page.encode())}))
