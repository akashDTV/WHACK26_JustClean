"""
test2.py - test inputs for to_claude: struggling_beginner

Struggling beginner (overall 42). Four findings, so Claude has to pick the top 3. Personality given as free text (blunt drill sergeant).

Run with:  python main.py test2
"""

PARAMS = {
    "scores": {
        "overall": 42,
        "position": 45,
        "timing": 30,
        "sharpness": 40,
        "holds": 35,
        "scale": "0-100"
    },
    "sections": [
        {
            "section": 1,
            "start_s": 0.0,
            "end_s": 4.0,
            "overall": 55,
            "position": 58,
            "timing": 40,
            "sharpness": 50,
            "holds": 45,
            "weakest_joint": "left_elbow",
            "mean_angle_error_deg": 21,
            "confidence": 0.92
        },
        {
            "section": 2,
            "start_s": 4.0,
            "end_s": 8.0,
            "overall": 38,
            "position": 41,
            "timing": 28,
            "sharpness": 36,
            "holds": 30,
            "weakest_joint": "right_knee",
            "mean_angle_error_deg": 30,
            "confidence": 0.9
        },
        {
            "section": 3,
            "start_s": 8.0,
            "end_s": 12.0,
            "overall": 33,
            "position": 36,
            "timing": 22,
            "sharpness": 30,
            "holds": 28,
            "weakest_joint": "left_shoulder",
            "mean_angle_error_deg": 32,
            "confidence": 0.91
        },
        {
            "section": 4,
            "start_s": 12.0,
            "end_s": 16.0,
            "overall": 42,
            "position": 46,
            "timing": 30,
            "sharpness": 42,
            "holds": 38,
            "weakest_joint": "right_hip",
            "mean_angle_error_deg": 27,
            "confidence": 0.9
        }
    ],
    "top_findings": [
        {
            "type": "position",
            "body_part": "left shoulder",
            "section": 3,
            "timestamp_s": 9.0,
            "measured_value": "32 degree mean difference from reference",
            "direction": "user shoulder lower",
            "confidence": 0.91
        },
        {
            "type": "position",
            "body_part": "right knee",
            "section": 2,
            "timestamp_s": 5.5,
            "measured_value": "30 degree mean difference from reference",
            "direction": "user knee more bent",
            "confidence": 0.9
        },
        {
            "type": "timing",
            "body_part": "right arm",
            "section": 2,
            "timestamp_s": 6.8,
            "measured_value": "430 ms later than reference",
            "confidence": 0.88
        },
        {
            "type": "holds",
            "body_part": "whole body",
            "section": 4,
            "timestamp_s": 14.4,
            "measured_value": "hold ended 600 ms earlier than reference",
            "confidence": 0.85
        }
    ],
    "data_quality": {
        "tracked_frame_percentage": 94,
        "valid_sections": [
            1,
            2,
            3,
            4
        ],
        "excluded_sections": [],
        "timing_events_detected": 11,
        "hold_events_detected": 4
    }
}

COMPARISON = {
    "body_part_scores": {
        "left_elbow": {
            "score": 58,
            "mean_angle_error_deg": 21,
            "confidence": 0.92,
            "tracked_samples": 120
        },
        "right_elbow": {
            "score": 52,
            "mean_angle_error_deg": 24,
            "confidence": 0.92,
            "tracked_samples": 120
        },
        "left_shoulder": {
            "score": 36,
            "mean_angle_error_deg": 32,
            "confidence": 0.91,
            "tracked_samples": 118
        },
        "right_knee": {
            "score": 40,
            "mean_angle_error_deg": 30,
            "confidence": 0.9,
            "tracked_samples": 118
        },
        "right_hip": {
            "score": 46,
            "mean_angle_error_deg": 27,
            "confidence": 0.9,
            "tracked_samples": 117
        }
    }
}

PERSONALITY = "blunt, no-nonsense drill sergeant who respects effort but hates excuses"
