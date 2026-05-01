"""
Invoice generation and delivery for billing.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.utils import timezone
try:
    # Optional dependency: reportlab is only required when generating PDF invoices.
    from reportlab.lib.pagesizes import A4  # type: ignore
    from reportlab.pdfgen import canvas  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    A4 = None
    canvas = None

from .credits import apply_reserved_credits, release_reserved_credits
from .models import Invoice, InvoiceLineItem
from .security import encrypt_billing_metadata


def _tax_rate() -> Decimal:
    return Decimal(str(getattr(settings, 'BILLING_TAX_RATE', '0.00')))


def generate_invoice_number() -> str:
    stamp = timezone.now().strftime('%Y%m%d%H%M%S')
    suffix = str(Invoice.objects.count() + 1).zfill(6)
    return f'INV-{stamp}-{suffix}'


def create_invoice(
    *,
    chama,
    plan,
    amount: Decimal,
    provider: str,
    billing_cycle: str,
    customer_email: str = '',
    subscription=None,
    payment_reference: str = '',
    provider_transaction_id: str = '',
    status: str = Invoice.PENDING,
    metadata: dict | None = None,
    line_items: list[dict] | None = None,
) -> Invoice:
    subtotal = Decimal(amount).quantize(Decimal('0.01'))
    tax_amount = (subtotal * _tax_rate()).quantize(Decimal('0.01'))
    total_amount = (subtotal + tax_amount).quantize(Decimal('0.01'))
    period_start = timezone.now()
    period_end = period_start + (
        timedelta(days=365) if billing_cycle == 'yearly' else timedelta(days=30)
    )

    invoice = Invoice.objects.create(
        invoice_number=generate_invoice_number(),
        chama=chama,
        subscription=subscription,
        plan=plan,
        status=status,
        provider=provider,
        subtotal=subtotal,
        tax_amount=tax_amount,
        total_amount=total_amount,
        billing_period_start=period_start,
        billing_period_end=period_end,
        due_at=period_start,
        payment_reference=payment_reference,
        provider_transaction_id=provider_transaction_id,
        customer_email=customer_email,
        metadata_encrypted=encrypt_billing_metadata(metadata),
    )

    rows = line_items or [
        {
            'title': f'{plan.name} subscription',
            'description': f'{billing_cycle.title()} plan access for {chama.name}',
            'quantity': 1,
            'unit_price': subtotal,
            'total_price': subtotal,
            'metadata': {'billing_cycle': billing_cycle},
        }
    ]
    for row in rows:
        InvoiceLineItem.objects.create(
            invoice=invoice,
            title=row['title'],
            description=row.get('description', ''),
            quantity=row.get('quantity', 1),
            unit_price=row.get('unit_price', subtotal),
            total_price=row.get('total_price', subtotal),
            metadata=row.get('metadata', {}),
        )

    return invoice


def mark_invoice_paid(invoice: Invoice, *, payment_reference: str = '', provider_transaction_id: str = '') -> Invoice:
    apply_reserved_credits(invoice)
    invoice.status = Invoice.PAID
    invoice.amount_paid = invoice.total_amount
    invoice.payment_reference = payment_reference or invoice.payment_reference
    invoice.provider_transaction_id = provider_transaction_id or invoice.provider_transaction_id
    invoice.paid_at = timezone.now()
    invoice.save(
        update_fields=[
            'status',
            'amount_paid',
            'payment_reference',
            'provider_transaction_id',
            'paid_at',
            'updated_at',
        ]
    )
    return invoice


def mark_invoice_failed(invoice: Invoice, *, payment_reference: str = '') -> Invoice:
    release_reserved_credits(invoice)
    invoice.status = Invoice.FAILED
    invoice.payment_reference = payment_reference or invoice.payment_reference
    invoice.save(update_fields=['status', 'payment_reference', 'updated_at'])
    return invoice


def generate_invoice_pdf(invoice: Invoice) -> Invoice:
    if canvas is None or A4 is None:
        raise RuntimeError(
            "Invoice PDF generation requires the optional 'reportlab' dependency."
        )
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    pdf.setTitle(invoice.invoice_number)
    pdf.setFont('Helvetica-Bold', 16)
    pdf.drawString(48, height - 60, 'Chama Billing Invoice')
    pdf.setFont('Helvetica', 11)
    pdf.drawString(48, height - 90, f'Invoice: {invoice.invoice_number}')
    pdf.drawString(48, height - 108, f'Chama: {invoice.chama.name}')
    pdf.drawString(48, height - 126, f'Plan: {invoice.plan.name}')
    pdf.drawString(48, height - 144, f'Status: {invoice.status.title()}')
    pdf.drawString(48, height - 162, f'Provider: {invoice.provider}')
    pdf.drawString(48, height - 180, f'Period: {invoice.billing_period_start:%Y-%m-%d} to {invoice.billing_period_end:%Y-%m-%d}')

    y = height - 220
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawString(48, y, 'Line Items')
    y -= 24
    pdf.setFont('Helvetica', 10)
    for item in invoice.line_items.all():
        pdf.drawString(56, y, item.title)
        pdf.drawRightString(width - 56, y, f'KES {item.total_price:,.2f}')
        y -= 18
        if item.description:
            pdf.drawString(70, y, item.description[:80])
            y -= 16

    y -= 10
    pdf.setFont('Helvetica-Bold', 11)
    pdf.drawRightString(width - 56, y, f'Subtotal: KES {invoice.subtotal:,.2f}')
    y -= 18
    pdf.drawRightString(width - 56, y, f'Tax: KES {invoice.tax_amount:,.2f}')
    y -= 18
    pdf.drawRightString(width - 56, y, f'Total: KES {invoice.total_amount:,.2f}')
    if invoice.payment_reference:
        y -= 24
        pdf.setFont('Helvetica', 10)
        pdf.drawString(48, y, f'Payment Reference: {invoice.payment_reference}')

    pdf.showPage()
    pdf.save()

    filename = f'{invoice.invoice_number}.pdf'
    invoice.pdf_file.save(filename, ContentFile(buffer.getvalue()), save=True)
    return invoice


def send_invoice_email(invoice: Invoice) -> bool:
    if not invoice.customer_email:
        return False

    if not invoice.pdf_file:
        generate_invoice_pdf(invoice)

    email = EmailMessage(
        subject=f'Invoice {invoice.invoice_number}',
        body=(
            f'Your invoice for {invoice.plan.name} is ready.\n'
            f'Amount: KES {invoice.total_amount:,.2f}\n'
            f'Status: {invoice.status.title()}'
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'billing@chama.local'),
        to=[invoice.customer_email],
    )

    if invoice.pdf_file:
        try:
            invoice.pdf_file.open('rb')
            email.attach(
                invoice.pdf_file.name.rsplit('/', 1)[-1],
                invoice.pdf_file.read(),
                'application/pdf',
            )
        finally:
            invoice.pdf_file.close()

    try:
        email.send(fail_silently=True)
    except Exception:
        return False
    return True
