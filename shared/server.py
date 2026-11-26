"""Adapter HTTP untuk container: request HTTP -> event bergaya API Gateway -> lambda_handler.

Dengan adapter ini logika service (app.py) tetap sama, tinggal dijalankan sebagai web server
di ECS Fargate di belakang Application Load Balancer.
"""
import json
import os
import re
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, x-admin-key",
}


def compile_routes(route_keys):
    routes = []
    for key in route_keys:
        method, path = key.split(" ", 1)
        pattern = "^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "/?$"
        routes.append((method, re.compile(pattern), key))
    return routes


def client_ip(headers, peer_ip):
    # ALB menambahkan IP klien di posisi paling KANAN X-Forwarded-For.
    # Nilai di sebelah kirinya bisa dipalsukan klien, jadi yang dipakai yang paling kanan.
    forwarded = headers.get("X-Forwarded-For", "")
    return forwarded.split(",")[-1].strip() if forwarded else peer_ip


def make_server(handler, route_keys, port=None):
    routes = compile_routes(route_keys)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status, headers, body):
            data = body.encode("utf-8")
            self.send_response(status)
            for name, value in {**headers, **CORS}.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle(self):
            url = urlsplit(self.path)
            route_key, params = f"{self.command} {url.path}", None
            for method, regex, key in routes:
                match = regex.match(url.path)
                if match and method == self.command:
                    route_key, params = key, (match.groupdict() or None)
                    break
            length = int(self.headers.get("Content-Length") or 0)
            event = {
                "routeKey": route_key,
                "rawPath": url.path,
                "headers": dict(self.headers.items()),
                "queryStringParameters": dict(parse_qsl(url.query)) or None,
                "pathParameters": params,
                "body": self.rfile.read(length).decode("utf-8") if length else None,
                "requestContext": {"http": {"method": self.command, "path": url.path,
                                            "sourceIp": client_ip(self.headers, self.client_address[0])}},
            }
            try:
                res = handler(event, None)
            except Exception:
                traceback.print_exc()
                res = {"statusCode": 500, "headers": {"Content-Type": "application/json"},
                       "body": json.dumps({"message": "Internal Server Error"})}
            self._send(res["statusCode"], res.get("headers") or {}, res.get("body") or "")

        do_GET = do_POST = do_PUT = do_DELETE = _handle

        def do_OPTIONS(self):
            self._send(204, {}, "")

        def log_message(self, fmt, *args):
            sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
            sys.stdout.flush()

    return ThreadingHTTPServer(("0.0.0.0", int(port if port is not None else os.environ.get("PORT", 8080))), Handler)


def serve(handler, route_keys):
    server = make_server(handler, route_keys)
    print(f"listening on :{server.server_address[1]}", flush=True)
    server.serve_forever()
