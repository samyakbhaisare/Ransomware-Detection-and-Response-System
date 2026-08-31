from pathlib import Path
import sys

try:
    import PyPDF2
except ImportError as exc:
    raise SystemExit(f"PyPDF2 missing: {exc}")

base = Path(r'd:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\RDRS-CLI\docs')
for p in sorted(base.iterdir()):
    if p.suffix.lower() != '.pdf':
        continue
    print(f'=== {p.name} ===')
    reader = PyPDF2.PdfReader(str(p))
    print('pages', len(reader.pages))
    all_text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    lines = all_text.splitlines()
    keywords = ['F01','audit','runtime','status','lifecycle','startup','shutdown','health','STOPPED','STARTING','RUNNING','DEGRADED','SHUTTING_DOWN','FAILED']
    hits = []
    for idx, line in enumerate(lines, 1):
        low = line.lower()
        if any(k.lower() in low for k in keywords):
            hits.append(f'{idx}: {line[:400]}')
    if hits:
        print('\n'.join(hits[:200]))
    else:
        print(all_text[:4000])
    print('---END_DOC---\n')
