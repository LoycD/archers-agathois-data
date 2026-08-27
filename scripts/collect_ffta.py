#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, os, re, subprocess, tempfile, time, unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
ARCHERS_FILE=ROOT/'config/archers.json'
RESULTS_FILE=ROOT/'data/results.json'
PROCESSED_FILE=ROOT/'data/processed.json'
CALENDAR_URL='https://www.ffta.fr/competitions'
HEADERS={'User-Agent':'ArchersAgathoisResultsCollector/1.0 (+https://github.com/LoycD/archers-agathois-data)','Accept-Language':'fr-FR,fr;q=0.9'}
DISCIPLINE_BY_PREFIX={'S':'18m','T':'tae','N':'nature','3':'3d','C':'campagne','B':'beursault'}
REQUEST_DELAY=.35

def load_json(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return default

def save_json(path, data):
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def norm(s):
    s=unicodedata.normalize('NFKD',s or '')
    s=''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+',' ',s).strip()

def get(url, timeout=25):
    r=requests.get(url,headers=HEADERS,timeout=timeout,allow_redirects=True)
    r.raise_for_status()
    return r

def calendar_page_url(start,end,page):
    return f'{CALENDAR_URL}?discipline=All&inter=All&start={start.isoformat()}&end={end.isoformat()}&sort_by=start&sort_order=ASC&page={page}'

def discover_result_links(start,end,max_pages=50):
    found=[]; seen=set()
    for page in range(max_pages):
        url=calendar_page_url(start,end,page)
        print(f'[calendrier] page {page}')
        soup=BeautifulSoup(get(url).text,'html.parser')
        page_links=[]
        for a in soup.find_all('a',href=True):
            label=norm(a.get_text(' ',strip=True)).lower()
            href=urljoin(url,a['href'])
            if 'resultat' not in label: continue
            if (urlparse(href).hostname or '').lower()!='extranet.ffta.fr': continue
            if '/pdfresultats/' not in href.lower() and not href.lower().endswith('.pdf'):
                try: href=get(href).url
                except Exception: continue
            if href not in seen:
                seen.add(href); page_links.append(href); found.append(href)
        print(f'  -> {len(page_links)} lien(s)')
        if page>0 and not page_links: break
        time.sleep(REQUEST_DELAY)
    return found

def pdf_to_text(pdf_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        pdf=Path(tmp)/'r.pdf'; txt=Path(tmp)/'r.txt'
        pdf.write_bytes(pdf_bytes)
        p=subprocess.run(['pdftotext','-layout','-enc','UTF-8',str(pdf),str(txt)],capture_output=True,text=True)
        if p.returncode: raise RuntimeError(p.stderr.strip() or 'pdftotext a échoué')
        return txt.read_text(encoding='utf-8',errors='replace')

def parse_header(text,pdf_url):
    lines=[norm(x) for x in text.splitlines() if norm(x)]
    head=' '.join(lines[:30])
    m=re.search(r'\ble\s+(\d{2})/(\d{2})/(\d{4})\b',head,re.I)
    date_iso=f'{m.group(3)}-{m.group(2)}-{m.group(1)}' if m else None
    title=''; place=''
    if m:
        before=re.sub(r'^Classement Officiel\s*','',head[:m.start()].strip(' -'),flags=re.I)
        chunks=[c.strip() for c in before.split(' - ') if c.strip()]
        if chunks: place=chunks[-1]
        if len(chunks)>=2: title=' - '.join(chunks[:-1])
        elif chunks: title=chunks[0]
    filename=Path(urlparse(pdf_url).path).name
    prefix=filename.split('_',1)[0].upper()
    return {'date':date_iso,'competition':title or 'Concours FFTA','place':place,'discipline':DISCIPLINE_BY_PREFIX.get(prefix,'autre'),'source_url':pdf_url}

def numeric_tokens_after_category(line,licence):
    pos=line.upper().find(licence.upper())
    if pos<0: return None,[]
    tail=line[pos+len(licence):]
    m=re.search(r'\b((?:U\d+|S\d)[HF][A-Z]{2})\b',tail.upper())
    if not m: return None,[]
    nums=[int(x) for x in re.findall(r'(?<![\w])(\d{1,4})(?![\w])',tail[m.end():])]
    return m.group(1),nums

def detect_rank(lines,idx,name):
    m=re.match(r'^\s*(\d{1,4})\s+',lines[idx])
    if m: return int(m.group(1))
    surname=norm(name).split()[-1].lower() if name else ''
    for j in range(max(0,idx-3),idx):
        mm=re.match(r'^\s*(\d{1,4})\s+',lines[j])
        if mm and (not surname or surname in norm(lines[j]).lower()): return int(mm.group(1))
    return None

def parse_archer_result(text,pdf_url,archer):
    licence=archer['licence'].upper(); lines=text.splitlines(); header=parse_header(text,pdf_url); out=[]
    for i,line in enumerate(lines):
        if licence not in line.upper(): continue
        category,nums=numeric_tokens_after_category(line,licence)
        if not category or len(nums)<3: continue
        s1,s2,total=nums[0],nums[1],nums[2]
        if total<=0: continue
        dep=1+sum(1 for r in out if r['licence']==licence)
        row={'id':'','licence':licence,'name':archer.get('name',''),'date':header['date'],'competition':header['competition'],'place':header['place'],'discipline':header['discipline'],'category':category,'departure':dep,'series':[s1,s2],'score':total,'rank':detect_rank(lines,i,archer.get('name','')),'source':'ffta','source_url':pdf_url}
        key='|'.join([licence,row['date'] or '',row['discipline'],str(total),str(dep),Path(urlparse(pdf_url).path).name])
        row['id']=hashlib.sha256(key.encode()).hexdigest()[:24]
        out.append(row)
    return out

def merge_results(existing,incoming):
    by={r.get('id'):r for r in existing if r.get('id')}; added=0
    for r in incoming:
        if r['id'] not in by: added+=1
        by[r['id']]=r
    merged=list(by.values())
    merged.sort(key=lambda r:(r.get('date') or '',r.get('licence') or '',r.get('departure') or 0),reverse=True)
    return merged,added

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--days-back',type=int,default=int(os.getenv('DAYS_BACK','45')))
    ap.add_argument('--force',action='store_true')
    args=ap.parse_args()
    archers=load_json(ARCHERS_FILE,[])
    state=load_json(PROCESSED_FILE,{'version':1,'processed_urls':[]})
    processed=set(state.get('processed_urls',[]))
    doc=load_json(RESULTS_FILE,{'version':1,'updated_at':None,'results':[]})
    end=date.today(); start=end-timedelta(days=max(1,args.days_back))
    links=discover_result_links(start,end)
    incoming=[]; newly=[]
    for n,url in enumerate(links,1):
        if url in processed and not args.force: continue
        print(f'[{n}/{len(links)}] {url}')
        try:
            r=get(url,35)
            if 'pdf' not in (r.headers.get('content-type') or '').lower() and not r.url.lower().endswith('.pdf'):
                newly.append(url); continue
            text=pdf_to_text(r.content); hits=0; upper=text.upper()
            for a in archers:
                if a['licence'].upper() not in upper: continue
                rows=parse_archer_result(text,r.url,a); incoming.extend(rows); hits+=len(rows)
            print(f'  -> {hits} résultat(s) Archers Agathois')
            newly.append(url)
        except Exception as exc:
            print(f'  !! erreur: {exc}')
        time.sleep(REQUEST_DELAY)
    merged,added=merge_results(doc.get('results',[]),incoming)
    now=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    save_json(RESULTS_FILE,{'version':1,'updated_at':now,'source':'FFTA official competition result PDFs','results':merged})
    processed.update(newly)
    save_json(PROCESSED_FILE,{'version':1,'updated_at':now,'processed_urls':sorted(processed)})
    print(f'Terminé: {len(incoming)} trouvé(s), {added} nouveau(x), {len(merged)} total.')

if __name__=='__main__':
    main()
