"""Descubrimiento RSS y verificación de JobPosting; no requiere servicios de pago."""
import hashlib
import html
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .transport import request

QUERIES = [
    'psicólogo general sanitario Madrid empleo adultos',
    'psicólogo psicogerontología residencia centro día Madrid empleo',
    'psicólogo Madrid empleo site:copmadrid.org',
    'psicólogo Madrid empleo site:psyred.org',
    'psicólogo Madrid empleo clínica asociación hospital',
]


def plain(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", str(value or ""))).split())


def folded(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.lower())
                   if not unicodedata.combining(c))


def canonical(url):
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Enlace de oferta no válido")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit(("https", p.netloc.lower(), p.path.rstrip("/") or "/",
                       urlencode(sorted(query)), ""))


class StructuredData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.capture = False
        self.buffer = []
        self.documents = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.capture = dict(attrs).get("type", "").lower() == "application/ld+json"
            self.buffer = []

    def handle_data(self, data):
        if self.capture:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.capture:
            try:
                self.documents.append(json.loads("".join(self.buffer)))
            except ValueError:
                pass
            self.capture = False


def postings(value):
    if isinstance(value, list):
        for item in value:
            yield from postings(item)
    elif isinstance(value, dict):
        types = value.get("@type", [])
        if types == "JobPosting" or isinstance(types, list) and "JobPosting" in types:
            yield value
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in value:
                yield from postings(value[key])


def extract(page, source, today, *, initial=False):
    parser = StructuredData()
    parser.feed(page)
    result = []
    for document in parser.documents:
        for job in postings(document):
            title = plain(job.get("title"))
            title_key = folded(title)
            if "psicolog" not in title_key or any(x in title_key for x in
                    ("forense", "perito", "pericial", "judicial")):
                continue
            end = str(job.get("validThrough") or "")[:10]
            published = str(job.get("datePosted") or "")[:10]
            try:
                if end and date.fromisoformat(end) < today:
                    continue
                if initial and published and date.fromisoformat(published) < today - timedelta(days=30):
                    continue
                if published and date.fromisoformat(published) > today:
                    continue
            except ValueError:
                end = ""
                published = ""
            locations = job.get("jobLocation", [])
            locations = locations if isinstance(locations, list) else [locations]
            addresses = [x.get("address", {}) for x in locations if isinstance(x, dict)]
            location = "; ".join(", ".join(plain(a.get(k)) for k in
                       ("streetAddress", "addressLocality", "addressRegion") if a.get(k))
                       if isinstance(a, dict) else plain(a) for a in addresses)
            remote = job.get("jobLocationType") == "TELECOMMUTE"
            if "madrid" not in folded(location) and not remote:
                continue  # No inferir la ubicación a partir de la consulta del buscador.
            org = job.get("hiringOrganization", {})
            employer = plain(org.get("name")) if isinstance(org, dict) else plain(org)
            url = canonical(job.get("url") or source)
            description = plain(job.get("description"))
            # Excluir funciones principales forenses sin excluir atención a víctimas.
            if re.search(r"(?:funcion(?:es)?|puesto|actividad principal).{0,60}(?:pericial|forense)",
                         folded(description)):
                continue
            salary = job.get("baseSalary")
            priority = 1 if "sanitari" in title_key else 2 if any(
                x in folded(title + " " + description) for x in
                ("mayores", "envejecimiento", "residencia", "gerontolog", "centro de dia")) else 3
            data = {
                "title": title, "employer": employer or "Por confirmar", "url": url,
                "location": location or "Remoto; ámbito geográfico por confirmar",
                "salary": json.dumps(salary, ensure_ascii=False) if salary else "No publicado; por confirmar",
                "salary_raw": salary,
                "hours": plain(job.get("workHours")) or "Por confirmar",
                "contract": plain(job.get("employmentType")) or "Por confirmar",
                "schedule": "Por confirmar",
                "requirements": plain(job.get("qualifications")) or description,
                "description": description, "deadline": end or None,
                "active": None, "published_at": published or None,
                "fit": "Interesante pendiente de aclaraciones; requisitos sin validar con empleador",
                "commute": "Sin trayecto puerta a puerta verificado",
                "workplace_evidence": "Sin información laboral fiable",
                "sources": [source], "priority": priority,
                "uncertainties": ["Vigencia no confirmada con el empleador", "Trayecto y condiciones laborales",
                                  "Comprobar requisitos frente al perfil actualizado"],
            }
            result.append(data)
    return result


def fingerprint(data):
    # No cambiar de versión por fechas de detección, orden del RSS o texto de reseñas.
    fields = ("title", "employer", "url", "location", "salary", "hours", "contract",
              "schedule", "requirements", "deadline", "published_at", "description")
    return hashlib.sha256(json.dumps({k: data.get(k) for k in fields},
                         sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def discover(today, *, initial=False, fetch=request):
    offers, errors, seen = [], [], set()
    for query in QUERIES:
        feed = "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
        try:
            root = ET.fromstring(fetch(feed))
        except Exception as exc:
            errors.append({"source": feed, "error": type(exc).__name__})
            continue
        for item in root.findall(".//item")[:10]:
            try:
                link = canonical(item.findtext("link") or "")
                if link in seen:
                    continue
                seen.add(link)
                page = fetch(link).decode("utf-8", errors="replace")
                found = extract(page, link, today, initial=initial)
                if not found:
                    errors.append({"source": link, "error": "Sin oferta estructurada elegible; revisión manual pendiente"})
                offers.extend(found)
            except Exception as exc:
                errors.append({"source": item.findtext("link") or feed, "error": type(exc).__name__})
    return offers, errors
