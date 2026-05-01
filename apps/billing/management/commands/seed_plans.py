"""
Management command to seed billing plans
"""
from django.core.management.base import BaseCommand

from apps.billing.entitlements import PLAN_ENTITLEMENTS
from apps.billing.models import Plan


class Command(BaseCommand):
    help = 'Seed billing plans (FREE, PRO, ENTERPRISE)'
    
    def handle(self, *args, **options):
        plans_data = [
            {
                'code': 'FREE',
                'name': 'Free Trial',
                'description': 'Included automatically for every new chama during the initial 30-day trial window.',
                'monthly_price': 0,
                'yearly_price': 0,
                'features': PLAN_ENTITLEMENTS['FREE'],
                'is_active': True,
                'is_featured': False,
                'sort_order': 1,
            },
            {
                'code': 'PRO',
                'name': 'Pro',
                'description': 'For growing chamas that need advanced features, exports, and automation.',
                'monthly_price': 4999,
                'yearly_price': 49990,
                'features': PLAN_ENTITLEMENTS['PRO'],
                'is_active': True,
                'is_featured': True,
                'sort_order': 2,
                # Stripe price IDs would be added here in production
                'stripe_monthly_price_id': 'price_pro_monthly',
                'stripe_yearly_price_id': 'price_pro_yearly',
            },
            {
                'code': 'ENTERPRISE',
                'name': 'Enterprise',
                'description': 'For large chamas requiring unlimited members, priority support, and custom integrations.',
                'monthly_price': 19999,
                'yearly_price': 199990,
                'features': PLAN_ENTITLEMENTS['ENTERPRISE'],
                'is_active': True,
                'is_featured': False,
                'sort_order': 3,
                'stripe_monthly_price_id': 'price_enterprise_monthly',
                'stripe_yearly_price_id': 'price_enterprise_yearly',
            },
        ]
        
        created_count = 0
        updated_count = 0
        
        for plan_data in plans_data:
            features = plan_data.pop('features', {})
            
            plan, created = Plan.objects.update_or_create(
                code=plan_data['code'],
                defaults={
                    **plan_data,
                    'features': features,
                }
            )
            
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'Created plan: {plan.name}'))
            else:
                updated_count += 1
                self.stdout.write(f'Updated plan: {plan.name}')
        
        self.stdout.write(self.style.SUCCESS(
            f'\nSeeded {created_count} new plans, updated {updated_count} existing plans'
        ))
        
        # Display plan summary
        self.stdout.write('\n--- Current Plans ---')
        for plan in Plan.objects.filter(is_active=True).order_by('sort_order'):
            price = f'KES {plan.monthly_price:,.0f}/mo' if plan.monthly_price else 'Free'
            featured = ' (FEATURED)' if plan.is_featured else ''
            self.stdout.write(f'  {plan.code}: {plan.name} - {price}{featured}')
        
        self.stdout.write(self.style.SUCCESS('\nDone!'))
