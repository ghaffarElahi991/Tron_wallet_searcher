#pragma once

#include <cstdint>

namespace tronforge::cuda_chain {

struct Field {
    std::uint64_t limbs[4]{};
};

struct Point {
    Field x{};
    Field y{};
    bool infinity{true};
};

__device__ __forceinline__ std::uint64_t add_with_carry(
    std::uint64_t left, std::uint64_t right, std::uint64_t& carry) {
    const std::uint64_t first = left + right;
    const std::uint64_t first_carry = first < left ? 1ULL : 0ULL;
    const std::uint64_t second = first + carry;
    const std::uint64_t second_carry = second < first ? 1ULL : 0ULL;
    carry = first_carry + second_carry;
    return second;
}

__device__ __forceinline__ std::uint64_t subtract_with_borrow(
    std::uint64_t left, std::uint64_t right, std::uint64_t& borrow) {
    const std::uint64_t first = left - right;
    const std::uint64_t first_borrow = left < right ? 1ULL : 0ULL;
    const std::uint64_t second = first - borrow;
    const std::uint64_t second_borrow = first < borrow ? 1ULL : 0ULL;
    borrow = first_borrow + second_borrow;
    return second;
}

__device__ __forceinline__ std::uint64_t add_raw(Field& output, const Field& left,
                                                  const Field& right) {
    std::uint64_t carry = 0;
#pragma unroll
    for (int index = 0; index < 4; ++index) {
        output.limbs[index] =
            add_with_carry(left.limbs[index], right.limbs[index], carry);
    }
    return carry;
}

__device__ __forceinline__ std::uint64_t subtract_raw(
    Field& output, const Field& left, const Field& right) {
    std::uint64_t borrow = 0;
#pragma unroll
    for (int index = 0; index < 4; ++index) {
        output.limbs[index] =
            subtract_with_borrow(left.limbs[index], right.limbs[index], borrow);
    }
    return borrow;
}

__device__ __forceinline__ Field prime() {
    return Field{{0xfffffffefffffc2fULL, 0xffffffffffffffffULL,
                  0xffffffffffffffffULL, 0xffffffffffffffffULL}};
}

__device__ __forceinline__ bool greater_than_or_equal_prime(const Field& value) {
    const Field modulus = prime();
    for (int index = 3; index >= 0; --index) {
        if (value.limbs[index] != modulus.limbs[index]) {
            return value.limbs[index] > modulus.limbs[index];
        }
    }
    return true;
}

__device__ __forceinline__ bool field_is_zero(const Field& value) {
    return (value.limbs[0] | value.limbs[1] | value.limbs[2] | value.limbs[3]) == 0;
}

__device__ __forceinline__ bool field_equal(const Field& left, const Field& right) {
    return ((left.limbs[0] ^ right.limbs[0]) |
            (left.limbs[1] ^ right.limbs[1]) |
            (left.limbs[2] ^ right.limbs[2]) |
            (left.limbs[3] ^ right.limbs[3])) == 0;
}

__device__ __forceinline__ void multiply_full(std::uint64_t output[8],
                                               const Field& left,
                                               const Field& right) {
    std::uint64_t product[8]{};
#pragma unroll
    for (int left_index = 0; left_index < 4; ++left_index) {
        std::uint64_t carry = 0;
#pragma unroll
        for (int right_index = 0; right_index < 4; ++right_index) {
            const int output_index = left_index + right_index;
            const std::uint64_t low =
                left.limbs[left_index] * right.limbs[right_index];
            const std::uint64_t high =
                __umul64hi(left.limbs[left_index], right.limbs[right_index]);
            const std::uint64_t first = product[output_index] + low;
            const std::uint64_t first_carry =
                first < product[output_index] ? 1ULL : 0ULL;
            const std::uint64_t second = first + carry;
            const std::uint64_t second_carry = second < first ? 1ULL : 0ULL;
            product[output_index] = second;
            carry = high + first_carry + second_carry;
        }
        product[left_index + 4] = carry;
    }
#pragma unroll
    for (int index = 0; index < 8; ++index) {
        output[index] = product[index];
    }
}

__device__ __forceinline__ Field reduce(const std::uint64_t input[8]) {
    constexpr std::uint64_t reduction_constant = 0x1000003d1ULL;
    std::uint64_t accumulator[5] = {
        input[0], input[1], input[2], input[3], 0,
    };

    std::uint64_t carry = 0;
    const std::uint64_t low0 = input[4] * reduction_constant;
    const std::uint64_t high0 = __umul64hi(input[4], reduction_constant);
    const std::uint64_t low1 = input[5] * reduction_constant;
    const std::uint64_t high1 = __umul64hi(input[5], reduction_constant);
    const std::uint64_t low2 = input[6] * reduction_constant;
    const std::uint64_t high2 = __umul64hi(input[6], reduction_constant);
    const std::uint64_t low3 = input[7] * reduction_constant;
    const std::uint64_t high3 = __umul64hi(input[7], reduction_constant);

    std::uint64_t sum = accumulator[0] + low0;
    carry = sum < accumulator[0] ? 1ULL : 0ULL;
    accumulator[0] = sum;

    std::uint64_t partial = accumulator[1];
    sum = partial + low1;
    std::uint64_t carry1 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + high0;
    std::uint64_t carry2 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + carry;
    std::uint64_t carry3 = sum < partial ? 1ULL : 0ULL;
    accumulator[1] = sum;
    carry = carry1 + carry2 + carry3;

    partial = accumulator[2];
    sum = partial + low2;
    carry1 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + high1;
    carry2 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + carry;
    carry3 = sum < partial ? 1ULL : 0ULL;
    accumulator[2] = sum;
    carry = carry1 + carry2 + carry3;

    partial = accumulator[3];
    sum = partial + low3;
    carry1 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + high2;
    carry2 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + carry;
    carry3 = sum < partial ? 1ULL : 0ULL;
    accumulator[3] = sum;
    carry = carry1 + carry2 + carry3;
    accumulator[4] = high3 + carry;

    const std::uint64_t folded_low = accumulator[4] * reduction_constant;
    const std::uint64_t folded_high =
        __umul64hi(accumulator[4], reduction_constant);
    partial = accumulator[0];
    sum = partial + folded_low;
    std::uint64_t folded_carry = sum < partial ? 1ULL : 0ULL;
    accumulator[0] = sum;

    partial = accumulator[1];
    sum = partial + folded_high;
    carry1 = sum < partial ? 1ULL : 0ULL;
    partial = sum;
    sum = partial + folded_carry;
    carry2 = sum < partial ? 1ULL : 0ULL;
    accumulator[1] = sum;
    folded_carry = carry1 + carry2;

    for (int index = 2; index < 4; ++index) {
        partial = accumulator[index];
        sum = partial + folded_carry;
        accumulator[index] = sum;
        folded_carry = sum < partial ? 1ULL : 0ULL;
    }

    if (folded_carry != 0) {
        partial = accumulator[0];
        sum = partial + folded_carry * reduction_constant;
        std::uint64_t final_carry = sum < partial ? 1ULL : 0ULL;
        accumulator[0] = sum;
        for (int index = 1; index < 4 && final_carry != 0; ++index) {
            partial = accumulator[index];
            sum = partial + 1ULL;
            accumulator[index] = sum;
            final_carry = sum < partial ? 1ULL : 0ULL;
        }
    }

    Field output{{accumulator[0], accumulator[1], accumulator[2], accumulator[3]}};
    if (greater_than_or_equal_prime(output)) {
        Field reduced{};
        static_cast<void>(subtract_raw(reduced, output, prime()));
        output = reduced;
    }
    return output;
}

__device__ __forceinline__ Field field_add(const Field& left, const Field& right) {
    Field low{};
    const std::uint64_t carry = add_raw(low, left, right);
    const std::uint64_t wide[8] = {
        low.limbs[0], low.limbs[1], low.limbs[2], low.limbs[3], carry, 0, 0, 0,
    };
    return reduce(wide);
}

__device__ __forceinline__ Field field_subtract(const Field& left,
                                                 const Field& right) {
    Field output{};
    if (subtract_raw(output, left, right) != 0) {
        Field adjusted{};
        static_cast<void>(add_raw(adjusted, output, prime()));
        output = adjusted;
    }
    return output;
}

__device__ __forceinline__ Field field_negate(const Field& value) {
    if (field_is_zero(value)) {
        return Field{};
    }
    Field output{};
    static_cast<void>(subtract_raw(output, prime(), value));
    return output;
}

__device__ __forceinline__ Field field_multiply(const Field& left,
                                                 const Field& right) {
    std::uint64_t wide[8]{};
    multiply_full(wide, left, right);
    return reduce(wide);
}

__device__ __forceinline__ Field field_square(const Field& value) {
    return field_multiply(value, value);
}

__device__ __forceinline__ Field field_from_small(std::uint64_t value) {
    return Field{{value, 0, 0, 0}};
}

__device__ __forceinline__ Field field_inverse(const Field& value) {
    constexpr std::uint64_t exponent[4] = {
        0xfffffffefffffc2dULL, 0xffffffffffffffffULL,
        0xffffffffffffffffULL, 0xffffffffffffffffULL,
    };
    Field result = field_from_small(1);
    Field base = value;
#pragma unroll 1
    for (int bit_index = 0; bit_index < 256; ++bit_index) {
        if (((exponent[bit_index >> 6] >> (bit_index & 63)) & 1ULL) != 0) {
            result = field_multiply(result, base);
        }
        base = field_square(base);
    }
    return result;
}

__device__ __forceinline__ Point point_double(const Point& point) {
    if (point.infinity || field_is_zero(point.y)) {
        return Point{};
    }
    const Field numerator =
        field_multiply(field_from_small(3), field_square(point.x));
    const Field denominator = field_multiply(field_from_small(2), point.y);
    const Field slope = field_multiply(numerator, field_inverse(denominator));
    const Field output_x = field_subtract(
        field_square(slope), field_multiply(field_from_small(2), point.x));
    const Field output_y = field_subtract(
        field_multiply(slope, field_subtract(point.x, output_x)), point.y);
    return Point{output_x, output_y, false};
}

__device__ __forceinline__ Point point_add(const Point& left, const Point& right) {
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
        return Point{};
    }
    const Field inverse = field_inverse(field_subtract(right.x, left.x));
    const Field slope =
        field_multiply(field_subtract(right.y, left.y), inverse);
    const Field output_x =
        field_subtract(field_subtract(field_square(slope), left.x), right.x);
    const Field output_y = field_subtract(
        field_multiply(slope, field_subtract(left.x, output_x)), left.y);
    return Point{output_x, output_y, false};
}

