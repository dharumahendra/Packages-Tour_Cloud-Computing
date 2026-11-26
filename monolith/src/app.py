"""MONOLIT (kondisi awal): satu aplikasi untuk semua route paket wisata.

Masalah yang sengaja dibiarkan untuk dianalisis di Fase 2:
- semua route (baca publik & tulis) ada di satu aplikasi,
- tidak ada kontrol akses: siapa pun bisa mengubah dan menghapus paket,
- data internal (costPrice, operatorNotes) ikut tampil ke publik,
- tidak ada validasi (sisa kuota boleh lebih besar dari kuota).

Jalankan lokal (tanpa AWS): python monolith/src/app.py  -> http://localhost:8080
"""
import json
import os
import sys
import uuid
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "shared"))

from store import make_store, to_json  # noqa: E402

STORE = make_store()
ROUTES = ["GET /health", "GET /packages", "POST /packages",
          "GET /packages/{id}", "PUT /packages/{id}", "DELETE /packages/{id}"]


def response(status, body=None):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": "" if body is None else json.dumps(body, default=to_json),
    }


def lambda_handler(event, context):
    route = event.get("routeKey", "")
    package_id = (event.get("pathParameters") or {}).get("id")
    body = json.loads(event.get("body") or "{}", parse_float=Decimal)

    if route == "GET /health":
        return response(200, {"service": "monolith", "status": "ok"})

    if route == "GET /packages":
        return response(200, STORE.scan())

    if route == "GET /packages/{id}":
        item = STORE.get(package_id)
        return response(200, item) if item else response(404, {"message": "Paket wisata tidak ditemukan"})

    if route == "POST /packages":
        item = {**body, "packageId": str(uuid.uuid4())}
        item.setdefault("remainingQuota", item.get("quota", 0))
        item.setdefault("status", "ACTIVE")
        STORE.put(item)
        return response(201, item)

    if route == "PUT /packages/{id}":
        item = STORE.get(package_id)
        if not item:
            return response(404, {"message": "Paket wisata tidak ditemukan"})
        item.update(body)
        STORE.put(item)
        return response(200, item)

    if route == "DELETE /packages/{id}":
        STORE.delete(package_id)
        return response(204)

    return response(404, {"message": f"Route tidak dikenal: {route}"})


if __name__ == "__main__":
    from server import serve

    serve(lambda_handler, ROUTES)
