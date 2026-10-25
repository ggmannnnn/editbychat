from pathlib import Path
import subprocess
import tempfile
import app


def sh(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def main():
    assert app.parse_deterministic("Zoom 1.3x from 4 to 7 seconds", 10)["edits"][0]["type"] == "zoom_segment"
    assert app.parse_deterministic("كبر 1.4x من 2 حتى 5", 10)["edits"][0]["zoom"] == 1.4
    assert app.parse_deterministic("Fade out 1 second", 10)["edits"][0]["direction"] == "out"
    ins = app.parse_deterministic("Insert clip at 5 seconds with fade", 10)["edits"][0]
    assert ins["type"] == "insert_clip" and ins["transition"] == "fade"
    rep = app.parse_deterministic("Replace from 5 to 9 seconds with clip", 10)["edits"][0]
    assert rep["type"] == "replace_segment" and rep["start"] == 5 and rep["end"] == 9

    zoom_meta = {"commands":[{"base":"edit_000.mp4","edits":[{"type":"zoom_segment","start":2,"end":5,"zoom":1.3,"focus":"center"}]}],"assets":[]}
    zrev = app.parse_context_revision("زيد شوية فالzoom", zoom_meta, 10)
    assert zrev and zrev["edits"][0]["zoom"] == 1.4 and zrev["base"] == "edit_000.mp4"
    logo_meta = {"commands":[{"base":"edit_000.mp4","edits":[{"type":"image_overlay","position":"top-right","scale":0.18,"asset_id":"img1"}]}],"assets":[{"id":"img1","kind":"image"}]}
    lrev = app.parse_context_revision("حركو لليسار", logo_meta, 10)
    assert lrev and lrev["edits"][0]["position"] == "top-left" and lrev["edits"][0]["asset_id"] == "img1"
    text_meta = {"commands":[{"base":"edit_000.mp4","edits":[{"type":"text_overlay","text":"Order Now","start":5,"end":8,"position":"bottom"}]}],"assets":[]}
    trev = app.parse_context_revision("خليه يبدا ثانية بكري", text_meta, 10)
    assert trev and trev["edits"][0]["start"] == 4 and trev["edits"][0]["end"] == 7
    assert app.is_undo_command("لا رجعو كيف كان")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        main_video = d / "main.mp4"
        clip = d / "clip.mp4"
        zoom_out = d / "zoom.mp4"
        fade_out = d / "fade.mp4"
        insert_out = d / "insert.mp4"
        replace_out = d / "replace.mp4"

        sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", main_video])
        sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=30", "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=44100", "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", clip])

        app.apply_edit(main_video, zoom_out, {"type":"zoom_segment","start":2,"end":5,"zoom":1.3,"focus":"right"}, d)
        app.apply_edit(main_video, fade_out, {"type":"fade","direction":"out","duration":1}, d)
        app.apply_edit(main_video, insert_out, {"type":"insert_clip","asset_path":str(clip),"at":5,"transition":"fade"}, d)
        app.apply_edit(main_video, replace_out, {"type":"replace_segment","asset_path":str(clip),"start":4,"end":7,"transition":"cut"}, d)

        assert abs(app.probe_duration(zoom_out) - 10) < 0.2
        assert abs(app.probe_duration(fade_out) - 10) < 0.2
        assert 11.0 < app.probe_duration(insert_out) < 11.6  # true crossfade overlaps both boundaries
        assert abs(app.probe_duration(replace_out) - 9) < 0.2
        for p in [zoom_out, fade_out, insert_out, replace_out]:
            assert p.exists() and app.probe_has_audio(p) and app.probe_video_size(p) == (640, 360)

        # Export settings path: no upscale for a small source, but re-encode succeeds.
        export_meta = {"current": main_video.name, "name": "Smoke test"}
        export_req = app.ExportRequest(project_id="000000000000", resolution="1080p", fps="30", quality="draft")
        export_out = app.export_project_file(d, export_meta, export_req)
        assert export_out.exists() and app.probe_video_size(export_out) == (640, 360)

    assert app.healthz()["ok"] is True
    print("smoke tests passed")


if __name__ == "__main__":
    main()
