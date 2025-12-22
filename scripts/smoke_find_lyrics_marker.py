from __future__ import annotations

import asyncio
import re
import sys

import httpx


NEXT_F_RE = re.compile(r"self\.__next_f\.push\(", re.DOTALL)
NEXT_F_STR_RE = re.compile(
    r"""self\.__next_f\.push\(\s*\[\s*\d+\s*,\s*"(?P<payload>(?:\\.|[^"\\])*)"\s*\]\s*\)""",
    re.DOTALL,
)


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(
            "Usage: poetry run python scripts/smoke_find_lyrics_marker.py <suno_url>"
        )

    url = sys.argv[1]

    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        html = r.text

    print(f"URL: {url}")
    print(f"HTTP: {r.status_code}")
    print(f"HTML length: {len(html)}\n")

    markers = [
        "whitespace-pre-wrap",
        "Copy lyrics to clipboard",
        "pr-6",
        "__NEXT_DATA__",
        "self.__next_f.push",
        "[Verse",
        "[Chorus",
        "\\n\\n",
    ]

    for m in markers:
        count = html.count(m)
        print(f"{m!r} present? -> {count > 0} (count={count})")

    print()

    # Explicit Next.js streaming diagnostics
    has_next_f = bool(NEXT_F_RE.search(html))
    print("Next.js streaming payload detected? ->", has_next_f)

    if has_next_f:
        matches = list(NEXT_F_STR_RE.finditer(html))
        print(f"__next_f string payload count -> {len(matches)}")

        if matches:
            sample = matches[0].group("payload")[:300]
            print("\nFirst payload excerpt (escaped):")
            print(sample)
            print("\nDecoded preview:")
            print(sample.replace("\\n", "\n")[:300])

    print("\nNOTE:")
    print(
        "- If __next_f.push is present, lyrics are streamed via JS, not HTML DOM.\n"
        "- DOM class-based scraping will NOT work.\n"
        "- HTTP-only scraping is still viable via string payload extraction."
    )


if __name__ == "__main__":
    asyncio.run(main())
