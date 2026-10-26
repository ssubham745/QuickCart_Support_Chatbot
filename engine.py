import os
import re
from datetime import datetime

import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(BASE, "actions_log.csv")
VOUCHER = 100

LOST, RETURNED = "LOST_IN_TRANSIT", "RETURNED_TO_SELLER"
DELAYED, CANCELLED, DELIVERED = "DELAYED_IN_TRANSIT", "CANCELLED_BEFORE_SHIP", "DELIVERED"


def inr(x):
    return f"₹{x:,.0f}"



def load_data():
    orders = pd.read_csv(os.path.join(BASE, "orders_clean.csv"), parse_dates=["order_date"])
    tickets = pd.read_csv(
        os.path.join(BASE, "tickets_clean.csv"),
        parse_dates=["ticket_created_at", "ticket_resolved_at"],
    )
    return orders, tickets


def read_log():
    if os.path.exists(LOG_PATH):
        return pd.read_csv(LOG_PATH)
    return pd.DataFrame(columns=["time", "order_id", "action", "refund", "voucher"])


def write_log(order_id, action, refund, voucher):
    row = pd.DataFrame(
        [[datetime.now().isoformat(timespec="seconds"), order_id, action, refund, voucher]],
        columns=["time", "order_id", "action", "refund", "voucher"],
    )
    row.to_csv(LOG_PATH, mode="a", header=not os.path.exists(LOG_PATH), index=False)



def normalise_id(text):
    """'ord-701920', '701920', 'TKT 901920' -> ('order'|'ticket', 'ORD-701920')"""
    t = text.strip().upper().replace("_", "-").replace(" ", "-")
    m = re.search(r"(\d{6})", t)
    if not m:
        return None, None
    num = m.group(1)
    if t.startswith("TKT"):
        return "ticket", f"TKT-{num}"
    return "order", f"ORD-{num}"


def lookup(text, orders, tickets):
    """Returns (order_row, ticket_row, error). ticket_row may be None."""
    kind, key = normalise_id(text)
    if key is None:
        return None, None, "That doesn't look like an order or ticket ID. Try something like ORD-701920 or TKT-901920."
    if kind == "ticket":
        t = tickets[tickets.ticket_id == key]
        if t.empty:
            return None, None, f"I couldn't find ticket {key}."
        t = t.iloc[0]
        o = orders[orders.order_id == t.order_id]
        return o.iloc[0], t, None
    o = orders[orders.order_id == key]
    if o.empty:
        return None, None, f"I couldn't find order {key}."
    t = tickets[tickets.order_id == key]
    return o.iloc[0], (t.iloc[0] if not t.empty else None), None


def history(order_id, tickets, log):
    """What has already been given on this order (tickets + actions taken in this app)."""
    t = tickets[tickets.order_id == order_id]
    cash = t.loc[t.resolution_status == "Resolved - Full Refund Issued", "refund_amount"].fillna(0).sum()
    voucher = t.loc[t.resolution_status == "Resolved - Apology Voucher Issued", "refund_amount"].fillna(0).sum()
    replacement = (t.resolution_status == "Resolved - Replacement Sent").any()
    l = log[log.order_id == order_id]
    cash += l.refund.sum()
    voucher += l.voucher.sum()
    replacement = replacement or (l.action == "REPLACEMENT").any()
    return {"cash": float(cash), "voucher": float(voucher), "replacement": bool(replacement)}



def detect_intent(text):
    s = text.lower()
    has = lambda words: any(w in s for w in words)
    if has(["damag", "broken", "broke", "defect", "faulty", "cracked", "not working", "torn", "doesn't work", "doesnt work"]):
        return "damaged"
    if has(["refund", "money back", "return my money", "get my money", "reimburse"]):
        return "refund"
    if has(["not received", "haven't received", "havent received", "didn't receive", "didnt receive",
            "never came", "not arrive", "didn't arrive", "didnt arrive", "never arrived", "not delivered"]):
        return "not_received"
    if has(["ticket", "complaint", "case status"]):
        return "ticket_status"
    if has(["cancel"]):
        return "cancel"
    if has(["where", "track", "status", "when will", "arrive", "eta", "late", "delay", "delivery", "update"]):
        return "track"
    return "unknown"



