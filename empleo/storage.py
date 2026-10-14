"""Almacenamiento con compare-and-swap: nunca enviar sin guardar la reserva."""
import base64
import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .transport import request_json


class Conflict(RuntimeError):
    pass


def encode(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


class LocalStore:
    def __init__(self, path):
        self.path = Path(path)
        self.version = None

    def load(self):
        raw = self.path.read_bytes() if self.path.exists() else None
        self.version = hashlib.sha256(raw).hexdigest() if raw is not None else None
        return json.loads(raw) if raw is not None else None

    def save(self, data):
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(str(self.path) + ".lock", "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            raw = self.path.read_bytes() if self.path.exists() else None
            current = hashlib.sha256(raw).hexdigest() if raw is not None else None
            if current != self.version:
                raise Conflict("El registro cambió durante la ejecución")
            raw = encode(data)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(tmp, self.path)
                directory = os.open(self.path.parent, os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self.version = hashlib.sha256(raw).hexdigest()


class GitHubStore:
    """Un único archivo en GitHub. Cada escritura usa el SHA leído, sin reintentos."""

    def __init__(self, repository, token, branch="main", path="estado/registro.json"):
        from urllib.parse import quote

        if len(repository.split("/")) != 2:
            raise ValueError("Repositorio inválido")
        self.repo_url = "https://api.github.com/repos/" + repository
        self.url = self.repo_url + "/contents/" + quote(path, safe="/")
        self.branch = branch
        self.headers = {"Authorization": "Bearer " + token,
                        "Accept": "application/vnd.github+json"}
        self.sha = None

    def require_private(self):
        if request_json(self.repo_url, headers=self.headers).get("private") is not True:
            raise RuntimeError("El registro operativo requiere un repositorio privado")

    def load(self):
        from urllib.parse import quote
        from .transport import HTTPFailure

        try:
            data = request_json(self.url + "?ref=" + quote(self.branch, safe=""),
                                headers=self.headers)
        except HTTPFailure as exc:
            if exc.status == 404:
                self.sha = None
                return None
            raise
        self.sha = data["sha"]
        if data.get("encoding") == "base64" and data.get("content"):
            raw = base64.b64decode(data["content"])
        else:
            # Contents omite el contenido de archivos de más de 1 MB.
            from .transport import request
            raw = request(self.url + "?ref=" + quote(self.branch, safe=""),
                          headers={**self.headers, "Accept": "application/vnd.github.raw+json"},
                          limit=50_000_000)
        return json.loads(raw)

    def save(self, data):
        payload = {"message": "Actualiza registro de búsqueda de empleo",
                   "branch": self.branch,
                   "content": base64.b64encode(encode(data)).decode()}
        if self.sha:
            payload["sha"] = self.sha
        response = request_json(self.url, method="PUT", headers=self.headers, data=payload)
        self.sha = response["content"]["sha"]


def seed(path):
    data = json.loads(Path(path).read_text())
    for key in ("offers", "aliases", "runs", "observations", "deliveries", "events"):
        if not isinstance(data.get(key), list):
            raise ValueError("Historial inválido: " + key)
    return copy.deepcopy(data)
