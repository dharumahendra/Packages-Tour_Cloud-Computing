import importlib.util
import os
import sys

import boto3
import pytest
from moto import mock_aws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "shared"))


def load_module(*parts):
    """Muat app.py sebagai modul terpisah (semua service memakai nama file app.py)."""
    path = os.path.join(ROOT, *parts)
    spec = importlib.util.spec_from_file_location("_".join(parts), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def table(monkeypatch):
    """Bucket S3 tiruan (moto) tempat paket wisata disimpan."""
    monkeypatch.setenv("PACKAGES_BUCKET", "tour-packages-test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="tour-packages-test")
        yield "tour-packages-test"


@pytest.fixture
def catalog(table):
    return load_module("services", "catalog", "app.py")


@pytest.fixture
def operator(table, monkeypatch):
    monkeypatch.setenv("ADMIN_KEY", "rahasia")
    monkeypatch.setenv("ALLOWED_IPS", "")
    return load_module("services", "operator", "app.py")


@pytest.fixture
def monolith(table):
    return load_module("monolith", "src", "app.py")
