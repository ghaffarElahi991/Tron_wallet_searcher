#include "tronforge/cuda_runtime.hpp"
#include "tronforge/tron.hpp"
#include "cuda_secp.cuh"
#include "cuda_chain.cuh"

#include <cuda_runtime.h>

#include <array>
#include <chrono>
#include <cstdint>
#include <exception>
#include <iomanip>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace tronforge {
namespace {

struct DevicePattern {
    static constexpr int max_prefix_intervals = 8;
    static constexpr int payload_size = 21;

    char prefix[4]{};
    char suffix[5]{};
    int prefix_length{};
    int suffix_length{};
    int prefix_interval_count{};
    std::uint8_t prefix_min[max_prefix_intervals][payload_size]{};
    std::uint8_t prefix_max[max_prefix_intervals][payload_size]{};
};

__device__ __constant__ std::uint64_t device_keccak_round_constants[24] = {
    0x0000000000000001ULL, 0x0000000000008082ULL, 0x800000000000808aULL,
    0x8000000080008000ULL, 0x000000000000808bULL, 0x0000000080000001ULL,
    0x8000000080008081ULL, 0x8000000000008009ULL, 0x000000000000008aULL,
    0x0000000000000088ULL, 0x0000000080008009ULL, 0x000000008000000aULL,
    0x000000008000808bULL, 0x800000000000008bULL, 0x8000000000008089ULL,
    0x8000000000008003ULL, 0x8000000000008002ULL, 0x8000000000000080ULL,
    0x000000000000800aULL, 0x800000008000000aULL, 0x8000000080008081ULL,
    0x8000000000008080ULL, 0x0000000080000001ULL, 0x8000000080008008ULL,
};

__device__ __constant__ unsigned int device_keccak_rotation[25] = {
    0,  1,  62, 28, 27, 36, 44, 6,  55, 20, 3,  10, 43,
    25, 39, 41, 45, 15, 21, 8,  18, 2,  61, 56, 14,
};

__device__ __constant__ std::uint32_t device_sha256_constants[64] = {
    0x428a2f98U, 0x71374491U, 0xb5c0fbcfU, 0xe9b5dba5U, 0x3956c25bU, 0x59f111f1U,
    0x923f82a4U, 0xab1c5ed5U, 0xd807aa98U, 0x12835b01U, 0x243185beU, 0x550c7dc3U,
    0x72be5d74U, 0x80deb1feU, 0x9bdc06a7U, 0xc19bf174U, 0xe49b69c1U, 0xefbe4786U,
    0x0fc19dc6U, 0x240ca1ccU, 0x2de92c6fU, 0x4a7484aaU, 0x5cb0a9dcU, 0x76f988daU,
    0x983e5152U, 0xa831c66dU, 0xb00327c8U, 0xbf597fc7U, 0xc6e00bf3U, 0xd5a79147U,
    0x06ca6351U, 0x14292967U, 0x27b70a85U, 0x2e1b2138U, 0x4d2c6dfcU, 0x53380d13U,
    0x650a7354U, 0x766a0abbU, 0x81c2c92eU, 0x92722c85U, 0xa2bfe8a1U, 0xa81a664bU,
    0xc24b8b70U, 0xc76c51a3U, 0xd192e819U, 0xd6990624U, 0xf40e3585U, 0x106aa070U,
    0x19a4c116U, 0x1e376c08U, 0x2748774cU, 0x34b0bcb5U, 0x391c0cb3U, 0x4ed8aa4aU,
    0x5b9cca4fU, 0x682e6ff3U, 0x748f82eeU, 0x78a5636fU, 0x84c87814U, 0x8cc70208U,
    0x90befffaU, 0xa4506cebU, 0xbef9a3f7U, 0xc67178f2U,
};

__device__ __forceinline__ std::uint64_t device_rotate_left(std::uint64_t value,
                                                            unsigned int amount) {
    return amount == 0 ? value : (value << amount) | (value >> (64U - amount));
}

__device__ __forceinline__ std::uint32_t device_rotate_right(std::uint32_t value,
                                                             unsigned int amount) {
    return (value >> amount) | (value << (32U - amount));
}

__device__ std::uint64_t device_load_little_endian(const std::uint8_t* bytes) {
    std::uint64_t value = 0;
#pragma unroll
    for (unsigned int index = 0; index < 8; ++index) {
        value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
    }
    return value;
}

__device__ void device_store_little_endian(std::uint64_t value, std::uint8_t* output) {
#pragma unroll
    for (unsigned int index = 0; index < 8; ++index) {
        output[index] = static_cast<std::uint8_t>(value >> (index * 8U));
    }
}

__device__ void device_keccak_permute(std::uint64_t state[25]) {
#pragma unroll
    for (int round = 0; round < 24; ++round) {
        std::uint64_t columns[5]{};
        std::uint64_t deltas[5]{};
        std::uint64_t moved[25]{};
#pragma unroll
        for (int x = 0; x < 5; ++x) {
            columns[x] = state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^
                         state[x + 20];
        }
#pragma unroll
        for (int x = 0; x < 5; ++x) {
            deltas[x] = columns[(x + 4) % 5] ^ device_rotate_left(columns[(x + 1) % 5], 1);
        }
#pragma unroll
        for (int y = 0; y < 5; ++y) {
#pragma unroll
            for (int x = 0; x < 5; ++x) {
                state[x + 5 * y] ^= deltas[x];
            }
        }
#pragma unroll
        for (int y = 0; y < 5; ++y) {
#pragma unroll
            for (int x = 0; x < 5; ++x) {
                moved[y + 5 * ((2 * x + 3 * y) % 5)] =
                    device_rotate_left(state[x + 5 * y], device_keccak_rotation[x + 5 * y]);
            }
        }
#pragma unroll
        for (int y = 0; y < 5; ++y) {
#pragma unroll
            for (int x = 0; x < 5; ++x) {
                state[x + 5 * y] =
                    moved[x + 5 * y] ^
                    ((~moved[(x + 1) % 5 + 5 * y]) & moved[(x + 2) % 5 + 5 * y]);
            }
        }
        state[0] ^= device_keccak_round_constants[round];
    }
}

__device__ void device_keccak_256_64(const std::uint8_t input[64],
                                     std::uint8_t digest[32]) {
    std::uint64_t state[25]{};
#pragma unroll
    for (int lane = 0; lane < 8; ++lane) {
        state[lane] = device_load_little_endian(input + lane * 8);
    }
    // The input is always exactly 64 bytes and Keccak-256's rate is 136 bytes.
    // Absorb the domain separator and final bit directly instead of materializing
    // a per-thread 136-byte padded block.
    state[8] = 0x01ULL;
    state[16] = 0x8000000000000000ULL;
    device_keccak_permute(state);
#pragma unroll
    for (int lane = 0; lane < 4; ++lane) {
        device_store_little_endian(state[lane], digest + lane * 8);
    }
}

__device__ void device_sha256_single_block(const std::uint8_t* input, int input_size,
                                           std::uint8_t digest[32]) {
    // Both Tron checksum inputs fit in one SHA-256 block. A 16-word rolling
    // schedule avoids a 64-byte padded block and 64-word schedule per thread.
    std::uint32_t schedule[16]{};
    for (int index = 0; index < input_size; ++index) {
        schedule[index / 4] |=
            static_cast<std::uint32_t>(input[index]) << (24U - (index % 4) * 8U);
    }
    schedule[input_size / 4] |= 0x80U << (24U - (input_size % 4) * 8U);
    const std::uint64_t bit_size = static_cast<std::uint64_t>(input_size) * 8ULL;
    schedule[14] = static_cast<std::uint32_t>(bit_size >> 32U);
    schedule[15] = static_cast<std::uint32_t>(bit_size);

    std::uint32_t a = 0x6a09e667U;
    std::uint32_t b = 0xbb67ae85U;
    std::uint32_t c = 0x3c6ef372U;
    std::uint32_t d = 0xa54ff53aU;
    std::uint32_t e = 0x510e527fU;
    std::uint32_t f = 0x9b05688cU;
    std::uint32_t g = 0x1f83d9abU;
    std::uint32_t h = 0x5be0cd19U;
#pragma unroll
    for (int index = 0; index < 64; ++index) {
        if (index >= 16) {
            const std::uint32_t previous_15 = schedule[(index - 15) & 15];
            const std::uint32_t previous_2 = schedule[(index - 2) & 15];
            const std::uint32_t sigma_zero = device_rotate_right(previous_15, 7) ^
                                             device_rotate_right(previous_15, 18) ^
                                             (previous_15 >> 3U);
            const std::uint32_t sigma_one = device_rotate_right(previous_2, 17) ^
                                            device_rotate_right(previous_2, 19) ^
                                            (previous_2 >> 10U);
            schedule[index & 15] +=
                sigma_zero + schedule[(index - 7) & 15] + sigma_one;
        }
        const std::uint32_t sum_one = device_rotate_right(e, 6) ^ device_rotate_right(e, 11) ^
                                      device_rotate_right(e, 25);
        const std::uint32_t choice = (e & f) ^ ((~e) & g);
        const std::uint32_t temporary_one =
            h + sum_one + choice + device_sha256_constants[index] + schedule[index & 15];
        const std::uint32_t sum_zero = device_rotate_right(a, 2) ^ device_rotate_right(a, 13) ^
                                       device_rotate_right(a, 22);
        const std::uint32_t majority = (a & b) ^ (a & c) ^ (b & c);
        const std::uint32_t temporary_two = sum_zero + majority;
        h = g;
        g = f;
        f = e;
        e = d + temporary_one;
        d = c;
        c = b;
        b = a;
        a = temporary_one + temporary_two;
    }

    const std::uint32_t state[8] = {
        a + 0x6a09e667U, b + 0xbb67ae85U, c + 0x3c6ef372U, d + 0xa54ff53aU,
        e + 0x510e527fU, f + 0x9b05688cU, g + 0x1f83d9abU, h + 0x5be0cd19U,
    };
#pragma unroll
    for (int index = 0; index < 8; ++index) {
        digest[index * 4] = static_cast<std::uint8_t>(state[index] >> 24U);
        digest[index * 4 + 1] = static_cast<std::uint8_t>(state[index] >> 16U);
        digest[index * 4 + 2] = static_cast<std::uint8_t>(state[index] >> 8U);
        digest[index * 4 + 3] = static_cast<std::uint8_t>(state[index]);
    }
}

__device__ void device_base58_25(const std::uint8_t input[25], char output[40]) {
    constexpr char alphabet[] =
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    std::uint8_t digits[40]{};
    int length = 0;
    int leading_zeroes = 0;
    while (leading_zeroes < 25 && input[leading_zeroes] == 0) {
        ++leading_zeroes;
    }
    for (int input_index = leading_zeroes; input_index < 25; ++input_index) {
        unsigned int carry = input[input_index];
        int processed = 0;
        for (int digit = 39; (carry != 0 || processed < length) && digit >= 0;
             --digit, ++processed) {
            carry += 256U * digits[digit];
            digits[digit] = static_cast<std::uint8_t>(carry % 58U);
            carry /= 58U;
        }
        length = processed;
    }
    int output_index = 0;
    for (int index = 0; index < leading_zeroes; ++index) {
        output[output_index++] = '1';
    }
    for (int index = 40 - length; index < 40; ++index) {
        output[output_index++] = alphabet[digits[index]];
    }
    output[output_index] = '\0';
}

__global__ void tron_address_pipeline_kernel(const std::uint8_t* public_key,
                                             char* address_output) {
    if (blockIdx.x != 0 || threadIdx.x != 0) {
        return;
    }
    std::uint8_t keccak_digest[32]{};
    std::uint8_t first_hash[32]{};
    std::uint8_t second_hash[32]{};
    std::uint8_t payload[25]{};
    device_keccak_256_64(public_key, keccak_digest);
    payload[0] = 0x41;
#pragma unroll
    for (int index = 0; index < 20; ++index) {
        payload[index + 1] = keccak_digest[index + 12];
    }
    device_sha256_single_block(payload, 21, first_hash);
    device_sha256_single_block(first_hash, 32, second_hash);
#pragma unroll
    for (int index = 0; index < 4; ++index) {
        payload[index + 21] = second_hash[index];
    }
    device_base58_25(payload, address_output);
}

__device__ void device_tron_payload_from_public_key(const std::uint8_t public_key[64],
                                                    std::uint8_t payload[21]) {
    std::uint8_t keccak_digest[32]{};
    device_keccak_256_64(public_key, keccak_digest);
    payload[0] = 0x41;
#pragma unroll
    for (int index = 0; index < 20; ++index) {
        payload[index + 1] = keccak_digest[index + 12];
    }
}

__device__ void device_tron_address_from_payload(const std::uint8_t payload_input[21],
                                                 char address_output[40]) {
    std::uint8_t first_hash[32]{};
    std::uint8_t second_hash[32]{};
    std::uint8_t payload[25]{};
#pragma unroll
    for (int index = 0; index < 21; ++index) {
        payload[index] = payload_input[index];
    }
    device_sha256_single_block(payload, 21, first_hash);
    device_sha256_single_block(first_hash, 32, second_hash);
#pragma unroll
    for (int index = 0; index < 4; ++index) {
        payload[index + 21] = second_hash[index];
    }
    device_base58_25(payload, address_output);
}

__device__ void device_tron_address_from_public_key(const std::uint8_t public_key[64],
                                                    char address_output[40]) {
    std::uint8_t payload[21]{};
    device_tron_payload_from_public_key(public_key, payload);
    device_tron_address_from_payload(payload, address_output);
}

__global__ void secp256k1_vector_kernel(const std::uint64_t* scalars, char* addresses,
                                        int vector_count) {
    const int index = static_cast<int>(blockIdx.x * blockDim.x + threadIdx.x);
    if (index >= vector_count) {
        return;
    }
    const cuda_secp::AffinePoint point = cuda_secp::scalar_multiply_u64(scalars[index]);
    if (point.infinity) {
        addresses[index * 40] = '\0';
        return;
    }
    std::uint8_t public_key[64]{};
    cuda_secp::point_to_public_key(point, public_key);
    device_tron_address_from_public_key(public_key, addresses + index * 40);
}

__device__ bool device_ascii_letter(char value) {
    return (value >= 'A' && value <= 'Z') || (value >= 'a' && value <= 'z');
}

__device__ char device_ascii_lower(char value) {
    return value >= 'A' && value <= 'Z' ? static_cast<char>(value + ('a' - 'A')) : value;
}

__device__ bool device_pattern_matches(const char address[40], const DevicePattern& pattern) {
    for (int index = 0; index < pattern.prefix_length; ++index) {
        const char actual = address[index + 1];
        const char requested = pattern.prefix[index];
        if (index == 0) {
            if (actual != requested) {
                return false;
            }
        } else if (actual != requested &&
                   (!device_ascii_letter(actual) || !device_ascii_letter(requested) ||
                    device_ascii_lower(actual) != device_ascii_lower(requested))) {
            return false;
        }
    }
    constexpr int address_length = 34;
    const int suffix_start = address_length - pattern.suffix_length;
    for (int index = 0; index < pattern.suffix_length; ++index) {
        const char actual = address[suffix_start + index];
        const char requested = pattern.suffix[index];
        if (actual != requested &&
            (!device_ascii_letter(actual) || !device_ascii_letter(requested) ||
             device_ascii_lower(actual) != device_ascii_lower(requested))) {
            return false;
        }
    }
    return true;
}

__device__ int device_compare_payload(const std::uint8_t left[DevicePattern::payload_size],
                                      const std::uint8_t right[DevicePattern::payload_size]) {
#pragma unroll
    for (int index = 0; index < DevicePattern::payload_size; ++index) {
        if (left[index] < right[index]) {
            return -1;
        }
        if (left[index] > right[index]) {
            return 1;
        }
    }
    return 0;
}

__device__ bool device_payload_may_match_prefix(
    const std::uint8_t payload[DevicePattern::payload_size], const DevicePattern& pattern) {
    for (int interval = 0; interval < pattern.prefix_interval_count; ++interval) {
        if (device_compare_payload(payload, pattern.prefix_min[interval]) >= 0 &&
            device_compare_payload(payload, pattern.prefix_max[interval]) <= 0) {
            return true;
        }
    }
    return false;
}

__global__ void functional_search_kernel(cuda_secp::AffinePoint range_start_point,
                                         DevicePattern pattern, std::uint64_t range_count,
                                         unsigned long long* winning_index) {
    const std::uint64_t index =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= range_count) {
        return;
    }
    const cuda_secp::AffinePoint relative = cuda_secp::scalar_multiply_u64(index);
    const cuda_secp::AffinePoint candidate = cuda_secp::point_add(range_start_point, relative);
    if (candidate.infinity) {
        return;
    }
    std::uint8_t public_key[64]{};
    char address[40]{};
    cuda_secp::point_to_public_key(candidate, public_key);
    device_tron_address_from_public_key(public_key, address);
    if (device_pattern_matches(address, pattern)) {
        atomicMin(winning_index, static_cast<unsigned long long>(index));
    }
}

