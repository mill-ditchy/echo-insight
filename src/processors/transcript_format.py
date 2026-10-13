"""Format transcript time windows for all media summaries."""

def format_transcript_for_summary(transcript, window_seconds: int = 300) -> str:
    """按时间窗口整理字幕，让模型明确覆盖视频的开头、中段和结尾。"""
    if not transcript.segments:
        return transcript.full_text

    windows: list[tuple[int, list[str]]] = []
    for segment in transcript.segments:
        window_start = int(segment.start // window_seconds) * window_seconds
        if not windows or windows[-1][0] != window_start:
            windows.append((window_start, []))
        windows[-1][1].append(segment.text)

    def clock(seconds: int) -> str:
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    sections = []
    for window_start, texts in windows:
        window_end = window_start + window_seconds
        sections.append(
            f"[{clock(window_start)}-{clock(window_end)}]\n" + " ".join(texts)
        )
    return "\n\n".join(sections)

