cmake_minimum_required(VERSION 3.16)
project(tchat_native LANGUAGES C CXX)

if (WIN32)
    set(CMAKE_CXX_STANDARD 20)
else()
    set(CMAKE_CXX_STANDARD 17)
endif()
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(GST REQUIRED gstreamer-1.0 gstreamer-audio-1.0 gstreamer-base-1.0 gstreamer-fft-1.0 glib-2.0 gobject-2.0)

set(WEBRTC_ROOT "" CACHE PATH "Path to WebRTC build output or source root")
set(WEBRTC_INCLUDE_DIR "" CACHE PATH "WebRTC include directory")
set(WEBRTC_LIB_DIR "" CACHE PATH "WebRTC library directory")
set(WEBRTC_LIBS "" CACHE STRING "WebRTC libraries to link")
if (WIN32)
    option(WEBRTC_LINK_LIBS "Link WebRTC .lib archives on Windows" OFF)
    option(WEBRTC_LINK_OBJS "Link WebRTC .obj files on Windows" ON)
endif()

set(ONNXRUNTIME_ROOT "" CACHE PATH "Path to ONNX Runtime")
set(ONNXRUNTIME_INCLUDE_DIR "" CACHE PATH "ONNX Runtime include directory")
set(ONNXRUNTIME_LIB_DIR "" CACHE PATH "ONNX Runtime library directory")
set(ONNXRUNTIME_LIBS "onnxruntime" CACHE STRING "ONNX Runtime libraries to link")

include_directories(${GST_INCLUDE_DIRS})
link_directories(${GST_LIBRARY_DIRS})
add_definitions(${GST_CFLAGS_OTHER})

if (NOT WEBRTC_INCLUDE_DIR AND WEBRTC_ROOT)
    if (EXISTS "${WEBRTC_ROOT}/modules/audio_processing/include/audio_processing.h")
        set(WEBRTC_INCLUDE_DIR "${WEBRTC_ROOT}")
    elseif (EXISTS "${WEBRTC_ROOT}/src/modules/audio_processing/include/audio_processing.h")
        set(WEBRTC_INCLUDE_DIR "${WEBRTC_ROOT}/src")
    elseif (EXISTS "${WEBRTC_ROOT}/include/modules/audio_processing/include/audio_processing.h")
        set(WEBRTC_INCLUDE_DIR "${WEBRTC_ROOT}/include")
    endif()
endif()
if (NOT WEBRTC_LIB_DIR AND WEBRTC_ROOT)
    if (EXISTS "${WEBRTC_ROOT}/lib")
        set(WEBRTC_LIB_DIR "${WEBRTC_ROOT}/lib")
    elseif (EXISTS "${WEBRTC_ROOT}/out/Release/obj")
        set(WEBRTC_LIB_DIR "${WEBRTC_ROOT}/out/Release/obj")
    elseif (EXISTS "${WEBRTC_ROOT}/out/Default/obj")
        set(WEBRTC_LIB_DIR "${WEBRTC_ROOT}/out/Default/obj")
    endif()
endif()
if (WEBRTC_INCLUDE_DIR)
    include_directories(${WEBRTC_INCLUDE_DIR})
    if (EXISTS "${WEBRTC_INCLUDE_DIR}/third_party/abseil-cpp")
        include_directories(${WEBRTC_INCLUDE_DIR}/third_party/abseil-cpp)
    endif()