__global__ void incremental_search_kernel(cuda_secp::AffinePoint range_start_point,
                                          cuda_secp::AffinePoint grid_stride_point,
                                          DevicePattern pattern, std::uint64_t range_count,
                                          unsigned long long* winning_index) {
    const std::uint64_t thread_index =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (thread_index >= range_count) {
        return;
    }
    const std::uint64_t grid_stride =
        static_cast<std::uint64_t>(gridDim.x) * blockDim.x;
    const cuda_secp::AffinePoint relative = cuda_secp::scalar_multiply_u64(thread_index);
    cuda_secp::AffinePoint candidate = cuda_secp::point_add(range_start_point, relative);
    for (std::uint64_t index = thread_index; index < range_count; index += grid_stride) {
        if (!candidate.infinity) {
            std::uint8_t public_key[64]{};
            char address[40]{};
            cuda_secp::point_to_public_key(candidate, public_key);
            device_tron_address_from_public_key(public_key, address);
            if (device_pattern_matches(address, pattern)) {
                atomicMin(winning_index, static_cast<unsigned long long>(index));
            }
        }
        if (index + grid_stride < range_count) {
            candidate = cuda_secp::point_add(candidate, grid_stride_point);
        }
    }
}

__device__ void evaluate_search_candidate(const cuda_secp::AffinePoint& candidate,
                                          std::uint64_t relative_index,
                                          const DevicePattern& pattern,
                                          unsigned long long* winning_index) {
    if (candidate.infinity) {
        return;
    }
    std::uint8_t public_key[64]{};
    std::uint8_t payload[21]{};
    char address[40]{};
    cuda_secp::point_to_public_key(candidate, public_key);
    device_tron_payload_from_public_key(public_key, payload);
    if (!device_payload_may_match_prefix(payload, pattern)) {
        return;
    }
    device_tron_address_from_payload(payload, address);
    if (device_pattern_matches(address, pattern)) {
        atomicMin(winning_index, static_cast<unsigned long long>(relative_index));
    }
}

