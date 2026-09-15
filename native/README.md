# TronForge native generator CLI

This directory contains the standalone correctness and hardware-test stage for the native wallet
generator. It is intentionally isolated from FastAPI until its output is verified on the target
GPU machine.

The CPU reference search uses the same custody boundary planned for CUDA: it receives a compressed
secp256k1 public key and a non-zero offset range, not the server's base private key. A successful
search returns the matching Tron address and offset. The server can later combine that offset with
its encrypted base scalar and independently verify the address.

## Build

Requirements:

- CMake 3.24 or newer
- A C++20 compiler
- OpenSSL 3 development headers
- Optional: NVIDIA driver and CUDA toolkit (`nvcc`) for GPU commands

```bash
cd native
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build --parallel
ctest --test-dir build --output-on-failure
```

When CUDA is available, configure for the GPUs installed on the build machine:

```bash
cmake --fresh -S . -B build-cuda \
  -DCMAKE_BUILD_TYPE=Release \
  -DTRONFORGE_ENABLE_CUDA=ON \
  -DTRONFORGE_REQUIRE_CUDA=ON \
  -DCMAKE_CUDA_COMPILER=/usr/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-cuda --parallel
```

The configure output explicitly says whether CUDA support was enabled. A CPU-only build remains
useful for correctness testing but is not a production generator.

## Commands

Run deterministic secp256k1, Keccak-256, Base58Check, Tron-address, and offset-search vectors:

```bash
./build/tronforge-generator self-test
```

List CUDA devices detected through the CUDA runtime:

```bash
./build/tronforge-generator gpu-info
```

Launch and validate a real arithmetic kernel plus the GPU Keccak-256, double-SHA256, Tron payload,
and Base58Check address pipeline on a selected GPU:

```bash
./build/tronforge-generator gpu-self-test --device 0
```

Derive a known test address from a private scalar:

```bash
./build/tronforge-generator derive \
  --private-key 0000000000000000000000000000000000000000000000000000000000000001
```

`derive` is for deterministic testing only. Passing production private keys in command-line
arguments can expose them through process inspection and shell history.

Test a public-point offset range on the CPU:

```bash
./build/tronforge-generator search-cpu \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 2x2 \
  --prefix Dv \
  --suffix 9K \
  --start 1 \
  --count 1
```

Run the same custody-safe range search on one GPU:

```bash
./build-cuda/tronforge-generator search-gpu \
  --device 0 \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 2x2 \
  --prefix Dv \
  --suffix 9K \
  --start 1 \
  --count 1
```

The validation CUDA search is deliberately bounded to 67,108,864 candidates per launch. The
reference mode computes each thread's point independently; the incremental and batched modes remain
regression comparisons. Every reported GPU winner is regenerated and pattern-checked by the CPU
reference before the CLI returns it.

The incremental comparison mode assigns each thread a lane and advances it by a precomputed grid
stride instead of repeating scalar multiplication for every candidate:

```bash
./build-cuda/tronforge-generator search-gpu-incremental \
  --device 0 \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 2x2 \
  --prefix ZZ \
  --suffix ZZ \
  --start 1 \
  --count 65536
```

The batched comparison mode generates nine symmetric points per field inversion:

```bash
./build-cuda/tronforge-generator search-gpu-batched \
  --device 0 \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 3x4 \
  --prefix ZZZ \
  --suffix ZZZZ \
  --start 1 \
  --count 1048576
```

The chained comparison modes use four 64-bit secp256k1 field limbs and batch-invert 8 or 16
independent point updates per thread. Both retain point state across exact-multiple, contiguous
search requests in one session:

```bash
./build-cuda/tronforge-generator search-gpu-chained16 \
  --device 0 \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 3x4 --prefix ZZZ --suffix ZZZZ --start 1 --count 67108864
```

`search-gpu-chained8` accepts the same options. Both modes cover the exact offset mapping
`start + chain_id + step * chain_count`; the GPU receives only the public base point and offset
range. Their four-limb point updates are checked against independently derived reference points
for both chain widths by `gpu-self-test` before the persistent worker accepts work.

The production scheduler uses the persistent line-protocol command internally:

```bash
./build-cuda/tronforge-generator serve-gpu --device 0
```

It runs the cryptographic GPU self-test once, keeps the CUDA context resident, and accepts repeated
`SEARCH PUBLIC_KEY PATTERN PREFIX SUFFIX START COUNT` lines on standard input. Each response is one
JSON line on standard output. The protocol carries only public points and offsets; it never receives
the server's private base scalar. Operators normally start this through `python -m app.generator`
rather than interacting with the protocol manually.

