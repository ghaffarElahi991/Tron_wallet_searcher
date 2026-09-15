#include "tronforge/tron.hpp"

#include <openssl/bn.h>
#include <openssl/crypto.h>
#include <openssl/ec.h>
#include <openssl/obj_mac.h>
#include <openssl/sha.h>

#include <array>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <iomanip>
#include <limits>
#include <memory>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

namespace tronforge {
namespace {

constexpr std::string_view base58_alphabet =
    "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
constexpr std::string_view allowed_first_custom = "9ABCDEFGHJKLMNPQRSTUVWXYZ";
constexpr std::array<std::uint64_t, 24> keccak_round_constants = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL,
};
constexpr std::array<unsigned int, 25> keccak_rotation = {
    0,  1,  62, 28, 27, 36, 44, 6,  55, 20, 3,  10, 43,
    25, 39, 41, 45, 15, 21, 8,  18, 2,  61, 56, 14,
};

using Bn = std::unique_ptr<BIGNUM, decltype(&BN_clear_free)>;
using BnContext = std::unique_ptr<BN_CTX, decltype(&BN_CTX_free)>;
using EcGroup = std::unique_ptr<EC_GROUP, decltype(&EC_GROUP_free)>;
using EcPoint = std::unique_ptr<EC_POINT, decltype(&EC_POINT_free)>;

[[nodiscard]] std::uint64_t rotate_left(std::uint64_t value, unsigned int amount) {
    if (amount == 0) {
        return value;
    }
    return (value << amount) | (value >> (64U - amount));
}

[[nodiscard]] std::uint64_t load_little_endian(const std::uint8_t* bytes) {
    std::uint64_t value = 0;
    for (unsigned int index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
    }
    return value;
}

void store_little_endian(std::uint64_t value, std::uint8_t* output) {
    for (unsigned int index = 0; index < 8; ++index) {
        output[index] = static_cast<std::uint8_t>(value >> (index * 8U));
    }
}

void keccak_permute(std::array<std::uint64_t, 25>& state) {
    for (std::uint64_t round_constant : keccak_round_constants) {
        std::array<std::uint64_t, 5> columns{};
        std::array<std::uint64_t, 5> deltas{};
        std::array<std::uint64_t, 25> moved{};
        for (std::size_t x = 0; x < 5; ++x) {
            columns[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^
                         state[x + 20];
        }
        for (std::size_t x = 0; x < 5; ++x) {
            deltas[x] = columns[(x + 4) % 5] ^ rotate_left(columns[(x + 1) % 5], 1);
        }
        for (std::size_t y = 0; y < 5; ++y) {
            for (std::size_t x = 0; x < 5; ++x) {
                state[x + 5 * y] ^= deltas[x];
            }
        }
        for (std::size_t y = 0; y < 5; ++y) {
            for (std::size_t x = 0; x < 5; ++x) {
                const std::size_t moved_x = y;
                const std::size_t moved_y = (2 * x + 3 * y) % 5;
                moved[moved_x + 5 * moved_y] =
                    rotate_left(state[x + 5 * y], keccak_rotation[x + 5 * y]);
            }
        }
        for (std::size_t y = 0; y < 5; ++y) {
            for (std::size_t x = 0; x < 5; ++x) {
                state[x + 5 * y] =
                    moved[x + 5 * y] ^
                    ((~moved[(x + 1) % 5 + 5 * y]) & moved[(x + 2) % 5 + 5 * y]);
            }
        }
        state[0] ^= round_constant;
    }
}

[[nodiscard]] std::array<std::uint8_t, 32> keccak_256(const std::uint8_t* data,
                                                       std::size_t size) {
    constexpr std::size_t rate = 136;
    std::array<std::uint64_t, 25> state{};
    while (size >= rate) {
        for (std::size_t lane = 0; lane < rate / 8; ++lane) {
            state[lane] ^= load_little_endian(data + lane * 8);
        }
        keccak_permute(state);
        data += rate;
        size -= rate;
    }

    std::array<std::uint8_t, rate> block{};
    for (std::size_t index = 0; index < size; ++index) {
        block[index] = data[index];
    }
    block[size] ^= 0x01;  // Original Keccak domain separator, not SHA3's 0x06.
    block[rate - 1] ^= 0x80;
    for (std::size_t lane = 0; lane < rate / 8; ++lane) {
        state[lane] ^= load_little_endian(block.data() + lane * 8);
    }
    keccak_permute(state);

    std::array<std::uint8_t, 32> digest{};
    for (std::size_t lane = 0; lane < digest.size() / 8; ++lane) {
        store_little_endian(state[lane], digest.data() + lane * 8);
    }
    return digest;
}

[[nodiscard]] std::vector<std::uint8_t> hex_to_bytes(std::string_view value) {
    if (value.size() % 2 != 0) {
        throw std::invalid_argument("Hexadecimal input must contain an even number of characters.");
    }
    auto nibble = [](char character) -> std::uint8_t {
        if (character >= '0' && character <= '9') {
            return static_cast<std::uint8_t>(character - '0');
        }
        if (character >= 'a' && character <= 'f') {
            return static_cast<std::uint8_t>(character - 'a' + 10);
        }
        if (character >= 'A' && character <= 'F') {
            return static_cast<std::uint8_t>(character - 'A' + 10);
        }
        throw std::invalid_argument("Input contains a non-hexadecimal character.");
    };

    std::vector<std::uint8_t> bytes(value.size() / 2);
    for (std::size_t index = 0; index < bytes.size(); ++index) {
        bytes[index] = static_cast<std::uint8_t>((nibble(value[index * 2]) << 4U) |
                                                 nibble(value[index * 2 + 1]));
    }
    return bytes;
}

[[nodiscard]] std::string bytes_to_hex(const std::uint8_t* bytes, std::size_t size) {
    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (std::size_t index = 0; index < size; ++index) {
        output << std::setw(2) << static_cast<unsigned int>(bytes[index]);
    }
    return output.str();
}

[[nodiscard]] std::string base58_encode(const std::vector<std::uint8_t>& input) {
    std::size_t leading_zeroes = 0;
    while (leading_zeroes < input.size() && input[leading_zeroes] == 0) {
        ++leading_zeroes;
    }
    std::vector<std::uint8_t> digits((input.size() - leading_zeroes) * 138 / 100 + 1);
    std::size_t length = 0;
    for (auto input_iterator = input.begin() + static_cast<std::ptrdiff_t>(leading_zeroes);
         input_iterator != input.end(); ++input_iterator) {
        unsigned int carry = *input_iterator;
        std::size_t processed = 0;
        for (auto digit = digits.rbegin();
             (carry != 0 || processed < length) && digit != digits.rend();
             ++digit, ++processed) {
            carry += 256U * *digit;
            *digit = static_cast<std::uint8_t>(carry % 58U);
            carry /= 58U;
        }
        if (carry != 0) {
            throw std::runtime_error("Base58 conversion overflowed its working buffer.");
        }
        length = processed;
    }
    auto digit = digits.begin() + static_cast<std::ptrdiff_t>(digits.size() - length);
    std::string encoded(leading_zeroes, '1');
    while (digit != digits.end()) {
        encoded.push_back(base58_alphabet[*digit]);
        ++digit;
    }
    return encoded;
}

[[nodiscard]] EcGroup make_group() {
    EcGroup group(EC_GROUP_new_by_curve_name(NID_secp256k1), &EC_GROUP_free);
    if (!group) {
        throw std::runtime_error("OpenSSL could not create the secp256k1 group.");
    }
    return group;
}

[[nodiscard]] BnContext make_context() {
    BnContext context(BN_CTX_new(), &BN_CTX_free);
    if (!context) {
        throw std::runtime_error("OpenSSL could not allocate a BIGNUM context.");
    }
    return context;
}

[[nodiscard]] Bn private_scalar(const std::string& private_key_hex, const EC_GROUP* group,
                                BN_CTX* context) {
    if (private_key_hex.size() != 64) {
        throw std::invalid_argument("Private key must be exactly 64 hexadecimal characters.");
    }
    static_cast<void>(hex_to_bytes(private_key_hex));
    BIGNUM* raw_scalar = nullptr;
    if (BN_hex2bn(&raw_scalar, private_key_hex.c_str()) == 0 || raw_scalar == nullptr) {
        throw std::runtime_error("OpenSSL could not parse the private key.");
    }
    Bn scalar(raw_scalar, &BN_clear_free);
    Bn order(BN_new(), &BN_clear_free);
    if (!order || EC_GROUP_get_order(group, order.get(), context) != 1) {
        throw std::runtime_error("OpenSSL could not read the secp256k1 order.");
    }
    if (BN_is_zero(scalar.get()) || BN_is_negative(scalar.get()) ||
        BN_cmp(scalar.get(), order.get()) >= 0) {
        throw std::invalid_argument("Private key is outside the secp256k1 scalar range.");
    }
    return scalar;
}

[[nodiscard]] Bn parse_range_start(const std::string& value) {
    if (value.empty()) {
        throw std::invalid_argument("Range start cannot be empty.");
    }
    BIGNUM* raw = nullptr;
    bool parse_as_hex = false;
    std::string digits = value;
    if (value.starts_with("0x") || value.starts_with("0X")) {
        parse_as_hex = true;
        digits = value.substr(2);
    } else {
        parse_as_hex = value.size() == 64;
        for (char character : value) {
            if ((character >= 'a' && character <= 'f') ||
                (character >= 'A' && character <= 'F')) {
                parse_as_hex = true;
            }
        }
    }
    const int parsed = parse_as_hex ? BN_hex2bn(&raw, digits.c_str())
                                    : BN_dec2bn(&raw, digits.c_str());
    if (parsed == 0 || raw == nullptr) {
        throw std::invalid_argument("Range start must be a decimal number or hexadecimal scalar.");
    }
    return Bn(raw, &BN_clear_free);
}

[[nodiscard]] std::string point_to_address(const EC_GROUP* group, const EC_POINT* point,
                                           BN_CTX* context) {
    std::array<std::uint8_t, 65> public_key{};
    const std::size_t written = EC_POINT_point2oct(group, point, POINT_CONVERSION_UNCOMPRESSED,
                                                   public_key.data(), public_key.size(), context);
    if (written != public_key.size()) {
        throw std::runtime_error("OpenSSL could not serialize an uncompressed public key.");
    }
    const auto digest = keccak_256(public_key.data() + 1, public_key.size() - 1);
    std::vector<std::uint8_t> payload(25);
    payload[0] = 0x41;
    for (std::size_t index = 0; index < 20; ++index) {
        payload[index + 1] = digest[digest.size() - 20 + index];
    }
    std::array<std::uint8_t, SHA256_DIGEST_LENGTH> first_hash{};
    std::array<std::uint8_t, SHA256_DIGEST_LENGTH> second_hash{};
    SHA256(payload.data(), 21, first_hash.data());
    SHA256(first_hash.data(), first_hash.size(), second_hash.data());
    for (std::size_t index = 0; index < 4; ++index) {
        payload[21 + index] = second_hash[index];
    }
    return base58_encode(payload);
}

[[nodiscard]] bool loose_equal(char left, char right) {
    if (left == right) {
        return true;
    }
    const auto left_unsigned = static_cast<unsigned char>(left);
    const auto right_unsigned = static_cast<unsigned char>(right);
    return std::isalpha(left_unsigned) != 0 && std::isalpha(right_unsigned) != 0 &&
           std::tolower(left_unsigned) == std::tolower(right_unsigned);
}

[[nodiscard]] bool matches_pattern(const std::string& address, const std::string& prefix,
                                   const std::string& suffix) {
    if (address.size() != 34 || address[0] != 'T') {
        return false;
    }
    for (std::size_t index = 0; index < prefix.size(); ++index) {
        const bool matches = index == 0 ? address[index + 1] == prefix[index]
                                        : loose_equal(address[index + 1], prefix[index]);
        if (!matches) {
            return false;
        }
    }
    const std::size_t suffix_start = address.size() - suffix.size();
    for (std::size_t index = 0; index < suffix.size(); ++index) {
        if (!loose_equal(address[suffix_start + index], suffix[index])) {
            return false;
        }
    }
    return true;
}

[[nodiscard]] std::string scalar_to_hex(const BIGNUM* scalar) {
    std::array<std::uint8_t, 32> bytes{};
    if (BN_bn2binpad(scalar, bytes.data(), bytes.size()) !=
        static_cast<int>(bytes.size())) {
        throw std::runtime_error("OpenSSL could not serialize the scalar.");
    }
    return bytes_to_hex(bytes.data(), bytes.size());
}

}  // namespace

void validate_pattern(const std::string& pattern, const std::string& prefix,
                      const std::string& suffix) {
    static const std::unordered_map<std::string, std::pair<std::size_t, std::size_t>> lengths = {
        {"3x4", {3, 4}}, {"2x5", {2, 5}}, {"4x3", {4, 3}}, {"2x2", {2, 2}}};
    const auto configured = lengths.find(pattern);
    if (configured == lengths.end()) {
        throw std::invalid_argument("Pattern must be one of: 3x4, 2x5, 4x3, or 2x2.");
    }
    if (prefix.size() != configured->second.first ||
        suffix.size() != configured->second.second) {
        throw std::invalid_argument("Pattern " + pattern + " requires " +
                                    std::to_string(configured->second.first) +
                                    " characters after T and " +
                                    std::to_string(configured->second.second) +
                                    " suffix characters.");
    }
    for (char character : prefix + suffix) {
        if (base58_alphabet.find(character) == std::string_view::npos) {
            throw std::invalid_argument(
                "Pattern contains an invalid TRON Base58 character (0, O, I, and l are excluded).");
        }
    }
    if (allowed_first_custom.find(prefix[0]) == std::string_view::npos) {
        throw std::invalid_argument(
            "The first character after T must be an uppercase Base58 letter or 9.");
    }
}

std::string derive_tron_address(const std::string& private_key_hex) {
    auto group = make_group();
    auto context = make_context();
    auto scalar = private_scalar(private_key_hex, group.get(), context.get());
    EcPoint point(EC_POINT_new(group.get()), &EC_POINT_free);
    if (!point || EC_POINT_mul(group.get(), point.get(), scalar.get(), nullptr, nullptr,
                               context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not derive the public point.");
    }
    return point_to_address(group.get(), point.get(), context.get());
}

std::string compressed_public_key(const std::string& private_key_hex) {
    auto group = make_group();
    auto context = make_context();
    auto scalar = private_scalar(private_key_hex, group.get(), context.get());
    EcPoint point(EC_POINT_new(group.get()), &EC_POINT_free);
    if (!point || EC_POINT_mul(group.get(), point.get(), scalar.get(), nullptr, nullptr,
                               context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not derive the public point.");
    }
    std::array<std::uint8_t, 33> compressed{};
    const std::size_t written = EC_POINT_point2oct(group.get(), point.get(),
                                                   POINT_CONVERSION_COMPRESSED, compressed.data(),
                                                   compressed.size(), context.get());
    if (written != compressed.size()) {
        throw std::runtime_error("OpenSSL could not serialize a compressed public key.");
    }
    return bytes_to_hex(compressed.data(), compressed.size());
}

std::string random_private_key() {
    auto group = make_group();
    auto context = make_context();
    Bn order(BN_new(), &BN_clear_free);
    Bn scalar(BN_secure_new(), &BN_clear_free);
    if (!order || !scalar || EC_GROUP_get_order(group.get(), order.get(), context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not prepare secure private-key generation.");
    }
    do {
        if (BN_priv_rand_range(scalar.get(), order.get()) != 1) {
            throw std::runtime_error("OpenSSL could not generate a secure private key.");
        }
    } while (BN_is_zero(scalar.get()));
    return scalar_to_hex(scalar.get());
}

std::string add_private_key_offset(const std::string& private_key_hex,
                                   const std::string& offset) {
    auto group = make_group();
    auto context = make_context();
    auto base = private_scalar(private_key_hex, group.get(), context.get());
    auto increment = parse_range_start(offset);
    Bn order(BN_new(), &BN_clear_free);
    Bn result(BN_secure_new(), &BN_clear_free);
    if (!order || !result || EC_GROUP_get_order(group.get(), order.get(), context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not prepare private-key recovery.");
    }
    if (BN_is_zero(increment.get()) || BN_is_negative(increment.get()) ||
        BN_cmp(increment.get(), order.get()) >= 0) {
        throw std::invalid_argument("Offset is outside the secp256k1 scalar range.");
    }
    if (BN_mod_add(result.get(), base.get(), increment.get(), order.get(), context.get()) != 1 ||
        BN_is_zero(result.get())) {
        throw std::runtime_error("Winning offset did not produce a valid private key.");
    }
    return scalar_to_hex(result.get());
}

std::string uncompressed_public_key(const std::string& public_key_hex) {
    const auto public_key = hex_to_bytes(public_key_hex);
    if (public_key.size() != 33 && public_key.size() != 65) {
        throw std::invalid_argument(
            "Public key must be compressed (33 bytes) or uncompressed (65 bytes).");
    }
    auto group = make_group();
    auto context = make_context();
    EcPoint point(EC_POINT_new(group.get()), &EC_POINT_free);
    if (!point ||
        EC_POINT_oct2point(group.get(), point.get(), public_key.data(), public_key.size(),
                           context.get()) != 1 ||
        EC_POINT_is_on_curve(group.get(), point.get(), context.get()) != 1 ||
        EC_POINT_is_at_infinity(group.get(), point.get()) == 1) {
        throw std::invalid_argument("Public key is not a finite secp256k1 point.");
    }
    std::array<std::uint8_t, 65> uncompressed{};
    const std::size_t written = EC_POINT_point2oct(
        group.get(), point.get(), POINT_CONVERSION_UNCOMPRESSED, uncompressed.data(),
        uncompressed.size(), context.get());
    if (written != uncompressed.size()) {
        throw std::runtime_error("OpenSSL could not serialize the public point.");
    }
    return bytes_to_hex(uncompressed.data() + 1, uncompressed.size() - 1);
}

std::string derive_offset_public_key(const std::string& public_key_hex,
                                     const std::string& offset) {
    const auto public_key = hex_to_bytes(public_key_hex);
    if (public_key.size() != 33 && public_key.size() != 65) {
        throw std::invalid_argument(
            "Public key must be compressed (33 bytes) or uncompressed (65 bytes).");
    }
    auto group = make_group();
    auto context = make_context();
    EcPoint base_point(EC_POINT_new(group.get()), &EC_POINT_free);
    EcPoint result_point(EC_POINT_new(group.get()), &EC_POINT_free);
    auto scalar = parse_range_start(offset);
    Bn order(BN_new(), &BN_clear_free);
    Bn one(BN_new(), &BN_clear_free);
    if (!base_point || !result_point || !order || !one || BN_one(one.get()) != 1 ||
        EC_GROUP_get_order(group.get(), order.get(), context.get()) != 1 ||
        EC_POINT_oct2point(group.get(), base_point.get(), public_key.data(), public_key.size(),
                           context.get()) != 1 ||
        EC_POINT_is_on_curve(group.get(), base_point.get(), context.get()) != 1) {
        throw std::invalid_argument("Public key is not a valid secp256k1 point.");
    }
    if (BN_is_zero(scalar.get()) || BN_is_negative(scalar.get()) ||
        BN_cmp(scalar.get(), order.get()) >= 0) {
        throw std::invalid_argument("Offset is outside the secp256k1 scalar range.");
    }
    if (EC_POINT_mul(group.get(), result_point.get(), scalar.get(), base_point.get(), one.get(),
                     context.get()) != 1 ||
        EC_POINT_is_at_infinity(group.get(), result_point.get()) == 1) {
        throw std::runtime_error("OpenSSL could not derive a finite offset public point.");
    }
    std::array<std::uint8_t, 65> uncompressed{};
    const std::size_t written = EC_POINT_point2oct(
        group.get(), result_point.get(), POINT_CONVERSION_UNCOMPRESSED, uncompressed.data(),
        uncompressed.size(), context.get());
    if (written != uncompressed.size()) {
        throw std::runtime_error("OpenSSL could not serialize the offset public point.");
    }
    return bytes_to_hex(uncompressed.data() + 1, uncompressed.size() - 1);
}

std::string add_to_offset(const std::string& offset, std::uint64_t increment) {
    auto group = make_group();
    auto context = make_context();
    auto scalar = parse_range_start(offset);
    Bn order(BN_new(), &BN_clear_free);
    if (!order || EC_GROUP_get_order(group.get(), order.get(), context.get()) != 1 ||
        increment > std::numeric_limits<BN_ULONG>::max() ||
        BN_add_word(scalar.get(), static_cast<BN_ULONG>(increment)) != 1 ||
        BN_is_zero(scalar.get()) || BN_is_negative(scalar.get()) ||
        BN_cmp(scalar.get(), order.get()) >= 0) {
        throw std::invalid_argument("Winning offset is outside the secp256k1 scalar range.");
    }
    return scalar_to_hex(scalar.get());
}

SearchResult search_cpu(const SearchRequest& request) {
    validate_pattern(request.pattern, request.prefix, request.suffix);
    if (request.range_count == 0) {
        throw std::invalid_argument("Range count must be at least 1.");
    }
    const auto public_key = hex_to_bytes(request.public_key_hex);
    if (public_key.size() != 33 && public_key.size() != 65) {
        throw std::invalid_argument("Public key must be compressed (33 bytes) or uncompressed (65 bytes).");
    }

    auto group = make_group();
    auto context = make_context();
    EcPoint base_point(EC_POINT_new(group.get()), &EC_POINT_free);
    EcPoint current_point(EC_POINT_new(group.get()), &EC_POINT_free);
    if (!base_point || !current_point ||
        EC_POINT_oct2point(group.get(), base_point.get(), public_key.data(), public_key.size(),
                           context.get()) != 1 ||
        EC_POINT_is_on_curve(group.get(), base_point.get(), context.get()) != 1) {
        throw std::invalid_argument("Public key is not a valid secp256k1 point.");
    }

    auto start = parse_range_start(request.range_start);
    Bn order(BN_new(), &BN_clear_free);
    Bn last(BN_dup(start.get()), &BN_clear_free);
    Bn one(BN_new(), &BN_clear_free);
    if (!order || !last || !one || BN_one(one.get()) != 1 ||
        EC_GROUP_get_order(group.get(), order.get(), context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not prepare the search range.");
    }
    if (BN_is_zero(start.get()) || BN_is_negative(start.get())) {
        throw std::invalid_argument("Range start must be at least 1.");
    }
    if (request.range_count - 1 > std::numeric_limits<BN_ULONG>::max() ||
        BN_add_word(last.get(), static_cast<BN_ULONG>(request.range_count - 1)) != 1 ||
        BN_cmp(last.get(), order.get()) >= 0) {
        throw std::invalid_argument("Search range crosses the secp256k1 scalar order.");
    }
    if (EC_POINT_mul(group.get(), current_point.get(), start.get(), base_point.get(), one.get(),
                     context.get()) != 1) {
        throw std::runtime_error("OpenSSL could not initialize the search point.");
    }

    const EC_POINT* generator = EC_GROUP_get0_generator(group.get());
    if (generator == nullptr) {
        throw std::runtime_error("OpenSSL could not read the secp256k1 generator.");
    }
    const auto started = std::chrono::steady_clock::now();
    for (std::uint64_t index = 0; index < request.range_count; ++index) {
        if (EC_POINT_is_at_infinity(group.get(), current_point.get()) != 1) {
            const std::string address =
                point_to_address(group.get(), current_point.get(), context.get());
            if (matches_pattern(address, request.prefix, request.suffix)) {
                Bn offset(BN_dup(start.get()), &BN_clear_free);
                if (!offset || index > std::numeric_limits<BN_ULONG>::max() ||
                    BN_add_word(offset.get(), static_cast<BN_ULONG>(index)) != 1) {
                    throw std::runtime_error("OpenSSL could not serialize the winning offset.");
                }
                const auto elapsed = std::chrono::duration<double>(
                    std::chrono::steady_clock::now() - started);
                return SearchResult{true, address, scalar_to_hex(offset.get()), index + 1,
                                    elapsed.count()};
            }
        }
        if (index + 1 < request.range_count &&
            EC_POINT_add(group.get(), current_point.get(), current_point.get(), generator,
                         context.get()) != 1) {
            throw std::runtime_error("OpenSSL failed while advancing the search point.");
        }
    }
    const auto elapsed =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - started);
    return SearchResult{false, "", "", request.range_count, elapsed.count()};
}

void run_self_tests() {
    const std::string scalar_one(63, '0');
    const std::string private_one = scalar_one + "1";
    const std::string private_two = scalar_one + "2";
    constexpr std::string_view expected_public_one =
        "0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798";
    constexpr std::string_view expected_address_one = "TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC";
    constexpr std::string_view expected_address_two = "TDvSsdrNM5eeXNL3czpa6AxLDHZA9nwe9K";
    if (compressed_public_key(private_one) != expected_public_one ||
        derive_tron_address(private_one) != expected_address_one ||
        derive_tron_address(private_two) != expected_address_two ||
        add_private_key_offset(private_one, "1") != private_two) {
        throw std::runtime_error("TRON derivation self-test did not match the Python reference.");
    }
    const SearchResult result = search_cpu(SearchRequest{std::string(expected_public_one), "2x2",
                                                          "Dv", "9K", "1", 1});
    if (!result.found || result.address != expected_address_two ||
        result.offset_hex != private_one) {
        throw std::runtime_error("Public-point offset search self-test failed.");
    }
    std::string random_secret = random_private_key();
    bool random_key_valid = false;
    try {
        random_key_valid = random_secret.size() == 64 &&
                           compressed_public_key(random_secret).size() == 66 &&
                           derive_tron_address(random_secret).size() == 34;
    } catch (...) {
        OPENSSL_cleanse(random_secret.data(), random_secret.size());
        throw;
    }
    OPENSSL_cleanse(random_secret.data(), random_secret.size());
    if (!random_key_valid) {
        throw std::runtime_error("Secure random private-key self-test failed.");
    }
}

}  // namespace tronforge
