import pytest

from app.core.errors import AppError
from app.core.security import (
    codes_match, create_access_token, decode_access_token, generate_otp, hash_code, hash_password, verify_password,
)


def test_password_hash_roundtrip():
    h = hash_password("correct horse battery")
    assert h != "correct horse battery"
    assert verify_password(h, "correct horse battery")
    assert not verify_password(h, "wrong")


def test_verify_password_unknown_user_is_false():
    assert verify_password(None, "anything") is False
    assert verify_password(None, "not-a-real-password") is False


def test_otp_is_six_digits_and_hash_matches():
    code = generate_otp()
    assert len(code) == 6 and code.isdigit()
    assert codes_match(code, hash_code(code))
    assert not codes_match("000000" if code != "000000" else "111111", hash_code(code))


def test_access_token_roundtrip_and_tamper():
    token = create_access_token("user-1")
    assert decode_access_token(token)["sub"] == "user-1"
    with pytest.raises(AppError):
        decode_access_token(token + "x")
