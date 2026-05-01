from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import ReferralReward, UserPreference
from apps.chama.models import Membership, MemberStatus


def get_referral_reward_policy():
    reward_days = max(0, int(getattr(settings, "REFERRAL_REWARD_EXTENSION_DAYS", 7)))
    reward_credit_amount = max(
        0,
        int(getattr(settings, "REFERRAL_REWARD_CREDIT_AMOUNT", 1000)),
    )
    reward_type = str(
        getattr(settings, "REFERRAL_REWARD_TYPE", ReferralReward.TRIAL_EXTENSION)
    ).strip() or ReferralReward.TRIAL_EXTENSION

    if reward_type == ReferralReward.BILLING_CREDIT:
        description = (
            f"Each completed referral adds KES {reward_credit_amount:,} in billing credit "
            "to the referrer's active chama."
        )
        reward_unit = "KES"
        reward_label = "billing credit"
        reward_display_value = reward_credit_amount
    elif reward_type == ReferralReward.TRIAL_EXTENSION:
        description = (
            f"Each completed referral adds {reward_days} day(s) to the referrer's "
            "active chama subscription."
        )
        reward_unit = "days"
        reward_label = "day(s)"
        reward_display_value = reward_days
    else:
        description = "Referral rewards are enabled."
        reward_unit = "units"
        reward_label = "reward unit(s)"
        reward_display_value = reward_days

    return {
        "reward_type": reward_type,
        "reward_days": reward_days,
        "reward_credit_amount": reward_credit_amount,
        "reward_unit": reward_unit,
        "reward_label": reward_label,
        "reward_display_value": reward_display_value,
        "description": description,
    }


def _resolve_reward_target_chama(user):
    try:
        preference = UserPreference.objects.select_related("active_chama").get(user=user)
        if preference.active_chama_id:
            return preference.active_chama
    except UserPreference.DoesNotExist:
        pass

    membership = (
        Membership.objects.select_related("chama")
        .filter(
            user=user,
            status=MemberStatus.ACTIVE,
            is_active=True,
            is_approved=True,
            exited_at__isnull=True,
        )
        .order_by("-updated_at", "-joined_at")
        .first()
    )
    if membership:
        return membership.chama
    return None


def award_referral_reward_for_completed_chama(chama):
    if not getattr(chama, "setup_completed", False) or not getattr(chama, "referred_by_id", None):
        return None

    policy = get_referral_reward_policy()
    reward, created = ReferralReward.objects.get_or_create(
        referred_chama=chama,
        defaults={
            "referrer": chama.referred_by,
            "reward_type": policy["reward_type"],
            "reward_value": policy["reward_display_value"],
            "status": ReferralReward.PENDING,
        },
    )
    if not created:
        return reward

    reward.reward_type = policy["reward_type"]
    reward.reward_value = policy["reward_display_value"]

    if reward.reward_value <= 0:
        reward.status = ReferralReward.SKIPPED
        reward.note = "No referral reward value is configured."
        reward.save(
            update_fields=[
                "reward_type",
                "reward_value",
                "status",
                "note",
                "updated_at",
            ]
        )
        return reward

    target_chama = _resolve_reward_target_chama(chama.referred_by)
    if not target_chama:
        reward.status = ReferralReward.PENDING
        reward.note = "No active chama was available to apply the reward."
        reward.save(
            update_fields=[
                "reward_type",
                "reward_value",
                "status",
                "note",
                "updated_at",
            ]
        )
        return reward

    if reward.reward_type == ReferralReward.BILLING_CREDIT:
        from apps.billing.credits import issue_billing_credit

        credit = issue_billing_credit(
            chama=target_chama,
            amount=reward.reward_value,
            source_type="referral",
            source_reference=str(reward.id),
            description=(
                f"Referral reward from {chama.name} signup completed"
                + (
                    f" using code {chama.referral_code_used}"
                    if chama.referral_code_used
                    else ""
                )
            ),
            metadata={
                "referral_reward_id": str(reward.id),
                "referred_chama_id": str(chama.id),
            },
        )
        reward.status = ReferralReward.APPLIED
        reward.rewarded_chama = target_chama
        reward.note = (
            f"Applied KES {reward.reward_value:,} billing credit to {target_chama.name}."
        )
        reward.applied_at = timezone.now()
        if not credit:
            reward.status = ReferralReward.SKIPPED
            reward.note = "Referral credit amount was zero, so no billing credit was issued."
        reward.save(
            update_fields=[
                "reward_type",
                "reward_value",
                "status",
                "rewarded_chama",
                "note",
                "applied_at",
                "updated_at",
            ]
        )
        return reward

    from apps.billing.models import Subscription
    from apps.billing.services import ensure_trial_subscription, get_latest_subscription

    subscription = get_latest_subscription(target_chama) or ensure_trial_subscription(target_chama)
    if not subscription or subscription.status not in {Subscription.TRIALING, Subscription.ACTIVE}:
        reward.status = ReferralReward.PENDING
        reward.rewarded_chama = target_chama
        reward.note = "Reward is ready but no eligible active subscription was found."
        reward.save(
            update_fields=[
                "reward_type",
                "reward_value",
                "status",
                "rewarded_chama",
                "note",
                "updated_at",
            ]
        )
        return reward

    current_period_end = subscription.current_period_end or timezone.now()
    if current_period_end < timezone.now():
        current_period_end = timezone.now()

    subscription.current_period_end = current_period_end + timedelta(days=reward.reward_value)
    subscription.save(update_fields=["current_period_end", "updated_at"])

    reward.status = ReferralReward.APPLIED
    reward.rewarded_chama = target_chama
    reward.note = (
        f"Applied {reward.reward_value} day(s) to {target_chama.name}'s subscription."
    )
    reward.applied_at = timezone.now()
    reward.save(
        update_fields=[
            "reward_type",
            "reward_value",
            "status",
            "rewarded_chama",
            "note",
            "applied_at",
            "updated_at",
        ]
    )
    return reward
