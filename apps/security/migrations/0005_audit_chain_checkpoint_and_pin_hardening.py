import hashlib
import json
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


GENESIS_HASH = "0" * 64


def _compute_event_hash(prev_hash: str, payload: dict) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{prev_hash}:{serialized}".encode("utf-8")).hexdigest()


def backfill_audit_chain(apps, schema_editor):
    AuditLog = apps.get_model("security", "AuditLog")

    previous_hash = GENESIS_HASH
    records = AuditLog.objects.order_by("created_at", "id")
    for index, record in enumerate(records.iterator(), start=1):
        payload = {
            "action_type": record.action_type,
            "target_type": record.target_type,
            "target_id": record.target_id,
            "actor_id": str(record.actor_id) if record.actor_id else None,
            "chama_id": str(record.chama_id) if record.chama_id else None,
            "metadata": record.metadata or {},
            "ip_address": record.ip_address,
            "trace_id": getattr(record, "trace_id", "") or "",
            "created_at": record.created_at.isoformat(),
        }
        event_hash = _compute_event_hash(previous_hash, payload)
        AuditLog.objects.filter(pk=record.pk).update(
            chain_index=index,
            prev_hash=previous_hash,
            event_hash=event_hash,
        )
        previous_hash = event_hash


class Migration(migrations.Migration):
    dependencies = [
        ("security", "0004_refreshtokenrecord"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="trace_id",
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="chain_index",
            field=models.PositiveBigIntegerField(db_index=True, default=0),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="auditlog",
            name="prev_hash",
            field=models.CharField(default=GENESIS_HASH, max_length=64),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="event_hash",
            field=models.CharField(db_index=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.CreateModel(
            name="MemberPinSecret",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        serialize=False,
                        editable=False,
                        default=uuid.uuid4,
                    ),
                ),
                (
                    "pin_type",
                    models.CharField(
                        choices=[
                            ("transaction", "Transaction PIN"),
                            ("withdrawal", "Withdrawal PIN"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("pin_hash", models.CharField(blank=True, default="", max_length=255)),
                ("salt", models.CharField(blank=True, default="", max_length=32)),
                ("failed_attempts", models.PositiveIntegerField(default=0)),
                ("lockout_level", models.PositiveSmallIntegerField(default=0)),
                ("is_locked", models.BooleanField(default=False)),
                ("locked_until", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("rotated_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="pin_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="pin_secrets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
                "unique_together": {("user", "pin_type")},
            },
        ),
        migrations.CreateModel(
            name="AuditChainCheckpoint",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        primary_key=True,
                        serialize=False,
                        editable=False,
                        default=uuid.uuid4,
                    ),
                ),
                ("checkpoint_date", models.DateField(db_index=True, unique=True)),
                ("last_chain_index", models.PositiveBigIntegerField(default=0)),
                ("last_event_hash", models.CharField(default=GENESIS_HASH, max_length=64)),
                ("record_count", models.PositiveBigIntegerField(default=0)),
                ("signature", models.CharField(max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={"ordering": ["-checkpoint_date", "-created_at"]},
        ),
        migrations.RunPython(backfill_audit_chain, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auditlog",
            name="chain_index",
            field=models.PositiveBigIntegerField(db_index=True, unique=True),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["chain_index", "created_at"],
                name="security_au_chain_i_58b6d6_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="memberpinsecret",
            index=models.Index(
                fields=["user", "pin_type"],
                name="security_me_user_id_90fa6a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="memberpinsecret",
            index=models.Index(
                fields=["locked_until"],
                name="security_me_locked__c16470_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="memberpinsecret",
            index=models.Index(
                fields=["is_locked"],
                name="security_me_is_lock_17bb35_idx",
            ),
        ),
    ]