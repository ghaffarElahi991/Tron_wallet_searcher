from coincurve import PrivateKey

from app.services.patterns import tron_address_from_public_key

CLIENT_PRIVATE_SCALAR = 1
CLIENT_PUBLIC_KEY = (
    PrivateKey.from_int(CLIENT_PRIVATE_SCALAR).public_key.format(compressed=True).hex()
)
ALLOWED_SECOND_CHARACTERS = set("9ABCDEFGHJKLMNPQRSTUVWXYZ")


def matching_candidate() -> tuple[str, str]:
    for final_scalar in range(CLIENT_PRIVATE_SCALAR + 1, 10_000):
        address = tron_address_from_public_key(PrivateKey.from_int(final_scalar).public_key)
        if address[1] in ALLOWED_SECOND_CHARACTERS:
            offset = final_scalar - CLIENT_PRIVATE_SCALAR
            return address, offset.to_bytes(32, "big").hex()
    raise AssertionError("Could not find a deterministic test candidate.")


def valid_job_payload() -> dict[str, str]:
    address, _ = matching_candidate()
    return {
        "pattern": "3x4",
        "prefix": address[:4],
        "suffix": address[-4:],
    }


def deterministic_server_key_share() -> tuple[str, str]:
    return CLIENT_PRIVATE_SCALAR.to_bytes(32, "big").hex(), CLIENT_PUBLIC_KEY
