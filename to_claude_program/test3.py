"""
test3.py - test inputs for to_claude: timing_problem

Good positions, late timing (overall 79). Fixes should be about timing. Very technical, analytical personality.

Run with:  python main.py test3
"""

PARAMS = {
    "scores": {
        "overall": 79,
        "position": 88,
        "timing": 35,
        "sharpness": 60,
        "holds": 80,
        "scale": "0-100"
    },
    "sections": [
        {
            "section": 1,
            "start_s": 0.0,
            "end_s": 4.0,
            "overall": 84,
            "position": 90,
            "timing": 48,
            "sharpness": 66,
            "holds": 85,
            "weakest_joint": "left_knee",
            "mean_angle_error_deg": 11,
            "confidence": 0.95
        },
        {
            "section": 2,
            "start_s": 4.0,
            "end_s": 8.0,
            "overall": 76,
            "position": 88,
            "timing": 30,
            "sharpness": 58,
            "holds": 80,
            "weakest_joint": "left_elbow",
            "mean_angle_error_deg": 12,
            "confidence": 0.94
        },
        {
            "section": 3,
            "start_s": 8.0,
            "end_s": 12.0,
            "overall": 73,
            "position": 86,
            "timing": 28,
            "sharpness": 55,
            "holds": 76,
            "weakest_joint": "right_elbow",
            "mean_angle_error_deg": 14,
            "confidence": 0.93
        },
        {
            "section": 4,
            "start_s": 12.0,
            "end_s": 16.0,
            "overall": 82,
            "position": 89,
            "timing": 38,
            "sharpness": 62,
            "holds": 82,
            "weakest_joint": "right_hip",
            "mean_angle_error_deg": 11,
            "confidence": 0.95
        }
    ],
    "top_findings": [
        {
            "type": "timing",
            "body_part": "left arm",
            "section": 3,
            "timestamp_s": 9.4,
            "measured_value": "380 ms later than reference",
            "confidence": 0.93
        },
        {
            "type": "timing",
            "body_part": "right arm",
            "section": 2,
            "timestamp_s": 6.1,
            "measured_value": "350 ms later than reference",
            "confidence": 0.92
        },
        {
            "type": "sharpness",
            "body_part": "left arm",
            "section": 3,
            "timestamp_s": 9.4,
            "measured_value": "movement took 450 ms longer to complete",
            "confidence": 0.9
        }
    ],
    "data_quality": {
        "tracked_frame_percentage": 97,
        "valid_sections": [
            1,
            2,
            3,
            4
        ],
        "excluded_sections": [],
        "timing_events_detected": 13,
        "hold_events_detected": 5
    }
}

COMPARISON = {
    "body_part_scores": {
        "left_elbow": {
            "score": 84,
            "mean_angle_error_deg": 8,
            "confidence": 0.94,
            "tracked_samples": 123
        },
        "right_elbow": {
            "score": 82,
            "mean_angle_error_deg": 9,
            "confidence": 0.94,
            "tracked_samples": 123
        },
        "left_knee": {
            "score": 90,
            "mean_angle_error_deg": 5,
            "confidence": 0.95,
            "tracked_samples": 122
        },
        "right_knee": {
            "score": 91,
            "mean_angle_error_deg": 4,
            "confidence": 0.95,
            "tracked_samples": 122
        }
    }
}

PERSONALITY = {
    "kindness": 0.5,
    "technicality": 0.95,
    "energy": 0.3,
    "directness": 0.7,
    "description": "analytical choreography assistant"
}
