from agentflow.core import Recorder, RunStore, render_html, render_text


def test_render_text_and_html(tmp_path):
    rec = Recorder(str(tmp_path), flow="demo")
    rec.note("hi")
    rec.finish()
    meta, events = RunStore(str(tmp_path)).load(rec.run_id)

    text = render_text(meta, events)
    assert rec.run_id in text and "note" in text

    html = render_html(meta, events)
    assert rec.run_id in html and "<ol>" in html
