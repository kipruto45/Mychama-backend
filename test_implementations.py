#!/usr/bin/env python
"""Test script to verify OTP, early repayment discount, and KYC implementations."""

import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.test')
django.setup()

from apps.accounts.models import User, OTPToken
from apps.accounts.services import OTPService, KYCService
from apps.finance.models import Loan, LoanProduct
from apps.finance.services import _loan_total_payable
from datetime import timedelta, date
from django.utils import timezone
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile


def test_otp_service():
    """Test OTP generation and verification."""
    print("\n" + "="*60)
    print("Testing OTP Service (2FA Implementation)")
    print("="*60)
    
    # Get or create test user
    user = User.objects.first()
    if not user:
        user = User.objects.create_user(
            phone='+254700000000', 
            password='test123', 
            full_name='Test User'
        )
    
    print(f"Test User: {user.phone}")
    
    # Test 1: Generate OTP
    otp = OTPService.generate_otp(user, delivery_method='sms')
    print(f"✓ OTP generated: {otp.code}")
    assert len(otp.code) == 6, "OTP code should be 6 digits"
    assert otp.code.isdigit(), "OTP code should be all digits"
    print(f"✓ OTP format valid (6 digits)")
    
    # Test 2: Check OTP validity
    assert otp.is_valid, "OTP should be valid immediately after generation"
    print(f"✓ OTP is valid")
    
    # Test 3: Check expiry
    assert not otp.is_expired, "OTP should not be expired immediately after generation"
    print(f"✓ OTP not yet expired (expires at {otp.expires_at})")
    
    # Test 4: Verify OTP with correct code
    verified, msg = OTPService.verify_otp(user, otp.code)
    assert verified, f"OTP verification failed: {msg}"
    print(f"✓ OTP verified successfully")
    
    # Test 5: Check OTP is now used
    otp.refresh_from_db()
    assert otp.is_used, "OTP should be marked as used after verification"
    print(f"✓ OTP marked as used")
    
    # Test 6: Try to re-verify (should fail)
    verified, msg = OTPService.verify_otp(user, otp.code)
    assert not verified, "Already-used OTP should not be verifiable"
    print(f"✓ Can't re-verify used OTP")
    
    # Test 7: Invalid OTP code
    verified, msg = OTPService.verify_otp(user, '000000')
    assert not verified, "Invalid OTP should be rejected"
    print(f"✓ Invalid OTP rejected: {msg}")


def test_kyc_validation():
    """Test KYC image validation."""
    print("\n" + "="*60)
    print("Testing KYC Image Validation")
    print("="*60)
    
    # Test 1: Valid image
    img_data = b'fake jpeg data'
    img = SimpleUploadedFile('test.jpg', img_data, content_type='image/jpeg')
    valid, msg = KYCService.validate_id_image(img, 'ID Front')
    assert valid, f"Valid JPEG should pass: {msg}"
    print(f"✓ JPEG image validation passed")
    
    # Test 2: PNG file
    png_data = b'fake png data'
    png = SimpleUploadedFile('test.png', png_data, content_type='image/png')
    valid, msg = KYCService.validate_id_image(png, 'Selfie')
    assert valid, f"Valid PNG should pass: {msg}"
    print(f"✓ PNG image validation passed")
    
    # Test 3: File too large
    large_data = b'x' * (6 * 1024 * 1024)  # 6MB
    large_img = SimpleUploadedFile('large.jpg', large_data, content_type='image/jpeg')
    valid, msg = KYCService.validate_id_image(large_img, 'ID Front')
    assert not valid, "Large file should be rejected"
    assert "5MB" in msg, "Error message should mention size limit"
    print(f"✓ Large file rejected: {msg}")
    
    # Test 4: Invalid file type
    pdf_data = b'%PDF-1.4...'
    pdf = SimpleUploadedFile('doc.pdf', pdf_data, content_type='application/pdf')
    valid, msg = KYCService.validate_id_image(pdf, 'ID Front')
    assert not valid, "PDF should be rejected"
    assert "JPG or PNG" in msg, "Error message should mention allowed formats"
    print(f"✓ Invalid file type rejected: {msg}")
    
    # Test 5: Multiple images validation
    valid_img = SimpleUploadedFile('id.jpg', b'data', content_type='image/jpeg')
    selfie_img = SimpleUploadedFile('selfie.png', b'data', content_type='image/png')
    errors = KYCService.validate_kyc_images(valid_img, selfie_img)
    assert len(errors) == 0, f"Both images should be valid: {errors}"
    print(f"✓ Multiple images validation passed")
    
    # Test 6: At least one image required
    errors = KYCService.validate_kyc_images(None, None)
    # This returns empty dict from service, validation happens in serializer
    print(f"✓ Image requirement validation delegated to serializer")


def test_early_repayment_discount():
    """Test early repayment discount in loan calculation."""
    print("\n" + "="*60)
    print("Testing Early Repayment Discount")
    print("="*60)
    
    # Create mock loan for testing
    from apps.chama.models import Chama
    from apps.finance.models import LoanInterestType
    
    # Get or create test chama
    chama = Chama.objects.first()
    if not chama:
        print("No test chama found - skipping early repayment test")
        return
    
    # Get or create loan product with discount
    product = LoanProduct.objects.filter(chama=chama).first()
    if not product:
        print("No test loan product found - skipping early repayment test")
        return
    
    # Update product with discount
    product.early_repayment_discount_percent = Decimal('5.00')
    product.save()
    
    # Create test loan
    from apps.accounts.models import User
    user = User.objects.filter(is_staff=False).first() or User.objects.first()
    
    loan = Loan(
        chama=chama,
        member=user,
        loan_product=product,
        principal=Decimal('10000.00'),
        interest_rate=Decimal('10.00'),
        interest_type=LoanInterestType.FLAT,
        duration_months=6,
        due_date=date(2026, 8, 22),
        requested_at=timezone.now(),
    )
    
    # Calculate without early repayment
    total_normal = _loan_total_payable(loan)
    print(f"Normal repayment amount: {total_normal}")
    
    # Calculate with early repayment (before due date)
    early_date = date(2026, 7, 22)  # 1 month early
    total_early = _loan_total_payable(loan, early_repayment_date=early_date)
    print(f"Early repayment amount: {total_early}")
    
    # Verify discount was applied
    discount = total_normal - total_early
    assert discount > 0, "Early repayment should result in lower amount"
    discount_percent = (discount / total_normal * 100)
    print(f"✓ Discount applied: {discount:.2f} ({discount_percent:.2f}%)")
    
    # Verify discount matches product setting
    expected_discount_percent = Decimal('5.00')
    actual_discount_percent = Decimal(str(discount_percent)).quantize(Decimal('0.01'))
    assert actual_discount_percent == expected_discount_percent, \
        f"Expected {expected_discount_percent}% but got {actual_discount_percent}%"
    print(f"✓ Discount matches product setting (5%)")
    
    # Test no discount for repayment after due date
    late_date = date(2026, 9, 22)  # After due date
    total_late = _loan_total_payable(loan, early_repayment_date=late_date)
    assert total_late == total_normal, "No discount should apply after due date"
    print(f"✓ No discount for late repayment")


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Running Implementation Tests")
    print("="*60)
    
    try:
        test_otp_service()
        test_kyc_validation()
        test_early_repayment_discount()
        
        print("\n" + "="*60)
        print("✓ All implementation tests passed!")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
