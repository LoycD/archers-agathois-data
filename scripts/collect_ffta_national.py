#!/usr/bin/env python3
"""Passe complémentaire pour les épreuves nationales/internationales FFTA.

Réutilise exactement le collecteur principal, mais interroge le calendrier
avec inter=1 afin de ne pas manquer les championnats de France et autres
épreuves nationales qui ne remontent pas toujours avec inter=All.
"""

import collect_ffta as collector


def national_calendar_page_url(start, end, page):
    return (
        f"{collector.CALENDAR_URL}?discipline=All&inter=1"
        f"&start={start.isoformat()}&end={end.isoformat()}"
        f"&sort_by=start&sort_order=ASC&page={page}"
    )


collector.calendar_page_url = national_calendar_page_url

if __name__ == "__main__":
    collector.main()
