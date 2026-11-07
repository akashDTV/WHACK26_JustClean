"""
test5.py - test inputs for to_claude: no_reliable_findings

Nothing reliable (overall 66): every finding is below the confidence cutoff, so priority_fixes should be empty.

Run with:  python main.py test5
"""

PARAMS = {
    "scores": {
        "overall": 66,
        "position": 66,
        "timing": None,
        "sharpness": None,
        "holds": None,
        "scale": "0-100"
    },
    "sections": [
        {
            "section": 1,
            "start_s": 0.0,
            "end_s": 4.0,
            "overall": 66,
            "position": 66,
            "timing": None,
            "sharpness": None,
            "holds": None,
            "weakest_joint": "left_knee",
            "mean_angle_error_deg": 17,
            "confidence": 0.45
        }
    ],
    "top_findings": [
        {
            "type": "position",
            "body_part": "left knee",
            "section": 1,
            "timestamp_s": 3.1,
            "measured_value": "17 degree mean difference from reference",
            "confidence": 0.45
        },
        {
            "type": "position",
            "body_part": "right elbow",
            "section": 1,
            "timestamp_s": 1.9,
            "measured_value": "15 degree mean difference from reference",
            "confidence": 0.4
        }
    ],
    "data_quality": {
        "tracked_frame_percentage": 58,
        "valid_sections": [
            1
        ],
        "excluded_sections": [
            2,
            3,
            4
        ],
        "timing_events_detected": 0,
        "hold_events_detected": 0
    }
}

COMPARISON = {
    "body_part_scores": {
        "left_knee": {
            "score": 66,
            "mean_angle_error_deg": 17,
            "confidence": 0.45,
            "tracked_samples": 40
        },
        "right_elbow": {
            "score": 70,
            "mean_angle_error_deg": 15,
            "confidence": 0.4,
            "tracked_samples": 36
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
    "kindness": 0.6,
    "technicality": 0.4,
    "energy": 0.6,
    "directness": 0.5
}