__device__ void evaluate_chain_candidate(const cuda_chain::Point& candidate,
                                         std::uint64_t relative_index,
                                         const DevicePattern& pattern,
                                         unsigned long long* winning_index) {
    if (candidate.infinity) {
        return;
    }
    std::uint8_t public_key[64]{};
    std::uint8_t payload[21]{};
    char address[40]{};
    cuda_chain::point_to_public_key(candidate, public_key);
    device_tron_payload_from_public_key(public_key, payload);
    if (!device_payload_may_match_prefix(payload, pattern)) {
        return;
    }
    device_tron_address_from_payload(payload, address);
    if (device_pattern_matches(address, pattern)) {
        atomicMin(winning_index, static_cast<unsigned long long>(relative_index));
    }
}

template <int PointsPerThread>
__global__ void chained_initialize_kernel(cuda_secp::AffinePoint range_start_point,
                                          cuda_chain::Point* points,
                                          std::uint64_t chain_count) {
    const std::uint64_t thread_index =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::uint64_t first_chain = thread_index * PointsPerThread;
#pragma unroll
    for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
        const std::uint64_t chain = first_chain + point_index;
        if (chain >= chain_count) {
            continue;
        }
        const cuda_secp::AffinePoint relative = cuda_secp::scalar_multiply_u64(chain);
        const cuda_secp::AffinePoint candidate =
            cuda_secp::point_add(range_start_point, relative);
        points[chain] = cuda_chain::from_legacy_point(candidate);
    }
}

template <int PointsPerThread>
__global__ void chained_search_kernel(cuda_chain::Point* global_points,
                                      cuda_chain::Point stride_point,
                                      DevicePattern pattern,
                                      std::uint64_t range_count,
                                      std::uint64_t chain_count,
                                      std::uint64_t steps,
                                      unsigned long long* winning_index) {
    const std::uint64_t thread_index =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::uint64_t first_chain = thread_index * PointsPerThread;
    if (first_chain >= chain_count) {
        return;
    }

    cuda_chain::Point points[PointsPerThread]{};
#pragma unroll
    for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
        points[point_index] = global_points[first_chain + point_index];
    }

    for (std::uint64_t step = 0; step < steps; ++step) {
#pragma unroll
        for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
            const std::uint64_t chain = first_chain + point_index;
            const std::uint64_t relative_index = step * chain_count + chain;
            if (relative_index < range_count) {
                evaluate_chain_candidate(points[point_index], relative_index, pattern,
                                         winning_index);
            }
        }

        cuda_chain::Field delta_x[PointsPerThread]{};
        cuda_chain::Field delta_y[PointsPerThread]{};
#pragma unroll
        for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
            if (points[point_index].infinity) {
                delta_x[point_index] = cuda_chain::Field{};
                delta_y[point_index] = cuda_chain::Field{};
                continue;
            }
            delta_x[point_index] = cuda_chain::field_subtract(
                stride_point.x, points[point_index].x);
            delta_y[point_index] = cuda_chain::field_subtract(
                stride_point.y, points[point_index].y);
        }
        cuda_chain::field_inverse_batch(delta_x);

#pragma unroll
        for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
            if (points[point_index].infinity) {
                points[point_index] = stride_point;
                continue;
            }
            if (cuda_chain::field_is_zero(delta_x[point_index])) {
                points[point_index] =
                    cuda_chain::point_add(points[point_index], stride_point);
                continue;
            }
            const cuda_chain::Field slope = cuda_chain::field_multiply(
                delta_y[point_index], delta_x[point_index]);
            const cuda_chain::Field output_x = cuda_chain::field_subtract(
                cuda_chain::field_subtract(cuda_chain::field_square(slope),
                                           points[point_index].x),
                stride_point.x);
            const cuda_chain::Field output_y = cuda_chain::field_subtract(
                cuda_chain::field_multiply(
                    slope, cuda_chain::field_subtract(points[point_index].x, output_x)),
                points[point_index].y);
            points[point_index] = cuda_chain::Point{output_x, output_y, false};
        }
    }

#pragma unroll
    for (int point_index = 0; point_index < PointsPerThread; ++point_index) {
        global_points[first_chain + point_index] = points[point_index];
    }
}

__global__ void batched_search_kernel(
    cuda_secp::AffinePoint range_start_point, cuda_secp::AffinePoint lane_stride_point,
    const cuda_secp::AffinePoint* batch_table, DevicePattern pattern,
    std::uint64_t range_count, unsigned long long* winning_index) {
    constexpr std::uint64_t batch_span = cuda_secp::batch_radius * 2ULL + 1ULL;
    const std::uint64_t lane =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    const std::uint64_t lane_count =
        static_cast<std::uint64_t>(gridDim.x) * blockDim.x;
    const std::uint64_t batch_count = (range_count + batch_span - 1) / batch_span;
    if (lane >= batch_count) {
        return;
    }
    std::uint64_t batch_index = lane;
    std::uint64_t center_index = lane * batch_span + cuda_secp::batch_radius;
    const cuda_secp::AffinePoint relative = cuda_secp::scalar_multiply_u64(center_index);
    cuda_secp::AffinePoint center = cuda_secp::point_add(range_start_point, relative);
    while (batch_index < batch_count) {
        cuda_secp::Field prefixes[cuda_secp::batch_radius]{};
        cuda_secp::Field product = cuda_secp::field_from_small(1);
#pragma unroll
        for (int index = 0; index < cuda_secp::batch_radius; ++index) {
            const cuda_secp::Field denominator =
                cuda_secp::field_subtract(batch_table[index].x, center.x);
            product = cuda_secp::field_multiply(
                product, cuda_secp::field_is_zero(denominator)
                             ? cuda_secp::field_from_small(1)
                             : denominator);
            prefixes[index] = product;
        }
        cuda_secp::Field inverse_product = cuda_secp::field_inverse(product);
        if (center_index < range_count) {
            evaluate_search_candidate(center, center_index, pattern, winning_index);
        }
#pragma unroll
        for (int index = cuda_secp::batch_radius - 1; index >= 0; --index) {
            const std::uint64_t distance = static_cast<std::uint64_t>(index + 1);
            const std::uint64_t negative_index = center_index - distance;
            const std::uint64_t positive_index = center_index + distance;
            const cuda_secp::Field denominator =
                cuda_secp::field_subtract(batch_table[index].x, center.x);
            if (cuda_secp::field_is_zero(denominator)) {
                if (negative_index < range_count) {
                    const cuda_secp::AffinePoint negative_table{
                        batch_table[index].x,
                        cuda_secp::field_negate(batch_table[index].y), false};
                    evaluate_search_candidate(cuda_secp::point_add(center, negative_table),
                                              negative_index, pattern, winning_index);
                }
                if (positive_index < range_count) {
                    evaluate_search_candidate(
                        cuda_secp::point_add(center, batch_table[index]), positive_index, pattern,
                        winning_index);
                }
                continue;
            }
            const cuda_secp::Field previous_product =
                index == 0 ? cuda_secp::field_from_small(1) : prefixes[index - 1];
            const cuda_secp::Field denominator_inverse =
                cuda_secp::field_multiply(inverse_product, previous_product);
            inverse_product = cuda_secp::field_multiply(inverse_product, denominator);
            if (negative_index < range_count) {
                const cuda_secp::AffinePoint negative_table{
                    batch_table[index].x,
                    cuda_secp::field_negate(batch_table[index].y), false};
                evaluate_search_candidate(
                    cuda_secp::point_add_with_inverse(center, negative_table,
                                                      denominator_inverse),
                    negative_index, pattern, winning_index);
            }
            if (positive_index < range_count) {
                evaluate_search_candidate(
                    cuda_secp::point_add_with_inverse(center, batch_table[index],
                                                      denominator_inverse),
                    positive_index, pattern, winning_index);
            }
        }
        batch_index += lane_count;
        center_index += lane_count * batch_span;
        if (batch_index < batch_count) {
            center = cuda_secp::point_add(center, lane_stride_point);
        }
    }
}

