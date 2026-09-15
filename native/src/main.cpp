#include "tronforge/cuda_runtime.hpp"
#include "tronforge/tron.hpp"

#include <openssl/crypto.h>

#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <exception>
#include <fcntl.h>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <sys/stat.h>
#include <thread>
#include <unordered_map>
#include <utility>
#include <unistd.h>
#include <vector>

namespace {

using Options = std::unordered_map<std::string, std::string>;

class SensitiveString {
  public:
    explicit SensitiveString(std::string value = {}) : value_(std::move(value)) {}
    SensitiveString(const SensitiveString&) = delete;
    SensitiveString& operator=(const SensitiveString&) = delete;
    ~SensitiveString() {
        if (!value_.empty()) {
            OPENSSL_cleanse(value_.data(), value_.size());
        }
    }

    [[nodiscard]] const std::string& get() const { return value_; }
    [[nodiscard]] std::string& get() { return value_; }

  private:
    std::string value_;
};

void print_usage() {
    std::cout
        << "TronForge native generator reference CLI\n\n"
        << "Commands:\n"
        << "  self-test\n"
        << "  gpu-info\n"
        << "  gpu-self-test [--device INDEX]\n"
        << "  derive --private-key HEX64\n"
        << "  search-cpu --public-key HEX --pattern 2x2 --prefix QR --suffix 99\n"
        << "             --start NUMBER --count NUMBER\n"
        << "  search-gpu --device INDEX --public-key HEX --pattern 2x2 --prefix QR\n"
        << "             --suffix 99 --start NUMBER --count NUMBER\n"
        << "  search-gpu-incremental (accepts the same options as search-gpu)\n\n"
        << "  search-gpu-batched (accepts the same options as search-gpu)\n\n"
        << "  search-gpu-chained8 (four-limb, 8 persistent points/thread)\n"
        << "  search-gpu-chained16 (four-limb, 16 persistent points/thread)\n\n"
        << "  serve-gpu --device INDEX\n"
        << "            Persistent line-protocol CUDA worker for the Python scheduler.\n\n"
        << "  search-gpu-all --public-key HEX --pattern 2x2 --prefix QR --suffix 99\n"
        << "                 --start NUMBER --count NUMBER\n\n"
        << "  generate-wallet --pattern 2x2 --prefix QR --suffix 99 --output FILE\n"
        << "                  [--chunk-count CANDIDATES_PER_GPU]\n\n"
        << "The search prefix starts after the fixed TRON T. Search output never contains\n"
        << "the server's base private key. Case-insensitive loose matching is automatic\n"
        << "except for the first character after T.\n";
}

[[noreturn]] void throw_file_error(std::string_view operation, const std::string& path,
                                   int error_number) {
    throw std::runtime_error(std::string(operation) + " " + path + ": " +
                             std::strerror(error_number));
}

void write_private_wallet_file(const std::string& path, const std::string& contents) {
    const int file = ::open(path.c_str(), O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,
                            S_IRUSR | S_IWUSR);
    if (file < 0) {
        throw_file_error("Could not create wallet file", path, errno);
    }
    bool complete = false;
    try {
        std::size_t position = 0;
        while (position < contents.size()) {
            const ssize_t written =
                ::write(file, contents.data() + position, contents.size() - position);
            if (written < 0 && errno == EINTR) {
                continue;
            }
            if (written <= 0) {
                throw_file_error("Could not write wallet file", path,
                                 written < 0 ? errno : EIO);
            }
            position += static_cast<std::size_t>(written);
        }
        if (::fsync(file) != 0) {
            throw_file_error("Could not synchronize wallet file", path, errno);
        }
        if (::close(file) != 0) {
            throw_file_error("Could not close wallet file", path, errno);
        }
        complete = true;
    } catch (...) {
        ::close(file);
        ::unlink(path.c_str());
        throw;
    }
    if (!complete) {
        ::unlink(path.c_str());
    }
}

[[nodiscard]] Options parse_options(int argc, char** argv, int first) {
    Options options;
    for (int index = first; index < argc; index += 2) {
        const std::string key = argv[index];
        if (!key.starts_with("--") || index + 1 >= argc) {
            throw std::invalid_argument("Options must use --name value pairs.");
        }
        if (!options.emplace(key.substr(2), argv[index + 1]).second) {
            throw std::invalid_argument("Option was provided more than once: " + key);
        }
    }
    return options;
}

[[nodiscard]] const std::string& required(const Options& options, std::string_view name) {
    const auto value = options.find(std::string(name));
    if (value == options.end()) {
        throw std::invalid_argument("Missing required option --" + std::string(name) + '.');
    }
    return value->second;
}

[[nodiscard]] std::uint64_t unsigned_number(const std::string& value,
                                            const std::string& option_name) {
    std::size_t consumed = 0;
    std::uint64_t parsed = 0;
    try {
        parsed = std::stoull(value, &consumed, 10);
    } catch (const std::exception&) {
        throw std::invalid_argument("--" + option_name + " must be an unsigned decimal number.");
    }
    if (consumed != value.size()) {
        throw std::invalid_argument("--" + option_name + " must be an unsigned decimal number.");
    }
    return parsed;
}

void print_search_result(const tronforge::SearchResult& result, std::string_view backend,
                         int devices_used = 0, int devices_available = 0) {
    const double rate = result.elapsed_seconds > 0
                            ? static_cast<double>(result.attempts) / result.elapsed_seconds
                            : 0.0;
    std::cout << std::fixed << std::setprecision(6) << R"({"backend":")" << backend
              << R"(","found":)" << (result.found ? "true" : "false")
              << R"(,"attempts":)" << result.attempts
              << R"(,"elapsed_seconds":)" << result.elapsed_seconds
              << R"(,"candidates_per_second":)" << std::setprecision(2) << rate;
    if (devices_used > 0) {
        std::cout << R"(,"devices_used":)" << devices_used
                  << R"(,"devices_available":)" << devices_available;
    }
    if (result.found) {
        std::cout << R"(,"address":")" << result.address << R"(","offset":")"
                  << result.offset_hex << '"';
    }
    std::cout << "}\n";
}

