import csv,io,json
import pytest
from app import store
from app.errors import DomainError
from app.pipeline import calculate,execute,load_sample
from app.modules.m2_nodes.rules import evaluate as nodes
from app.modules.m4_paths.rules import evaluate as paths
def scan(id,parcel,node,at,note="集运"):
    return {"scan_id":str(id),"parcel":parcel,"node":node,"event_utc":at,"received_utc":"2026-11-09T00:00:00Z","zone":"Asia/Tokyo","destination":"JP","promised_hours":"24","declared_value":"200","reported":"0","note":note,"_sources":[id]}
@pytest.fixture
def reissue(): return calculate(load_sample("reissue.csv"))
def test_lost_node_mapped(): assert nodes([{"node":"遗失","destination":"JP","parcel":"1"}],{})["rows"][0]["node"]=="LOST"
def test_first_valid_signature_ends_liability():
    "TRAP-27 两条运输链都签收时，先出现的有效签收形成正式交付"
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","DELIVERED","2026-11-05T00:00:00Z"),scan(3,"B","ACCEPT","2026-11-04T00:00:00Z","补发:A"),scan(4,"B","DELIVERED","2026-11-06T00:00:00Z")]
    r=paths(rows,{})
    assert [x["parcel"] for x in r["rows"]]==["A"] and r["rows"][0]["delivered_at"]=="2026-11-05T00:00:00Z"
    assert r["rows"][0]["not_adopted"]==["B"] and r["quarantine"][0]["reason"]=="CHAIN_NOT_ADOPTED"
def test_reissue_adopted_when_original_lost():
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","LOST","2026-11-03T00:00:00Z"),scan(3,"B","ACCEPT","2026-11-04T00:00:00Z","补发:A"),scan(4,"B","DELIVERED","2026-11-06T00:00:00Z")]
    r=paths(rows,{})
    assert r["rows"][0]["parcel"]=="B" and r["rows"][0]["unit_id"]=="A"
    assert r["quarantine"][0]["parcel"]=="A" and 2 in r["quarantine"][0]["_sources"]
def test_late_loss_record_does_not_revoke_delivery():
    "TRAP-28 晚到的遗失记录不得撤销更早已签收的交付"
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","DELIVERED","2026-11-03T00:00:00Z"),scan(3,"A","LOST","2026-11-06T00:00:00Z"),scan(4,"B","ACCEPT","2026-11-07T00:00:00Z","补发:A"),scan(5,"B","DELIVERED","2026-11-09T00:00:00Z")]
    row=paths(rows,{})["rows"][0]
    assert row["parcel"]=="A" and row["delivered_at"]=="2026-11-03T00:00:00Z" and row["lost_void"] is True
    assert 3 in row["_sources"]
def test_unsigned_unit_single_open_liability():
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","LOST","2026-11-03T00:00:00Z"),scan(3,"B","ACCEPT","2026-11-04T00:00:00Z","补发:A")]
    r=paths(rows,{})
    assert len(r["rows"])==1 and r["rows"][0]["parcel"]=="A" and r["rows"][0]["delivered_at"] is None
    assert r["quarantine"][0]["reason"]=="CHAIN_NOT_ADOPTED"
def test_multiple_reissues_one_liability():
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","LOST","2026-11-03T00:00:00Z"),scan(3,"B","ACCEPT","2026-11-04T00:00:00Z","补发:A"),scan(4,"B","LOST","2026-11-05T00:00:00Z"),scan(5,"C","ACCEPT","2026-11-06T00:00:00Z","补发:B"),scan(6,"C","DELIVERED","2026-11-08T00:00:00Z")]
    r=paths(rows,{})
    assert len(r["rows"])==1 and r["rows"][0]["parcel"]=="C" and r["rows"][0]["chains"]==3 and len(r["quarantine"])==2
def test_reissue_conflict_quarantined():
    "TRAP-29 补发关系指向多个原件时整链隔离"
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"B","ACCEPT","2026-11-03T00:00:00Z","补发:A"),scan(3,"B","DEPART","2026-11-03T01:00:00Z","补发:C")]
    r=paths(rows,{})
    assert [q["reason"] for q in r["quarantine"]].count("REISSUE_CONFLICT")==2 and len(r["rows"])==1
def test_fullwidth_colon_marker():
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"B","ACCEPT","2026-11-03T00:00:00Z","补发：A"),scan(3,"B","DELIVERED","2026-11-05T00:00:00Z")]
    assert paths(rows,{})["rows"][0]["chains"]==2
def test_dangling_marker_stays_singleton():
    rows=[scan(1,"B","ACCEPT","2026-11-03T00:00:00Z","补发:Z9"),scan(2,"B","DELIVERED","2026-11-05T00:00:00Z")]
    assert paths(rows,{})["rows"][0]["chains"]==1
