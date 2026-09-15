from app.models import PatternType
from app.services.patterns import BASE58_ALPHABET, PATTERN_LENGTHS

FIRST_CUSTOM_CHARACTERS = frozenset("9ABCDEFGHJKLMNPQRSTUVWXYZ")


class PatternInputError(ValueError):
    pass


def validate_custom_prefix(pattern: PatternType, value: str) -> str:
    custom_length, _ = PATTERN_LENGTHS[pattern]
    normalized = value.strip()
    if normalized.startswith("T") and len(normalized) == custom_length + 1:
        raise PatternInputError("Enter only the characters after T; the bot adds T automatically.")
    if len(normalized) != custom_length:
        raise PatternInputError(
            f"Enter exactly {custom_length} prefix characters after the fixed T."
        )
    invalid = [character for character in normalized if character not in BASE58_ALPHABET]
    if invalid:
        raise PatternInputError(
            "Use TRON Base58 characters only. Characters 0, O, I, and lowercase l are excluded."
        )
    if normalized[0] not in FIRST_CUSTOM_CHARACTERS:
        raise PatternInputError("The first character after T must be uppercase or 9.")
    return f"T{normalized}"


def validate_suffix(pattern: PatternType, value: str) -> str:
    _, suffix_length = PATTERN_LENGTHS[pattern]
    normalized = value.strip()
    if len(normalized) != suffix_length:
        raise PatternInputError(f"Enter exactly {suffix_length} suffix characters.")
    if any(character not in BASE58_ALPHABET for character in normalized):
        raise PatternInputError(
            "Use TRON Base58 characters only. Characters 0, O, I, and lowercase l are excluded."
        )
    return normalized
