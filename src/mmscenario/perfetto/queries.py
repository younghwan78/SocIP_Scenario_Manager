"""SQL query constants for Perfetto trace detection.

All queries use only process/thread tables + android_logs.
Slice names are deliberately avoided (unreliable across vendors/Android versions).
"""

# Exynos logcat tags
TAG_HWC = "HWC"
TAG_CAMERA_HAL = "ExynosCameraHAL"
TAG_ISP = "ExynosISP"
TAG_CODEC = "MediaCodec"

# P1: Active processes/threads → SW layer classification
SQL_ACTIVE_PROCESSES = """
SELECT DISTINCT p.name
FROM process p
JOIN thread t ON t.upid = p.upid
WHERE p.name IS NOT NULL
ORDER BY p.name;
"""

# P2: GPU composition vs HW overlay (Exynos HWC logcat)
SQL_HWC_COMPOSITION = f"""
SELECT msg FROM android_logs
WHERE tag = '{TAG_HWC}'
LIMIT 200;
"""

# P3: NPU activity via process table
SQL_NPU_ACTIVE = """
SELECT COUNT(*) as cnt FROM process
WHERE LOWER(name) LIKE '%npu%';
"""

# P4: ISP configuration — single vs dual (CameraHAL logcat)
SQL_ISP_CONFIG = f"""
SELECT msg FROM android_logs
WHERE tag LIKE '%{TAG_ISP}%' OR tag LIKE '%{TAG_CAMERA_HAL}%'
LIMIT 300;
"""

# P5: Codec type and direction (MediaCodec logcat)
SQL_CODEC_TYPE = f"""
SELECT msg FROM android_logs
WHERE tag = '{TAG_CODEC}'
LIMIT 500;
"""

# Known process-name → SW layer classification heuristics
SW_LAYER_PATTERNS: dict[str, str] = {
    "camera": "app",
    "youtube": "app",
    "mediaplayer": "app",
    "cameraserver": "framework",
    "mediaserver": "framework",
    "surfaceflinger": "framework",
    "camerahal": "hal_kernel",
    "vendor.camera": "hal_kernel",
    "vendor.media": "hal_kernel",
    "v4l2": "hal_kernel",
}

# ISP dual-configuration hint keywords in logcat messages
ISP_DUAL_HINTS: list[str] = ["dual", "isp_1", "isp1", "sensor_1", "multi_cam"]
