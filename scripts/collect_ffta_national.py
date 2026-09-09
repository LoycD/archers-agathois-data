#!/usr/bin/env python3
"""Passe complémentaire pour les épreuves nationales/internationales FFTA.

Cette passe ne dépend plus uniquement des boutons "Résultats" visibles dans
les pages du calendrier. Elle :

1. parcourt le calendrier national/international (inter=1) ;
2. récupère les fiches /epreuve/<id> ;
3. ouvre chaque fiche et cherche son lien officiel "Résultats" ;
4. résout les éventuelles redirections vers le PDF extranet.ffta.fr ;
5. fusionne ensuite les résultats avec l'historique existant grâce au
   collecteur principal.

Cela couvre notamment les Championnats de France dont le PDF n'est pas
forcément découvert directement depuis la liste du calendrier.
"""

import re
import time
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import collect_ffta as collector


TARGET_EVENT_ID = "23139"
TARGET_PDF_FRAGMENT = "S_0893240_20260227_74763.pdf"


def national_calendar_page_url(start, end, page):
    return (
        f"{collector.CALENDAR_URL}?discipline=All&inter=1"
        f"&start={start.isoformat()}&end={end.isoformat()}"
        f"&sort_by=start&sort_order=ASC&page={page}"
    )


def is_official_result_url(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    return (
        host == "extranet.ffta.fr"
        and ("/pdfresultats/" in path or path.endswith(".pdf"))
    )


def resolve_result_url(url):
    """Suit une éventuelle redirection FFTA vers le vrai PDF officiel."""
    if is_official_result_url(url):
        return url
    try:
        resolved = collector.get(url).url
    except Exception as exc:
        print(f"    ! impossible de résoudre {url}: {exc}")
        return None
    return resolved if is_official_result_url(resolved) else None


def result_links_from_soup(soup, base_url):
    links = []
    for a in soup.find_all("a", href=True):
        label = collector.norm(a.get_text(" ", strip=True)).lower()
        if "resultat" not in label:
            continue
        href = urljoin(base_url, a["href"])
        resolved = resolve_result_url(href)
        if resolved:
            links.append(resolved)
    return links


def event_detail_links_from_soup(soup, base_url):
    links = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base_url, a["href"])
        parsed = urlparse(href)
        if (parsed.hostname or "").lower() != "www.ffta.fr":
            continue
        if not re.fullmatch(r"/epreuve/\d+/?", parsed.path):
            continue
        href = href.rstrip("/")
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def discover_result_links(start, end, max_pages=500):
    found = []
    seen_results = set()
    seen_events = set()
    empty_event_streak = 0
    target_event_seen = False
    target_pdf_seen = False

    for page in range(max_pages):
        url = national_calendar_page_url(start, end, page)
        print(f"[national] calendrier page {page}")

        try:
            soup = BeautifulSoup(collector.get(url).text, "html.parser")
        except Exception as exc:
            print(f"  !! erreur calendrier: {exc}")
            break

        direct_links = result_links_from_soup(soup, url)
        detail_links = event_detail_links_from_soup(soup, url)

        new_event_links = []
        for detail_url in detail_links:
            if detail_url.endswith(f"/epreuve/{TARGET_EVENT_ID}"):
                target_event_seen = True
                print(f"  >>> ÉPREUVE CIBLE TROUVÉE: {detail_url}")
            if detail_url not in seen_events:
                seen_events.add(detail_url)
                new_event_links.append(detail_url)

        for pdf_url in direct_links:
            if TARGET_PDF_FRAGMENT.lower() in pdf_url.lower():
                target_pdf_seen = True
                print(f"  >>> PDF CIBLE TROUVÉ DIRECTEMENT: {pdf_url}")
            if pdf_url not in seen_results:
                seen_results.add(pdf_url)
                found.append(pdf_url)

        print(
            f"  -> {len(detail_links)} fiche(s), "
            f"{len(direct_links)} résultat(s) direct(s), "
            f"{len(new_event_links)} nouvelle(s) fiche(s)"
        )

        # Le critère d'arrêt se base désormais sur l'absence d'épreuves,
        # pas sur l'absence de boutons Résultats.
        empty_event_streak = 0 if detail_links else empty_event_streak + 1
        if page > 0 and empty_event_streak >= 2:
            break

        time.sleep(collector.REQUEST_DELAY)

    print(f"[national] {len(seen_events)} fiche(s) d'épreuve à inspecter")

    for n, detail_url in enumerate(sorted(seen_events), 1):
        if n % 25 == 0 or detail_url.endswith(f"/epreuve/{TARGET_EVENT_ID}"):
            print(f"[national fiche {n}/{len(seen_events)}] {detail_url}")

        try:
            soup = BeautifulSoup(collector.get(detail_url).text, "html.parser")
        except Exception as exc:
            print(f"  !! erreur fiche {detail_url}: {exc}")
            continue

        detail_results = result_links_from_soup(soup, detail_url)
        if detail_url.endswith(f"/epreuve/{TARGET_EVENT_ID}"):
            print(f"  >>> FICHE CIBLE: {len(detail_results)} lien(s) Résultats trouvé(s)")

        for pdf_url in detail_results:
            if TARGET_PDF_FRAGMENT.lower() in pdf_url.lower():
                target_pdf_seen = True
                print(f"  >>> PDF CIBLE CONFIRMÉ: {pdf_url}")
            if pdf_url not in seen_results:
                seen_results.add(pdf_url)
                found.append(pdf_url)

        time.sleep(collector.REQUEST_DELAY)

    print(
        "[national diagnostic] "
        f"épreuve {TARGET_EVENT_ID}: {'OUI' if target_event_seen else 'NON'} | "
        f"PDF {TARGET_PDF_FRAGMENT}: {'OUI' if target_pdf_seen else 'NON'}"
    )

    return found


collector.calendar_page_url = national_calendar_page_url
collector.discover_result_links = discover_result_links

if __name__ == "__main__":
    collector.main()
