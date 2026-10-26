import os

import streamlit as st

import engine as e

st.set_page_config(page_title="QuickCart Support", page_icon="🛒", layout="centered")


@st.cache_data
def get_data():
    return e.load_data()


orders, tickets = get_data()


def get_key():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:                                                              
        return None


def llm_available():
    return bool(get_key())


def polish(question, decision):
    """Rewrites the reply in a friendlier tone. The DECISION is already made by engine.py;
    the model is told not to change any amount, rule or option. Falls back to plain text on any error."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=get_key())
        msg = client.messages.create(
            model=os.environ.get("QC_MODEL", "claude-sonnet-5-5"),
            max_tokens=400,
            system=(
                "You are QuickCart's support assistant. Rewrite the draft reply so it sounds warm and "
                "natural, 3-5 short sentences. Do NOT change any rupee amount, status, rule outcome or "
                "option. Do NOT promise anything that is not in the draft. Keep markdown bold on amounts."
            ),
            messages=[{"role": "user", "content": f"Customer said: {question}\n\nDraft reply:\n{decision['reply']}"}],
        )
        return msg.content[0].text
    except Exception:
        return decision["reply"]


ss = st.session_state
ss.setdefault("messages", [])
ss.setdefault("order", None)
ss.setdefault("ticket", None)
ss.setdefault("pending", None)                                                    
ss.setdefault("trace_msgs", [])


def reset_chat():
    ss.messages, ss.pending, ss.trace_msgs = [], None, []


with st.sidebar:
    st.header("Demo IDs")
    st.caption("Click one to load it, then ask a question.")
    m = orders.merge(tickets[["order_id", "ticket_id", "issue_category", "resolution_status", "resolution_hours"]], on="order_id", how="left")
    samples = {
        "Lost order (open ticket)": m[(m.delivery_status == e.LOST) & m.resolution_status.fillna("").str.startswith("Open")],
        "Delayed order": m[m.delivery_status == e.DELAYED],
        "Returned to seller": m[(m.delivery_status == e.RETURNED)],
        "COD cancelled before ship": m[(m.delivery_status == e.CANCELLED) & m.payment_method.str.startswith("Cash")],
        "Delivered, no ticket": m[(m.delivery_status == e.DELIVERED) & m.ticket_id.isna()],
        "Delivered, replacement already sent": m[m.resolution_status == "Resolved - Replacement Sent"],
    }
    for label, df in samples.items():
        if len(df):
            oid = df.iloc[3 if len(df) > 3 else 0].order_id
            if st.button(f"{label}\n{oid}", use_container_width=True):
                ss.id_box = oid
                reset_chat()
    st.divider()
    if st.button("Reset demo actions", use_container_width=True):
        if os.path.exists(e.LOG_PATH):
            os.remove(e.LOG_PATH)
        reset_chat()
        st.rerun()

                                                       
st.title("🛒 QuickCart Order Help")
st.write("Enter your order ID or ticket ID, then ask your question.")

id_text = st.text_input("Order ID or Ticket ID", key="id_box", placeholder="e.g. ORD-701920 or TKT-901920")

if id_text:
    order, ticket, err = e.lookup(id_text, orders, tickets)
    if err:
        st.error(err)
        order = None
    elif ss.order is None or ss.order.order_id != order.order_id:
        reset_chat()
    ss.order, ss.ticket = order, ticket
else:
    ss.order = ss.ticket = None

order, ticket = ss.order, ss.ticket

if order is not None:
    with st.container(border=True):
        c1, c2, c3 = st.columns(3)
        c1.metric("Order", order.order_id)
        c2.metric("Amount", e.inr(order.order_amount))
        c3.metric("Status", order.delivery_status.replace("_", " ").title())
        st.caption(
            f"{order.product_category} · {order.courier_partner.replace('_', ' ')} · {order.payment_method} · "
            f"{order.customer_city} · placed {order.order_date:%d %b %Y}"
            + (f" · Ticket {ticket.ticket_id}: {ticket.resolution_status}" if ticket is not None else " · No support ticket yet")
        )

                  
    for i, msg in enumerate(ss.messages):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and ss.trace_msgs[i]:
                with st.expander("Policy check (rules applied)"):
                    for rule, res in ss.trace_msgs[i]:
                        st.markdown(f"- **{rule}**: {res}")

                                                 
    if ss.pending and ss.pending["options"]:
        opts = ss.pending["options"]
        st.write("**Choose an action:**" if len(opts) > 1 else "**Instant action:**")
        cols = st.columns(len(opts))
        for col, a in zip(cols, opts):
            if col.button(a["label"], key=f"act_{a['kind']}_{a['label']}", use_container_width=True):
                e.write_log(order.order_id, a["kind"], a["refund"], a["voucher"])
                done = {
                    "FULL_REFUND": f"✅ Done. A refund of {e.inr(a['refund'])} has been initiated to your original payment method.",
                    "REFUND_VOUCHER": f"✅ Done. A refund of {e.inr(a['refund'])} has been initiated and a {e.inr(a['voucher'])} voucher added to your account.",
                    "VOUCHER": f"✅ Done. A {e.inr(a['voucher'])} voucher has been added to your account.",
                    "REPLACEMENT": "✅ Done. A free replacement is being arranged. No cash refund applies to this order.",
                    "ESCALATE": "✅ Done. A delivery investigation has been raised. Our team will get back to you.",
                }[a["kind"]]
                ss.messages.append({"role": "assistant", "content": done})
                ss.trace_msgs.append([])
                ss.pending = None
                st.rerun()

                
    if q := st.chat_input("Ask something about this order"):
        ss.messages.append({"role": "user", "content": q})
        ss.trace_msgs.append([])
        out = e.respond(q, order, ticket, orders, tickets)
        out["reply"] = polish(q, out) if llm_available() else out["reply"]
        ss.messages.append({"role": "assistant", "content": out["reply"]})
        ss.trace_msgs.append(out["trace"])
        ss.pending = out
        st.rerun()
else:
    st.info("Pick a demo ID from the sidebar or type your own to get started.")
