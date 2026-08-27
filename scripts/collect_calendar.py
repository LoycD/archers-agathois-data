#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "calendar.json"
BASE = "https://www.ffta.fr/competitions"
DEPARTMENTS = ("34", "30", "11", "66")
HEADERS = {
    "User-Agent": "ArchersAgathoisCalendarCollector/1.0 (+https://github.com/LoycD/archers-agathois-data)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}


def norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip()


def get(url: str, timeout: int = 30) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    r.raise_for_status()
    return r


def iso_date(fr: str, year: int | None = None) -> str | None:
    months = {
        "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
        "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12,
    }
    s = norm(fr).lower()
    m = re.search(r"(\d{1,2})\s+([a-z]+)(?:\s+(20\d{2}))?", s)
    if not m or m.group(2) not in months:
        return None
    y = int(m.group(3)) if m.group(3) else year
    if not y:
        return None
    return date(y, months[m.group(2)], int(m.group(1))).isoformat()


def parse_date_label(label: str) -> tuple[str | None, str | None]:
    s = norm(label)
    # Le 29 août 2026
    m = re.search(r"(?:Le|Du)\s+(\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2})", s, re.I)
    if not m:
        return None, None
    start = iso_date(m.group(1))
    # Du 29 au 30 août 2026
    m2 = re.search(r"Du\s+(\d{1,2})(?:\s+([A-Za-zÀ-ÿ]+))?\s+au\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})", s, re.I)
    if m2:
        year = int(m2.group(5)); end = iso_date(f"{m2.group(3)} {m2.group(4)} {year}")
        start_month = m2.group(2) or m2.group(4)
        start = iso_date(f"{m2.group(1)} {start_month} {year}")
        return start, end
    return start, start


def classify_discipline(text: str) -> str:
    n = norm(text).lower()
    if "18m" in n or "18 m" in n: return "18m"
    if "3d" in n: return "3d"
    if "nature" in n: return "nature"
    if "campagne" in n: return "campagne"
    if "beursault" in n: return "beursault"
    if "exterieur" in n or "tae" in n: return "tae"
    return n or "autre"


def stable_id(detail_url: str, title: str, start_date: str | None) -> str:
    # L'URL détail FFTA est la meilleure clé stable. Fallback déterministe sinon.
    q = parse_qs(urlparse(detail_url).query)
    for key in ("id", "competition", "concours"):
        if q.get(key): return str(q[key][0])
    m = re.search(r"(\d{4,})", detail_url)
    if m: return m.group(1)
    return hashlib.sha256(f"{title}|{start_date}|{detail_url}".encode()).hexdigest()[:20]


def page_url(start: date, end: date, dep: str, page: int) -> str:
    # Le filtre dep[] est celui exposé par le calendrier FFTA.
    params = [
        ("dep[]", dep), ("discipline", "All"), ("inter", "All"),
        ("start", start.isoformat()), ("end", end.isoformat()),
        ("sort_by", "start"), ("sort_order", "ASC"), ("page", str(page)),
    ]
    return BASE + "?" + urlencode(params)


def extract_cards(html: str, dep: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    # Les titres de compétitions sont des h2 contenant un lien vers Détail.
    for h2 in soup.find_all("h2"):
        a = h2.find("a", href=True)
        if not a: continue
        title = norm(a.get_text(" ", strip=True))
        detail = urljoin(BASE, a["href"])
        if not title: continue

        # Remonte au conteneur qui inclut date, discipline, club et liens d'action.
        card = h2
        for _ in range(5):
            if card.parent is None: break
            card = card.parent
            txt = norm(card.get_text(" ", strip=True))
            if ("Mandat" in txt or "Detail" in norm(txt) or "Détail" in txt) and re.search(r"(?:Le|Du)\s+\d", txt):
                break
        text = norm(card.get_text(" ", strip=True))
        start_date, end_date = parse_date_label(text)
        if not start_date: continue

        links = {norm(x.get_text(" ", strip=True)).lower(): urljoin(BASE, x["href"]) for x in card.find_all("a", href=True)}
        mandate = next((u for k,u in links.items() if "mandat" in k), "")
        detail = next((u for k,u in links.items() if "detail" in k or "détail" in k), detail)

        # Les lignes utiles se trouvent généralement juste après le h2.
        strings = [norm(x) for x in card.stripped_strings if norm(x)]
        discipline_label = next((x for x in strings if norm(x).lower().startswith("tir ") or "arc exterieur" in norm(x).lower() or "arc extérieur" in x.lower()), "")
        club = ""
        for x in strings:
            if x in (title, discipline_label, "Individuel", "Uniquement équipe", "Mandat", "Détail", "Detail", "Résultats", "Mail", "Site"): continue
            if x.upper() == x and len(x) > 4 and not re.match(r"^(LE|DU)\s+\d", x):
                club = x
        status = ""
        low = text.lower()
        if "annulee" in norm(low): status = "cancelled"
        elif "reportee" in norm(low): status = "postponed"

        # La ville est généralement après « à » dans le titre FFTA.
        city = ""
        mcity = re.search(r"\s+à\s+(.+)$", title, re.I)
        if mcity: city = norm(mcity.group(1))

        rows.append({
            "ffta_id": stable_id(detail, title, start_date),
            "title": title,
            "discipline": classify_discipline(discipline_label),
            "discipline_label": discipline_label,
            "start_date": start_date,
            "end_date": end_date,
            "city": city,
            "club": club,
            "department": dep,
            "detail_url": detail,
            "mandate_url": mandate,
            "source_url": detail,
            "status": status,
        })
    # dédoublonnage de la page
    return list({r["ffta_id"]: r for r in rows}.values())


def collect(start: date, end: date) -> list[dict]:
    all_rows = {}
    for dep in DEPARTMENTS:
        print(f"\n=== Département {dep} ===")
        empty = 0
        for page in range(100):
            url = page_url(start, end, dep, page)
            print(f"[calendrier] page {page}")
            rows = extract_cards(get(url).text, dep)
            print(f"  -> {len(rows)} compétition(s)")
            if not rows:
                empty += 1
                if empty >= 2: break
            else:
                empty = 0
                for row in rows: all_rows[row["ffta_id"]] = row
            time.sleep(0.25)
    return sorted(all_rows.values(), key=lambda r: (r["start_date"] or "", r["city"], r["title"]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-ahead", type=int, default=365)
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()
    today = date.today()
    start, end = today - timedelta(days=max(0,args.days_back)), today + timedelta(days=max(1,args.days_ahead))
    rows = collect(start, end)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    OUT.write_text(json.dumps({
        "version": 1,
        "updated_at": now,
        "source": "FFTA competitions calendar",
        "departments": list(DEPARTMENTS),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "competitions": rows,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mandates = sum(1 for r in rows if r["mandate_url"])
    print(f"\nTerminé : {len(rows)} compétition(s), {mandates} mandat(s) disponible(s).")

if __name__ == "__main__":
    main()
