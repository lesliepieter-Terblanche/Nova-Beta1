"""v2.8: Google stays signed in (clear reconnect messages), Google Slides + formatted Sheets, Canva one-click."""
import json

import pytest

from nova import context


class Call:
    def __init__(self, result=None, log=None, name=""):
        self.result, self.log, self.name = result, log, name

    def execute(self):
        return self.result


class FakeSlides:
    def __init__(self):
        self.log = []

    def presentations(self):
        return self

    def create(self, body):
        self.log.append(("create", body))
        return Call({"presentationId": "P1", "slides": [{"objectId": "default"}]})

    def batchUpdate(self, presentationId, body):
        self.log.append(("batch", body))
        return Call({})


class FakeDrive:
    def files(self):
        return self

    def export(self, fileId, mimeType):
        return Call(b"PK-fake-pptx")


class FakeSheets:
    def __init__(self):
        self.log = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def create(self, body):
        return Call({"spreadsheetId": "S1", "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/S1",
                     "sheets": [{"properties": {"sheetId": 0}}]})

    def update(self, **kw):
        self.log.append(("values", kw["body"]["values"]))
        return Call({})

    def batchUpdate(self, spreadsheetId, body):
        self.log.append(("batch", body["requests"]))
        return Call({})


@pytest.fixture()
def g(nova, monkeypatch):
    from nova.skills import google_ws
    fakes = {"slides": FakeSlides(), "drive": FakeDrive(), "sheets": FakeSheets()}
    monkeypatch.setattr(google_ws, "svc", lambda name, version: fakes[name])
    return google_ws, fakes


def test_outline_parsing(g):
    gw, _ = g
    assert gw._outline("# Results\n- Revenue R6.2m\n- GP 17%\n\n# Next\nMist refresh") == [
        {"title": "Results", "bullets": ["Revenue R6.2m", "GP 17%"]}, {"title": "Next", "bullets": ["Mist refresh"]}]


def test_slides_create_with_pptx_copy(g, tmp_path, monkeypatch):
    gw, fakes = g
    monkeypatch.setattr(gw, "resolve", lambda p: tmp_path / p)
    out = gw.slides_create("Avaya QBR", "# Q3\n- Revenue R6.2m\n# Next steps\n- Renewals", subtitle="Axiz · Oct 2026")
    assert "3 slides" in out and "PowerPoint copy" in out
    reqs = fakes["slides"].log[1][1]["requests"]
    kinds = [next(iter(r)) for r in reqs]
    assert kinds.count("createSlide") == 3 and kinds[-1] == "deleteObject"
    texts = [r["insertText"]["text"] for r in reqs if "insertText" in r]
    assert texts[:2] == ["Avaya QBR", "Axiz · Oct 2026"] and "Revenue R6.2m" in texts
    assert (tmp_path / "workspace/docs/Avaya QBR.pptx").read_bytes() == b"PK-fake-pptx"


def test_sheets_create_table(g):
    gw, fakes = g
    out = gw.sheets_create_table("Q4 deals", ["Partner", "Vendor", "Value"], "Axiz | Juniper | 1200000\nDimension Data | Avaya | 800000",
                                 currency_columns=["Value"])
    assert "2 rows" in out
    vals = fakes["sheets"].log[0][1]
    assert vals[0] == ["Partner", "Vendor", "Value"] and vals[2] == ["Dimension Data", "Avaya", "800000"]
    reqs = fakes["sheets"].log[1][1]
    assert any("setBasicFilter" in r for r in reqs) and any("numberFormat" in json.dumps(r) for r in reqs)


def test_login_asks_for_reconnect_clearly(nova, tmp_path, monkeypatch):
    from google.oauth2.credentials import Credentials

    from nova.skills import google_ws
    tok = tmp_path / "token.json"
    creds = Credentials(token="x", refresh_token="r", client_id="c", client_secret="s",
                        token_uri="https://oauth2.googleapis.com/token", scopes=google_ws.SCOPES[:-1])
    tok.write_text(creds.to_json())
    nova[0]["google"]["token_file"] = str(tok)
    nova[0]["google"]["credentials_file"] = str(tmp_path / "credentials.json")
    with pytest.raises(google_ws.NeedsReconnect, match="one-time reconnect"):
        google_ws.login(interactive=False)                 # Slides permission is new → reconnect once
    creds = Credentials(token="x", refresh_token="r", client_id="c", client_secret="s",
                        token_uri="https://oauth2.googleapis.com/token", scopes=google_ws.SCOPES)
    data = json.loads(creds.to_json())
    data["expiry"] = "2020-01-01T00:00:00Z"
    tok.write_text(json.dumps(data))
    from google.auth.exceptions import RefreshError

    def boom(self, request):
        raise RefreshError("invalid_grant: Token has been expired or revoked.")
    monkeypatch.setattr(Credentials, "refresh", boom)
    with pytest.raises(google_ws.NeedsReconnect, match="Publish app"):
        google_ws.login(interactive=False)                 # testing-mode 7-day expiry → exact fix explained


def test_canva_catalog_entry():
    from nova import settings
    spec = settings.mcp_catalog_spec("canva")
    assert spec["enabled"] and spec["command"] == "npx" and "https://mcp.canva.com/mcp" in spec["args"]
    assert spec["timeout"] >= 120 and "canva" in spec["keywords"]


def test_slow_servers_connect_last():
    import asyncio

    from nova.mcp_client import MCPManager
    order = []
    m = MCPManager({"mcp_servers": {"canva": {"timeout": 300}, "excel": {}, "youtube": {"timeout": 30}}})

    async def fake(name, spec):
        order.append(name)
    m._connect = fake
    asyncio.run(m._connect_all())
    assert order[-1] == "canva"
    _ = context


def test_finds_client_file_with_hidden_extension_or_in_downloads(tmp_path, monkeypatch):
    from nova.skills import google_ws
    monkeypatch.setattr(google_ws.Path, "home", lambda: tmp_path)
    secret = tmp_path / "Nova" / "secrets" / "credentials.json"
    secret.parent.mkdir(parents=True)
    (secret.parent / "credentials.json.json").write_text('{"installed": {"client_id": "a"}}')
    assert google_ws.find_client_file(secret) == secret and secret.exists()
    secret.unlink()
    (secret.parent / "credentials.json.json").unlink()
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "client_secret_123.apps.googleusercontent.com.json").write_text('{"installed": {"client_id": "b"}}')
    assert google_ws.find_client_file(secret) and '"b"' in secret.read_text()
    secret.unlink()
    (tmp_path / "Downloads" / "client_secret_123.apps.googleusercontent.com.json").write_text('{"web": {"client_id": "c"}}')
    with pytest.raises(RuntimeError, match="Desktop app"):
        google_ws.find_client_file(secret)
    (tmp_path / "Downloads" / "client_secret_123.apps.googleusercontent.com.json").unlink()
    assert google_ws.find_client_file(secret) is None
