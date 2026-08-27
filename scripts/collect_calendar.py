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
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "calendar.json"
BASE = "https://www.ffta.fr/competitions"
DEPARTMENTS = ("34", "30", "11", "66")
HEADERS = {
    "User-Agent": "ArchersAgathoisCalendarCollector/1.1 (+https://github.com/LoycD/archers-agathois-data)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
REQUEST_DELAY = 0.20


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
    m2 = re.search(
        r"Du\s+(\d{1,2})(?:\s+([A-Za-zÀ-ÿ]+))?\s+au\s+(\d{1,2})\s+([A-Za-zÀ-ÿ]+)\s+(20\d{2})",
        s, re.I,
    )
    if m2:
        year = int(m2.group(5))
        end = iso_date(f"{m2.group(3)} {m2.group(4)} {year}")
        start_month = m2.group(2) or m2.group(4)
        start = iso_date(f"{m2.group(1)} {start_month} {year}")
        return start, end

    m = re.search(r"(?:Le|Du)\s+(\d{1,2}\s+[A-Za-zÀ-ÿ]+\s+20\d{2})", s, re.I)
    if not m:
        return None, None
    start = iso_date(m.group(1))
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
    q = parse_qs(urlparse(detail_url).query)
    for key in ("id", "competition", "concours"):
        if q.get(key):
            return str(q[key][0])
    m = re.search(r"/epreuve/(\d+)", detail_url)
    if m:
        return m.group(1)
    m = re.search(r"(\d{4,})", detail_url)
    if m:
        return m.group(1)
    return hashlib.sha256(f"{title}|{start_date}|{detail_url}".encode()).hexdigest()[:20]


def page_url(start: date, end: date, dep: str, page: int) -> str:
    params = [
        ("dep[]", dep), ("discipline", "All"), ("inter", "All"),
        ("start", start.isoformat()), ("end", end.isoformat()),
        ("sort_by", "start"), ("sort_order", "ASC"), ("page", str(page)),
    ]
    return BASE + "?" + urlencode(params)


def is_mandate_href(href: str) -> bool:
    h = (href or "").lower()
    return (
        "/medias/documents_epreuves/" in h
        or "document_epreuve" in h
        or "mandat" in h
    )


def mandate_from_soup(soup: BeautifulSoup, base_url: str) -> str:
    # 1) Le cas normal : le texte du lien indique Mandat.
    for a in soup.find_all("a", href=True):
        label = norm(a.get_text(" ", strip=True)).lower()
        aria = norm(a.get("aria-label", "")).lower()
        title = norm(a.get("title", "")).lower()
        if "mandat" in label or "mandat" in aria or "mandat" in title:
            return urljoin(base_url, a["href"])

    # 2) Fallback FFTA : les mandats sont servis depuis documents_epreuves.
    for a in soup.find_all("a", href=True):
        if is_mandate_href(a["href"]):
            return urljoin(base_url, a["href"])

    return ""


def enrich_from_detail(detail_url: str) -> dict:
    """Ouvre la fiche FFTA pour récupérer le mandat et fiabiliser les métadonnées."""
    if not detail_url:
        return {}
    try:
        r = get(detail_url, 25)
        soup = BeautifulSoup(r.text, "html.parser")
        text = norm(soup.get_text(" ", strip=True))
        mandate = mandate_from_soup(soup, r.url)

        out = {"mandate_url": mandate}

        # Discipline officielle de la fiche.
        m = re.search(r"Discipline\s*:\s*(.+?)(?:Championnat\s*:|Distances|Duels|Comite|Comité|Organisateur|Lieu\s*:)", text, re.I)
        if m:
            out["discipline_label"] = norm(m.group(1))
            out["discipline"] = classify_discipline(m.group(1))

        m = re.search(r"Organisateur\s*:\s*(.+?)(?:Lieu\s*:|Tel|Mail|Site|Itineraire|Itinéraire)", text, re.I)
        if m:
            out["club"] = norm(m.group(1))

        m = re.search(r"Lieu\s*:\s*([^\n]+?)(?:Tel|Mail|Site|Itineraire|Itinéraire|$)", text, re.I)
        if m:
            # On ne remplace la ville que par le premier segment court de la zone Lieu.
            candidate = norm(m.group(1))
            if candidate:
                out["city_detail"] = candidate

        return out
    except Exception as exc:
        print(f"    ! fiche détail inaccessible: {detail_url} ({exc})")
        return {}


def extract_cards(html: str, dep: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    for h2 in soup.find_all("h2"):
        a = h2.find("a", href=True)
        if not a:
            continue
        title = norm(a.get_text(" ", strip=True))
        detail = urljoin(BASE, a["href"])
        if not title:
            continue

        # Remonte assez haut pour englober date + infos + boutons d'action.
        card = h2
        chosen = h2
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            txt = norm(card.get_text(" ", strip=True))
            if re.search(r"(?:Le|Du)\s+\d", txt):
                chosen = card
            # Un vrai conteneur de compétition contient normalement Détail.
            if re.search(r"\bD[eé]tail\b", txt, re.I) and re.search(r"(?:Le|Du)\s+\d", txt):
                chosen = card
                # Ne casse pas tout de suite : le mandat peut être dans un frère un niveau au-dessus.
                if "Mandat" in txt:
                    break
        card = chosen
        text = norm(card.get_text(" ", strip=True))
        start_date, end_date = parse_date_label(text)
        if not start_date:
            continue

        detail_candidates = []
        mandate = ""
        for x in card.find_all("a", href=True):
            href = urljoin(BASE, x["href"])
            label = norm(x.get_text(" ", strip=True)).lower()
            aria = norm(x.get("aria-label", "")).lower()
            title_attr = norm(x.get("title", "")).lower()

            if "detail" in label or "détail" in label or "detail" in aria or "détail" in aria:
                detail_candidates.append(href)
            if not mandate and (
                "mandat" in label or "mandat" in aria or "mandat" in title_attr or is_mandate_href(href)
            ):
                mandate = href

        if detail_candidates:
            detail = detail_candidates[0]

        strings = [norm(x) for x in card.stripped_strings if norm(x)]
        discipline_label = next((
            x for x in strings
            if norm(x).lower().startswith("tir ")
            or "arc exterieur" in norm(x).lower()
            or "arc extérieur" in x.lower()
        ), "")

        club = ""
        for x in strings:
            if x in (title, discipline_label, "Individuel", "Uniquement équipe", "Mandat", "Détail", "Detail", "Résultats", "Mail", "Site"):
                continue
            if x.upper() == x and len(x) > 4 and not re.match(r"^(LE|DU)\s+\d", x):
                club = x

        status = ""
        low = norm(text).lower()
        if "annulee" in low:
            status = "cancelled"
        elif "reportee" in low:
            status = "postponed"

        city = ""
        mcity = re.search(r"\s+à\s+(.+)$", title, re.I)
        if mcity:
            city = norm(mcity.group(1))

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
                if empty >= 2:
                    break
            else:
                empty = 0
                for row in rows:
                    all_rows[row["ffta_id"]] = row
            time.sleep(REQUEST_DELAY)

    rows = sorted(all_rows.values(), key=lambda r: (r["start_date"] or "", r["city"], r["title"]))

    # On ouvre chaque fiche : indispensable car le mandat peut être ajouté après la création du concours.
    print(f"\n=== Vérification des {len(rows)} fiches détail / mandats ===")
    for i, row in enumerate(rows, 1):
        extra = enrich_from_detail(row["detail_url"])
        if extra.get("mandate_url"):
            row["mandate_url"] = extra["mandate_url"]
        if extra.get("discipline_label"):
            row["discipline_label"] = extra["discipline_label"]
            row["discipline"] = extra.get("discipline", row["discipline"])
        if extra.get("club"):
            row["club"] = extra["club"]

        flag = "MANDAT" if row["mandate_url"] else "sans mandat"
        print(f"[{i}/{len(rows)}] {row['start_date']} {row['title']} -> {flag}")
        time.sleep(REQUEST_DELAY)

    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days-ahead", type=int, default=365)
    ap.add_argument("--days-back", type=int, default=30)
    args = ap.parse_args()

    today = date.today()
    start = today - timedelta(days=max(0, args.days_back))
    end = today + timedelta(days=max(1, args.days_ahead))
    rows = collect(start, end)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    OUT.write_text(
        json.dumps({
            "version": 2,
            "updated_at": now,
            "source": "FFTA competitions calendar + detail pages",
            "departments": list(DEPARTMENTS),
            "range": {"start": start.isoformat(), "end": end.isoformat()},
            "competitions": rows,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    mandates = sum(1 for r in rows if r["mandate_url"])
    print(f"\nTerminé : {len(rows)} compétition(s), {mandates} mandat(s) disponible(s).")


if __name__ == "__main__":
    main()