The persistent worker also reuses one CUDA stream, timing events, a pinned host result buffer, the
device result buffer, and the immutable secp256k1 batch table. It also owns persistent 16-chain
point state. Its startup handshake reports the launch dimensions, chain count, and short-search
threshold. Short requests use the existing batched kernel; sustained ranges use the chained kernel.
When a chained request ends on a complete chain cycle and the next request starts at the immediately
following offset, point state is reused without reinitialization. One-shot search commands use the
same implementation through a temporary session, which keeps them useful as regression comparisons.

Both production CUDA paths apply an exact Base58 numeric-range prefilter after Keccak-256.
Candidates whose 21-byte Tron payload cannot produce the requested prefix skip both SHA-256 checksum
passes and full Base58 encoding. Ambiguous checksum-boundary payloads continue through the full
pipeline, so the prefilter cannot discard a valid result.

## Profiling build

Generate CUDA line information and print compiler resource usage for Nsight Compute without changing
the release search behavior:

```bash
cmake --fresh -S . -B build-profile \
  -DCMAKE_BUILD_TYPE=Release \
  -DTRONFORGE_REQUIRE_CUDA=ON \
  -DTRONFORGE_CUDA_PROFILE=ON \
  -DCMAKE_CUDA_COMPILER=/usr/bin/nvcc \
  -DCMAKE_CUDA_ARCHITECTURES=89
cmake --build build-profile --parallel
```

Profile a no-match batch so the complete kernel executes:

```bash
ncu --set basic --kernel-name-base demangled \
  --kernel-name 'regex:.*batched_search_kernel.*' \
  ./build-profile/tronforge-generator search-gpu-batched \
  --device 0 \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 3x4 --prefix ZZZ --suffix ZZZZ --start 1 --count 67108864
```

Run one job across every CUDA device detected in the machine. The total range is divided into
balanced, contiguous, non-overlapping shards and each device runs in its own host thread:

```bash
./build-cuda/tronforge-generator search-gpu-all \
  --public-key 0279be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798 \
  --pattern 3x4 \
  --prefix ZZZ \
  --suffix ZZZZ \
  --start 1 \
  --count 67108864
```

The fleet result reports both `devices_used` and `devices_available`. Each device result is checked
by the CPU reference, and the coordinator returns the lowest verified offset if multiple shards
contain a match.

Generate a fresh wallet without placing private-key material in command-line arguments:

```bash
./build-cuda/tronforge-generator generate-wallet \
  --pattern 3x4 \
  --prefix Avc \
  --suffix wqe1 \
  --output ./wallet-Avc-wqe1.json
```

The command creates a fresh cryptographically random base scalar inside the process and searches
successive, non-overlapping chunks across all detected GPUs until it finds a match. Each GPU owns a
different 2^128-offset band and a persistent session, so its own chunks remain contiguous even while
other GPUs search concurrently. Large chunks are aligned to that GPU's chain count for point-state
reuse; small chunks use the batched kernel. It reconstructs
the final private key, independently derives and checks the address, and creates the output file
with owner-only `0600` permissions. It refuses to overwrite an existing file. Progress goes to
standard error; the private key is written only to the requested file.

The wallet file contains live private-key material. Keep it offline, never paste it into a website,
and do not use test-vector addresses or keys for funds. The CLI path is suitable for controlled
local validation; production delivery still requires encryption, access auditing, and a secrets
manager or KMS.

The `--prefix` value starts after the mandatory Tron `T`. Supported patterns are `3x4`, `2x5`,
`4x3`, and `2x2`. The first character after `T` is exact and must be an uppercase Base58 letter or
`9`; safe case-insensitive matching is automatic for the remaining alphabetic characters.

All machine-readable results are emitted as one JSON object on standard output. Errors are sent to
standard error with a non-zero exit status.

## CUDA delivery stages

1. CPU reference derivation and public-point offset search — implemented.
2. CUDA discovery and arithmetic launch smoke test — verified on the target RTX 4050.
3. GPU secp256k1 field and point-operation vectors — verified on the target RTX 4050.
4. GPU Keccak-256, SHA-256, and Base58Check address vector — verified on the target RTX 4050.
5. Single-GPU batched and four-limb 8/16-chain offset searches — implemented and verified on the
   target RTX 4050.
6. Multi-GPU balanced range coordinator — implemented; multi-device hardware validation pending.
7. Persistent native CUDA protocol and FastAPI scheduler integration — implemented.

The GPU self-test and deterministic offset vectors prove kernel launch, secp256k1 search, and
address-hashing correctness. Production throughput still depends on GPU power limits and the
selected batch size.
