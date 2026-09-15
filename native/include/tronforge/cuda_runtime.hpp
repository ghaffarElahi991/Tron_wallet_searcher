#pragma once

#include <memory>
#include <string>

#include "tronforge/tron.hpp"

namespace tronforge {

enum class CudaSearchMode {
    Reference,
    Incremental,
    Batched,
    Chained8,
    Chained16,
};

struct CudaFleetSearchResult {
    SearchResult search;
    int available_devices{};
    int used_devices{};
};

// A device-bound search engine that owns its CUDA stream, events, result buffers,
// launch configuration, and immutable secp256k1 batch table. One instance is
// intentionally used by one host thread at a time.
class CudaSearchSession {
  public:
    CudaSearchSession(int device_index, CudaSearchMode mode);
    ~CudaSearchSession();

    CudaSearchSession(const CudaSearchSession&) = delete;
    CudaSearchSession& operator=(const CudaSearchSession&) = delete;
    CudaSearchSession(CudaSearchSession&&) noexcept;
    CudaSearchSession& operator=(CudaSearchSession&&) noexcept;

    [[nodiscard]] SearchResult search(const SearchRequest& request);
    [[nodiscard]] int device_index() const noexcept;
    [[nodiscard]] int threads_per_block() const noexcept;
    [[nodiscard]] int resident_block_target() const noexcept;
    [[nodiscard]] int points_per_thread() const noexcept;
    [[nodiscard]] std::uint64_t chain_count() const noexcept;

  private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

[[nodiscard]] bool cuda_compiled();
[[nodiscard]] int cuda_device_count();
void cuda_host_crypto_self_test();
[[nodiscard]] std::string cuda_device_info_json();
[[nodiscard]] std::string cuda_self_test_json(int device_index);
[[nodiscard]] SearchResult search_cuda(const SearchRequest& request, int device_index,
                                       CudaSearchMode mode);
[[nodiscard]] CudaFleetSearchResult search_cuda_all(const SearchRequest& request,
                                                    CudaSearchMode mode);

}  // namespace tronforge
