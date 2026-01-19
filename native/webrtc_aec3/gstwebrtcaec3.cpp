#include "gstwebrtcaec3.h"

#include <gst/audio/audio.h>
#include <gst/base/gstadapter.h>
#include <gst/gst.h>

#include <api/audio/audio_processing.h>
#include <api/audio/builtin_audio_processing_builder.h>
#include <api/environment/environment_factory.h>
#include <api/scoped_refptr.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <vector>
#include <absl/types/optional.h>

#ifndef PACKAGE
#define PACKAGE "tchat"
#endif

struct WebRtcAec3Private;

struct _GstWebRtcAec3 {
    GstElement parent;
    GstPad *capture_sinkpad;
    GstPad *render_sinkpad;
    GstPad *srcpad;
    GstAdapter *capture_adapter;
    GstAdapter *render_adapter;
    guint frame_bytes;
    guint frame_samples;
    gint sample_rate;
    gboolean bypass;
    gint stream_delay_ms;
    gboolean auto_delay;
    gint estimated_delay_ms;
    gint max_delay_ms;
    gint delay_step_ms;
    gint delay_update_frames;
    gint delay_frame_count;
    guint stats_interval_frames;
    guint stats_frame_count;
    float delay_smoothing;
    float delay_corr_threshold;
    std::vector<float> render_ring;
    size_t render_ring_pos;
    guint64 render_samples_seen;
    gboolean agc_enabled;
    gboolean agc_input_volume;
    float agc_headroom_db;
    float agc_max_gain_db;
    float agc_initial_gain_db;
    float agc_max_noise_dbfs;
    gboolean hpf_enabled;
    GMutex lock;
    WebRtcAec3Private *priv;
};

struct WebRtcAec3Private {
    webrtc::scoped_refptr<webrtc::AudioProcessing> apm;
    std::vector<float> render_scratch;
    
    WebRtcAec3Private() : apm(nullptr), render_scratch() {}
};

G_DEFINE_TYPE(GstWebRtcAec3, gst_webrtc_aec3, GST_TYPE_ELEMENT)

enum {
    PROP_0,
    PROP_BYPASS,
    PROP_STREAM_DELAY_MS,
    PROP_AUTO_DELAY,
    PROP_AGC_ENABLED,
    PROP_AGC_INPUT_VOLUME,
    PROP_AGC_HEADROOM_DB,
    PROP_AGC_MAX_GAIN_DB,
    PROP_AGC_INITIAL_GAIN_DB,
    PROP_AGC_MAX_NOISE_DBFS,
    PROP_HPF_ENABLED,
    PROP_ESTIMATED_DELAY_MS,
};

