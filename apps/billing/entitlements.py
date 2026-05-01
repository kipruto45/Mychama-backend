"""
Billing Entitlements Matrix

Defines the feature-to-plan mapping for the Chama platform.
"""

# Feature keys (must match Plan.features JSON structure)
ENTITLEMENTS = {
    # Core features (always available)
    'billing_enabled': True,

    # Core starter capabilities
    'contributions_basic': True,
    'mpesa_stk': True,
    'meeting_scheduler': True,
    'full_finance_management': False,
    'notifications_access': False,
    'messaging_access': False,
    'governance_tools': False,
    'fines_management': False,
    
    # Export features
    'exports_pdf': False,
    'exports_excel': False,
    
    # Reports
    'advanced_reports': False,
    'audit_explorer': False,
    
    # Notifications
    'scheduled_notifications': False,
    'notification_templates': False,
    'broadcast_notifications': False,
    
    # AI
    'ai_basic': True,  # Basic chat for FREE
    'ai_advanced': False,
    
    # Automations
    'automations_read': False,
    'automations_write': False,
    
    # Security
    'security_2fa': True,
    'sessions_management': True,
    
    # Finance
    'reconciliation_dashboard': False,
    
    # Governance
    'role_delegation': False,
    
    # Limits
    'seat_limit': 25,
    'sms_limit': 50,
    'otp_sms_limit': 100,
    'monthly_stk_limit': 100,
    'storage_limit_mb': 250,
    'support_level': 'community',
}


# Plan matrices (exact as specified in requirements)
PLAN_ENTITLEMENTS = {
    'FREE': {
        # Core
        'billing_enabled': True,
        'contributions_basic': True,
        'mpesa_stk': True,
        'meeting_scheduler': True,
        'full_finance_management': False,
        'notifications_access': False,
        'messaging_access': False,
        'governance_tools': False,
        'fines_management': False,
        
        # Exports - NO
        'exports_pdf': False,
        'exports_excel': False,
        
        # Reports - Basic only
        'advanced_reports': False,
        'audit_explorer': False,
        
        # Notifications - Basic only
        'scheduled_notifications': False,
        'notification_templates': False,
        'broadcast_notifications': False,
        
        # AI - Basic only
        'ai_basic': True,
        'ai_advanced': False,
        
        # Automations - NO
        'automations_read': False,
        'automations_write': False,
        
        # Security - Recommended
        'security_2fa': True,
        'sessions_management': True,
        
        # Finance
        'reconciliation_dashboard': False,
        
        # Governance
        'role_delegation': False,
        
        # Limits
        'seat_limit': 25,
        'sms_limit': 50,
        'otp_sms_limit': 100,
        'monthly_stk_limit': 100,
        'storage_limit_mb': 250,
        'support_level': 'community',
    },
    
    'PRO': {
        # Core
        'billing_enabled': True,
        'contributions_basic': True,
        'mpesa_stk': True,
        'meeting_scheduler': True,
        'full_finance_management': True,
        'notifications_access': True,
        'messaging_access': True,
        'governance_tools': True,
        'fines_management': True,
        
        # Exports - YES
        'exports_pdf': True,
        'exports_excel': True,
        
        # Reports - Full
        'advanced_reports': True,
        'audit_explorer': True,
        
        # Notifications - Full
        'scheduled_notifications': True,
        'notification_templates': True,
        'broadcast_notifications': True,
        
        # AI - Full
        'ai_basic': True,
        'ai_advanced': True,
        
        # Automations - Full
        'automations_read': True,
        'automations_write': True,
        
        # Security - Full
        'security_2fa': True,
        'sessions_management': True,
        
        # Finance - Full
        'reconciliation_dashboard': True,
        
        # Governance - Full
        'role_delegation': True,
        
        # Limits
        'seat_limit': 150,
        'sms_limit': 1000,
        'otp_sms_limit': 2500,
        'monthly_stk_limit': 2500,
        'storage_limit_mb': 2000,
        'support_level': 'email',
    },
    
    'ENTERPRISE': {
        # Core
        'billing_enabled': True,
        'contributions_basic': True,
        'mpesa_stk': True,
        'meeting_scheduler': True,
        'full_finance_management': True,
        'notifications_access': True,
        'messaging_access': True,
        'governance_tools': True,
        'fines_management': True,
        
        # Exports - YES
        'exports_pdf': True,
        'exports_excel': True,
        
        # Reports - Full
        'advanced_reports': True,
        'audit_explorer': True,
        
        # Notifications - Full
        'scheduled_notifications': True,
        'notification_templates': True,
        'broadcast_notifications': True,
        
        # AI - Full
        'ai_basic': True,
        'ai_advanced': True,
        
        # Automations - Full
        'automations_read': True,
        'automations_write': True,
        
        # Security - Full
        'security_2fa': True,
        'sessions_management': True,
        
        # Finance - Full
        'reconciliation_dashboard': True,
        
        # Governance - Full
        'role_delegation': True,
        
        # Future features
        'webhooks': True,
        'integrations_hub': True,
        'document_center': True,
        'approval_workflows_advanced': True,
        
        # Limits - Unlimited
        'seat_limit': 99999,  # Unlimited
        'sms_limit': 25000,
        'otp_sms_limit': 50000,
        'monthly_stk_limit': 50000,
        'storage_limit_mb': 20000,
        'support_level': 'priority',
    },
}


