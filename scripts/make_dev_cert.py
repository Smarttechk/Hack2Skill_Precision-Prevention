"""
Make a self-signed certificate so the demo server can run over HTTPS on your own network.

    python scripts/make_dev_cert.py                 writes config/tls/cert.pem and config/tls/key.pem
    python server.py --host 0.0.0.0 --certfile config/tls/cert.pem --keyfile config/tls/key.pem

Browsers warn about a self-signed certificate (accept it once). A pilot uses the clinic's real certificate.
Keep key.pem private: it is the server's secret key.
"""
import datetime
import ipaddress
import os
import socket

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", "tls")


def main():
    os.makedirs(OUT, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    host = socket.gethostname()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    alt = [x509.DNSName("localhost"), x509.DNSName(host), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=90))
            .add_extension(x509.SubjectAlternativeName(alt), critical=False)
            .sign(key, hashes.SHA256()))
    with open(os.path.join(OUT, "key.pem"), "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    with open(os.path.join(OUT, "cert.pem"), "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    print(f"Wrote {OUT}\\cert.pem and key.pem (valid 90 days, for localhost and {host})")


if __name__ == "__main__":
    main()
