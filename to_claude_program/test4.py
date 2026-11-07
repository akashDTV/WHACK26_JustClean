"""
test4.py - test inputs for to_claude: missing_data

Messy data (overall 70): holds unavailable, section 3 excluded, wrists unavailable, one very low-confidence finding that should be left out.

Run with:  python main.py test4
"""

PARAMS = {
    "scores": {
        "overall": 70,
        "position": 72,
        "timing": 58,
        "sharpness": 64,
        "holds": None,
        "scale": "0-100"
    },
    "sections": [
        {
            "section": 1,
            "start_s": 0.0,
            "end_s": 4.0,
            "overall": 76,
            "position": 78,
            "timing": 60,
            "sharpness": 68,
            "holds": None,
            "weakest_joint": "right_knee",
            "mean_angle_error_deg": 14,
            "confidence": 0.9
        },
        {
            "section": 2,
            "start_s": 4.0,
            "end_s": 8.0,
            "overall": 68,
            "position": 70,
            "timing": 55,
            "sharpness": 62,
            "holds": None,
            "weakest_joint": "left_elbow",
            "mean_angle_error_deg": 17,
            "confidence": 0.62
        },
        {
            "section": 4,
            "start_s": 12.0,
            "end_s": 16.0,
            "overall": 66,
            "position": 68,
            "timing": 58,
            "sharpness": 60,
            "holds": None,
            "weakest_joint": "right_hip",
            "mean_angle_error_deg": 18,
            "confidence": 0.8
        }
    ],
    "top_findings": [
        {
            "type": "position",
            "body_part": "right knee",
            "section": 1,
            "timestamp_s": 2.8,
            "measured_value": "14 degree mean difference from reference",
            "direction": "user knee less bent",
            "confidence": 0.9
        },
        {
            "type": "position",
            "body_part": "right hip",
            "section": 4,
            "timestamp_s": 13.5,
            "measured_value": "18 degree mean difference from reference",
            "confidence": 0.8
        },
        {
            "type": "position",
            "body_part": "left elbow",
            "section": 2,
            "timestamp_s": 6.2,
            "measured_value": "17 degree mean difference from reference",
            "confidence": 0.62
        },
        {
            "type": "timing",
            "body_part": "left arm",
            "section": 2,
            "timestamp_s": 7.0,
            "measured_value": "300 ms later than reference",
            "confidence": 0.35
        }
    ],
    "data_quality": {
        "tracked_frame_percentage": 71,
        "valid_sections": [
            1,
            2,
            4
        ],
        "excluded_sections": [
            3
        ],
        "timing_events_detected": 6,
        "hold_events_detected": 0
    }
}

COMPARISON = {
    "body_part_scores": {
        "right_knee": {
            "score": 72,
            "mean_angle_error_deg": 14,
            "confidence": 0.9,
            "tracked_samples": 98
        },
        "right_hip": {
            "score": 64,
            "mean_angle_error_deg": 18,
            "confidence": 0.8,
            "tracked_samples": 90
        },
        "left_elbow": {
            "score": 66,
            "mean_angle_error_deg": 17,
            "confidence": 0.62,
            "tracked_samples": 52
        },
        "left_wrist": {
            "score": None,
            "metric": "unavailable",
            "confidence": 0.0,
            "tracked_samples": 0
        },
        "right_wrist": {
            "score": None,
            "metric": "unavailable",
            "confidence": 0.0,
            "tracked_samples": 0
        }
    }
}

PERSONALITY = {
    "kindness": 0.7,
    "technicality": 0.5,
    "energy": 0.5,
    "directness": 0.5
}
