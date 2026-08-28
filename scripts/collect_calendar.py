#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, re, time, unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "calendar.json"
BASE = "https://www.ffta.fr/competitions"
DEPARTMENTS = ("11", "30", "34", "66")
ALLOWED = {"18m", "tae", "taei", "taen", "nature", "3d", "campagne"}
HEADERS = {
    "User-Agent": "ArchersAgathoisCalendarCollector/1.2 (+https://github.com/LoycD/archers-agathois-data)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
REQUEST_DELAY = 0.20


def norm(v: str) -> str:
    v = unicodedata.normalize("NFKD", v or "")
    v = "".join(c for c in v if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", v).strip()


def get(url: str, timeout: int = 30):
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status(); return r


def iso_date(fr: str, year: int | None = None):
    months = {"janvier":1,"fevrier":2,"mars":3,"avril":4,"mai":5,"juin":6,"juillet":7,"aout":8,"septembre":9,"octobre":10,"novembre":11,"decembre":12}
    s = norm(fr).lower(); m = re.search(r"(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?", s)
    if not m or m.group(2) not in months: return None
    y = int(m.group(3)) if m.group(3) else year
    return date(y, months[m.group(2)], int(m.group(1))).isoformat() if y else None


def parse_date_label(label: str):
    s = norm(label)
    m = re.search(r"Du\s+(\d{1,2})(?:\s+([A-Za-zÀ-ÿ]+))?\s+au\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})", s, re.I)
    if m:
        y = int(m.group(5)); sm = m.group(2) or m.group(4)
        return iso_date(f"{m.group(1)} {sm} {y}"), iso_date(f"{m.group(3)} {m.group(4)} {y}")
    m = re.search(r"(?:Le|Du)\s+(\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2})", s, re.I)
    if not m: return None, None
    d = iso_date(m.group(1)); return d, d


def classify(text: str) -> str:
    n = norm(text).lower()
    if "para" in n: return "autre"
    if "18m" in n or "18 m" in n or "tir en salle" in n: return "18m"
    if "3d" in n: return "3d"
    if "nature" in n: return "nature"
    if "campagne" in n: return "campagne"
    if "tae international" in n or "taei" in n: return "taei"
    if "tae national" in n or "taen" in n: return "taen"
    if "exterieur" in n or "extérieur" in n or re.search(r"\btae\b", n): return "tae"
    return "autre"


def stable_id(detail_url, title, start_date):
    q = parse_qs(urlparse(detail_url).query)
    for key in ("id", "competition", "concours"):
        if q.get(key): return str(q[key][0])
    m = re.search(r"/epreuve/(\d+)", detail_url)
    if m: return m.group(1)
    return hashlib.sha256(f"{detail_url}|{title}|{start_date}".encode()).hexdigest()[:20]


def page_url(start, end, dep, page):
    params = [("dep[]", dep),("discipline","All"),("inter","All"),("search",""),("univers","All"),("start",start.isoformat()),("end",end.isoformat()),("sort_by","start"),("sort_order","ASC"),("page",str(page))]
    return BASE + "?" + urlencode(params)


def is_mandate_href(href):
    h=(href or "").lower()
    return "/medias/documents_epreuves/" in h or "document_epreuve" in h or "mandat" in h


def mandate_from_soup(soup, base_url):
    for a in soup.find_all("a", href=True):
        lab = " ".join([norm(a.get_text(" ", strip=True)), norm(a.get("aria-label","")), norm(a.get("title",""))]).lower()
        if "mandat" in lab or is_mandate_href(a["href"]):
            return urljoin(base_url, a["href"])
    return ""


def nearest_card(detail_link):
    node = detail_link
    for _ in range(10):
        node = node.parent
        if node is None: break
        txt = norm(node.get_text(" ", strip=True))
        h2 = node.find("h2")
        if h2 and re.search(r"(?:Le|Du)\s+\d", txt, re.I):
            # On veut le plus petit bloc correspondant à UNE compétition.
            if len(node.find_all("h2")) == 1:
                return node
    return None


def extract_cards(html, dep):
    soup = BeautifulSoup(html, "html.parser")
    rows=[]
    detail_links=[]
    for a in soup.find_all("a", href=True):
        lab = norm(a.get_text(" ", strip=True)).lower()
        if lab in ("detail", "détail"):
            detail_links.append(a)

    for detail_link in detail_links:
        card = nearest_card(detail_link)
        if card is None: continue
        h2 = card.find("h2")
        if h2 is None: continue
        title = norm(h2.get_text(" ", strip=True))
        detail = urljoin(BASE, detail_link["href"])
        text = norm(card.get_text(" ", strip=True))
        start_date,end_date = parse_date_label(text)
        if not title or not start_date: continue

        strings=[norm(x) for x in card.stripped_strings if norm(x)]
        discipline_label = next((x for x in strings if norm(x).lower().startswith("tir ") or "arc exterieur" in norm(x).lower() or "arc extérieur" in x.lower()), "")
        discipline = classify(discipline_label + " " + title)
        if discipline not in ALLOWED: continue

        mandate = mandate_from_soup(card, BASE)
        city=""; m=re.search(r"\s+à\s+(.+)$", title, re.I)
        if m: city=norm(m.group(1))
        club=""
        for x in strings:
            if x.upper()==x and len(x)>4 and x not in {title.upper(),"MANDAT","DETAIL","DÉTAIL","MAIL","SITE","RESULTATS","RÉSULTATS"} and not re.match(r"^(LE|DU)\s+\d", x):
                club=x
        status=""; low=norm(text).lower()
        if "annulee" in low: status="cancelled"
        elif "reportee" in low: status="postponed"

        rows.append({"ffta_id":stable_id(detail,title,start_date),"title":title,"discipline":discipline,"discipline_label":discipline_label,"start_date":start_date,"end_date":end_date,"city":city,"club":club,"department":dep,"detail_url":detail,"mandate_url":mandate,"source_url":detail,"status":status})
    return list({r["ffta_id"]:r for r in rows}.values())


def enrich(row):
    try:
        r=get(row["detail_url"],25); soup=BeautifulSoup(r.text,"html.parser")
        mandate=mandate_from_soup(soup,r.url)
        if mandate: row["mandate_url"]=mandate
        text=norm(soup.get_text(" ",strip=True))
        # Si la fiche précise TAEN/TAEI, on affine le TAE générique.
        if row["discipline"]=="tae":
            if re.search(r"\bTAE\s*International\b|\bTAEI\b", text, re.I): row["discipline"]="taei"
            elif re.search(r"\bTAE\s*National\b|\bTAEN\b", text, re.I): row["discipline"]="taen"
    except Exception as exc:
        print(f"    ! détail inaccessible: {row['detail_url']} ({exc})")


def collect(start,end):
    all_rows={}
    for dep in DEPARTMENTS:
        print(f"\n=== Département {dep} ===")
        empty=0
        for page in range(100):
            url=page_url(start,end,dep,page); print(f"[calendrier] page {page}")
            rows=extract_cards(get(url).text,dep)
            print(f"  -> {len(rows)} compétition(s) retenue(s)")
            if not rows:
                empty+=1
                if empty>=2: break
            else:
                empty=0
                for row in rows: all_rows[row["ffta_id"]]=row
            time.sleep(REQUEST_DELAY)

    rows=sorted(all_rows.values(),key=lambda r:(r["start_date"] or "",r["department"],r["city"],r["title"]))
    print(f"\n=== Vérification des {len(rows)} fiches détail / mandats ===")
    for i,row in enumerate(rows,1):
        enrich(row)
        print(f"[{i}/{len(rows)}] dep {row['department']} | {row['discipline']} | {row['start_date']} {row['title']} -> {'MANDAT' if row['mandate_url'] else 'sans mandat'}")
        time.sleep(REQUEST_DELAY)
    return rows


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--days-ahead",type=int,default=365); ap.add_argument("--days-back",type=int,default=30); args=ap.parse_args()
    today=date.today(); start=today-timedelta(days=max(0,args.days_back)); end=today+timedelta(days=max(1,args.days_ahead))
    rows=collect(start,end); now=datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.write_text(json.dumps({"version":3,"updated_at":now,"source":"FFTA competitions calendar + detail pages","departments":list(DEPARTMENTS),"disciplines":sorted(ALLOWED),"range":{"start":start.isoformat(),"end":end.isoformat()},"competitions":rows},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    mandates=sum(1 for r in rows if r["mandate_url"])
    by={d:sum(1 for r in rows if r["discipline"]==d) for d in sorted(ALLOWED)}
    print("\nBilan disciplines : " + " | ".join(f"{k}: {v}" for k,v in by.items()))
    print(f"Terminé : {len(rows)} compétition(s), {mandates} mandat(s) disponible(s).")

if __name__=="__main__": main()
