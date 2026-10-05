from tests.conftest import PASSWORD, USERS

ADMIN = USERS["Administrator"]


def test_login_returns_user_and_csrf_token_without_hash(anon):
    r = anon.login(ADMIN)
    assert r.status_code == 200
    assert r.json["user"]["role"] == "Administrator" and r.json["csrf_token"]
    assert "password" not in str(r.json)


def test_wrong_password_and_unknown_email_give_the_same_answer(anon):
    a = anon.login(ADMIN, "wrong-password")
    b = anon.login("nobody@test.local", "wrong-password")
    assert a.status_code == b.status_code == 401
    assert a.json == b.json            # the text must not reveal which emails exist


def test_inactive_user_cannot_sign_in(anon, set_user):
    set_user("Operator", status="Inactive")
    r = anon.login(USERS["Operator"])
    assert r.status_code == 401 and r.json == anon.login("nobody@test.local", "x").json


def test_email_is_case_insensitive(anon):
    assert anon.login(ADMIN.upper()).status_code == 200


def test_lockout_after_five_failures_then_429_with_retry_after(anon):
    for _ in range(5):
        assert anon.login(ADMIN, "bad").status_code == 401
    r = anon.login(ADMIN, PASSWORD)    # even the right password is refused while locked
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0


def test_lockout_is_per_email(anon):
    for _ in range(5):
        anon.login(ADMIN, "bad")
    assert anon.login(USERS["Operator"]).status_code == 200


def test_successful_login_resets_the_failure_count(anon):
    for _ in range(4):
        anon.login(ADMIN, "bad")
    assert anon.login(ADMIN).status_code == 200
    for _ in range(4):
        assert anon.login(ADMIN, "bad").status_code == 401   # would be locked if the count had survived


def test_me_needs_a_session(anon):
    assert anon.get("/api/auth/me").status_code == 401


def test_me_returns_the_same_csrf_token(admin):
    r = admin.get("/api/auth/me")
    assert r.status_code == 200 and r.json["csrf_token"] == admin.csrf


def test_logout_ends_the_session(admin):
    assert admin.post("/api/auth/logout").status_code == 200
    assert admin.get("/api/auth/me").status_code == 401


def test_login_starts_a_fresh_session_each_time(world):
    c = world.test_client()
    from tests.conftest import Api
    api = Api(c)
    api.login(ADMIN)
    first = api.csrf
    api.login(ADMIN)
    assert api.csrf != first


def test_state_changing_request_needs_the_csrf_header(admin):
    body = {"registration_no": "AAA 1111", "type": "Van", "make": "A", "model": "B", "year": 2020}
    assert admin.client.post("/api/vehicles", json=body).status_code == 403
    assert admin.client.post("/api/vehicles", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert admin.post("/api/vehicles", json=body).status_code == 201


def test_unauthenticated_write_is_401_not_403(anon):
    assert anon.post("/api/vehicles", json={}).status_code == 401


def test_non_json_body_is_415_and_malformed_json_is_400(admin):
    h = {"X-CSRF-Token": admin.csrf}
    assert admin.client.post("/api/vehicles", data="a=b", headers=h).status_code == 415
    r = admin.client.post("/api/vehicles", data="{not json", headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 400 and "error" in r.json


def test_role_is_re_read_from_the_database_on_every_request(admin, set_user):
    assert admin.get("/api/users").status_code == 200
    set_user("Administrator", role="Operator")           # demoted while signed in
    assert admin.get("/api/users").status_code == 403


def test_deactivated_user_loses_access_at_once(operator, set_user):
    assert operator.get("/api/vehicles").status_code == 200
    set_user("Operator", status="Inactive")
    assert operator.get("/api/vehicles").status_code == 401


def test_security_headers_and_request_id(anon):
    r = anon.get("/api/auth/me")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert len(r.headers["X-Request-ID"]) == 8


def test_errors_use_one_json_shape(anon):
    r = anon.get("/api/nope")
    assert r.status_code == 404 and r.is_json and set(r.json) == {"error"}
    r = anon.client.delete("/api/auth/me")
    assert r.status_code in (401, 405) and r.is_json
