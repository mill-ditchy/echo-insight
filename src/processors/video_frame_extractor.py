"""通过密集扫描建立画面证据时间线，再选择少量关键帧供视觉模型分析。"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter, ImageStat
from fractions import Fraction
import math
import hashlib

from ..platforms.bilibili_subtitle import BilibiliSubtitleFetcher
from ..platforms.bilibili_video import BilibiliVideoDownloader
from .video_analysis_config import load_settings


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "vision.json"
FRAME_DIR = PROJECT_ROOT / "output" / "frames"
EXTRACTOR_VERSION = "4"
DEFAULT_CONFIG = {
    "enabled": True,
    "max_frames": 12,
    "max_scan_frames": 220,
    "min_scan_interval": 1.0,
    "max_scan_interval": 10.0,
    "max_width": 960,
    "jpeg_quality": 82,
    "cleanup_video": True,
}


PROFILE_CONFIG = {
    "tutorial_guide": {
        "interval": 2.0,
        "change_weight": 0.8,
        "text_weight": 1.05,
        "transition_pairs": 1,
        "text_seeds": 3,
    },
    "product_review": {
        "interval": 2.0,
        "change_weight": 0.9,
        "text_weight": 1.0,
        "transition_pairs": 2,
        "text_seeds": 3,
    },
    "knowledge_lesson": {
        "interval": 2.5,
        "change_weight": 0.75,
        "text_weight": 1.1,
        "transition_pairs": 1,
        "text_seeds": 3,
    },
    "technology_explainer": {
        "interval": 2.5,
        "change_weight": 0.8,
        "text_weight": 1.0,
        "transition_pairs": 1,
        "text_seeds": 3,
    },
    "news_analysis": {
        "interval": 2.0,
        "change_weight": 0.85,
        "text_weight": 1.0,
        "transition_pairs": 1,
        "text_seeds": 3,
    },
    "opinion_commentary": {
        "interval": 4.0,
        "change_weight": 0.65,
        "text_weight": 0.75,
        "transition_pairs": 1,
        "text_seeds": 2,
    },
    "fan_creation": {
        "interval": 1.25,
        "change_weight": 1.2,
        "text_weight": 0.55,
        "transition_pairs": 2,
        "text_seeds": 1,
    },
    "gameplay_entertainment": {
        "interval": 1.25,
        "change_weight": 1.2,
        "text_weight": 0.65,
        "transition_pairs": 2,
        "text_seeds": 2,
    },
    "entertainment": {
        "interval": 1.5,
        "change_weight": 1.1,
        "text_weight": 0.55,
        "transition_pairs": 2,
        "text_seeds": 1,
    },
    "educational_story": {
        "interval": 1.5,
        "change_weight": 1.0,
        "text_weight": 0.7,
        "transition_pairs": 2,
        "text_seeds": 2,
    },
    "vlog_experience": {
        "interval": 1.5,
        "change_weight": 1.1,
        "text_weight": 0.65,
        "transition_pairs": 2,
        "text_seeds": 1,
    },
    "music_performance": {
        "interval": 1.5,
        "change_weight": 1.1,
        "text_weight": 0.65,
        "transition_pairs": 2,
        "text_seeds": 2,
    },
    "commercial_recommendation": {
        "interval": 2.0,
        "change_weight": 0.8,
        "text_weight": 1.1,
        "transition_pairs": 1,
        "text_seeds": 3,
    },
    "general": {
        "interval": 2.0,
        "change_weight": 1.0,
        "text_weight": 0.75,
        "transition_pairs": 1,
        "text_seeds": 2,
    },
}


def _load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            config.update(saved)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return config


@dataclass
class VisualFrame:
    timestamp: float
    path: str
    reason: str = ""
    visual_score: float = 0.0
    text_score: float = 0.0


@dataclass
class VisualSegment:
    start: float
    end: float
    frames: list[VisualFrame]
    clip_path: str = ""


@dataclass
class FrameExtractionResult:
    bvid: str
    duration: float
    frames: list[VisualFrame]
    created_at: str
    extractor_version: str = EXTRACTOR_VERSION
    scan_interval: float = 0.0
    scanned_frame_count: int = 0
    selection_strategy: str = "general"
    segments: list[VisualSegment] = field(default_factory=list)

    @property
    def cache_dir(self) -> Path:
        return FRAME_DIR / self.bvid

    @property
    def manifest_path(self) -> Path:
        return self.cache_dir / "manifest.json"


@dataclass(eq=False)
class _Candidate:
    timestamp: float
    thumb: Image.Image = field(repr=False)
    change: float = 0.0
    local_peak: float = 0.0
    text_score: float = 0.0
    sharpness: float = 0.0
    blank_penalty: float = 0.0
    score: float = 0.0
    reasons: set[str] = field(default_factory=set)


class VideoFrameExtractor:
    """先密集扫描视觉证据，再按内容类型选择覆盖时间线的代表画面。"""

    def __init__(self) -> None:
        self.config = _load_config()
        self.analysis_settings = load_settings()

    def _config_hash(self) -> str:
        return hashlib.sha256(json.dumps({"frames": self.config, "analysis": self.analysis_settings}, sort_keys=True).encode()).hexdigest()

    def _load_cache(
        self,
        bvid: str,
        strategy: str,
    ) -> FrameExtractionResult | None:
        manifest = FRAME_DIR / bvid / "manifest.json"
        if not manifest.exists():
            return None
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if data.get("extractor_version") != EXTRACTOR_VERSION:
                return None
            if data.get("config_hash") != self._config_hash():
                return None
            if data.get("selection_strategy", "general") != strategy:
                return None
            if int(data.get("max_frames", 0)) != self._max_frames():
                return None
            frames = [VisualFrame(**item) for item in data.get("frames", [])]
            if not frames or not all(Path(frame.path).exists() for frame in frames):
                return None
            segments = [VisualSegment(start=item["start"], end=item["end"],
                frames=[VisualFrame(**frame) for frame in item["frames"]],
                clip_path=item.get("clip_path", "")) for item in data.get("segments", [])]
            for segment in segments:
                if not segment.frames or not all(Path(f.path).exists() for f in segment.frames):
                    return None
                if segment.clip_path and not Path(segment.clip_path).exists():
                    return None
            cached_paths = [f.path for f in frames] + [f.path for s in segments for f in s.frames] + [s.clip_path for s in segments if s.clip_path]
            if any(not Path(path).resolve().is_relative_to(manifest.parent.resolve()) for path in cached_paths):
                return None
            return FrameExtractionResult(
                bvid=data["bvid"],
                duration=float(data.get("duration", 0)),
                frames=frames,
                created_at=data.get("created_at", ""),
                extractor_version=data.get("extractor_version", ""),
                scan_interval=float(data.get("scan_interval", 0)),
                scanned_frame_count=int(data.get("scanned_frame_count", 0)),
                selection_strategy=data.get("selection_strategy", "general"),
                segments=segments,
            )
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _max_frames(self) -> int:
        return max(6, min(16, int(self.config.get("max_frames", 12))))

    @staticmethod
    def _duration(container, stream) -> float:
        import av

        if container.duration:
            return max(0.0, float(container.duration / av.time_base))
        if stream.duration is not None and stream.time_base is not None:
            return max(0.0, float(stream.duration * stream.time_base))
        return 0.0

    @staticmethod
    def _read_frame(container, stream, timestamp: float):
        import av

        container.seek(
            max(0, int(timestamp * av.time_base)),
            stream=None,
            any_frame=False,
            backward=True,
        )
        best = None
        for index, frame in enumerate(container.decode(video=stream.index)):
            best = frame
            frame_time = float(frame.time or 0)
            if frame_time >= timestamp - 0.12 or index >= 300:
                break
        return best.to_image().convert("RGB") if best is not None else None

    @staticmethod
    def _difference(previous: Image.Image | None, current: Image.Image) -> float:
        if previous is None:
            return 255.0
        difference = ImageChops.difference(previous, current)
        return sum(ImageStat.Stat(difference).mean) / 3

    @staticmethod
    def _percentile(values: list[float], ratio: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = round((len(ordered) - 1) * ratio)
        return ordered[index]

    @staticmethod
    def _visual_features(image: Image.Image) -> tuple[Image.Image, float, float, float]:
        thumb = image.resize((160, 90), Image.Resampling.BILINEAR).convert("RGB")
        gray = image.resize((192, 108), Image.Resampling.BILINEAR).convert("L")
        stats = ImageStat.Stat(gray)
        brightness = stats.mean[0]
        contrast = stats.stddev[0]
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_histogram = edges.histogram()
        edge_pixels = sum(edge_histogram[56:])
        edge_density = edge_pixels / max(1, gray.width * gray.height)

        pixels = edges.load()
        text_like_rows = 0
        for y in range(3, gray.height - 3):
            strong = sum(1 for x in range(3, gray.width - 3) if pixels[x, y] >= 56)
            density = strong / max(1, gray.width - 6)
            if 0.06 <= density <= 0.55:
                text_like_rows += 1
        row_density = text_like_rows / max(1, gray.height - 6)

        # 这里只判断“可能存在可读文字的高对比细节”，并不冒充 OCR 结果。
        # 后续还会在同一视频内部按相对分数筛选，避免复杂纹理全部得满分。
        text_score = min(
            1.0,
            max(0.0, (edge_density - 0.06) / 0.28) * 0.55
            + row_density * 0.25
            + min(contrast / 90, 1) * 0.2,
        )
        sharpness = min(1.0, ImageStat.Stat(edges).mean[0] / 38)
        blank_penalty = 0.0
        if brightness < 12 or brightness > 246 or contrast < 5:
            blank_penalty = 1.0
        elif brightness < 22 or brightness > 238 or contrast < 10:
            blank_penalty = 0.5
        return thumb, text_score, sharpness, blank_penalty

    def _scan_interval(self, duration: float, profile: dict) -> float:
        base = float(profile["interval"])
        if duration <= 90:
            base = min(base, 1.0)
        elif duration > 1200:
            base = max(base, 6.0)
        elif duration > 600:
            base = max(base, 4.0)
        elif duration > 240:
            base = max(base, 2.5)

        max_scan_frames = max(40, int(self.config.get("max_scan_frames", 220)))
        minimum = max(0.5, float(self.config.get("min_scan_interval", 1.0)))
        maximum = max(minimum, float(self.config.get("max_scan_interval", 10.0)))
        normal_interval = min(maximum, max(minimum, base))
        return max(normal_interval, duration / max(1, max_scan_frames - 1))

    def _scan_candidates(
        self,
        container,
        stream,
        duration: float,
        interval: float,
        profile: dict,
    ) -> list[_Candidate]:
        if duration <= 0:
            raise RuntimeError("无法读取视频时长")

        edge = min(0.5, duration * 0.01)
        final_target = max(edge, duration - edge)
        targets = []
        timestamp = edge
        while timestamp < final_target:
            targets.append(timestamp)
            timestamp += interval
        targets.append(final_target)

        candidates = []
        target_index = 0
        previous_thumb = None
        stream.thread_type = "AUTO"
        for frame in container.decode(video=stream.index):
            if target_index >= len(targets):
                break
            frame_time = float(frame.time or 0)
            if frame_time + 0.03 < targets[target_index]:
                continue
            image = frame.to_image().convert("RGB")
            thumb, text_score, sharpness, blank_penalty = self._visual_features(image)
            change = self._difference(previous_thumb, thumb)
            candidates.append(
                _Candidate(
                    timestamp=frame_time,
                    thumb=thumb,
                    change=change,
                    text_score=text_score,
                    sharpness=sharpness,
                    blank_penalty=blank_penalty,
                )
            )
            previous_thumb = thumb
            while target_index < len(targets) and targets[target_index] <= frame_time + 0.03:
                target_index += 1

        if not candidates:
            raise RuntimeError("视频解码成功，但没有读取到可用画面")

        changes = [candidate.change for candidate in candidates[1:]]
        change_floor = max(4.0, self._percentile(changes, 0.7))
        text_floor = self._percentile(
            [candidate.text_score for candidate in candidates],
            0.78,
        )
        for index, candidate in enumerate(candidates):
            previous_change = candidates[index - 1].change if index else 0.0
            next_change = candidates[index + 1].change if index + 1 < len(candidates) else 0.0
            candidate.local_peak = candidate.change / max(1.0, previous_change, next_change)
            normalized_change = min(1.0, candidate.change / max(12.0, change_floor * 1.5))
            candidate.score = (
                normalized_change * float(profile["change_weight"])
                + candidate.text_score * float(profile["text_weight"])
                + candidate.sharpness * 0.25
                - candidate.blank_penalty * 1.5
            )
            if candidate.change >= change_floor and candidate.local_peak >= 0.85:
                candidate.reasons.add("scene_change")
            if candidate.text_score >= max(0.38, text_floor):
                candidate.reasons.add("text_candidate")
            if candidate.sharpness >= 0.55:
                candidate.reasons.add("clear_frame")
        return candidates

    def _select_evidence(
        self,
        candidates: list[_Candidate],
        duration: float,
        profile: dict,
    ) -> list[_Candidate]:
        max_frames = min(self._max_frames(), len(candidates))
        if len(candidates) <= max_frames:
            for candidate in candidates:
                candidate.reasons.add("timeline_coverage")
            return candidates

        selected: list[_Candidate] = []

        def add(candidate: _Candidate, reason: str, force: bool = False) -> bool:
            if candidate in selected or len(selected) >= max_frames:
                return False
            if candidate.blank_penalty >= 1.0 and not force:
                return False
            candidate.reasons.add(reason)
            selected.append(candidate)
            return True

        usable = [item for item in candidates if item.blank_penalty < 1.0] or candidates
        add(usable[0], "timeline_start", force=True)
        add(usable[-1], "timeline_end", force=True)

        scene_indices = [
            index
            for index, item in enumerate(candidates)
            if "scene_change" in item.reasons and index > 0
        ]
        scene_indices.sort(key=lambda index: candidates[index].change, reverse=True)
        chosen_scene_indices = []
        transition_pairs = int(profile["transition_pairs"])
        event_gap = duration / max(4, transition_pairs * 3)
        for index in scene_indices:
            if len(chosen_scene_indices) >= transition_pairs:
                break
            if all(
                abs(candidates[index].timestamp - candidates[chosen].timestamp)
                >= event_gap
                for chosen in chosen_scene_indices
            ):
                chosen_scene_indices.append(index)
        for index in chosen_scene_indices:
            add(candidates[index - 1], "before_transition")
            add(candidates[index], "after_transition")

        text_candidates = sorted(
            usable,
            key=lambda item: (item.text_score, item.sharpness),
            reverse=True,
        )
        text_gap = max(2.0, duration / max(4, max_frames))
        seeded_text = 0
        for candidate in text_candidates:
            if seeded_text >= int(profile["text_seeds"]):
                break
            if all(abs(candidate.timestamp - item.timestamp) >= text_gap for item in selected):
                if add(candidate, "text_candidate"):
                    seeded_text += 1

        minimum_frames = min(max_frames, 6)
        maximum_gap = duration / max(1, max_frames - 1) * 1.75
        while len(selected) < max_frames:
            ordered = sorted(selected, key=lambda item: item.timestamp)
            gaps = [
                (right.timestamp - left.timestamp, left.timestamp, right.timestamp)
                for left, right in zip(ordered, ordered[1:])
            ]
            if not gaps:
                break
            gap, left_time, right_time = max(gaps)
            if gap <= maximum_gap:
                break
            middle = [
                item
                for item in usable
                if item not in selected and left_time < item.timestamp < right_time
            ]
            if not middle:
                break
            midpoint = (left_time + right_time) / 2
            best_coverage = max(
                middle,
                key=lambda item: item.score
                - abs(item.timestamp - midpoint) / max(1.0, gap) * 0.7,
            )
            add(best_coverage, "timeline_gap")

        while len(selected) < max_frames:
            best = None
            best_rank = float("-inf")
            for candidate in usable:
                if candidate in selected:
                    continue
                nearest_time = min(
                    abs(candidate.timestamp - item.timestamp) for item in selected
                )
                coverage = min(1.0, nearest_time / max(1.0, duration / max_frames))
                nearest_difference = min(
                    self._difference(candidate.thumb, item.thumb) for item in selected
                )
                novelty = min(1.0, nearest_difference / 22.0)
                if len(selected) >= minimum_frames and novelty < 0.08:
                    continue
                rank = candidate.score + coverage * 0.85 + novelty * 0.4
                if rank > best_rank:
                    best = candidate
                    best_rank = rank
            if best is None:
                break
            add(best, "timeline_coverage")

        return sorted(selected, key=lambda item: item.timestamp)

    def _select_candidates(
        self,
        container,
        stream,
        duration: float,
        strategy: str,
    ) -> tuple[list[tuple[_Candidate, Image.Image]], float, int]:
        profile = PROFILE_CONFIG.get(strategy, PROFILE_CONFIG["general"])
        interval = self._scan_interval(duration, profile)
        candidates = self._scan_candidates(container, stream, duration, interval, profile)
        self._timeline = candidates
        selected = self._select_evidence(candidates, duration, profile)

        images = []
        for candidate in selected:
            image = self._read_frame(container, stream, candidate.timestamp)
            if image is not None:
                images.append((candidate, image))
        if not images:
            raise RuntimeError("已找到关键时间点，但无法读取对应的高清画面")
        return images, interval, len(candidates)

    def _segment_candidates(self, duration: float) -> list[tuple[float, float, list[_Candidate]]]:
        """Cover the entire timeline under a fixed frame/request budget."""
        if not self.analysis_settings["enabled"]:
            return []
        count = min(self.analysis_settings["max_segments"], max(1, math.ceil(duration / self.analysis_settings["segment_seconds"])))
        span = duration / count
        boundaries = [0.0]
        cuts = [c.timestamp for c in self._timeline if "scene_change" in c.reasons]
        for index in range(1, count):
            target = index * span
            nearby = [t for t in cuts if abs(t - target) <= span * .18 and t > boundaries[-1] + span * .5]
            boundaries.append(min(nearby, key=lambda t: abs(t - target)) if nearby else target)
        boundaries.append(duration)
        groups = []
        for index in range(count):
            start, end = boundaries[index], boundaries[index + 1]
            pool = [c for c in self._timeline if start <= c.timestamp < end or (index == count - 1 and c.timestamp <= end and c.timestamp >= start)]
            if not pool:
                continue
            budget = min(len(pool), self.analysis_settings["frames_per_segment"])
            # Ordered coverage first, then use nearby cut/text evidence without leaving holes.
            chosen = []
            for slot in range(budget):
                target = start + (slot + .5) * (end - start) / budget
                available = [c for c in pool if c not in chosen]
                best = min(available, key=lambda c: abs(c.timestamp - target) - min(.35, c.score * .12))
                chosen.append(best)
            groups.append((round(start, 2), round(end, 2), sorted(chosen, key=lambda c: c.timestamp)))
        return groups

    @staticmethod
    def _encode_clip(segment: VisualSegment, path: Path) -> None:
        """Variable-time sampled visual clip; audio is supplied separately as transcript."""
        import av
        with Image.open(segment.frames[0].path) as first:
            width, height = first.size
        width, height = width // 2 * 2, height // 2 * 2
        temp = path.with_suffix(".tmp.mp4")
        try:
            with av.open(str(temp), "w") as output:
                stream = output.add_stream("libx264", rate=2)
                stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
                stream.options = {"preset": "veryfast", "crf": "26"}
                for item in segment.frames:
                    with Image.open(item.path) as image:
                        frame = av.VideoFrame.from_image(image.convert("RGB").resize((width, height)))
                    frame.pts = round((item.timestamp - segment.start) * 1000)
                    frame.time_base = Fraction(1, 1000)
                    for packet in stream.encode(frame):
                        output.mux(packet)
                for packet in stream.encode():
                    output.mux(packet)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def extract(
        self,
        video_url: str,
        summary_plan: dict | None = None,
    ) -> FrameExtractionResult:
        if not self.config.get("enabled", True):
            raise RuntimeError("视频视觉分析已关闭")

        strategy = (summary_plan or {}).get("primary_strategy", "general")
        if strategy not in PROFILE_CONFIG:
            strategy = "general"
        bvid = BilibiliSubtitleFetcher.extract_bvid(video_url)
        if not bvid:
            raise ValueError(f"无法从视频地址提取BVID: {video_url}")
        cached = self._load_cache(bvid, strategy)
        if cached:
            return cached

        downloaded = BilibiliVideoDownloader().download(video_url)
        completed = False
        try:
            import av

            with av.open(str(downloaded.path)) as container:
                stream = container.streams.video[0]
                duration = self._duration(container, stream)
                selected, scan_interval, scanned_count = self._select_candidates(
                    container,
                    stream,
                    duration,
                    strategy,
                )

            cache_dir = FRAME_DIR / bvid
            cache_dir.mkdir(parents=True, exist_ok=True)
            frames = []
            max_width = max(320, int(self.config.get("max_width", 960)))
            quality = max(60, min(95, int(self.config.get("jpeg_quality", 82))))
            for index, (candidate, image) in enumerate(selected, start=1):
                if image.width > max_width:
                    height = round(image.height * max_width / image.width)
                    image = image.resize(
                        (max_width, height),
                        Image.Resampling.LANCZOS,
                    )
                frame_path = cache_dir / (
                    f"v{EXTRACTOR_VERSION}_{index:02d}_{round(candidate.timestamp):06d}s.jpg"
                )
                image.save(frame_path, "JPEG", quality=quality, optimize=True)
                frames.append(
                    VisualFrame(
                        timestamp=round(candidate.timestamp, 2),
                        path=str(frame_path),
                        reason="+".join(sorted(candidate.reasons)),
                        visual_score=round(candidate.score, 3),
                        text_score=round(candidate.text_score, 3),
                    )
                )

            segments = []
            groups = self._segment_candidates(duration)
            if groups:
                with av.open(str(downloaded.path)) as container:
                    stream = container.streams.video[0]
                    for segment_index, (start, end, candidates) in enumerate(groups):
                        segment_frames = []
                        for frame_index, candidate in enumerate(candidates):
                            image = self._read_frame(container, stream, candidate.timestamp)
                            if image is None:
                                continue
                            if image.width > max_width:
                                image = image.resize((max_width, round(image.height * max_width / image.width)), Image.Resampling.LANCZOS)
                            path = cache_dir / f"v{EXTRACTOR_VERSION}_segment{segment_index:02d}_{frame_index:02d}.jpg"
                            image.save(path, "JPEG", quality=quality, optimize=True)
                            segment_frames.append(VisualFrame(round(candidate.timestamp, 2), str(path), "+".join(sorted(candidate.reasons)), round(candidate.score, 3), round(candidate.text_score, 3)))
                        if not segment_frames:
                            raise RuntimeError("视频片段缺少可读取的画面")
                        segment = VisualSegment(start, end, segment_frames)
                        if self.analysis_settings["input_mode"] == "video":
                            clip = cache_dir / f"v{EXTRACTOR_VERSION}_segment{segment_index:02d}.mp4"
                            self._encode_clip(segment, clip)
                            segment.clip_path = str(clip)
                        segments.append(segment)

            result = FrameExtractionResult(
                bvid=bvid,
                duration=round(duration, 2),
                frames=frames,
                created_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                scan_interval=round(scan_interval, 2),
                scanned_frame_count=scanned_count,
                selection_strategy=strategy,
                segments=segments,
            )
            temp_path = result.manifest_path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(
                    {
                        "bvid": result.bvid,
                        "duration": result.duration,
                        "extractor_version": result.extractor_version,
                        "config_hash": self._config_hash(),
                        "max_frames": self._max_frames(),
                        "scan_interval": result.scan_interval,
                        "scanned_frame_count": result.scanned_frame_count,
                        "selected_frame_count": len(result.frames),
                        "selection_strategy": result.selection_strategy,
                        "frames": [asdict(frame) for frame in result.frames],
                        "segments": [asdict(segment) for segment in result.segments],
                        "created_at": result.created_at,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temp_path.replace(result.manifest_path)
            completed = True
            return result
        except Exception as exc:
            if isinstance(exc, RuntimeError):
                raise
            raise RuntimeError(f"视频关键帧提取失败: {exc}") from exc
        finally:
            if completed and self.config.get("cleanup_video", True):
                downloaded.path.unlink(missing_ok=True)