# Feature descriptions for UI
FEATURE_DESCRIPTIONS = {
    'contributions_basic': {
        'name': 'Basic Contributions Tracking',
        'description': 'Track contribution types, member contributions, goals, and wallet balances',
    },
    'mpesa_stk': {
        'name': 'M-Pesa STK Push',
        'description': 'Collect member payments through M-Pesa STK push flows',
    },
    'meeting_scheduler': {
        'name': 'Meeting Scheduler',
        'description': 'Create, manage, and follow up on chama meetings',
    },
    'full_finance_management': {
        'name': 'Full Finance Management',
        'description': 'Unlock loans, penalties, month close, ledgers, and advanced finance operations',
    },
    'notifications_access': {
        'name': 'Notifications Workspace',
        'description': 'Access notification management, scheduling, broadcast, and inbox APIs',
    },
    'messaging_access': {
        'name': 'Messaging Workspace',
        'description': 'Access team conversations, announcements, and message moderation',
    },
    'governance_tools': {
        'name': 'Governance Tools',
        'description': 'Access rules, approvals, role changes, and governance dashboards',
    },
    'fines_management': {
        'name': 'Fines Management',
        'description': 'Access fine rules, fine issuance, waivers, and fine collections',
    },
    'exports_pdf': {
        'name': 'PDF Exports',
        'description': 'Export reports and data to PDF format',
    },
    'exports_excel': {
        'name': 'Excel Exports',
        'description': 'Export reports and data to Excel format',
    },
    'advanced_reports': {
        'name': 'Advanced Reports',
        'description': 'Access detailed financial and membership reports',
    },
    'audit_explorer': {
        'name': 'Audit Explorer',
        'description': 'View and export audit logs with advanced filters',
    },
    'scheduled_notifications': {
        'name': 'Scheduled Notifications',
        'description': 'Schedule notifications to be sent later',
    },
    'notification_templates': {
        'name': 'Notification Templates',
        'description': 'Create and manage reusable notification templates',
    },
    'broadcast_notifications': {
        'name': 'Broadcast Notifications',
        'description': 'Send notifications to all members at once',
    },
    'ai_basic': {
        'name': 'AI Basic',
        'description': 'Access basic AI chat and status features',
    },
    'ai_advanced': {
        'name': 'AI Advanced',
        'description': 'Access AI predictions, forecasts, and governance scores',
    },
    'automations_read': {
        'name': 'View Automations',
        'description': 'View available automation workflows',
    },
    'automations_write': {
        'name': 'Manage Automations',
        'description': 'Create, edit, and delete automation workflows',
    },
    'security_2fa': {
        'name': 'Two-Factor Authentication',
        'description': 'Enable 2FA for enhanced account security',
    },
    'sessions_management': {
        'name': 'Session Management',
        'description': 'View and manage active sessions',
    },
    'reconciliation_dashboard': {
        'name': 'Reconciliation Dashboard',
        'description': 'View M-Pesa logs and reconciliation status',
    },
    'role_delegation': {
        'name': 'Role Delegation',
        'description': 'Delegate roles to other members',
    },
    'sms_limit': {
        'name': 'SMS Limit',
        'description': 'Maximum outbound reminder and notification messages per billing cycle',
    },
    'otp_sms_limit': {
        'name': 'OTP SMS Limit',
        'description': 'Maximum security and OTP messages per billing cycle, tracked separately from general notifications',
    },
    'monthly_stk_limit': {
        'name': 'Monthly STK Limit',
        'description': 'Maximum M-Pesa STK requests the chama can initiate each billing cycle',
    },
    'webhooks': {
        'name': 'Webhooks',
        'description': 'Configure webhooks for system integrations',
    },
    'integrations_hub': {
        'name': 'Integrations Hub',
        'description': 'Access third-party integrations',
    },
    'document_center': {
        'name': 'Document Center',
        'description': 'Centralized document storage and management',
    },
    'approval_workflows_advanced': {
        'name': 'Advanced Approval Workflows',
        'description': 'Multi-approver workflow configurations',
    },
}


def get_plan_entitlements(plan_code: str) -> dict:
    """Get entitlements for a specific plan"""
    return PLAN_ENTITLEMENTS.get(plan_code, PLAN_ENTITLEMENTS['FREE'])


def get_all_features() -> list:
    """Get all available features with descriptions"""
    features = []
    for key, desc in FEATURE_DESCRIPTIONS.items():
        features.append({
            'key': key,
            'name': desc['name'],
            'description': desc['description'],
            # Check which plans have this feature
            'free': PLAN_ENTITLEMENTS['FREE'].get(key, False),
            'pro': PLAN_ENTITLEMENTS['PRO'].get(key, False),
            'enterprise': PLAN_ENTITLEMENTS['ENTERPRISE'].get(key, False),
        })
    return features
