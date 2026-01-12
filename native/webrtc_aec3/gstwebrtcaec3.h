#pragma once

#include <gst/base/gstadapter.h>
#include <gst/gst.h>

G_BEGIN_DECLS

#define GST_TYPE_WEBRTC_AEC3 (gst_webrtc_aec3_get_type())
G_DECLARE_FINAL_TYPE(GstWebRtcAec3, gst_webrtc_aec3, GST, WEBRTC_AEC3, GstElement)

G_END_DECLS
