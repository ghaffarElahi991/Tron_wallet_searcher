import hashlib
import hmac
from typing import Any

from coincurve import PublicKey
from Crypto.Hash import keccak

from app.models import PatternType

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {character: index for index, character in enumerate(BASE58_ALPHABET)}
SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
PATTERN_LENGTHS: dict[PatternType, tuple[int, int]] = {
    # The fixed TRON network prefix (T) is not included in these lengths.
    PatternType.THREE_BY_FOUR: (3, 4),
    PatternType.TWO_BY_FIVE: (2, 5),
    PatternType.FOUR_BY_THREE: (4, 3),
    PatternType.TWO_BY_TWO: (2, 2),
}
VERIFIER_VERSION = "cpu-v1"


def character_options(character: str, *, exact: bool = False) -> list[str]:
    if exact or not character.isalpha():
        return [character]
    options = [character]
    swapped = character.swapcase()
    if swapped in BASE58_ALPHABET and swapped not in options:
        options.append(swapped)
    return options


def compile_match_spec(prefix: str, suffix: str) -> dict[str, Any]:
    return {
        "version": 1,
        "prefix": [
            character_options(character, exact=index <= 1) for index, character in enumerate(prefix)
        ],
        "suffix": [character_options(character) for character in suffix],
    }


def address_matches_spec(address: str, spec: dict[str, Any]) -> bool:
    prefix_options = spec.get("prefix", [])
    suffix_options = spec.get("suffix", [])
    if len(address) < len(prefix_options) + len(suffix_options):
        return False
    prefix_matches = all(address[index] in options for index, options in enumerate(prefix_options))
    suffix_start = len(address) - len(suffix_options)
    suffix_matches = all(
        address[suffix_start + index] in options for index, options in enumerate(suffix_options)
    )
    return prefix_matches and suffix_matches


def base58check_encode(payload: bytes) -> str:
    checksum = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    encoded_value = int.from_bytes(payload + checksum, "big")
    encoded = ""
    while encoded_value:
        encoded_value, remainder = divmod(encoded_value, 58)
        encoded = BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(payload + checksum) - len((payload + checksum).lstrip(b"\x00"))
    return "1" * leading_zeroes + encoded


def base58check_decode(value: str) -> bytes:
    encoded_value = 0
    for character in value:
        if character not in BASE58_INDEX:
            raise ValueError("Address contains an invalid Base58 character.")
        encoded_value = encoded_value * 58 + BASE58_INDEX[character]
    decoded = encoded_value.to_bytes((encoded_value.bit_length() + 7) // 8, "big")
    leading_ones = len(value) - len(value.lstrip("1"))
    decoded = b"\x00" * leading_ones + decoded
    if len(decoded) < 5:
        raise ValueError("Address is too short.")
    payload, checksum = decoded[:-4], decoded[-4:]
    expected = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    if not hmac.compare_digest(checksum, expected):
        raise ValueError("Address checksum is invalid.")
    return payload


def validate_tron_address(address: str) -> bool:
    if len(address) != 34 or not address.startswith("T"):
        return False
    try:
        payload = base58check_decode(address)
    except ValueError:
        return False
    return len(payload) == 21 and payload[0] == 0x41


def tron_address_from_public_key(public_key: PublicKey) -> str:
    uncompressed = public_key.format(compressed=False)
    digest = keccak.new(digest_bits=256, data=uncompressed[1:]).digest()
    payload = b"\x41" + digest[-20:]
    return base58check_encode(payload)


def derive_candidate_address(client_public_key: str, offset: str) -> str:
    public_key = PublicKey(bytes.fromhex(client_public_key))
    final_public_key = public_key.add(bytes.fromhex(offset))
    return tron_address_from_public_key(final_public_key)


def verify_candidate(
    *, client_public_key: str, offset: str, submitted_address: str, match_spec: dict[str, Any]
) -> str:
    if not validate_tron_address(submitted_address):
        raise ValueError("Candidate is not a valid TRON Base58Check address.")
    derived_address = derive_candidate_address(client_public_key, offset)
    if not hmac.compare_digest(derived_address, submitted_address):
        raise ValueError("Candidate does not match the client public point and offset.")
    if not address_matches_spec(derived_address, match_spec):
        raise ValueError("Candidate does not satisfy the requested prefix and suffix.")
    return derived_address
