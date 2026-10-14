"""python -m empleo --help"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .gmail import Gmail
from .runner import TZ, blocked, recipients_key, reconcile, run
from .search import discover
from .storage import GitHubStore, LocalStore, seed

ROOT = Path(__file__).resolve().parent.parent
RECIPIENTS = ["davidmeca93@gmail.com", "antoniojose.23.juor@gmail.com"]
GATES = ("CLOUD_ACTIVATION_APPROVED", "LOCAL_TASK_DISABLED", "FINAL_HISTORY_SYNCED",
         "DURABLE_STORAGE_VERIFIED")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Búsqueda de empleo; no envía por defecto")
    parser.add_argument("command", choices=["init", "run", "reconcile", "check"])
    parser.add_argument("--store", choices=["local", "github"], default="local")
    parser.add_argument("--state", default=str(ROOT / "data/registro.json"))
    parser.add_argument("--seed", default=str(ROOT / "traspaso/registro-unificado.json"))
    parser.add_argument("--fixtures", type=Path, help="Datos de prueba; incompatible con envío")
    parser.add_argument("--send", action="store_true", help="Requiere Gmail y cuatro controles de activación")
    parser.add_argument("--report", type=Path, default=ROOT / "reports/ultimo-informe.md")
    args = parser.parse_args(argv)
    os.umask(0o077)
    if os.environ.get("SCHEDULED") == "true" and datetime.now(TZ).hour != 9:
        print("Tick fuera de las 09:00 Europe/Madrid; no ejecutar.")
        return 0
    if args.send and (args.fixtures or args.command != "run"):
        parser.error("--send solo es válido para una búsqueda real sin fixtures")
    if args.store == "github":
        repo = os.environ.get("GITHUB_REPOSITORY")
        token = os.environ.get("GITHUB_TOKEN")
        if not repo or not token:
            raise RuntimeError("Faltan GITHUB_REPOSITORY y GITHUB_TOKEN")
        store = GitHubStore(repo, token, os.environ.get("STATE_BRANCH", "main"))
        store.require_private()
    else:
        store = LocalStore(args.state)
    state = store.load()
    if state is None:
        state = seed(args.seed)
        store.save(state)
    if args.command == "init":
        print("Registro disponible; historial existente preservado.")
        return 0
    if args.command == "reconcile":
        count = reconcile(store, state, Gmail.from_environment())
        print(f"Envíos conciliados positivamente: {count}. El resto permanece bloqueado.")
        return 0
    if args.send:
        missing = [name for name in GATES if os.environ.get(name) != "true"]
        if missing:
            raise RuntimeError("Activación bloqueada: " + ", ".join(missing))
        if args.store == "local" and os.environ.get("ALLOW_LOCAL_SEND") != "true":
            raise RuntimeError("El envío local requiere ALLOW_LOCAL_SEND=true y volumen persistente verificado")
    today = datetime.now(TZ).date()
    if args.send and blocked(state, today.isoformat(), recipients_key(RECIPIENTS)):
        print("Ya hay envío o reserva para hoy. No se envía de nuevo; conciliar si procede.")
        return 0
    gmail = Gmail.from_environment() if args.send or args.command == "check" else None
    if args.command == "check":
        # La comprobación valida lectura y scopes de envío sin mandar un correo.
        offers, errors = discover(today)
        successful_sources = len(offers)
        print(json.dumps({"gmail_read_and_send_scopes": True,
                          "extracted_offers": successful_sources, "source_errors": len(errors)}))
        if not successful_sources:
            raise RuntimeError("No se ha demostrado extracción web útil; revisar fuentes")
        return 0
    if args.fixtures:
        fixture = json.loads(args.fixtures.read_text())
        offers, errors = fixture["offers"], fixture.get("errors", [])
    else:
        offers, errors = discover(today, initial=not state["runs"])
    report = run(store, state, offers, errors, today.isoformat(), RECIPIENTS,
                 gmail=gmail, send=args.send)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report)
    args.report.chmod(0o600)
    print(f"Informe guardado: {args.report}; modo: {'envío' if args.send else 'sin envío'}")
    if errors:
        print(f"Búsqueda parcial: {len(errors)} incidencias, detalladas en el informe.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