endif()
set(WEBRTC_LIB_ROOT "${WEBRTC_LIB_DIR}")
if (WEBRTC_LIB_DIR)
    if (EXISTS "${WEBRTC_LIB_DIR}/api/audio")
        set(WEBRTC_LIB_ROOT "${WEBRTC_LIB_DIR}")
    endif()
    link_directories(${WEBRTC_LIB_DIR})
    if (EXISTS "${WEBRTC_LIB_ROOT}/api/audio")
        link_directories("${WEBRTC_LIB_ROOT}/api/audio")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/aecm")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/aecm")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/agc")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/agc")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/agc2")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/agc2")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/aec_dump")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/aec_dump")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/ns")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/ns")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/utility")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/utility")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/modules/audio_processing/vad")
        link_directories("${WEBRTC_LIB_ROOT}/modules/audio_processing/vad")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/common_audio")
        link_directories("${WEBRTC_LIB_ROOT}/common_audio")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/rtc_base")
        link_directories("${WEBRTC_LIB_ROOT}/rtc_base")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/rtc_base/system")
        link_directories("${WEBRTC_LIB_ROOT}/rtc_base/system")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/rtc_base/synchronization")
        link_directories("${WEBRTC_LIB_ROOT}/rtc_base/synchronization")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/rtc_base/experiments")
        link_directories("${WEBRTC_LIB_ROOT}/rtc_base/experiments")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/system_wrappers")
        link_directories("${WEBRTC_LIB_ROOT}/system_wrappers")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/audio/utility")
        link_directories("${WEBRTC_LIB_ROOT}/audio/utility")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/third_party/pffft")
        link_directories("${WEBRTC_LIB_ROOT}/third_party/pffft")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/api")
        link_directories("${WEBRTC_LIB_ROOT}/api")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/api/environment")
        link_directories("${WEBRTC_LIB_ROOT}/api/environment")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/api/rtc_event_log")
        link_directories("${WEBRTC_LIB_ROOT}/api/rtc_event_log")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/api/task_queue")
        link_directories("${WEBRTC_LIB_ROOT}/api/task_queue")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/api/units")
        link_directories("${WEBRTC_LIB_ROOT}/api/units")
    endif()
    if (EXISTS "${WEBRTC_LIB_ROOT}/third_party/protobuf")
        link_directories("${WEBRTC_LIB_ROOT}/third_party/protobuf")
    endif()
endif()