std::uint8_t host_hex_nibble(char value) {
    if (value >= '0' && value <= '9') {
        return static_cast<std::uint8_t>(value - '0');
    }
    if (value >= 'a' && value <= 'f') {
        return static_cast<std::uint8_t>(value - 'a' + 10);
    }
    if (value >= 'A' && value <= 'F') {
        return static_cast<std::uint8_t>(value - 'A' + 10);
    }
    throw std::invalid_argument("Input contains a non-hexadecimal character.");
}

std::array<std::uint8_t, 64> host_public_key_bytes(const std::string& value) {
    if (value.size() != 128) {
        throw std::runtime_error("Expected a 64-byte uncompressed public point.");
    }
    std::array<std::uint8_t, 64> output{};
    for (std::size_t index = 0; index < output.size(); ++index) {
        output[index] = static_cast<std::uint8_t>((host_hex_nibble(value[index * 2]) << 4U) |
                                                  host_hex_nibble(value[index * 2 + 1]));
    }
    return output;
}

cuda_chain::Field host_chain_field_from_big_endian(const std::uint8_t input[32]) {
    cuda_chain::Field output{};
    for (int limb = 0; limb < 4; ++limb) {
        std::uint64_t value = 0;
        for (int byte = 0; byte < 8; ++byte) {
            value = (value << 8U) | input[limb * 8 + byte];
        }
        output.limbs[3 - limb] = value;
    }
    return output;
}

cuda_chain::Point host_chain_point_from_public_key(std::uint64_t scalar) {
    std::ostringstream private_key;
    private_key << std::hex << std::setfill('0') << std::setw(64) << scalar;
    const auto bytes = host_public_key_bytes(
        uncompressed_public_key(compressed_public_key(private_key.str())));
    return cuda_chain::Point{host_chain_field_from_big_endian(bytes.data()),
                             host_chain_field_from_big_endian(bytes.data() + 32), false};
}

using HostBase58Integer = std::array<std::uint8_t, 25>;

int host_base58_digit(char value) {
    constexpr std::string_view alphabet =
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    const std::size_t index = alphabet.find(value);
    if (index == std::string_view::npos) {
        throw std::invalid_argument("Prefix contains a character outside the Base58 alphabet.");
    }
    return static_cast<int>(index);
}

void host_multiply_small(HostBase58Integer& value, std::uint32_t multiplier) {
    std::uint32_t carry = 0;
    for (int index = static_cast<int>(value.size()) - 1; index >= 0; --index) {
        const std::uint32_t product =
            static_cast<std::uint32_t>(value[index]) * multiplier + carry;
        value[index] = static_cast<std::uint8_t>(product);
        carry = product >> 8U;
    }
    if (carry != 0) {
        throw std::overflow_error("Base58 prefix bound exceeded 25 bytes.");
    }
}

void host_add_small(HostBase58Integer& value, std::uint32_t addend) {
    std::uint32_t carry = addend;
    for (int index = static_cast<int>(value.size()) - 1; index >= 0 && carry != 0; --index) {
        const std::uint32_t sum = static_cast<std::uint32_t>(value[index]) + carry;
        value[index] = static_cast<std::uint8_t>(sum);
        carry = sum >> 8U;
    }
    if (carry != 0) {
        throw std::overflow_error("Base58 prefix bound exceeded 25 bytes.");
    }
}

void host_subtract_one(HostBase58Integer& value) {
    for (int index = static_cast<int>(value.size()) - 1; index >= 0; --index) {
        if (value[index] != 0) {
            --value[index];
            return;
        }
        value[index] = 0xff;
    }
    throw std::underflow_error("Cannot subtract from a zero Base58 bound.");
}

HostBase58Integer host_prefix_bound(std::string_view address_prefix, bool upper) {
    HostBase58Integer value{};
    for (char character : address_prefix) {
        host_multiply_small(value, 58);
        host_add_small(value, static_cast<std::uint32_t>(host_base58_digit(character)));
    }
    if (upper) {
        host_add_small(value, 1);
    }
    constexpr int tron_address_length = 34;
    const int remaining = tron_address_length - static_cast<int>(address_prefix.size());
    for (int index = 0; index < remaining; ++index) {
        host_multiply_small(value, 58);
    }
    if (upper) {
        host_subtract_one(value);
    }
    return value;
}

HostBase58Integer host_decode_base58(std::string_view encoded) {
    HostBase58Integer value{};
    for (char character : encoded) {
        host_multiply_small(value, 58);
        host_add_small(value, static_cast<std::uint32_t>(host_base58_digit(character)));
    }
    return value;
}

bool host_base58_contains(char value) {
    constexpr std::string_view alphabet =
        "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
    return alphabet.find(value) != std::string_view::npos;
}

void populate_prefix_intervals(DevicePattern& pattern, std::string_view requested_prefix) {
    std::array<int, 3> variable_positions{};
    int variable_count = 0;
    for (int index = 1; index < static_cast<int>(requested_prefix.size()); ++index) {
        const char value = requested_prefix[index];
        const char alternative =
            value >= 'A' && value <= 'Z' ? static_cast<char>(value + ('a' - 'A'))
            : value >= 'a' && value <= 'z' ? static_cast<char>(value - ('a' - 'A'))
                                           : value;
        if (alternative != value && host_base58_contains(alternative)) {
            variable_positions[variable_count++] = index;
        }
    }
    pattern.prefix_interval_count = 1 << variable_count;
    for (int variant = 0; variant < pattern.prefix_interval_count; ++variant) {
        std::string actual_prefix = "T" + std::string(requested_prefix);
        for (int bit = 0; bit < variable_count; ++bit) {
            if (((variant >> bit) & 1) == 0) {
                continue;
            }
            char& value = actual_prefix[variable_positions[bit] + 1];
            value = value >= 'A' && value <= 'Z'
                        ? static_cast<char>(value + ('a' - 'A'))
                        : static_cast<char>(value - ('a' - 'A'));
        }
        const HostBase58Integer minimum = host_prefix_bound(actual_prefix, false);
        const HostBase58Integer maximum = host_prefix_bound(actual_prefix, true);
        for (int index = 0; index < DevicePattern::payload_size; ++index) {
            pattern.prefix_min[variant][index] = minimum[index];
            pattern.prefix_max[variant][index] = maximum[index];
        }
    }
}

bool host_payload_may_match_prefix(const HostBase58Integer& encoded,
                                   const DevicePattern& pattern) {
    for (int interval = 0; interval < pattern.prefix_interval_count; ++interval) {
        int minimum_comparison = 0;
        int maximum_comparison = 0;
        for (int index = 0; index < DevicePattern::payload_size; ++index) {
            if (minimum_comparison == 0 && encoded[index] != pattern.prefix_min[interval][index]) {
                minimum_comparison =
                    encoded[index] < pattern.prefix_min[interval][index] ? -1 : 1;
            }
            if (maximum_comparison == 0 && encoded[index] != pattern.prefix_max[interval][index]) {
                maximum_comparison =
                    encoded[index] < pattern.prefix_max[interval][index] ? -1 : 1;
            }
        }
        if (minimum_comparison >= 0 && maximum_comparison <= 0) {
            return true;
        }
    }
    return false;
}

void check_cuda(cudaError_t status, const char* operation) {
    if (status != cudaSuccess) {
        throw std::runtime_error(std::string(operation) + ": " + cudaGetErrorString(status));
    }
}