static GstStaticPadTemplate capture_sink_template = GST_STATIC_PAD_TEMPLATE(
    "sink",
    GST_PAD_SINK,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-raw,format=F32LE,rate=[8000,96000],channels=1,layout=interleaved"));

static GstStaticPadTemplate render_sink_template = GST_STATIC_PAD_TEMPLATE(
    "render_sink",
    GST_PAD_SINK,
    GST_PAD_REQUEST,
    GST_STATIC_CAPS("audio/x-raw,format=F32LE,rate=[8000,96000],channels=1,layout=interleaved"));

static GstStaticPadTemplate src_template = GST_STATIC_PAD_TEMPLATE(
    "src",
    GST_PAD_SRC,
    GST_PAD_ALWAYS,
    GST_STATIC_CAPS("audio/x-raw,format=F32LE,rate=[8000,96000],channels=1,layout=interleaved"));

static GstStateChangeReturn gst_webrtc_aec3_change_state(GstElement *element, GstStateChange transition);

static webrtc::AudioProcessing::Config gst_webrtc_aec3_build_config(GstWebRtcAec3 *self) {
    webrtc::AudioProcessing::Config cfg;
    cfg.echo_canceller.enabled = true;
    cfg.gain_controller2.enabled = self->agc_enabled;
    cfg.gain_controller2.adaptive_digital.enabled = self->agc_enabled;
    cfg.gain_controller2.input_volume_controller.enabled = self->agc_enabled && self->agc_input_volume;
    cfg.gain_controller2.adaptive_digital.headroom_db = self->agc_headroom_db;
    cfg.gain_controller2.adaptive_digital.max_gain_db = self->agc_max_gain_db;
    cfg.gain_controller2.adaptive_digital.initial_gain_db = self->agc_initial_gain_db;
    cfg.gain_controller2.adaptive_digital.max_output_noise_level_dbfs = self->agc_max_noise_dbfs;
    cfg.high_pass_filter.enabled = self->hpf_enabled;
    return cfg;
}

static gboolean gst_webrtc_aec3_ensure_apm(GstWebRtcAec3 *self) {
    if (self->bypass) {
        return FALSE;
    }
    g_mutex_lock(&self->lock);
    if (!self->priv->apm) {
        webrtc::BuiltinAudioProcessingBuilder builder;
        builder.SetConfig(gst_webrtc_aec3_build_config(self));
        self->priv->apm = builder.Build(webrtc::CreateEnvironment());
        if (self->priv->apm) {
            self->priv->apm->Initialize();
        }
    }
    g_mutex_unlock(&self->lock);
    return self->priv->apm != nullptr;
}

static void gst_webrtc_aec3_apply_config(GstWebRtcAec3 *self) {
    if (!self->priv->apm) {
        return;
    }
    g_mutex_lock(&self->lock);
    self->priv->apm->ApplyConfig(gst_webrtc_aec3_build_config(self));
    g_mutex_unlock(&self->lock);
}

static void gst_webrtc_aec3_update_rate(GstWebRtcAec3 *self, gint rate) {
    if (rate <= 0 || rate == self->sample_rate) {
        return;
    }
    g_mutex_lock(&self->lock);
    self->sample_rate = rate;
    self->frame_samples = static_cast<guint>(std::max(1, rate / 100));
    self->frame_bytes = self->frame_samples * sizeof(float);
    self->render_samples_seen = 0;
    self->render_ring_pos = 0;
    const int samples_per_ms = std::max(1, rate / 1000);
    self->render_ring.assign(static_cast<size_t>(self->max_delay_ms * samples_per_ms + self->frame_samples), 0.0f);
    self->priv->render_scratch.assign(self->frame_samples, 0.0f);
    gst_adapter_clear(self->capture_adapter);
    gst_adapter_clear(self->render_adapter);
    if (self->priv->apm) {
        self->priv->apm->Initialize();
    }
    g_mutex_unlock(&self->lock);
}

static gboolean gst_webrtc_aec3_stats_value(const absl::optional<float> &value, float *out) {
    if (!value.has_value()) {
        return FALSE;
    }
    *out = value.value();
    return TRUE;
}

static gboolean gst_webrtc_aec3_stats_value(const absl::optional<double> &value, float *out) {
    if (!value.has_value()) {
        return FALSE;
    }
    *out = static_cast<float>(value.value());
    return TRUE;
}

static gboolean gst_webrtc_aec3_stats_value(float value, float *out) {
    *out = value;
    return TRUE;
}

static gboolean gst_webrtc_aec3_stats_value(double value, float *out) {
    *out = static_cast<float>(value);
    return TRUE;
}

static void gst_webrtc_aec3_post_stats(GstWebRtcAec3 *self,
                                       gboolean have_erle,
                                       float erle_db,
                                       gboolean have_erl,
                                       float erl_db,
                                       gint estimated_delay_ms,
                                       gint stream_delay_ms) {
    GstStructure *s = gst_structure_new("aec3-stats",
                                        "estimated_delay_ms", G_TYPE_INT, estimated_delay_ms,
                                        "stream_delay_ms", G_TYPE_INT, stream_delay_ms,
                                        nullptr);
    if (have_erle) {
        gst_structure_set(s, "erle_db", G_TYPE_FLOAT, erle_db, nullptr);
    }
    if (have_erl) {
        gst_structure_set(s, "erl_db", G_TYPE_FLOAT, erl_db, nullptr);
    }
    gst_element_post_message(GST_ELEMENT(self), gst_message_new_element(GST_OBJECT(self), s));
}

static GstFlowReturn gst_webrtc_aec3_process_capture(GstWebRtcAec3 *self, GstBuffer *inbuf) {
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

    gst_webrtc_aec3_ensure_apm(self);

    gboolean post_stats = FALSE;
    gboolean have_erle = FALSE;
    gboolean have_erl = FALSE;
    float erle_db = 0.0f;
    float erl_db = 0.0f;
    gint stats_est_delay_ms = 0;
    gint stats_stream_delay_ms = 0;

    if (self->priv->apm && !self->bypass) {
        webrtc::StreamConfig cfg(self->sample_rate, 1);
        const float *in_ptr[1] = {in};
        float *out_ptr[1] = {out};
        g_mutex_lock(&self->lock);
        if (self->auto_delay && self->render_samples_seen >= self->frame_samples) {
            self->delay_frame_count += 1;
            if (self->delay_frame_count >= self->delay_update_frames) {
                self->delay_frame_count = 0;
                float cap_energy = 0.0f;
                for (guint i = 0; i < self->frame_samples; ++i) {
                    cap_energy += in[i] * in[i];
                }
                if (cap_energy > 1e-6f && !self->render_ring.empty()) {
                    const int sample_rate = self->sample_rate;
                    int max_delay_samples = static_cast<int>(self->render_ring.size()) - static_cast<int>(self->frame_samples);
                    if (max_delay_samples > 0) {
                        const int configured_max = (self->max_delay_ms * sample_rate) / 1000;
                        if (max_delay_samples > configured_max) {
                            max_delay_samples = configured_max;
                        }
                        const int step_samples = (self->delay_step_ms * sample_rate) / 1000;
                        float best_corr = 0.0f;
                        int best_delay_ms = self->estimated_delay_ms;
                        for (int delay_samples = 0; delay_samples <= max_delay_samples; delay_samples += step_samples) {
                            float render_energy = 0.0f;
                            float dot = 0.0f;
                            for (guint i = 0; i < self->frame_samples; ++i) {
                                const size_t idx = (self->render_ring_pos + self->render_ring.size() - delay_samples - self->frame_samples + i) % self->render_ring.size();
                                float r = self->render_ring[idx];
                                render_energy += r * r;
                                dot += r * in[i];
                            }
                            if (render_energy < 1e-6f) {
                                continue;
                            }
                            float denom = std::sqrt(render_energy * cap_energy) + 1e-6f;
                            float corr = dot / denom;
                            if (corr > best_corr) {
                                best_corr = corr;
                                best_delay_ms = (delay_samples * 1000) / sample_rate;
                            }
                        }
                        if (best_corr > self->delay_corr_threshold) {
                            int target = best_delay_ms;
                            int smoothed = static_cast<int>(std::round(self->delay_smoothing * self->estimated_delay_ms + (1.0f - self->delay_smoothing) * target));
                            if (std::abs(smoothed - self->estimated_delay_ms) >= 5) {
                                self->estimated_delay_ms = smoothed;
                            }
                            self->stream_delay_ms = self->estimated_delay_ms;
                        }
                    }
                }
            }
        }
        self->priv->apm->set_stream_delay_ms(self->stream_delay_ms);
        self->priv->apm->ProcessStream(in_ptr, cfg, cfg, out_ptr);
        if (self->stats_interval_frames > 0) {
            self->stats_frame_count += 1;
            if (self->stats_frame_count >= self->stats_interval_frames) {
                self->stats_frame_count = 0;
                auto stats = self->priv->apm->GetStatistics();
                have_erle = gst_webrtc_aec3_stats_value(stats.echo_return_loss_enhancement, &erle_db);
                have_erl = gst_webrtc_aec3_stats_value(stats.echo_return_loss, &erl_db);
                stats_est_delay_ms = self->estimated_delay_ms;
                stats_stream_delay_ms = self->stream_delay_ms;
                post_stats = TRUE;
            }
        }
        g_mutex_unlock(&self->lock);
    } else {
        memcpy(out, in, self->frame_bytes);
    }

    if (post_stats) {
        gst_webrtc_aec3_post_stats(self, have_erle, erle_db, have_erl, erl_db, stats_est_delay_ms, stats_stream_delay_ms);
    }

    GstClockTime pts = GST_BUFFER_PTS(inbuf);
    GstClockTime dur = GST_BUFFER_DURATION(inbuf);

    gst_buffer_unmap(inbuf, &map_in);
    gst_buffer_unref(inbuf);
    gst_buffer_unmap(outbuf, &map_out);
    if (dur == GST_CLOCK_TIME_NONE) {
        dur = gst_util_uint64_scale_int(GST_SECOND, self->frame_samples, self->sample_rate);
    }
    GST_BUFFER_PTS(outbuf) = pts;
    GST_BUFFER_DURATION(outbuf) = dur;

    return gst_pad_push(self->srcpad, outbuf);
}

static GstFlowReturn gst_webrtc_aec3_chain_capture(GstPad *pad, GstObject *parent, GstBuffer *buffer) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(parent);
    gst_adapter_push(self->capture_adapter, buffer);
    while (gst_adapter_available(self->capture_adapter) >= self->frame_bytes) {
        GstBuffer *inbuf = gst_adapter_take_buffer(self->capture_adapter, self->frame_bytes);
        GstFlowReturn ret = gst_webrtc_aec3_process_capture(self, inbuf);
        if (ret != GST_FLOW_OK) {
            return ret;
        }
    }
    return GST_FLOW_OK;
}

