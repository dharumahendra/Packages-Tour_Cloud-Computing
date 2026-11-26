import json

KEY = {"x-admin-key": "rahasia"}
PACKAGE = {
    "name": "Snorkeling Pulau Seribu 2D1N",
    "destination": "Kepulauan Seribu",
    "durationDays": 2,
    "price": 850000,
    "costPrice": 600000,
    "quota": 20,
    "operatorNotes": "Kapal berangkat 07.00 dari Marina Ancol",
}


def call(svc, route, body=None, id=None, headers=None, ip="1.2.3.4", query=None, raw_body=None):
    event = {"routeKey": route, "headers": headers or {}, "requestContext": {"http": {"sourceIp": ip}}}
    if raw_body is not None:
        event["body"] = raw_body
    elif body is not None:
        event["body"] = json.dumps(body)
    if id:
        event["pathParameters"] = {"id": id}
    if query:
        event["queryStringParameters"] = query
    res = svc.lambda_handler(event, None)
    return res["statusCode"], (json.loads(res["body"]) if res["body"] else None)


def create(operator, **overrides):
    status, pkg = call(operator, "POST /admin/packages", {**PACKAGE, **overrides}, headers=KEY)
    assert status == 201, pkg
    return pkg


def test_health(catalog, operator):
    assert call(catalog, "GET /health")[0] == 200
    assert call(operator, "GET /admin/health")[0] == 200


def test_operator_requires_key(operator):
    assert call(operator, "GET /admin/packages")[0] == 403
    assert call(operator, "GET /admin/packages", headers={"X-Admin-Key": "salah"})[0] == 403
    assert call(operator, "GET /admin/packages", headers={"X-Admin-Key": "rahasia"})[0] == 200


def test_operator_ip_allowlist(table, monkeypatch):
    from conftest import load_module
    monkeypatch.setenv("ADMIN_KEY", "")
    monkeypatch.setenv("ALLOWED_IPS", "10.0.0.1, 10.0.0.2")
    svc = load_module("services", "operator", "app.py")
    assert call(svc, "GET /admin/packages", ip="1.2.3.4")[0] == 403
    assert call(svc, "GET /admin/packages", ip="10.0.0.2")[0] == 200


def test_validation(operator):
    status, body = call(operator, "POST /admin/packages", {"name": "Tanpa detail"}, headers=KEY)
    assert status == 400 and "destination" in body["message"]
    assert call(operator, "POST /admin/packages", {**PACKAGE, "quota": 0}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", {**PACKAGE, "durationDays": 1.5}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", {**PACKAGE, "price": -1}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", {**PACKAGE, "remainingQuota": 25}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", {**PACKAGE, "status": "DRAFT"}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", {**PACKAGE, "price": True}, headers=KEY)[0] == 400
    assert call(operator, "POST /admin/packages", raw_body="{rusak", headers=KEY)[0] == 400


def test_full_flow_across_services(catalog, operator):
    pkg = create(operator)
    assert pkg["remainingQuota"] == 20 and pkg["status"] == "ACTIVE"
    pid = pkg["packageId"]

    status, public = call(catalog, "GET /packages/{id}", id=pid)
    assert status == 200
    assert "costPrice" not in public and "operatorNotes" not in public
    assert public["price"] == 850000

    create(operator, name="Wisata Kawah Ijen", destination="Banyuwangi", price=450000)
    status, items = call(catalog, "GET /packages", query={"destination": "seribu"})
    assert status == 200 and [i["packageId"] for i in items] == [pid]
    status, items = call(catalog, "GET /packages")
    assert [i["price"] for i in items] == [450000, 850000]  # urut harga termurah

    status, updated = call(operator, "PUT /admin/packages/{id}", {"remainingQuota": 3, "price": 799000.5},
                           id=pid, headers=KEY)
    assert status == 200 and updated["remainingQuota"] == 3 and updated["price"] == 799000.5
    assert call(operator, "PUT /admin/packages/{id}", {"remainingQuota": 99}, id=pid, headers=KEY)[0] == 400
    assert call(operator, "PUT /admin/packages/{id}", {"quota": 2}, id=pid, headers=KEY)[0] == 400
    assert call(operator, "PUT /admin/packages/{id}", {"status": "ACTIVE"}, id="tidak-ada", headers=KEY)[0] == 404

    assert call(operator, "PUT /admin/packages/{id}", {"status": "INACTIVE"}, id=pid, headers=KEY)[0] == 200
    assert call(catalog, "GET /packages/{id}", id=pid)[0] == 404
    assert len(call(catalog, "GET /packages")[1]) == 1
    assert len(call(operator, "GET /admin/packages", headers=KEY)[1]) == 2

    assert call(operator, "DELETE /admin/packages/{id}", id=pid, headers=KEY)[0] == 204
    assert call(operator, "DELETE /admin/packages/{id}", id=pid, headers=KEY)[0] == 404


def test_http_adapter_routes_and_client_ip(catalog, operator):
    """Adapter HTTP (dipakai di container ECS) meneruskan route, query, dan IP klien dengan benar."""
    import threading
    import urllib.request

    from server import make_server

    seen = {}

    def spy(event, context):
        seen.update(event)
        return operator.lambda_handler(event, context)

    server = make_server(spy, operator.ROUTES, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/admin/packages/abc-123?x=1"
        req = urllib.request.Request(url, method="DELETE", headers={
            "x-admin-key": "rahasia", "X-Forwarded-For": "6.6.6.6, 9.9.9.9"})
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as e:
            assert e.code == 404  # paket tidak ada
        assert seen["routeKey"] == "DELETE /admin/packages/{id}"
        assert seen["pathParameters"] == {"id": "abc-123"}
        assert seen["queryStringParameters"] == {"x": "1"}
        # IP paling kanan = IP yang ditambahkan ALB; yang kiri bisa dipalsukan klien
        assert seen["requestContext"]["http"]["sourceIp"] == "9.9.9.9"
    finally:
        server.shutdown()


def test_monolith_has_no_access_control(monolith):
    status, pkg = call(monolith, "POST /packages", PACKAGE)
    assert status == 201
    assert "costPrice" in call(monolith, "GET /packages/{id}", id=pkg["packageId"])[1]
    assert call(monolith, "DELETE /packages/{id}", id=pkg["packageId"])[0] == 204
