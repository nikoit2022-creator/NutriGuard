from app.core.owner_scope import UNKNOWN_OWNER_SCOPE, pseudonymous_owner_scope


def test_same_user_id_always_yields_the_same_scope():
    assert pseudonymous_owner_scope("user-1") == pseudonymous_owner_scope("user-1")


def test_different_user_ids_yield_different_scopes():
    assert pseudonymous_owner_scope("user-1") != pseudonymous_owner_scope("user-2")


def test_scope_never_contains_the_raw_user_id():
    scope = pseudonymous_owner_scope("00000000-0000-0000-0000-000000000001")
    assert "00000000-0000-0000-0000-000000000001" not in scope


def test_scope_is_not_reversible_without_the_secret(monkeypatch):
    from app.core import owner_scope as owner_scope_module

    scope_with_secret_a = pseudonymous_owner_scope("user-1")
    monkeypatch.setattr(owner_scope_module.settings, "JWT_SECRET", "a-different-secret")
    scope_with_secret_b = pseudonymous_owner_scope("user-1")
    assert scope_with_secret_a != scope_with_secret_b


def test_unknown_owner_scope_sentinel_is_distinct_from_any_real_scope():
    assert UNKNOWN_OWNER_SCOPE != pseudonymous_owner_scope("user-1")
    assert UNKNOWN_OWNER_SCOPE != pseudonymous_owner_scope("")
