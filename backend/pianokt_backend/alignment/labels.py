"""Canonical note error codes, independent of the HTTP or exporter layers."""
import pandas as pd
TIMING_THRESHOLD_MS = 150

def classify_matched_note(
    row: pd.Series,
    ref_pitch_col: str,
    perf_pitch_col: str,
    timing_dev_col: str,
    timing_threshold_ms: float = TIMING_THRESHOLD_MS,
) -> int:
    """判断一条 match note 的 note-level 错误类型。"""
    # 保留两个独立的观察结果；两者同时为真时用专门的类别 4。
    pitch_error = False
    if pd.notna(row.get(ref_pitch_col)) and pd.notna(row.get(perf_pitch_col)):
        pitch_offset = int(round(row[perf_pitch_col] - row[ref_pitch_col]))
        if pitch_offset != 0:
            pitch_error = True

    # 时间错误。
    timing_error = False
    if pd.notna(row.get(timing_dev_col)):
        timing_ms = float(row[timing_dev_col]) * 1000
        if abs(timing_ms) > timing_threshold_ms:
            timing_error = True
    if pitch_error and timing_error:
        return 4
    if pitch_error:
        return 2
    return 3 if timing_error else 0

def combine_reject_reasons(reasons: list[int | None]) -> int:
    """Collapse target-note labels using the schema's fixed precedence."""
    values = {reason for reason in reasons if reason is not None}
    if 1 in values:
        return 1
    if 4 in values:
        return 4
    if 2 in values:
        return 2
    if 3 in values:
        return 3
    return 0

