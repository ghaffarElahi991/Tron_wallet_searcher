cmake_minimum_required(VERSION 3.24)

project(tronforge_native VERSION 0.1.0 LANGUAGES CXX)

option(TRONFORGE_ENABLE_CUDA "Build CUDA discovery and smoke-test support" ON)
option(TRONFORGE_REQUIRE_CUDA "Fail configuration when CUDA support is unavailable" OFF)
option(TRONFORGE_CUDA_PROFILE "Build CUDA kernels with profiler line information" OFF)

if(TRONFORGE_REQUIRE_CUDA)
    set(TRONFORGE_ENABLE_CUDA ON)
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(OpenSSL 3.0 REQUIRED COMPONENTS Crypto)
find_package(Threads REQUIRED)

add_executable(
    tronforge-generator
    src/main.cpp
    src/tron.cpp
)

target_include_directories(tronforge-generator PRIVATE include)
target_link_libraries(tronforge-generator PRIVATE OpenSSL::Crypto Threads::Threads)
target_compile_options(
    tronforge-generator
    PRIVATE
        $<$<COMPILE_LANGUAGE:CXX>:-Wall;-Wextra;-Wpedantic>
)

if(TRONFORGE_ENABLE_CUDA)
    include(CheckLanguage)
    check_language(CUDA)
endif()

if(TRONFORGE_ENABLE_CUDA AND CMAKE_CUDA_COMPILER)
    enable_language(CUDA)
    target_sources(tronforge-generator PRIVATE src/cuda_runtime.cu)
    set_target_properties(
        tronforge-generator
        PROPERTIES
            CUDA_STANDARD 17
            CUDA_STANDARD_REQUIRED ON
    )
    target_compile_definitions(tronforge-generator PRIVATE TRONFORGE_HAS_CUDA=1)
    if(TRONFORGE_CUDA_PROFILE)
        target_compile_options(
            tronforge-generator
            PRIVATE
                $<$<COMPILE_LANGUAGE:CUDA>:-lineinfo;--resource-usage>
        )
    endif()
    message(STATUS "TronForge CUDA support: enabled")
else()
    if(TRONFORGE_REQUIRE_CUDA)
        message(FATAL_ERROR "TronForge CUDA support was required, but nvcc was not found")
    endif()
    target_sources(tronforge-generator PRIVATE src/cuda_stub.cpp)
    message(STATUS "TronForge CUDA support: unavailable (building CPU reference CLI)")
endif()

include(CTest)
if(BUILD_TESTING)
    add_test(NAME native-self-test COMMAND tronforge-generator self-test)
endif()
