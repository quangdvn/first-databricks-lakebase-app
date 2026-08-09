"""
One-time setup script: creates the Databricks secret scope and stores the
Massive API key. Run this locally (with the Databricks CLI configured) or
from a notebook - never commit the resulting secret value anywhere.

Usage:
    python setup_secrets.py
"""

import getpass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service import workspace

w = WorkspaceClient()

print("Current scopes: ")
for scope in w.secrets.list_scopes():
    print(f" - {scope}")

w.secrets.create_scope(scope="massive")
w.secrets.put_secret(
    scope="massive",
    key="api-key",
    string_value=getpass.getpass("Paste your Massive API key: "),
)

w.secrets.create_scope(scope="database")
w.secrets.put_secret(
    scope="database",
    key="lakebase-url",
    string_value=getpass.getpass("Paste your Lakebase URL: "),
)


w.secrets.put_acl(
    scope="database",
    principal="users",
    permission=workspace.AclPermission.READ,
)

w.secrets.put_acl(
    scope="massive",
    principal="users",
    permission=workspace.AclPermission.READ,
)

print("After scopes: ")
for scope in w.secrets.list_scopes():
    print(f" - {scope}")
