#include "gstdeepfilternet.h"

#include <gst/audio/audio.h>
#include <gst/base/gstadapter.h>
#include <gst/fft/gstfftf32.h>
#include <gst/gst.h>

#include <onnxruntime_c_api.h>

#include <glib/gstdio.h>
#include <glib.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>

#ifndef PACKAGE
#define PACKAGE "tchat"
#endif

struct _GstDeepFilterNet {
    GstElement parent;
    GstPad *sinkpad;
    GstPad *srcpad;
    GstAdapter *adapter;
    guint frame_samples;
    guint frame_bytes;
    gboolean bypass;
    gint consecutive_over;
    gint64 cooldown_until;
    guint64 bypass_count;
    guint frame_counter;
    double p50_ms;
    double p95_ms;
    std::vector<double> frame_times;
    gchar *model_path;
    gchar *model_dir;
    gchar *input_name;
    gchar *output_name;
    const OrtApi *ort;
    OrtEnv *env;
    OrtSessionOptions *session_opts;
    OrtMemoryInfo *mem_info;
    OrtSession *single_session;

    OrtSession *enc_session;
    OrtSession *erb_session;
    OrtSession *df_session;
    gchar *enc_input_names[2];
    gchar *enc_output_names[7];
    gchar *erb_input_names[5];
    gchar *erb_output_names[1];
    gchar *df_input_names[2];
    gchar *df_output_names[2];
    gboolean use_dfn3;

    gint sample_rate;
    gint fft_size;
    gint hop_size;
    gint nb_erb;
    gint nb_df;
    gint df_order;
    gint df_lookahead;

    GstFFTF32 *fft;
    GstFFTF32 *ifft;
    std::vector<float> time_buffer;
    std::vector<float> fft_in;
    std::vector<float> ifft_out;
    std::vector<float> window;
    std::vector<float> ola_buffer;
    std::vector<float> ola_norm;
    std::vector<GstFFTF32Complex> spectrum;
    std::vector<float> magnitude;
    std::vector<float> mask_bins;
    std::vector<float> erb_filters;
    std::vector<float> erb_bin_sum;
    std::vector<float> feat_erb;
    std::vector<float> feat_spec;
    std::vector<float> mask_erb;
    std::vector<float> df_coefs;
    std::vector<float> df_cur_real;
    std::vector<float> df_cur_imag;
    std::vector<float> df_hist_real;
    std::vector<float> df_hist_imag;
    gint df_hist_filled;
};

G_DEFINE_TYPE(GstDeepFilterNet, gst_deepfilternet, GST_TYPE_ELEMENT)

enum {
    PROP_0,
    PROP_MODEL_PATH,
    PROP_MODEL_DIR,
    PROP_BYPASS,
    PROP_INPUT_NAME,
    PROP_OUTPUT_NAME,
};

