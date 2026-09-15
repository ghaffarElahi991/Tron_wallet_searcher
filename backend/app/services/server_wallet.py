from coincurve import PrivateKey

from app.services.patterns import SECP256K1_ORDER, tron_address_from_public_key


def create_server_key_share() -> tuple[str, str]:
    private_key = PrivateKey()
    return (
        private_key.secret.hex(),
        private_key.public_key.format(compressed=True).hex(),
    )


def combine_private_key(base_private_key: str, offset: str) -> str:
    combined = (int(base_private_key, 16) + int(offset, 16)) % SECP256K1_ORDER
    if combined == 0:
        raise ValueError("The combined private key is outside the valid secp256k1 range.")
    return combined.to_bytes(32, "big").hex()


def verify_private_key_address(private_key: str, expected_address: str) -> None:
    derived_address = tron_address_from_public_key(
        PrivateKey(bytes.fromhex(private_key)).public_key
    )
    if derived_address != expected_address:
        raise ValueError("The combined private key does not produce the verified address.")
