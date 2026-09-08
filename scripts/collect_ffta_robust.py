#!/usr/bin/env python3
"""Collecteur FFTA avec secours via les fiches des épreuves nationales.

Le collecteur historique reste la source principale. Cette enveloppe ajoute les
liens Résultats trouvés sur les fiches /epreuve/ du calendrier national afin de
ne pas manquer les Championnats de France et autres épreuves nationales.
"""
from __future__ import annotations

import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import collect_ffta as base

_original_discover = base.discover_result_links


def national_calendar_url(start, end, page):
    return (
        f"{base.CALENDAR_URL}?discipline=All&inter=1"
        f"&start={start.isoformat()}&end={end.isoformat()}"
        f"&sort_by=start&sort_order=ASC&page={page}"
    )


def discover_national_result_links(start, end, max_pages=100):
    event_urls = []
    seen_events = set()
    empty_streak = 0

    # On pagine selon la présence d'épreuves, pas selon la présence de PDF.
    # Ainsi deux pages sans résultat publié ne coupent plus la collecte trop tôt.
    for page in range(max_pages):
        url = national_calendar_url(start, end, page)
        print(f"[national] calendrier page {page}")
        soup = BeautifulSoup(base.get(url).text, "html.parser")
        page_events = []
        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            parsed = urlparse(href)
            if (parsed.hostname or "").lower() != "www.ffta.fr":
                continue
            if not parsed.path.startswith("/epreuve/"):
                continue
            clean = f"https://www.ffta.fr{parsed.path}"
            if clean not in seen_events:
                seen_events.add(clean)
                page_events.append(clean)
                event_urls.append(clean)
        print(f"  -> {len(page_events)} épreuve(s)")
        empty_streak = 0 if page_events else empty_streak + 1
        if page > 0 and empty_streak >= 2:
            break
        time.sleep(base.REQUEST_DELAY)

    found = []
    seen_results = set()
    print(f"[national] {len(event_urls)} fiche(s) à contrôler")
    for n, event_url in enumerate(event_urls, 1):
        try:
            soup = BeautifulSoup(base.get(event_url).text, "html.parser")
        except Exception as exc:
            print(f"[national] {event_url} !! {exc}")
            continue

        for a in soup.find_all("a", href=True):
            label = base.norm(a.get_text(" ", strip=True)).lower()
            if "resultat" not in label or "direct" in label:
                continue
            href = urljoin(event_url, a["href"])
            host = (urlparse(href).hostname or "").lower()
            if host != "extranet.ffta.fr":
                continue
            if "/pdfresultats/" not in href.lower() and not href.lower().endswith(".pdf"):
                try:
                    href = base.get(href).url
                except Exception:
                    continue
            if href not in seen_results:
                seen_results.add(href)
                found.append(href)
                print(f"[national] résultat {n}/{len(event_urls)}: {href}")
        time.sleep(base.REQUEST_DELAY)

    return found


def robust_discover(start, end, max_pages=500):
    regular = _original_discover(start, end, max_pages=max_pages)
    national = discover_national_result_links(start, end)
    merged = []
    seen = set()
    for href in regular + national:
        if href not in seen:
            seen.add(href)
            merged.append(href)
    print(
        f"[découverte robuste] général={len(regular)} | "
        f"national={len(national)} | uniques={len(merged)}"
    )
    return merged


base.discover_result_links = robust_discover

if __name__ == "__main__":
    base.main()