static GstStaticPadTemplate sink_template = GST_STATIC_PAD_TEMPLATE(
    "sink",
    GST_PAD_SINK,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"));

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src",
    GST_PAD_SRC,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-raw,format=F32LE,rate=48000,channels=1,layout=interleaved"));

static double percentile(std::vector<double> values, double p) {
    if (values.empty()) {
        return 0.0;
    }
    std::sort(values.begin(), values.end());
    size_t idx = static_cast<size_t>(std::floor((p / 100.0) * (values.size() - 1)));
    return values[idx];
}

static gboolean ort_ok(GstDeepFilterNet *self, OrtStatus *status, const gchar *what) {
    if (!status) {
        return TRUE;
    }
    const char *msg = self->ort->GetErrorMessage(status);
    GST_WARNING_OBJECT(self, "%s: %s", what, msg ? msg : "unknown");
    self->ort->ReleaseStatus(status);
    return FALSE;
}

static void dfn_apply_default_config(GstDeepFilterNet *self) {
    self->sample_rate = 48000;
    self->fft_size = 960;
    self->hop_size = 480;
    self->nb_erb = 32;
    self->nb_df = 96;
    self->df_order = 5;
    self->df_lookahead = 0;
}

static void dfn_load_config(GstDeepFilterNet *self, const gchar *config_path) {
    dfn_apply_default_config(self);
    if (!config_path || !g_file_test(config_path, G_FILE_TEST_EXISTS)) {
        return;
    }
    GError *error = nullptr;
    GKeyFile *key = g_key_file_new();
    if (!g_key_file_load_from_file(key, config_path, G_KEY_FILE_NONE, &error)) {
        if (error) {
            g_error_free(error);
        }
        g_key_file_unref(key);
        return;
    }
    self->sample_rate = g_key_file_get_integer(key, "df", "sr", nullptr);
    self->fft_size = g_key_file_get_integer(key, "df", "fft_size", nullptr);
    self->hop_size = g_key_file_get_integer(key, "df", "hop_size", nullptr);
    self->nb_erb = g_key_file_get_integer(key, "df", "nb_erb", nullptr);
    self->nb_df = g_key_file_get_integer(key, "df", "nb_df", nullptr);
    self->df_order = g_key_file_get_integer(key, "df", "df_order", nullptr);
    self->df_lookahead = g_key_file_get_integer(key, "df", "df_lookahead", nullptr);
    g_key_file_unref(key);
    if (self->sample_rate <= 0 || self->sample_rate != 48000) {
        self->sample_rate = 48000;
    }
    if (self->hop_size <= 0) {
        self->hop_size = 480;
    }
    if (self->fft_size <= 0) {
        self->fft_size = self->hop_size * 2;
    }
    if (self->nb_erb <= 0) {
        self->nb_erb = 32;
    }
    if (self->nb_df <= 0) {
        self->nb_df = 96;
    }
    if (self->df_order <= 0) {
        self->df_order = 5;
    }
}

static float erb_scale(float f) {
    return 21.4f * log10f(1.0f + 0.00437f * f);
}

static float inv_erb_scale(float erb) {
    return (powf(10.0f, erb / 21.4f) - 1.0f) / 0.00437f;
}

static void dfn_prepare_buffers(GstDeepFilterNet *self) {
    if (self->hop_size != static_cast<gint>(self->frame_samples)) {
        self->hop_size = static_cast<gint>(self->frame_samples);
    }
    if (self->fft_size < self->hop_size * 2) {
        self->fft_size = self->hop_size * 2;
    }
    gint fft_bins = self->fft_size / 2 + 1;
    if (self->nb_df > fft_bins) {
        self->nb_df = fft_bins;
    }
    self->time_buffer.assign(self->fft_size, 0.0f);
    self->fft_in.assign(self->fft_size, 0.0f);
    self->ifft_out.assign(self->fft_size, 0.0f);
    self->window.assign(self->fft_size, 0.0f);
    self->ola_buffer.assign(self->fft_size, 0.0f);
    self->ola_norm.assign(self->hop_size, 1.0f);
    self->spectrum.assign(fft_bins, GstFFTF32Complex{0.0f, 0.0f});
    self->magnitude.assign(fft_bins, 0.0f);
    self->mask_bins.assign(fft_bins, 1.0f);
    self->erb_filters.assign(self->nb_erb * fft_bins, 0.0f);
    self->erb_bin_sum.assign(fft_bins, 0.0f);
    self->feat_erb.assign(self->nb_erb, 0.0f);
    self->feat_spec.assign(self->nb_df * 2, 0.0f);
    self->mask_erb.assign(self->nb_erb, 1.0f);
    self->df_coefs.assign(self->nb_df * self->df_order * 2, 0.0f);
    self->df_cur_real.assign(self->nb_df, 0.0f);
    self->df_cur_imag.assign(self->nb_df, 0.0f);
    self->df_hist_real.assign(self->df_order * self->nb_df, 0.0f);
    self->df_hist_imag.assign(self->df_order * self->nb_df, 0.0f);
    self->df_hist_filled = 0;

    for (gint i = 0; i < self->fft_size; ++i) {
        float w = 0.5f - 0.5f * cosf(2.0f * G_PI * i / (self->fft_size - 1));
        self->window[i] = sqrtf(w);
    }
    for (gint i = 0; i < self->hop_size; ++i) {
        float v = self->window[i] * self->window[i];
        if (i + self->hop_size < self->fft_size) {
            v += self->window[i + self->hop_size] * self->window[i + self->hop_size];
        }
        if (v < 1e-6f) {
            v = 1.0f;
        }
        self->ola_norm[i] = v;
    }

    float erb_min = erb_scale(0.0f);
    float erb_max = erb_scale(self->sample_rate * 0.5f);
    std::vector<float> erb_points(self->nb_erb + 2, 0.0f);
    for (gint i = 0; i < self->nb_erb + 2; ++i) {
        float t = static_cast<float>(i) / static_cast<float>(self->nb_erb + 1);
        erb_points[i] = inv_erb_scale(erb_min + t * (erb_max - erb_min));
    }

    for (gint b = 0; b < self->nb_erb; ++b) {
        float lower = erb_points[b];
        float center = erb_points[b + 1];
        float upper = erb_points[b + 2];
        float sum = 0.0f;
        for (gint k = 0; k < fft_bins; ++k) {
            float f = static_cast<float>(k) * self->sample_rate / self->fft_size;
            float weight = 0.0f;
            if (f >= lower && f <= center && center > lower) {
                weight = (f - lower) / (center - lower);
            } else if (f > center && f <= upper && upper > center) {
                weight = (upper - f) / (upper - center);
            }
            self->erb_filters[b * fft_bins + k] = weight;
            sum += weight;
            self->erb_bin_sum[k] += weight;
        }
        if (sum > 0.0f) {
            for (gint k = 0; k < fft_bins; ++k) {
                self->erb_filters[b * fft_bins + k] /= sum;
            }
        }
    }

    if (self->fft) {
        gst_fft_f32_free(self->fft);
    }
    if (self->ifft) {
        gst_fft_f32_free(self->ifft);
    }
    self->fft = gst_fft_f32_new(self->fft_size, FALSE);
    self->ifft = gst_fft_f32_new(self->fft_size, TRUE);

    self->frame_samples = self->hop_size;
    self->frame_bytes = self->frame_samples * sizeof(float);
}

static gboolean dfn_init_ort(GstDeepFilterNet *self) {
    if (!self->ort) {
        self->ort = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    }
    if (!self->env) {
        if (!ort_ok(self, self->ort->CreateEnv(ORT_LOGGING_LEVEL_WARNING, "dfn", &self->env), "CreateEnv")) {
            return FALSE;
        }
    }
    if (!self->session_opts) {
        if (!ort_ok(self, self->ort->CreateSessionOptions(&self->session_opts), "CreateSessionOptions")) {
            return FALSE;
        }
        self->ort->SetIntraOpNumThreads(self->session_opts, 1);
        self->ort->SetInterOpNumThreads(self->session_opts, 1);
    }
    if (!self->mem_info) {
        if (!ort_ok(self, self->ort->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &self->mem_info), "CreateCpuMemoryInfo")) {
            return FALSE;
        }
    }
    return TRUE;
}

