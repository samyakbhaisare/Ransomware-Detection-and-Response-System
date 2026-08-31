from pathlib import Path
import PyPDF2

base = Path(r'd:\Samyak\Projects\Internmo\Project 1 RDRS\RDRS\RDRS-CLI\docs')
for p in sorted(base.iterdir()):
    if p.suffix.lower() != '.pdf':
        continue
    print(f'=== {p.name} ===')
    reader = PyPDF2.PdfReader(str(p))
    print('pages', len(reader.pages))
    text = '\n'.join(page.extract_text() or '' for page in reader.pages)
    lines = text.splitlines()
    for i, line in enumerate(lines[:200], 1):
        if any(k.lower() in line.lower() for k in ['f01','audit','runtime','status','lifecycle','startup','shutdown','health','state','signal','stopped','starting','running','degraded','shutting_down','failed']):
            print(f'{i}: {line[:500]}')
    print('---END---\n')