def test_adjudication_order_independent():
    rows=[scan(1,"A","ACCEPT","2026-11-02T00:00:00Z"),scan(2,"A","DELIVERED","2026-11-05T00:00:00Z"),scan(3,"B","ACCEPT","2026-11-04T00:00:00Z","补发:A"),scan(4,"B","DELIVERED","2026-11-06T00:00:00Z")]
    assert paths(rows,{})==paths(list(reversed(rows)),{})
def test_sample_no_double_delivery(reissue):
    m=reissue["stages"][3]["metrics"]
    assert (m["parcels"],m["delivered"],m["reissue_units"],m["chains_not_adopted"])==(5,4,4,4)
def test_sample_unit_results(reissue):
    rows={r["parcel"]:r for r in reissue["stages"][3]["rows"]}
    assert rows["8001"]["delivered_at"].startswith("2026-11-06") and rows["8001"]["not_adopted"]==["8002"]
    assert rows["8004"]["unit_id"]=="8003" and rows["8005"]["lost_void"] is True and rows["8007"]["delivered_at"] is None
def test_sample_no_double_compensation(reissue):
    got={r["parcel"]:r["expected"] for r in reissue["stages"][4]["rows"]}
    assert got=={"8001":"20.00","8004":"15.00","8005":"0.00","8007":"20.00","8009":"0.00"}
    m=reissue["stages"][5]["metrics"]
    assert m["expected_total"]=="55.00" and m["reported_total"]=="5.00" and m["differences"]==3
def test_sample_balance_and_archive(reissue):
    assert reissue["stages"][5]["metrics"]["balanced"] is True and reissue["stages"][6]["metrics"]["ready"] is True
def test_original_scans_and_loss_declarations_kept(reissue):
    final=reissue["stages"][6]
    used=[x for row in final["rows"] for x in row["_sources"]]+[x for q in final["cumulative_quarantine"] for x in q["_sources"]]
    assert sorted(used)==list(range(2,25))
def test_loss_declarations_in_evidence(reissue):
    quar={q["parcel"]:q for q in reissue["stages"][3]["quarantine"]}
    rows={r["parcel"]:r for r in reissue["stages"][3]["rows"]}
    assert quar["8003"]["lost_declared_at"].startswith("2026-11-04") and rows["8005"]["lost_declared_at"].startswith("2026-11-06")
def test_deterministic_rerun(): assert calculate(load_sample("reissue.csv"))["run_id"]==calculate(load_sample("reissue.csv"))["run_id"]
def test_evidence_download_same_conclusion():
    from app.archive import build_archive
    run=execute(load_sample("reissue.csv"))
    rows=list(csv.reader(io.StringIO(build_archive(run).encode().decode("utf-8-sig"))))
    quar=[x for x in rows[1:] if x[3]=="quarantine" and x[5]=="CHAIN_NOT_ADOPTED"]
    accepted=[x for x in rows[1:] if x[3]=="accepted"]
    assert len(quar)==4 and len(accepted)==5
    adopted=json.loads([x for x in accepted if json.loads(x[6])["parcel"]=="8001"][0][6])
    assert adopted["unit_id"]=="8001" and adopted["not_adopted"]==["8002"] and adopted["delivered_at"].startswith("2026-11-06")
    dropped=[json.loads(x[6]) for x in quar]
    assert {d["parcel"] for d in dropped}=={"8002","8003","8006","8008"} and all(d["unit_id"] for d in dropped)
    assert json.loads([x for x in quar if json.loads(x[6])["parcel"]=="8003"][0][4])==[8,9,10]
def test_failed_switch_keeps_previous_complete_version():
    run=execute(load_sample("reissue.csv"))
    reviewed=store.review(run["run_id"],run["version"],"已复核")
    with pytest.raises(DomainError): execute(b"bad")
    latest=store.latest()
    assert latest["run_id"]==run["run_id"] and latest["version"]==reviewed["version"] and latest["decision"]=="已复核" and len(latest["stages"])==7
def test_api_failed_switch_keeps_old_run(client):
    run=execute(load_sample("reissue.csv"))
    assert client.post("/api/runs",json={"csv":"bad"}).status_code==422
    assert store.latest()["run_id"]==run["run_id"] and run["run_id"] in client.get("/").data.decode()
def test_paths_page_marks_adjudication(client):
    execute(load_sample("reissue.csv"))
    html=client.get("/paths").data.decode()
    assert "采用运输链 8001" in html and "未采用记录" in html and "晚于签收，未撤销交付" in html
def test_paths_page_old_sample_compatible(client):
    execute(load_sample("dirty.csv"))
    assert "本批无补发裁决" in client.get("/paths").data.decode()
def test_archive_endpoint_same_conclusion(client):
    run=execute(load_sample("reissue.csv"))
    text=client.get("/api/runs/"+run["run_id"]+"/archive").data.decode("utf-8-sig")
    assert "CHAIN_NOT_ADOPTED" in text and "8001" in text
