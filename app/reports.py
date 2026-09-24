"""PDF reports. Kept separate from the blueprints so any screen can reuse a builder."""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.timeutil import to_eat

INK = colors.HexColor("#111827")
LINE = colors.HexColor("#e5e7eb")
ZEBRA = colors.HexColor("#f9fafb")


def build_farmer_transactions_pdf(factory_name, farmer, transactions, start, end):
    """A farmer's tea-delivery transactions between start and end (dates), as PDF bytes."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title="Tea delivery transactions",
        topMargin=18 * mm, bottomMargin=18 * mm, leftMargin=16 * mm, rightMargin=16 * mm,
    )
    styles = getSampleStyleSheet()

    story = [
        Paragraph(factory_name, styles["Title"]),
        Paragraph("Tea delivery transactions", styles["Heading2"]),
        Paragraph(f"{farmer.full_name} &nbsp;&middot;&nbsp; {farmer.farmer_number} "
                  f"&nbsp;&middot;&nbsp; {farmer.buying_centre.name}", styles["Normal"]),
        Paragraph(f"{start.strftime('%d %b %Y')} &ndash; {end.strftime('%d %b %Y')}", styles["Normal"]),
        Spacer(1, 8 * mm),
    ]

    rows = [["When", "Transaction", "Centre", "Kilos", "Status"]]
    valid_total = 0.0
    for t in transactions:
        if t.status == "VALID":
            valid_total += t.weight_kg
        rows.append([
            to_eat(t.transaction_time).strftime("%d %b %Y, %H:%M"),
            t.transaction_number,
            t.buying_centre.name,
            f"{t.weight_kg:.1f}",
            "Voided" if t.status == "VOIDED" else "Valid",
        ])
    if len(rows) == 1:
        rows.append(["No tea delivered in this period.", "", "", "", ""])

    table = Table(rows, colWidths=[36 * mm, 32 * mm, 38 * mm, 20 * mm, 22 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, ZEBRA]),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(table)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(f"Valid kilos in this period: <b>{valid_total:.1f} kg</b>", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()
