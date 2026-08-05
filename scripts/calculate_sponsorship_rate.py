#!/usr/bin/env python3
"""
Calculate rolling average views and sponsorship rate for a YouTube channel.

(n.b. this script was vibecoded with Claude Fable)

Uses the YouTube Data API v3 to:

  1. Fetch all videos uploaded in the last LOOKBACK_DAYS days.
  2. Only match long-form videos (duration >= LONG_FORM_MIN_SECONDS).
  3. Compute the mean and median of *current* view counts for:
       - V30: last 30 days
       - V90: last 90 days
  4. Compute the sponsorship rate as ((avg views / 1000) x CPM_USD).

Usage:
    YOUTUBE_API_KEY=xxxx python3 scripts/calculate_sponsorship_rate.py
    YOUTUBE_API_KEY=xxxx python3 scripts/calculate_sponsorship_rate.py --update-readme README.md

For --update-readme, the README must contain these markers:

    <!-- SPONSORSHIP-RATE-START -->
    <!-- SPONSORSHIP-RATE-END -->

Everything between the markers is replaced on each run.
"""

import argparse
import json
import os
import re
import statistics
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API_BASE = "https://www.googleapis.com/youtube/v3"
CHANNEL_HANDLE = "@JeffGeerling"
CPM_USD = 15.00
LOOKBACK_DAYS = 90
LONG_FORM_MIN_SECONDS = 180  # Videos > 3 min

START_MARKER = "<!-- SPONSORSHIP-RATE-START -->"
END_MARKER = "<!-- SPONSORSHIP-RATE-END -->"

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def api_get(endpoint: str, params: dict) -> dict:
    """Call a YouTube Data API endpoint and return parsed JSON."""
    params = dict(params)
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    url = f"{API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def parse_duration(iso8601: str) -> int:
    """Convert an ISO 8601 duration (e.g. PT1H2M3S) to seconds."""
    match = _DURATION_RE.match(iso8601 or "")
    if not match:
        return 0
    parts = {k: int(v) for k, v in match.groupdict().items() if v}
    return (
        parts.get("days", 0) * 86400
        + parts.get("hours", 0) * 3600
        + parts.get("minutes", 0) * 60
        + parts.get("seconds", 0)
    )


