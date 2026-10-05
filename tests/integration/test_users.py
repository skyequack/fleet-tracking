from tests.conftest import USERS

NEW = {"name": "New Person", "email": "New.Person@Test.Local", "role": "Operator", "password": "long-enough-1"}


def test_only_admin_manages_users(manager, operator):
    for api in (manager, operator):
        assert api.get("/api/users").status_code == 403
        assert api.post("/api/users", json=NEW).status_code == 403


def test_list_never_exposes_hashes(admin):
    r = admin.get("/api/users")
    assert r.json["total"] == 3 and "password" not in str(r.json)


def test_created_user_can_sign_in(admin, anon):
    r = admin.post("/api/users", json=NEW)
    assert r.status_code == 201 and r.json["email"] == "new.person@test.local" and r.json["status"] == "Active"
    assert anon.login("new.person@test.local", NEW["password"]).status_code == 200


def test_duplicate_email_is_409_even_in_another_case(admin):
    admin.post("/api/users", json=NEW)
    r = admin.post("/api/users", json={**NEW, "email": "new.person@test.local"})
    assert r.status_code == 409 and r.json["field"] == "email"


def test_short_password_and_bad_role_are_rejected(admin):
    r = admin.post("/api/users", json={**NEW, "password": "short"})
    assert r.status_code == 422 and r.json["field"] == "password"
    r = admin.post("/api/users", json={**NEW, "role": "Superuser"})
    assert r.status_code == 422 and r.json["field"] == "role"
    assert admin.post("/api/users", json={**NEW, "email": "not-an-email"}).json["field"] == "email"


def test_password_is_required_on_create_but_not_on_update(admin):
    body = {k: v for k, v in NEW.items() if k != "password"}
    assert admin.post("/api/users", json=body).json["field"] == "password"
    uid = admin.post("/api/users", json=NEW).json["user_id"]
    assert admin.put(f"/api/users/{uid}", json={"name": "Renamed"}).json["name"] == "Renamed"


def test_password_change_takes_effect(admin, anon):
    uid = admin.post("/api/users", json=NEW).json["user_id"]
    assert admin.put(f"/api/users/{uid}", json={"password": "another-pass-2"}).status_code == 200
    assert anon.login("new.person@test.local", NEW["password"]).status_code == 401
    assert anon.login("new.person@test.local", "another-pass-2").status_code == 200


def test_deactivating_a_user_blocks_their_next_sign_in(admin, anon):
    uid = admin.post("/api/users", json=NEW).json["user_id"]
    assert admin.put(f"/api/users/{uid}", json={"status": "Inactive"}).status_code == 200
    assert anon.login("new.person@test.local", NEW["password"]).status_code == 401


def test_admin_cannot_demote_or_deactivate_themselves(admin):
    me = admin.get("/api/auth/me").json["user"]["user_id"]
    r = admin.put(f"/api/users/{me}", json={"role": "Operator"})
    assert r.status_code == 422 and r.json["field"] == "role"
    r = admin.put(f"/api/users/{me}", json={"status": "Inactive"})
    assert r.status_code == 422 and r.json["field"] == "status"
    assert admin.put(f"/api/users/{me}", json={"name": "Still Me"}).status_code == 200


def test_update_missing_user_is_404(admin):
    assert admin.put("/api/users/999", json={"name": "X"}).status_code == 404
