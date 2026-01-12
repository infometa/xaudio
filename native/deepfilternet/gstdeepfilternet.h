#pragma once

#include <gst/base/gstadapter.h>
#include <gst/gst.h>

G_BEGIN_DECLS

#define GST_TYPE_DEEPFILTERNET (gst_deepfilternet_get_type())
G_DECLARE_FINAL_TYPE(GstDeepFilterNet, gst_deepfilternet, GST, DEEPFILTERNET, GstElement)

G_END_DECLS
