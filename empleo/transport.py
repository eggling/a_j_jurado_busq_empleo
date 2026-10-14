"""HTTP con TLS verificado, límites y errores sin cuerpos ni credenciales."""
import json
import urllib.error
import urllib.parse
import urllib.request


class HTTPFailure(RuntimeError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"HTTP {status}")


def request(url, *, method="GET", headers=None, body=None, limit=4_000_000):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("Se requiere una URL HTTPS sin credenciales")
    # Las fuentes remotas nunca deben provocar solicitudes a la red privada.
    import ipaddress
    import socket
    try:
        destinations = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise RuntimeError("DNS del destino no disponible") from None
    for result in destinations:
        if not ipaddress.ip_address(result[4][0]).is_global:
            raise ValueError("Destino no público")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            # No reenviar Authorization ni aceptar destinos elegidos por otra página.
            return None

    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"User-Agent": "EmpleoMadrid/1.0", **(headers or {})})
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=25) as response:
            raw = response.read(limit + 1)
            if len(raw) > limit:
                raise RuntimeError("Respuesta demasiado grande")
            return raw
    except urllib.error.HTTPError as exc:
        raise HTTPFailure(exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Conexión HTTPS no disponible") from None


def request_json(url, *, method="GET", headers=None, data=None, form=None):
    h = dict(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    return json.loads(request(url, method=method, headers=h, body=body))
