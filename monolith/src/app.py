"""MONOLIT (kondisi awal): satu fungsi untuk semua route paket wisata.

Masalah yang sengaja dibiarkan untuk dianalisis di Fase 2:
- semua route (baca publik & tulis) ada di satu fungsi,
- tidak ada kontrol akses: siapa pun bisa mengubah dan menghapus paket,
- data internal (costPrice, operatorNotes) ikut tampil ke publik.
"""
import json
import os
import uuid
from decimal import Decimal

import boto3

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])


def to_json(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Tipe tidak dikenal: {type(value)}")


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
        return response(200, TABLE.scan().get("Items", []))

    if route == "GET /packages/{id}":
        item = TABLE.get_item(Key={"packageId": package_id}).get("Item")
        return response(200, item) if item else response(404, {"message": "Paket wisata tidak ditemukan"})

    if route == "POST /packages":
        item = {**body, "packageId": str(uuid.uuid4())}
        item.setdefault("remainingQuota", item.get("quota", 0))
        item.setdefault("status", "ACTIVE")
        TABLE.put_item(Item=item)
        return response(201, item)

    if route == "PUT /packages/{id}":
        item = TABLE.get_item(Key={"packageId": package_id}).get("Item")
        if not item:
            return response(404, {"message": "Paket wisata tidak ditemukan"})
        item.update(body)
        TABLE.put_item(Item=item)
        return response(200, item)

    if route == "DELETE /packages/{id}":
        TABLE.delete_item(Key={"packageId": package_id})
        return response(204)

    return response(404, {"message": f"Route tidak dikenal: {route}"})