template <int Count>
__device__ __forceinline__ void field_inverse_batch(Field (&values)[Count]) {
    Field prefixes[Count]{};
    bool zeroes[Count]{};
    Field product = field_from_small(1);
#pragma unroll
    for (int index = 0; index < Count; ++index) {
        zeroes[index] = field_is_zero(values[index]);
        product = field_multiply(
            product, zeroes[index] ? field_from_small(1) : values[index]);
        prefixes[index] = product;
    }
    Field inverse = field_inverse(product);
#pragma unroll
    for (int index = Count - 1; index >= 0; --index) {
        const Field previous =
            index == 0 ? field_from_small(1) : prefixes[index - 1];
        const Field factor = zeroes[index] ? field_from_small(1) : values[index];
        const Field value_inverse = field_multiply(inverse, previous);
        inverse = field_multiply(inverse, factor);
        values[index] = zeroes[index] ? Field{} : value_inverse;
    }
}

__device__ __forceinline__ Field from_legacy_field(const cuda_secp::Field& value) {
    Field output{};
#pragma unroll
    for (int index = 0; index < 4; ++index) {
        output.limbs[index] =
            static_cast<std::uint64_t>(value.limbs[index * 2]) |
            (static_cast<std::uint64_t>(value.limbs[index * 2 + 1]) << 32U);
    }
    return output;
}

__device__ __forceinline__ Point from_legacy_point(
    const cuda_secp::AffinePoint& value) {
    return Point{from_legacy_field(value.x), from_legacy_field(value.y), value.infinity};
}

__device__ __forceinline__ void field_to_big_endian(const Field& value,
                                                     std::uint8_t output[32]) {
#pragma unroll
    for (int limb = 0; limb < 4; ++limb) {
        const std::uint64_t word = value.limbs[3 - limb];
#pragma unroll
        for (int byte = 0; byte < 8; ++byte) {
            output[limb * 8 + byte] =
                static_cast<std::uint8_t>(word >> (56U - byte * 8U));
        }
    }
}

__device__ __forceinline__ void point_to_public_key(const Point& point,
                                                    std::uint8_t output[64]) {
    field_to_big_endian(point.x, output);
    field_to_big_endian(point.y, output + 32);
}

}  // namespace tronforge::cuda_chain
