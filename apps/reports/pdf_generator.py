from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

DEFAULT_HEADER_BG = colors.HexColor("#1f2937")


class ReportPDFRenderer:
    @staticmethod
    def _base_document() -> tuple[BytesIO, SimpleDocTemplate, list, dict]:
        buffer = BytesIO()
        document = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        return buffer, document, [], styles

    @staticmethod
    def _styled_table(rows, header_bg=DEFAULT_HEADER_BG) -> Table:
        table = Table(rows, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), header_bg),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 10),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
                    ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    (
                        "ROWBACKGROUNDS",
                        (0, 1),
                        (-1, -1),
                        [colors.white, colors.HexColor("#f9fafb")],
                    ),
                ]
            )
        )
        return table

    @staticmethod
    def render_member_statement(payload: dict, *, watermark: bool = False) -> bytes:
        buffer, document, elements, styles = ReportPDFRenderer._base_document()

        elements.append(Paragraph("Member Statement", styles["Title"]))
        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(
                f"Member: {payload.get('member_name')} | Chama: {payload.get('chama_id')}",
                styles["Normal"],
            )
        )
        elements.append(
            Paragraph(
                f"Period: {payload.get('from') or 'N/A'} to {payload.get('to') or 'N/A'}",
                styles["Normal"],
            )
        )
        if watermark:
            elements.append(
                Paragraph(
                    "AUDITOR COPY",
                    styles["Heading4"],
                )
            )
        elements.append(Spacer(1, 10))

        totals = payload.get("totals", {})
        totals_rows = [
            ["Metric", "Value"],
            ["Contributions", totals.get("contributions", "0.00")],
            ["Loan Disbursements", totals.get("loan_disbursements", "0.00")],
            ["Repayments", totals.get("repayments", "0.00")],
            ["Penalties Debited", totals.get("penalties_debited", "0.00")],
            ["Penalties Credited", totals.get("penalties_credited", "0.00")],
            ["Closing Balance", totals.get("closing_balance", "0.00")],
        ]
        elements.append(ReportPDFRenderer._styled_table(totals_rows))
        elements.append(Spacer(1, 12))

        ledger_rows = [["Date", "Type", "Direction", "Amount", "Running Balance"]]
        for line in payload.get("ledger", []):
            ledger_rows.append(
                [
                    str(line.get("date", ""))[:19],
                    line.get("entry_type", ""),
                    line.get("direction", ""),
                    line.get("amount", ""),
                    line.get("running_balance", ""),
                ]
            )
        if len(ledger_rows) == 1:
            ledger_rows.append(["-", "-", "-", "-", "-"])

        elements.append(Paragraph("Ledger Timeline", styles["Heading3"]))
        elements.append(ReportPDFRenderer._styled_table(ledger_rows))

        document.build(elements)
        buffer.seek(0)
        return buffer.read()

    @staticmethod
    def render_chama_summary(payload: dict, *, watermark: bool = False) -> bytes:
        buffer, document, elements, styles = ReportPDFRenderer._base_document()

        elements.append(Paragraph("Chama Monthly Summary", styles["Title"]))
        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(
                f"Chama: {payload.get('chama_name')} | Period: {payload.get('month')}/{payload.get('year')}",
                styles["Normal"],
            )
        )
        if watermark:
            elements.append(
                Paragraph(
                    "AUDITOR COPY",
                    styles["Heading4"],
                )
            )
        elements.append(Spacer(1, 10))

        totals = payload.get("totals", {})
        cashflow = payload.get("cashflow", {})
        totals_rows = [
            ["Metric", "Value"],
            ["Contributions", totals.get("contributions", "0.00")],
            ["Repayments", totals.get("repayments", "0.00")],
            ["Penalties Issued", totals.get("penalties_issued", "0.00")],
            ["Penalties Collected", totals.get("penalties_collected", "0.00")],
            ["Loans Out", totals.get("loans_out", "0.00")],
            ["Cashflow Credits", cashflow.get("credits", "0.00")],
            ["Cashflow Debits", cashflow.get("debits", "0.00")],
            ["Net Cashflow", cashflow.get("net", "0.00")],
        ]
        elements.append(ReportPDFRenderer._styled_table(totals_rows))
        elements.append(Spacer(1, 12))

        defaulters_rows = [
            ["Member", "Phone", "Loan Status", "Outstanding", "Overdue Installments"]
        ]
        for row in payload.get("defaulters", []):
            defaulters_rows.append(
                [
                    row.get("member_name", ""),
                    row.get("member_phone", ""),
                    row.get("status", ""),
                    row.get("outstanding_balance", "0.00"),
                    str(row.get("overdue_installments", 0)),
                ]
            )
        if len(defaulters_rows) == 1:
            defaulters_rows.append(["-", "-", "-", "0.00", "0"])

        elements.append(Paragraph("Defaulters", styles["Heading3"]))
        elements.append(ReportPDFRenderer._styled_table(defaulters_rows))

        document.build(elements)
        buffer.seek(0)
        return buffer.read()

    @staticmethod
    def render_loan_schedule(payload: dict, *, watermark: bool = False) -> bytes:
        buffer, document, elements, styles = ReportPDFRenderer._base_document()

        loan = payload.get("loan", {})
        elements.append(Paragraph("Loan Repayment Schedule", styles["Title"]))
        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(
                f"Member: {loan.get('member_name')} | Loan: {loan.get('loan_id')}",
                styles["Normal"],
            )
        )
        if watermark:
            elements.append(Paragraph("AUDITOR COPY", styles["Heading4"]))
        elements.append(Spacer(1, 10))

        rows = [["Due Date", "Expected", "Principal", "Interest", "Penalty", "Status"]]
        for item in payload.get("schedule", []):
            rows.append(
                [
                    item.get("due_date", ""),
                    item.get("expected_amount", "0.00"),
                    item.get("expected_principal", "0.00"),
                    item.get("expected_interest", "0.00"),
                    item.get("expected_penalty", "0.00"),
                    item.get("status", ""),
                ]
            )
        if len(rows) == 1:
            rows.append(["-", "0.00", "0.00", "0.00", "0.00", "-"])

        elements.append(ReportPDFRenderer._styled_table(rows))
        document.build(elements)
        buffer.seek(0)
        return buffer.read()

    @staticmethod
    def render_loan_approvals_log(payload: dict, *, watermark: bool = False) -> bytes:
        buffer, document, elements, styles = ReportPDFRenderer._base_document()

        elements.append(Paragraph("Loan Approvals Log", styles["Title"]))
        elements.append(Spacer(1, 8))
        elements.append(
            Paragraph(f"Chama: {payload.get('chama_name')}", styles["Normal"])
        )
        if payload.get("month") and payload.get("year"):
            elements.append(
                Paragraph(
                    f"Period: {payload.get('month')}/{payload.get('year')}",
                    styles["Normal"],
                )
            )
        if watermark:
            elements.append(Paragraph("AUDITOR COPY", styles["Heading4"]))
        elements.append(Spacer(1, 10))

        rows = [["Loan", "Member", "Stage", "Decision", "Actor", "Acted At"]]
        for row in payload.get("rows", []):
            rows.append(
                [
                    row.get("loan_id", ""),
                    row.get("member_name", ""),
                    row.get("stage", ""),
                    row.get("decision", ""),
                    row.get("actor_name", ""),
                    str(row.get("acted_at", ""))[:19],
                ]
            )
        if len(rows) == 1:
            rows.append(["-", "-", "-", "-", "-", "-"])

        elements.append(ReportPDFRenderer._styled_table(rows))
        document.build(elements)
        buffer.seek(0)
        return buffer.read()
