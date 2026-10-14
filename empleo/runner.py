import copy
import hashlib
import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .gmail import body_hash, recipients_key
from .search import canonical, fingerprint, folded

TZ = ZoneInfo("Europe/Madrid")
BLOCKED_STATUSES = {"discarded", "applied", "descartada", "descartado", "presentada",
                    "presentado", "candidatura presentada"}


def stamp():
    return datetime.now(TZ).isoformat()


def blocked(state, day, recipient):
    # Cualquier estado ya reservado bloquea, también un fallo aparente de envío.
    return any(d["local_date"] == day and d["recipient"] == recipient
               for d in state["deliveries"])


def eligibility(offer):
    text = folded(offer.get("title", "") + " " + offer.get("requirements", ""))
    import re
    exclusive = re.search(r"(?:imprescindible|obligatorio|requisito excluyente).{0,55}"
                          r"(?:\bpir\b|emdr|especialista en neuropsicologia)|"
                          r"(?:\bpir\b|emdr).{0,35}(?:imprescindible|obligatorio)", text)
    return not bool(exclusive)


def salary_note(data):
    raw = data.get("salary_raw")
    if not isinstance(raw, dict):
        return "Objetivo de 20.000 € brutos/año: pendiente de confirmar."
    value = raw.get("value")
    if not isinstance(value, dict) or raw.get("currency") != "EUR" or value.get("unitText") != "YEAR":
        return "No se convierte a anual ni neto: faltan unidad, pagas o condiciones."
    minimum = value.get("minValue", value.get("value"))
    maximum = value.get("maxValue", value.get("value"))
    if isinstance(maximum, (int, float)) and maximum < 20000:
        return "Alternativa por debajo del objetivo anual; confirmar salario real por jornada."
    if isinstance(minimum, (int, float)) and minimum < 20000:
        return "El rango solo cumple parcialmente; confirmar salario real por jornada."
    return "Confirmar que es bruto fijo anual real para la jornada ofertada, no equivalente ni facturación."


def rank(offer):
    text = folded(offer.get("description", "") + " " + offer.get("contract", ""))
    alternative = any(x in text for x in ("autonomo", "alquiler de consulta", "pago por sesion"))
    raw = offer.get("salary_raw") or {}
    if isinstance(raw, dict) and raw.get("currency") == "EUR":
        value = raw.get("value")
        if isinstance(value, dict) and value.get("unitText") == "YEAR":
            maximum = value.get("maxValue", value.get("value"))
            if isinstance(maximum, (int, float)) and maximum < 20000:
                alternative = True
    # Requisitos excluyentes se filtran antes; autónomos y sueldos inferiores son alternativas.
    return (alternative, offer.get("priority", 3))


def redact(text):
    # El domicilio de origen nunca debe aparecer en los informes enviados.
    import re
    return re.sub(r"(?:calle\s+(?:de\s+)?)?delicias\s*[,º°n.\s-]*101", "[domicilio omitido]",
                  str(text), flags=re.I)


def render(day, selected, errors):
    lines = [f"Empleo psicología Madrid — {day}", "",
             "Búsqueda parcial: solo fuentes accesibles con datos JobPosting verificables.",
             "La extracción automática no acredita vigencia ni encaje confirmado; revisar antes de presentar candidatura.", ""]
    if not selected:
        lines.append("No se han podido confirmar novedades adecuadas en las fuentes consultadas. "
                     "Esto no acredita ausencia de ofertas.")
    for index, offer in enumerate(selected, 1):
        lines += [f"{index}. {offer['title']} — {offer['employer']}",
                  "Destacada para revisión" if index <= 3 else "Pendiente de revisión",
                  f"Enlace: {offer['url']}", f"Zona: {offer['location']}",
                  f"Publicación: {offer.get('published_at') or 'desconocida'}; detección: {day}",
                  f"Jornada: {offer['hours']}; contrato: {offer['contract']}; horario: {offer['schedule']}",
                  f"Salario publicado: {offer['salary']}", salary_note(offer),
                  f"Requisitos publicados: {offer['requirements'][:6000]}",
                  "Clasificación: " + ("alternativa con inconvenientes" if rank(offer)[0] else
                                        "interesante pendiente de aclaraciones") +
                  ". Prácticas supervisadas, no empleo clínico autónomo.",
                  f"Trayecto: {offer['commute']}", f"Opiniones laborales: {offer['workplace_evidence']}",
                  "Prioridad de ámbito: " + ("sanitaria" if offer.get("priority") == 1 else
                                             "envejecimiento" if offer.get("priority") == 2 else "por confirmar"),
                  "Preguntar: vigencia, vía de candidatura, salario fijo real, jornada, horario, "
                  "experiencia exigida y documentación sanitaria aceptada.",
                  "Experiencia que contrastar: 30–40 sesiones supervisadas en Princesa 81; "
                  "unos 20 talleres cognitivos en STIMA. Título solicitado; horas totales pendientes.",
                  "Siguiente paso: comprobar requisitos, ubicación y condiciones en el enlace; "
                  "no se ha enviado candidatura.", ""]
    if errors:
        lines += ["Limitaciones de acceso y extracción:"]
        lines += [f"- {e['source']}: {e['error']}" for e in errors]
    return redact("\n".join(lines) + "\n")


