"""
Backfill wallets command.

Creates wallets for existing users and chamas that don't have wallets yet.
Ensures all entities have proper wallet references for financial operations.
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import User
from apps.chama.models import Chama
from apps.finance.models import Wallet, WalletOwnerType


class Command(BaseCommand):
    help = "Backfill wallets for existing users and chamas"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without making changes",
        )
        parser.add_argument(
            "--chamas-only",
            action="store_true",
            help="Only create chama wallets",
        )
        parser.add_argument(
            "--users-only",
            action="store_true",
            help="Only create user wallets",
        )
        parser.add_argument(
            "--currency",
            type=str,
            default="KES",
            help="Default currency for wallets",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        chamas_only = options["chamas_only"]
        users_only = options["users_only"]
        currency = options["currency"]

        if chamas_only and users_only:
            raise CommandError("Cannot specify both --chamas-only and --users-only")

        results = {
            "chamas_created": 0,
            "chamas_skipped": 0,
            "users_created": 0,
            "users_skipped": 0,
        }

        # Get existing wallet owner IDs
        existing_chama_ids = set(
            Wallet.objects.filter(owner_type=WalletOwnerType.CHAMA)
            .values_list("owner_id", flat=True)
        )
        existing_user_ids = set(
            Wallet.objects.filter(owner_type=WalletOwnerType.USER)
            .values_list("owner_id", flat=True)
        )

        # Create missing chama wallets
        if not users_only:
            chama_ids = set(Chama.objects.values_list("id", flat=True))
            missing_chama_ids = chama_ids - existing_chama_ids

            self.stdout.write(f"Found {len(missing_chama_ids)} missing chama wallets")

            for chama_id in missing_chama_ids:
                if dry_run:
                    results["chamas_created"] += 1
                else:
                    try:
                        with transaction.atomic():
                            Wallet.objects.create(
                                owner_type=WalletOwnerType.CHAMA,
                                owner_id=chama_id,
                                available_balance=Decimal("0.00"),
                                locked_balance=Decimal("0.00"),
                                currency=currency,
                            )
                            results["chamas_created"] += 1
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"Error creating wallet for chama {chama_id}: {e}")
                        )
                        results["chamas_skipped"] += 1

            results["chamas_skipped"] += len(existing_chama_ids)

        # Create missing user wallets
        if not chamas_only:
            user_ids = set(User.objects.values_list("id", flat=True))
            missing_user_ids = user_ids - existing_user_ids

            self.stdout.write(f"Found {len(missing_user_ids)} missing user wallets")

            for user_id in missing_user_ids:
                if dry_run:
                    results["users_created"] += 1
                else:
                    try:
                        with transaction.atomic():
                            Wallet.objects.create(
                                owner_type=WalletOwnerType.USER,
                                owner_id=user_id,
                                available_balance=Decimal("0.00"),
                                locked_balance=Decimal("0.00"),
                                currency=currency,
                            )
                            results["users_created"] += 1
                    except Exception as e:
                        self.stdout.write(
                            self.style.ERROR(f"Error creating wallet for user {user_id}: {e}")
                        )
                        results["users_skipped"] += 1

            results["users_skipped"] += len(existing_user_ids)

        # Summary
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - No changes made"))
        
        self.stdout.write("\n" + "=" * 50)
        self.stdout.write("SUMMARY")
        self.stdout.write("=" * 50)
        
        if not users_only:
            self.stdout.write(
                f"Chamas: {results['chamas_created']} created, "
                f"{results['chamas_skipped']} already exist"
            )
        
        if not chamas_only:
            self.stdout.write(
                f"Users: {results['users_created']} created, "
                f"{results['users_skipped']} already exist"
            )

        total_created = results["chamas_created"] + results["users_created"]
        if total_created > 0 and not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nSuccessfully created {total_created} wallets"))
        elif total_created == 0 and not dry_run:
            self.stdout.write(self.style.SUCCESS("\nAll wallets already exist"))
