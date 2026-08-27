#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
ARCHERS_FILE = ROOT / "config" / "archers.json"
RESULTS_FILE = ROOT / "data" / "results.json"
PROCESSED_FILE = ROOT / "data" / "processed.json"

CALENDAR_URL = "https://www.ffta.fr/competitions"
HEADERS = {
    "User-Agent": "ArchersAgathoisResultsCollector/2.0 (+https://github.com/LoycD/archers-agathois-data)",
    "Accept-Language": "fr-FR,fr;q=0.9",
}
DISCIPLINE_BY_PREFIX = {
    "S": "18m",
    "T": "tae",
    "N": "nature",
    "3": "3d",
    "C": "campagne",
    "B": "beursault",
}
REQUEST_DELAY = 0.35


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip()


def get(url, timeout=25):
    r = requests.get(
        url,
        headers=HEADERS,
        timeout=timeout,
        allow_redirects=True,
    )
    r.raise_for_status()
    return r


def calendar_page_url(start, end, page):
    return (
        f"{CALENDAR_URL}?discipline=All&inter=All"
        f"&start={start.isoformat()}&end={end.isoformat()}"
        f"&sort_by=start&sort_order=ASC&page={page}"
    )


def discover_result_links(start, end, max_pages=50):
    found = []
    seen = set()

    for page in range(max_pages):
        url = calendar_page_url(start, end, page)
        print(f"[calendrier] page {page}")
        soup = BeautifulSoup(get(url).text, "html.parser")
        page_links = []

        for a in soup.find_all("a", href=True):
            label = norm(a.get_text(" ", strip=True)).lower()
            href = urljoin(url, a["href"])

            if "resultat" not in label:
                continue

            if (urlparse(href).hostname or "").lower() != "extranet.ffta.fr":
                continue

            if "/pdfresultats/" not in href.lower() and not href.lower().endswith(".pdf"):
                try:
                    href = get(href).url
                except Exception:
                    continue

            if href not in seen:
                seen.add(href)
                page_links.append(href)
                found.append(href)

        print(f"  -> {len(page_links)} lien(s)")

        if page > 0 and not page_links:
            break

        time.sleep(REQUEST_DELAY)

    return found


def pdf_to_text(pdf_bytes):
    with tempfile.TemporaryDirectory() as tmp:
        pdf = Path(tmp) / "r.pdf"
        txt = Path(tmp) / "r.txt"
        pdf.write_bytes(pdf_bytes)

        p = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(pdf), str(txt)],
            capture_output=True,
            text=True,
        )

        if p.returncode:
            raise RuntimeError(p.stderr.strip() or "pdftotext a échoué")

        return txt.read_text(encoding="utf-8", errors="replace")


def date_from_pdf_url(pdf_url):
    name = Path(urlparse(pdf_url).path).name
    m = re.search(r"_(20\d{6})_", name)
    if not m:
        return None
    raw = m.group(1)
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def parse_header(text, pdf_url):
    lines = [norm(x) for x in text.splitlines() if norm(x)]
    head = " ".join(lines[:45])

    m = re.search(
        r"\b(?:le|du)\s+(\d{2})/(\d{2})/(\d{4})(?:\s+au\s+\d{2}/\d{2}/\d{4})?",
        head,
        re.I,
    )

    if m:
        date_iso = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
        before = head[:m.start()].strip(" -")
    else:
        date_iso = date_from_pdf_url(pdf_url)
        before = head

    before = re.sub(r"^Classement Officiel\s*", "", before, flags=re.I)
    before = norm(before).strip(" -")

    title = ""
    place = ""
    chunks = [c.strip() for c in before.split(" - ") if c.strip()]

    if len(chunks) >= 3:
        place = chunks[-1]
        title = " - ".join(chunks[:-1])
    elif len(chunks) == 2:
        title = " - ".join(chunks)
    elif chunks:
        title = chunks[0]

    filename = Path(urlparse(pdf_url).path).name
    prefix = filename.split("_", 1)[0].upper()

    return {
        "date": date_iso,
        "competition": title or "Concours FFTA",
        "place": place,
        "discipline": DISCIPLINE_BY_PREFIX.get(prefix, "autre"),
        "source_url": pdf_url,
    }


HEADER_ALIASES = {
    "rank": ("Clt", "Clas", "Rang"),
    "licence": ("Licence", "Lic."),
    "category": ("Cat", "Cat."),
    "total": ("Total", "Score"),
    "p1": ("P1", "S1", "Serie 1", "Série 1"),
    "p2": ("P2", "S2", "Serie 2", "Série 2"),
}


def find_header_before(lines, row_index, max_up=35):
    start = max(0, row_index - max_up)

    for i in range(row_index - 1, start - 1, -1):
        raw = lines[i]
        n = norm(raw).lower()

        if "licence" in n and ("total" in n or "score" in n) and "cat" in n:
            return i, raw

    return None, None


def column_positions(header_line):
    found = {}

    for key, aliases in HEADER_ALIASES.items():
        best = None

        for alias in aliases:
            m = re.search(re.escape(alias), header_line, re.I)
            if m and (best is None or m.start() < best):
                best = m.start()

        if best is not None:
            found[key] = best

    return found


def slice_columns(line, positions):
    ordered = sorted((pos, key) for key, pos in positions.items())
    values = {}

    for idx, (start, key) in enumerate(ordered):
        end = ordered[idx + 1][0] if idx + 1 < len(ordered) else len(line)
        values[key] = line[start:end].strip()

    return values


def first_int(value):
    if value is None:
        return None
    m = re.search(r"-?\d+", str(value))
    return int(m.group(0)) if m else None


