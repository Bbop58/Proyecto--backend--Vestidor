"""Ephemeral HTTPS S3 transport double, exclusively for local recovery rehearsal.

This validates production boto3 transport, not a provider's authentication or
encryption-at-rest guarantees. It must never be used as application storage.
"""
import hashlib
import ipaddress
import ssl
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


class LocalS3:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.objects = {}
        self.fail_next_put = False
        self.hold_put = False
        self.started = threading.Event()
        self.release = threading.Event()
        self.lock = threading.Lock()

    def __enter__(self):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Local Backup QA')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
        self.ca_file = self.directory / 'qa-ca.pem'
        private_file = self.directory / 'qa-key.pem'
        self.ca_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass  # Never log signatures, credentials or payloads.

            def respond(self, status, body=b''):
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'application/octet-stream')
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def object_key(self):
                if 'Credential=QA_LOCAL_ACCESS/' not in self.headers.get('Authorization', ''):
                    self.respond(403); return None
                path = unquote(urlsplit(self.path).path)
                prefix = '/qa-private-backups/'
                if not path.startswith(prefix) or '..' in path.split('/'):
                    self.respond(404); return None
                return path[len(prefix):]

            def do_PUT(self):
                name = self.object_key()
                if name is None: return
                length = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= length <= 10485760:
                    self.respond(400); return
                content = self.rfile.read(length)
                with owner.lock:
                    fail, owner.fail_next_put = owner.fail_next_put, False
                if fail:
                    self.respond(503); return
                if owner.hold_put:
                    owner.started.set()
                    if not owner.release.wait(30):
                        self.respond(503); return
                with owner.lock:
                    owner.objects[name] = content
                self.respond(200)

            def do_GET(self):
                name = self.object_key()
                if name is None: return
                with owner.lock:
                    content = owner.objects.get(name)
                self.respond(404 if content is None else 200, content or b'')

            def do_DELETE(self):
                name = self.object_key()
                if name is None: return
                with owner.lock:
                    owner.objects.pop(name, None)
                self.respond(204)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.ca_file, private_file)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.endpoint = f'https://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