static gboolean gst_webrtc_aec3_sink_event(GstPad *pad, GstObject *parent, GstEvent *event) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(parent);
    if (GST_EVENT_TYPE(event) == GST_EVENT_CAPS) {
        GstCaps *caps = nullptr;
        gst_event_parse_caps(event, &caps);
        if (caps) {
            const GstStructure *s = gst_caps_get_structure(caps, 0);
            gint rate = 0;
            if (gst_structure_get_int(s, "rate", &rate) && rate > 0) {
                gst_webrtc_aec3_update_rate(self, rate);
            }
        }
    }
    GstEvent *forward = gst_event_ref(event);
    gboolean ret = gst_pad_event_default(pad, parent, event);
    if (ret && self->srcpad) {
        gst_pad_push_event(self->srcpad, forward);
    } else {
        gst_event_unref(forward);
    }
    return ret;
}

static GstFlowReturn gst_webrtc_aec3_chain_render(GstPad *pad, GstObject *parent, GstBuffer *buffer) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(parent);
    gst_adapter_push(self->render_adapter, buffer);
    gst_webrtc_aec3_ensure_apm(self);
    while (gst_adapter_available(self->render_adapter) >= self->frame_bytes) {
        GstBuffer *inbuf = gst_adapter_take_buffer(self->render_adapter, self->frame_bytes);
        GstMapInfo map_in;
        if (!gst_buffer_map(inbuf, &map_in, GST_MAP_READ)) {
            gst_buffer_unref(inbuf);
            continue;
        }
        const float *in = reinterpret_cast<const float *>(map_in.data);
        float *tmp = self->priv->render_scratch.data();
        g_mutex_lock(&self->lock);
        if (!self->render_ring.empty()) {
            for (guint i = 0; i < self->frame_samples; ++i) {
                self->render_ring[self->render_ring_pos] = in[i];
                self->render_ring_pos = (self->render_ring_pos + 1) % self->render_ring.size();
            }
            self->render_samples_seen += self->frame_samples;
        }
        if (self->priv->apm && !self->bypass) {
            webrtc::StreamConfig cfg(self->sample_rate, 1);
            const float *in_ptr[1] = {in};
            float *out_ptr[1] = {tmp};
            self->priv->apm->ProcessReverseStream(in_ptr, cfg, cfg, out_ptr);
        }
        g_mutex_unlock(&self->lock);
        gst_buffer_unmap(inbuf, &map_in);
        gst_buffer_unref(inbuf);
    }
    return GST_FLOW_OK;
}

