from scripts.check_secrets import API_TOKEN, SENSITIVE_ASSIGNMENT


def test_secret_scanner_detects_python_api_token_literals() -> None:
    token = "sk_" + ("a" * 40)

    assert API_TOKEN.search(f"client(api_key={token!r})") is not None
    assert API_TOKEN.search("client(api_key='')") is None


def test_secret_scanner_detects_populated_stripe_secret_assignment() -> None:
    assert SENSITIVE_ASSIGNMENT.search("STRIPE_SECRET_KEY=" + "example-secret") is not None
    assert SENSITIVE_ASSIGNMENT.search("STRIPE_SECRET_KEY=\n") is None
