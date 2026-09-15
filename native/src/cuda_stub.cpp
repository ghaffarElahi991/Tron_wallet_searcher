#include "tronforge/cuda_runtime.hpp"

#include <memory>
#include <stdexcept>

namespace tronforge {

bool cuda_compiled() { return false; }

int cuda_device_count() { return 0; }

void cuda_host_crypto_self_test() {}

struct CudaSearchSession::Impl {};

CudaSearchSession::CudaSearchSession(int /*device_index*/, CudaSearchMode /*mode*/)
    : impl_(std::make_unique<Impl>()) {
    throw std::runtime_error(
        "CUDA support is not compiled. Install the CUDA toolkit and configure again.");
}

CudaSearchSession::~CudaSearchSession() = default;
CudaSearchSession::CudaSearchSession(CudaSearchSession&&) noexcept = default;
CudaSearchSession& CudaSearchSession::operator=(CudaSearchSession&&) noexcept = default;

SearchResult CudaSearchSession::search(const SearchRequest& /*request*/) {
    throw std::runtime_error(
        "CUDA support is not compiled. Install the CUDA toolkit and configure again.");
}

int CudaSearchSession::device_index() const noexcept { return -1; }

int CudaSearchSession::threads_per_block() const noexcept { return 0; }

int CudaSearchSession::resident_block_target() const noexcept { return 0; }

int CudaSearchSession::points_per_thread() const noexcept { return 0; }

std::uint64_t CudaSearchSession::chain_count() const noexcept { return 0; }

std::string cuda_device_info_json() {
    return R"({"cuda_compiled":false,"devices":[]})";
}

std::string cuda_self_test_json(int /*device_index*/) {
    throw std::runtime_error(
        "CUDA support is not compiled. Install the CUDA toolkit and configure again.");
}

SearchResult search_cuda(const SearchRequest& /*request*/, int /*device_index*/,
                         CudaSearchMode /*mode*/) {
    throw std::runtime_error(
        "CUDA support is not compiled. Install the CUDA toolkit and configure again.");
}

CudaFleetSearchResult search_cuda_all(const SearchRequest& /*request*/,
                                      CudaSearchMode /*mode*/) {
    throw std::runtime_error(
        "CUDA support is not compiled. Install the CUDA toolkit and configure again.");
}

}  // namespace tronforge
