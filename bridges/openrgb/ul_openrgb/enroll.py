"""Enrollment: the same HTTPS exchange a node does (contracts/schemas/enroll-request.schema.json,
bridge branch). The private key is made here and never leaves this machine."""
import http.client
import json
import os
import socket
import ssl
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from . import __version__


class EnrollError(RuntimeError):
    pass


class _HTTPS(http.client.HTTPSConnection):
    """HTTPS to `connect` (an IP, dev only) while checking the certificate against the server name."""

    def __init__(self, server: str, connect: str | None, port: int, context: ssl.SSLContext):
        super().__init__(server, port, context=context, timeout=20)
        self._server, self._connect = server, connect

    def connect(self) -> None:
        sock = socket.create_connection((self._connect or self._server, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._server)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def enroll(server: str, device_id: str, token: str, key_file: Path, cert_file: Path,
           server_ca: str | None = None, connect: str | None = None, port: int = 443) -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
           .sign(key, hashes.SHA256()))
    body = {"device_id": device_id, "token": token, "csr": csr.public_bytes(serialization.Encoding.PEM).decode(),
            "bridge": "openrgb", "fw": __version__, "host": socket.gethostname()[:64]}
    ctx = ssl.create_default_context(cafile=server_ca) if server_ca else ssl.create_default_context()
    conn = _HTTPS(server, connect, port, ctx)
    try:
        conn.request("POST", "/api/v1/enroll", json.dumps(body), {"Content-Type": "application/json"})
        r = conn.getresponse()
        raw = r.read()
    except (OSError, ssl.SSLError) as e:
        raise EnrollError(f"cannot reach https://{server}:{port}: {e}") from None
    finally:
        conn.close()
    try:
        res = json.loads(raw)
    except ValueError:
        raise EnrollError(f"server answered {r.status} with something that isn't JSON") from None
    if r.status != 200:
        raise EnrollError(f"{res.get('error', r.status)}: {res.get('detail', '')}".rstrip(": "))
    _write_private(key_file, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    cert_file.write_text(res["cert"] + res["ca_chain"])
    os.chmod(cert_file, 0o644)
    if os.geteuid() == 0:
        # Run with sudo: hand the identity to whoever owns the state dir (the service user).
        st = key_file.parent.stat()
        for f in (key_file, cert_file):
            os.chown(f, st.st_uid, st.st_gid)
    return res