__global__ void arithmetic_smoke_kernel(std::uint64_t* output, std::uint64_t count) {
    const std::uint64_t index =
        static_cast<std::uint64_t>(blockIdx.x) * blockDim.x + threadIdx.x;
    if (index >= count) {
        return;
    }
    std::uint64_t value = index + 0x9e3779b97f4a7c15ULL;
#pragma unroll 32
    for (int round = 0; round < 256; ++round) {
        value ^= value >> 12;
        value ^= value << 25;
        value ^= value >> 27;
        value *= 0x2545f4914f6cdd1dULL;
    }
    output[index] = value;
}

}  // namespace

bool cuda_compiled() { return true; }

int cuda_device_count() {
    int count = 0;
    check_cuda(cudaGetDeviceCount(&count), "cudaGetDeviceCount");
    return count;
}

void cuda_host_crypto_self_test() {
    constexpr std::array<std::uint64_t, 10> scalar_vectors = {
        1, 2, 3, 7, 31, 255, 256, 1000, 65537, 999983,
    };
    for (std::uint64_t scalar : scalar_vectors) {
        const cuda_secp::AffinePoint point = cuda_secp::scalar_multiply_u64(scalar);
        if (point.infinity) {
            throw std::runtime_error("Host secp256k1 CUDA-reference test returned infinity.");
        }
        std::array<std::uint8_t, 64> public_key{};
        cuda_secp::point_to_public_key(point, public_key.data());
        std::ostringstream actual;
        actual << (public_key[63] % 2 == 0 ? "02" : "03") << std::hex << std::setfill('0');
        for (std::size_t index = 0; index < 32; ++index) {
            actual << std::setw(2) << static_cast<unsigned int>(public_key[index]);
        }
        std::ostringstream private_key;
        private_key << std::hex << std::setfill('0') << std::setw(64) << scalar;
        const std::string expected = compressed_public_key(private_key.str());
        if (actual.str() != expected) {
            throw std::runtime_error("Host secp256k1 CUDA-reference vector failed for scalar " +
                                     std::to_string(scalar) + '.');
        }
    }
    std::array<cuda_secp::AffinePoint, cuda_secp::batch_radius> batch_table{};
    for (int index = 0; index < cuda_secp::batch_radius; ++index) {
        batch_table[index] = cuda_secp::scalar_multiply_u64(index + 1);
    }
    const cuda_secp::AffinePoint center = cuda_secp::scalar_multiply_u64(10);
    const cuda_secp::SymmetricPointBatch batch =
        cuda_secp::generate_symmetric_batch(center, batch_table.data());
    for (int index = 0; index < cuda_secp::batch_radius; ++index) {
        const cuda_secp::AffinePoint expected_negative =
            cuda_secp::scalar_multiply_u64(10 - index - 1);
        const cuda_secp::AffinePoint expected_positive =
            cuda_secp::scalar_multiply_u64(10 + index + 1);
        if (!cuda_secp::field_equal(batch.negative[index].x, expected_negative.x) ||
            !cuda_secp::field_equal(batch.negative[index].y, expected_negative.y) ||
            !cuda_secp::field_equal(batch.positive[index].x, expected_positive.x) ||
            !cuda_secp::field_equal(batch.positive[index].y, expected_positive.y)) {
            throw std::runtime_error("Host secp256k1 batch-inversion reference test failed.");
        }
    }

    const HostBase58Integer scalar_two_address =
        host_decode_base58("TDvSsdrNM5eeXNL3czpa6AxLDHZA9nwe9K");
    DevicePattern exact_prefix{};
    populate_prefix_intervals(exact_prefix, "Dv");
    DevicePattern loose_second_character{};
    populate_prefix_intervals(loose_second_character, "DV");
    DevicePattern wrong_exact_first_character{};
    populate_prefix_intervals(wrong_exact_first_character, "dv");
    if (!host_payload_may_match_prefix(scalar_two_address, exact_prefix) ||
        !host_payload_may_match_prefix(scalar_two_address, loose_second_character) ||
        host_payload_may_match_prefix(scalar_two_address, wrong_exact_first_character)) {
        throw std::runtime_error("Host Base58 prefix-range prefilter test failed.");
    }
}

std::string cuda_device_info_json() {
    int count = 0;
    check_cuda(cudaGetDeviceCount(&count), "cudaGetDeviceCount");
    std::ostringstream output;
    output << R"({"cuda_compiled":true,"devices":[)";
    for (int index = 0; index < count; ++index) {
        cudaDeviceProp properties{};
        check_cuda(cudaGetDeviceProperties(&properties, index), "cudaGetDeviceProperties");
        if (index != 0) {
            output << ',';
        }
        output << R"({"index":)" << index << R"(,"name":")" << properties.name
               << R"(","memory_bytes":)" << properties.totalGlobalMem
               << R"(,"compute_capability":")" << properties.major << '.' << properties.minor
               << R"(","multiprocessors":)" << properties.multiProcessorCount << '}';
    }
    output << "]}";
    return output.str();
}