static gchar *dfn_strdup_ort_name(GstDeepFilterNet *self, OrtAllocator *allocator, char *name) {
    if (!name) {
        return nullptr;
    }
    gchar *dup = g_strdup(name);
    allocator->Free(allocator, name);
    return dup;
}

static void dfn_free_name_array(gchar **names, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        g_free(names[i]);
        names[i] = nullptr;
    }
}

static gboolean dfn_init_single_session(GstDeepFilterNet *self) {
    if (!self->model_path || !g_file_test(self->model_path, G_FILE_TEST_EXISTS)) {
        return FALSE;
    }
    GStatBuf st;
    if (g_stat(self->model_path, &st) != 0 || st.st_size < 1024) {
        return FALSE;
    }
    if (!dfn_init_ort(self)) {
        return FALSE;
    }
    if (self->single_session) {
        self->ort->ReleaseSession(self->single_session);
        self->single_session = nullptr;
    }
    if (!ort_ok(self, self->ort->CreateSession(self->env, self->model_path, self->session_opts, &self->single_session), "CreateSession single")) {
        return FALSE;
    }

    if (!self->input_name || !self->output_name) {
        OrtAllocator *allocator = nullptr;
        self->ort->GetAllocatorWithDefaultOptions(&allocator);
        if (!self->input_name) {
            char *name = nullptr;
            self->ort->SessionGetInputName(self->single_session, 0, allocator, &name);
            self->input_name = dfn_strdup_ort_name(self, allocator, name);
        }
        if (!self->output_name) {
            char *name = nullptr;
            self->ort->SessionGetOutputName(self->single_session, 0, allocator, &name);
            self->output_name = dfn_strdup_ort_name(self, allocator, name);
        }
    }
    return TRUE;
}

static gboolean dfn_init_dfn3_session(GstDeepFilterNet *self) {
    if (!self->model_dir || !g_file_test(self->model_dir, G_FILE_TEST_IS_DIR)) {
        return FALSE;
    }
    gchar *enc_path = g_build_filename(self->model_dir, "enc.onnx", nullptr);
    gchar *erb_path = g_build_filename(self->model_dir, "erb_dec.onnx", nullptr);
    gchar *df_path = g_build_filename(self->model_dir, "df_dec.onnx", nullptr);
    gchar *config_path = g_build_filename(self->model_dir, "config.ini", nullptr);

    gboolean ok = g_file_test(enc_path, G_FILE_TEST_EXISTS) &&
                 g_file_test(erb_path, G_FILE_TEST_EXISTS) &&
                 g_file_test(df_path, G_FILE_TEST_EXISTS);

    if (!ok) {
        g_free(enc_path);
        g_free(erb_path);
        g_free(df_path);
        g_free(config_path);
        return FALSE;
    }

    if (!dfn_init_ort(self)) {
        g_free(enc_path);
        g_free(erb_path);
        g_free(df_path);
        g_free(config_path);
        return FALSE;
    }

    if (self->enc_session) {
        self->ort->ReleaseSession(self->enc_session);
        self->enc_session = nullptr;
    }
    if (self->erb_session) {
        self->ort->ReleaseSession(self->erb_session);
        self->erb_session = nullptr;
    }
    if (self->df_session) {
        self->ort->ReleaseSession(self->df_session);
        self->df_session = nullptr;
    }

    if (!ort_ok(self, self->ort->CreateSession(self->env, enc_path, self->session_opts, &self->enc_session), "CreateSession enc")) {
        ok = FALSE;
    }
    if (ok && !ort_ok(self, self->ort->CreateSession(self->env, erb_path, self->session_opts, &self->erb_session), "CreateSession erb")) {
        ok = FALSE;
    }
    if (ok && !ort_ok(self, self->ort->CreateSession(self->env, df_path, self->session_opts, &self->df_session), "CreateSession df")) {
        ok = FALSE;
    }

    if (ok) {
        dfn_load_config(self, config_path);
        dfn_prepare_buffers(self);
    }

    g_free(enc_path);
    g_free(erb_path);
    g_free(df_path);
    g_free(config_path);

    if (!ok) {
        return FALSE;
    }

    OrtAllocator *allocator = nullptr;
    self->ort->GetAllocatorWithDefaultOptions(&allocator);

    dfn_free_name_array(self->enc_input_names, 2);
    dfn_free_name_array(self->enc_output_names, 7);
    dfn_free_name_array(self->erb_input_names, 5);
    dfn_free_name_array(self->erb_output_names, 1);
    dfn_free_name_array(self->df_input_names, 2);
    dfn_free_name_array(self->df_output_names, 2);

    for (size_t i = 0; i < 2; ++i) {
        char *name = nullptr;
        self->ort->SessionGetInputName(self->enc_session, i, allocator, &name);
        self->enc_input_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }
    for (size_t i = 0; i < 7; ++i) {
        char *name = nullptr;
        self->ort->SessionGetOutputName(self->enc_session, i, allocator, &name);
        self->enc_output_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }
    for (size_t i = 0; i < 5; ++i) {
        char *name = nullptr;
        self->ort->SessionGetInputName(self->erb_session, i, allocator, &name);
        self->erb_input_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }
    for (size_t i = 0; i < 1; ++i) {
        char *name = nullptr;
        self->ort->SessionGetOutputName(self->erb_session, i, allocator, &name);
        self->erb_output_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }
    for (size_t i = 0; i < 2; ++i) {
        char *name = nullptr;
        self->ort->SessionGetInputName(self->df_session, i, allocator, &name);
        self->df_input_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }
    for (size_t i = 0; i < 2; ++i) {
        char *name = nullptr;
        self->ort->SessionGetOutputName(self->df_session, i, allocator, &name);
        self->df_output_names[i] = dfn_strdup_ort_name(self, allocator, name);
    }

    return TRUE;
}