[[nodiscard]] int parsed_device(const Options& options) {
    const auto value = unsigned_number(required(options, "device"), "device");
    if (value > static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
        throw std::invalid_argument("--device is too large.");
    }
    return static_cast<int>(value);
}

[[nodiscard]] std::string json_escape(std::string_view value) {
    std::ostringstream output;
    for (const unsigned char character : value) {
        switch (character) {
            case '"':
                output << R"(\")";
                break;
            case '\\':
                output << R"(\\)";
                break;
            case '\n':
                output << R"(\n)";
                break;
            case '\r':
                output << R"(\r)";
                break;
            case '\t':
                output << R"(\t)";
                break;
            default:
                if (character < 0x20U) {
                    output << R"(\u00)" << std::hex << std::setw(2) << std::setfill('0')
                           << static_cast<unsigned int>(character) << std::dec;
                } else {
                    output << static_cast<char>(character);
                }
        }
    }
    return output.str();
}

int serve_gpu(int device) {
    // The self-test initializes this process's CUDA primary context. That context remains
    // resident for every subsequent SEARCH request handled by this process.
    static_cast<void>(tronforge::cuda_self_test_json(device));
    tronforge::CudaSearchSession batched_session(device, tronforge::CudaSearchMode::Batched);
    tronforge::CudaSearchSession chained_session(device, tronforge::CudaSearchMode::Chained16);
    const std::uint64_t chained_threshold = chained_session.chain_count() * 4;
    std::cout << R"({"ready":true,"backend":"cuda-persistent","protocol":1,"device":)"
              << device << R"(,"self_test_passed":true,"resources_reused":true)"
              << R"(,"prefix_prefilter":true,"threads_per_block":)"
              << chained_session.threads_per_block() << R"(,"resident_block_target":)"
              << chained_session.resident_block_target() << R"(,"points_per_thread":)"
              << chained_session.points_per_thread() << R"(,"chain_count":)"
              << chained_session.chain_count() << R"(,"chained_threshold":)"
              << chained_threshold << '}' << '\n'
              << std::flush;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) {
            continue;
        }
        if (line == "QUIT") {
            std::cout << R"({"stopped":true})" << '\n' << std::flush;
            return 0;
        }
        try {
            std::istringstream input(line);
            std::string operation;
            tronforge::SearchRequest request;
            std::string count;
            std::string trailing;
            if (!(input >> operation >> request.public_key_hex >> request.pattern >>
                  request.prefix >> request.suffix >> request.range_start >> count) ||
                operation != "SEARCH" || (input >> trailing)) {
                throw std::invalid_argument(
                    "Expected SEARCH PUBLIC_KEY PATTERN PREFIX SUFFIX START COUNT.");
            }
            request.range_count = unsigned_number(count, "count");
            const bool use_chained = request.range_count >= chained_threshold;
            print_search_result(
                use_chained ? chained_session.search(request) : batched_session.search(request),
                use_chained ? "cuda-persistent-chained16" : "cuda-persistent-batched");
            std::cout << std::flush;
        } catch (const std::exception& error) {
            std::cout << R"({"ok":false,"error":")" << json_escape(error.what())
                      << R"("})" << '\n'
                      << std::flush;
            return 1;
        }
    }
    return 0;
}