def _act(label, kind, refund=0, voucher=0):
    return {"label": label, "kind": kind, "refund": float(refund), "voucher": float(voucher)}


def decide(order, ticket, intent, hist):
    """
    Returns dict: reply (str), trace (list of (rule, result)), options (list of action dicts).
    options empty -> information only. >1 option -> customer must pick one (Rule 2).
    """
    status, amt, pay = order.delivery_status, float(order.order_amount), order.payment_method
    cap = amt + VOUCHER
    cod = pay.startswith("Cash on Delivery")
    trace, options = [], []
    courier = order.courier_partner.replace("_", " ")
    late_ticket = ticket is not None and pd.notna(ticket.resolution_hours) and ticket.resolution_hours > 24

    head = f"Order {order.order_id} ({order.product_category}, {inr(amt)}, via {courier}) is **{status.replace('_', ' ').title()}**."

    if intent == "unknown":
        return {"reply": f"{head}\n\nWhat would you like help with? For example: *Where is my order?* or *My item came broken, I want a refund.*", "trace": [("Intent", "unclear -> asked customer")], "options": []}

    if intent == "ticket_status" and ticket is not None:
        st = ticket.resolution_status
        if st.startswith("Open"):
            body = f"Your ticket {ticket.ticket_id} ({ticket.issue_category}) is still open: *{st.split(' - ')[1]}*."
        else:
            body = f"Your ticket {ticket.ticket_id} ({ticket.issue_category}) is resolved: *{st.split(' - ')[1]}*."
        trace.append(("Ticket lookup", st))
        # fall through to also offer action if order status warrants it
        if status not in (LOST, RETURNED, DELAYED):
            return {"reply": f"{head}\n\n{body}", "trace": trace, "options": []}
        head = f"{head}\n\n{body}\n\nLooking at the order itself:"

    
    if status in (LOST, RETURNED):
        why = "was lost in transit" if status == LOST else "was returned to the seller"
        if hist["cash"] >= amt:
            trace.append(("Rule 1", f"Full refund already given ({inr(hist['cash'])})"))
            extra = f" (that already includes the {inr(hist['cash'] - amt)} late-support voucher)" if hist["cash"] > amt else ""
            return {"reply": f"{head}\n\nThe order {why}, and a full refund of {inr(hist['cash'])}{extra} has already been issued, so there's nothing more to refund.", "trace": trace, "options": []}
        refund = amt - hist["cash"]
        voucher = VOUCHER if (late_ticket and hist["voucher"] == 0) else 0
        if hist["cash"] + hist["voucher"] + refund + voucher > cap:   # Rule 4
            voucher = max(0, cap - hist["cash"] - hist["voucher"] - refund)
        trace.append(("Rule 1", f"{status} -> refund 100% of order_amount = {inr(refund)}"))
        if voucher:
            trace.append(("Rule 1 (late support)", f"Ticket took {ticket.resolution_hours:.0f}h (>24h) -> +{inr(voucher)} voucher"))
        trace.append(("Rule 4", f"Total {inr(refund + voucher + hist['cash'] + hist['voucher'])} <= cap {inr(cap)}"))
        if intent == "damaged":
            head += "\n\nSince it never reached you in the first place, damage doesn't apply here."
        msg = f"{head}\n\nSorry about this. The order {why}, so you're eligible for a **full refund of {inr(refund)}**."
        if voucher:
            msg += f" Because your earlier ticket took more than 24 hours to resolve, I'm also adding a **{inr(voucher)} apology voucher**."
        label = f"Refund {inr(refund)}" + (f" + {inr(voucher)} voucher" if voucher else "")
        options.append(_act(label, "REFUND_VOUCHER" if voucher else "FULL_REFUND", refund, voucher))
        return {"reply": msg, "trace": trace, "options": options}

    
    if status == DELAYED:
        trace.append(("Rule 1", "DELAYED_IN_TRANSIT -> no full refund yet, ₹100 voucher + wait 48h"))
        if intent == "refund":
            lead = "I can't refund the full amount yet because the order is still on its way."
        else:
            lead = "Your order is delayed but still moving."
        if hist["voucher"] > 0:
            return {"reply": f"{head}\n\n{lead} You've already received a {inr(hist['voucher'])} apology voucher. Please give it 48 more hours. If it hasn't reached you by then, message us again and we'll re-check.", "trace": trace, "options": []}
        trace.append(("Rule 4", f"{inr(VOUCHER)} <= cap {inr(cap)}"))
        msg = f"{head}\n\n{lead} As an apology I can give you a **{inr(VOUCHER)} voucher** right now. Please wait **48 hours**; if it still hasn't arrived after that, contact us again and we'll look at a refund."
        return {"reply": msg, "trace": trace, "options": [_act(f"Claim {inr(VOUCHER)} voucher", "VOUCHER", 0, VOUCHER)]}

    
    if status == CANCELLED:
        if cod:
            trace.append(("Rule 3", "COD + CANCELLED_BEFORE_SHIP -> refund must be ₹0"))
            return {"reply": f"{head}\n\nThis was a Cash on Delivery order that was cancelled before shipping, so you were never charged anything. That means there's **nothing to refund (₹0)**.", "trace": trace, "options": []}
        trace.append(("Rule 3", "Not COD -> rule doesn't apply"))
        trace.append(("Policy gap", "SOP has no rule for prepaid cancellations; assumed refund of amount paid"))
        if hist["cash"] >= amt:
            return {"reply": f"{head}\n\nYou paid online and the order was cancelled before shipping. A refund of {inr(hist['cash'])} has already been issued.", "trace": trace, "options": []}
        refund = amt - hist["cash"]
        return {"reply": f"{head}\n\nYou paid online and the order was cancelled before shipping, so the amount paid, **{inr(refund)}**, can go back to you.", "trace": trace, "options": [_act(f"Refund {inr(refund)}", "FULL_REFUND", refund, 0)]}

    
    if intent == "damaged":
        trace.append(("Rule 2", "Damaged/defective -> EITHER replacement OR full refund, never both"))
        if hist["replacement"] or hist["cash"] > 0:
            given = "a replacement was already sent" if hist["replacement"] else f"a refund of {inr(hist['cash'])} was already issued"
            trace.append(("Rule 2", "Already resolved -> blocked"))
            return {"reply": f"{head}\n\nI'm sorry the item was damaged. On our records {given} for this order. Under our policy we can't give both a replacement and a refund on the same order, so I can't add another one here.", "trace": trace, "options": []}
        trace.append(("Rule 4", f"Refund {inr(amt)} <= cap {inr(cap)}"))
        msg = f"{head}\n\nSorry the item reached you damaged. You can choose **one** of these (not both):\n\n- a **free replacement** (no cash refund), or\n- a **full refund of {inr(amt)}**."
        return {"reply": msg, "trace": trace, "options": [_act("Free replacement", "REPLACEMENT", 0, 0), _act(f"Full refund {inr(amt)}", "FULL_REFUND", amt, 0)]}

    if intent == "refund":
        trace.append(("Rules 1-3", "DELIVERED and no damage reported -> no refund rule applies"))
        return {"reply": f"{head}\n\nOur records show it was delivered, and refunds are only given for lost/returned orders or damaged/defective items. If the item arrived damaged or defective, tell me and I'll sort out a replacement or refund.", "trace": trace, "options": []}

    if intent == "not_received":
        trace.append(("Escalation", "System says DELIVERED but customer says not received -> human check"))
        return {"reply": f"{head}\n\nOur system shows it as delivered, so I can't refund automatically. I can raise a delivery investigation with {courier} for you.", "trace": trace, "options": [_act("Raise delivery investigation", "ESCALATE", 0, 0)]}

    if intent == "ticket_status":
        return {"reply": f"{head}\n\nI couldn't find a support ticket for this order. Tell me what went wrong and I'll help.", "trace": trace, "options": []}

    trace.append(("Info", "Delivered, nothing to action"))
    return {"reply": f"{head}\n\nIt's been delivered. If something is wrong with it (damaged, defective), just tell me.", "trace": trace, "options": []}


def respond(text, order, ticket, orders, tickets):
    intent = detect_intent(text)
    hist = history(order.order_id, tickets, read_log())
    out = decide(order, ticket, intent, hist)
    out["intent"] = intent
    return out
