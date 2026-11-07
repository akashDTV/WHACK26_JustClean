"""
test1.py - test inputs for to_claude: strong_dancer

Strong run (overall 92). Few small findings, so Claude should praise and not invent problems. Very kind, high-energy personality.

Run with:  python main.py test1
"""

PARAMS = {
    "scores": {
        "overall": 92,
        "position": 94,
        "timing": 88,
        "sharpness": 85,
        "holds": 90,
        "scale": "0-100"
    },
    "sections": [
        {
            "section": 1,
            "start_s": 0.0,
            "end_s": 4.0,
            "overall": 95,
            "position": 96,
            "timing": 92,
            "sharpness": 90,
            "holds": 94,
            "weakest_joint": "right_knee",
            "mean_angle_error_deg": 6,
            "confidence": 0.96
        },
        {
            "section": 2,
            "start_s": 4.0,
            "end_s": 8.0,
            "overall": 93,
            "position": 95,
            "timing": 88,
            "sharpness": 86,
            "holds": 90,
            "weakest_joint": "left_elbow",
            "mean_angle_error_deg": 7,
            "confidence": 0.95
        },
        {
            "section": 3,
            "start_s": 8.0,
            "end_s": 12.0,
            "overall": 90,
            "position": 92,
            "timing": 85,
            "sharpness": 82,
            "holds": 88,
            "weakest_joint": "right_hip",
            "mean_angle_error_deg": 9,
            "confidence": 0.95
        },
        {
            "section": 4,
            "start_s": 12.0,
            "end_s": 16.0,
            "overall": 91,
            "position": 93,
            "timing": 87,
            "sharpness": 84,
            "holds": 89,
            "weakest_joint": "left_shoulder",
            "mean_angle_error_deg": 8,
            "confidence": 0.96
        }
    ],
    "top_findings": [
        {
            "type": "timing",
            "body_part": "right arm",
            "section": 3,
            "timestamp_s": 9.6,
            "measured_value": "120 ms later than reference",
            "confidence": 0.93
        },
        {
            "type": "position",
            "body_part": "right hip",
            "section": 3,
            "timestamp_s": 10.2,
            "measured_value": "9 degree mean difference from reference",
            "direction": "user hip less open",
            "confidence": 0.95
        }
    ],
    "data_quality": {
        "tracked_frame_percentage": 98,
        "valid_sections": [
            1,
            2,
            3,
            4
        ],
        "excluded_sections": [],
        "timing_events_detected": 14,
        "hold_events_detected": 6
    }
}

COMPARISON = {
    "body_part_scores": {
        "left_elbow": {
            "score": 93,
            "mean_angle_error_deg": 4,
            "confidence": 0.96,
            "tracked_samples": 124
        },
        "right_elbow": {
            "score": 95,
            "mean_angle_error_deg": 5,
            "confidence": 0.96,
            "tracked_samples": 124
        },
        "left_knee": {
            "score": 96,
            "mean_angle_error_deg": 3,
            "confidence": 0.95,
            "tracked_samples": 122
        },
        "right_knee": {
            "score": 94,
            "mean_angle_error_deg": 6,
            "confidence": 0.95,
            "tracked_samples": 122
        },
        "right_hip": {
            "score": 82,
            "mean_angle_error_deg": 9,
            "confidence": 0.95,
            "tracked_samples": 121
        },
        "left_wrist": {
            "score": None,
            "metric": "unavailable",
            "confidence": 0.0,
            "tracked_samples": 0
        }
    }
}

PERSONALITY = {
    "kindness": 0.9,
    "technicality": 0.3,
    "energy": 0.9,
    "directness": 0.4
}