static gboolean dfn_init_session(GstDeepFilterNet *self) {
    self->use_dfn3 = FALSE;
    if (dfn_init_dfn3_session(self)) {
        self->use_dfn3 = TRUE;
        return TRUE;
    }
    return dfn_init_single_session(self);
}

static void dfn_release_sessions(GstDeepFilterNet *self) {
    if (self->ort) {
        if (self->single_session) {
            self->ort->ReleaseSession(self->single_session);
            self->single_session = nullptr;
        }
        if (self->enc_session) {
            self->ort->ReleaseSession(self->enc_session);
            self->enc_session = nullptr;
        }
        if (self->erb_session) {
            self->ort->ReleaseSession(self->erb_session);
            self->erb_session = nullptr;
        }
        if (self->df_session) {
            self->ort->ReleaseSession(self->df_session);
            self->df_session = nullptr;
        }
        if (self->session_opts) {
            self->ort->ReleaseSessionOptions(self->session_opts);
            self->session_opts = nullptr;
        }
        if (self->mem_info) {
            self->ort->ReleaseMemoryInfo(self->mem_info);
            self->mem_info = nullptr;
        }
        if (self->env) {
            self->ort->ReleaseEnv(self->env);
            self->env = nullptr;
        }
    }
    self->ort = nullptr;
}

static OrtValue *dfn_pick_enc_output(GstDeepFilterNet *self, const gchar *name, OrtValue **enc_outputs) {
    if (g_strcmp0(name, "e0") == 0) {
        return enc_outputs[0];
    }
    if (g_strcmp0(name, "e1") == 0) {
        return enc_outputs[1];
    }
    if (g_strcmp0(name, "e2") == 0) {
        return enc_outputs[2];
    }
    if (g_strcmp0(name, "e3") == 0) {
        return enc_outputs[3];
    }
    if (g_strcmp0(name, "emb") == 0) {
        return enc_outputs[4];
    }
    if (g_strcmp0(name, "c0") == 0) {
        return enc_outputs[5];
    }
    return nullptr;
}

static size_t dfn_tensor_len(GstDeepFilterNet *self, const OrtValue *value) {
    OrtTensorTypeAndShapeInfo *shape = nullptr;
    size_t count = 0;
    if (!ort_ok(self, self->ort->GetTensorTypeAndShape(value, &shape), "GetTensorTypeAndShape")) {
        return 0;
    }
    self->ort->GetTensorShapeElementCount(shape, &count);
    self->ort->ReleaseTensorTypeAndShapeInfo(shape);
    return count;
}

static gboolean dfn_run_single(GstDeepFilterNet *self, const float *in, float *out) {
    if (!self->single_session) {
        return FALSE;
    }
    int64_t dims[3] = {1, 1, static_cast<int64_t>(self->frame_samples)};
    OrtValue *input_tensor = nullptr;
    OrtValue *output_tensor = nullptr;
    if (!ort_ok(self, self->ort->CreateTensorWithDataAsOrtValue(
                        self->mem_info,
                        const_cast<float *>(in),
                        self->frame_bytes,
                        dims,
                        3,
                        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                        &input_tensor),
                    "CreateTensor single")) {
        return FALSE;
    }
    const char *input_names[] = {self->input_name ? self->input_name : "input"};
    const char *output_names[] = {self->output_name ? self->output_name : "output"};
    if (!ort_ok(self, self->ort->Run(self->single_session, nullptr, input_names, (const OrtValue *const *)&input_tensor, 1, output_names, 1, &output_tensor), "Run single")) {
        self->ort->ReleaseValue(input_tensor);
        return FALSE;
    }
    float *out_data = nullptr;
    self->ort->GetTensorMutableData(output_tensor, reinterpret_cast<void **>(&out_data));
    if (out_data) {
        memcpy(out, out_data, self->frame_bytes);
    } else {
        memcpy(out, in, self->frame_bytes);
    }
    self->ort->ReleaseValue(input_tensor);
    self->ort->ReleaseValue(output_tensor);
    return TRUE;
}

