from __future__ import annotations

import io

from .unified_models import PaymentReceipt


def render_receipt_pdf(*, receipt: PaymentReceipt) -> bytes:
    """
    Render a receipt PDF.

    Tries `reportlab` when available; falls back to a minimal dependency-free PDF
    generator to keep local/dev environments and tests working without optional
    binary dependencies.
    """

    def _pdf_escape(value: str) -> str:
        return (
            str(value or "")
            .replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .replace("\n", " ")
            .replace("\r", " ")
        )

    def _render_minimal_pdf(lines: list[str]) -> bytes:
        # Minimal PDF with a single page and Helvetica text.
        content_lines = ["BT", "/F1 12 Tf"]
        start_y = 760
        line_height = 16
        for idx, line in enumerate(lines):
            y = start_y - idx * line_height
            if y < 50:
                break
            content_lines.append(f"1 0 0 1 50 {y} Tm ({_pdf_escape(line)}) Tj")
        content_lines.append("ET")
        stream = ("\n".join(content_lines) + "\n").encode("latin-1", errors="replace")

        objects: list[bytes] = []
        objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
        objects.append(b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        )
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

        pdf = bytearray()
        pdf.extend(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(f"{index} 0 obj\n".encode("ascii"))
            pdf.extend(obj)
            pdf.extend(b"\nendobj\n")

        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        pdf.extend(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            pdf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
        pdf.extend(b"trailer\n")
        pdf.extend(f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode("ascii"))
        pdf.extend(b"startxref\n")
        pdf.extend(f"{xref_offset}\n".encode("ascii"))
        pdf.extend(b"%%EOF\n")
        return bytes(pdf)

    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas
    except ImportError:
        intent = receipt.payment_intent
        chama = getattr(intent, "chama", None)
        member = getattr(intent, "user", None)
        issued = getattr(receipt, "issued_at", None)
        issued_str = issued.strftime("%Y-%m-%d %H:%M") if issued else ""

        chama_name = getattr(chama, "name", "") if chama else ""
        member_name = getattr(member, "full_name", "") if member else ""
        member_phone = getattr(member, "phone", "") if member else ""
        masked_phone = member_phone
        if member_phone and len(member_phone) >= 7:
            masked_phone = f"{member_phone[:6]}***{member_phone[-2:]}"

        lines = [
            "MyChama Receipt",
            f"Chama: {chama_name}" if chama_name else "Chama: —",
            f"Receipt: {receipt.receipt_number}",
            f"Reference: {receipt.reference_number}",
            f"Amount: {receipt.amount} {receipt.currency}",
            f"Method: {receipt.payment_method}",
            f"Issued: {issued_str}" if issued_str else "Issued: —",
            f"Member: {member_name}" if member_name else "Member: —",
            f"Phone: {masked_phone}" if masked_phone else "Phone: —",
            "Keep this receipt for your records.",
        ]
        return _render_minimal_pdf(lines)

    intent = receipt.payment_intent
    chama = getattr(intent, "chama", None)
    member = getattr(intent, "user", None)

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 22 * mm
    c.setFont("Helvetica-Bold", 16)
    c.drawString(22 * mm, y, "MyChama Receipt")

    y -= 10 * mm
    c.setFont("Helvetica", 10)
    chama_name = getattr(chama, "name", "") if chama else ""
    if chama_name:
        c.drawString(22 * mm, y, f"Chama: {chama_name}")
        y -= 6 * mm

    c.drawString(22 * mm, y, f"Receipt: {receipt.receipt_number}")
    y -= 6 * mm
    c.drawString(22 * mm, y, f"Reference: {receipt.reference_number}")
    y -= 6 * mm
    c.drawString(22 * mm, y, f"Amount: {receipt.amount} {receipt.currency}")
    y -= 6 * mm
    c.drawString(22 * mm, y, f"Method: {receipt.payment_method}")
    y -= 6 * mm
    c.drawString(22 * mm, y, f"Issued: {receipt.issued_at:%Y-%m-%d %H:%M}")
    y -= 10 * mm

    if member:
        name = getattr(member, "full_name", "") or ""
        phone = getattr(member, "phone", "") or ""
        if name:
            c.drawString(22 * mm, y, f"Member: {name}")
            y -= 6 * mm
        if phone:
            masked = phone
            if len(phone) >= 7:
                masked = f"{phone[:6]}***{phone[-2:]}"
            c.drawString(22 * mm, y, f"Phone: {masked}")
            y -= 6 * mm

    c.setFont("Helvetica", 9)
    y -= 6 * mm
    c.drawString(
        22 * mm,
        y,
        "Keep this receipt for your records. If you need support, share the receipt number.",
    )

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()