static GstPad *gst_webrtc_aec3_request_new_pad(GstElement *element, GstPadTemplate *templ, const gchar *name, const GstCaps *caps) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(element);
    if (self->render_sinkpad) {
        return nullptr;
    }
    self->render_sinkpad = gst_pad_new_from_template(templ, name ? name : "render_sink");
    gst_pad_set_chain_function(self->render_sinkpad, GST_DEBUG_FUNCPTR(gst_webrtc_aec3_chain_render));
    gst_pad_set_event_function(self->render_sinkpad, GST_DEBUG_FUNCPTR(gst_webrtc_aec3_sink_event));
    gst_element_add_pad(GST_ELEMENT(self), self->render_sinkpad);
    return self->render_sinkpad;
}

static void gst_webrtc_aec3_release_pad(GstElement *element, GstPad *pad) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(element);
    if (pad == self->render_sinkpad) {
        gst_element_remove_pad(GST_ELEMENT(self), pad);
        self->render_sinkpad = nullptr;
    }
}

static void gst_webrtc_aec3_finalize(GObject *object) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(object);
    if (self->capture_adapter) {
        g_object_unref(self->capture_adapter);
        self->capture_adapter = nullptr;
    }
    if (self->render_adapter) {
        g_object_unref(self->render_adapter);
        self->render_adapter = nullptr;
    }
    if (self->priv) {
        delete self->priv;
        self->priv = nullptr;
    }
    g_mutex_clear(&self->lock);
    G_OBJECT_CLASS(gst_webrtc_aec3_parent_class)->finalize(object);
}

