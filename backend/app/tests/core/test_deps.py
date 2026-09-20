from fastapi import Request
from fastapi.testclient import TestClient

from app.api.deps import client_ip


def _req(headers: dict[str, str], peer: str = "10.0.0.9") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (peer, 1234),
        "query_string": b"",
    }
    return Request(scope)


def test_client_ip_uses_rightmost_forwarded_for() -> None:
    assert client_ip(_req({"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"})) == "3.3.3.3"


def test_client_ip_falls_back_to_peer() -> None:
    assert client_ip(_req({})) == "10.0.0.9"


def test_missing_bearer_is_401(client: TestClient) -> None:
    # Any route using CurrentUser will do once modules exist; the deps are exercised
    # directly here via a throwaway route registered on the test app.
    from fastapi import APIRouter, Depends

    from app.api.deps import get_current_user
    from app.main import app

    r = APIRouter()

    @r.get("/_t/me")
    def me(user: object = Depends(get_current_user)) -> dict[str, str]:  # noqa: ARG001
        return {"ok": "yes"}

    app.include_router(r)
    assert client.get("/_t/me").status_code == 401
    assert client.get("/_t/me", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_client_geo_prefers_cloudfront_viewer_headers() -> None:
    from app.api.deps import client_geo

    geo = client_geo(
        _req(
            {
                "cloudfront-viewer-city": "Pune",
                "cloudfront-viewer-country": "IN",
                "cloudfront-viewer-country-region": "MH",
                "x-vercel-ip-city": "Delhi",
            }
        )
    )
    assert geo == ("Pune", "IN", "MH")


def test_client_geo_falls_back_to_vercel_headers_and_url_decodes() -> None:
    from app.api.deps import client_geo

    geo = client_geo(_req({"x-vercel-ip-city": "New%20Delhi", "x-vercel-ip-country": "IN"}))
    assert geo.city == "New Delhi"
    assert geo.country == "IN"
    assert geo.region == ""


def test_client_geo_is_empty_without_edge_headers() -> None:
    from app.api.deps import client_geo

    assert client_geo(_req({})) == ("", "", "")


def test_client_ip_prefers_cloudfront_viewer_address() -> None:
    req = _req(
        {
            "cloudfront-viewer-address": "198.51.100.10:46532",
            "x-forwarded-for": "1.1.1.1, 130.176.0.9",
        }
    )
    assert client_ip(req) == "198.51.100.10"


def test_client_ip_handles_ipv6_viewer_address() -> None:
    req = _req({"cloudfront-viewer-address": "2001:db8:85a3::8a2e:370:7334:443"})
    assert client_ip(req) == "2001:db8:85a3::8a2e:370:7334"
