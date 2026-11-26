#!/usr/bin/env bash
# Tes end-to-end kedua microservice.
# Pakai: ./scripts/e2e.sh <API_URL> <ADMIN_KEY>
set -uo pipefail
API=${1:?API URL}
KEY=${2:?ADMIN KEY}
J="Content-Type: application/json"

echo "== catalog: health";                curl -s "$API/health"; echo
echo "== operator: health";               curl -s "$API/admin/health"; echo
echo "== operator tanpa key (harus 403)"; curl -s -o /dev/null -w "%{http_code}\n" "$API/admin/packages"

echo "== operator: buat paket (201)"
RESP=$(curl -s -X POST "$API/admin/packages" -H "$J" -H "x-admin-key: $KEY" \
  -d '{"name":"Snorkeling Pulau Seribu 2D1N","destination":"Kepulauan Seribu","durationDays":2,"price":850000,"costPrice":600000,"quota":20,"operatorNotes":"Kapal berangkat 07.00"}')
echo "$RESP"
ID=$(echo "$RESP" | python3 -c 'import sys, json; print(json.load(sys.stdin)["packageId"])')

echo "== catalog: daftar paket (tanpa costPrice/operatorNotes)"; curl -s "$API/packages"; echo
echo "== catalog: filter destinasi";      curl -s "$API/packages?destination=seribu"; echo
echo "== catalog: detail paket";          curl -s "$API/packages/$ID"; echo
echo "== operator: semua data (dengan costPrice)"; curl -s "$API/admin/packages" -H "x-admin-key: $KEY"; echo

echo "== operator: ubah sisa kuota (200)"
curl -s -X PUT "$API/admin/packages/$ID" -H "$J" -H "x-admin-key: $KEY" -d '{"remainingQuota":3}'; echo
echo "== operator: sisa kuota > kuota (harus 400)"
curl -s -o /dev/null -w "%{http_code}\n" -X PUT "$API/admin/packages/$ID" -H "$J" -H "x-admin-key: $KEY" -d '{"remainingQuota":99}'

echo "== operator: nonaktifkan paket (200)"
curl -s -o /dev/null -w "%{http_code}\n" -X PUT "$API/admin/packages/$ID" -H "$J" -H "x-admin-key: $KEY" -d '{"status":"INACTIVE"}'
echo "== catalog: paket nonaktif disembunyikan (harus 404)"
curl -s -o /dev/null -w "%{http_code}\n" "$API/packages/$ID"

echo "== operator: hapus paket (204)"
curl -s -o /dev/null -w "%{http_code}\n" -X DELETE "$API/admin/packages/$ID" -H "x-admin-key: $KEY"
