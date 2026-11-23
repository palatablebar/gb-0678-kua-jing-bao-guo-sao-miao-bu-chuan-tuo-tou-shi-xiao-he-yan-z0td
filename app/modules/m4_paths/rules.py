import re
from collections import defaultdict
from app.values import instant
from app.reasons import reject,sources
PRIORITY={"ACCEPT":0,"DEPART":1,"CUSTOMS":2,"DELIVERED":3}
REISSUE=re.compile(r"补发\s*[:：]\s*([0-9A-Za-z-]+)")
def chain(events,lost):
    events=sorted(events,key=lambda r:(instant(r["event_utc"]),PRIORITY[r["node"]],r["scan_id"]))
    seen={};valid=[];bad=[];last=-1
    for e in events:
        time=e["event_utc"]
        if time in seen and seen[time]!=e["node"]:bad.append(reject(e,"SIMULTANEOUS_CONFLICT","同刻扫描节点冲突"));continue
        seen[time]=e["node"]
        if PRIORITY[e["node"]]<last:bad.append(reject(e,"PATH_REGRESSION","扫描路径倒退"));continue
        last=PRIORITY[e["node"]];valid.append(e)
    accepts=[e for e in valid if e["node"]=="ACCEPT"]
    if not accepts:
        bad.extend(reject(e,"MISSING_ACCEPT","缺少收件扫描") for e in valid+lost)
        return None,bad
    delivery=next((e for e in valid if e["node"]=="DELIVERED"),None)
    first=accepts[0]
    lost=sorted(lost,key=lambda r:(instant(r["event_utc"]),r["scan_id"]))
    declared=lost[0]["event_utc"] if lost else None
    void=bool(declared and delivery and instant(declared)>instant(delivery["event_utc"]))
    source_lines=[line for e in valid+lost for line in sources(e)]
    return {**first,"_sources":source_lines,"accepted_at":first["event_utc"],"delivered_at":delivery["event_utc"] if delivery else None,"delivery_uploaded_at":delivery["received_utc"] if delivery else None,"scan_count":len(valid),"path":" → ".join(e["node"] for e in valid),"reported":delivery["reported"] if delivery else first["reported"],"lost_declared_at":declared,"lost_recorded_at":lost[0]["received_utc"] if lost else None,"lost_void":void},bad
def evaluate(rows,context):
    groups=defaultdict(list)
    for r in rows:groups[r["parcel"]].append(r)
    chains={};links={};bad=[]
    for parcel in sorted(groups):
        scans=groups[parcel]
        targets={t for e in scans for t in REISSUE.findall(str(e.get("note",""))) if t!=parcel}
        if len(targets)>1:
            bad.extend(reject(e,"REISSUE_CONFLICT","补发关系指向多个原件") for e in scans);continue
        row,errors=chain([e for e in scans if e["node"]!="LOST"],[e for e in scans if e["node"]=="LOST"])
        bad.extend(errors)
        if row:chains[parcel]=row;links[parcel]=targets
    parent={p:p for p in chains}
    def find(x):
        while parent[x]!=x:parent[x]=parent[parent[x]];x=parent[x]
        return x
    for parcel in sorted(chains):
        for target in links[parcel]:
            if target in parent:parent[find(parcel)]=find(target)
    units=defaultdict(list)
    for parcel in chains:units[find(parcel)].append(parcel)
    good=[]
    for root in sorted(units,key=lambda u:min(units[u])):
        members=sorted(units[root]);unit=members[0]
        signed=[p for p in members if chains[p]["delivered_at"]]
        if signed:adopted=min(signed,key=lambda p:(instant(chains[p]["delivered_at"]),p))
        else:adopted=min(members,key=lambda p:(instant(chains[p]["accepted_at"]),p))
        others=[p for p in members if p!=adopted]
        row=chains[adopted]
        good.append({**row,"unit_id":unit,"chains":len(members),"not_adopted":others})
        for p in others:
            kept=chains[p]
            if row["delivered_at"]:detail=f"交付责任{unit}已采用运输链{adopted}于{row['delivered_at']}的签收，本链签收不再计赔"
            else:detail=f"交付责任{unit}的未妥投责任由运输链{adopted}承载，本链不再计赔"
            bad.append({**reject(kept,"CHAIN_NOT_ADOPTED",detail),"parcel":p,"unit_id":unit,"delivered_at":kept["delivered_at"],"lost_declared_at":kept["lost_declared_at"]})
    return {"rows":good,"quarantine":bad,"metrics":{"parcels":len(good),"delivered":sum(r["delivered_at"] is not None for r in good),"path_errors":len(bad),"delivery_units":len(good),"reissue_units":sum(r["chains"]>1 for r in good),"chains_not_adopted":sum(len(r["not_adopted"]) for r in good)}}
