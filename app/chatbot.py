from datetime import date, timedelta

from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

from app.models import Farm, PluckingRecord, PruningRecord, Tool, Note

chatbot_bp = Blueprint("chatbot", __name__)

OVERVIEW_KEYWORDS = [
    "overview", "summary", "report", "how is my farm", "how's my farm",
    "how am i doing", "recap", "status",
]

# Small rule-based farming assistant — works offline, no API key needed.
# Matched by keyword so it stays fast and predictable for farmers on slow connections.
KNOWLEDGE_BASE = [
    (["prune", "pruning", "skiff"], 
     "Pruning cycles for tea in Kericho are usually every 3-5 years. Light skiffing keeps bushes "
     "productive between full prunes. Best done in the dry season and always record the date and "
     "number of bushes so you can track the cycle on this dashboard."),
    (["pluck", "plucking", "harvest", "leaves"],
     "The standard is 'two leaves and a bud' — this gives the best quality and price. Plucking rounds "
     "of 7-10 days keep bushes producing consistently. Log your kilos every day you pluck so you can "
     "see your trend on the chart."),
    (["price", "auction", "money", "pay", "ksh", "shilling"],
     "Tea prices move with the Mombasa auction and your factory's bonus payout. Check the News page "
     "on this dashboard for the latest price updates."),
    (["pest", "disease", "aphid", "blister", "fungus"],
     "Common tea pests in Kericho include tea mosquito bugs and red spider mites; blister blight is "
     "the main fungal disease in wet weather. Inspect bushes regularly and consult your local KTDA "
     "extension officer for approved treatments."),
    (["fertilizer", "manure", "npk", "nitrogen"],
     "Tea generally responds well to NPK fertilizer application at the start and end of the rainy "
     "season. Follow your soil test and factory/extension officer's recommendation for exact rates."),
    (["weather", "rain", "rainfall"],
     "Check the weather card on your dashboard for the latest Kericho forecast — useful for planning "
     "plucking and pruning days."),
    (["tool", "equipment", "shears", "panga", "basket"],
     "Track all your farm tools — shears, pangas, plucking baskets, sprayers — on the Tools section of "
     "your dashboard, including purchase date and condition, so nothing gets lost or forgotten."),
    (["bush", "bushes", "spacing", "plant"],
     "Standard tea bush spacing is about 1.2m x 0.75m, giving roughly 9,000-11,000 bushes per hectare "
     "depending on cultivar. Keep your approximate bush count updated on your Farm profile."),
    (["record", "app", "dashboard", "how"],
     "Use the quick-add forms on your dashboard to log plucking (kilos), pruning (bushes pruned), tools, "
     "and notes. Your cumulative kilos and trend chart update automatically."),
]

FALLBACK = (
    "I'm still learning! I can help with questions about plucking, pruning, pests, fertilizer, "
    "tools, tea prices, using this dashboard — or ask me for an 'overview' of your farm records."
)

GREETINGS = ["hi", "hello", "habari", "mambo", "hey"]


def build_farm_overview(farmer) -> str:
    """Pull the farmer's real records from the database and summarise them in plain language."""
    farms = farmer.farms
    if not farms:
        return (
            "You don't have any farms registered yet. Use '+ Add farm' on your dashboard to add "
            "your first farm number, then I can give you an overview."
        )

    lines = [f"Here's a quick overview across your {len(farms)} farm(s):"]
    cutoff_30 = date.today() - timedelta(days=30)
    grand_total_kilos = 0
    needs_attention = []

    for farm in farms:
        records = PluckingRecord.query.filter_by(farm_id=farm.id).order_by(PluckingRecord.date.desc()).all()
        total_kilos = sum(r.kilos for r in records)
        grand_total_kilos += total_kilos
        last_pluck = records[0].date.strftime("%d %b") if records else "never recorded"

        last_30_kilos = sum(r.kilos for r in records if r.date >= cutoff_30)

        pruning = PruningRecord.query.filter_by(farm_id=farm.id).order_by(PruningRecord.date.desc()).first()
        last_prune = pruning.date.strftime("%d %b") if pruning else "never recorded"

        lines.append(
            f"\n📍 {farm.farm_number}{' (' + farm.location + ')' if farm.location else ''}: "
            f"{total_kilos:.1f} kg all-time, {last_30_kilos:.1f} kg in the last 30 days. "
            f"Last plucked: {last_pluck}. Last pruned: {last_prune}. "
            f"Approx. {farm.approx_bushes or 0} bushes."
        )

        if not records:
            needs_attention.append(f"no plucking recorded yet on {farm.farm_number}")
        if not pruning:
            needs_attention.append(f"no pruning recorded yet on {farm.farm_number}")

    tools_needing_repair = Tool.query.filter(
        Tool.farmer_id == farmer.id, Tool.status.in_(["needs_repair", "damaged"])
    ).count()
    if tools_needing_repair:
        needs_attention.append(f"{tools_needing_repair} tool(s) marked needs-repair or damaged")

    notes_count = Note.query.filter_by(farmer_id=farmer.id).count()

    lines.append(f"\n💰 Grand total across all farms: {grand_total_kilos:.1f} kg. You have {notes_count} saved note(s).")

    if needs_attention:
        lines.append("\n⚠️ Worth a look: " + "; ".join(needs_attention) + ".")
    else:
        lines.append("\n✅ Everything looks up to date — nice work keeping your records current.")

    return "".join(lines) if len(lines) == 1 else "\n".join(lines)


def get_bot_reply(message: str, farmer=None) -> str:
    text = message.lower().strip()

    if any(k in text for k in OVERVIEW_KEYWORDS) and farmer is not None:
        return build_farm_overview(farmer)

    if any(g in text for g in GREETINGS) and len(text) < 20:
        return "Habari! I'm your tea farm assistant. Ask me about pruning, plucking, pests, tools, or say 'overview' for a summary of your farm records."

    for keywords, answer in KNOWLEDGE_BASE:
        if any(k in text for k in keywords):
            return answer

    return FALLBACK


@chatbot_bp.route("/chatbot/ask", methods=["POST"])
@login_required
def ask():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    if not message.strip():
        return jsonify({"reply": "Please type a question."})
    return jsonify({"reply": get_bot_reply(message, farmer=current_user), "user": current_user.full_name.split(" ")[0]})