std::string cuda_self_test_json(int device_index) {
    check_cuda(cudaSetDevice(device_index), "cudaSetDevice");
    constexpr std::uint64_t item_count = 1ULL << 20;
    std::uint64_t* device_output = nullptr;
    std::uint8_t* device_public_key = nullptr;
    char* device_address = nullptr;
    std::uint64_t* device_scalars = nullptr;
    char* device_vector_addresses = nullptr;
    cuda_chain::Point* device_chain_points = nullptr;
    unsigned long long* device_chain_winner = nullptr;
    cudaEvent_t started{};
    cudaEvent_t completed{};
    try {
        check_cuda(cudaMalloc(&device_output, item_count * sizeof(std::uint64_t)),
                   "cudaMalloc(arithmetic output)");
        check_cuda(cudaMalloc(&device_public_key, 64), "cudaMalloc(public key)");
        check_cuda(cudaMalloc(&device_address, 40), "cudaMalloc(address)");
        constexpr std::array<std::uint64_t, 10> scalar_vectors = {
            1, 2, 3, 7, 31, 255, 256, 1000, 65537, 999983,
        };
        check_cuda(cudaMalloc(&device_scalars, scalar_vectors.size() * sizeof(std::uint64_t)),
                   "cudaMalloc(secp256k1 scalars)");
        check_cuda(cudaMalloc(&device_vector_addresses, scalar_vectors.size() * 40),
                   "cudaMalloc(secp256k1 addresses)");
        check_cuda(cudaEventCreate(&started), "cudaEventCreate(started)");
        check_cuda(cudaEventCreate(&completed), "cudaEventCreate(completed)");
        check_cuda(cudaEventRecord(started), "cudaEventRecord(started)");
        constexpr int threads = 256;
        const int blocks = static_cast<int>((item_count + threads - 1) / threads);
        arithmetic_smoke_kernel<<<blocks, threads>>>(device_output, item_count);
        check_cuda(cudaGetLastError(), "arithmetic_smoke_kernel launch");
        check_cuda(cudaEventRecord(completed), "cudaEventRecord(completed)");
        check_cuda(cudaEventSynchronize(completed), "cudaEventSynchronize");

        std::uint64_t first = 0;
        std::uint64_t last = 0;
        check_cuda(cudaMemcpy(&first, device_output, sizeof(first), cudaMemcpyDeviceToHost),
                   "cudaMemcpy(first)");
        check_cuda(cudaMemcpy(&last, device_output + item_count - 1, sizeof(last),
                              cudaMemcpyDeviceToHost),
                   "cudaMemcpy(last)");
        if (first == 0 || last == 0 || first == last) {
            throw std::runtime_error("CUDA arithmetic smoke test returned invalid output.");
        }

        constexpr std::array<std::uint8_t, 64> generator_public_key = {
            0x79, 0xbe, 0x66, 0x7e, 0xf9, 0xdc, 0xbb, 0xac, 0x55, 0xa0, 0x62,
            0x95, 0xce, 0x87, 0x0b, 0x07, 0x02, 0x9b, 0xfc, 0xdb, 0x2d, 0xce,
            0x28, 0xd9, 0x59, 0xf2, 0x81, 0x5b, 0x16, 0xf8, 0x17, 0x98, 0x48,
            0x3a, 0xda, 0x77, 0x26, 0xa3, 0xc4, 0x65, 0x5d, 0xa4, 0xfb, 0xfc,
            0x0e, 0x11, 0x08, 0xa8, 0xfd, 0x17, 0xb4, 0x48, 0xa6, 0x85, 0x54,
            0x19, 0x9c, 0x47, 0xd0, 0x8f, 0xfb, 0x10, 0xd4, 0xb8,
        };
        check_cuda(cudaMemcpy(device_public_key, generator_public_key.data(),
                              generator_public_key.size(), cudaMemcpyHostToDevice),
                   "cudaMemcpy(public key)");
        tron_address_pipeline_kernel<<<1, 1>>>(device_public_key, device_address);
        check_cuda(cudaGetLastError(), "tron_address_pipeline_kernel launch");
        std::array<char, 40> address{};
        check_cuda(cudaMemcpy(address.data(), device_address, address.size(), cudaMemcpyDeviceToHost),
                   "cudaMemcpy(address)");
        constexpr std::string_view expected_address = "TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC";
        if (std::string_view(address.data()) != expected_address) {
            throw std::runtime_error("CUDA Tron address pipeline returned " +
                                     std::string(address.data()) + ", expected " +
                                     std::string(expected_address) + '.');
        }

        check_cuda(cudaMemcpy(device_scalars, scalar_vectors.data(),
                              scalar_vectors.size() * sizeof(std::uint64_t),
                              cudaMemcpyHostToDevice),
                   "cudaMemcpy(secp256k1 scalars)");
        secp256k1_vector_kernel<<<1, 32>>>(device_scalars, device_vector_addresses,
                                          static_cast<int>(scalar_vectors.size()));
        check_cuda(cudaGetLastError(), "secp256k1_vector_kernel launch");
        std::array<char, scalar_vectors.size() * 40> vector_addresses{};
        check_cuda(cudaMemcpy(vector_addresses.data(), device_vector_addresses,
                              vector_addresses.size(), cudaMemcpyDeviceToHost),
                   "cudaMemcpy(secp256k1 addresses)");
        constexpr std::array<std::string_view, 10> expected_vector_addresses = {
            "TMVQGm1qAQYVdetCeGRRkTWYYrLXuHK2HC", "TDvSsdrNM5eeXNL3czpa6AxLDHZA9nwe9K",
            "TKTX96CBxr5kvhjsDHcqoiPWZageGxoTW3", "TVJjphBwXTtDTVJZUEEG9g3CWD7vLcBAS8",
            "TKjB64xXcaJyZwM4HqpHa57q7dYHPB7Nsn", "THHdJjkPUngpM4PdJd8Wq8Rq77CFoKHj1u",
            "TYxhUsYJ7nX2snqPJJ68w2MbFmiPRrUjLs", "TMZL2ZobSG3DYBvfBHBdqCX1LVJNLwvdpX",
            "TSKdHBaNwEPFUQ14t2Yjv81axhYzyRXpHh", "TLG79a2pLdTY3rrLEjPctUzBzPppR39J4y",
        };
        for (std::size_t index = 0; index < expected_vector_addresses.size(); ++index) {
            const std::string_view actual(vector_addresses.data() + index * 40);
            if (actual != expected_vector_addresses[index]) {
                throw std::runtime_error("CUDA secp256k1 vector " + std::to_string(index) +
                                         " returned " + std::string(actual) + ", expected " +
                                         std::string(expected_vector_addresses[index]) + '.');
            }
        }

        check_cuda(cudaMalloc(&device_chain_points, 16 * sizeof(cuda_chain::Point)),
                   "cudaMalloc(chained self-test points)");
        check_cuda(cudaMalloc(&device_chain_winner, sizeof(unsigned long long)),
                   "cudaMalloc(chained self-test winner)");
        const cuda_secp::AffinePoint generator_point{
            cuda_secp::field_from_big_endian(generator_public_key.data()),
            cuda_secp::field_from_big_endian(generator_public_key.data() + 32), false};
        DevicePattern chained_pattern{};
        chained_pattern.prefix_length = 3;
        chained_pattern.suffix_length = 4;
        chained_pattern.prefix[0] = 'Z';
        chained_pattern.prefix[1] = 'Z';
        chained_pattern.prefix[2] = 'Z';
        chained_pattern.suffix[0] = 'Z';
        chained_pattern.suffix[1] = 'Z';
        chained_pattern.suffix[2] = 'Z';
        chained_pattern.suffix[3] = 'Z';
        populate_prefix_intervals(chained_pattern, "ZZZ");
        constexpr unsigned long long no_chain_winner = ~0ULL;

        const auto verify_chained_point = [&](int points_per_thread,
                                              std::uint64_t expected_scalar) {
            const std::uint64_t chain_count =
                static_cast<std::uint64_t>(points_per_thread);
            check_cuda(cudaMemcpy(device_chain_winner, &no_chain_winner,
                                  sizeof(no_chain_winner), cudaMemcpyHostToDevice),
                       "cudaMemcpy(chained self-test winner)");
            if (points_per_thread == 16) {
                chained_initialize_kernel<16><<<1, 1>>>(
                    generator_point, device_chain_points, chain_count);
                chained_search_kernel<16><<<1, 1>>>(
                    device_chain_points, host_chain_point_from_public_key(chain_count),
                    chained_pattern, chain_count * 2, chain_count, 2,
                    device_chain_winner);
            } else {
                chained_initialize_kernel<8><<<1, 1>>>(
                    generator_point, device_chain_points, chain_count);
                chained_search_kernel<8><<<1, 1>>>(
                    device_chain_points, host_chain_point_from_public_key(chain_count),
                    chained_pattern, chain_count * 2, chain_count, 2,
                    device_chain_winner);
            }
            check_cuda(cudaGetLastError(), "chained CUDA self-test launch");
            cuda_chain::Point actual{};
            check_cuda(cudaMemcpy(&actual, device_chain_points, sizeof(actual),
                                  cudaMemcpyDeviceToHost),
                       "cudaMemcpy(chained self-test point)");
            const cuda_chain::Point expected =
                host_chain_point_from_public_key(expected_scalar);
            bool equal = actual.infinity == expected.infinity;
            for (int limb = 0; limb < 4; ++limb) {
                equal = equal && actual.x.limbs[limb] == expected.x.limbs[limb] &&
                        actual.y.limbs[limb] == expected.y.limbs[limb];
            }
            if (!equal) {
                throw std::runtime_error(
                    "Four-limb chained secp256k1 CUDA self-test failed.");
            }
        };
        verify_chained_point(8, 17);
        verify_chained_point(16, 33);

        float elapsed_ms = 0.0F;
        check_cuda(cudaEventElapsedTime(&elapsed_ms, started, completed),
                   "cudaEventElapsedTime");
        std::ostringstream output;
        output << std::fixed << std::setprecision(3)
               << R"({"passed":true,"device":)" << device_index
               << R"(,"items":)" << item_count << R"(,"elapsed_ms":)" << elapsed_ms
               << R"(,"tron_pipeline_passed":true,"tron_address":")" << address.data()
               << R"(","secp256k1_passed":true,"secp256k1_vectors":)"
               << scalar_vectors.size()
               << R"(,"chained_four_limb_passed":true,"chained_variants":[8,16]})";
        cudaEventDestroy(completed);
        cudaEventDestroy(started);
        cudaFree(device_chain_winner);
        cudaFree(device_chain_points);
        cudaFree(device_address);
        cudaFree(device_public_key);
        cudaFree(device_vector_addresses);
        cudaFree(device_scalars);
        cudaFree(device_output);
        return output.str();
    } catch (...) {
        if (completed != nullptr) {
            cudaEventDestroy(completed);
        }
        if (started != nullptr) {
            cudaEventDestroy(started);
        }
        cudaFree(device_chain_winner);
        cudaFree(device_chain_points);
        cudaFree(device_address);
        cudaFree(device_public_key);
        cudaFree(device_vector_addresses);
        cudaFree(device_scalars);
        cudaFree(device_output);
        throw;
    }
}

struct CudaSearchSession::Impl {
    explicit Impl(int requested_device, CudaSearchMode requested_mode)
        : device_index(requested_device), mode(requested_mode) {
        try {
            check_cuda(cudaSetDevice(device_index), "cudaSetDevice(session)");
            cudaDeviceProp properties{};
            check_cuda(cudaGetDeviceProperties(&properties, device_index),
                       "cudaGetDeviceProperties(session)");
            multiprocessors = properties.multiProcessorCount;
            tune_launch_configuration();

            check_cuda(cudaStreamCreateWithFlags(&stream, cudaStreamNonBlocking),
                       "cudaStreamCreateWithFlags(search)");
            check_cuda(cudaEventCreate(&started), "cudaEventCreate(search started)");
            check_cuda(cudaEventCreate(&completed), "cudaEventCreate(search completed)");
            check_cuda(cudaMalloc(&device_winner, sizeof(unsigned long long)),
                       "cudaMalloc(search winner)");
            check_cuda(cudaMallocHost(&host_winner, sizeof(unsigned long long)),
                       "cudaMallocHost(search winner)");

            if (mode == CudaSearchMode::Batched) {
                std::array<cuda_secp::AffinePoint, cuda_secp::batch_radius> host_batch_table{};
                for (int index = 0; index < cuda_secp::batch_radius; ++index) {
                    host_batch_table[index] = cuda_secp::scalar_multiply_u64(index + 1);
                }
                check_cuda(cudaMalloc(&device_batch_table, sizeof(host_batch_table)),
                           "cudaMalloc(batch table)");
                check_cuda(cudaMemcpyAsync(device_batch_table, host_batch_table.data(),
                                           sizeof(host_batch_table), cudaMemcpyHostToDevice,
                                           stream),
                           "cudaMemcpyAsync(batch table)");
                check_cuda(cudaStreamSynchronize(stream),
                           "cudaStreamSynchronize(batch table)");
            }
            if (is_chained()) {
                chain_stride_point = host_chain_point_from_public_key(chains);
                check_cuda(cudaMalloc(&device_chain_points,
                                      chains * sizeof(cuda_chain::Point)),
                           "cudaMalloc(chain points)");
            }
        } catch (...) {
            release();
            throw;
        }
    }

