"""Envelope crypto round trips."""

from custom_components.sgcc_power import crypto


def test_sm4_round_trip() -> None:
    key = crypto.generate_sm4_key_text()
    plaintext = '{"consNo":"4302332076092","dayElePq":13.82}'
    ciphertext = crypto.sm4_encrypt_hex(plaintext, key)
    assert crypto.sm4_decrypt_hex(ciphertext, key).decode("utf-8") == plaintext


def test_sm2_key_wrap_round_trip() -> None:
    private = "50C4AF48DF75808050729977AA6BC127DC21A404755C660B94222F9D50FA4A75"
    public = crypto.derive_public_key(private)
    key_text = crypto.generate_sm4_key_text()
    wrapped = crypto.sm2_encrypt_key(key_text, "04" + public)
    assert crypto.sm2_decrypt_key(wrapped, private) == key_text


def test_request_envelope_round_trip() -> None:
    private = "42B0F5A9C3D1E8476A2F5C0B1D3E7F91AABBCCDDEEFF00112233445566778899"
    public = crypto.derive_public_key(private)
    payload = {"serviceCode": "BCP_000026", "data": {"consNo": "12345"}}
    envelope = crypto.build_request_envelope(
        payload, public, timestamp="1730000000000", key_text="a" * 32
    )
    assert crypto.verify_request_envelope(envelope)
    envelope["data"] = envelope["data"][:-4] + "0000"
    assert not crypto.verify_request_envelope(envelope)
