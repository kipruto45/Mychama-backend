from django.urls import path

from apps.accounts.kyc.views import (
    KYCDocumentDownloadView,
    KYCDetailsView,
    KYCLocationView,
    KYCReasonsView,
    KYCReTriggerView,
    KYCResubmitView,
    KYCStartView,
    KYCStatusView,
    KYCSubmitView,
    KYCUploadDocumentView,
    KYCUploadSelfieView,
)
from apps.accounts.kyc.webhooks import SmileIdentityWebhookView

urlpatterns = [
    path("status/", KYCStatusView.as_view(), name="kyc-status"),
    path("documents/<uuid:kyc_id>/<str:document_role>/download/", KYCDocumentDownloadView.as_view(), name="kyc-document-download"),
    path("start/", KYCStartView.as_view(), name="kyc-start"),
    path("details/", KYCDetailsView.as_view(), name="kyc-details"),
    path("upload-document/", KYCUploadDocumentView.as_view(), name="kyc-upload-document"),
    path("upload-selfie/", KYCUploadSelfieView.as_view(), name="kyc-upload-selfie"),
    path("submit/", KYCSubmitView.as_view(), name="kyc-submit"),
    path("resubmit/", KYCResubmitView.as_view(), name="kyc-resubmit"),
    path("location/", KYCLocationView.as_view(), name="kyc-location"),
    path("reasons/", KYCReasonsView.as_view(), name="kyc-reasons"),
    path("retrigger/", KYCReTriggerView.as_view(), name="kyc-retrigger"),
    path("provider/webhook/", SmileIdentityWebhookView.as_view(), name="kyc-provider-webhook"),
]