    ~Impl() { release(); }

    Impl(const Impl&) = delete;
    Impl& operator=(const Impl&) = delete;

    void release() noexcept {
        static_cast<void>(cudaSetDevice(device_index));
        if (stream != nullptr) {
            static_cast<void>(cudaStreamSynchronize(stream));
        }
        if (device_batch_table != nullptr) {
            static_cast<void>(cudaFree(device_batch_table));
            device_batch_table = nullptr;
        }
        if (device_chain_points != nullptr) {
            static_cast<void>(cudaFree(device_chain_points));
            device_chain_points = nullptr;
        }
        if (device_winner != nullptr) {
            static_cast<void>(cudaFree(device_winner));
            device_winner = nullptr;
        }
        if (host_winner != nullptr) {
            static_cast<void>(cudaFreeHost(host_winner));
            host_winner = nullptr;
        }
        if (completed != nullptr) {
            static_cast<void>(cudaEventDestroy(completed));
            completed = nullptr;
        }
        if (started != nullptr) {
            static_cast<void>(cudaEventDestroy(started));
            started = nullptr;
        }
        if (stream != nullptr) {
            static_cast<void>(cudaStreamDestroy(stream));
            stream = nullptr;
        }
    }

    void tune_launch_configuration() {
        if (is_chained()) {
            threads = 64;
            points = mode == CudaSearchMode::Chained16 ? 16 : 8;
            resident_blocks = multiprocessors * 4;
            chains = static_cast<std::uint64_t>(resident_blocks) * threads * points;
            return;
        }
        if (mode == CudaSearchMode::Reference) {
            threads = 128;
            resident_blocks = 0;
            return;
        }

        // Occupancy is a safe first-pass tuner. Keeping this selection in the long-lived
        // session also prevents repeated occupancy queries for every scheduler batch.
        constexpr std::array<int, 3> candidates = {128, 256, 64};
        int best_threads = 0;
        int best_active_blocks = 0;
        int best_resident_threads = -1;
        for (const int candidate_threads : candidates) {
            int active_blocks = 0;
            if (mode == CudaSearchMode::Batched) {
                check_cuda(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
                               &active_blocks, batched_search_kernel, candidate_threads, 0),
                           "cudaOccupancyMaxActiveBlocksPerMultiprocessor(batched)");
            } else {
                check_cuda(cudaOccupancyMaxActiveBlocksPerMultiprocessor(
                               &active_blocks, incremental_search_kernel, candidate_threads, 0),
                           "cudaOccupancyMaxActiveBlocksPerMultiprocessor(incremental)");
            }
            const int resident_threads = active_blocks * candidate_threads;
            if (active_blocks > 0 && resident_threads > best_resident_threads) {
                best_threads = candidate_threads;
                best_active_blocks = active_blocks;
                best_resident_threads = resident_threads;
            }
        }
        if (best_threads == 0 || best_active_blocks == 0) {
            throw std::runtime_error("CUDA occupancy calculation returned zero active blocks.");
        }
        threads = best_threads;
        resident_blocks = multiprocessors * best_active_blocks;
    }

    [[nodiscard]] bool is_chained() const noexcept {
        return mode == CudaSearchMode::Chained8 || mode == CudaSearchMode::Chained16;
    }

    [[nodiscard]] cuda_secp::AffinePoint stride_point(std::uint64_t grid_stride) {
        if (has_cached_stride && grid_stride == cached_grid_stride) {
            return cached_stride_point;
        }
        std::ostringstream stride_private_key;
        stride_private_key << std::hex << std::setfill('0') << std::setw(64) << grid_stride;
        const auto stride_public_key = host_public_key_bytes(
            uncompressed_public_key(compressed_public_key(stride_private_key.str())));
        cached_stride_point = cuda_secp::AffinePoint{
            cuda_secp::field_from_big_endian(stride_public_key.data()),
            cuda_secp::field_from_big_endian(stride_public_key.data() + 32), false};
        cached_grid_stride = grid_stride;
        has_cached_stride = true;
        return cached_stride_point;
    }

    [[nodiscard]] SearchResult search(const SearchRequest& request) {
        validate_pattern(request.pattern, request.prefix, request.suffix);
        if (request.range_count == 0) {
            throw std::invalid_argument("Range count must be at least 1.");
        }
        constexpr std::uint64_t validation_limit = 1ULL << 26;
        if (request.range_count > validation_limit) {
            throw std::invalid_argument(
                "The validation CUDA search is limited to 67108864 candidates per launch.");
        }
        if (request.range_count > 1) {
            static_cast<void>(add_to_offset(request.range_start, request.range_count - 1));
        }

        const auto start_public_key = host_public_key_bytes(
            derive_offset_public_key(request.public_key_hex, request.range_start));
        const cuda_secp::AffinePoint start_point{
            cuda_secp::field_from_big_endian(start_public_key.data()),
            cuda_secp::field_from_big_endian(start_public_key.data() + 32), false};
        DevicePattern pattern{};
        pattern.prefix_length = static_cast<int>(request.prefix.size());
        pattern.suffix_length = static_cast<int>(request.suffix.size());
        for (std::size_t index = 0; index < request.prefix.size(); ++index) {
            pattern.prefix[index] = request.prefix[index];
        }
        for (std::size_t index = 0; index < request.suffix.size(); ++index) {
            pattern.suffix[index] = request.suffix[index];
        }
        populate_prefix_intervals(pattern, request.prefix);

        check_cuda(cudaSetDevice(device_index), "cudaSetDevice(search)");
        int blocks = static_cast<int>((request.range_count + threads - 1) / threads);
        const std::string normalized_start = add_to_offset(request.range_start, 0);
        const bool initialize_chains =
            is_chained() &&
            (!chain_state_valid || chain_public_key != request.public_key_hex ||
             chain_next_start != normalized_start);
        const std::uint64_t chain_steps =
            is_chained() ? (request.range_count + chains - 1) / chains : 0;
        if (is_chained()) {
            blocks = resident_blocks;
            // The state is considered reusable again only after this launch and its
            // device-to-host result transfer complete successfully.
            chain_state_valid = false;
        }
        cuda_secp::AffinePoint grid_stride_point{};
        if (mode == CudaSearchMode::Incremental || mode == CudaSearchMode::Batched) {
            if (mode == CudaSearchMode::Batched) {
                constexpr std::uint64_t batch_span = cuda_secp::batch_radius * 2ULL + 1ULL;
                const std::uint64_t batch_count =
                    (request.range_count + batch_span - 1) / batch_span;
                blocks = static_cast<int>((batch_count + threads - 1) / threads);
            }
            if (blocks > resident_blocks) {
                blocks = resident_blocks;
            }
            const std::uint64_t grid_stride =
                static_cast<std::uint64_t>(blocks) * threads *
                (mode == CudaSearchMode::Batched
                     ? static_cast<std::uint64_t>(cuda_secp::batch_radius * 2 + 1)
                     : 1ULL);
            grid_stride_point = stride_point(grid_stride);
        }

        constexpr unsigned long long no_winner = ~0ULL;
        *host_winner = no_winner;
        check_cuda(cudaMemsetAsync(device_winner, 0xff, sizeof(*device_winner), stream),
                   "cudaMemsetAsync(search winner)");
        check_cuda(cudaEventRecord(started, stream), "cudaEventRecord(search started)");
        if (initialize_chains) {
            if (points == 16) {
                chained_initialize_kernel<16><<<blocks, threads, 0, stream>>>(
                    start_point, device_chain_points, chains);
            } else {
                chained_initialize_kernel<8><<<blocks, threads, 0, stream>>>(
                    start_point, device_chain_points, chains);
            }
            check_cuda(cudaGetLastError(), "chained_initialize_kernel launch");
        }
        if (is_chained()) {
            if (points == 16) {
                chained_search_kernel<16><<<blocks, threads, 0, stream>>>(
                    device_chain_points, chain_stride_point, pattern,
                    request.range_count, chains, chain_steps, device_winner);
            } else {
                chained_search_kernel<8><<<blocks, threads, 0, stream>>>(
                    device_chain_points, chain_stride_point, pattern,
                    request.range_count, chains, chain_steps, device_winner);
            }
            check_cuda(cudaGetLastError(), "chained_search_kernel launch");
        } else if (mode == CudaSearchMode::Batched) {
            batched_search_kernel<<<blocks, threads, 0, stream>>>(
                start_point, grid_stride_point, device_batch_table, pattern,
                request.range_count, device_winner);
            check_cuda(cudaGetLastError(), "batched_search_kernel launch");
        } else if (mode == CudaSearchMode::Incremental) {
            incremental_search_kernel<<<blocks, threads, 0, stream>>>(
                start_point, grid_stride_point, pattern, request.range_count, device_winner);
            check_cuda(cudaGetLastError(), "incremental_search_kernel launch");
        } else {
            functional_search_kernel<<<blocks, threads, 0, stream>>>(
                start_point, pattern, request.range_count, device_winner);
            check_cuda(cudaGetLastError(), "functional_search_kernel launch");
        }
        check_cuda(cudaEventRecord(completed, stream), "cudaEventRecord(search completed)");
        check_cuda(cudaMemcpyAsync(host_winner, device_winner, sizeof(*host_winner),
                                   cudaMemcpyDeviceToHost, stream),
                   "cudaMemcpyAsync(search result)");
        check_cuda(cudaStreamSynchronize(stream), "cudaStreamSynchronize(search)");

        if (is_chained() && request.range_count % chains == 0) {
            chain_public_key = request.public_key_hex;
            chain_next_start = add_to_offset(normalized_start, request.range_count);
            chain_state_valid = true;
        }

        float elapsed_ms = 0.0F;
        check_cuda(cudaEventElapsedTime(&elapsed_ms, started, completed),
                   "cudaEventElapsedTime(search)");
        const unsigned long long winning_index = *host_winner;
        const double elapsed_seconds = static_cast<double>(elapsed_ms) / 1000.0;
        if (winning_index == no_winner) {
            return SearchResult{false, "", "", request.range_count, elapsed_seconds};
        }
        const std::string winning_offset =
            add_to_offset(request.range_start, static_cast<std::uint64_t>(winning_index));
        SearchRequest verification = request;
        verification.range_start = winning_offset;
        verification.range_count = 1;
        const SearchResult verified = search_cpu(verification);
        if (!verified.found || verified.offset_hex != winning_offset) {
            throw std::runtime_error("CPU verification rejected the CUDA winning candidate.");
        }
        return SearchResult{true, verified.address, winning_offset, request.range_count,
                            elapsed_seconds};
    }

    int device_index{};
    CudaSearchMode mode{CudaSearchMode::Batched};
    int threads{128};
    int multiprocessors{};
    int resident_blocks{};
    int points{};
    std::uint64_t chains{};
    cudaStream_t stream{};
    cudaEvent_t started{};
    cudaEvent_t completed{};
    unsigned long long* device_winner{};
    unsigned long long* host_winner{};
    cuda_secp::AffinePoint* device_batch_table{};
    cuda_chain::Point* device_chain_points{};
    cuda_chain::Point chain_stride_point{};
    bool chain_state_valid{};
    std::string chain_public_key;
    std::string chain_next_start;
    bool has_cached_stride{};
    std::uint64_t cached_grid_stride{};
    cuda_secp::AffinePoint cached_stride_point{};
};

