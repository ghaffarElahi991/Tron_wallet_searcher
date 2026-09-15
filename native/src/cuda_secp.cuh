#pragma once

#include <cstdint>

namespace tronforge::cuda_secp {

#define TRONFORGE_HD __host__ __device__

struct Field {
    std::uint32_t limbs[8]{};
};

struct AffinePoint {
    Field x{};
    Field y{};
    bool infinity{true};
};

constexpr int batch_radius = 4;

struct SymmetricPointBatch {
    AffinePoint negative[batch_radius]{};
    AffinePoint positive[batch_radius]{};
};

TRONFORGE_HD inline std::uint32_t field_prime_limb(int index) {
    if (index == 0) {
        return 0xfffffc2fU;
    }
    if (index == 1) {
        return 0xfffffffeU;
    }
    return 0xffffffffU;
}

TRONFORGE_HD inline int field_compare(const Field& left, const Field& right) {
    for (int index = 7; index >= 0; --index) {
        if (left.limbs[index] < right.limbs[index]) {
            return -1;
        }
        if (left.limbs[index] > right.limbs[index]) {
            return 1;
        }
    }
    return 0;
}

TRONFORGE_HD inline bool field_is_zero(const Field& value) {
    std::uint32_t combined = 0;
    for (std::uint32_t limb : value.limbs) {
        combined |= limb;
    }
    return combined == 0;
}

TRONFORGE_HD inline bool field_equal(const Field& left, const Field& right) {
    std::uint32_t difference = 0;
    for (int index = 0; index < 8; ++index) {
        difference |= left.limbs[index] ^ right.limbs[index];
    }
    return difference == 0;
}

TRONFORGE_HD inline void field_subtract_raw(const Field& left, const Field& right,
                                             Field& output) {
    std::uint64_t borrow = 0;
    for (int index = 0; index < 8; ++index) {
        const std::uint64_t left_limb = left.limbs[index];
        const std::uint64_t subtrahend = static_cast<std::uint64_t>(right.limbs[index]) + borrow;
        output.limbs[index] = static_cast<std::uint32_t>(left_limb - subtrahend);
        borrow = left_limb < subtrahend ? 1 : 0;
    }
}

TRONFORGE_HD inline void reduce_accumulator(std::uint64_t accumulator[18], Field& output) {
    constexpr std::uint64_t limb_mask = 0xffffffffULL;
    for (int pass = 0; pass < 12; ++pass) {
        for (int index = 0; index < 17; ++index) {
            const std::uint64_t carry = accumulator[index] >> 32U;
            accumulator[index] &= limb_mask;
            accumulator[index + 1] += carry;
        }
        bool has_high_limb = false;
        for (int index = 17; index >= 8; --index) {
            const std::uint64_t value = accumulator[index];
            if (value == 0) {
                continue;
            }
            has_high_limb = true;
            accumulator[index] = 0;
            accumulator[index - 8] += value * 977ULL;
            accumulator[index - 7] += value;
        }
        if (!has_high_limb) {
            break;
        }
    }
    for (int index = 0; index < 8; ++index) {
        output.limbs[index] = static_cast<std::uint32_t>(accumulator[index]);
    }
    Field prime{};
    for (int index = 0; index < 8; ++index) {
        prime.limbs[index] = field_prime_limb(index);
    }
    if (field_compare(output, prime) >= 0) {
        Field reduced{};
        field_subtract_raw(output, prime, reduced);
        output = reduced;
    }
}

TRONFORGE_HD inline Field field_add(const Field& left, const Field& right) {
    std::uint64_t accumulator[18]{};
    for (int index = 0; index < 8; ++index) {
        accumulator[index] =
            static_cast<std::uint64_t>(left.limbs[index]) + right.limbs[index];
    }
    Field output{};
    reduce_accumulator(accumulator, output);
    return output;
}

TRONFORGE_HD inline Field field_negate(const Field& value) {
    if (field_is_zero(value)) {
        return Field{};
    }
    Field prime{};
    for (int index = 0; index < 8; ++index) {
        prime.limbs[index] = field_prime_limb(index);
    }
    Field output{};
    field_subtract_raw(prime, value, output);
    return output;
}

TRONFORGE_HD inline Field field_subtract(const Field& left, const Field& right) {
    return field_add(left, field_negate(right));
}

TRONFORGE_HD inline Field field_multiply(const Field& left, const Field& right) {
    std::uint32_t product[16]{};
    for (int left_index = 0; left_index < 8; ++left_index) {
        std::uint64_t carry = 0;
        for (int right_index = 0; right_index < 8; ++right_index) {
            const int output_index = left_index + right_index;
            const std::uint64_t combined =
                static_cast<std::uint64_t>(left.limbs[left_index]) *
                    static_cast<std::uint64_t>(right.limbs[right_index]) +
                product[output_index] + carry;
            product[output_index] = static_cast<std::uint32_t>(combined);
            carry = combined >> 32U;
        }
        product[left_index + 8] = static_cast<std::uint32_t>(carry);
    }
    std::uint64_t accumulator[18]{};
    for (int index = 0; index < 16; ++index) {
        accumulator[index] = product[index];
    }
    Field output{};
    reduce_accumulator(accumulator, output);
    return output;
}

TRONFORGE_HD inline Field field_square(const Field& value) {
    return field_multiply(value, value);
}

TRONFORGE_HD inline Field field_from_small(std::uint32_t value) {
    Field output{};
    output.limbs[0] = value;
    return output;
}

TRONFORGE_HD inline Field field_inverse(const Field& value) {
    // Fermat inversion: value^(p-2), with p-2 ending in 0xfffffc2d.
    Field result = field_from_small(1);
    for (int bit = 255; bit >= 0; --bit) {
        result = field_square(result);
        const int limb = bit / 32;
        const std::uint32_t exponent_limb =
            limb == 0 ? 0xfffffc2dU : (limb == 1 ? 0xfffffffeU : 0xffffffffU);
        if (((exponent_limb >> (bit % 32)) & 1U) != 0) {
            result = field_multiply(result, value);
        }
    }
    return result;
}

TRONFORGE_HD inline AffinePoint point_double(const AffinePoint& point) {
    if (point.infinity || field_is_zero(point.y)) {
        return AffinePoint{};
    }
    const Field x_squared = field_square(point.x);
    const Field numerator = field_multiply(field_from_small(3), x_squared);
    const Field denominator = field_multiply(field_from_small(2), point.y);
    const Field slope = field_multiply(numerator, field_inverse(denominator));
    const Field x_output =
        field_subtract(field_square(slope), field_multiply(field_from_small(2), point.x));
    const Field y_output =
        field_subtract(field_multiply(slope, field_subtract(point.x, x_output)), point.y);
    return AffinePoint{x_output, y_output, false};
}

TRONFORGE_HD inline AffinePoint point_add(const AffinePoint& left,
                                          const AffinePoint& right) {
    if (left.infinity) {
        return right;
    }
    if (right.infinity) {
        return left;
    }
    if (field_equal(left.x, right.x)) {
        if (field_equal(left.y, right.y)) {
            return point_double(left);
        }
        return AffinePoint{};
    }
    const Field numerator = field_subtract(right.y, left.y);
    const Field denominator = field_subtract(right.x, left.x);
    const Field slope = field_multiply(numerator, field_inverse(denominator));
    const Field x_output =
        field_subtract(field_subtract(field_square(slope), left.x), right.x);
    const Field y_output =
        field_subtract(field_multiply(slope, field_subtract(left.x, x_output)), left.y);
    return AffinePoint{x_output, y_output, false};
}

TRONFORGE_HD inline AffinePoint point_add_with_inverse(const AffinePoint& left,
                                                       const AffinePoint& right,
                                                       const Field& inverse_denominator) {
    const Field slope =
        field_multiply(field_subtract(right.y, left.y), inverse_denominator);
    const Field x_output =
        field_subtract(field_subtract(field_square(slope), left.x), right.x);
    const Field y_output =
        field_subtract(field_multiply(slope, field_subtract(left.x, x_output)), left.y);
    return AffinePoint{x_output, y_output, false};
}

TRONFORGE_HD inline SymmetricPointBatch generate_symmetric_batch(
    const AffinePoint& center, const AffinePoint table[batch_radius]) {
    Field prefixes[batch_radius]{};
    Field product = field_from_small(1);
    for (int index = 0; index < batch_radius; ++index) {
        const Field denominator = field_subtract(table[index].x, center.x);
        product = field_multiply(product,
                                 field_is_zero(denominator) ? field_from_small(1) : denominator);
        prefixes[index] = product;
    }
    Field inverse_product = field_inverse(product);
    SymmetricPointBatch batch{};
    for (int index = batch_radius - 1; index >= 0; --index) {
        const Field denominator = field_subtract(table[index].x, center.x);
        if (field_is_zero(denominator)) {
            batch.positive[index] = point_add(center, table[index]);
            const AffinePoint negative_table{table[index].x, field_negate(table[index].y), false};
            batch.negative[index] = point_add(center, negative_table);
            continue;
        }
        const Field previous_product =
            index == 0 ? field_from_small(1) : prefixes[index - 1];
        const Field denominator_inverse = field_multiply(inverse_product, previous_product);
        inverse_product = field_multiply(inverse_product, denominator);
        batch.positive[index] =
            point_add_with_inverse(center, table[index], denominator_inverse);
        const AffinePoint negative_table{table[index].x, field_negate(table[index].y), false};
        batch.negative[index] =
            point_add_with_inverse(center, negative_table, denominator_inverse);
    }
    return batch;
}

TRONFORGE_HD inline Field field_from_big_endian(const std::uint8_t input[32]) {
    Field output{};
    for (int limb = 0; limb < 8; ++limb) {
        const int offset = 28 - limb * 4;
        output.limbs[limb] = (static_cast<std::uint32_t>(input[offset]) << 24U) |
                             (static_cast<std::uint32_t>(input[offset + 1]) << 16U) |
                             (static_cast<std::uint32_t>(input[offset + 2]) << 8U) |
                             static_cast<std::uint32_t>(input[offset + 3]);
    }
    return output;
}

TRONFORGE_HD inline void field_to_big_endian(const Field& value, std::uint8_t output[32]) {
    for (int limb = 0; limb < 8; ++limb) {
        const int offset = 28 - limb * 4;
        output[offset] = static_cast<std::uint8_t>(value.limbs[limb] >> 24U);
        output[offset + 1] = static_cast<std::uint8_t>(value.limbs[limb] >> 16U);
        output[offset + 2] = static_cast<std::uint8_t>(value.limbs[limb] >> 8U);
        output[offset + 3] = static_cast<std::uint8_t>(value.limbs[limb]);
    }
}

TRONFORGE_HD inline AffinePoint generator_point() {
    constexpr std::uint8_t x[32] = {
        0x79, 0xbe, 0x66, 0x7e, 0xf9, 0xdc, 0xbb, 0xac, 0x55, 0xa0, 0x62,
        0x95, 0xce, 0x87, 0x0b, 0x07, 0x02, 0x9b, 0xfc, 0xdb, 0x2d, 0xce,
        0x28, 0xd9, 0x59, 0xf2, 0x81, 0x5b, 0x16, 0xf8, 0x17, 0x98,
    };
    constexpr std::uint8_t y[32] = {
        0x48, 0x3a, 0xda, 0x77, 0x26, 0xa3, 0xc4, 0x65, 0x5d, 0xa4, 0xfb,
        0xfc, 0x0e, 0x11, 0x08, 0xa8, 0xfd, 0x17, 0xb4, 0x48, 0xa6, 0x85,
        0x54, 0x19, 0x9c, 0x47, 0xd0, 0x8f, 0xfb, 0x10, 0xd4, 0xb8,
    };
    return AffinePoint{field_from_big_endian(x), field_from_big_endian(y), false};
}

TRONFORGE_HD inline AffinePoint scalar_multiply_u64(std::uint64_t scalar) {
    AffinePoint result{};
    AffinePoint addend = generator_point();
    while (scalar != 0) {
        if ((scalar & 1ULL) != 0) {
            result = point_add(result, addend);
        }
        scalar >>= 1U;
        if (scalar != 0) {
            addend = point_double(addend);
        }
    }
    return result;
}

TRONFORGE_HD inline void point_to_public_key(const AffinePoint& point,
                                             std::uint8_t output[64]) {
    field_to_big_endian(point.x, output);
    field_to_big_endian(point.y, output + 32);
}

#undef TRONFORGE_HD

}  // namespace tronforge::cuda_secp
