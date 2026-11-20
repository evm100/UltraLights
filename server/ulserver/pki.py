"""Device PKI: root CA (meant to go offline), device intermediate, device and bridge
certificates, CRL, and a separate dev-only TLS CA for bench servers.

Layout of pki_dir:
    root-ca.crt     root certificate (public, keep forever)
    root-ca.key     root key: move it offline after init (ulctl ca export-root)
    device-ca.crt   intermediate that signs device and bridge certificates
    device-ca.key
    ca-chain.crt    device-ca + root, the broker's cafile for client verification
    crl.pem         revoked device certificates, signed by device-ca (broker crlfile)
    ul-server.crt/.key  the MQTT bridge's client certificate (CN ul-server)
"""
import datetime as dt
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ORG = "UltraLights"
UTC = dt.timezone.utc


class CsrError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code  # csr_invalid | device_mismatch
        self.detail = detail


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


def _name(cn: str, ou: str | None = None) -> x509.Name:
    attrs = [x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG)]
    if ou:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou))
    attrs.append(x509.NameAttribute(NameOID.COMMON_NAME, cn))
    return x509.Name(attrs)


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def cert_pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def pubkey_sha256(pub) -> str:
    der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


def _write(path: Path, data: bytes | str, secret: bool = False) -> None:
    if isinstance(data, str):
        data = data.encode()
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if secret else 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _ca_cert(subject: x509.Name, issuer: x509.Name, pub, sign_key, years: int, pathlen: int | None,
             issuer_pub=None) -> x509.Certificate:
    now = _now()
    b = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(pub)
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365 * years))
        .add_extension(x509.BasicConstraints(ca=True, path_length=pathlen), critical=True)
        .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                     content_commitment=False, key_encipherment=False, data_encipherment=False,
                                     key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
    )
    if issuer_pub is not None:
        b = b.add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_pub), critical=False)
    return b.sign(sign_key, hashes.SHA256())


@dataclass
class Issuer:
    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey


class DevicePKI:
    def __init__(self, pki_dir: Path):
        self.dir = Path(pki_dir)

    # ---- files ----
    def p(self, name: str) -> Path:
        return self.dir / name

    @property
    def initialized(self) -> bool:
        return self.p("device-ca.crt").exists() and self.p("device-ca.key").exists()

    def init(self) -> list[str]:
        """Create root, intermediate, chain and an empty CRL. Never overwrites. Returns created files."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o750)
        created = []
        if not self.p("root-ca.crt").exists():
            if self.p("device-ca.crt").exists():
                raise RuntimeError("device-ca exists without root-ca.crt; refusing to create a new root")
            rk = new_key()
            rn = _name("UltraLights Root CA")
            rc = _ca_cert(rn, rn, rk.public_key(), rk, years=25, pathlen=1)
            _write(self.p("root-ca.key"), key_pem(rk), secret=True)
            _write(self.p("root-ca.crt"), cert_pem(rc))
            created += ["root-ca.key", "root-ca.crt"]
        if not self.p("device-ca.crt").exists():
            if not self.p("root-ca.key").exists():
                raise RuntimeError("root-ca.key is offline; bring it back to create a new intermediate")
            rc = x509.load_pem_x509_certificate(self.p("root-ca.crt").read_bytes())
            rk = serialization.load_pem_private_key(self.p("root-ca.key").read_bytes(), None)
            ik = new_key()
            ic = _ca_cert(_name("UltraLights Device CA"), rc.subject, ik.public_key(), rk, years=15, pathlen=0,
                          issuer_pub=rc.public_key())
            _write(self.p("device-ca.key"), key_pem(ik), secret=True)
            _write(self.p("device-ca.crt"), cert_pem(ic))
            created += ["device-ca.key", "device-ca.crt"]
        chain = self.p("device-ca.crt").read_text() + self.p("root-ca.crt").read_text()
        if not self.p("ca-chain.crt").exists() or self.p("ca-chain.crt").read_text() != chain:
            _write(self.p("ca-chain.crt"), chain)
            created.append("ca-chain.crt")
        if not self.p("crl.pem").exists():
            self.write_crl([])
            created.append("crl.pem")
        return created

    def issuer(self) -> Issuer:
        return Issuer(
            x509.load_pem_x509_certificate(self.p("device-ca.crt").read_bytes()),
            serialization.load_pem_private_key(self.p("device-ca.key").read_bytes(), None),  # type: ignore[arg-type]
        )

    # ---- leaf certificates ----
    def _leaf(self, cn: str, pub, years: int) -> x509.Certificate:
        iss = self.issuer()
        now = _now()
        return (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, ORG),
                                     x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(iss.cert.subject)
            .public_key(pub)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(min(now + dt.timedelta(days=365 * years), iss.cert.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_agreement=True, content_commitment=False,
                                         key_encipherment=False, data_encipherment=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(pub), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(iss.cert.public_key()), critical=False)
            .sign(iss.key, hashes.SHA256())
        )

    @staticmethod
    def check_csr(csr_pem: str, device_id: str) -> x509.CertificateSigningRequest:
        try:
            csr = x509.load_pem_x509_csr(csr_pem.encode())
        except Exception as e:  # noqa: BLE001 - any parse failure is a bad CSR
            raise CsrError("csr_invalid", f"cannot parse CSR: {e.__class__.__name__}") from None
        if not csr.is_signature_valid:
            raise CsrError("csr_invalid", "CSR signature is not valid")
        pub = csr.public_key()
        if not isinstance(pub, ec.EllipticCurvePublicKey) or pub.curve.name != "secp256r1":
            raise CsrError("csr_invalid", "key must be EC P-256")
        cns = csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
        if len(cns) != 1:
            raise CsrError("csr_invalid", "CSR must have exactly one CN")
        if cns[0].value != device_id:
            raise CsrError("device_mismatch", "CSR CN does not match device_id")
        return csr

    def sign_device_csr(self, csr_pem: str, device_id: str, years: int = 10) -> x509.Certificate:
        csr = self.check_csr(csr_pem, device_id)
        return self._leaf(device_id, csr.public_key(), years)

    def issue_client(self, cn: str, years: int = 10) -> tuple[bytes, x509.Certificate]:
        """Key + cert for a non-device client (bridge ul-server, tests, simulated devices)."""
        k = new_key()
        return key_pem(k), self._leaf(cn, k.public_key(), years)

    def ensure_bridge_cert(self) -> bool:
        if self.p("ul-server.crt").exists() and self.p("ul-server.key").exists():
            return False
        k, c = self.issue_client("ul-server")
        _write(self.p("ul-server.key"), k, secret=True)
        _write(self.p("ul-server.crt"), cert_pem(c) + self.p("device-ca.crt").read_text())
        return True

    def chain_pem(self) -> str:
        return self.p("device-ca.crt").read_text()

    # ---- revocation ----
    def write_crl(self, revoked: list[tuple[int, dt.datetime]]) -> None:
        iss = self.issuer()
        now = _now()
        b = (x509.CertificateRevocationListBuilder()
             .issuer_name(iss.cert.subject)
             .last_update(now - dt.timedelta(minutes=5))
             # The broker rejects every client once the CRL expires, so it is long-lived and
             # rewritten on each revocation (and on every API start).
             .next_update(now + dt.timedelta(days=3650)))
        for serial, at in revoked:
            b = b.add_revoked_certificate(
                x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(at).build())
        crl = b.sign(iss.key, hashes.SHA256())
        _write(self.p("crl.pem"), crl.public_bytes(serialization.Encoding.PEM))


def init_dev_tls(dir_: Path, hosts: list[str]) -> dict[str, Path]:
    """A throwaway TLS CA plus a server certificate for the dev host names (nginx and the broker).
    Nodes trust it through provision.server_ca. Regenerates the server cert if hosts change."""
    dir_.mkdir(parents=True, exist_ok=True)
    ca_crt, ca_key = dir_ / "tls-ca.crt", dir_ / "tls-ca.key"
    if not ca_crt.exists():
        k = new_key()
        n = _name("UltraLights Dev TLS CA", "dev only")
        c = _ca_cert(n, n, k.public_key(), k, years=10, pathlen=0)
        _write(ca_key, key_pem(k), secret=True)
        _write(ca_crt, cert_pem(c))
    srv_crt, srv_key = dir_ / "server.crt", dir_ / "server.key"
    want = sorted(hosts)
    if srv_crt.exists():
        cur = x509.load_pem_x509_certificate(srv_crt.read_bytes())
        names = sorted(cur.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName))
        if names == want:
            return {"ca": ca_crt, "cert": srv_crt, "key": srv_key}
    ca = x509.load_pem_x509_certificate(ca_crt.read_bytes())
    cak = serialization.load_pem_private_key(ca_key.read_bytes(), None)
    k = new_key()
    now = _now()
    c = (
        x509.CertificateBuilder()
        .subject_name(_name(hosts[0], "dev only"))
        .issuer_name(ca.subject)
        .public_key(k.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=825))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(h) for h in hosts]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key()), critical=False)
        .sign(cak, hashes.SHA256())  # type: ignore[arg-type]
    )
    _write(srv_key, key_pem(k), secret=True)
    _write(srv_crt, cert_pem(c) + cert_pem(ca))
    return {"ca": ca_crt, "cert": srv_crt, "key": srv_key}