CudaSearchSession::CudaSearchSession(int device_index, CudaSearchMode mode)
    : impl_(std::make_unique<Impl>(device_index, mode)) {}

CudaSearchSession::~CudaSearchSession() = default;
CudaSearchSession::CudaSearchSession(CudaSearchSession&&) noexcept = default;
CudaSearchSession& CudaSearchSession::operator=(CudaSearchSession&&) noexcept = default;

SearchResult CudaSearchSession::search(const SearchRequest& request) {
    if (!impl_) {
        throw std::logic_error("Cannot search with a moved-from CUDA session.");
    }
    return impl_->search(request);
}

int CudaSearchSession::device_index() const noexcept {
    return impl_ ? impl_->device_index : -1;
}

int CudaSearchSession::threads_per_block() const noexcept {
    return impl_ ? impl_->threads : 0;
}

int CudaSearchSession::resident_block_target() const noexcept {
    return impl_ ? impl_->resident_blocks : 0;
}

int CudaSearchSession::points_per_thread() const noexcept {
    return impl_ ? impl_->points : 0;
}

std::uint64_t CudaSearchSession::chain_count() const noexcept {
    return impl_ ? impl_->chains : 0;
}

SearchResult search_cuda(const SearchRequest& request, int device_index, CudaSearchMode mode) {
    CudaSearchSession session(device_index, mode);
    return session.search(request);
}

CudaFleetSearchResult search_cuda_all(const SearchRequest& request, CudaSearchMode mode) {
    validate_pattern(request.pattern, request.prefix, request.suffix);
    if (request.range_count == 0) {
        throw std::invalid_argument("Range count must be at least 1.");
    }
    static_cast<void>(add_to_offset(request.range_start, request.range_count - 1));

    int available_devices = 0;
    check_cuda(cudaGetDeviceCount(&available_devices), "cudaGetDeviceCount(fleet search)");
    if (available_devices < 1) {
        throw std::runtime_error("No CUDA devices are available for fleet search.");
    }
    const int used_devices =
        request.range_count < static_cast<std::uint64_t>(available_devices)
            ? static_cast<int>(request.range_count)
            : available_devices;
    constexpr std::uint64_t per_device_limit = 1ULL << 26;
    if (request.range_count > per_device_limit * static_cast<std::uint64_t>(used_devices)) {
        throw std::invalid_argument(
            "Fleet range count exceeds 67108864 candidates per active CUDA device.");
    }

    std::vector<SearchResult> device_results(static_cast<std::size_t>(used_devices));
    std::vector<std::exception_ptr> device_errors(static_cast<std::size_t>(used_devices));
    std::vector<std::thread> workers;
    workers.reserve(static_cast<std::size_t>(used_devices));
    const std::uint64_t base_count =
        request.range_count / static_cast<std::uint64_t>(used_devices);
    const std::uint64_t remainder =
        request.range_count % static_cast<std::uint64_t>(used_devices);
    std::uint64_t relative_start = 0;
    const auto started = std::chrono::steady_clock::now();
    for (int device = 0; device < used_devices; ++device) {
        const std::uint64_t shard_count =
            base_count + (static_cast<std::uint64_t>(device) < remainder ? 1ULL : 0ULL);
        SearchRequest shard = request;
        shard.range_start = add_to_offset(request.range_start, relative_start);
        shard.range_count = shard_count;
        relative_start += shard_count;
        workers.emplace_back([&, device, shard = std::move(shard)]() {
            try {
                CudaSearchMode shard_mode = mode;
                if (mode == CudaSearchMode::Chained8 ||
                    mode == CudaSearchMode::Chained16) {
                    cudaDeviceProp properties{};
                    check_cuda(cudaGetDeviceProperties(&properties, device),
                               "cudaGetDeviceProperties(fleet shard)");
                    const std::uint64_t chains =
                        static_cast<std::uint64_t>(properties.multiProcessorCount) *
                        4ULL * 64ULL *
                        (mode == CudaSearchMode::Chained16 ? 16ULL : 8ULL);
                    if (shard.range_count < chains * 4ULL) {
                        shard_mode = CudaSearchMode::Batched;
                    }
                }
                device_results[static_cast<std::size_t>(device)] =
                    search_cuda(shard, device, shard_mode);
            } catch (...) {
                device_errors[static_cast<std::size_t>(device)] = std::current_exception();
            }
        });
    }
    for (std::thread& worker : workers) {
        worker.join();
    }
    const auto completed = std::chrono::steady_clock::now();
    for (const std::exception_ptr& error : device_errors) {
        if (error != nullptr) {
            std::rethrow_exception(error);
        }
    }

    const double elapsed_seconds =
        std::chrono::duration<double>(completed - started).count();
    SearchResult combined{false, "", "", request.range_count, elapsed_seconds};
    for (const SearchResult& candidate : device_results) {
        if (candidate.found &&
            (!combined.found || candidate.offset_hex < combined.offset_hex)) {
            combined.found = true;
            combined.address = candidate.address;
            combined.offset_hex = candidate.offset_hex;
        }
    }
    return CudaFleetSearchResult{std::move(combined), available_devices, used_devices};
}

}  // namespace tronforge