def category_from_row(values, line, licence):
    cat = (values.get("category") or "").strip().upper()

    m = re.search(r"\b((?:U\d+|S\d)[HF][A-Z]{2})\b", cat)
    if m:
        return m.group(1)

    pos = line.upper().find(licence.upper())
    if pos >= 0:
        tail = line[pos + len(licence):]
        m = re.search(r"\b((?:U\d+|S\d)[HF][A-Z]{2})\b", tail.upper())
        if m:
            return m.group(1)

    return None



def numbers_after_category(line, licence, category):
    pos = line.upper().find(licence.upper())
    if pos < 0:
        return []
    tail = line[pos + len(licence):]
    cat_pos = tail.upper().find(category.upper())
    if cat_pos < 0:
        return []
    after = tail[cat_pos + len(category):]
    return [int(x) for x in re.findall(r"(?<![\w])(\d{1,4})(?![\w])", after)]

def parse_row_by_header(lines, row_index, licence):
    header_index, header_line = find_header_before(lines, row_index)
    if header_line is None:
        return None

    positions = column_positions(header_line)

    if not all(k in positions for k in ("licence", "category", "total")):
        return None

    values = slice_columns(lines[row_index], positions)

    row_licence = re.sub(r"\s+", "", values.get("licence", "")).upper()
    if licence.upper() not in row_licence:
        return None

    category = category_from_row(values, lines[row_index], licence)
    total = first_int(values.get("total"))
    rank = first_int(values.get("rank")) if "rank" in positions else None

    if not category or total is None or total <= 0:
        return None

    series = []
    for key in ("p1", "p2"):
        if key in positions:
            val = first_int(values.get(key))
            if val is not None:
                series.append(val)

    if not series:
        nums = numbers_after_category(lines[row_index], licence, category)
        if len(nums) >= 3:
            candidate_s1, candidate_s2, candidate_total = nums[0], nums[1], nums[2]
            if candidate_total == total:
                series = [candidate_s1, candidate_s2]

    return {
        "category": category,
        "series": series,
        "score": total,
        "rank": rank,
        "header_index": header_index,
    }


def parse_archer_result(text, pdf_url, archer):
    licence = archer["licence"].upper()
    lines = text.splitlines()
    header = parse_header(text, pdf_url)
    out = []

    for i, line in enumerate(lines):
        if licence not in line.upper():
            continue

        parsed = parse_row_by_header(lines, i, licence)

        if not parsed:
            print(f"  ! {licence}: ligne trouvée mais colonnes FFTA non reconnues")
            continue

        dep = 1 + len(out)

        row = {
            "id": "",
            "licence": licence,
            "name": archer.get("name", ""),
            "date": header["date"],
            "competition": header["competition"],
            "place": header["place"],
            "discipline": header["discipline"],
            "category": parsed["category"],
            "departure": dep,
            "series": parsed["series"],
            "score": parsed["score"],
            "rank": parsed["rank"],
            "source": "ffta",
            "source_url": pdf_url,
        }

        key = "|".join([
            licence,
            row["date"] or "",
            row["discipline"],
            str(row["score"]),
            str(dep),
            Path(urlparse(pdf_url).path).name,
        ])
        row["id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
        out.append(row)

    return out


def merge_results(existing, incoming):
    by = {r.get("id"): r for r in existing if r.get("id")}
    added = 0

    for r in incoming:
        if r["id"] not in by:
            added += 1
        by[r["id"]] = r

    merged = list(by.values())
    merged.sort(
        key=lambda r: (
            r.get("date") or "",
            r.get("licence") or "",
            r.get("departure") or 0,
        ),
        reverse=True,
    )
    return merged, added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--days-back",
        type=int,
        default=int(os.getenv("DAYS_BACK", "45")),
    )
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--rebuild",
        action="store_true",
        help="Reconstruit results.json uniquement avec les PDF retraités pendant ce passage.",
    )
    args = ap.parse_args()

    archers = load_json(ARCHERS_FILE, [])
    state = load_json(PROCESSED_FILE, {"version": 1, "processed_urls": []})
    processed = set(state.get("processed_urls", []))
    doc = load_json(RESULTS_FILE, {"version": 1, "updated_at": None, "results": []})

    if args.rebuild:
        processed = set()
        existing_results = []
    else:
        existing_results = doc.get("results", [])

    end = date.today()
    start = end - timedelta(days=max(1, args.days_back))
    links = discover_result_links(start, end)

    incoming = []
    newly = []

    for n, url in enumerate(links, 1):
        if url in processed and not args.force:
            continue

        print(f"[{n}/{len(links)}] {url}")

        try:
            r = get(url, 35)

            if (
                "pdf" not in (r.headers.get("content-type") or "").lower()
                and not r.url.lower().endswith(".pdf")
            ):
                newly.append(url)
                continue

            text = pdf_to_text(r.content)
            hits = 0
            upper = text.upper()

            for a in archers:
                if a["licence"].upper() not in upper:
                    continue

                rows = parse_archer_result(text, r.url, a)
                incoming.extend(rows)
                hits += len(rows)

            print(f"  -> {hits} résultat(s) Archers Agathois")
            newly.append(url)

        except Exception as exc:
            print(f"  !! erreur: {exc}")

        time.sleep(REQUEST_DELAY)

    merged, added = merge_results(existing_results, incoming)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    save_json(
        RESULTS_FILE,
        {
            "version": 3,
            "updated_at": now,
            "source": "FFTA official competition result PDFs",
            "parser": "header-columns-v3",
            "results": merged,
        },
    )

    processed.update(newly)
    save_json(
        PROCESSED_FILE,
        {
            "version": 3,
            "updated_at": now,
            "processed_urls": sorted(processed),
        },
    )

    print(
        f"Terminé: {len(incoming)} trouvé(s), "
        f"{added} nouveau(x), {len(merged)} total."
    )


if __name__ == "__main__":
    main()