static gboolean dfn_run_dfn3(GstDeepFilterNet *self, const float *in, float *out) {
    if (!self->use_dfn3 || !self->enc_session || !self->erb_session || !self->df_session) {
        return FALSE;
    }
    if (!self->fft || !self->ifft) {
        return FALSE;
    }

    gint fft_bins = self->fft_size / 2 + 1;

    memmove(self->time_buffer.data(), self->time_buffer.data() + self->hop_size, (self->fft_size - self->hop_size) * sizeof(float));
    memcpy(self->time_buffer.data() + (self->fft_size - self->hop_size), in, self->frame_bytes);

    for (gint i = 0; i < self->fft_size; ++i) {
        self->fft_in[i] = self->time_buffer[i] * self->window[i];
    }

    gst_fft_f32_fft(self->fft, self->fft_in.data(), self->spectrum.data());

    for (gint k = 0; k < fft_bins; ++k) {
        float re = self->spectrum[k].r;
        float im = self->spectrum[k].i;
        self->magnitude[k] = sqrtf(re * re + im * im);
    }

    for (gint b = 0; b < self->nb_erb; ++b) {
        float sum = 0.0f;
        for (gint k = 0; k < fft_bins; ++k) {
            sum += self->erb_filters[b * fft_bins + k] * self->magnitude[k];
        }
        self->feat_erb[b] = logf(1e-6f + sum);
    }

    for (gint k = 0; k < self->nb_df; ++k) {
        self->feat_spec[k] = self->spectrum[k].r;
        self->feat_spec[self->nb_df + k] = self->spectrum[k].i;
    }

    int64_t dims_erb[4] = {1, 1, 1, self->nb_erb};
    int64_t dims_spec[4] = {1, 2, 1, self->nb_df};
    OrtValue *enc_inputs[2] = {nullptr, nullptr};

    if (!ort_ok(self, self->ort->CreateTensorWithDataAsOrtValue(
                        self->mem_info,
                        self->feat_erb.data(),
                        self->nb_erb * sizeof(float),
                        dims_erb,
                        4,
                        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                        &enc_inputs[0]),
                    "CreateTensor feat_erb")) {
        return FALSE;
    }
    if (!ort_ok(self, self->ort->CreateTensorWithDataAsOrtValue(
                        self->mem_info,
                        self->feat_spec.data(),
                        self->feat_spec.size() * sizeof(float),
                        dims_spec,
                        4,
                        ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT,
                        &enc_inputs[1]),
                    "CreateTensor feat_spec")) {
        self->ort->ReleaseValue(enc_inputs[0]);
        return FALSE;
    }

    OrtValue *enc_outputs[7] = {nullptr};
    const char *enc_input_names[2] = {self->enc_input_names[0], self->enc_input_names[1]};
    const char *enc_output_names[7] = {self->enc_output_names[0], self->enc_output_names[1], self->enc_output_names[2], self->enc_output_names[3], self->enc_output_names[4], self->enc_output_names[5], self->enc_output_names[6]};
    OrtValue *enc_inputs_ordered[2] = {nullptr, nullptr};
    for (gint i = 0; i < 2; ++i) {
        if (g_strcmp0(enc_input_names[i], "feat_erb") == 0) {
            enc_inputs_ordered[i] = enc_inputs[0];
        } else if (g_strcmp0(enc_input_names[i], "feat_spec") == 0) {
            enc_inputs_ordered[i] = enc_inputs[1];
        } else {
            enc_inputs_ordered[i] = enc_inputs[i];
        }
    }

    if (!ort_ok(self, self->ort->Run(self->enc_session, nullptr, enc_input_names, (const OrtValue *const *)&enc_inputs_ordered[0], 2, enc_output_names, 7, enc_outputs), "Run enc")) {
        self->ort->ReleaseValue(enc_inputs[0]);
        self->ort->ReleaseValue(enc_inputs[1]);
        return FALSE;
    }

    OrtValue *erb_inputs[5] = {nullptr};
    const char *erb_input_names[5] = {self->erb_input_names[0], self->erb_input_names[1], self->erb_input_names[2], self->erb_input_names[3], self->erb_input_names[4]};
    for (gint i = 0; i < 5; ++i) {
        OrtValue *val = dfn_pick_enc_output(self, erb_input_names[i], enc_outputs);
        if (!val) {
            val = enc_outputs[4];
        }
        erb_inputs[i] = val;
    }
    OrtValue *erb_outputs[1] = {nullptr};
    const char *erb_output_names[1] = {self->erb_output_names[0]};
    if (!ort_ok(self, self->ort->Run(self->erb_session, nullptr, erb_input_names, (const OrtValue *const *)&erb_inputs[0], 5, erb_output_names, 1, erb_outputs), "Run erb_dec")) {
        for (gint i = 0; i < 2; ++i) {
            self->ort->ReleaseValue(enc_inputs[i]);
        }
        for (gint i = 0; i < 7; ++i) {
            self->ort->ReleaseValue(enc_outputs[i]);
        }
        return FALSE;
    }

    OrtValue *df_inputs[2] = {nullptr};
    const char *df_input_names[2] = {self->df_input_names[0], self->df_input_names[1]};
    for (gint i = 0; i < 2; ++i) {
        OrtValue *val = dfn_pick_enc_output(self, df_input_names[i], enc_outputs);
        if (!val) {
            val = enc_outputs[4];
        }
        df_inputs[i] = val;
    }
    OrtValue *df_outputs[2] = {nullptr};
    const char *df_output_names[2] = {self->df_output_names[0], self->df_output_names[1]};
    if (!ort_ok(self, self->ort->Run(self->df_session, nullptr, df_input_names, (const OrtValue *const *)&df_inputs[0], 2, df_output_names, 2, df_outputs), "Run df_dec")) {
        for (gint i = 0; i < 2; ++i) {
            self->ort->ReleaseValue(enc_inputs[i]);
        }
        for (gint i = 0; i < 7; ++i) {
            self->ort->ReleaseValue(enc_outputs[i]);
        }
        self->ort->ReleaseValue(erb_outputs[0]);
        return FALSE;
    }

    float *mask_data = nullptr;
    self->ort->GetTensorMutableData(erb_outputs[0], reinterpret_cast<void **>(&mask_data));
    size_t mask_len = dfn_tensor_len(self, erb_outputs[0]);
    if (mask_data && mask_len >= static_cast<size_t>(self->nb_erb)) {
        memcpy(self->mask_erb.data(), mask_data, self->nb_erb * sizeof(float));
    } else {
        std::fill(self->mask_erb.begin(), self->mask_erb.end(), 1.0f);
    }

    size_t coef_len = 0;
    for (gint i = 0; i < 2; ++i) {
        if (g_strcmp0(df_output_names[i], "coefs") == 0) {
            float *coef_data = nullptr;
            self->ort->GetTensorMutableData(df_outputs[i], reinterpret_cast<void **>(&coef_data));
            coef_len = dfn_tensor_len(self, df_outputs[i]);
            if (coef_data && coef_len > 0) {
                size_t expected = static_cast<size_t>(self->nb_df * self->df_order * 2);
                if (coef_len == expected && self->df_coefs.size() == expected) {
                    memcpy(self->df_coefs.data(), coef_data, coef_len * sizeof(float));
                } else {
                    self->df_coefs.assign(coef_data, coef_data + coef_len);
                }
            }
        }
    }

    for (gint k = 0; k < fft_bins; ++k) {
        float sum = 0.0f;
        for (gint b = 0; b < self->nb_erb; ++b) {
            sum += self->erb_filters[b * fft_bins + k] * self->mask_erb[b];
        }
        float denom = self->erb_bin_sum[k] > 1e-6f ? self->erb_bin_sum[k] : 1.0f;
        float mask = sum / denom;
        if (mask < 0.0f) {
            mask = 0.0f;
        }
        if (mask > 2.0f) {
            mask = 2.0f;
        }
        self->mask_bins[k] = mask;
    }

    for (gint k = 0; k < fft_bins; ++k) {
        self->spectrum[k].r *= self->mask_bins[k];
        self->spectrum[k].i *= self->mask_bins[k];
    }

    for (gint k = 0; k < self->nb_df; ++k) {
        self->df_cur_real[k] = self->spectrum[k].r;
        self->df_cur_imag[k] = self->spectrum[k].i;
    }

    for (gint o = self->df_order - 1; o > 0; --o) {
        memcpy(&self->df_hist_real[o * self->nb_df], &self->df_hist_real[(o - 1) * self->nb_df], self->nb_df * sizeof(float));
        memcpy(&self->df_hist_imag[o * self->nb_df], &self->df_hist_imag[(o - 1) * self->nb_df], self->nb_df * sizeof(float));
    }
    for (gint k = 0; k < self->nb_df; ++k) {
        self->df_hist_real[k] = self->df_cur_real[k];
        self->df_hist_imag[k] = self->df_cur_imag[k];
    }
    if (self->df_hist_filled < self->df_order) {
        self->df_hist_filled += 1;
    }

    gboolean df_ready = coef_len >= static_cast<size_t>(self->nb_df * self->df_order * 2);
    if (df_ready && self->df_hist_filled >= self->df_order) {
        for (gint k = 0; k < self->nb_df; ++k) {
            float out_re = 0.0f;
            float out_im = 0.0f;
            for (gint o = 0; o < self->df_order; ++o) {
                size_t coef_idx = k * self->df_order * 2 + o * 2;
                float h_re = self->df_coefs[coef_idx];
                float h_im = self->df_coefs[coef_idx + 1];
                float x_re = self->df_hist_real[o * self->nb_df + k];
                float x_im = self->df_hist_imag[o * self->nb_df + k];
                out_re += h_re * x_re - h_im * x_im;
                out_im += h_re * x_im + h_im * x_re;
            }
            self->spectrum[k].r = out_re;
            self->spectrum[k].i = out_im;
        }
    }

    gst_fft_f32_inverse_fft(self->ifft, self->spectrum.data(), self->ifft_out.data());
    float scale = 1.0f / static_cast<float>(self->fft_size);
    for (gint i = 0; i < self->fft_size; ++i) {
        float val = self->ifft_out[i] * scale * self->window[i];
        self->ola_buffer[i] += val;
    }

    for (gint i = 0; i < self->hop_size; ++i) {
        out[i] = self->ola_buffer[i] / self->ola_norm[i];
    }
    memmove(self->ola_buffer.data(), self->ola_buffer.data() + self->hop_size, (self->fft_size - self->hop_size) * sizeof(float));
    memset(self->ola_buffer.data() + (self->fft_size - self->hop_size), 0, self->hop_size * sizeof(float));

    for (gint i = 0; i < 2; ++i) {
        self->ort->ReleaseValue(enc_inputs[i]);
    }
    for (gint i = 0; i < 7; ++i) {
        self->ort->ReleaseValue(enc_outputs[i]);
    }
    self->ort->ReleaseValue(erb_outputs[0]);
    for (gint i = 0; i < 2; ++i) {
        self->ort->ReleaseValue(df_outputs[i]);
    }

    return TRUE;
}

