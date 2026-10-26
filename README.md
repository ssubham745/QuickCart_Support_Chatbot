# QuickCart Order Help (Part 2)

Customer chatbot: enter an order ID / ticket ID, ask a question, get an answer based on the real
order + the 4 SOP refund rules, plus a one-click action.

## Run
    pip install -r requirements.txt
    streamlit run app.py
Optional: set `ANTHROPIC_API_KEY` to let Claude reword replies (decisions stay rule-based).

## Files
- `app.py` - Streamlit UI (chat, order card, action buttons, "policy check" expander)
- `engine.py` - lookup, intent detection and the 4 rules (all refund decisions live here)
- `orders_clean.csv`, `tickets_clean.csv` - cleaned data from Part 1
- `test_engine.py` - run `python test_engine.py` to check the rules
- `actions_log.csv` - created when a customer confirms an action

## How the rules are applied
- Rule 1: LOST / RETURNED -> 100% refund (+₹100 voucher if the earlier ticket took >24h). DELAYED -> ₹100 voucher, wait 48h, no refund.
- Rule 2: damaged item -> replacement OR full refund, never both (also blocks if one was already given).
- Rule 3: COD + cancelled before ship -> ₹0.
- Rule 4: refund + voucher never exceeds order_amount + ₹100.

## Assumptions / limits (be upfront about these)
- Intent detection is keyword-based, so unusual phrasing may get the "what do you need?" reply.
- SOP says nothing about prepaid orders cancelled before shipping; I assumed refund of the amount paid.
- Delivered order + "not received" is escalated to a human investigation, no automatic refund.
- No customer login: anyone with an order ID can query it. A real system would verify the customer.
- "Actions" only write to a CSV; no real payment system is connected.
