"""catalog-service: katalog paket wisata untuk wisatawan (read-only, padanan 'customer' di lab asli)."""
import json

from store import make_store, to_json

STORE = make_store()
VERSION = "1.1"
ROUTES = ["GET /health", "GET /packages", "GET /packages/{id}"]
# Field internal (harga modal, catatan operator) tidak ditampilkan ke wisatawan
PUBLIC_FIELDS = ["packageId", "name", "destination", "durationDays", "price", "quota", "remainingQuota", "status"]


def response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=to_json),
    }


def public_view(item):
    return {k: item[k] for k in PUBLIC_FIELDS if k in item}


def lambda_handler(event, context):
    route = event.get("routeKey", "")

    if route == "GET /health":
        return response(200, {"service": "catalog", "version": VERSION, "status": "ok"})

    if route == "GET /packages":
        items = [i for i in STORE.scan() if i.get("status") == "ACTIVE"]
        destination = (event.get("queryStringParameters") or {}).get("destination")
        if destination:
            items = [i for i in items if destination.lower() in str(i.get("destination", "")).lower()]
        items.sort(key=lambda i: i.get("price", 0))
        return response(200, [public_view(i) for i in items])

    if route == "GET /packages/{id}":
        package_id = (event.get("pathParameters") or {}).get("id")
        item = STORE.get(package_id)
        if not item or item.get("status") != "ACTIVE":
            return response(404, {"message": "Paket wisata tidak ditemukan"})
        return response(200, public_view(item))

    return response(404, {"message": f"Route tidak dikenal: {route}"})


if __name__ == "__main__":
    from server import serve

    serve(lambda_handler, ROUTES)
