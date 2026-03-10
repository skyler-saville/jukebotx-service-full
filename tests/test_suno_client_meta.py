from jukebotx_infra.suno.client import (
    SunoTrackData,
    _extract_video_url,
    _parse_meta_tags,
    _parse_title_artist_from_description,
)


def test_parse_meta_tags_unescapes_description_title() -> None:
    page_html = """
    <html>
      <head>
        <meta name="description" content="Pixel Fighter&#x27;s by DJ Example. Listen and make your own on Suno. (@djexample)">
        <meta property="og:description" content="Pixel Fighter&#x27;s by DJ Example. Listen and make your own on Suno. (@djexample)">
      </head>
      <body></body>
    </html>
    """

    meta = _parse_meta_tags(page_html)
    description = meta.get("description") or meta.get("og:description")
    title, artist_display, artist_username = _parse_title_artist_from_description(description)

    track = SunoTrackData(
        suno_url="https://suno.com/song/example",
        title=title,
        artist_display=artist_display,
        artist_username=artist_username,
        lyrics=None,
        image_url=None,
        video_url=None,
        mp3_url=None,
    )

    assert track.title == "Pixel Fighter's"


def test_parse_title_artist_preserves_periods_before_promo() -> None:
    description = "Track Title by Mr.Finnish. Listen and make your own on Suno"

    title, artist_display, artist_username = _parse_title_artist_from_description(description)

    assert title == "Track Title"
    assert artist_display == "Mr.Finnish"
    assert artist_username is None


def test_extract_video_url_falls_back_to_video_tag_src() -> None:
    page_html = """
    <html>
      <body>
        <video
          src="https://cdn1.suno.ai/video_upload_153f0af0-f436-4721-ae0a-e4af4bde1e78_processed_video.mp4"
          preload="auto"
          loop
        ></video>
      </body>
    </html>
    """

    assert (
        _extract_video_url(page_html, og_video=None)
        == "https://cdn1.suno.ai/video_upload_153f0af0-f436-4721-ae0a-e4af4bde1e78_processed_video.mp4"
    )


def test_extract_video_url_prefers_og_video_when_present() -> None:
    page_html = """
    <html>
      <body>
        <video src="https://cdn1.suno.ai/from-tag.mp4"></video>
      </body>
    </html>
    """

    assert _extract_video_url(page_html, og_video="https://cdn1.suno.ai/from-meta.mp4") == "https://cdn1.suno.ai/from-meta.mp4"