std::string device_band_start(int device) {
    // Each GPU owns a disjoint 2^128-offset band for the lifetime of a CLI search.
    // Repeated chunks within that band remain contiguous for its persistent chains.
    std::ostringstream offset;
    offset << std::hex << std::setfill('0') << std::setw(32) << device
           << std::setw(32) << 1;
    return offset.str();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc < 2 || std::string_view(argv[1]) == "--help" ||
            std::string_view(argv[1]) == "help") {
            print_usage();
            return 0;
        }
        const std::string command = argv[1];
        if (command == "self-test") {
            if (argc != 2) {
                throw std::invalid_argument("self-test does not accept options.");
            }
            tronforge::run_self_tests();
            tronforge::cuda_host_crypto_self_test();
            const std::string first_band = device_band_start(0);
            const std::string second_band = device_band_start(1);
            if (first_band.size() != 64 || second_band.size() != 64 ||
                first_band.substr(0, 32) == second_band.substr(0, 32) ||
                !(tronforge::add_to_offset(first_band, 1) < second_band)) {
                throw std::runtime_error("GPU offset-band isolation self-test failed.");
            }
            std::cout << R"({"passed":true,"suite":"tron-reference"})" << '\n';
            return 0;
        }
        if (command == "gpu-info") {
            if (argc != 2) {
                throw std::invalid_argument("gpu-info does not accept options.");
            }
            std::cout << tronforge::cuda_device_info_json() << '\n';
            return 0;
        }
        if (command == "gpu-self-test") {
            const Options options = parse_options(argc, argv, 2);
            int device = 0;
            if (const auto configured = options.find("device"); configured != options.end()) {
                const auto parsed = unsigned_number(configured->second, "device");
                if (parsed > static_cast<std::uint64_t>(std::numeric_limits<int>::max())) {
                    throw std::invalid_argument("--device is too large.");
                }
                device = static_cast<int>(parsed);
            }
            std::cout << tronforge::cuda_self_test_json(device) << '\n';
            return 0;
        }
        if (command == "serve-gpu") {
            const Options options = parse_options(argc, argv, 2);
            return serve_gpu(parsed_device(options));
        }
        if (command == "generate-wallet") {
            const Options options = parse_options(argc, argv, 2);
            const std::string& pattern = required(options, "pattern");
            const std::string& prefix = required(options, "prefix");
            const std::string& suffix = required(options, "suffix");
            const std::string& output_path = required(options, "output");
            tronforge::validate_pattern(pattern, prefix, suffix);
            std::uint64_t per_device_chunk = 1ULL << 26;
            if (const auto configured = options.find("chunk-count");
                configured != options.end()) {
                per_device_chunk = unsigned_number(configured->second, "chunk-count");
            }
            constexpr std::uint64_t maximum_per_device_chunk = 1ULL << 26;
            if (per_device_chunk == 0 || per_device_chunk > maximum_per_device_chunk) {
                throw std::invalid_argument(
                    "--chunk-count must be between 1 and 67108864 candidates per GPU.");
            }
            const int devices = tronforge::cuda_device_count();
            if (devices < 1) {
                throw std::runtime_error("No CUDA devices are available for wallet generation.");
            }

            std::vector<tronforge::CudaSearchSession> sessions;
            std::vector<std::uint64_t> chunk_counts;
            std::vector<std::string> next_offsets;
            sessions.reserve(static_cast<std::size_t>(devices));
            chunk_counts.reserve(static_cast<std::size_t>(devices));
            next_offsets.reserve(static_cast<std::size_t>(devices));
            for (int device = 0; device < devices; ++device) {
                sessions.emplace_back(device, tronforge::CudaSearchMode::Chained16);
                const std::uint64_t chains = sessions.back().chain_count();
                std::uint64_t count = per_device_chunk;
                if (chains > 0 && count < chains * 4) {
                    sessions.back() = tronforge::CudaSearchSession(
                        device, tronforge::CudaSearchMode::Batched);
                } else if (chains > 0) {
                    count -= count % chains;
                }
                chunk_counts.push_back(count);
                next_offsets.push_back(device_band_start(device));
            }

            SensitiveString base_private_key(tronforge::random_private_key());
            const std::string base_public_key =
                tronforge::compressed_public_key(base_private_key.get());
            std::uint64_t total_attempts = 0;
            const auto generation_started = std::chrono::steady_clock::now();
            for (;;) {
                std::vector<tronforge::SearchResult> results(
                    static_cast<std::size_t>(devices));
                std::vector<std::exception_ptr> errors(
                    static_cast<std::size_t>(devices));
                std::vector<std::thread> workers;
                workers.reserve(static_cast<std::size_t>(devices));
                for (int device = 0; device < devices; ++device) {
                    const tronforge::SearchRequest shard{
                        base_public_key, pattern, prefix, suffix,
                        next_offsets[static_cast<std::size_t>(device)],
                        chunk_counts[static_cast<std::size_t>(device)]};
                    workers.emplace_back([&, device, shard]() {
                        try {
                            results[static_cast<std::size_t>(device)] =
                                sessions[static_cast<std::size_t>(device)].search(shard);
                        } catch (...) {
                            errors[static_cast<std::size_t>(device)] =
                                std::current_exception();
                        }
                    });
                }
                for (std::thread& worker : workers) {
                    worker.join();
                }
                for (const std::exception_ptr& error : errors) {
                    if (error != nullptr) {
                        std::rethrow_exception(error);
                    }
                }
                tronforge::SearchResult winner{};
                for (int device = 0; device < devices; ++device) {
                    const std::size_t index = static_cast<std::size_t>(device);
                    if (total_attempts >
                        std::numeric_limits<std::uint64_t>::max() - results[index].attempts) {
                        throw std::overflow_error(
                            "Wallet-generation attempt counter overflowed.");
                    }
                    total_attempts += results[index].attempts;
                    if (results[index].found &&
                        (!winner.found || results[index].offset_hex < winner.offset_hex)) {
                        winner = results[index];
                    }
                }
                const double elapsed = std::chrono::duration<double>(
                                           std::chrono::steady_clock::now() - generation_started)
                                           .count();
                const double rate =
                    elapsed > 0 ? static_cast<double>(total_attempts) / elapsed : 0.0;
                std::cerr << std::fixed << std::setprecision(2)
                          << R"({"event":"search-progress","attempts":)" << total_attempts
                          << R"(,"elapsed_seconds":)" << elapsed << R"(,"candidates_per_second":)"
                          << rate << R"(,"devices_used":)" << devices << "}\n";
                if (winner.found) {
                    SensitiveString final_private_key(tronforge::add_private_key_offset(
                        base_private_key.get(), winner.offset_hex));
                    const std::string verified_address =
                        tronforge::derive_tron_address(final_private_key.get());
                    if (verified_address != winner.address) {
                        throw std::runtime_error(
                            "Recovered private key does not match the GPU result.");
                    }
                    const std::string final_public_key =
                        tronforge::compressed_public_key(final_private_key.get());
                    SensitiveString wallet_file;
                    wallet_file.get().reserve(384);
                    wallet_file.get() = R"({"network":"tron-mainnet","address":")";
                    wallet_file.get() += verified_address;
                    wallet_file.get() += R"(","public_key":")";
                    wallet_file.get() += final_public_key;
                    wallet_file.get() += R"(","private_key":")";
                    wallet_file.get() += final_private_key.get();
                    wallet_file.get() += R"(","pattern":")";
                    wallet_file.get() += pattern;
                    wallet_file.get() += R"(","prefix":")";
                    wallet_file.get() += prefix;
                    wallet_file.get() += R"(","suffix":")";
                    wallet_file.get() += suffix;
                    wallet_file.get() += "\"}\n";
                    write_private_wallet_file(output_path, wallet_file.get());
                    std::cout << std::fixed << std::setprecision(6)
                              << R"({"generated":true,"address":")" << verified_address
                              << R"(","attempts":)" << total_attempts
                              << R"(,"elapsed_seconds":)" << elapsed
                              << R"(,"candidates_per_second":)" << std::setprecision(2) << rate
                              << R"(,"devices_used":)" << devices << "}\n";
                    return 0;
                }
                for (int device = 0; device < devices; ++device) {
                    const std::size_t index = static_cast<std::size_t>(device);
                    next_offsets[index] = tronforge::add_to_offset(
                        next_offsets[index], chunk_counts[index]);
                    if (next_offsets[index].substr(0, 32) !=
                        device_band_start(device).substr(0, 32)) {
                        throw std::overflow_error(
                            "A GPU's reserved non-overlapping offset band was exhausted.");
                    }
                }
            }
        }
        if (command == "derive") {
            const Options options = parse_options(argc, argv, 2);
            const std::string private_key = required(options, "private-key");
            std::cout << R"({"address":")" << tronforge::derive_tron_address(private_key)
                      << R"(","public_key":")"
                      << tronforge::compressed_public_key(private_key) << R"("})" << '\n';
            return 0;
        }
        if (command == "search-cpu") {
            const Options options = parse_options(argc, argv, 2);
            tronforge::SearchRequest request{
                required(options, "public-key"), required(options, "pattern"),
                required(options, "prefix"),     required(options, "suffix"),
                required(options, "start"),      unsigned_number(required(options, "count"),
                                                                  "count"),
            };
            print_search_result(tronforge::search_cpu(request), "cpu");
            return 0;
        }
        if (command == "search-gpu-all") {
            const Options options = parse_options(argc, argv, 2);
            tronforge::SearchRequest request{
                required(options, "public-key"), required(options, "pattern"),
                required(options, "prefix"),     required(options, "suffix"),
                required(options, "start"),      unsigned_number(required(options, "count"),
                                                                  "count"),
            };
            const tronforge::CudaFleetSearchResult result =
                tronforge::search_cuda_all(request, tronforge::CudaSearchMode::Chained16);
            print_search_result(result.search, "cuda-all-hybrid", result.used_devices,
                                result.available_devices);
            return 0;
        }
        if (command == "search-gpu" || command == "search-gpu-incremental" ||
            command == "search-gpu-batched" || command == "search-gpu-chained8" ||
            command == "search-gpu-chained16") {
            const Options options = parse_options(argc, argv, 2);
            tronforge::SearchRequest request{
                required(options, "public-key"), required(options, "pattern"),
                required(options, "prefix"),     required(options, "suffix"),
                required(options, "start"),      unsigned_number(required(options, "count"),
                                                                  "count"),
            };
            const bool incremental = command == "search-gpu-incremental";
            const bool batched = command == "search-gpu-batched";
            const bool chained8 = command == "search-gpu-chained8";
            const bool chained16 = command == "search-gpu-chained16";
            const tronforge::CudaSearchMode mode =
                chained16   ? tronforge::CudaSearchMode::Chained16
                : chained8  ? tronforge::CudaSearchMode::Chained8
                : batched   ? tronforge::CudaSearchMode::Batched
                : incremental ? tronforge::CudaSearchMode::Incremental
                              : tronforge::CudaSearchMode::Reference;
            print_search_result(tronforge::search_cuda(
                                    request, parsed_device(options), mode),
                                chained16   ? "cuda-chained16"
                                : chained8  ? "cuda-chained8"
                                : batched       ? "cuda-batched"
                                : incremental ? "cuda-incremental"
                                              : "cuda-reference");
            return 0;
        }
        throw std::invalid_argument("Unknown command: " + command);
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }
}