static GstFlowReturn dfn_process_frame(GstDeepFilterNet *self, GstBuffer *inbuf) {
    GstMapInfo map_in;
    if (!gst_buffer_map(inbuf, &map_in, GST_MAP_READ)) {
        gst_buffer_unref(inbuf);
        return GST_FLOW_ERROR;
    }

    GstBuffer *outbuf = gst_buffer_new_allocate(nullptr, self->frame_bytes, nullptr);
    GstMapInfo map_out;
    if (!gst_buffer_map(outbuf, &map_out, GST_MAP_WRITE)) {
        gst_buffer_unmap(inbuf, &map_in);
        gst_buffer_unref(inbuf);
        gst_buffer_unref(outbuf);
        return GST_FLOW_ERROR;
    }

    const float *in = reinterpret_cast<const float *>(map_in.data);
    float *out = reinterpret_cast<float *>(map_out.data);

    gboolean bypass = self->bypass;
    gint64 now_us = g_get_monotonic_time();
    if (self->cooldown_until > now_us) {
        bypass = TRUE;
    }

    double elapsed_ms = 0.0;
    gboolean ok = FALSE;
    if (!bypass) {
        auto start_us = g_get_monotonic_time();
        if (self->use_dfn3) {
            ok = dfn_run_dfn3(self, in, out);
        } else {
            ok = dfn_run_single(self, in, out);
        }
        auto end_us = g_get_monotonic_time();
        elapsed_ms = (end_us - start_us) / 1000.0;
    }

    if (!ok) {
        memcpy(out, in, self->frame_bytes);
        bypass = TRUE;
    }

    if (elapsed_ms > 8.0) {
        bypass = TRUE;
        self->consecutive_over += 1;
        if (self->consecutive_over >= 3) {
            self->cooldown_until = g_get_monotonic_time() + 2000000;
            self->consecutive_over = 0;
        }
    } else {
        self->consecutive_over = 0;
    }

    if (bypass) {
        memcpy(out, in, self->frame_bytes);
        self->bypass_count += 1;
    }

    for (guint i = 0; i < self->frame_samples; ++i) {
        if (out[i] > 0.98f) {
            out[i] = 0.98f;
        } else if (out[i] < -0.98f) {
            out[i] = -0.98f;
        }
    }

    if (elapsed_ms > 0.0) {
        self->frame_times.push_back(elapsed_ms);
        if (self->frame_times.size() > 200) {
            self->frame_times.erase(self->frame_times.begin());
        }
    }
    self->frame_counter += 1;
    if (self->frame_counter % 50 == 0) {
        self->p50_ms = percentile(self->frame_times, 50.0);
        self->p95_ms = percentile(self->frame_times, 95.0);
        GstStructure *s = gst_structure_new(
            "dfn-stats",
            "p50_ms",
            G_TYPE_DOUBLE,
            self->p50_ms,
            "p95_ms",
            G_TYPE_DOUBLE,
            self->p95_ms,
            "bypass_count",
            G_TYPE_UINT64,
            self->bypass_count,
            nullptr);
        gst_element_post_message(GST_ELEMENT(self), gst_message_new_element(GST_OBJECT(self), s));
    }

    GstClockTime pts = GST_BUFFER_PTS(inbuf);
    GstClockTime dur = GST_BUFFER_DURATION(inbuf);
    if (dur == GST_CLOCK_TIME_NONE) {
        dur = gst_util_uint64_scale_int(GST_SECOND, self->frame_samples, 48000);
    }
    GST_BUFFER_PTS(outbuf) = pts;
    GST_BUFFER_DURATION(outbuf) = dur;

    gst_buffer_unmap(inbuf, &map_in);
    gst_buffer_unref(inbuf);
    gst_buffer_unmap(outbuf, &map_out);

    return gst_pad_push(self->srcpad, outbuf);
}

