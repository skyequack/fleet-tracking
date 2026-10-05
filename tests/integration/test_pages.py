"""The nine HTML screens (ARCHITECTURE.md 13.1): access rules, navigation by role, and CSP-safe markup."""
import pathlib
import re
import shutil
import subprocess

import pytest

from app.blueprints.pages import PAGES

STATIC = pathlib.Path(__file__).resolve().parents[2] / "app" / "static"
TEMPLATES = pathlib.Path(__file__).resolve().parents[2] / "app" / "templates"

LEADERSHIP_ONLY = {"/dashboard", "/drivers", "/analytics", "/predict"}
EVERYONE = {"/vehicles", "/trips", "/fuel", "/maintenance"}


def nav_links(html):
    return re.findall(r'class="nav-link[^"]*" href="([^"]+)"', html)


def test_there_are_nine_screens_including_login():
    assert len(PAGES) + 1 == 9


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.path)
def test_signed_out_visitors_are_sent_to_login_with_a_way_back(anon, page):
    r = anon.get(page.path)
    assert r.status_code == 302 and r.headers["Location"] == f"/login?next={page.path}"


def test_login_page_is_public_and_a_signed_in_user_is_sent_home(anon, admin, operator):
    assert anon.get("/login").status_code == 200
    assert admin.get("/login").headers["Location"] == "/dashboard"
    assert operator.get("/login").headers["Location"] == "/trips"


def test_root_redirects_by_role(anon, manager, operator):
    assert anon.get("/").headers["Location"] == "/login"
    assert manager.get("/").headers["Location"] == "/dashboard"
    assert operator.get("/").headers["Location"] == "/trips"


@pytest.mark.parametrize("path", sorted(LEADERSHIP_ONLY | EVERYONE))
def test_every_page_loads_for_admin_and_manager(admin, manager, path):
    for api in (admin, manager):
        r = api.get(path)
        assert r.status_code == 200 and b"<title>" in r.data


@pytest.mark.parametrize("path", sorted(EVERYONE))
def test_operators_can_open_the_shared_screens(operator, path):
    assert operator.get(path).status_code == 200


@pytest.mark.parametrize("path", sorted(LEADERSHIP_ONLY))
def test_operators_get_403_on_leadership_screens_even_by_typing_the_url(operator, path):
    r = operator.get(path)
    assert r.status_code == 403 and b"does not have access" in r.data


def test_navigation_hides_what_a_role_cannot_use(admin, manager, operator):
    everything = {"/dashboard", "/vehicles", "/drivers", "/trips", "/fuel", "/maintenance", "/analytics", "/predict"}
    assert set(nav_links(admin.get("/vehicles").text)) == everything
    assert set(nav_links(manager.get("/vehicles").text)) == everything
    assert set(nav_links(operator.get("/vehicles").text)) == {"/vehicles", "/trips", "/fuel", "/maintenance"}


def test_admin_only_controls_are_not_rendered_for_other_roles(admin, manager, operator):
    assert b'id="add"' in admin.get("/vehicles").data
    assert b'id="add"' not in manager.get("/vehicles").data and b'id="add"' not in operator.get("/vehicles").data
    assert b"vehicle-modal" not in manager.get("/vehicles").data


def test_fuel_form_and_charts_follow_the_matrix(admin, manager, operator):
    for api, form, charts in ((admin, True, True), (manager, False, True), (operator, True, False)):
        html = api.get("/fuel").text
        assert ('id="fuel-form"' in html) is form and ('id="charts"' in html) is charts


def test_maintenance_entry_forms_are_hidden_from_managers(admin, manager, operator):
    assert 'id="record-form"' in admin.get("/maintenance").text
    assert 'id="record-form"' not in manager.get("/maintenance").text
    assert "Open services" in operator.get("/maintenance").text and "Maintenance history" in manager.get("/maintenance").text


def test_pages_carry_the_csrf_token_and_are_not_cached(admin):
    r = admin.get("/vehicles")
    token = re.search(r'name="csrf-token" content="([^"]+)"', r.text).group(1)
    assert token == admin.csrf == admin.get("/api/auth/me").json["csrf_token"]
    assert r.headers["Cache-Control"] == "no-store"


def test_login_page_has_no_session_token_and_forms_never_get_submit(anon):
    html = anon.get("/login").text
    assert 'content=""' in html                                  # no token before sign-in
    assert 'method="post"' in html                               # a script failure must not put the password in the URL


def test_user_text_in_pages_is_escaped(admin, set_user):
    set_user("Administrator", name="<script>alert(1)</script>")
    html = admin.get("/vehicles").text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_static_assets_are_served_locally_and_sources_are_not_cdn_links(anon):
    for path in ("vendor/bootstrap.min.css", "vendor/bootstrap.bundle.min.js", "vendor/plotly.min.js", "css/app.css",
                 "js/common.js"):
        assert anon.get(f"/static/{path}").status_code == 200, path
    for template in TEMPLATES.glob("*.html"):
        assert not re.search(r'(src|href)="https?://', template.read_text(encoding="utf-8")), template.name


def test_pages_are_csp_safe_no_inline_scripts_or_handlers():
    """The CSP allows scripts from this origin only, so inline <script> bodies and on*= attributes cannot work."""
    for template in TEMPLATES.glob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text), f"inline script in {template.name}"
        assert not re.search(r"\son[a-z]+=\"", text), f"inline event handler in {template.name}"


def test_no_script_assigns_inner_html():
    """User text must reach the DOM through textContent only (ARCHITECTURE.md 11, XSS)."""
    for script in (STATIC / "js").glob("*.js"):
        code = re.sub(r"^\s*//.*$", "", script.read_text(encoding="utf-8"), flags=re.M)   # comments may mention it
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", code), script.name


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_every_script_parses():
    """A syntax error silently kills a screen (a form falls back to a native submit), so check them all."""
    for script in sorted((STATIC / "js").glob("*.js")):
        result = subprocess.run(["node", "--check", str(script)], capture_output=True, text=True)
        assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_assignment_listing_names_the_vehicle_and_driver(manager, seed):
    from app import models
    vid, did = seed(models.Vehicle), seed(models.Driver)
    seed(models.VehicleAssignment, vehicle_id=vid, driver_id=did)
    item = manager.get("/api/assignments").json["items"][0]
    assert item["registration_no"] == "ABC 1234" and item["driver_name"] == "Test Driver"
