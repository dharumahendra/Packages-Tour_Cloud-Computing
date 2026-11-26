"""Penyimpanan paket wisata: satu objek JSON per paket.

- Di AWS: bucket S3 (env PACKAGES_BUCKET). Role lab yang bisa dipakai ECS task (PipelineRole)
  punya akses S3, tapi tidak punya akses DynamoDB, jadi data disimpan di S3.
- Lokal (demo monolit tanpa AWS): folder di disk (env PACKAGES_DIR, default ./.data).
"""
import json
import os
import re
from decimal import Decimal

PACKAGE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def to_json(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(f"Tipe tidak dikenal: {type(value)}")


def dumps(item):
    return json.dumps(item, default=to_json)


def loads(raw):
    return json.loads(raw, parse_float=Decimal)


def valid_id(package_id):
    return isinstance(package_id, str) and bool(PACKAGE_ID.match(package_id))


class S3Store:
    def __init__(self, bucket, prefix="packages/"):
        import boto3

        self.bucket = bucket
        self.prefix = prefix
        self.s3 = boto3.client("s3")

    def _key(self, package_id):
        return f"{self.prefix}{package_id}.json"

    def _read(self, key):
        try:
            raw = self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        return loads(raw)

    def scan(self):
        items = []
        pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix)
        for page in pages:
            for obj in page.get("Contents", []):
                item = self._read(obj["Key"])
                if item is not None:
                    items.append(item)
        return items

    def get(self, package_id):
        return self._read(self._key(package_id)) if valid_id(package_id) else None

    def put(self, item):
        self.s3.put_object(Bucket=self.bucket, Key=self._key(item["packageId"]),
                           Body=dumps(item).encode("utf-8"), ContentType="application/json")

    def delete(self, package_id):
        """Hapus paket; False kalau paket tidak ada."""
        if self.get(package_id) is None:
            return False
        self.s3.delete_object(Bucket=self.bucket, Key=self._key(package_id))
        return True


class LocalStore:
    def __init__(self, directory):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, package_id):
        return os.path.join(self.directory, f"{package_id}.json")

    def scan(self):
        items = []
        for name in sorted(os.listdir(self.directory)):
            if name.endswith(".json"):
                with open(os.path.join(self.directory, name), encoding="utf-8") as f:
                    items.append(loads(f.read()))
        return items

    def get(self, package_id):
        if not valid_id(package_id) or not os.path.exists(self._path(package_id)):
            return None
        with open(self._path(package_id), encoding="utf-8") as f:
            return loads(f.read())

    def put(self, item):
        with open(self._path(item["packageId"]), "w", encoding="utf-8") as f:
            f.write(dumps(item))

    def delete(self, package_id):
        if self.get(package_id) is None:
            return False
        os.remove(self._path(package_id))
        return True


def make_store():
    bucket = os.environ.get("PACKAGES_BUCKET")
    if bucket:
        return S3Store(bucket)
    return LocalStore(os.environ.get("PACKAGES_DIR", ".data"))