static GstFlowReturn gst_deepfilternet_chain(GstPad *pad, GstObject *parent, GstBuffer *buffer) {
    GstDeepFilterNet *self = GST_DEEPFILTERNET(parent);
    gst_adapter_push(self->adapter, buffer);
    while (gst_adapter_available(self->adapter) >= self->frame_bytes) {
        GstBuffer *inbuf = gst_adapter_take_buffer(self->adapter, self->frame_bytes);
        GstFlowReturn ret = dfn_process_frame(self, inbuf);
        if (ret != GST_FLOW_OK) {
            return ret;
        }
    }
    return GST_FLOW_OK;
}

static void gst_deepfilternet_set_property(GObject *object, guint prop_id, const GValue *value, GParamSpec *pspec) {
    GstDeepFilterNet *self = GST_DEEPFILTERNET(object);
    switch (prop_id) {
        case PROP_MODEL_PATH:
            g_free(self->model_path);
            self->model_path = g_value_dup_string(value);
            break;
        case PROP_MODEL_DIR:
            g_free(self->model_dir);
            self->model_dir = g_value_dup_string(value);
            break;
        case PROP_BYPASS:
            self->bypass = g_value_get_boolean(value);
            break;
        case PROP_INPUT_NAME:
            g_free(self->input_name);
            self->input_name = g_value_dup_string(value);
            break;
        case PROP_OUTPUT_NAME:
            g_free(self->output_name);
            self->output_name = g_value_dup_string(value);
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
            break;
    }
}

static void gst_deepfilternet_get_property(GObject *object, guint prop_id, GValue *value, GParamSpec *pspec) {
    GstDeepFilterNet *self = GST_DEEPFILTERNET(object);
    switch (prop_id) {
        case PROP_MODEL_PATH:
            g_value_set_string(value, self->model_path);
            break;
        case PROP_MODEL_DIR:
            g_value_set_string(value, self->model_dir);
            break;
        case PROP_BYPASS:
            g_value_set_boolean(value, self->bypass);
            break;
        case PROP_INPUT_NAME:
            g_value_set_string(value, self->input_name);
            break;
        case PROP_OUTPUT_NAME:
            g_value_set_string(value, self->output_name);
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
            break;
    }
}

