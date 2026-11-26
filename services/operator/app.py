"""operator-service: kelola paket wisata (padanan 'employee' di lab asli)."""
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

TABLE = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
ALLOWED_IPS = {ip.strip() for ip in os.environ.get("ALLOWED_IPS", "").split(",") if ip.strip()}

REQUIRED = ["name", "destination", "durationDays", "price", "quota"]
EDITABLE = {"name", "destination", "durationDays", "price", "quota", "remainingQuota",
            "costPrice", "operatorNotes", "status"}
STATUSES = {"ACTIVE", "INACTIVE"}


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


def authorized(event):
    """Cek admin key (header x-admin-key) dan IP sumber, kalau dikonfigurasi."""
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if ADMIN_KEY and not hmac.compare_digest(headers.get("x-admin-key", ""), ADMIN_KEY):
        return False
    source_ip = event.get("requestContext", {}).get("http", {}).get("sourceIp")
    if ALLOWED_IPS and source_ip not in ALLOWED_IPS:
        return False
    return True


def is_number(value):
    return isinstance(value, (int, Decimal)) and not isinstance(value, bool)


def is_whole(value):
    return is_number(value) and value == int(value)


def validate(pkg):
    """Kembalikan pesan error, atau None kalau data paket valid."""
    for field in ("name", "destination"):
        if not isinstance(pkg.get(field), str) or not pkg[field].strip():
            return f"{field} wajib berupa teks"
    if not is_whole(pkg.get("durationDays")) or pkg["durationDays"] < 1:
        return "durationDays harus bilangan bulat >= 1"
    if not is_number(pkg.get("price")) or pkg["price"] < 0:
        return "price harus angka >= 0"
    if "costPrice" in pkg and (not is_number(pkg["costPrice"]) or pkg["costPrice"] < 0):
        return "costPrice harus angka >= 0"
    if not is_whole(pkg.get("quota")) or pkg["quota"] < 1:
        return "quota harus bilangan bulat >= 1"
    remaining = pkg.get("remainingQuota")
    if not is_whole(remaining) or not 0 <= remaining <= pkg["quota"]:
        return "remainingQuota harus bilangan bulat antara 0 dan quota"
    if pkg.get("status") not in STATUSES:
        return "status harus ACTIVE atau INACTIVE"
    return None


def lambda_handler(event, context):
    route = event.get("routeKey", "")
    package_id = (event.get("pathParameters") or {}).get("id")

    if route == "GET /admin/health":
        return response(200, {"service": "operator", "status": "ok"})

    if not authorized(event):
        return response(403, {"message": "Akses operator ditolak"})

    try:
        body = json.loads(event.get("body") or "{}", parse_float=Decimal)
    except json.JSONDecodeError:
        return response(400, {"message": "Body harus berupa JSON"})
    if not isinstance(body, dict):
        return response(400, {"message": "Body harus berupa objek JSON"})

    if route == "GET /admin/packages":
        return response(200, TABLE.scan().get("Items", []))

    if route == "POST /admin/packages":
        missing = [f for f in REQUIRED if f not in body]
        if missing:
            return response(400, {"message": "Field wajib: " + ", ".join(missing)})
        pkg = {k: v for k, v in body.items() if k in EDITABLE}
        pkg.setdefault("remainingQuota", pkg["quota"])
        pkg.setdefault("status", "ACTIVE")
        error = validate(pkg)
        if error:
            return response(400, {"message": error})
        pkg.update({
            "packageId": str(uuid.uuid4()),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        TABLE.put_item(Item=pkg)
        return response(201, pkg)

    if route == "PUT /admin/packages/{id}":
        changes = {k: v for k, v in body.items() if k in EDITABLE}
        if not changes:
            return response(400, {"message": "Tidak ada field yang bisa diubah"})
        current = TABLE.get_item(Key={"packageId": package_id}).get("Item")
        if not current:
            return response(404, {"message": "Paket wisata tidak ditemukan"})
        updated = {**current, **changes}
        error = validate(updated)
        if error:
            return response(400, {"message": error})
        updated["updatedAt"] = datetime.now(timezone.utc).isoformat()
        try:
            TABLE.put_item(Item=updated, ConditionExpression="attribute_exists(packageId)")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return response(404, {"message": "Paket wisata tidak ditemukan"})
            raise
        return response(200, updated)

    if route == "DELETE /admin/packages/{id}":
        try:
            TABLE.delete_item(Key={"packageId": package_id}, ConditionExpression="attribute_exists(packageId)")
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return response(404, {"message": "Paket wisata tidak ditemukan"})
            raise
        return response(204)

    return response(404, {"message": f"Route tidak dikenal: {route}"})
