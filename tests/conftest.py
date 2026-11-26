import importlib.util
import os

import boto3
import pytest
from moto import mock_aws

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(*parts):
    """Muat app.py sebagai modul terpisah (semua service memakai nama file app.py)."""
    path = os.path.join(ROOT, *parts)
    spec = importlib.util.spec_from_file_location("_".join(parts), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def table(monkeypatch):
    monkeypatch.setenv("TABLE_NAME", "Packages")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="Packages",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "packageId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "packageId", "AttributeType": "S"}],
        )
        yield boto3.resource("dynamodb").Table("Packages")


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