static void gst_deepfilternet_finalize(GObject *object) {
    GstDeepFilterNet *self = GST_DEEPFILTERNET(object);
    if (self->adapter) {
        g_object_unref(self->adapter);
        self->adapter = nullptr;
    }
    if (self->fft) {
        gst_fft_f32_free(self->fft);
        self->fft = nullptr;
    }
    if (self->ifft) {
        gst_fft_f32_free(self->ifft);
        self->ifft = nullptr;
    }
    dfn_release_sessions(self);
    g_free(self->model_path);
    g_free(self->model_dir);
    g_free(self->input_name);
    g_free(self->output_name);
    dfn_free_name_array(self->enc_input_names, 2);
    dfn_free_name_array(self->enc_output_names, 7);
    dfn_free_name_array(self->erb_input_names, 5);
    dfn_free_name_array(self->erb_output_names, 1);
    dfn_free_name_array(self->df_input_names, 2);
    dfn_free_name_array(self->df_output_names, 2);
    G_OBJECT_CLASS(gst_deepfilternet_parent_class)->finalize(object);
}

static GstStateChangeReturn gst_deepfilternet_change_state(GstElement *element, GstStateChange transition) {
    GstDeepFilterNet *self = GST_DEEPFILTERNET(element);
    if (transition == GST_STATE_CHANGE_READY_TO_PAUSED) {
        if (!dfn_init_session(self)) {
            self->bypass = TRUE;
        }
    }
    return GST_ELEMENT_CLASS(gst_deepfilternet_parent_class)->change_state(element, transition);
}

static void gst_deepfilternet_class_init(GstDeepFilterNetClass *klass) {
    GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
    gst_element_class_set_static_metadata(
        element_class,
        "DeepFilterNet",
        "Filter/Audio",
        "DeepFilterNet noise suppression",
        "TChat");

    gst_element_class_add_pad_template(element_class, gst_static_pad_template_get(&sink_template));
    gst_element_class_add_pad_template(element_class, gst_static_pad_template_get(&src_template));
    element_class->change_state = gst_deepfilternet_change_state;

    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    gobject_class->set_property = gst_deepfilternet_set_property;
    gobject_class->get_property = gst_deepfilternet_get_property;
    gobject_class->finalize = gst_deepfilternet_finalize;

    g_object_class_install_property(
        gobject_class,
        PROP_MODEL_PATH,
        g_param_spec_string("model-path", "Model Path", "Path to single DeepFilterNet ONNX model", nullptr, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_MODEL_DIR,
        g_param_spec_string("model-dir", "Model Dir", "Path to DeepFilterNet3 ONNX directory", nullptr, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_BYPASS,
        g_param_spec_boolean("bypass", "Bypass", "Bypass inference", FALSE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_INPUT_NAME,
        g_param_spec_string("input-name", "Input Name", "ONNX input name", nullptr, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_OUTPUT_NAME,
        g_param_spec_string("output-name", "Output Name", "ONNX output name", nullptr, GParamFlags(G_PARAM_READWRITE)));
}

static void gst_deepfilternet_init(GstDeepFilterNet *self) {
    self->frame_samples = 480;
    self->frame_bytes = self->frame_samples * sizeof(float);
    self->adapter = gst_adapter_new();
    self->bypass = FALSE;
    self->consecutive_over = 0;
    self->cooldown_until = 0;
    self->bypass_count = 0;
    self->frame_counter = 0;
    self->p50_ms = 0.0;
    self->p95_ms = 0.0;
    self->frame_times.reserve(200);
    self->model_path = nullptr;
    self->model_dir = nullptr;
    self->input_name = nullptr;
    self->output_name = nullptr;
    self->ort = nullptr;
    self->env = nullptr;
    self->session_opts = nullptr;
    self->single_session = nullptr;
    self->enc_session = nullptr;
    self->erb_session = nullptr;
    self->df_session = nullptr;
    self->mem_info = nullptr;
    self->use_dfn3 = FALSE;
    self->fft = nullptr;
    self->ifft = nullptr;
    dfn_apply_default_config(self);

    for (gint i = 0; i < 2; ++i) {
        self->enc_input_names[i] = nullptr;
        self->df_input_names[i] = nullptr;
        self->df_output_names[i] = nullptr;
    }
    for (gint i = 0; i < 7; ++i) {
        self->enc_output_names[i] = nullptr;
    }
    for (gint i = 0; i < 5; ++i) {
        self->erb_input_names[i] = nullptr;
    }
    self->erb_output_names[0] = nullptr;

    self->sinkpad = gst_pad_new_from_static_template(&sink_template, "sink");
    gst_pad_set_chain_function(self->sinkpad, GST_DEBUG_FUNCPTR(gst_deepfilternet_chain));
    gst_element_add_pad(GST_ELEMENT(self), self->sinkpad);

    self->srcpad = gst_pad_new_from_static_template(&src_template, "src");
    gst_element_add_pad(GST_ELEMENT(self), self->srcpad);
}

static gboolean plugin_init(GstPlugin *plugin) {
    return gst_element_register(plugin, "deepfilternet", GST_RANK_NONE, GST_TYPE_DEEPFILTERNET);
}

GST_PLUGIN_DEFINE(
    GST_VERSION_MAJOR,
    GST_VERSION_MINOR,
    deepfilternet,
    "DeepFilterNet ONNX plugin",
    plugin_init,
    "1.0",
    "LGPL",
    "tchat",
    "tchat")