if (WEBRTC_LIB_DIR AND WEBRTC_LIBS STREQUAL "")
    if (WIN32)
        if (WEBRTC_LINK_LIBS)
            file(GLOB_RECURSE WEBRTC_LIB_FILES
                "${WEBRTC_LIB_ROOT}/api/**/*.lib"
                "${WEBRTC_LIB_ROOT}/modules/audio_processing/**/*.lib"
                "${WEBRTC_LIB_ROOT}/common_audio/**/*.lib"
                "${WEBRTC_LIB_ROOT}/audio/utility/**/*.lib"
                "${WEBRTC_LIB_ROOT}/rtc_base/**/*.lib"
                "${WEBRTC_LIB_ROOT}/system_wrappers/**/*.lib"
                "${WEBRTC_LIB_ROOT}/third_party/pffft/**/*.lib"
                "${WEBRTC_LIB_ROOT}/third_party/protobuf/**/*.lib"
            )
            list(APPEND WEBRTC_LIBS ${WEBRTC_LIB_FILES})
        endif()
        if (WEBRTC_LINK_OBJS)
            file(GLOB_RECURSE WEBRTC_OBJ_FILES
                "${WEBRTC_LIB_ROOT}/api/**/*.obj"
                "${WEBRTC_LIB_ROOT}/modules/audio_processing/**/*.obj"
                "${WEBRTC_LIB_ROOT}/common_audio/**/*.obj"
                "${WEBRTC_LIB_ROOT}/audio/utility/**/*.obj"
                "${WEBRTC_LIB_ROOT}/rtc_base/**/*.obj"
                "${WEBRTC_LIB_ROOT}/system_wrappers/**/*.obj"
                "${WEBRTC_LIB_ROOT}/third_party/pffft/**/*.obj"
                "${WEBRTC_LIB_ROOT}/third_party/protobuf/**/*.obj"
            )
            list(APPEND WEBRTC_LIBS ${WEBRTC_OBJ_FILES})
        endif()
    else()
    file(GLOB WEBRTC_APM_LIBS "${WEBRTC_LIB_ROOT}/modules/audio_processing/*.a")
    file(GLOB_RECURSE WEBRTC_APM_SUB_LIBS "${WEBRTC_LIB_ROOT}/modules/audio_processing/**/*.a")
    file(GLOB WEBRTC_AUDIO_CODING_ROOT_LIBS "${WEBRTC_LIB_ROOT}/modules/audio_coding/*.a")
    file(GLOB_RECURSE WEBRTC_AUDIO_CODING_LIBS "${WEBRTC_LIB_ROOT}/modules/audio_coding/**/*.a")
    file(GLOB WEBRTC_AUDIO_API_LIBS "${WEBRTC_LIB_ROOT}/api/audio/*.a")
    foreach(_lib ${WEBRTC_AUDIO_API_LIBS})
        if (_lib MATCHES "libaudio_processing\\.a$")
            file(SIZE "${_lib}" _lib_size)
            if (_lib_size EQUAL 0)
                list(REMOVE_ITEM WEBRTC_AUDIO_API_LIBS "${_lib}")
            endif()
        endif()
    endforeach()
    file(GLOB WEBRTC_COMMON_AUDIO_ROOT_LIBS "${WEBRTC_LIB_ROOT}/common_audio/*.a")
    file(GLOB_RECURSE WEBRTC_COMMON_AUDIO_LIBS "${WEBRTC_LIB_ROOT}/common_audio/**/*.a")
    file(GLOB WEBRTC_PROTO_LIBS "${WEBRTC_LIB_ROOT}/third_party/protobuf/*.a")
    file(GLOB WEBRTC_RTC_BASE_LIBS "${WEBRTC_LIB_ROOT}/rtc_base/*.a")
    file(GLOB WEBRTC_RTC_BASE_SYS_LIBS "${WEBRTC_LIB_ROOT}/rtc_base/system/*.a")
    file(GLOB WEBRTC_RTC_BASE_SYNC_LIBS "${WEBRTC_LIB_ROOT}/rtc_base/synchronization/*.a")
    file(GLOB WEBRTC_RTC_BASE_EXP_LIBS "${WEBRTC_LIB_ROOT}/rtc_base/experiments/*.a")
    file(GLOB_RECURSE WEBRTC_RTC_BASE_MEM_LIBS "${WEBRTC_LIB_ROOT}/rtc_base/memory/*.a")
    file(GLOB WEBRTC_SYSTEM_WRAPPERS_LIBS "${WEBRTC_LIB_ROOT}/system_wrappers/*.a")
    file(GLOB WEBRTC_AUDIO_UTIL_LIBS "${WEBRTC_LIB_ROOT}/audio/utility/*.a")
    file(GLOB WEBRTC_PFFFT_LIBS "${WEBRTC_LIB_ROOT}/third_party/pffft/*.a")
    file(GLOB WEBRTC_API_LIBS "${WEBRTC_LIB_ROOT}/api/*.a")
    file(GLOB WEBRTC_ENV_LIBS "${WEBRTC_LIB_ROOT}/api/environment/*.a")
    file(GLOB WEBRTC_RTC_EVENT_LOG_LIBS "${WEBRTC_LIB_ROOT}/api/rtc_event_log/*.a")
    file(GLOB WEBRTC_TASK_QUEUE_LIBS "${WEBRTC_LIB_ROOT}/api/task_queue/*.a")
    file(GLOB WEBRTC_UNITS_LIBS "${WEBRTC_LIB_ROOT}/api/units/*.a")
    list(APPEND WEBRTC_LIBS
        ${WEBRTC_APM_LIBS}
        ${WEBRTC_APM_SUB_LIBS}
        ${WEBRTC_AUDIO_CODING_ROOT_LIBS}
        ${WEBRTC_AUDIO_CODING_LIBS}
        ${WEBRTC_AUDIO_API_LIBS}
        ${WEBRTC_COMMON_AUDIO_ROOT_LIBS}
        ${WEBRTC_COMMON_AUDIO_LIBS}
        ${WEBRTC_PROTO_LIBS}
        ${WEBRTC_RTC_BASE_LIBS}
        ${WEBRTC_RTC_BASE_SYS_LIBS}
        ${WEBRTC_RTC_BASE_SYNC_LIBS}
        ${WEBRTC_RTC_BASE_EXP_LIBS}
        ${WEBRTC_RTC_BASE_MEM_LIBS}
        ${WEBRTC_SYSTEM_WRAPPERS_LIBS}
        ${WEBRTC_AUDIO_UTIL_LIBS}
        ${WEBRTC_PFFFT_LIBS}
        ${WEBRTC_API_LIBS}
        ${WEBRTC_ENV_LIBS}
        ${WEBRTC_RTC_EVENT_LOG_LIBS}
        ${WEBRTC_TASK_QUEUE_LIBS}
        ${WEBRTC_UNITS_LIBS}
    )
    set(WEBRTC_API_AUDIO_LIB "${WEBRTC_LIB_ROOT}/api/audio/libaudio_processing.a")
    if (EXISTS "${WEBRTC_API_AUDIO_LIB}")
        file(SIZE "${WEBRTC_API_AUDIO_LIB}" WEBRTC_API_AUDIO_SIZE)
        if (NOT WEBRTC_API_AUDIO_SIZE EQUAL 0)
            list(APPEND WEBRTC_LIBS "${WEBRTC_API_AUDIO_LIB}")
        endif()
    endif()
    endif()
