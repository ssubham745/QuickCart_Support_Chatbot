import os, engine as e

e.LOG_PATH = os.path.join(os.path.dirname(__file__), "_test_log.csv")
if os.path.exists(e.LOG_PATH): os.remove(e.LOG_PATH)
o, t = e.load_data()
m = o.merge(t[["order_id", "ticket_id", "issue_category", "resolution_status", "resolution_hours"]], on="order_id", how="left")

def run(oid, q):
    od, tk, err = e.lookup(oid, o, t); assert not err, err
    return e.respond(q, od, tk, o, t)

def first(df): return df.iloc[0].order_id

oid = first(m[(m.delivery_status == "LOST_IN_TRANSIT") & m.resolution_status.fillna("").str.startswith("Open")])
r = run(oid, "where is my order"); a = r["options"][0]
assert a["refund"] == float(o[o.order_id == oid].order_amount.iloc[0]) and a["voucher"] == 0, r
print("Rule1 lost OK  ", oid, a["label"])

oid = first(m[(m.delivery_status == "RETURNED_TO_SELLER") & (m.resolution_status == "Resolved - Full Refund Issued") & (m.resolution_hours > 24)])
r = run(oid, "refund please"); assert r["options"] == [], r
print("Rule1 no double refund OK", oid)

oid = first(m[(m.delivery_status == "DELAYED_IN_TRANSIT") & (m.resolution_status != "Resolved - Apology Voucher Issued")])
r = run(oid, "I want a refund")
assert all(x["refund"] == 0 for x in r["options"]) and r["options"][0]["voucher"] == 100, r
print("Rule1 delayed OK", oid)

oid = first(m[(m.delivery_status == "DELIVERED") & m.ticket_id.isna()])
r = run(oid, "My item came broken, I want a refund")
kinds = {x["kind"] for x in r["options"]}
assert kinds == {"REPLACEMENT", "FULL_REFUND"}
assert all(not (x["kind"] == "REPLACEMENT" and x["refund"] > 0) for x in r["options"]); print("Rule2 choice OK", oid)

oid = first(m[(m.delivery_status == "DELIVERED") & (m.resolution_status == "Resolved - Replacement Sent")])
r = run(oid, "it's damaged, give me a refund"); assert r["options"] == [], r
print("Rule2 never both OK", oid)

oid = first(m[(m.delivery_status == "CANCELLED_BEFORE_SHIP") & m.payment_method.str.startswith("Cash")])
r = run(oid, "refund"); assert r["options"] == [] and "₹0" in r["reply"]; print("Rule3 OK", oid)

bad = 0
for _, od in o.sample(600, random_state=1).iterrows():
    tk = t[t.order_id == od.order_id]; tk = tk.iloc[0] if len(tk) else None
    for q in ["refund", "broken item", "where is my order", "not received"]:
        out = e.respond(q, od, tk, o, t)
        for a in out["options"]:
            if a["refund"] + a["voucher"] > od.order_amount + 100: bad += 1
assert bad == 0; print("Rule4 cap OK (600 orders x 4 questions)")

assert e.lookup("tkt 901920", o, t)[0].order_id == "ORD-701920"
assert e.lookup("nonsense", o, t)[2] and e.lookup("ORD-999999", o, t)[2]
print("lookup OK")
if os.path.exists(e.LOG_PATH): os.remove(e.LOG_PATH)