def prepare(state, offers, errors, day):
    state = copy.deepcopy(state)
    selected = []
    by_url = {}
    for row in state["offers"]:
        by_url[canonical(row["data"]["url"])] = row
    for alias in state["aliases"]:
        row = next((r for r in state["offers"] if r["id"] == alias["offer_id"]), None)
        if row:
            by_url[canonical(alias["url"])] = row
    seen = set()
    run_id = day + "-" + uuid.uuid4().hex[:12]
    for offer in sorted(offers, key=rank):
        url = canonical(offer["url"])
        if url in seen:
            continue
        seen.add(url)
        fp = fingerprint(offer)
        row = by_url.get(url)
        if row is None:
            # Fusionar portales solo cuando identidad y descripción coinciden exactamente.
            row = next((r for r in state["offers"] if all(
                folded(str(r["data"].get(k, ""))) == folded(str(offer.get(k, "")))
                for k in ("title", "employer", "location", "description"))
                and offer.get("description") and offer.get("employer") != "Por confirmar"), None)
            if row:
                state["aliases"].append({"url": url, "offer_id": row["id"]})
                # La URL alternativa por sí sola no constituye una versión nueva.
                offer = dict(offer, url=row["data"]["url"])
                fp = fingerprint(offer)
        if row and folded(row["status"]) in BLOCKED_STATUSES:
            continue
        changed = row is not None and fingerprint(row["data"]) != fp
        already_sent = row and (row.get("last_sent_fingerprint") == fp or
                               not changed and row.get("last_sent_fingerprint"))
        if row is None:
            row = {"id": hashlib.sha256(url.encode()).hexdigest()[:16], "first_seen": day,
                   "last_seen": day, "status": "pendiente", "data": offer,
                   "fingerprint": fp, "last_sent_fingerprint": None}
            state["offers"].append(row)
            state["aliases"].append({"url": url, "offer_id": row["id"]})
            by_url[url] = row
        else:
            row.update(last_seen=day, data=offer, fingerprint=fp)
        state["observations"].append({"run_id": run_id, "offer_id": row["id"],
                                      "fingerprint": fp, "data": offer,
                                      "change_kind": "changed" if changed else "seen"})
        if (not already_sent and eligibility(offer) and len(selected) < 10
                and row["id"] not in {o["offer_id"] for o in selected}):
            annotated = dict(offer, offer_id=row["id"], fingerprint=fp)
            if changed:
                annotated["title"] += " (cambio en datos publicados; revisar frente a versión anterior)"
            selected.append(annotated)
    report = render(day, selected, errors)
    state["runs"].append({"id": run_id, "local_date": day, "created_at": stamp(),
                           "data": {"errors": errors, "offers": selected, "partial": True},
                           "report": report})
    return state, selected, report, run_id


def run(store, state, offers, errors, day, recipients, *, gmail=None, send=False):
    recipient = recipients_key(recipients)
    if send and blocked(state, day, recipient):
        raise RuntimeError("Ya existe un envío o reserva para esa fecha; conciliar, no reenviar")
    state, selected, report, run_id = prepare(state, offers, errors, day)
    subject = f"Empleo psicología Madrid — {day} — {len(selected)} novedades (búsqueda parcial)"
    # Guardar el informe antes de reservar. Un fallo de almacenamiento impide enviar.
    store.save(state)
    if not send:
        return report
    if gmail is None:
        raise RuntimeError("Gmail no verificado")
    delivery = {"id": uuid.uuid4().hex, "run_id": run_id, "local_date": day,
                "recipient": recipient, "subject": subject, "state": "pending",
                "created_at": stamp(), "confirmed_at": None, "message_id": None,
                "body_sha256": body_hash(report),
                "selection": {o["offer_id"]: o["fingerprint"] for o in selected}}
    state["deliveries"].append(delivery)
    store.save(state)  # Confirmación persistente obligatoria ANTES de Gmail.
    try:
        message_id = gmail.send(subject, report, recipients)
    except Exception:
        delivery["state"] = "uncertain"
        store.save(state)
        raise RuntimeError("Envío incierto: conciliar Gmail; no reintentar") from None
    delivery.update(state="sent", message_id=message_id, confirmed_at=stamp())
    for row in state["offers"]:
        if row["id"] in delivery["selection"]:
            row["last_sent_fingerprint"] = delivery["selection"][row["id"]]
    store.save(state)  # Si falla, la reserva remota sigue bloqueando los reintentos.
    return report


def reconcile(store, state, gmail):
    count = 0
    for delivery in state["deliveries"]:
        if delivery["state"] in {"sent", "confirmed"} or not delivery.get("body_sha256"):
            continue
        message_id = gmail.reconcile(delivery)
        if message_id:
            delivery.update(state="sent", message_id=message_id, confirmed_at=stamp())
            for row in state["offers"]:
                fp = delivery.get("selection", {}).get(row["id"])
                if fp:
                    row["last_sent_fingerprint"] = fp
            count += 1
    if count:
        store.save(state)
    return count