endif()
if (WEBRTC_LIBS)
    list(REMOVE_DUPLICATES WEBRTC_LIBS)
endif()

if (NOT ONNXRUNTIME_INCLUDE_DIR AND ONNXRUNTIME_ROOT)
    if (EXISTS "${ONNXRUNTIME_ROOT}/include/onnxruntime_c_api.h")
        set(ONNXRUNTIME_INCLUDE_DIR "${ONNXRUNTIME_ROOT}/include")
    elseif (EXISTS "${ONNXRUNTIME_ROOT}/onnxruntime_c_api.h")
        set(ONNXRUNTIME_INCLUDE_DIR "${ONNXRUNTIME_ROOT}")
    endif()
endif()
if (NOT ONNXRUNTIME_LIB_DIR AND ONNXRUNTIME_ROOT)
    if (EXISTS "${ONNXRUNTIME_ROOT}/lib")
        set(ONNXRUNTIME_LIB_DIR "${ONNXRUNTIME_ROOT}/lib")
    endif()
endif()
if (ONNXRUNTIME_INCLUDE_DIR)
    include_directories(${ONNXRUNTIME_INCLUDE_DIR})
endif()
if (ONNXRUNTIME_LIB_DIR)
    link_directories(${ONNXRUNTIME_LIB_DIR})
endif()

set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/gst-plugins)

add_library(gstwebrtcaec3 SHARED
    webrtc_aec3/gstwebrtcaec3.cpp
)

target_include_directories(gstwebrtcaec3 PRIVATE ${GST_INCLUDE_DIRS})
target_link_libraries(gstwebrtcaec3 PRIVATE ${GST_LIBRARIES} ${WEBRTC_LIBS})
if (CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    target_compile_options(gstwebrtcaec3 PRIVATE -Wno-nullability-completeness)
endif()
if (MSVC)
    add_compile_options(/utf-8 /EHsc)
endif()
if (WIN32)
    target_link_libraries(gstwebrtcaec3 PRIVATE
        winmm
        ws2_32
        secur32
        dmoguids
        wmcodecdspuuid
        msdmo
        strmiids
        iphlpapi
    )
endif()
if (WEBRTC_LIB_DIR)
    file(GLOB_RECURSE WEBRTC_ABSL_OBJS
        "${WEBRTC_LIB_ROOT}/third_party/abseil-cpp/absl/**/*.o"
    )
    if (WEBRTC_ABSL_OBJS)
        target_sources(gstwebrtcaec3 PRIVATE ${WEBRTC_ABSL_OBJS})
    endif()
    file(GLOB WEBRTC_RNNOISE_WEIGHTS_OBJ
        "${WEBRTC_LIB_ROOT}/third_party/rnnoise/rnn_vad/rnn_vad_weights.o"
    )
    if (WEBRTC_RNNOISE_WEIGHTS_OBJ)
        target_sources(gstwebrtcaec3 PRIVATE ${WEBRTC_RNNOISE_WEIGHTS_OBJ})
    endif()
    file(GLOB WEBRTC_UTF8_RANGE_OBJ
        "${WEBRTC_LIB_ROOT}/third_party/protobuf/utf8_range/utf8_range.o"
    )
    if (WEBRTC_UTF8_RANGE_OBJ)
        target_sources(gstwebrtcaec3 PRIVATE ${WEBRTC_UTF8_RANGE_OBJ})
    endif()
endif()
if (APPLE)
    target_link_libraries(gstwebrtcaec3 PRIVATE "-framework CoreFoundation")
endif()

add_library(gstdeepfilternet SHARED
    deepfilternet/gstdeepfilternet.cpp
)

target_include_directories(gstdeepfilternet PRIVATE ${GST_INCLUDE_DIRS})
target_link_libraries(gstdeepfilternet PRIVATE ${GST_LIBRARIES} onnxruntime)

message(STATUS "GStreamer include dirs: ${GST_INCLUDE_DIRS}")
message(STATUS "WebRTC include dir: ${WEBRTC_INCLUDE_DIR}")
message(STATUS "ONNX Runtime include dir: ${ONNXRUNTIME_INCLUDE_DIR}")
