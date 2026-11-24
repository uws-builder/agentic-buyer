import json

from buyer.cli import main


def run(tmp_path, argv=("run",), answer=None, interactive=True):
    lines = []
    code = main(list(argv), ask=lambda _: answer, interactive=interactive,
                out=lines.append, out_dir=tmp_path)
    record = json.loads((tmp_path / "buy_plan_summit_ridge_2027_ss.json").read_text())
    return code, "\n".join(lines), record


def statuses(record):
    return [o["status"] for o in record["orders"] if o["status"] != "cancelled"]


def test_non_interactive_never_submits(tmp_path):
    code, text, record = run(tmp_path, interactive=False)
    assert code == 0 and "approval needs an interactive terminal" in text
    assert set(statuses(record)) == {"drafted"}


def test_default_answer_approves_nothing(tmp_path):
    _, text, record = run(tmp_path, answer="")
    assert "Nothing approved" in text and set(statuses(record)) == {"drafted"}


def test_approve_all_submits_every_draft(tmp_path):
    _, text, record = run(tmp_path, answer="all")
    assert statuses(record) == ["submitted"] * 7 and "SO-" in text


def test_approve_some(tmp_path):
    _, _, record = run(tmp_path, answer="ord-001, ORD-003")
    by_id = {o["order_id"]: o["status"] for o in record["orders"]}
    assert by_id["ORD-001"] == by_id["ORD-003"] == "submitted" and by_id["ORD-002"] == "drafted"


def test_unknown_ids_approve_nothing(tmp_path):
    _, text, record = run(tmp_path, answer="ORD-001,ORD-999")
    assert "Not drafts: ORD-999" in text and set(statuses(record)) == {"drafted"}


def test_outage_at_submit_is_shown(tmp_path):
    _, text, record = run(tmp_path, argv=("run", "--seller-down", "Arena Live"), answer="all")
    # The script still tries Arena Live; listing fails, its draft fails, so no Arena order exists.
    assert "seller unavailable" in text
    assert all(o["publisher"] != "Arena Live" for o in record["orders"])


def test_bad_plan_is_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "nope"}))
    lines = []
    assert main(["run", "--plan", str(bad)], out=lines.append, out_dir=tmp_path) == 2
    assert lines[0].startswith("Plan rejected")
