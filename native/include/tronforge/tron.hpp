#pragma once

#include <cstdint>
#include <string>

namespace tronforge {

struct SearchRequest {
    std::string public_key_hex;
    std::string pattern;
    std::string prefix;
    std::string suffix;
    std::string range_start;
    std::uint64_t range_count{};
};

struct SearchResult {
    bool found{};
    std::string address;
    std::string offset_hex;
    std::uint64_t attempts{};
    double elapsed_seconds{};
};

[[nodiscard]] std::string derive_tron_address(const std::string& private_key_hex);
[[nodiscard]] std::string compressed_public_key(const std::string& private_key_hex);
[[nodiscard]] std::string random_private_key();
[[nodiscard]] std::string add_private_key_offset(const std::string& private_key_hex,
                                                 const std::string& offset);
[[nodiscard]] std::string uncompressed_public_key(const std::string& public_key_hex);
[[nodiscard]] std::string derive_offset_public_key(const std::string& public_key_hex,
                                                   const std::string& offset);
[[nodiscard]] std::string add_to_offset(const std::string& offset, std::uint64_t increment);
[[nodiscard]] SearchResult search_cpu(const SearchRequest& request);
void validate_pattern(const std::string& pattern, const std::string& prefix,
                      const std::string& suffix);
void run_self_tests();

}  // namespace tronforge