static void gst_webrtc_aec3_set_property(GObject *object, guint prop_id, const GValue *value, GParamSpec *pspec) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(object);
    switch (prop_id) {
        case PROP_BYPASS:
            self->bypass = g_value_get_boolean(value);
            if (!self->bypass) {
                gst_webrtc_aec3_ensure_apm(self);
            }
            break;
        case PROP_STREAM_DELAY_MS:
            self->stream_delay_ms = g_value_get_int(value);
            if (self->stream_delay_ms < 0) {
                self->stream_delay_ms = 0;
            }
            self->estimated_delay_ms = self->stream_delay_ms;
            break;
        case PROP_AUTO_DELAY:
            self->auto_delay = g_value_get_boolean(value);
            break;
        case PROP_AGC_ENABLED:
            self->agc_enabled = g_value_get_boolean(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_AGC_INPUT_VOLUME:
            self->agc_input_volume = g_value_get_boolean(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_AGC_HEADROOM_DB:
            self->agc_headroom_db = g_value_get_float(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_AGC_MAX_GAIN_DB:
            self->agc_max_gain_db = g_value_get_float(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_AGC_INITIAL_GAIN_DB:
            self->agc_initial_gain_db = g_value_get_float(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_AGC_MAX_NOISE_DBFS:
            self->agc_max_noise_dbfs = g_value_get_float(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        case PROP_HPF_ENABLED:
            self->hpf_enabled = g_value_get_boolean(value);
            gst_webrtc_aec3_apply_config(self);
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
            break;
    }
}

static void gst_webrtc_aec3_get_property(GObject *object, guint prop_id, GValue *value, GParamSpec *pspec) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(object);
    switch (prop_id) {
        case PROP_BYPASS:
            g_value_set_boolean(value, self->bypass);
            break;
        case PROP_STREAM_DELAY_MS:
            g_value_set_int(value, self->stream_delay_ms);
            break;
        case PROP_AUTO_DELAY:
            g_value_set_boolean(value, self->auto_delay);
            break;
        case PROP_AGC_ENABLED:
            g_value_set_boolean(value, self->agc_enabled);
            break;
        case PROP_AGC_INPUT_VOLUME:
            g_value_set_boolean(value, self->agc_input_volume);
            break;
        case PROP_AGC_HEADROOM_DB:
            g_value_set_float(value, self->agc_headroom_db);
            break;
        case PROP_AGC_MAX_GAIN_DB:
            g_value_set_float(value, self->agc_max_gain_db);
            break;
        case PROP_AGC_INITIAL_GAIN_DB:
            g_value_set_float(value, self->agc_initial_gain_db);
            break;
        case PROP_AGC_MAX_NOISE_DBFS:
            g_value_set_float(value, self->agc_max_noise_dbfs);
            break;
        case PROP_HPF_ENABLED:
            g_value_set_boolean(value, self->hpf_enabled);
            break;
        case PROP_ESTIMATED_DELAY_MS:
            g_value_set_int(value, self->estimated_delay_ms);
            break;
        default:
            G_OBJECT_WARN_INVALID_PROPERTY_ID(object, prop_id, pspec);
            break;
    }
}

static void gst_webrtc_aec3_class_init(GstWebRtcAec3Class *klass) {
    GstElementClass *element_class = GST_ELEMENT_CLASS(klass);
    gst_element_class_set_static_metadata(
        element_class,
        "WebRTC AEC3",
        "Filter/Audio",
        "AEC3 using WebRTC AudioProcessing",
        "TChat");

    gst_element_class_add_pad_template(element_class, gst_static_pad_template_get(&capture_sink_template));
    gst_element_class_add_pad_template(element_class, gst_static_pad_template_get(&render_sink_template));
    gst_element_class_add_pad_template(element_class, gst_static_pad_template_get(&src_template));

    element_class->request_new_pad = gst_webrtc_aec3_request_new_pad;
    element_class->release_pad = gst_webrtc_aec3_release_pad;
    element_class->change_state = gst_webrtc_aec3_change_state;

    GObjectClass *gobject_class = G_OBJECT_CLASS(klass);
    gobject_class->set_property = gst_webrtc_aec3_set_property;
    gobject_class->get_property = gst_webrtc_aec3_get_property;
    gobject_class->finalize = gst_webrtc_aec3_finalize;

    g_object_class_install_property(
        gobject_class,
        PROP_BYPASS,
        g_param_spec_boolean("bypass", "Bypass", "Bypass AEC processing", FALSE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_STREAM_DELAY_MS,
        g_param_spec_int("stream-delay-ms", "Stream Delay (ms)", "AEC stream delay in milliseconds", 0, 500, 0, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AUTO_DELAY,
        g_param_spec_boolean("auto-delay", "Auto Delay", "Enable automatic delay estimation", TRUE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_ENABLED,
        g_param_spec_boolean("agc", "AGC", "Enable WebRTC AGC (gain control)", TRUE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_INPUT_VOLUME,
        g_param_spec_boolean("agc-input-volume", "AGC Input Volume", "Enable AGC input volume controller", FALSE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_HEADROOM_DB,
        g_param_spec_float("agc-headroom-db", "AGC Headroom (dB)", "AGC2 headroom in dB", 0.0f, 20.0f, 5.0f, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_MAX_GAIN_DB,
        g_param_spec_float("agc-max-gain-db", "AGC Max Gain (dB)", "AGC2 max gain in dB", 0.0f, 80.0f, 50.0f, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_INITIAL_GAIN_DB,
        g_param_spec_float("agc-initial-gain-db", "AGC Initial Gain (dB)", "AGC2 initial gain in dB", 0.0f, 30.0f, 15.0f, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_AGC_MAX_NOISE_DBFS,
        g_param_spec_float("agc-max-noise-dbfs", "AGC Max Noise (dBFS)", "AGC2 max output noise level", -100.0f, -20.0f, -50.0f, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_HPF_ENABLED,
        g_param_spec_boolean("hpf", "High Pass Filter", "Enable WebRTC high-pass filter", TRUE, GParamFlags(G_PARAM_READWRITE)));
    g_object_class_install_property(
        gobject_class,
        PROP_ESTIMATED_DELAY_MS,
        g_param_spec_int("estimated-delay-ms", "Estimated Delay (ms)", "Estimated AEC delay in milliseconds", 0, 500, 0, GParamFlags(G_PARAM_READABLE)));
}

static GstStateChangeReturn gst_webrtc_aec3_change_state(GstElement *element, GstStateChange transition) {
    GstWebRtcAec3 *self = GST_WEBRTC_AEC3(element);
    switch (transition) {
        case GST_STATE_CHANGE_READY_TO_PAUSED:
            gst_webrtc_aec3_ensure_apm(self);
            break;
        case GST_STATE_CHANGE_PAUSED_TO_READY:
            g_mutex_lock(&self->lock);
            self->render_samples_seen = 0;
            self->render_ring_pos = 0;
            std::fill(self->render_ring.begin(), self->render_ring.end(), 0.0f);
            self->delay_frame_count = 0;
            self->stats_frame_count = 0;
            self->estimated_delay_ms = self->stream_delay_ms;
            if (self->priv->apm) {
                self->priv->apm->Initialize();
            }
            g_mutex_unlock(&self->lock);
            break;
        default:
            break;
    }
    return GST_ELEMENT_CLASS(gst_webrtc_aec3_parent_class)->change_state(element, transition);
}

static void gst_webrtc_aec3_init(GstWebRtcAec3 *self) {
    self->frame_samples = 480;
    self->frame_bytes = self->frame_samples * sizeof(float);
    self->sample_rate = 48000;
    self->capture_adapter = gst_adapter_new();
    self->render_adapter = gst_adapter_new();
    self->bypass = FALSE;
    self->stream_delay_ms = 0;
    self->auto_delay = TRUE;
    self->estimated_delay_ms = 0;
    self->max_delay_ms = 500;
    self->delay_step_ms = 10;
    self->delay_update_frames = 10;
    self->delay_frame_count = 0;
    self->stats_interval_frames = 50;
    self->stats_frame_count = 0;
    self->delay_smoothing = 0.9f;
    self->delay_corr_threshold = 0.45f;
    self->agc_enabled = TRUE;
    self->agc_input_volume = FALSE;
    self->agc_headroom_db = 5.0f;
    self->agc_max_gain_db = 50.0f;
    self->agc_initial_gain_db = 15.0f;
    self->agc_max_noise_dbfs = -50.0f;
    self->hpf_enabled = TRUE;
    const int samples_per_ms = self->sample_rate / 1000;
    self->render_ring.resize(static_cast<size_t>(self->max_delay_ms * samples_per_ms + self->frame_samples));
    self->render_ring_pos = 0;
    self->render_samples_seen = 0;
    
    self->priv = new WebRtcAec3Private();
    self->priv->render_scratch.resize(self->frame_samples);
    
    g_mutex_init(&self->lock);

    self->capture_sinkpad = gst_pad_new_from_static_template(&capture_sink_template, "sink");
    gst_pad_set_chain_function(self->capture_sinkpad, GST_DEBUG_FUNCPTR(gst_webrtc_aec3_chain_capture));
    gst_pad_set_event_function(self->capture_sinkpad, GST_DEBUG_FUNCPTR(gst_webrtc_aec3_sink_event));
    gst_element_add_pad(GST_ELEMENT(self), self->capture_sinkpad);

    self->srcpad = gst_pad_new_from_static_template(&src_template, "src");
    gst_element_add_pad(GST_ELEMENT(self), self->srcpad);
}

static gboolean plugin_init(GstPlugin *plugin) {
    return gst_element_register(plugin, "webrtcaec3", GST_RANK_NONE, GST_TYPE_WEBRTC_AEC3);
}

GST_PLUGIN_DEFINE(
    GST_VERSION_MAJOR,
    GST_VERSION_MINOR,
    webrtcaec3,
    "WebRTC AEC3 plugin",
    plugin_init,
    "1.0",
    "LGPL",
    "tchat",
    "tchat")