def get_uploads_playlist_id(handle: str) -> str:
    data = api_get("channels", {"part": "contentDetails", "forHandle": handle})
    items = data.get("items", [])
    if not items:
        sys.exit(f"Error: no channel found for handle {handle}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def get_recent_video_ids(playlist_id: str, cutoff: datetime) -> list:
    """Page through the uploads playlist, returning IDs published after cutoff."""
    video_ids = []
    page_token = None
    while True:
        params = {
            "part": "contentDetails",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        data = api_get("playlistItems", params)

        stop = False
        for item in data.get("items", []):
            details = item["contentDetails"]
            published = datetime.fromisoformat(
                details["videoPublishedAt"].replace("Z", "+00:00")
            )
            if published < cutoff:
                # Uploads playlist is newest-first; everything after this
                # is older. (One page of buffer below guards against the
                # occasional out-of-order item, e.g. rescheduled premieres.)
                stop = True
                continue
            video_ids.append(details["videoId"])
        if stop or "nextPageToken" not in data:
            break
        page_token = data["nextPageToken"]
    return video_ids


def get_video_details(video_ids: list) -> list:
    """Fetch snippet/duration/stats for a list of video IDs (batched by 50)."""
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        data = api_get(
            "videos",
            {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(batch),
                "maxResults": 50,
            },
        )
        for item in data.get("items", []):
            snippet = item["snippet"]
            videos.append(
                {
                    "id": item["id"],
                    "title": snippet["title"],
                    "published_at": datetime.fromisoformat(
                        snippet["publishedAt"].replace("Z", "+00:00")
                    ),
                    "duration_seconds": parse_duration(
                        item["contentDetails"]["duration"]
                    ),
                    "views": int(item["statistics"].get("viewCount", 0)),
                    "live": snippet.get("liveBroadcastContent", "none") != "none",
                }
            )
    return videos


def cohort_stats(videos: list, days: int, now: datetime) -> dict:
    cutoff = now - timedelta(days=days)
    cohort = [v for v in videos if v["published_at"] >= cutoff]
    views = [v["views"] for v in cohort]
    return {
        "days": days,
        "count": len(cohort),
        "mean": statistics.mean(views) if views else 0,
        "median": statistics.median(views) if views else 0,
        "videos": sorted(cohort, key=lambda v: v["published_at"], reverse=True),
    }


def fmt_views(n: float) -> str:
    return f"{round(n):,}"


def fmt_views_short(n: float) -> str:
    return f"{round(n / 1000)}K" if n >= 1000 else str(round(n))


def sponsorship_rate(avg_views: float) -> float:
    """CPM rate, rounded to the nearest $10."""
    return round((avg_views / 1000.0) * CPM_USD / 10) * 10


def build_readme_block(v30: dict, v90: dict, now: datetime) -> str:
    rate = sponsorship_rate(v90["mean"])
    return (
        f"{START_MARKER}\n"
        f"| Metric | Value |\n"
        f"| --- | --- |\n"
        f"| 90-day average views (V90) | **{fmt_views(v90['mean'])}** "
        f"(median {fmt_views(v90['median'])}, {v90['count']} long-form videos) |\n"
        f"| 30-day average views (V30) | {fmt_views(v30['mean'])} "
        f"(median {fmt_views(v30['median'])}, {v30['count']} long-form videos) |\n"
        f"| Sponsorship rate (${CPM_USD:.2f} CPM × V90) | "
        f"**${rate:,.0f}** |\n"
        f"\n"
        f"_Rate calculation: {fmt_views_short(v90['mean'])} average views ÷ 1,000 "
        f"× ${CPM_USD:.2f} CPM ≈ ${rate:,.0f}. "
        f"Updated automatically on {now:%Y-%m-%d}._\n"
        f"{END_MARKER}"
    )


def update_readme(path: str, block: str) -> bool:
    """Replace the marker block in the README. Returns True if file changed."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    if START_MARKER not in content or END_MARKER not in content:
        sys.exit(
            f"Error: {path} is missing the markers.\n"
            f"Add these lines inside the '## Sponsors' section:\n\n"
            f"{START_MARKER}\n{END_MARKER}"
        )
    pattern = re.compile(
        re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.DOTALL
    )
    new_content = pattern.sub(lambda _: block, content, count=1)
    if new_content == content:
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update-readme",
        metavar="README_PATH",
        help="Update the marker block in this README file.",
    )
    parser.add_argument(
        "--list-videos",
        action="store_true",
        help="Also print each video in the 90-day cohort.",
    )
    args = parser.parse_args()

    if "YOUTUBE_API_KEY" not in os.environ:
        sys.exit("Error: set the YOUTUBE_API_KEY environment variable.")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=LOOKBACK_DAYS)

    playlist_id = get_uploads_playlist_id(CHANNEL_HANDLE)
    video_ids = get_recent_video_ids(playlist_id, cutoff)
    videos = get_video_details(video_ids)

    long_form = [
        v
        for v in videos
        if v["duration_seconds"] >= LONG_FORM_MIN_SECONDS and not v["live"]
    ]

    v30 = cohort_stats(long_form, 30, now)
    v90 = cohort_stats(long_form, 90, now)
    rate = sponsorship_rate(v90["mean"])

    print(f"Channel: {CHANNEL_HANDLE}")
    print(f"Long-form videos in last {LOOKBACK_DAYS} days: {len(long_form)}")
    print(f"(of {len(videos)} total uploads; Shorts/live excluded)\n")
    for cohort in (v30, v90):
        print(
            f"V{cohort['days']}: mean {fmt_views(cohort['mean'])} | "
            f"median {fmt_views(cohort['median'])} | "
            f"{cohort['count']} videos"
        )
    print(
        f"\nSponsorship rate: {fmt_views_short(v90['mean'])} avg views ÷ 1,000 "
        f"× ${CPM_USD:.2f} CPM ≈ ${rate:,.0f}"
    )

    if args.list_videos:
        print("\n90-day cohort:")
        for v in v90["videos"]:
            print(
                f"  {v['published_at']:%Y-%m-%d}  "
                f"{fmt_views(v['views']):>12}  {v['title']}"
            )

    if args.update_readme:
        block = build_readme_block(v30, v90, now)
        changed = update_readme(args.update_readme, block)
        print(
            f"\nREADME {'updated' if changed else 'already up to date'}: "
            f"{args.update_readme}"
        )


if __name__ == "__main__":
    main()
