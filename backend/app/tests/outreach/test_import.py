from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.api.routes import outreach as routes
from app.models.outreach import OutreachContact
from app.tests.outreach.helpers import make_contact

PATH = "/api/v1/outreach/import"


def _upload(client: TestClient, headers: dict[str, str], body: str | bytes, name: str = "list.csv"):
    data = body.encode() if isinstance(body, str) else body
    return client.post(PATH, headers=headers, files={"file": (name, data, "text/csv")})


def test_import_inserts_valid_rows_and_counts_invalid_and_duplicates(
    client: TestClient, session: Session, admin_headers: dict[str, str]
) -> None:
    make_contact(session, "old@example.com")
    csv_text = (
        "﻿E-mail, Full  Name ,Organisation,Sector\r\n"
        "New@Example.com,Asha Rao,Acme,Fintech\r\n"
        "not-an-email,Bad,,\r\n"
        "old@example.com,Old,,\r\n"
        "new@example.com,Dup,,\r\n"
        "\r\n"
        '"quoted@example.com","Rao, ""Q""",,\n'
    )

    res = _upload(client, admin_headers, csv_text)

    assert res.status_code == 200
    assert res.json() == {"success": True, "total": 5, "inserted": 2, "duplicates": 2, "invalid": 1}
    row = session.exec(
        select(OutreachContact).where(OutreachContact.email == "new@example.com")
    ).one()
    assert row.status == "pending"
    assert row.source == "excel_import_v1"
    assert row.name == "Asha Rao"
    assert row.company == "Acme"
    assert row.industry == "Fintech"
    assert len(row.magic_token) == 43
    quoted = session.exec(
        select(OutreachContact).where(OutreachContact.email == "quoted@example.com")
    ).one()
    assert quoted.name == 'Rao, "Q"'
    assert quoted.company is None


def test_import_requires_admin(client: TestClient) -> None:
    res = client.post(PATH, files={"file": ("a.csv", b"Email\na@b.co\n", "text/csv")})

    assert res.status_code == 401


def test_import_requires_a_file(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = client.post(PATH, headers=admin_headers)

    assert res.status_code == 400
    assert res.json()["detail"] == "No file uploaded."


def test_import_rejects_files_over_two_megabytes(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = _upload(client, admin_headers, b"x" * (routes.MAX_FILE_BYTES + 1))

    assert res.status_code == 413
    assert res.json()["detail"] == "File too large (max 2 MB). Export as CSV."


def test_import_rejects_non_csv_files(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = _upload(client, admin_headers, "Email\na@b.co\n", name="list.xlsx")

    assert res.status_code == 400
    assert res.json()["detail"] == "Only CSV files are accepted. In Excel: File → Save As → CSV."


def test_import_rejects_an_empty_sheet(client: TestClient, admin_headers: dict[str, str]) -> None:
    res = _upload(client, admin_headers, "Email\n")

    assert res.status_code == 400
    assert res.json()["detail"] == "Spreadsheet is empty."


def test_import_names_the_columns_when_no_email_column(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = _upload(client, admin_headers, "Name,Phone\nAsha,123\n")

    assert res.status_code == 400
    assert res.json()["detail"] == (
        'Cannot detect email column. Columns found: Name, Phone. Rename your email column to "Email".'
    )


def test_parse_csv_stops_after_the_row_cap() -> None:
    text = "Email\n" + "".join(f"u{i}@x.co\n" for i in range(routes.MAX_ROWS + 50))

    grid = routes.parse_csv(text)

    assert len(grid) == routes.MAX_ROWS + 1


def test_detect_column_normalises_case_and_whitespace() -> None:
    assert (
        routes.detect_column(["  ", "Email   Address"], routes.EMAIL_ALIASES) == "Email   Address"
    )
    assert routes.detect_column(["Phone"], routes.EMAIL_ALIASES) is None
